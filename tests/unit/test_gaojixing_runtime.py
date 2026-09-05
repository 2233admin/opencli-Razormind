from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.channels.base import ChannelResult
from backend.workflow import gaojixing_runtime as runtime
from backend.workflow.gaojixing_runtime import (
    GAOJIXING_CAPABILITY_ID,
    GaojixingReadinessError,
    build_question_package,
    capture_live_doubao,
    map_capture_item,
)
from backend.workflow.opencli_hda_tracer import (
    _execute_gaojixing_fixture_source,
    _execute_gaojixing_source,
    _gaojixing_execution_mode,
    _is_gaojixing_source_node,
    _store_record_sink_outputs,
    replay_downstream_from_persisted_gaojixing_source,
)


def _persisted_capture_receipt():
    return {
        "schema": "gaojixing.pre-cleanup-receipt.v1",
        "persisted": True,
        "path": "pre-cleanup-evidence/run/capture.json",
        "sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
    }


def test_compiled_doubao_adapter_selects_live_gaojixing_path():
    node = SimpleNamespace(
        id="doubao-research",
        kind="source",
        params={"question": "{{keyword}}"},
        adapter=SimpleNamespace(
            provider="doubao_research",
            mode="live",
            config={"channelType": "doubao_research"},
        ),
        runtime={"binding": {"input": {}}},
    )

    assert _is_gaojixing_source_node(node) is True
    assert _gaojixing_execution_mode(node) == "live"


def test_question_package_uses_runtime_question_and_stable_digest():
    first = build_question_package(
        node_params={"question": "configured", "sourceGroup": "research"},
        adapter_config={"site_session": "persistent", "settle_seconds": 35},
        runtime_payload={"question": "runtime"},
    )
    second = build_question_package(
        node_params={"question": "other", "sourceGroup": "research"},
        adapter_config={"site_session": "persistent", "settle_seconds": 35},
        runtime_payload={"question": "runtime"},
    )

    assert first.question == "runtime"
    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert first.options["settle_seconds"] == 35


def test_question_package_remains_immutable_after_source_changes():
    node_params = {"question": "original", "sourceGroup": "before"}
    package = build_question_package(
        node_params=node_params,
        adapter_config={"settle_seconds": 35},
        runtime_payload={},
    )

    node_params["question"] = "edited"
    node_params["sourceGroup"] = "after"

    assert package.question == "original"
    assert package.options["sourceGroup"] == "before"
    with pytest.raises(TypeError):
        package.options["sourceGroup"] = "mutated"
    assert package.digest == build_question_package(
        node_params={"question": "original", "sourceGroup": "before"},
        adapter_config={"settle_seconds": 35},
        runtime_payload={},
    ).digest


def test_question_package_accepts_published_run_query_input():
    package = build_question_package(
        node_params={},
        adapter_config={},
        runtime_payload={"query": "高吉星燕窝酸 DHA 藻油"},
    )

    assert package.question == "高吉星燕窝酸 DHA 藻油"


def test_question_package_requires_effective_question():
    with pytest.raises(GaojixingReadinessError) as error:
        build_question_package(node_params={}, adapter_config={}, runtime_payload={})

    assert error.value.code == "gaojixing_question_required"


@pytest.mark.asyncio
async def test_capture_fails_closed_when_capability_missing(monkeypatch):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )

    async def should_not_probe(self, _config):
        raise AssertionError("session probe must not run for a missing capability")

    monkeypatch.setattr(runtime.DoubaoResearchChannel, "health_check", should_not_probe)
    with pytest.raises(GaojixingReadinessError) as error:
        await capture_live_doubao(
            package=package,
            node_params={"capabilityId": "missing.capture"},
            adapter_config={},
            network_allowed=True,
        )

    assert error.value.code == "gaojixing_capability_missing"



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_config", "network_allowed", "expected_code"),
    [
        ({"capabilityAvailable": False}, True, "gaojixing_capability_missing"),
        ({"adapterAvailable": False}, True, "gaojixing_adapter_missing"),
        ({"authenticated": False}, True, "gaojixing_authentication_required"),
        ({"sessionAvailable": False}, True, "gaojixing_session_unavailable"),
        ({}, False, "gaojixing_network_denied"),
    ],
)
async def test_capture_reports_each_configured_readiness_blocker(
    monkeypatch,
    adapter_config,
    network_allowed,
    expected_code,
):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )

    async def should_not_probe(self, _config):
        raise AssertionError("session probe must not run after a readiness blocker")

    monkeypatch.setattr(runtime.DoubaoResearchChannel, "health_check", should_not_probe)
    with pytest.raises(GaojixingReadinessError) as error:
        await capture_live_doubao(
            package=package,
            node_params={},
            adapter_config=adapter_config,
            network_allowed=network_allowed,
        )

    assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_capture_reports_captcha_from_session_probe(monkeypatch):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )

    async def unhealthy(self, _config):
        return False

    async def captcha(self, _config):
        return "captcha_challenge"

    monkeypatch.setattr(runtime.DoubaoResearchChannel, "health_check", unhealthy)
    monkeypatch.setattr(runtime.DoubaoResearchChannel, "readiness_code", captcha)

    with pytest.raises(GaojixingReadinessError) as error:
        await capture_live_doubao(
            package=package,
            node_params={},
            adapter_config={},
            network_allowed=True,
        )

    assert error.value.code == "gaojixing_captcha_challenge"
    assert error.value.details["site"] == "doubao"

@pytest.mark.asyncio
async def test_capture_uses_live_channel_after_health_probe(monkeypatch):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    calls = []

    async def healthy(self, _config):
        calls.append("health")
        return True

    async def collect(self, config, parameters):
        calls.append((config["question"], parameters["question"]))
        return ChannelResult.ok([{"content": "answer", "citations": [], "conversation_url": ""}])

    monkeypatch.setattr(runtime.DoubaoResearchChannel, "health_check", healthy)
    monkeypatch.setattr(runtime.DoubaoResearchChannel, "collect", collect)
    result = await capture_live_doubao(
        package=package,
        node_params={},
        adapter_config={"capabilityId": GAOJIXING_CAPABILITY_ID},
        network_allowed=True,
    )

    assert result.success
    assert calls == ["health", ("q", "q")]


@pytest.mark.asyncio
async def test_capture_agent_mode_dispatches_native_runtime_and_maps_browser_evidence(monkeypatch):
    exact_question = "第一行问题\n第二行补充"
    package = build_question_package(
        node_params={"question": exact_question},
        adapter_config={"executionMode": "agent", "agentRuntime": "codex"},
        runtime_payload={},
    )

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return [
                SimpleNamespace(
                    url="http://host.docker.internal:19824",
                    protocol="ws",
                    status="online",
                    runtime_capabilities={
                        "codex": ["streaming", "tool_events", "workspace_read"]
                    },
                )
            ]

    class _Session:
        async def execute(self, _query):
            return _Rows()

    captured = {}

    async def fake_send(agent_url, task, on_event, timeout):
        captured.update({"agent_url": agent_url, "task": task, "timeout": timeout})
        await on_event({"type": "text", "text": "working"})
        await on_event(
            {
                "type": "evidence",
                "evidence": {
                    "kind": "doubao.capture.pre_cleanup",
                    "response": {"answer": "answer", "conversation_deleted": False},
                },
            }
        )
        return {
            "type": "done",
            "result": {
                "text": (
                    '{"status":"completed","answer":"answer",'
                    '"answer_complete":true,"conversation_deleted":true,'
                    '"data":[{"point":"value"}],'
                    '"links":[{"url":"https://example.test/source"}],'
                    '"conversation_url":"https://www.doubao.com/chat/123",'
                    '"session_share_data":{"url":"https://www.doubao.com/chat/123"},'
                    '"suggested_keywords":["follow-up"],'
                    '"search_keywords":["search term"],'
                    '"search_keyword_count":1,"reference_count":1,'
                    '"video_contents":["video card"]}'
                )
            },
        }

    monkeypatch.setattr(runtime.ws_agent_manager, "list_connected", lambda: [
        "http://host.docker.internal:19824"
    ])
    monkeypatch.setattr("backend.ws_agent_manager.send_agent_task", fake_send)
    monkeypatch.setattr(
        runtime,
        "write_precleanup_capture_receipt",
        lambda *_args, **_kwargs: _persisted_capture_receipt(),
    )

    result = await capture_live_doubao(
        package=package,
        node_params={},
        adapter_config={
            "capabilityId": GAOJIXING_CAPABILITY_ID,
            "executionMode": "agent",
            "agentRuntime": "codex",
        },
        network_allowed=True,
        external_mutation_allowed=True,
        session=_Session(),
        workflow_id="workflow",
        run_id="run",
    )

    assert result.success
    assert captured["agent_url"] == "http://host.docker.internal:19824"
    assert captured["task"]["runtime"] == "codex"
    assert captured["task"]["input"]["question"] == exact_question
    assert captured["task"]["input"]["message"].endswith(f"\n{exact_question}")
    assert captured["task"]["permissions"]["action_scope"] == [
        "doubao.ask",
        "doubao.read",
        "doubao.delete",
    ]
    assert "Doubao CLI" in captured["task"]["instructions"]
    item = result.items[0]
    assert item["content"] == "answer"
    assert item["links"] == [{"url": "https://example.test/source"}]
    assert item["conversation_url"] == "https://www.doubao.com/chat/123"
    assert item["suggested_keywords"] == ["follow-up"]
    assert item["search_keywords"] == ["search term"]
    assert item["search_keyword_count"] == 1
    assert item["reference_count"] == 1
    assert item["video_contents"] == ["video card"]
    assert item["provenance"] == "agent:codex:browser:opencli"


@pytest.mark.asyncio
async def test_capture_agent_mode_can_dispatch_bbx_on_the_same_vnc_agent(monkeypatch):
    package = build_question_package(
        node_params={"question": "q"},
        adapter_config={"executionMode": "agent", "agentRuntime": "bbx"},
        runtime_payload={},
    )

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return [
                SimpleNamespace(
                    url="http://agent-1:19823",
                    protocol="ws",
                    status="online",
                    runtime_capabilities={"bbx": ["browser", "tool_events"]},
                )
            ]

    class _Session:
        async def execute(self, _query):
            return _Rows()

    captured = {}
    persisted_receipts = []

    def fake_write_receipt(storage_root, **kwargs):
        persisted_receipts.append({"storage_root": storage_root, **kwargs})
        return _persisted_capture_receipt()

    async def fake_send(agent_url, task, on_event, timeout):
        captured.update({"agent_url": agent_url, "task": task, "timeout": timeout})
        await on_event(
            {
                "type": "evidence",
                "evidence": {
                    "kind": "doubao.capture.pre_cleanup",
                    "response": {
                        "answer": "answer",
                        "conversation_deleted": False,
                    },
                },
            }
        )
        return {
            "type": "done",
            "result": {
                "text": (
                    '{"status":"completed","answer":"answer",'
                    '"answer_complete":true,"conversation_deleted":true,'
                    '"data":[],"links":[{"url":"https://example.test/source"}],'
                    '"conversation_url":"https://www.doubao.com/chat/123",'
                    '"session_share_data":{"url":"https://www.doubao.com/chat/123"},'
                    '"suggested_keywords":["follow-up"]}'
                )
            },
        }

    monkeypatch.setattr(runtime.ws_agent_manager, "list_connected", lambda: ["http://agent-1:19823"])
    monkeypatch.setattr("backend.ws_agent_manager.send_agent_task", fake_send)
    monkeypatch.setattr(runtime, "write_precleanup_capture_receipt", fake_write_receipt)

    result = await capture_live_doubao(
        package=package,
        node_params={},
        adapter_config={
            "capabilityId": GAOJIXING_CAPABILITY_ID,
            "executionMode": "agent",
            "agentRuntime": "bbx",
        },
        network_allowed=True,
        external_mutation_allowed=True,
        session=_Session(),
        workflow_id="workflow",
        run_id="run",
    )

    assert result.success
    assert captured["agent_url"] == "http://agent-1:19823"
    assert captured["task"]["runtime"] == "bbx"
    assert captured["task"]["required_capabilities"] == ["browser", "tool_events"]
    assert captured["task"]["permissions"]["tool_scope"] == ["bbx.browser"]
    assert result.items[0]["provenance"] == "agent:bbx:browser:bbx"
    assert result.items[0]["conversation_deleted"] is True
    assert result.items[0]["answer_complete"] is True
    assert result.items[0]["citations"] == [{"url": "https://example.test/source"}]
    assert result.items[0]["capture_receipt"]["persisted"] is True
    assert len(persisted_receipts) == 1
    assert persisted_receipts[0]["run_id"] == "run"
    assert persisted_receipts[0]["workflow_id"] == "workflow"
    assert persisted_receipts[0]["question"] == "q"
    assert persisted_receipts[0]["package_digest"] == package.digest
    assert persisted_receipts[0]["evidence"]["kind"] == "doubao.capture.pre_cleanup"


@pytest.mark.asyncio
async def test_agent_capture_fails_closed_when_conversation_cleanup_is_unconfirmed(
    monkeypatch,
):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )

    async def fake_select(_session, _adapter_config):
        return "http://agent-1:19823", "bbx"

    async def fake_send(_agent_url, _task, on_event, timeout):
        assert timeout >= 30
        await on_event(
            {
                "type": "evidence",
                "evidence": {
                    "kind": "doubao.capture.pre_cleanup",
                    "response": {"answer": "完整回答", "conversation_deleted": False},
                },
            }
        )
        return {
            "type": "done",
            "result": {
                "text": (
                    '{"status":"completed","answer":"完整回答",'
                    '"answer_complete":true,"conversation_deleted":false,'
                    '"links":[],"suggested_keywords":[]}'
                )
            },
        }

    monkeypatch.setattr(runtime, "_select_local_agent", fake_select)
    monkeypatch.setattr("backend.ws_agent_manager.send_agent_task", fake_send)
    monkeypatch.setattr(
        runtime,
        "write_precleanup_capture_receipt",
        lambda *_args, **_kwargs: _persisted_capture_receipt(),
    )

    result = await runtime._capture_live_doubao_via_agent(
        package=package,
        adapter_config={},
        session=None,
        workflow_id="workflow",
        run_id="run",
    )

    assert result.success is False
    assert result.error_type == "doubao_conversation_cleanup_failed"


@pytest.mark.asyncio
async def test_agent_capture_rejects_deleted_conversation_without_durable_receipt(monkeypatch):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )

    async def fake_select(_session, _adapter_config):
        return "http://agent-1:19823", "bbx"

    async def fake_send(_agent_url, _task, _on_event, timeout):
        return {
            "type": "done",
            "result": {
                "text": (
                    '{"status":"completed","answer":"完整回答",'
                    '"answer_complete":true,"conversation_deleted":true,'
                    '"links":[],"suggested_keywords":[]}'
                )
            },
        }

    monkeypatch.setattr(runtime, "_select_local_agent", fake_select)
    monkeypatch.setattr("backend.ws_agent_manager.send_agent_task", fake_send)

    result = await runtime._capture_live_doubao_via_agent(
        package=package,
        adapter_config={},
        session=None,
        workflow_id="workflow",
        run_id="run",
    )

    assert result.success is False
    assert result.error_type == "doubao_capture_persistence_missing"


def test_capture_mapping_keeps_package_and_independent_evidence():
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    mapped = map_capture_item(
        {
            "content": "answer https://example.test/source",
            "citations": [{"url": "https://example.test/source"}],
            "citation_capture": "answer_url_extraction",
            "conversation_url": "https://www.doubao.com/chat/123",
        },
        package=package,
        workflow_id="wf",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )

    evidence = mapped["gaojixing"]["evidence"]
    assert mapped["packageDigest"] == package.digest
    assert evidence["answer"]["artifactId"] == "artifact"
    assert evidence["citations"]["verified"] is False
    assert evidence["conversation"]["status"] == "captured"
    assert mapped["dedupe"] == {
        "type": "source-identity",
        "field": "conversation_url",
        "identity": "https://www.doubao.com/chat/123",
        "value": "https://www.doubao.com/chat/123",
        "status": "unique",
    }


def test_capture_mapping_does_not_invent_malformed_optional_evidence():
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    mapped = map_capture_item(
        {
            "content": "answer",
            "citations": "not-a-citation-list",
            "conversation_url": {"url": "https://www.doubao.com/chat/guessed"},
        },
        package=package,
        workflow_id="wf",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )

    evidence = mapped["gaojixing"]["evidence"]
    assert evidence["citations"] == {
        "status": "empty",
        "capture": "answer_url_extraction",
        "verified": False,
        "items": [],
    }
    assert evidence["conversation"] == {"status": "unknown", "url": None}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.doubao.com:443/chat/123", False),
        ("https://doubao.com?conversation=123", False),
        ("https://www.doubao.com./chat/123", False),
        ("https://doubao.com.evil.test/source", True),
        ("https://example.test/source", True),
    ],
)
def test_external_evidence_url_filters_normalized_doubao_hosts(url, expected):
    assert runtime._is_external_evidence_url(url) is expected


class _Emitter:
    def __init__(self):
        self.events = []

    def emit(self, node, event_type, **kwargs):
        self.events.append((node.id, event_type, kwargs))


@pytest.mark.asyncio
async def test_hda_live_source_branch_maps_capture_output(monkeypatch):
    node = SimpleNamespace(
        id="gaojixing-source",
        kind="source",
        adapter=None,
        depends_on=[],
        params={"question": "configured", "sourceGroup": "gaojixing"},
        runtime={
            "binding": {
                "binding_id": "workflow.source.fetch",
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "live",
                    "adapterConfig": {"capabilityId": GAOJIXING_CAPABILITY_ID},
                },
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={"question": "runtime"}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=True),
        ),
    )
    emitter = _Emitter()
    outputs = {}

    async def fake_capture(**kwargs):
        assert kwargs["package"].question == "runtime"
        return ChannelResult.ok(
            [
                {
                    "content": "answer",
                    "citations": [{"url": "https://example.test"}],
                    "conversation_url": "https://www.doubao.com/chat/123",
                }
            ]
        )

    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.capture_live_doubao", fake_capture)
    await _execute_gaojixing_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        trace_id="trace",
        outputs_by_node=outputs,
        emitter=emitter,
        session=None,
    )

    item = outputs["gaojixing-source"][0]
    raw = item["raw"]
    assert raw["gaojixing"]["mode"] == "live"
    assert raw["gaojixing"]["evidence"]["packageDigest"] == raw["packageDigest"]
    assert item["lineage"][0]["artifact"] == "gaojixing.capture"
    assert [event[1] for event in emitter.events] == ["partial", "completed"]


@pytest.mark.asyncio
async def test_hda_live_source_preserves_feishu_identity_and_business_number(monkeypatch):
    node = SimpleNamespace(
        id="gaojixing-source",
        kind="source",
        adapter=None,
        depends_on=["feishu-keywords"],
        params={"question": "{{keyword}}", "sourceGroup": "gaojixing"},
        runtime={
            "binding": {
                "binding_id": "workflow.source.fetch",
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "live",
                    "adapterConfig": {"capabilityId": GAOJIXING_CAPABILITY_ID},
                },
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=True),
        ),
    )
    emitter = _Emitter()
    outputs = {
        "feishu-keywords": [
            {
                "raw": {
                    "id": "feishu:source:rec-23",
                    "source_row_id": "rec-23",
                    "source_number": "23",
                    "keyword": "宝宝DHA",
                    "source": "feishu_table",
                    "source_group": "feishu-keywords",
                    "fields": {"编号": "23", "推荐追问": "宝宝DHA"},
                    "feishu": {"record_id": "rec-23", "table_id": "sheet-1"},
                },
                "lineage": [{"nodeId": "feishu-keywords", "index": 22}],
            }
        ]
    }

    async def fake_capture(**kwargs):
        assert kwargs["package"].question == "宝宝DHA"
        return ChannelResult.ok(
            [
                {
                    "content": "完整回答",
                    "answer_complete": True,
                    "conversation_deleted": True,
                    "citations": [{"url": "https://example.test/source"}],
                    "conversation_url": "https://www.doubao.com/chat/123",
                }
            ]
        )

    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.capture_live_doubao", fake_capture)
    await _execute_gaojixing_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        trace_id="trace",
        outputs_by_node=outputs,
        emitter=emitter,
        session=None,
    )

    item = outputs["gaojixing-source"][0]
    raw = item["raw"]
    assert raw["source_row_id"] == "rec-23"
    assert raw["source_number"] == "23"
    assert raw["source_fields"] == {"编号": "23", "推荐追问": "宝宝DHA"}
    assert raw["source_record"]["table_id"] == "sheet-1"
    assert raw["dedupe"] == {
        "type": "source-identity",
        "field": "source_row_id",
        "identity": "rec-23",
        "value": "rec-23",
        "status": "unique",
    }
    assert item["lineage"][-1]["sourceRowId"] == "rec-23"
    assert item["lineage"][-1]["sourceNumber"] == "23"
    partial_details = emitter.events[-2][2]["details"]
    assert partial_details["sourceRecords"] == [
        {
            "source_row_id": "rec-23",
            "source_number": "23",
            "source_fields": {"编号": "23", "推荐追问": "宝宝DHA"},
        }
    ]


@pytest.mark.asyncio
async def test_hda_live_source_skips_doubao_when_upstream_is_empty(monkeypatch):
    node = SimpleNamespace(
        id="gaojixing-source",
        kind="source",
        adapter=None,
        depends_on=["feishu-keywords"],
        params={"question": "{{keyword}}", "sourceGroup": "gaojixing"},
        runtime={
            "binding": {
                "binding_id": "workflow.source.fetch",
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "live",
                    "adapterConfig": {"capabilityId": GAOJIXING_CAPABILITY_ID},
                },
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=True),
        ),
    )
    emitter = _Emitter()
    outputs = {"feishu-keywords": []}

    async def should_not_capture(**_kwargs):
        raise AssertionError("Doubao must not run without an eligible Feishu row")

    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer.capture_live_doubao",
        should_not_capture,
    )

    await _execute_gaojixing_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        trace_id="trace",
        outputs_by_node=outputs,
        emitter=emitter,
        session=None,
    )

    assert outputs["gaojixing-source"] == []
    assert [event[1] for event in emitter.events] == ["completed"]
    assert emitter.events[0][2]["details"]["inputItemCount"] == 0
    assert emitter.events[0][2]["details"]["outputItemCount"] == 0


@pytest.mark.asyncio
async def test_hda_live_source_keeps_completed_prefix_when_later_capture_fails(monkeypatch):
    node = SimpleNamespace(
        id="gaojixing-source",
        kind="source",
        adapter=None,
        depends_on=["feishu-keywords"],
        params={"question": "{{keyword}}", "sourceGroup": "gaojixing"},
        runtime={
            "binding": {
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "live",
                    "adapterConfig": {"capabilityId": GAOJIXING_CAPABILITY_ID},
                }
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=True),
        ),
    )
    emitter = _Emitter()
    outputs = {
        "feishu-keywords": [
            {"raw": {"source_row_id": "rec-1", "keyword": "问题一"}, "lineage": []},
            {"raw": {"source_row_id": "rec-2", "keyword": "问题二"}, "lineage": []},
        ]
    }
    calls = 0

    async def fake_capture(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            return ChannelResult.fail("agent disconnected", error_type="AgentDisconnected")
        return ChannelResult.ok(
            [
                {
                    "content": "完整回答",
                    "answer_complete": True,
                    "conversation_deleted": True,
                    "citations": [],
                    "conversation_url": "https://www.doubao.com/chat/1",
                }
            ]
        )

    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.capture_live_doubao", fake_capture)
    await _execute_gaojixing_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        trace_id="trace",
        outputs_by_node=outputs,
        emitter=emitter,
        session=None,
    )

    assert len(outputs["gaojixing-source"]) == 1
    assert outputs["gaojixing-source"][0]["raw"]["source_row_id"] == "rec-1"
    assert [event[1] for event in emitter.events][-2:] == ["partial", "failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capture_result", "expected_code"),
    [
        (ChannelResult.fail("Doubao request timed out", error_type="TimeoutError"), "TimeoutError"),
        (
            ChannelResult.fail("CAPTCHA challenge", error_type="captcha_challenge"),
            "captcha_challenge",
        ),
        (ChannelResult.ok([{"content": " "}]), "gaojixing_answer_missing"),
    ],
)
async def test_hda_live_source_fails_closed_on_capture_errors(
    monkeypatch,
    capture_result,
    expected_code,
):
    node = SimpleNamespace(
        id="gaojixing-source",
        kind="source",
        adapter=None,
        depends_on=[],
        params={"question": "q", "sourceGroup": "gaojixing"},
        runtime={
            "binding": {
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "live",
                    "adapterConfig": {"capabilityId": GAOJIXING_CAPABILITY_ID},
                }
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=True),
        ),
    )
    emitter = _Emitter()
    outputs = {}

    async def fake_capture(**_kwargs):
        return capture_result

    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.capture_live_doubao", fake_capture)
    await _execute_gaojixing_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        trace_id="trace",
        outputs_by_node=outputs,
        emitter=emitter,
        session=None,
    )

    assert outputs["gaojixing-source"] == []
    assert emitter.events[-1][1] == "failed"
    assert emitter.events[-1][2]["block_reason"].code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "provenance", "business_outcome"),
    [
        ("live", "opencli:doubao", "unconfirmed"),
        ("fixture", "fixture:contract-vector", "fixture"),
    ],
)
async def test_replay_preserves_provenance_for_webhook_delivery(
    monkeypatch,
    mode,
    provenance,
    business_outcome,
):
    source_node = SimpleNamespace(
        id="source",
        kind="source",
        params={},
        adapter=None,
        runtime={"binding": {"input": {"channelType": "doubao_research", "liveMode": mode}}},
    )
    source_input = SimpleNamespace(
        sourceId=None,
        model_copy=lambda *, update: SimpleNamespace(**{**source_input.__dict__, **update}),
    )
    source_request = SimpleNamespace(
        project=object(),
        input=source_input,
        model_copy=lambda *, update, deep: SimpleNamespace(**{**source_request.__dict__, **update}),
    )
    persisted_evidence = {
        "mode": mode,
        "provenance": provenance,
        "packageDigest": "digest",
        "runId": "source-run",
        "workflowId": "workflow",
        "nodeId": "source",
        "answer": {"artifactId": "artifact", "text": "persisted answer"},
        "citations": {"items": []},
        "conversation": {"url": "https://example.test/conversation"},
    }
    event_details = {
        "channelType": "doubao_research",
        "capabilityId": "chat-ai.capture",
        "artifacts": ["artifact"],
        "evidence": [persisted_evidence],
    }
    if mode == "live":
        event_details.update(
            {
                "packages": [{"digest": "digest"}],
                "sourceRecords": [
                    {
                        "source_row_id": "rec-23",
                        "source_number": "23",
                        "source_fields": {"编号": "23", "推荐追问": "宝宝DHA"},
                    }
                ],
            }
        )
    else:
        event_details["package"] = {"digest": "digest"}
    source_run = SimpleNamespace(
        projection=SimpleNamespace(status="completed", workflowId="workflow"),
        studio_workflow_version_id="version",
        workflow_version_id=None,
        request=source_request,
        events=[
            SimpleNamespace(
                nodeId="source",
                eventType="partial",
                details=event_details,
            ),
            SimpleNamespace(nodeId="source", eventType="completed"),
        ],
    )
    record = SimpleNamespace(
        raw_data={
            "gaojixing": {"artifactId": "artifact", "package": {"digest": "digest"}},
            "_workflowLineage": [
                {
                    "nodeId": "source",
                    "artifact": "gaojixing.capture",
                    "artifactId": "artifact",
                    "packageDigest": "digest",
                }
            ],
        }
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [record]))
        )
    )
    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer._load_workflow_run",
        AsyncMock(side_effect=[source_run, None]),
    )
    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer.compile_workflow_project",
        lambda _project: SimpleNamespace(
            valid=True, plan=SimpleNamespace(runtime=SimpleNamespace(nodes=[source_node]))
        ),
    )
    start = AsyncMock(return_value=SimpleNamespace(runId="replay-run"))
    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.start_workflow_run", start)

    replay = await replay_downstream_from_persisted_gaojixing_source(
        "source-run",
        expected_workflow_id="workflow",
        expected_studio_workflow_version_id="version",
        session=session,
    )

    assert replay.runId == "replay-run"
    request = start.await_args.args[0]
    assert request.input.sourceId == "source-run"
    replay_raw = request.sourceOutputs["source"][0]["raw"]
    assert replay_raw["gaojixing"]["artifactId"] == "artifact"
    assert replay_raw["gaojixing"]["provenance"] == provenance
    if mode == "live":
        assert replay_raw["source_row_id"] == "rec-23"
        assert replay_raw["source_number"] == "23"
        assert replay_raw["source_fields"] == {"编号": "23", "推荐追问": "宝宝DHA"}
        assert replay_raw["dedupe"]["identity"] == "rec-23"
    assert request.sourceOutputs["source"][0]["lineage"][0]["mode"] == "persisted-replay"
    assert start.await_args.kwargs["replay_source_node_ids"] == {"source"}

    from backend.notifiers.base import NotificationSendResult
    from backend.workflow.webhook_delivery import execute_workflow_webhook_delivery

    class _Notifier:
        async def send(self, _config, _payload):
            return NotificationSendResult(success=True, response_data=None)

    monkeypatch.setattr(
        "backend.workflow.webhook_delivery.get_notifier",
        lambda _kind: _Notifier(),
    )
    delivery = await execute_workflow_webhook_delivery(
        {},
        request.sourceOutputs["source"],
        workflow_id="workflow",
        run_id="replay-run",
        node_id="notify",
    )
    assert delivery["mode"] == mode
    assert delivery["provenance"] == provenance
    assert delivery["businessOutcome"] == business_outcome
    assert delivery["liveAccepted"] is False


@pytest.mark.asyncio
async def test_record_sink_persists_gaojixing_refs_and_collection_lineage(monkeypatch):
    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {
            "content": "answer",
            "citations": [{"url": "https://example.test/source"}],
            "conversation_url": "https://www.doubao.com/chat/123",
        },
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )
    source_node = SimpleNamespace(
        id="source",
        kind="source",
        adapter=None,
        depends_on=[],
        params={"sourceGroup": "gaojixing"},
        runtime={
            "binding": {
                "input": {"channelType": "doubao_research"},
                "binding_id": "workflow.source.fetch",
            }
        },
    )
    sink_node = SimpleNamespace(
        id="sink",
        kind="sink",
        adapter=None,
        depends_on=["source"],
        params={},
        runtime={"binding": {"binding_id": "workflow.record-sink.records", "input": {}}},
    )

    class _Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: [SimpleNamespace(id="record-id", content_hash=captured["hash"])]
            )

        async def flush(self):
            return None

    captured = {}

    async def fake_materialize(*args, **kwargs):
        return "source-id", "task-id"

    async def fake_store(*args, **kwargs):
        captured["hash"] = args[3][0][2]
        captured["lineage"] = kwargs["lineage"]
        return [SimpleNamespace(id="record-id")], 0

    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer._materialize_source_task",
        fake_materialize,
    )
    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.store_records", fake_store)
    stored, skipped = await _store_record_sink_outputs(
        sink_node,
        [{"raw": raw, "lineage": [{"artifact": "gaojixing.capture", "nodeId": "source"}]}],
        run_id="run",
        workflow_id="workflow",
        target="records",
        session=_Session(),
        runtime_nodes_by_id={"source": source_node},
        materialized_source_tasks={},
    )

    assert skipped == 0
    assert stored[0]["recordId"] == "record-id"
    assert stored[0]["raw"]["packageDigest"] == package.digest
    assert stored[0]["normalizedData"]["packageDigest"] == package.digest
    artifact_refs = captured["lineage"]["artifact_refs"]
    assert artifact_refs[1]["artifactId"] == "artifact"
    assert artifact_refs[1]["packageDigest"] == package.digest
    assert captured["lineage"]["collection_run_id"] == "run"


@pytest.mark.asyncio
async def test_gaojixing_delivery_distinguishes_transport_from_business_ack(monkeypatch):
    from backend.notifiers.base import NotificationSendResult
    from backend.workflow.webhook_delivery import execute_workflow_webhook_delivery

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "answer", "citations": [], "conversation_url": ""},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )
    captured = {}

    class _Notifier:
        async def send(self, config, payload):
            captured["payload"] = payload
            return NotificationSendResult(success=True, response_data=None)

    monkeypatch.setattr(
        "backend.workflow.webhook_delivery.get_notifier",
        lambda _kind: _Notifier(),
    )
    result = await execute_workflow_webhook_delivery(
        {"target": "business", "config": {"url": "https://example.test/hook"}},
        [{"raw": raw, "lineage": [{"nodeId": "source"}]}],
        workflow_id="workflow",
        run_id="run",
        node_id="notify",
    )

    assert result["transportStatus"] == "accepted"
    assert result["businessOutcome"] == "unconfirmed"
    assert result["ackEvidence"] is None
    assert result["packageDigest"] == package.digest
    assert captured["payload"].delivery_id == result["deliveryAttemptId"]


@pytest.mark.asyncio
async def test_matching_destination_ack_confirms_one_idempotent_business_outcome(monkeypatch):
    from backend.notifiers.base import NotificationSendResult
    from backend.workflow.webhook_delivery import execute_workflow_webhook_delivery

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )

    class _Notifier:
        async def send(self, _config, payload):
            return NotificationSendResult(
                success=True,
                response_data={
                    "businessAck": True,
                    "deliveryAttemptId": payload.delivery_id,
                },
            )

    monkeypatch.setattr(
        "backend.workflow.webhook_delivery.get_notifier",
        lambda _kind: _Notifier(),
    )
    first = await execute_workflow_webhook_delivery(
        {},
        [{"raw": raw, "lineage": [{"nodeId": "source"}]}],
        workflow_id="workflow",
        run_id="run",
        node_id="notify",
    )
    retry = await execute_workflow_webhook_delivery(
        {},
        [{"raw": raw, "lineage": [{"nodeId": "source"}]}],
        workflow_id="workflow",
        run_id="run",
        node_id="notify",
    )

    assert first["deliveryAttemptId"] == retry["deliveryAttemptId"]
    assert first["transportStatus"] == "accepted"
    assert first["businessOutcome"] == "confirmed"
    assert first["liveAccepted"] is True
    assert first["ackEvidence"]["matchesDeliveryAttempt"] is True


@pytest.mark.asyncio
async def test_unmatched_destination_ack_remains_unconfirmed(monkeypatch):
    from backend.notifiers.base import NotificationSendResult
    from backend.workflow.webhook_delivery import execute_workflow_webhook_delivery

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )

    class _Notifier:
        async def send(self, _config, _payload):
            return NotificationSendResult(
                success=True,
                response_data={"businessAck": True, "deliveryAttemptId": "another-attempt"},
            )

    monkeypatch.setattr(
        "backend.workflow.webhook_delivery.get_notifier",
        lambda _kind: _Notifier(),
    )
    result = await execute_workflow_webhook_delivery(
        {},
        [{"raw": raw, "lineage": [{"nodeId": "source"}]}],
        workflow_id="workflow",
        run_id="run",
        node_id="notify",
    )

    assert result["transportStatus"] == "accepted"
    assert result["businessOutcome"] == "unconfirmed"
    assert result["liveAccepted"] is False
    assert result["ackEvidence"]["matchesDeliveryAttempt"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("notifier", "webhook_delivery_failed"),
        ("network", "webhook_delivery_network_error"),
    ],
)
async def test_destination_failures_preserve_typed_unknown_business_state(
    monkeypatch,
    failure_kind,
    expected_code,
):
    from backend.notifiers.base import NotificationSendResult
    from backend.workflow.webhook_delivery import (
        WorkflowWebhookDeliveryError,
        execute_workflow_webhook_delivery,
    )

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )

    class _Notifier:
        async def send(self, _config, _payload):
            if failure_kind == "network":
                raise OSError("destination timed out")
            return NotificationSendResult(success=False, response_data=None)

    monkeypatch.setattr(
        "backend.workflow.webhook_delivery.get_notifier",
        lambda _kind: _Notifier(),
    )
    with pytest.raises(WorkflowWebhookDeliveryError) as error:
        await execute_workflow_webhook_delivery(
            {},
            [{"raw": raw, "lineage": [{"nodeId": "source"}]}],
            workflow_id="workflow",
            run_id="run",
            node_id="notify",
        )

    assert error.value.code == expected_code
    assert error.value.details["transportStatus"] == "failed"
    assert error.value.details["businessOutcome"] == "unknown"
    assert error.value.details["deliveryAttemptId"]


@pytest.mark.asyncio
async def test_fixture_source_labels_digest_evidence_and_lineage_without_live_dispatch(
    monkeypatch,
):
    node = SimpleNamespace(
        id="fixture-source",
        kind="source",
        adapter=None,
        depends_on=[],
        params={
            "question": "configured",
            "sourceGroup": "gaojixing",
            "fixtureItems": [
                {
                    "content": "fixture answer",
                    "citations": [{"url": "https://fixture.test/citation"}],
                    "conversation_url": "https://fixture.test/conversation",
                }
            ],
        },
        runtime={
            "binding": {
                "binding_id": "workflow.source.fetch",
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "fixture",
                    "adapterConfig": {"fixtureProvenance": "fixture:contract-vector"},
                },
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={"question": "runtime"}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=False),
        ),
        sourceOutputs={},
    )
    emitter = _Emitter()
    outputs = {}

    async def should_not_dispatch_live(**_kwargs):
        raise AssertionError("fixture execution must not dispatch the live channel")

    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer.capture_live_doubao", should_not_dispatch_live
    )
    await _execute_gaojixing_fixture_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        outputs_by_node=outputs,
        emitter=emitter,
    )

    raw = outputs["fixture-source"][0]["raw"]
    evidence = raw["gaojixing"]["evidence"]
    assert raw["gaojixing"]["mode"] == "fixture"
    assert raw["gaojixing"]["provenance"] == "fixture:contract-vector"
    assert evidence["mode"] == "fixture"
    assert evidence["provenance"] == "fixture:contract-vector"
    assert raw["packageDigest"] == build_question_package(
        node_params=node.params,
        adapter_config={"fixtureProvenance": "fixture:contract-vector"},
        runtime_payload={"question": "runtime"},
    ).digest
    assert outputs["fixture-source"][0]["lineage"][0]["mode"] == "fixture"
    assert (
        outputs["fixture-source"][0]["lineage"][0]["provenance"]
        == "fixture:contract-vector"
    )
    assert emitter.events[-2][2]["details"]["liveAccepted"] is False


@pytest.mark.asyncio
async def test_live_source_never_falls_back_to_fixture_items_when_session_is_unavailable(
    monkeypatch,
):
    node = SimpleNamespace(
        id="live-source",
        kind="source",
        adapter=None,
        depends_on=[],
        params={"question": "q", "fixtureItems": [{"content": "must not be used"}]},
        runtime={
            "binding": {
                "input": {
                    "channelType": "doubao_research",
                    "liveMode": "live",
                    "adapterConfig": {"capabilityId": GAOJIXING_CAPABILITY_ID},
                }
            }
        },
    )
    body = SimpleNamespace(
        input=SimpleNamespace(payload={}),
        project=SimpleNamespace(
            id="workflow",
            agentPermissions=SimpleNamespace(canFetchNetwork=True),
        ),
    )
    emitter = _Emitter()
    outputs = {}

    async def unavailable(**_kwargs):
        raise GaojixingReadinessError("gaojixing_session_unavailable", "session unavailable")

    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.capture_live_doubao", unavailable)
    await _execute_gaojixing_source(
        node,
        body=body,
        run_id="run",
        workflow_id="workflow",
        trace_id="trace",
        outputs_by_node=outputs,
        emitter=emitter,
        session=None,
    )

    assert outputs["live-source"] == []
    assert [event[1] for event in emitter.events] == ["blocked"]
    details = emitter.events[0][2]
    assert details["details"] == {"nodeId": "live-source"}
    assert details["block_reason"].code == "gaojixing_session_unavailable"


@pytest.mark.asyncio
async def test_mock_delivery_ack_remains_mock_business_outcome(monkeypatch):
    from backend.notifiers.base import NotificationSendResult
    from backend.workflow.webhook_delivery import execute_workflow_webhook_delivery

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "mock answer", "citations": [], "conversation_url": ""},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
        mode="mock",
        provenance="mock:contract-vector",
    )
    captured = {}

    class _Notifier:
        async def send(self, _config, payload):
            captured["payload"] = payload
            return NotificationSendResult(success=True, response_data={"businessAck": True})

    monkeypatch.setattr(
        "backend.workflow.webhook_delivery.get_notifier",
        lambda _kind: _Notifier(),
    )
    result = await execute_workflow_webhook_delivery(
        {"target": "business", "config": {"url": "https://example.test/hook"}},
        [{"raw": raw, "lineage": [{"nodeId": "source", "mode": "mock"}]}],
        workflow_id="workflow",
        run_id="run",
        node_id="notify",
    )

    assert result["transportStatus"] == "accepted"
    assert result["businessOutcome"] == "mock"
    assert result["ackEvidence"]["status"] == "confirmed"
    assert result["liveAccepted"] is False
    assert result["mode"] == "mock"
    assert result["provenance"] == "mock:contract-vector"
    assert captured["payload"].data["mode"] == "mock"


@pytest.mark.asyncio
async def test_delivery_rejects_mixed_live_and_mock_contexts():
    from backend.workflow.webhook_delivery import (
        WorkflowWebhookDeliveryError,
        execute_workflow_webhook_delivery,
    )

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    live = map_capture_item(
        {"content": "live answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="live-artifact",
    )
    mock = map_capture_item(
        {"content": "mock answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="mock-artifact",
        mode="mock",
        provenance="mock:contract-vector",
    )

    with pytest.raises(WorkflowWebhookDeliveryError) as error:
        await execute_workflow_webhook_delivery(
            {},
            [{"raw": live}, {"raw": mock}],
            workflow_id="workflow",
            run_id="run",
            node_id="notify",
        )

    assert error.value.code == "gaojixing_delivery_context_mismatch"
    assert error.value.details["inconsistentFields"] == ["mode", "provenance"]


@pytest.mark.asyncio
async def test_delivery_rejects_contradictory_evidence_envelope():
    from backend.workflow.webhook_delivery import (
        WorkflowWebhookDeliveryError,
        execute_workflow_webhook_delivery,
    )

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )
    raw["gaojixing"]["evidence"]["mode"] = "mock"

    with pytest.raises(WorkflowWebhookDeliveryError) as error:
        await execute_workflow_webhook_delivery(
            {},
            [{"raw": raw}],
            workflow_id="workflow",
            run_id="run",
            node_id="notify",
        )

    assert error.value.code == "gaojixing_delivery_evidence_mismatch"


@pytest.mark.asyncio
async def test_delivery_rejects_cross_run_and_package_lineage():
    from backend.workflow.webhook_delivery import (
        WorkflowWebhookDeliveryError,
        execute_workflow_webhook_delivery,
    )

    package = build_question_package(
        node_params={"question": "q"}, adapter_config={}, runtime_payload={}
    )
    raw = map_capture_item(
        {"content": "answer"},
        package=package,
        workflow_id="workflow",
        run_id="run",
        node_id="source",
        artifact_id="artifact",
    )

    with pytest.raises(WorkflowWebhookDeliveryError) as error:
        await execute_workflow_webhook_delivery(
            {},
            [
                {
                    "raw": raw,
                    "lineage": [
                        {
                            "nodeId": "source",
                            "packageDigest": "another-package",
                            "runId": "another-run",
                        }
                    ],
                }
            ],
            workflow_id="workflow",
            run_id="run",
            node_id="notify",
        )

    assert error.value.code == "gaojixing_delivery_lineage_mismatch"
    assert error.value.details["mismatchedFields"] == [
        "sourceLineage.packageDigest",
        "sourceLineage.runId",
    ]
