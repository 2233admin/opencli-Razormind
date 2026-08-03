import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.workflow import opencli_adapter_nodes
from backend.workflow.native_intelligence_executor import (
    NATIVE_INTELLIGENCE_LIFECYCLE_ACTIONS,
)
from backend.workflow.opencli_adapter_nodes import list_opencli_adapter_nodes
from backend.workflow.tool_capabilities import list_workflow_tool_capabilities


def _fixture_opencli_catalog() -> tuple[dict, ...]:
    return (
        {
            "site": "bbc",
            "name": "news",
            "description": "BBC news",
            "access": "read",
            "browser": False,
            "strategy": "public",
            "args": [{"name": "limit", "type": "int", "required": False}],
            "columns": ["title", "url"],
        },
        {
            "site": "twitter",
            "name": "search",
            "description": "X search",
            "access": "read",
            "browser": True,
            "strategy": "cookie",
            "args": [
                {
                    "name": "query",
                    "type": "str",
                    "required": True,
                    "positional": True,
                },
                {"name": "limit", "type": "int", "required": False},
            ],
            "columns": ["id", "text"],
        },
        {
            "site": "twitter",
            "name": "post",
            "description": "Post to X",
            "access": "write",
            "browser": True,
            "strategy": "cookie",
            "args": [{"name": "text", "type": "str", "required": True}],
            "columns": ["id", "url"],
        },
    )


def _webhook_notify_project() -> dict:
    return {
        "id": "wf-webhook-contract",
        "name": "Webhook contract",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "source-bilibili",
                "kind": "source",
                "capability": "fetch",
                "adapter": "opencli-bilibili",
                "params": {"site": "bilibili", "command": "search"},
            },
            {
                "id": "notify-webhook",
                "kind": "notify",
                "capability": "send",
                "adapter": "webhook-notifier",
                "params": {"template": "brief", "target": "webhook"},
                "ui": {"catalogId": "intelligence.output.webhook"},
            },
        ],
        "edges": [
            {
                "id": "e-source-notify",
                "source": "source-bilibili",
                "target": "notify-webhook",
            }
        ],
        "adapters": [
            {
                "id": "opencli-bilibili",
                "type": "source",
                "provider": "opencli",
                "mode": "live",
                "config": {"channel": "opencli"},
            },
            {
                "id": "webhook-notifier",
                "type": "notification",
                "provider": "webhook",
                "mode": "webhook",
                "config": {"notifierType": "webhook", "target": "webhook"},
            },
        ],
        "agentPermissions": {
            "canFetchNetwork": True,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
    }


def _native_lifecycle_tools():
    return [
        tool
        for tool in list_workflow_tool_capabilities().tools
        if tool.executor.mode == "native_intelligence"
        and tool.executor.params.get("action") in NATIVE_INTELLIGENCE_LIFECYCLE_ACTIONS
    ]


async def _native_lifecycle_capability(client, monkeypatch, tools):
    registry = list_workflow_tool_capabilities()
    native_ids = {tool.id for tool in _native_lifecycle_tools()}
    monkeypatch.setattr(
        "backend.workflow.capability_projection.list_workflow_tool_capabilities",
        lambda: registry.model_copy(
            update={
                "tools": [
                    tool for tool in registry.tools if tool.id not in native_ids
                ]
                + tools
            }
        ),
    )
    response = await client.get("/api/v1/workflows/capabilities")
    assert response.status_code == 200
    return next(
        item
        for item in response.json()["data"]["catalog"]
        if item["id"] == "package.intelligence.native-lifecycle"
    )


@pytest.mark.asyncio
async def test_compile_reports_webhook_notify_contract_without_live_delivery(client):
    response = await client.post(
        "/api/v1/workflows/compile",
        json={"project": _webhook_notify_project()},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    node = next(node for node in data["plan"]["runtime"]["nodes"] if node["id"] == "notify-webhook")
    assert node["runtime"]["origin"]["catalog_id"] == "intelligence.output.webhook"
    assert "binding" not in node["runtime"]
    assert node["runtime"]["notifier"]["binding_id"] == "workflow.notifier.webhook.send"
    assert node["runtime"]["notifier"]["dispatch"] == "blocked_until_projection"
    assert node["runtime"]["notifier"]["input"]["delivery_configured"] is False
    notifier_contract = node["runtime"]["notifier"]["contract"]
    assert notifier_contract["bindingId"] == "workflow.notifier.webhook.send"
    assert notifier_contract["certification"]["realNodeIoContract"] is True
    assert notifier_contract["certification"]["realWebhookDelivery"] is True
    assert node["runtime"]["missing_runtime"] == {
        "status": "missing",
        "code": "missing_delivery_projection",
        "node_id": "notify-webhook",
        "kind": "notify",
        "capability": "send",
        "adapter_id": "webhook-notifier",
        "provider": "webhook",
        "required_params": [
            "evidencebatch_projection_api",
            "delivery_projection",
            "webhook_url",
        ],
        "message": (
            "Webhook Notify has a backend notifier contract, but live delivery "
            "waits for EvidenceBatch projection and a configured webhook URL."
        ),
    }


@pytest.mark.asyncio
async def test_native_lifecycle_readiness_requires_exact_complete_action_set(
    client, monkeypatch
):
    tools = _native_lifecycle_tools()

    capability = await _native_lifecycle_capability(client, monkeypatch, tools)

    assert capability["status"] == "runnable"
    assert capability["backendAvailable"] is True
    assert capability["missing"] == []
    assert capability["manifest"]["readiness"] == {
        "status": "runnable",
        "childCount": 18,
        "expectedChildCount": 18,
        "blockedChildren": [],
        "missingReasons": [],
        "missingActions": [],
        "extraActions": [],
        "duplicateActions": [],
        "missingToolIds": [],
        "extraToolIds": [],
        "duplicateToolIds": [],
        "missingChildren": [],
        "extraChildren": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_detail"),
    [
        ("missing", ("missingActions", ["report.answers"])),
        ("duplicate", ("duplicateActions", ["report.answers"])),
        ("extra", ("extraActions", ["unexpected.lifecycle.action"])),
        (
            "wrong_id",
            (
                "extraToolIds",
                ["tool.intelligence.native.report.answers.unexpected"],
            ),
        ),
        (
            "blocked",
            (
                "blockedChildren",
                ["tool.intelligence.native.report.answers"],
            ),
        ),
    ],
)
async def test_native_lifecycle_readiness_fails_closed_for_invalid_action_set(
    client, monkeypatch, case, expected_detail
):
    tools = _native_lifecycle_tools()
    target = next(
        tool
        for tool in tools
        if tool.executor.params.get("action") == "report.answers"
    )
    if case == "missing":
        tools.remove(target)
    elif case == "duplicate":
        tools.append(target.model_copy(deep=True))
    elif case == "extra":
        tools[tools.index(target)] = target.model_copy(
            update={
                "executor": target.executor.model_copy(
                    update={"params": {"action": "unexpected.lifecycle.action"}}
                ),
            },
            deep=True,
        )
    elif case == "wrong_id":
        tools[tools.index(target)] = target.model_copy(
            update={"id": "tool.intelligence.native.report.answers.unexpected"},
            deep=True,
        )
    elif case == "blocked":
        manifest = dict(target.manifest)
        manifest["readiness"] = {
            "status": "blocked",
            "missingReasons": ["test_child_readiness_blocked"],
        }
        tools[tools.index(target)] = target.model_copy(
            update={"status": "blocked", "manifest": manifest},
            deep=True,
        )

    capability = await _native_lifecycle_capability(client, monkeypatch, tools)

    assert capability["status"] == "blocked"
    assert capability["backendAvailable"] is False
    assert "missing_native_intelligence_action_set" in capability["missing"]
    detail_key, detail_value = expected_detail
    assert capability["manifest"]["readiness"][detail_key] == detail_value


@pytest.mark.asyncio
async def test_workflow_capabilities_project_real_backend_surfaces(client, monkeypatch):
    monkeypatch.setattr(
        "backend.workflow.capability_projection.get_opencli_adapter_node_summary",
        lambda: {
            "total": 3,
            "sites": 2,
            "access": {"read": 2, "write": 1},
            "browser": {"browser": 2, "non_browser": 1},
            "sourceSlotReady": 1,
            "sourceSlotRequiresParams": 1,
            "toolCapabilityReviewRequired": 1,
        },
    )

    response = await client.get("/api/v1/workflows/capabilities")

    assert response.status_code == 200
    data = response.json()["data"]

    catalog = {item["id"]: item for item in data["catalog"]}
    for item in catalog.values():
        if item["status"] == "runnable" and item.get("runtimeBinding"):
            contract = item["manifest"]["contract"]
            assert contract["bindingId"] == item["runtimeBinding"]
            assert contract["inputShape"]["ports"] is not None
            assert contract["outputShape"]["ports"] is not None
            assert contract["eventShape"]["events"]
            assert contract["fixtureCoverage"]["cases"]
            assert contract["canvas"]["exposeResourceInternals"] is False

    assert catalog["intelligence.input.collection-need"]["status"] == "runnable"
    assert catalog["intelligence.input.collection-need"]["backendAvailable"] is True
    assert catalog["intelligence.input.collection-need"]["runtimeBinding"] == (
        "workflow.demand-draft.patch"
    )
    assert catalog["intelligence.schedule.cron"]["status"] == "runnable"
    assert catalog["intelligence.schedule.cron"]["backendAvailable"] is True
    assert catalog["intelligence.schedule.cron"]["runtimeBinding"] == (
        "workflow.trigger.schedule_tick"
    )
    assert "workflow_trigger_binding" not in catalog["intelligence.schedule.cron"]["missing"]
    for source_id in (
        "intelligence.source.rss",
        "intelligence.source.rsshub",
        "intelligence.source.rss-bridge",
        "intelligence.source.http",
    ):
        assert catalog[source_id]["status"] == "runnable"
        assert catalog[source_id]["backendAvailable"] is True
        assert catalog[source_id]["runtimeBinding"] == "workflow.source.fetch"
        assert catalog[source_id]["manifest"]["ports"]["outputs"] == [
            {"name": "out", "type": "items[]"}
        ]
        assert "network.fetch" in catalog[source_id]["manifest"]["permissions"]
    assert catalog["intelligence.source.opencli-slot"]["status"] == "runnable"
    assert catalog["intelligence.source.opencli-slot"]["backendAvailable"] is True
    assert catalog["intelligence.source.opencli-slot"]["runtimeBinding"]
    opencli_manifest = catalog["intelligence.source.opencli-slot"]["manifest"]
    assert opencli_manifest["schema"] == "capability.source.opencli-slot.v1"
    assert opencli_manifest["ports"]["outputs"] == [{"name": "out", "type": "items[]"}]
    assert opencli_manifest["runtime"]["binding"] == "iii.collector-opencli.snapshot"
    assert "canFetchNetwork" in opencli_manifest["permissions"]
    assert "source_output_ingest_available" in opencli_manifest["probes"]
    assert (
        "frontend_run_event_binding" not in catalog["intelligence.source.opencli-slot"]["missing"]
    )
    assert catalog["intelligence.source.pool"]["status"] == "runnable"
    assert catalog["intelligence.source.pool"]["backendAvailable"] is True
    assert catalog["intelligence.source.pool"]["runtimeBinding"] == (
        "workflow.source-pool.parallel-fanout"
    )
    assert catalog["intelligence.processing.dedupe"]["status"] == "runnable"
    assert catalog["intelligence.processing.dedupe"]["runtimeBinding"] == (
        "workflow.transform.dedupe"
    )
    assert catalog["package.processing.record-hygiene"]["status"] == "runnable"
    assert catalog["package.processing.record-hygiene"]["runtimeBinding"] is None
    assert catalog["intelligence.output.collection-result"]["status"] == "runnable"
    assert catalog["intelligence.output.collection-result"]["backendAvailable"] is True
    assert catalog["intelligence.output.collection-result"]["runtimeBinding"] == (
        "workflow.collection-output.items"
    )
    assert catalog["intelligence.flow.merge"]["status"] == "runnable"
    assert catalog["intelligence.flow.merge"]["runtimeBinding"] == "workflow.flow.merge"
    merge_manifest = catalog["intelligence.flow.merge"]["manifest"]
    assert merge_manifest["ports"]["inputs"] == [
        {"name": "in1", "type": "recordCandidate[]"},
        {"name": "in2", "type": "recordCandidate[]"},
    ]
    assert merge_manifest["trace"]["events"] == [
        "partial:mergedCandidateCount",
        "completed",
    ]
    assert catalog["intelligence.processing.dedupe"]["status"] == "runnable"
    assert catalog["intelligence.processing.dedupe"]["backendAvailable"] is True
    assert catalog["intelligence.processing.dedupe"]["runtimeBinding"] == (
        "workflow.transform.dedupe"
    )
    dedupe_manifest = catalog["intelligence.processing.dedupe"]["manifest"]
    assert dedupe_manifest["contract"]["outputShape"]["artifacts"] == [
        "recordCandidate[]",
        "rejected[]",
        "metrics",
    ]
    assert catalog["intelligence.control.record-acceptance"]["status"] == "runnable"
    assert catalog["intelligence.control.record-acceptance"]["runtimeBinding"] == (
        "workflow.gate.record-acceptance"
    )
    gate_manifest = catalog["intelligence.control.record-acceptance"]["manifest"]
    assert gate_manifest["schema"] == "capability.control.record-acceptance.v1"
    assert "record_schema_available" in gate_manifest["probes"]
    assert catalog["intelligence.sink.records"]["status"] == "runnable"
    assert catalog["intelligence.sink.records"]["runtimeBinding"] == (
        "workflow.record-sink.records"
    )
    sink_manifest = catalog["intelligence.sink.records"]["manifest"]
    assert sink_manifest["resources"] == [
        "data_sources",
        "collection_tasks",
        "collected_records",
    ]
    assert sink_manifest["runtime"]["binding"] == "workflow.record-sink.records"
    assert catalog["external.tool.capability"]["status"] == "runnable"
    assert catalog["external.tool.capability"]["backendAvailable"] is True
    assert catalog["external.tool.capability"]["runtimeBinding"] == (
        "workflow.external-tool.capability"
    )
    external_tool_manifest = catalog["external.tool.capability"]["manifest"]
    assert external_tool_manifest["schema"] == "capability.external.tool.v1"
    assert external_tool_manifest["ports"] == {
        "inputs": [{"name": "in", "type": "unknown"}],
        "outputs": [{"name": "out", "type": "unknown"}],
    }
    assert (
        "node_level_tool_capability_binding_when_unconfigured"
        in catalog["external.tool.capability"]["missing"]
    )
    assert "tool_capability_registry" in external_tool_manifest["resources"]
    assert "partial:outputItemCount" in external_tool_manifest["trace"]["events"]
    assert catalog["package.opencli.multi-source-hda"]["status"] == "runnable"
    record_hygiene_package = catalog["package.processing.record-hygiene"]
    assert record_hygiene_package["status"] == "runnable"
    assert record_hygiene_package["backendAvailable"] is True
    assert record_hygiene_package["runtimeBinding"] is None
    assert record_hygiene_package["manifest"]["canvas"]["structural"] is True
    assert record_hygiene_package["manifest"]["implementation"]["authority"] == (
        "locked-internal-nodes"
    )
    assert record_hygiene_package["manifest"]["ports"]["outputs"] == [
        {"name": "out", "type": "record[]"}
    ]
    assert record_hygiene_package["manifest"]["artifacts"] == {
        "trace": ["rejected[]", "metrics"],
        "routable": False,
    }
    assert (
        "frontend_run_event_binding" not in catalog["package.opencli.multi-source-hda"]["missing"]
    )
    assert (
        "typed_demand_input_envelope" not in catalog["package.opencli.multi-source-hda"]["missing"]
    )
    assert catalog["intelligence.output.webhook"]["status"] == "blocked"
    assert catalog["intelligence.output.webhook"]["backendAvailable"] is True
    assert catalog["intelligence.output.webhook"]["runtimeBinding"] == (
        "workflow.notifier.webhook.send"
    )
    assert "workflow_notifier_sink_binding" not in catalog["intelligence.output.webhook"]["missing"]
    assert "evidencebatch_projection_input" in catalog["intelligence.output.webhook"]["missing"]
    webhook_contract = catalog["intelligence.output.webhook"]["manifest"]["contract"]
    assert webhook_contract["status"] == "blocked_until_preconditions"
    assert webhook_contract["certification"]["realWebhookDelivery"] is True
    assert webhook_contract["configGate"]["required"] == [
        "evidencebatch_projection_api",
        "delivery_projection",
        "webhook_url",
    ]

    channels = {item["channelType"]: item for item in data["channels"]}
    assert set(channels) == {
        "api",
        "browser_act",
        "cli",
        "crawl4ai",
        "doubao_research",
        "douyin_detail",
        "opencli",
        "rss",
        "skill",
        "web_scraper",
    }
    assert channels["opencli"]["status"] == "runnable"
    assert channels["opencli"]["backendAvailable"] is True
    assert channels["rss"]["status"] == "blocked"
    assert channels["rss"]["backendAvailable"] is True
    assert "canvas_source_projection" in channels["rss"]["missing"]

    notifiers = {item["notifierType"]: item for item in data["notifiers"]}
    assert "webhook" in notifiers
    assert notifiers["webhook"]["status"] == "blocked"
    assert notifiers["webhook"]["backendAvailable"] is True
    assert notifiers["webhook"]["runtimeBinding"] == "workflow.notifier.webhook.send"
    assert "evidencebatch_projection_input" in notifiers["webhook"]["missing"]

    primitives = {item["id"]: item for item in data["primitives"]}
    assert primitives["primitive.ops.trigger-webhook"]["status"] == "runnable"
    assert primitives["primitive.ops.trigger-webhook"]["backendAvailable"] is True
    assert primitives["primitive.ops.trigger-webhook"]["runtimeBinding"] == (
        "workflow.trigger.webhook_input"
    )
    assert primitives["primitive.ops.trigger-webhook"]["manifest"]["contract"]["outputShape"][
        "ports"
    ] == [{"name": "request", "type": "webhookRequest"}]
    assert primitives["primitive.ops.trigger-webhook"]["missing"] == []

    triggers = {item["id"]: item for item in data["triggers"]}
    assert triggers["trigger.manual"]["status"] == "runnable"
    assert triggers["trigger.manual"]["missing"] == []
    assert triggers["trigger.webhook"]["status"] == "runnable"
    assert triggers["trigger.webhook"]["missing"] == []
    assert triggers["trigger.webhook"]["runtimeBinding"] == "workflow.trigger.webhook_input"
    assert triggers["trigger.webhook"]["manifest"]["contract"]["status"] == (
        "dispatch_only"
    )
    assert triggers["trigger.webhook"]["manifest"]["contract"]["configGate"] == {
        "required": []
    }
    assert "workflow-webhook-ingress-api" in triggers["trigger.webhook"]["manifest"][
        "contract"
    ]["fixtureCoverage"]["cases"]

    resources = {item["id"]: item for item in data["resources"]}
    fleet_resource = resources["resource.workflow-fleet-runtime"]
    assert fleet_resource["status"] == "runnable"
    assert fleet_resource["backendAvailable"] is True
    assert fleet_resource["runtimeBinding"] == "workflow.fleet.inventory"
    assert fleet_resource["manifest"]["canvas"]["node"] is False
    assert fleet_resource["manifest"]["endpoints"] == {
        "inventory": "/api/v1/workflows/fleet/inventory",
        "match": "/api/v1/workflows/fleet/match",
    }
    adapter_registry = resources["resource.opencli-adapter-nodes"]
    assert adapter_registry["status"] == "runnable"
    assert adapter_registry["backendAvailable"] is True
    assert adapter_registry["runtimeBinding"] == "iii.collector-opencli.snapshot"
    assert adapter_registry["manifest"]["canvas"]["node"] is False
    assert adapter_registry["manifest"]["endpoint"] == ("/api/v1/workflows/opencli-adapter-nodes")
    assert adapter_registry["manifest"]["summary"]["total"] == 3
    assert adapter_registry["manifest"]["catalogModel"] == {
        "kind": "core_nodes_plus_presets",
        "coreNodes": {
            "endpoint": "/api/v1/workflows/capabilities",
            "role": "node_definition",
        },
        "adapterCommands": {
            "endpoint": "/api/v1/workflows/opencli-adapter-nodes",
            "role": "node_preset",
            "presetKinds": ["source_slot", "tool_capability"],
        },
    }
    assert adapter_registry["manifest"]["query"]["grouping"] == "facets"
    assert not any(item_id.startswith("opencli.adapter.") for item_id in catalog)
    assert adapter_registry["manifest"]["materialization"]["write"] == (
        "external.tool.capability with review"
    )
    tool_resource = resources["resource.tool-capability.tool.search.fixture"]
    assert tool_resource["status"] == "runnable"
    assert tool_resource["backendAvailable"] is True
    assert tool_resource["runtimeBinding"] == "workflow.external-tool.capability"
    assert tool_resource["manifest"]["toolCapability"]["id"] == "tool.search.fixture"
    assert tool_resource["manifest"]["toolCapability"]["versionPin"] == {
        "package": "opencli-admin",
        "packageVersion": "0.1.0",
        "capabilityVersion": "1.0.0",
        "provenance": "built-in",
    }
    assert tool_resource["manifest"]["toolCapability"]["executor"]["mode"] == "fixture"
    realtime_resource = resources["resource.tool-capability.tool.realtime.stream.subscribe"]
    assert realtime_resource["status"] == "runnable"
    assert realtime_resource["runtimeBinding"] == "workflow.external-tool.capability"
    assert realtime_resource["manifest"]["toolCapability"]["id"] == (
        "tool.realtime.stream.subscribe"
    )
    assert realtime_resource["manifest"]["toolCapability"]["executor"]["mode"] == (
        "okx_market_ticker_snapshot"
    )
    assert "realtime.source.stream" not in catalog


def test_opencli_adapter_nodes_classify_manifest_entries(monkeypatch):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_catalog,
    )

    response = list_opencli_adapter_nodes()
    nodes = {node.id: node for node in response.nodes}

    bbc = nodes["opencli.adapter.bbc.news"]
    assert bbc.status == "runnable"
    assert bbc.catalogId == "intelligence.source.opencli-slot"
    assert bbc.presetKind == "source_slot"
    assert bbc.runtimeReadiness == "source_slot_ready"
    assert bbc.manifest["canvas"]["node"] is True
    assert bbc.manifest["canvas"]["runBlocked"] is False
    assert bbc.manifest["canvas"]["materialization"] == "source_slot_ready"
    assert bbc.params == {"site": "bbc", "command": "news", "format": "json", "args": {}}

    twitter_search = nodes["opencli.adapter.twitter.search"]
    assert twitter_search.status == "preview_only"
    assert twitter_search.catalogId == "intelligence.source.opencli-slot"
    assert twitter_search.requiredArgs == ["query"]
    assert twitter_search.presetKind == "source_slot"
    assert twitter_search.runtimeReadiness == "source_slot_requires_params"
    assert twitter_search.manifest["canvas"]["node"] is True
    assert twitter_search.manifest["canvas"]["runBlocked"] is True
    assert twitter_search.manifest["canvas"]["materialization"] == ("source_slot_requires_params")
    assert twitter_search.manifest["canvas"]["positionalRequiredArgs"] == ["query"]

    twitter_post = nodes["opencli.adapter.twitter.post"]
    assert twitter_post.status == "blocked"
    assert twitter_post.catalogId == "external.tool.capability"
    assert twitter_post.presetKind == "tool_capability"
    assert twitter_post.runtimeReadiness == "tool_capability_review_required"
    assert twitter_post.manifest["canvas"]["node"] is True
    assert twitter_post.manifest["canvas"]["runBlocked"] is True
    assert twitter_post.manifest["canvas"]["materialization"] == ("tool_capability_review_required")
    assert response.summary == {
        "total": 3,
        "sites": 2,
        "access": {"read": 2, "write": 1},
        "browser": {"non_browser": 1, "browser": 2},
        "sourceSlotReady": 1,
        "sourceSlotRequiresParams": 1,
        "toolCapabilityReviewRequired": 1,
    }
    assert response.facets.model_dump() == {
        "site": {"bbc": 1, "twitter": 2},
        "capability": {"fetch": 2, "store": 1},
        "access": {"read": 2, "write": 1},
        "browser": {"non_browser": 1, "browser": 2},
        "status": {"runnable": 1, "blocked": 1, "preview_only": 1},
        "presetKind": {"source_slot": 2, "tool_capability": 1},
        "runtimeReadiness": {
            "source_slot_ready": 1,
            "source_slot_requires_params": 1,
            "tool_capability_review_required": 1,
        },
    }


def test_opencli_adapter_nodes_refresh_after_opencli_catalog_changes(monkeypatch):
    catalogs = iter(
        [
            [{"site": "bbc", "name": "news", "access": "read", "args": []}],
            [{"site": "douyin", "name": "tophot", "access": "read", "args": []}],
        ]
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(next(catalogs)),
            stderr="",
        )

    monkeypatch.setattr(opencli_adapter_nodes, "resolve_opencli_bin", lambda: "opencli-test")
    monkeypatch.setattr(opencli_adapter_nodes.subprocess, "run", fake_run)
    opencli_adapter_nodes.refresh_opencli_adapter_catalog()

    first = list_opencli_adapter_nodes(refresh=True)
    cached = list_opencli_adapter_nodes()
    refreshed = list_opencli_adapter_nodes(refresh=True)

    assert [node.id for node in first.nodes] == ["opencli.adapter.bbc.news"]
    assert [node.id for node in cached.nodes] == ["opencli.adapter.bbc.news"]
    assert [node.id for node in refreshed.nodes] == ["opencli.adapter.douyin.tophot"]
    opencli_adapter_nodes.refresh_opencli_adapter_catalog()


def test_opencli_adapter_nodes_reject_invalid_utf8_catalog(monkeypatch):
    def fake_run(*args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        assert "errors" not in kwargs
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(opencli_adapter_nodes, "resolve_opencli_bin", lambda: "opencli-test")
    monkeypatch.setattr(opencli_adapter_nodes.subprocess, "run", fake_run)
    opencli_adapter_nodes.refresh_opencli_adapter_catalog()

    response = list_opencli_adapter_nodes(refresh=True)

    assert response.total == 0
    assert response.nodes == []
    opencli_adapter_nodes.refresh_opencli_adapter_catalog()


def test_opencli_adapter_catalog_coalesces_concurrent_discovery(monkeypatch):
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                [{"site": "bbc", "name": "news", "access": "read", "args": []}]
            ),
            stderr="",
        )

    monkeypatch.setattr(opencli_adapter_nodes, "resolve_opencli_bin", lambda: "opencli-test")
    monkeypatch.setattr(opencli_adapter_nodes.subprocess, "run", fake_run)
    opencli_adapter_nodes.refresh_opencli_adapter_catalog()

    ready = threading.Barrier(2)

    def force_refresh():
        ready.wait()
        return opencli_adapter_nodes.get_opencli_adapter_catalog(refresh=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        catalogs = list(executor.map(lambda _: force_refresh(), range(2)))

    assert calls == 1
    assert catalogs[0] == catalogs[1]
    opencli_adapter_nodes.refresh_opencli_adapter_catalog()


@pytest.mark.asyncio
async def test_opencli_adapter_nodes_endpoint_filters_and_limits(client, monkeypatch):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_catalog,
    )

    response = await client.get(
        "/api/v1/workflows/opencli-adapter-nodes",
        params={"site": "twitter", "includeWrite": False, "limit": 10},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["summary"]["access"] == {"read": 1}
    assert [node["id"] for node in data["nodes"]] == ["opencli.adapter.twitter.search"]


@pytest.mark.asyncio
async def test_opencli_adapter_nodes_endpoint_filters_presets_and_returns_facets(
    client, monkeypatch
):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_catalog,
    )

    response = await client.get(
        "/api/v1/workflows/opencli-adapter-nodes",
        params={
            "access": "read",
            "capability": "fetch",
            "browser": True,
            "presetKind": "source_slot",
            "runtimeReadiness": "source_slot_requires_params",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["facets"] == {
        "site": {"twitter": 1},
        "capability": {"fetch": 1},
        "access": {"read": 1},
        "browser": {"browser": 1},
        "status": {"preview_only": 1},
        "presetKind": {"source_slot": 1},
        "runtimeReadiness": {"source_slot_requires_params": 1},
    }
    assert data["nodes"][0]["id"] == "opencli.adapter.twitter.search"
    assert data["nodes"][0]["presetKind"] == "source_slot"
    assert data["nodes"][0]["runtimeReadiness"] == "source_slot_requires_params"


@pytest.mark.asyncio
async def test_workflow_tool_capabilities_register_opencli_tool_bindings(client):
    response = await client.get("/api/v1/workflows/tool-capabilities")

    assert response.status_code == 200
    data = response.json()["data"]
    tools = {tool["id"]: tool for tool in data["tools"]}
    assert "tool.search.fixture" in tools
    fixture_tool = tools["tool.search.fixture"]
    assert fixture_tool["status"] == "runnable"
    assert fixture_tool["provider"] == "opencli-admin"
    assert fixture_tool["executor"]["mode"] == "fixture"
    assert fixture_tool["versionPin"] == {
        "package": "opencli-admin",
        "packageVersion": "0.1.0",
        "capabilityVersion": "1.0.0",
        "provenance": "built-in",
    }
    assert fixture_tool["inputPorts"] == [{"name": "in", "type": "unknown"}]
    assert fixture_tool["outputPorts"] == [{"name": "out", "type": "unknown"}]
    assert fixture_tool["manifest"]["runtime"]["binding"] == ("workflow.external-tool.capability")
    realtime_ids = {
        "tool.realtime.stream.subscribe",
        "tool.realtime.event.normalize",
        "tool.realtime.window.rolling",
        "tool.realtime.state.cache",
        "tool.realtime.feature.compute",
        "tool.realtime.signal.emit",
    }
    assert realtime_ids.issubset(tools)
    stream_tool = tools["tool.realtime.stream.subscribe"]
    assert stream_tool["inputPorts"] == [{"name": "in", "type": "trigger"}]
    assert stream_tool["outputPorts"] == [{"name": "out", "type": "event[]"}]
    assert stream_tool["executor"]["mode"] == "okx_market_ticker_snapshot"
    assert stream_tool["executor"]["params"]["instId"] == "ETH-USDT-SWAP"
    assert stream_tool["manifest"]["canvas"]["node"] is False
    signal_tool = tools["tool.realtime.signal.emit"]
    assert signal_tool["inputPorts"] == [{"name": "in", "type": "feature[]"}]
    assert signal_tool["outputPorts"] == [{"name": "out", "type": "signal[]"}]
