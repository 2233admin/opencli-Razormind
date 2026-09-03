"""Resolve compiled workflow nodes to backend runtime bindings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.workflow import (
    WorkflowAdapterBinding,
    WorkflowProjectNode,
    WorkflowRuntimeResourceRequirement,
)
from backend.workflow.block_reasons import (
    MISSING_DELIVERY_PROJECTION,
    MISSING_RUNTIME_BINDING,
    MISSING_RUNTIME_IO_CONTRACT,
    MISSING_RUNTIME_PARAMETER,
    MISSING_TOOL_CAPABILITY_BINDING,
    MISSING_TURBOPUSH_CONTENT_TYPE,
    MISSING_TURBOPUSH_SERVICE,
)
from backend.workflow.data_operators import resolve_data_operator
from backend.workflow.dify_graphon_client import (
    DIFY_GRAPHON_BINDING_ID,
    DIFY_GRAPHON_COMMIT,
    DIFY_GRAPHON_CONTRACT_VERSION,
    DIFY_GRAPHON_SOURCE_FORMAT,
    DIFY_GRAPHON_VERSION,
)
from backend.workflow.native_node_runtime import prepare_native_node
from backend.workflow.runtime_contracts import runtime_io_contract_manifest
from backend.workflow.tool_capabilities import resolve_workflow_tool_capability
from backend.workflow.turbopush_runtime import (
    TURBOPUSH_BINDING_ID,
    TURBOPUSH_CHANNEL,
    TURBOPUSH_MCP_SERVER,
    TURBOPUSH_PROVIDER,
    normalize_turbopush_content_type,
    resolve_turbopush_service_resource,
    turbopush_binding_input,
    turbopush_platform_projection,
)

OPENCLI_BINDING_ID = "iii.collector-opencli.snapshot"
OPENCLI_WORKER = "collector-opencli"
OPENCLI_FUNCTION_ID = "odp.collect::opencli_snapshot"
DEMAND_DRAFT_BINDING_ID = "workflow.demand-draft.patch"
SCHEDULE_TRIGGER_BINDING_ID = "workflow.trigger.schedule_tick"
WEBHOOK_TRIGGER_BINDING_ID = "workflow.trigger.webhook_input"
SOURCE_FETCH_BINDING_ID = "workflow.source.fetch"
SOURCE_POOL_BINDING_ID = "workflow.source-pool.parallel-fanout"
COLLECTION_OUTPUT_BINDING_ID = "workflow.collection-output.items"
NORMALIZE_BINDING_ID = "workflow.transform.normalize"
DATA_OPERATOR_CATALOG_BINDINGS = {
    "intelligence.data.generate": "workflow.data.generate",
    "intelligence.data.filter": "workflow.data.filter",
    "intelligence.data.evaluate": "workflow.data.evaluate",
    "intelligence.data.refine": "workflow.data.refine",
}
DEDUPE_BINDING_ID = "workflow.transform.dedupe"
MERGE_BINDING_ID = "workflow.flow.merge"
ROUTER_ROUTE_BINDING_ID = "workflow.router.route"
RECORD_ACCEPTANCE_BINDING_ID = "workflow.gate.record-acceptance"
RECORD_SINK_BINDING_ID = "workflow.record-sink.records"
FEISHU_BITABLE_SINK_BINDING_ID = "workflow.feishu-bitable.records"
INBOX_STORE_BINDING_ID = "workflow.inbox.store"
WEBHOOK_NOTIFY_BINDING_ID = "workflow.notifier.webhook.send"
NOTIFY_SEND_BINDING_ID = "workflow.notify.send"
EXTERNAL_TOOL_BINDING_ID = "workflow.external-tool.capability"
IMAGE_GENERATION_BINDING_ID = "workflow.media.image-generation"
IMAGE_ASSET_BINDING_ID = "workflow.media.image-asset"
COLLECTOR_SOURCE_TYPES = {"web", "api", "rss", "cli"}
COLLECTOR_BINDING_PREFIX = "collection.source."
SUPPORTED_TOOL_EXECUTOR_MODES = {
    "fixture",
    "okx_market_ticker_snapshot",
    "joyai_vl_interaction",
    "situation_awareness",
    "swarm_simulation",
    "native_intelligence",
    "opentabs",
    "bbx",
    "kats_runtime",
    "gaojixing_doubao_batch",
    "gaojixing_batch_certify",
}
_LEGACY_DATA_OPERATOR_PACK_VERSION = "1.0.0"
_SENSITIVE_CONFIG_KEYS = {
    "accesstoken",
    "authorization",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "cookie",
    "password",
    "secret",
    "token",
    "apikey",
    "xapikey",
    "xapikey",
    "refreshtoken",
}


class WorkflowRuntimeBinding(BaseModel):
    status: Literal["bound"] = "bound"
    binding_id: str
    runtime: Literal["iii"]
    worker: str
    function_id: str
    channel: str
    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowMissingRuntime(BaseModel):
    status: Literal["missing"] = "missing"
    code: str
    node_id: str
    kind: str
    capability: str
    adapter_id: str | None = None
    provider: str | None = None
    required_params: list[str] = Field(default_factory=list)
    message: str


def resolve_runtime_metadata(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    *,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Return runtime binding metadata for a compiled WorkflowProject node."""

    resolved_node_id = node_id or node.id
    native_metadata = _resolve_native_node(node, node_id=resolved_node_id)
    if native_metadata is not None:
        metadata = native_metadata
    elif _is_dify_graphon_managed_package(node):
        metadata = _resolve_dify_graphon_package(node, node_id=resolved_node_id)
    elif _is_image_generation(node):
        metadata = _resolve_image_generation(node, node_id=resolved_node_id)
    elif _is_image_asset(node):
        metadata = _resolve_image_asset(node, node_id=resolved_node_id)
    elif _is_collection_need(node):
        metadata = _resolve_collection_need(node, node_id=resolved_node_id)
    elif _is_webhook_trigger(node):
        metadata = _resolve_webhook_trigger(node, node_id=resolved_node_id)
    elif _is_schedule_trigger(node):
        metadata = _resolve_schedule_trigger(node, node_id=resolved_node_id)
    elif _is_source_pool(node):
        metadata = _resolve_source_pool(node, node_id=resolved_node_id)
    elif _is_collection_output(node):
        metadata = _resolve_collection_output(node, node_id=resolved_node_id)
    elif _is_data_operator_node(node):
        metadata = _resolve_data_operator_node(node, node_id=resolved_node_id)
    elif _is_normalize_node(node):
        metadata = _resolve_normalize_node(node, node_id=resolved_node_id)
    elif _is_dedupe_node(node):
        metadata = _resolve_dedupe_node(node, node_id=resolved_node_id)
    elif _is_merge_node(node):
        metadata = _resolve_merge_node(node, node_id=resolved_node_id)
    elif _is_router_route_node(node):
        metadata = _resolve_router_route_node(node, node_id=resolved_node_id)
    elif _is_record_acceptance_gate(node):
        metadata = _resolve_record_acceptance_gate(node, node_id=resolved_node_id)
    elif _is_feishu_bitable_sink(node):
        metadata = _resolve_feishu_bitable_sink(node, node_id=resolved_node_id)
    elif _is_record_sink(node):
        metadata = _resolve_record_sink(node, node_id=resolved_node_id)
    elif _is_inbox_store_node(node):
        metadata = _resolve_inbox_store_node(node, node_id=resolved_node_id)
    elif _is_opencli_node(node, adapter):
        metadata = _resolve_opencli_node(node, adapter, node_id=resolved_node_id)
    elif _is_external_tool_capability(node):
        metadata = _resolve_external_tool_capability(node, node_id=resolved_node_id)
    elif _is_turbopush_publish(node, adapter):
        metadata = _resolve_turbopush_publish(node, adapter, node_id=resolved_node_id)
    elif _is_webhook_notifier(node, adapter):
        metadata = _resolve_webhook_notifier(node, adapter, node_id=resolved_node_id)
    elif _is_source_fetch_node(node, adapter):
        metadata = _resolve_source_fetch_node(node, adapter, node_id=resolved_node_id)
    elif _is_notify_send_node(node, adapter):
        metadata = _resolve_notify_send_node(node, adapter, node_id=resolved_node_id)
    else:
        metadata = {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_RUNTIME_BINDING,
                    node_id=resolved_node_id,
                    kind=node.kind,
                    capability=node.capability,
                    adapter_id=adapter.id if adapter else None,
                    provider=adapter.provider if adapter else None,
                    message=(
                        f"No runtime binding registered for workflow.{node.kind}.{node.capability}"
                    ),
                )
            )
        }
    return _attach_runtime_contract(
        metadata,
        node=node,
        adapter=adapter,
        node_id=resolved_node_id,
    )


def _resolve_native_node(
    node: WorkflowProjectNode,
    *,
    node_id: str,
) -> dict[str, Any] | None:
    ui = node.ui or {}
    node_library_id = _read_string(ui.get("primitiveId")) or _read_string(ui.get("catalogId"))
    if node_library_id is None:
        dify_type = _read_string(node.params.get("difyType"))
        node_library_id = f"compat.dify.{dify_type}" if dify_type else None
    if node_library_id is None:
        return None

    preparation = prepare_native_node(node_library_id, node.params)
    if preparation is None:
        return None

    readiness = preparation.model_dump(mode="json")
    if preparation.status != "ready":
        return {
            "native": {
                "node_id": node_id,
                "node_type": preparation.node_type,
                "binding_id": preparation.binding_id,
                "readiness": readiness,
            },
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code="invalid_native_node_config",
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=preparation.errors,
                    message=(
                        f'Native node "{preparation.node_type}" has invalid '
                        "configuration and cannot execute."
                    ),
                )
            ),
        }

    return {
        "binding": {
            "status": "bound",
            "binding_id": preparation.binding_id,
            "runtime": "workflow",
            "channel": "native",
            "executor": preparation.executor,
            "input": {
                "nodeType": preparation.node_type,
                "config": preparation.config,
            },
        },
        "native": {
            "node_id": node_id,
            "node_type": preparation.node_type,
            "binding_id": preparation.binding_id,
            "readiness": readiness,
        },
    }


def _resolve_image_generation(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    canvas_snapshot_id = _read_string(node.params.get("canvasSnapshotId"))
    if canvas_snapshot_id is None:
        return {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_RUNTIME_PARAMETER,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=["canvasSnapshotId"],
                    message=(
                        "Image generation runtime requires an immutable "
                        "node.params.canvasSnapshotId."
                    ),
                )
            )
        }
    return {
        "binding": {
            "status": "bound",
            "binding_id": IMAGE_GENERATION_BINDING_ID,
            "runtime": "workflow",
            "channel": "image-generation",
            "input": {"canvasSnapshotId": canvas_snapshot_id},
        }
    }


def _resolve_image_asset(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    asset_ids = _read_string_list(node.params.get("assetIds"))
    if not asset_ids:
        return {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_RUNTIME_PARAMETER,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=["assetIds"],
                    message="Image asset runtime requires node.params.assetIds.",
                )
            )
        }
    return {
        "binding": {
            "status": "bound",
            "binding_id": IMAGE_ASSET_BINDING_ID,
            "runtime": "workflow",
            "channel": "media-asset",
            "input": {"assetIds": asset_ids},
        }
    }


def _resolve_opencli_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    *,
    node_id: str,
) -> dict[str, Any]:
    site = _read_string(node.params.get("site"))
    command = _read_string(node.params.get("command"))
    missing_params = [
        param_name
        for param_name, param_value in (("site", site), ("command", command))
        if param_value is None
    ]
    if missing_params:
        return {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_RUNTIME_PARAMETER,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    adapter_id=adapter.id if adapter else None,
                    provider=adapter.provider if adapter else None,
                    required_params=missing_params,
                    message=(
                        "OpenCLI runtime binding requires node.params.site and node.params.command"
                    ),
                )
            )
        }

    access = _read_string(node.params.get("opencliAccess")) or (
        "write" if node.kind == "action" else "read"
    )
    mutation_mode = "write" if access == "write" else "read"
    return {
        "binding": WorkflowRuntimeBinding(
            binding_id=OPENCLI_BINDING_ID,
            runtime="iii",
            worker=OPENCLI_WORKER,
            function_id=OPENCLI_FUNCTION_ID,
            channel="opencli",
            input={
                "site": site,
                "command": command,
                **({"access": "write"} if mutation_mode == "write" else {}),
            },
        ).model_dump(),
        "resource_requirement": WorkflowRuntimeResourceRequirement(
            nodeId=node_id,
            sourceGroup=(
                _read_string(node.params.get("sourceGroup"))
                or _read_string(node.params.get("source_group"))
                or node_id
            ),
            site=site,
            mutationMode=mutation_mode,
            requestedCapability=f"opencli.{site}.{command}",
            adapterNodeId=_read_string(node.params.get("opencliAdapterNodeId")),
        ).model_dump(mode="json", exclude_none=True),
    }


def _resolve_collection_need(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    text = _read_string(node.params.get("text"))
    return {
        "binding": {
            "status": "bound",
            "binding_id": DEMAND_DRAFT_BINDING_ID,
            "runtime": "workflow",
            "channel": "demand-draft",
            "input": {
                "text": text,
                "locale": _read_string(node.params.get("locale")) or "zh-CN",
            },
        },
        "demand_draft": {
            "node_id": node_id,
            "endpoint": "/api/v1/workflows/demand-draft",
        },
    }


def _resolve_source_pool(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    return {
        "binding": {
            "status": "bound",
            "binding_id": SOURCE_POOL_BINDING_ID,
            "runtime": "workflow",
            "channel": "source-pool",
            "input": {
                "sourceCount": _read_int(node.params.get("sourceCount")),
                "sourceGroups": _read_string_list(node.params.get("sourceGroups")),
                "fanout": "parallel",
            },
        },
        "source_pool": {
            "node_id": node_id,
            "fanout": "parallel",
        },
    }


def _resolve_source_fetch_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    *,
    node_id: str,
) -> dict[str, Any]:
    config = adapter.config if adapter else {}
    collector_type = _collector_source_type(node)
    provider = (
        adapter.provider if adapter else _read_string(node.params.get("provider")) or "workflow"
    )
    channel_type = (
        _read_string(node.params.get("channelType"))
        or _read_string(node.params.get("channel_type"))
        or _read_string(config.get("channelType"))
        or _read_string(config.get("channel_type"))
        or _read_string(config.get("channel"))
        or collector_type
        or provider
    )
    live_mode = (
        _read_string(node.params.get("liveMode"))
        or _read_string(config.get("liveMode"))
        or (adapter.mode if adapter else None)
        or "live"
    )
    source_id = _read_string(node.params.get("sourceId")) or _read_string(
        node.params.get("dataSourceId")
    )
    sources = [
        _sanitize_runtime_config(source)
        for source in node.params.get("sources", [])
        if isinstance(source, dict)
    ]
    execution = _sanitize_runtime_config(_read_dict(node.params.get("execution")))
    if collector_type:
        mismatched_sources = [
            _read_string(source.get("sourceId")) or "<unknown>"
            for source in sources
            if _read_string(source.get("kind")) != collector_type
        ]
        if mismatched_sources:
            raise ValueError(
                f"collector source kind mismatch for {collector_type}: "
                + ", ".join(mismatched_sources)
            )
    return {
        "binding": {
            "status": "bound",
            "binding_id": (
                f"{COLLECTOR_BINDING_PREFIX}{collector_type}"
                if collector_type
                else SOURCE_FETCH_BINDING_ID
            ),
            "runtime": "workflow",
            "channel": "collector" if collector_type else "source",
            "input": {
                "provider": provider,
                "channelType": channel_type,
                "liveMode": live_mode,
                "sourceId": source_id,
                "adapterMode": adapter.mode if adapter else None,
                "adapterConfig": _sanitize_runtime_config(config),
                "params": _sanitize_runtime_config(dict(node.params)),
                "collectorType": collector_type,
                "sources": sources,
                "execution": execution,
                "outputPort": "items[]",
            },
        },
        "source_fetch": {
            "node_id": node_id,
            "provider": provider,
            "channelType": channel_type,
            "dispatch": (
                "collector_multi_source_fanout"
                if collector_type in COLLECTOR_SOURCE_TYPES
                else "runtime_source_binding"
            ),
        },
    }


def _resolve_collection_output(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    return {
        "binding": {
            "status": "bound",
            "binding_id": COLLECTION_OUTPUT_BINDING_ID,
            "runtime": "workflow",
            "channel": "collection-output",
            "input": {
                "queue": _read_string(node.params.get("queue")) or "opencli-hda-output",
                "archive": bool(node.params.get("archive", False)),
            },
        },
        "collection_output": {
            "node_id": node_id,
            "artifact": "items[]",
        },
    }


def _resolve_normalize_node(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    return {
        "binding": {
            "status": "bound",
            "binding_id": NORMALIZE_BINDING_ID,
            "runtime": "workflow",
            "channel": "transform",
            "input": {
                "language": _read_string(node.params.get("language")) or "zh-CN",
                "preserveSourceRefs": node.params.get("preserveSourceRefs") is not False,
                "inputPort": "items[]",
                "outputPort": "recordCandidate[]",
            },
        },
        "normalize": {
            "node_id": node_id,
            "candidate_port": "recordCandidate[]",
        },
    }


def _resolve_data_operator_node(
    node: WorkflowProjectNode,
    *,
    node_id: str,
) -> dict[str, Any]:
    operator_id = _read_string(node.params.get("operatorId"))
    pack_version_provided = "packVersion" in node.params
    requested_pack_version = _read_string(node.params.get("packVersion"))
    resolved_pack_version = requested_pack_version or _LEGACY_DATA_OPERATOR_PACK_VERSION
    spec = (
        resolve_data_operator(operator_id, resolved_pack_version)
        if operator_id and (requested_pack_version or not pack_version_provided)
        else None
    )
    known_spec = resolve_data_operator(operator_id) if operator_id else None
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    binding_id = DATA_OPERATOR_CATALOG_BINDINGS.get(catalog_id or "")
    catalog_kind = binding_id.rsplit(".", 1)[-1] if binding_id else None
    if spec is None or spec.kind != catalog_kind:
        code = (
            "missing_data_operator_id"
            if operator_id is None
            else "unsupported_data_operator_version"
            if pack_version_provided and known_spec is not None
            else "unknown_data_operator"
            if spec is None
            else "data_operator_kind_mismatch"
        )
        return {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=code,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=["operatorId"],
                    message=(
                        "Data operator node requires a registered operatorId matching "
                        f'catalog kind "{catalog_kind or "unknown"}".'
                    ),
                )
            )
        }

    flat_config = {
        key: value
        for key, value in node.params.items()
        if key not in {"operatorId", "packVersion", "config"}
    }
    nested_config = node.params.get("config")
    config: Any = (
        {**flat_config, **nested_config}
        if isinstance(nested_config, dict)
        else flat_config
        if nested_config is None
        else nested_config
    )
    return {
        "binding": {
            "status": "bound",
            "binding_id": binding_id,
            "runtime": "workflow",
            "channel": "data-operator",
            "input": {
                "operatorId": operator_id,
                "operatorKind": spec.kind,
                "packId": spec.pack_id,
                "packVersion": spec.pack_version,
                "config": config,
                "inputPort": "recordCandidate[]",
                "outputPort": "recordCandidate[]",
            },
        },
        "data_operator": {
            "node_id": node_id,
            "operator_id": operator_id,
            "operator_kind": spec.kind,
            "pack_id": spec.pack_id,
            "pack_version": spec.pack_version,
        },
    }


def _resolve_dedupe_node(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    identity_fields = node.params.get("identityFields")
    if not isinstance(identity_fields, list) or not all(
        isinstance(field, str) and field.strip() for field in identity_fields
    ):
        identity_fields = ["title", "source"]
    window_config: dict[str, Any]
    if "window" in node.params:
        # Studio owns the duration-string syntax. Preserve it verbatim so the
        # RecordHygiene interface can validate supported and invalid values.
        window_config = {"window": node.params.get("window")}
    else:
        window_hours = _read_number(node.params.get("windowHours"))
        if window_hours is None:
            window_seconds = _read_number(node.params.get("windowSeconds"))
            window_hours = window_seconds / 3600 if window_seconds is not None else 24.0
        window_config = {
            "windowHours": window_hours,
            "windowSeconds": window_hours * 3600,
        }
    return {
        "binding": {
            "status": "bound",
            "binding_id": DEDUPE_BINDING_ID,
            "runtime": "workflow",
            "channel": "transform",
            "input": {
                "key": _read_string(node.params.get("key")) or "title+source+publishedAt",
                **window_config,
                "identityFields": identity_fields,
                "eventTimeField": (
                    _read_string(node.params.get("eventTimeField")) or "publishedAt"
                ),
                "strategy": _read_string(node.params.get("strategy")) or "keep_first",
                "inputPort": "recordCandidate[]",
                "outputPort": "recordCandidate[]",
            },
        },
        "dedupe": {
            "node_id": node_id,
            "candidate_port": "recordCandidate[]",
            "evidence_ports": ["rejected[]", "metrics"],
        },
    }


def _resolve_merge_node(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    strategy = _read_string(node.params.get("strategy")) or "concat"
    lineage = node.params.get("preserveLineage")
    return {
        "binding": {
            "status": "bound",
            "binding_id": MERGE_BINDING_ID,
            "runtime": "workflow",
            "channel": "flow",
            "input": {
                "strategy": strategy,
                "preserveLineage": lineage if isinstance(lineage, bool) else True,
                "inputType": _read_string(node.params.get("inputType")) or "recordCandidate[]",
                "outputType": _read_string(node.params.get("outputType")) or "recordCandidate[]",
            },
        },
        "merge": {
            "node_id": node_id,
            "strategy": strategy,
            "lineage": "preserved",
        },
    }


def _resolve_router_route_node(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    expression = _read_string(node.params.get("expression")) or "true"
    return {
        "binding": {
            "status": "bound",
            "binding_id": ROUTER_ROUTE_BINDING_ID,
            "runtime": "workflow",
            "channel": "router",
            "input": {
                "expression": expression,
                "mode": _read_string(node.params.get("mode")) or "filter",
                "inputPort": "recordCandidate[]",
                "outputPort": "recordCandidate[]",
            },
        },
        "router": {
            "node_id": node_id,
            "expression": expression,
        },
    }


def _resolve_record_acceptance_gate(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    return {
        "binding": {
            "status": "bound",
            "binding_id": RECORD_ACCEPTANCE_BINDING_ID,
            "runtime": "workflow",
            "channel": "gate",
            "input": {
                "mode": _read_string(node.params.get("mode")) or "automatic_with_review",
                "schema": _read_string(node.params.get("schema")) or "record.v1",
                "dedupe": _read_string(node.params.get("dedupe")) or "required",
                "lineageRequired": node.params.get("lineageRequired") is not False,
                "minQuality": _read_number(node.params.get("minQuality")) or 0.0,
            },
        },
        "record_acceptance": {
            "node_id": node_id,
            "candidate_port": "recordCandidate[]",
            "record_port": "record[]",
        },
    }


def _resolve_record_sink(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    feishu_writeback = _read_dict(node.params.get("feishuWriteback"))
    binding_input: dict[str, Any] = {
        "target": _read_string(node.params.get("target")) or "records",
        "writeMode": _read_string(node.params.get("writeMode")) or "append",
        "preserveLineage": node.params.get("preserveLineage") is not False,
    }
    if feishu_writeback:
        binding_input["feishuWriteback"] = feishu_writeback
    return {
        "binding": {
            "status": "bound",
            "binding_id": RECORD_SINK_BINDING_ID,
            "runtime": "workflow",
            "channel": "records",
            "input": binding_input,
        },
        "record_sink": {
            "node_id": node_id,
            "target": "records",
        },
    }


def _resolve_feishu_bitable_sink(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    values = {
        key: _read_string(node.params.get(key)) for key in ("connectionId", "appToken", "tableId")
    }
    field_map = _read_dict(node.params.get("fieldMap"))
    missing = [key for key, value in values.items() if value is None]
    if not field_map:
        missing.append("fieldMap")
    if missing:
        return {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_RUNTIME_PARAMETER,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=missing,
                    message="Feishu Bitable delivery requires connectionId, appToken, and tableId.",
                )
            )
        }
    return {
        "binding": {
            "status": "bound",
            "binding_id": FEISHU_BITABLE_SINK_BINDING_ID,
            "runtime": "workflow",
            "channel": "feishu-bitable",
            "input": {
                **values,
                "fieldMap": field_map,
            },
        }
    }


def _resolve_inbox_store_node(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    queue = _read_string(node.params.get("queue")) or "workflow-inbox"
    return {
        "binding": {
            "status": "bound",
            "binding_id": INBOX_STORE_BINDING_ID,
            "runtime": "workflow",
            "channel": "inbox",
            "input": {
                "queue": queue,
                "writeMode": _read_string(node.params.get("writeMode")) or "append",
                "archive": bool(node.params.get("archive", False)),
                "preserveLineage": node.params.get("preserveLineage") is not False,
            },
        },
        "inbox": {
            "node_id": node_id,
            "queue": queue,
        },
    }


def _resolve_external_tool_capability(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    tool_capability = _read_dict(node.params.get("toolCapability"))
    capability_id = _read_string(tool_capability.get("id")) or _read_string(
        node.params.get("toolCapabilityId")
    )
    executor = _read_dict(tool_capability.get("executor"))
    executor_mode = _read_string(executor.get("mode"))
    if not capability_id or executor_mode not in SUPPORTED_TOOL_EXECUTOR_MODES:
        return {
            "external_tool": {
                "node_id": node_id,
                "binding_id": EXTERNAL_TOOL_BINDING_ID,
                "dispatch": "blocked_until_tool_capability_binding",
                "origin": _read_dict(node.params.get("externalWorkflow")),
            },
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_TOOL_CAPABILITY_BINDING,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=[
                        "toolCapability.id",
                        f"toolCapability.executor.mode in {sorted(SUPPORTED_TOOL_EXECUTOR_MODES)}",
                    ],
                    message=(
                        "Imported external tool node requires an OpenCLI Admin "
                        "Tool Capability binding before it can run."
                    ),
                )
            ),
        }

    tool = resolve_workflow_tool_capability(capability_id)
    if tool is None:
        return {
            "external_tool": {
                "node_id": node_id,
                "binding_id": EXTERNAL_TOOL_BINDING_ID,
                "dispatch": "blocked_unknown_tool_capability",
                "toolCapabilityId": capability_id,
                "origin": _read_dict(node.params.get("externalWorkflow")),
            },
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code="unknown_tool_capability",
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=["registered_toolCapability.id"],
                    message=(
                        f'OpenCLI Admin Tool Capability "{capability_id}" is not '
                        "registered in the workflow tool capability registry."
                    ),
                )
            ),
        }
    if tool.status != "runnable":
        readiness = _read_dict(tool.manifest.get("readiness"))
        missing_reasons = [
            value for value in readiness.get("missingReasons", []) if isinstance(value, str)
        ]
        return {
            "external_tool": {
                "node_id": node_id,
                "binding_id": EXTERNAL_TOOL_BINDING_ID,
                "dispatch": "blocked_tool_capability_not_ready",
                "toolCapabilityId": capability_id,
            },
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code="tool_capability_not_ready",
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=missing_reasons,
                    message=f'Tool Capability "{capability_id}" is not machine-certified runnable.',
                )
            ),
        }
    if executor_mode != tool.executor.mode:
        return {
            "external_tool": {
                "node_id": node_id,
                "binding_id": EXTERNAL_TOOL_BINDING_ID,
                "dispatch": "blocked_executor_mismatch",
                "toolCapabilityId": capability_id,
            },
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code="tool_capability_executor_mismatch",
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    required_params=[f"toolCapability.executor.mode={tool.executor.mode}"],
                    message=f'Tool Capability "{capability_id}" requires its registered executor.',
                )
            ),
        }

    tool_runtime = _read_dict(tool.manifest.get("runtime"))
    action_binding_id = _read_string(tool_runtime.get("actionBinding"))
    resolved_binding_id = action_binding_id or EXTERNAL_TOOL_BINDING_ID
    presentation = _read_dict(tool.manifest.get("presentation"))
    presentation_parameters = presentation.get("parameters")
    exposed_param_names = {
        _read_string(parameter.get("name"))
        for parameter in (
            presentation_parameters if isinstance(presentation_parameters, list) else []
        )
        if isinstance(parameter, dict)
    } - {"toolCapability", "toolCapabilityId", "toolParams", "externalWorkflow", ""}
    return {
        "binding": {
            "status": "bound",
            "binding_id": resolved_binding_id,
            "runtime": "workflow",
            "channel": "tool-capability",
            "input": {
                "toolCapabilityId": capability_id,
                "toolCapabilityVersionPin": (
                    tool.versionPin.model_dump() if tool.versionPin else None
                ),
                "executorMode": executor_mode,
                "toolLabel": tool.label,
                "inputPort": (tool.inputPorts[0].type if tool.inputPorts else "unknown"),
                "outputPort": (tool.outputPorts[0].type if tool.outputPorts else "unknown"),
                "transportBindingId": EXTERNAL_TOOL_BINDING_ID,
                "actionBindingId": action_binding_id,
                "fixtureOutput": executor.get("output"),
                "fixtureOutputs": executor.get("outputs"),
                "executorParams": {
                    **tool.executor.params,
                    **_read_dict(executor.get("params")),
                },
                "toolParams": {
                    **{
                        key: value
                        for key, value in node.params.items()
                        if key in exposed_param_names
                    },
                    **_read_dict(node.params.get("toolParams")),
                },
                "externalWorkflow": _read_dict(node.params.get("externalWorkflow")),
            },
        },
        "external_tool": {
            "node_id": node_id,
            "binding_id": resolved_binding_id,
            "dispatch": "opencli_admin_tool_capability",
            "toolCapabilityId": capability_id,
            "versionPin": tool.versionPin.model_dump() if tool.versionPin else None,
        },
    }


def _resolve_turbopush_publish(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    *,
    node_id: str,
) -> dict[str, Any]:
    content_type = normalize_turbopush_content_type(node.params.get("contentType"))
    if content_type is None:
        return {
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_TURBOPUSH_CONTENT_TYPE,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    adapter_id=adapter.id if adapter else None,
                    provider=adapter.provider if adapter else None,
                    required_params=["contentType"],
                    message=(
                        "TurboPush Publish requires node.params.contentType to be "
                        "article, graph_text, or video."
                    ),
                )
            )
        }

    service = resolve_turbopush_service_resource()
    publish_contract = {
        "node_id": node_id,
        "type": "turbopush",
        "binding_id": TURBOPUSH_BINDING_ID,
        "dispatch": "blocked_until_resource" if not service.configured else "guarded_publish",
        "resource": service.model_dump(exclude_none=True),
        "platforms": turbopush_platform_projection(),
        "input": turbopush_binding_input({**node.params, "contentType": content_type}),
    }
    if not service.configured:
        return {
            "turbopush": publish_contract,
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_TURBOPUSH_SERVICE,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    adapter_id=adapter.id if adapter else None,
                    provider=adapter.provider if adapter else None,
                    required_params=service.missing,
                    message=service.message,
                )
            ),
        }

    return {
        "binding": {
            "status": "bound",
            "binding_id": TURBOPUSH_BINDING_ID,
            "runtime": "workflow",
            "channel": TURBOPUSH_CHANNEL,
            "input": {
                **turbopush_binding_input({**node.params, "contentType": content_type}),
                "service": {
                    "base_url": service.base_url,
                    "auth": "runtime-secret",
                    "source": service.source,
                },
            },
        },
        "turbopush": publish_contract,
    }


def _resolve_schedule_trigger(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    enabled = node.params.get("enabled")
    return {
        "binding": {
            "status": "bound",
            "binding_id": SCHEDULE_TRIGGER_BINDING_ID,
            "runtime": "workflow",
            "channel": "schedule",
            "input": {
                "interval": _read_string(node.params.get("interval")) or "5m",
                "timezone": _read_string(node.params.get("timezone")) or "Asia/Shanghai",
                "enabled": enabled if isinstance(enabled, bool) else True,
            },
        },
        "trigger": {
            "node_id": node_id,
            "mode": "manual_schedule_tick",
        },
    }


def _resolve_webhook_trigger(node: WorkflowProjectNode, *, node_id: str) -> dict[str, Any]:
    method = (_read_string(node.params.get("method")) or "POST").upper()
    path = _read_string(node.params.get("path")) or "/hook"
    return {
        "binding": {
            "status": "bound",
            "binding_id": WEBHOOK_TRIGGER_BINDING_ID,
            "runtime": "workflow",
            "channel": "webhook-input",
            "input": {
                "method": method,
                "path": path,
            },
        },
        "trigger": {
            "node_id": node_id,
            "mode": "webhook_input",
            "envelope": "runtimeInputEnvelope",
        },
    }


def _resolve_webhook_notifier(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    *,
    node_id: str,
) -> dict[str, Any]:
    config = adapter.config if adapter else {}
    webhook_url = _read_string(config.get("url")) or _read_string(config.get("webhook_url"))
    target = (
        _read_string(node.params.get("target")) or _read_string(config.get("target")) or "webhook"
    )
    delivery_configured = bool(webhook_url)
    notifier_contract = {
        "node_id": node_id,
        "type": "webhook",
        "binding_id": WEBHOOK_NOTIFY_BINDING_ID,
        "dispatch": "blocked_until_projection",
        "input": {
            "notifier_type": "webhook",
            "template": _read_string(node.params.get("template")) or "brief",
            "target": target,
            "adapter_mode": adapter.mode if adapter else "webhook",
            "delivery_configured": delivery_configured,
            "url": webhook_url,
            "config": config,
        },
    }
    if not delivery_configured:
        return {
            "notifier": notifier_contract,
            "missing_runtime": _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_DELIVERY_PROJECTION,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    adapter_id=adapter.id if adapter else None,
                    provider=adapter.provider if adapter else None,
                    required_params=[
                        "evidencebatch_projection_api",
                        "delivery_projection",
                        "webhook_url",
                    ],
                    message=(
                        "Webhook Notify has a backend notifier contract, but live "
                        "delivery waits for EvidenceBatch projection and a "
                        "configured webhook URL."
                    ),
                )
            ),
        }

    return {
        "binding": {
            "status": "bound",
            "binding_id": WEBHOOK_NOTIFY_BINDING_ID,
            "runtime": "workflow",
            "channel": "notifier",
            "input": {
                "notifier_type": "webhook",
                "template": _read_string(node.params.get("template")) or "brief",
                "target": target,
                "adapter_mode": adapter.mode if adapter else "webhook",
                "delivery_configured": delivery_configured,
                "url": webhook_url,
                "config": config,
            },
        },
        "notifier": {
            "node_id": node_id,
            "type": "webhook",
            "dispatch": "guarded_delivery",
        },
    }


def _resolve_notify_send_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    *,
    node_id: str,
) -> dict[str, Any]:
    config = adapter.config if adapter else {}
    provider = adapter.provider if adapter else "workflow"
    notifier_type = (
        _read_string(config.get("notifierType"))
        or _read_string(config.get("notifier_type"))
        or ("webhook" if provider in {"generic-webhook", "webhook"} else provider)
    )
    target = (
        _read_string(node.params.get("target"))
        or _read_string(config.get("target"))
        or notifier_type
    )
    delivery_configured = bool(
        _read_string(config.get("url")) or _read_string(config.get("webhook_url"))
    )
    return {
        "binding": {
            "status": "bound",
            "binding_id": NOTIFY_SEND_BINDING_ID,
            "runtime": "workflow",
            "channel": "notifier",
            "input": {
                "notifier_type": notifier_type,
                "template": _read_string(node.params.get("template")) or "brief",
                "target": target,
                "adapter_mode": adapter.mode if adapter else "mock",
                "delivery_configured": delivery_configured,
                "config": config,
            },
        },
        "notifier": {
            "node_id": node_id,
            "type": notifier_type,
            "dispatch": "guarded_delivery" if delivery_configured else "blocked_until_delivery",
        },
    }


def _is_collection_need(node: WorkflowProjectNode) -> bool:
    ui = node.ui or {}
    return _read_string(ui.get("catalogId")) == "intelligence.input.collection-need" or (
        node.kind == "schedule"
        and node.capability == "trigger"
        and _read_string(node.params.get("mode")) == "demand-draft"
    )


def _is_source_pool(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.source.pool"


def _is_source_fetch_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
) -> bool:
    return (
        node.kind == "source"
        and node.capability == "fetch"
        and (adapter is not None or _collector_source_type(node) is not None)
    )


def _is_collection_output(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.output.collection-result"


def _is_normalize_node(node: WorkflowProjectNode) -> bool:
    if (node.internals and node.internals.nodes) or node.topicCollapse or node.miniNetwork:
        return False
    return _read_string(
        (node.ui or {}).get("catalogId")
    ) == "intelligence.processing.normalize" or (
        node.kind == "agent" and node.capability == "normalize"
    )


def _is_data_operator_node(node: WorkflowProjectNode) -> bool:
    return _data_operator_kind(node) is not None


def _data_operator_kind(node: WorkflowProjectNode) -> str | None:
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    binding_id = DATA_OPERATOR_CATALOG_BINDINGS.get(catalog_id or "")
    return binding_id.rsplit(".", 1)[-1] if binding_id else None


def _is_dedupe_node(node: WorkflowProjectNode) -> bool:
    if (node.internals and node.internals.nodes) or node.topicCollapse or node.miniNetwork:
        return False
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.processing.dedupe" or (
        node.kind == "agent" and node.capability == "dedupe"
    )


def _is_merge_node(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.flow.merge" or (
        node.kind == "flow" and node.capability == "merge"
    )


def _is_router_route_node(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.router.importance" or (
        node.kind == "router" and node.capability == "route"
    )


def _is_record_acceptance_gate(node: WorkflowProjectNode) -> bool:
    return _read_string(
        (node.ui or {}).get("catalogId")
    ) == "intelligence.control.record-acceptance" or (
        node.kind == "control" and node.capability == "accept"
    )


def _is_record_sink(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.sink.records" or (
        node.kind == "sink" and node.capability == "store"
    )


def _is_feishu_bitable_sink(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.sink.feishu-bitable"


def _is_inbox_store_node(node: WorkflowProjectNode) -> bool:
    return _read_string((node.ui or {}).get("catalogId")) == "intelligence.output.inbox" or (
        node.kind == "inbox" and node.capability == "store"
    )


def _is_external_tool_capability(node: WorkflowProjectNode) -> bool:
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    if catalog_id == "external.tool.capability":
        return True
    tool_capability = _read_dict(node.params.get("toolCapability"))
    tool_id = _read_string(tool_capability.get("id"))
    return tool_id is not None and catalog_id == tool_id


def _is_schedule_trigger(node: WorkflowProjectNode) -> bool:
    return node.kind == "schedule" and node.capability == "trigger"


def _is_image_generation(node: WorkflowProjectNode) -> bool:
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    return (
        node.kind == "media"
        and node.capability == "generate"
        and catalog_id in {None, "media.image-generation"}
    )


def _is_image_asset(node: WorkflowProjectNode) -> bool:
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    return (
        node.kind == "media"
        and node.capability == "fetch"
        and catalog_id in {None, "media.image-asset"}
    )


def _is_webhook_trigger(node: WorkflowProjectNode) -> bool:
    ui = node.ui or {}
    node_library_id = _read_string(ui.get("primitiveId")) or _read_string(ui.get("catalogId"))
    return node_library_id in {
        "primitive.core.webhook-trigger",
        "primitive.ops.trigger-webhook",
    }


def _is_opencli_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
) -> bool:
    if adapter is None:
        return False
    if (node.kind, node.capability) not in {
        ("source", "fetch"),
        ("action", "store"),
    }:
        return False

    config = adapter.config
    return (
        adapter.provider == "opencli"
        or _read_string(config.get("channel")) == "opencli"
        or _read_string(config.get("channel_type")) == "opencli"
    )


def _is_webhook_notifier(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
) -> bool:
    if node.kind != "notify" or node.capability != "send" or adapter is None:
        return False

    notifier_type = _read_string(adapter.config.get("notifierType"))
    return adapter.provider == "webhook" or notifier_type == "webhook"


def _is_notify_send_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
) -> bool:
    return node.kind == "notify" and node.capability == "send" and adapter is not None


def _is_turbopush_publish(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
) -> bool:
    if node.kind != "notify" or node.capability != "send" or adapter is None:
        return False

    config = adapter.config
    return (
        adapter.provider == TURBOPUSH_PROVIDER
        or _read_string(config.get("channel")) == TURBOPUSH_CHANNEL
        or _read_string(config.get("mcpServer")) == TURBOPUSH_MCP_SERVER
    )


def _resolve_dify_graphon_package(
    node: WorkflowProjectNode,
    *,
    node_id: str,
) -> dict[str, Any]:
    compat_runtime = _read_dict(node.params.get("compatRuntime"))
    inspection = _read_dict(compat_runtime.get("inspection"))
    inspection_nodes = inspection.get("nodes")
    dependencies = inspection.get("dependencies")
    blockers = inspection.get("blockers")
    blocker = (
        next((item for item in blockers if isinstance(item, dict)), None)
        if isinstance(blockers, list)
        else None
    )
    if inspection.get("loadStatus") != "ready":
        return {
            "missing_runtime": {
                "status": "missing",
                "code": (_read_string(blocker.get("code")) if isinstance(blocker, dict) else None)
                or _read_string(inspection.get("loadReason"))
                or "dify_graphon_unavailable",
                "node_id": node_id,
                "kind": node.kind,
                "capability": node.capability,
                "message": (
                    _read_string(blocker.get("message")) if isinstance(blocker, dict) else None
                )
                or "Managed Dify package has not passed Graphon inspection.",
            }
        }

    return {
        "binding": {
            "status": "bound",
            "binding_id": DIFY_GRAPHON_BINDING_ID,
            "runtime": "graphon",
            "worker": "dify-graphon",
            "function_id": "dify.graphon.run",
            "channel": "dify-graphon",
            "input": {
                "contractVersion": DIFY_GRAPHON_CONTRACT_VERSION,
                "sourceFormat": DIFY_GRAPHON_SOURCE_FORMAT,
                "sourceSha256": _read_string(compat_runtime.get("sourceSha256")),
                "engineVersion": DIFY_GRAPHON_VERSION,
                "engineCommit": DIFY_GRAPHON_COMMIT,
                "appMode": _read_string(node.params.get("appMode")),
                "policy": _read_dict(compat_runtime.get("executionPolicy")),
                "dependencies": (dependencies if isinstance(dependencies, list) else []),
                "sourceNodeIndex": [
                    source_node_id
                    for item in (inspection_nodes if isinstance(inspection_nodes, list) else [])
                    if isinstance(item, dict)
                    and (source_node_id := _read_string(item.get("sourceNodeId")))
                ],
            },
        },
        "resource_requirement": WorkflowRuntimeResourceRequirement(
            nodeId=node_id,
            sourceGroup=node_id,
            site="graphon",
            mutationMode="read",
            requestedCapability=DIFY_GRAPHON_BINDING_ID,
        ).model_dump(mode="json", exclude_none=True),
    }


def _is_dify_graphon_managed_package(node: WorkflowProjectNode) -> bool:
    compat_runtime = _read_dict(node.params.get("compatRuntime"))
    return (
        node.params.get("packageFormat") == "dify"
        and node.params.get("packageExecution") == "managed"
        and compat_runtime.get("engine") == "graphon"
    )


def _read_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _read_number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _read_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _read_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _collector_source_type(node: WorkflowProjectNode) -> str | None:
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    if catalog_id and catalog_id.startswith(COLLECTOR_BINDING_PREFIX):
        catalog_kind = catalog_id.rsplit(".", 1)[-1]
        if catalog_kind in COLLECTOR_SOURCE_TYPES:
            return catalog_kind
    configured_kind = (
        _read_string(node.params.get("collectorType"))
        or _read_string(node.params.get("sourceType"))
    )
    return configured_kind if configured_kind in COLLECTOR_SOURCE_TYPES else None


def _sanitize_runtime_config(value: Any) -> Any:
    """Keep references while removing secret material from runtime metadata."""
    if isinstance(value, list):
        return [_sanitize_runtime_config(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        normalized = "".join(character for character in str(key).lower() if character.isalnum())
        if normalized in _SENSITIVE_CONFIG_KEYS:
            continue
        sanitized[str(key)] = _sanitize_runtime_config(item)
    return sanitized


def _dump_missing_runtime(missing_runtime: WorkflowMissingRuntime) -> dict[str, Any]:
    payload = missing_runtime.model_dump(exclude_none=True)
    if not payload.get("required_params"):
        payload.pop("required_params", None)
    return payload


def _attach_runtime_contract(
    metadata: dict[str, Any],
    *,
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    node_id: str,
) -> dict[str, Any]:
    result = dict(metadata)
    binding = _read_dict(result.get("binding"))
    if binding:
        binding_id = _read_string(binding.get("binding_id"))
        contract = runtime_io_contract_manifest(binding_id)
        if contract is None:
            result.pop("binding", None)
            result["missing_runtime"] = _dump_missing_runtime(
                WorkflowMissingRuntime(
                    code=MISSING_RUNTIME_IO_CONTRACT,
                    node_id=node_id,
                    kind=node.kind,
                    capability=node.capability,
                    adapter_id=adapter.id if adapter else None,
                    provider=adapter.provider if adapter else None,
                    required_params=["runtime_io_contract"],
                    message=(
                        f'Runtime binding "{binding_id or "unknown"}" exists but '
                        "does not declare a real node I/O contract."
                    ),
                )
            )
        else:
            result["binding"] = {**binding, "contract": contract}

    for key, value in list(result.items()):
        if key in {"binding", "missing_runtime"} or not isinstance(value, dict):
            continue
        binding_id = _read_string(value.get("binding_id"))
        contract = runtime_io_contract_manifest(binding_id)
        if contract is not None:
            result[key] = {**value, "contract": contract}

    return result
