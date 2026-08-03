"""Build III dispatch envelopes for Multi Source OpenCLI HDA nodes."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import signature
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.database import (
    commit_session,
    queue_after_commit,
    rollback_session_preserving_primary,
)
from backend.models.record import CollectedRecord
from backend.models.source import DataSource
from backend.models.task import CollectionTask
from backend.models.workflow_run import WorkflowRun as WorkflowRunRow
from backend.models.workflow_run import WorkflowRunEvent as WorkflowRunEventRow
from backend.pipeline.normalizer import normalize_item
from backend.pipeline.storer import store_records
from backend.schemas.workflow import (
    CompiledWorkflowNode,
    WorkflowCompileError,
    WorkflowFleetCapabilityMatchRequest,
    WorkflowFleetCapabilityMatchResponse,
    WorkflowNodeRunEvent,
    WorkflowNodeRunEventType,
    WorkflowOpenCLIHDATraceDispatch,
    WorkflowOpenCLIHDATraceResponse,
    WorkflowProject,
    WorkflowRunBatchReference,
    WorkflowRunBlockReason,
    WorkflowRunCheckpoint,
    WorkflowRunNodeState,
    WorkflowRunProjection,
    WorkflowRunSourceOutputsRequest,
    WorkflowRunStartRequest,
    WorkflowRunStatus,
)
from backend.workflow.async_orchestrator import image_generation_execution_key
from backend.workflow.bbx_tool_nodes import (
    BBX_EXECUTOR_MODE,
    BBX_TOOL_CAPABILITY_ID,
    BbxToolExecutionError,
    bbx_result_items,
    invoke_bbx_tool,
)
from backend.workflow.block_reasons import (
    FETCH_PERMISSION_REQUIRED,
    MISSING_DELIVERY_PROJECTION,
    MISSING_SOURCE_CREDENTIAL,
    OPENCLI_WRITE_APPROVAL_REQUIRED,
    OPENCLI_WRITE_PERMISSION_REQUIRED,
    SEND_PERMISSION_REQUIRED,
    SOURCE_OUTPUT_REQUIRED,
)
from backend.workflow.compiler import INTERNAL_ID_SEPARATOR, compile_workflow_project
from backend.workflow.data_operators import execute_data_operator
from backend.workflow.dify_compile import compile_managed_dify_workflow_project
from backend.workflow.dify_event_adapter import execute_dify_graphon_run
from backend.workflow.dify_grants import resolve_dify_ephemeral_grants
from backend.workflow.dify_graphon_client import DifyGraphonClient
from backend.workflow.event_mirror import publish_workflow_run_event_mirror
from backend.workflow.fleet_inventory import match_workflow_fleet_capability
from backend.workflow.http_source_executor import (
    WorkflowHTTPSourceExecutionError,
    execute_workflow_http_source,
)
from backend.workflow.intelligence_store import (
    IntelligenceStoreError,
    run_intelligence_transaction,
)
from backend.workflow.joyai_vl_executor import (
    JOYAI_VL_INTERACTION_EXECUTOR,
    JOYAI_VL_TOOL_CAPABILITY_ID,
    JoyAIVLExecutionError,
    execute_joyai_vl_interaction,
)
from backend.workflow.kats_runtime import (
    KATS_EXECUTOR_MODE,
    KATS_TOOL_IDS,
    KatsRuntimeError,
    execute_kats_operation,
)
from backend.workflow.last30days_provider import Last30DaysProviderError
from backend.workflow.native_intelligence_executor import (
    NATIVE_INTELLIGENCE_ACTION_BY_TOOL_ID,
    NATIVE_INTELLIGENCE_EXECUTOR,
    execute_native_intelligence_action,
)
from backend.workflow.native_node_runtime import (
    NATIVE_BINDING_IDS,
    NativeNodeValidationError,
    execute_native_node,
)
from backend.workflow.opentabs_tool_nodes import (
    OPENTABS_EXECUTOR_MODE,
    OPENTABS_TOOL_CAPABILITY_ID,
    OpenTabsToolExecutionError,
    invoke_opentabs_tool,
    opentabs_result_items,
)
from backend.workflow.realtime_market_executor import (
    OKX_MARKET_TICKER_SNAPSHOT_EXECUTOR,
    RealtimeMarketExecutionError,
    execute_okx_market_ticker_snapshot,
)
from backend.workflow.record_hygiene import (
    HygieneConfigError,
    HygieneInvariantError,
    execute_record_hygiene,
)
from backend.workflow.rss_source_executor import (
    WorkflowRSSSourceExecutionError,
    execute_workflow_rss_source,
)
from backend.workflow.runtime_registry import (
    COLLECTION_OUTPUT_BINDING_ID,
    DATA_OPERATOR_CATALOG_BINDINGS,
    DEDUPE_BINDING_ID,
    DIFY_GRAPHON_BINDING_ID,
    EXTERNAL_TOOL_BINDING_ID,
    IMAGE_ASSET_BINDING_ID,
    IMAGE_GENERATION_BINDING_ID,
    INBOX_STORE_BINDING_ID,
    MERGE_BINDING_ID,
    NORMALIZE_BINDING_ID,
    NOTIFY_SEND_BINDING_ID,
    OPENCLI_BINDING_ID,
    OPENCLI_FUNCTION_ID,
    OPENCLI_WORKER,
    RECORD_ACCEPTANCE_BINDING_ID,
    RECORD_SINK_BINDING_ID,
    ROUTER_ROUTE_BINDING_ID,
    SCHEDULE_TRIGGER_BINDING_ID,
    SOURCE_FETCH_BINDING_ID,
    WEBHOOK_NOTIFY_BINDING_ID,
    WEBHOOK_TRIGGER_BINDING_ID,
)
from backend.workflow.runtime_resources import resolve_runtime_resources
from backend.workflow.situation_awareness import (
    SITUATION_AWARENESS_EXECUTOR,
    SITUATION_AWARENESS_TOOL_CAPABILITY_ID,
    execute_situation_awareness,
)
from backend.workflow.swarm_simulation import (
    SWARM_SIMULATION_EXECUTOR,
    SWARM_SIMULATION_TOOL_CAPABILITY_ID,
    SwarmSimulationExecutionError,
    execute_swarm_simulation,
)
from backend.workflow.turbopush_executor import (
    TurboPushPublishError,
    execute_turbopush_publish,
)
from backend.workflow.turbopush_runtime import TURBOPUSH_BINDING_ID
from backend.workflow.webhook_delivery import (
    WorkflowWebhookDeliveryError,
    execute_workflow_webhook_delivery,
)
from backend.workflow.workflow_run_events import append_workflow_run_events


@dataclass
class _StoredWorkflowRun:
    request: WorkflowRunStartRequest
    projection: WorkflowRunProjection
    events: list[WorkflowNodeRunEvent]
    workflow_version_id: str | None = None
    studio_workflow_version_id: str | None = None


_RUNS: dict[str, _StoredWorkflowRun] = {}
_DATA_OPERATOR_BINDING_IDS = set(DATA_OPERATOR_CATALOG_BINDINGS.values())
_LEGACY_DATA_OPERATOR_PACK_VERSION = "1.0.0"

# Per-run_id locks so concurrent requests against the same workflow run
# serialize their read-modify-write of stored run state/event rows instead
# of racing (see continue_workflow_run_with_source_outputs). The registry
# itself is guarded by _RUN_LOCKS_GUARD to avoid a create-or-get race on the
# dict. Note: this registry is never pruned, so long-lived processes that
# see many distinct run_ids will accumulate Lock objects (minor memory
# growth, not a correctness issue).
_RUN_LOCKS: dict[str, asyncio.Lock] = {}
_RUN_LOCKS_GUARD = asyncio.Lock()


async def _get_run_lock(run_id: str) -> asyncio.Lock:
    async with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            _RUN_LOCKS[run_id] = lock
        return lock


def build_opencli_hda_trace(
    project: WorkflowProject,
    *,
    package_node_id: str | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
) -> WorkflowOpenCLIHDATraceResponse:
    """Compile a WorkflowProject and return OpenCLI HDA fanout trigger envelopes."""

    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_trace_id = trace_id or str(uuid.uuid4())
    compile_result = compile_workflow_project(project)
    if not compile_result.valid or compile_result.plan is None:
        return WorkflowOpenCLIHDATraceResponse(
            valid=False,
            errors=compile_result.errors,
            workflowId=project.id,
            runId=resolved_run_id,
            traceId=resolved_trace_id,
            packageNodeId=package_node_id,
            dispatch=_dispatch_metadata(),
            dispatches=[],
        )

    runtime_nodes = compile_result.plan.runtime.nodes
    selected_package_id = _select_package_id(runtime_nodes, package_node_id)
    if selected_package_id is None:
        return WorkflowOpenCLIHDATraceResponse(
            valid=False,
            errors=[
                WorkflowCompileError(
                    code="missing_opencli_hda_package",
                    message="No Multi Source OpenCLI HDA package node is available to trace",
                    node_id=package_node_id,
                    path=["nodes", package_node_id] if package_node_id else ["nodes"],
                )
            ],
            workflowId=project.id,
            runId=resolved_run_id,
            traceId=resolved_trace_id,
            packageNodeId=package_node_id,
            dispatch=_dispatch_metadata(),
            dispatches=[],
        )

    dispatches = [
        _to_dispatch(
            project,
            node,
            package_node_id=selected_package_id,
            run_id=resolved_run_id,
            trace_id=resolved_trace_id,
        )
        for node in runtime_nodes
        if _is_opencli_internal_source(node, selected_package_id)
    ]
    if not dispatches:
        return WorkflowOpenCLIHDATraceResponse(
            valid=False,
            errors=[
                WorkflowCompileError(
                    code="missing_opencli_hda_sources",
                    message=(
                        f'Multi Source OpenCLI HDA "{selected_package_id}" has no '
                        "compiled OpenCLI source bindings"
                    ),
                    node_id=selected_package_id,
                    path=["nodes", selected_package_id, "internals"],
                )
            ],
            workflowId=project.id,
            runId=resolved_run_id,
            traceId=resolved_trace_id,
            packageNodeId=selected_package_id,
            dispatch=_dispatch_metadata(),
            dispatches=[],
        )

    return WorkflowOpenCLIHDATraceResponse(
        valid=True,
        errors=[],
        workflowId=project.id,
        runId=resolved_run_id,
        traceId=resolved_trace_id,
        packageNodeId=selected_package_id,
        dispatch=_dispatch_metadata(),
        dispatches=dispatches,
    )


async def start_workflow_run(
    body: WorkflowRunStartRequest,
    *,
    session: AsyncSession | None = None,
    existing_events: list[WorkflowNodeRunEvent] | None = None,
    workflow_version_id: str | None = None,
    studio_workflow_version_id: str | None = None,
    graphon_client: DifyGraphonClient | None = None,
) -> WorkflowRunProjection:
    """Create a replayable workflow run projection from a compiled WorkflowProject."""

    run_id = body.runId or str(uuid.uuid4())
    trace_id = body.traceId or str(uuid.uuid4())
    started_at = _utcnow()
    prior_events = list(existing_events or [])
    compile_result = (
        await compile_managed_dify_workflow_project(
            body.project,
            graphon_client=graphon_client,
            session=session,
        )
        if graphon_client is not None
        else compile_workflow_project(body.project)
    )

    if not compile_result.valid or compile_result.plan is None:
        events = _compile_failure_events(
            workflow_id=body.project.id,
            run_id=run_id,
            trace_id=trace_id,
            errors=compile_result.errors,
        )
        projection = _build_projection(
            workflow_id=body.project.id,
            run_id=run_id,
            trace_id=trace_id,
            package_node_id=body.packageNodeId,
            started_at=started_at,
            valid=False,
            errors=compile_result.errors,
            runtime_nodes=[],
            events=events,
        )
        stored_events = [*prior_events, *events]
        projection = _build_projection(
            workflow_id=body.project.id,
            run_id=run_id,
            trace_id=trace_id,
            package_node_id=body.packageNodeId,
            started_at=started_at,
            valid=False,
            errors=compile_result.errors,
            runtime_nodes=[],
            events=stored_events,
        )
        await _store_workflow_run(
            run_id,
            request=body,
            projection=projection,
            events=stored_events,
            session=session,
            workflow_version_id=workflow_version_id,
            studio_workflow_version_id=studio_workflow_version_id,
        )
        return projection

    emitter = _WorkflowRunEventEmitter(
        workflow_id=body.project.id,
        run_id=run_id,
        trace_id=trace_id,
        source_id=(
            body.input.sourceId or body.input.source if body.trigger.kind == "webhook" else None
        ),
        initial_sequence=len(prior_events),
    )
    runtime_nodes, trigger_selection_error = _select_runtime_nodes_for_trigger(
        compile_result.plan.runtime.nodes,
        trigger_kind=body.trigger.kind,
        trigger_node_id=body.trigger.triggerNodeId,
    )
    if trigger_selection_error is not None:
        errors = [trigger_selection_error]
        events = _compile_failure_events(
            workflow_id=body.project.id,
            run_id=run_id,
            trace_id=trace_id,
            errors=errors,
        )
        stored_events = [*prior_events, *events]
        projection = _build_projection(
            workflow_id=body.project.id,
            run_id=run_id,
            trace_id=trace_id,
            package_node_id=body.packageNodeId,
            started_at=started_at,
            valid=False,
            errors=errors,
            runtime_nodes=[],
            events=stored_events,
        )
        await _store_workflow_run(
            run_id,
            request=body,
            projection=projection,
            events=stored_events,
            session=session,
            workflow_version_id=workflow_version_id,
            studio_workflow_version_id=studio_workflow_version_id,
        )
        return projection

    runtime_nodes_by_id = {node.id: node for node in runtime_nodes}
    if session is not None:
        queued_projection = _build_projection(
            workflow_id=body.project.id,
            run_id=run_id,
            trace_id=trace_id,
            package_node_id=body.packageNodeId,
            started_at=started_at,
            valid=True,
            errors=[],
            runtime_nodes=runtime_nodes,
            events=prior_events,
        )
        await _store_workflow_run(
            run_id,
            request=body,
            projection=queued_projection,
            events=prior_events,
            session=session,
            workflow_version_id=workflow_version_id,
            studio_workflow_version_id=studio_workflow_version_id,
        )
    should_trace_opencli = any(
        _binding_id(node) == OPENCLI_BINDING_ID for node in runtime_nodes
    ) and (body.packageNodeId is not None or _select_package_id(runtime_nodes, None) is not None)
    trace = (
        build_opencli_hda_trace(
            body.project,
            package_node_id=body.packageNodeId,
            run_id=run_id,
            trace_id=trace_id,
        )
        if should_trace_opencli
        else None
    )
    package_nodes = [node for node in runtime_nodes if node.package is not None]
    package_ids = {node.id for node in package_nodes}
    dispatches_by_node = {
        dispatch.nodeId: dispatch for dispatch in (trace.dispatches if trace else [])
    }
    for node in runtime_nodes:
        if (
            _binding_id(node) != OPENCLI_BINDING_ID
            or node.id in dispatches_by_node
            or not _read_string(node.params.get("opencliAdapterNodeId"))
        ):
            continue
        dispatches_by_node[node.id] = _to_dispatch(
            body.project,
            node,
            package_node_id=None,
            run_id=run_id,
            trace_id=trace_id,
        )
    blocked_by_package: dict[str, list[WorkflowRunBlockReason]] = {}
    managed_package_terminal_ids: set[str] = set()
    outputs_by_node: dict[str, list[dict[str, Any]]] = {}
    materialized_source_tasks: dict[str, tuple[str, str]] = {}
    waiting_nodes: set[str] = set()

    for node in runtime_nodes:
        emitter.emit(node, "queued", message="Node queued for workflow run")

    for node in runtime_nodes:
        if node.id in package_ids:
            emitter.emit(node, "started", message="Package node started")
            if _binding_id(node) == DIFY_GRAPHON_BINDING_ID:
                package = next(
                    (
                        project_node
                        for project_node in body.project.nodes
                        if project_node.id == node.id
                    ),
                    None,
                )
                compat_runtime = _read_dict(package.params.get("compatRuntime")) if package else {}
                source_content = compat_runtime.get("sourceContent")
                source_sha256 = _read_string(compat_runtime.get("sourceSha256"))
                if (
                    package is None
                    or graphon_client is None
                    or not isinstance(source_content, str)
                    or not source_content
                    or source_sha256 is None
                ):
                    reason = WorkflowRunBlockReason(
                        code="dify_graphon_unavailable",
                        message="The managed Dify package has no available Graphon runtime.",
                        source="dify_graphon_runtime",
                        details={"nodeId": node.id},
                    )
                    emitter.emit(
                        node,
                        "failed",
                        message=reason.message,
                        block_reason=reason,
                    )
                    managed_package_terminal_ids.add(node.id)
                    continue

                grants = await resolve_dify_ephemeral_grants(package, session=session)
                run_result = await execute_dify_graphon_run(
                    graphon_client=graphon_client,
                    source_content=source_content,
                    source_sha256=source_sha256,
                    policy={
                        "allowNetwork": body.project.agentPermissions.canFetchNetwork,
                        "allowedDomains": body.project.agentPermissions.allowedDomains,
                        "allowCode": False,
                        "allowTools": False,
                    },
                    inputs=body.input.payload,
                    grants=grants,
                )
                queued_internal_ids: set[str] = set()
                for runtime_event in run_result.events:
                    if runtime_event.source_node_id is None:
                        continue
                    if runtime_event.source_node_id not in queued_internal_ids:
                        queued_internal_ids.add(runtime_event.source_node_id)
                        emitter.emit_nested(
                            node,
                            runtime_event.source_node_id,
                            "queued",
                            message=f'Dify node "{runtime_event.source_node_id}" queued',
                            details={
                                "runtime": "graphon",
                                "runtimeRunId": runtime_event.runtime_run_id,
                                "runtimeSequence": runtime_event.runtime_sequence,
                                "synthetic": True,
                            },
                        )
                    block_reason = None
                    if runtime_event.event_type in {"blocked", "failed"}:
                        block_reason = WorkflowRunBlockReason(
                            code=(
                                "dify_runtime_failed"
                                if runtime_event.event_type == "failed"
                                else "dify_runtime_blocked"
                            ),
                            message=runtime_event.message,
                            source="dify_graphon_runtime",
                            details={
                                "runtimeRunId": runtime_event.runtime_run_id,
                                "runtimeSequence": runtime_event.runtime_sequence,
                            },
                        )
                    emitter.emit_nested(
                        node,
                        runtime_event.source_node_id,
                        runtime_event.event_type,
                        message=runtime_event.message,
                        block_reason=block_reason,
                        details=runtime_event.details,
                    )

                terminal_details = {
                    "runtime": "graphon",
                    "runtimeRunId": run_result.runtime_run_id,
                    "outputPreview": run_result.terminal_details,
                }
                if run_result.status == "completed":
                    emitter.emit(
                        node,
                        "completed",
                        message="Managed Dify package completed through Graphon",
                        details=terminal_details,
                    )
                else:
                    reason = WorkflowRunBlockReason(
                        code=run_result.code or "dify_runtime_failed",
                        message=run_result.message or "The managed Dify package did not complete.",
                        source="dify_graphon_runtime",
                        details={
                            "runtimeRunId": run_result.runtime_run_id,
                            **run_result.terminal_details,
                        },
                    )
                    emitter.emit(
                        node,
                        "blocked" if run_result.status == "blocked" else "failed",
                        message=reason.message,
                        block_reason=reason,
                        details=terminal_details,
                    )
                managed_package_terminal_ids.add(node.id)
            continue

        if any(dependency in waiting_nodes for dependency in node.depends_on):
            # The queued event is the durable declaration that this node has
            # not executed yet. It will be reconsidered after the async node
            # resumes with platform-owned output assets.
            waiting_nodes.add(node.id)
            continue

        missing_runtime = _read_dict(node.runtime.get("missing_runtime"))
        if missing_runtime:
            reason = WorkflowRunBlockReason(
                code=_read_string(missing_runtime.get("code")) or "missing_runtime",
                message=_read_string(missing_runtime.get("message"))
                or "Node has no executable runtime binding",
                source="runtime_registry",
                details=missing_runtime,
            )
            emitter.emit(
                node,
                "blocked",
                message=reason.message,
                block_reason=reason,
            )
            for ancestor_id in _package_ancestor_ids(node):
                blocked_by_package.setdefault(ancestor_id, []).append(reason)
            continue

        if _binding_id(node) == WEBHOOK_TRIGGER_BINDING_ID:
            if body.trigger.kind != "webhook" or body.trigger.triggerNodeId != node.id:
                reason = WorkflowRunBlockReason(
                    code="workflow_webhook_input_required",
                    message="Webhook trigger requires workflow webhook ingress input.",
                    source="workflow_webhook_ingress",
                    details={
                        "nodeId": node.id,
                        "bindingId": WEBHOOK_TRIGGER_BINDING_ID,
                    },
                )
                emitter.emit(node, "blocked", message=reason.message, block_reason=reason)
                continue

            envelope = _webhook_runtime_input_envelope(body, node)
            outputs_by_node[node.id] = [envelope]
            emitter.emit(node, "started", message="Workflow webhook input accepted")
            emitter.emit(
                node,
                "partial",
                message="Webhook request projected as runtime input",
                details={
                    "bindingId": WEBHOOK_TRIGGER_BINDING_ID,
                    "outputPort": "request",
                    "workflowId": body.project.id,
                    "runId": run_id,
                    "nodeId": node.id,
                    "sourceId": body.input.sourceId or body.input.source,
                    "requestId": body.trigger.requestId,
                    "runtimeInputEnvelope": envelope,
                },
            )
            emitter.emit(node, "completed", message="Workflow webhook input completed")
            continue

        resumed_assets = _read_dict_list(body.sourceOutputs.get(node.id))
        if _binding_id(node) == IMAGE_GENERATION_BINDING_ID and resumed_assets:
            outputs_by_node[node.id] = resumed_assets
            waiting_details = _latest_waiting_details(prior_events, node.id)
            emitter.emit(node, "started", message="Ingested image assets loaded")
            emitter.emit(
                node,
                "partial",
                message="Image generation result committed to OpenCLI assets",
                details={
                    "bindingId": IMAGE_GENERATION_BINDING_ID,
                    "outputPort": "mediaAsset[]",
                    "assetCount": len(resumed_assets),
                    "assets": resumed_assets,
                    "generation": {
                        "jobId": waiting_details.get("jobId"),
                        "attempt": waiting_details.get("attempt", 1),
                        "status": "succeeded",
                    },
                },
            )
            emitter.emit(node, "completed", message="Image generation node completed")
            continue

        request_items = _request_source_items(node, body.sourceOutputs)
        if request_items:
            outputs_by_node[node.id] = request_items
            emitter.emit(node, "started", message="Runtime source output started")
            emitter.emit(
                node,
                "partial",
                message="Runtime source output loaded as workflow items",
                batch=_node_batch_reference(
                    body.project.id,
                    run_id,
                    node,
                    item_count=len(request_items),
                ),
                details={
                    "itemCount": len(request_items),
                    "outputPort": "items[]",
                    "lineage": _lineage_pointer(node),
                },
            )
            emitter.emit(node, "completed", message="Runtime source output completed")
            continue

        fixture_items = _fixture_source_items(node)
        if fixture_items:
            outputs_by_node[node.id] = fixture_items
            emitter.emit(node, "started", message="Fixture source items started")
            emitter.emit(
                node,
                "partial",
                message="Fixture source items ready",
                batch=_node_batch_reference(
                    body.project.id,
                    run_id,
                    node,
                    item_count=len(fixture_items),
                ),
                details={
                    "itemCount": len(fixture_items),
                    "outputPort": "items[]",
                    "lineage": _lineage_pointer(node),
                },
            )
            emitter.emit(node, "completed", message="Fixture source items completed")
            continue

        persisted_items = await _bound_source_record_items(node, session=session)
        if persisted_items:
            outputs_by_node[node.id] = persisted_items
            emitter.emit(node, "started", message="Bound source records started")
            emitter.emit(
                node,
                "partial",
                message="Bound source records loaded as workflow items",
                batch=_node_batch_reference(
                    body.project.id,
                    run_id,
                    node,
                    item_count=len(persisted_items),
                    record_count=len(persisted_items),
                    adapter_task_id=_bound_task_id(node),
                ),
                details={
                    "itemCount": len(persisted_items),
                    "outputPort": "items[]",
                    "taskId": _bound_task_id(node),
                    "sourceId": _bound_source_id_from_items(persisted_items),
                    "lineage": _lineage_pointer(node),
                },
            )
            emitter.emit(node, "completed", message="Bound source records completed")
            continue

        if _binding_id(node) == IMAGE_GENERATION_BINDING_ID:
            binding = _read_dict(node.runtime.get("binding"))
            binding_input = _read_dict(binding.get("input"))
            snapshot_id = _read_string(binding_input.get("canvasSnapshotId"))
            execution_key = image_generation_execution_key(run_id, node.id, 1)
            emitter.emit(node, "started", message="Image generation job prepared")
            emitter.emit(
                node,
                "waiting",
                message="Image generation is waiting for asset ingestion",
                details={
                    "bindingId": IMAGE_GENERATION_BINDING_ID,
                    "canvasSnapshotId": snapshot_id,
                    **execution_key.as_details(),
                },
            )
            waiting_nodes.add(node.id)
            continue

        if _binding_id(node) == IMAGE_ASSET_BINDING_ID:
            binding = _read_dict(node.runtime.get("binding"))
            binding_input = _read_dict(binding.get("input"))
            asset_ids = [
                value
                for value in binding_input.get("assetIds", [])
                if isinstance(value, str) and value
            ]
            outputs_by_node[node.id] = [
                {"id": asset_id, "type": "mediaAsset"} for asset_id in asset_ids
            ]
            emitter.emit(node, "started", message="Fixed image assets loaded")
            emitter.emit(
                node,
                "partial",
                message="OpenCLI media assets projected",
                details={
                    "bindingId": IMAGE_ASSET_BINDING_ID,
                    "outputPort": "mediaAsset[]",
                    "assetIds": asset_ids,
                    "assetCount": len(asset_ids),
                },
            )
            emitter.emit(node, "completed", message="Image asset node completed")
            continue

        if _is_workflow_source_fetch_node(node):
            emitter.emit(node, "started", message="Workflow source fetch binding started")
            binding = _read_dict(node.runtime.get("binding"))
            binding_input = _read_dict(binding.get("input"))
            try:
                rss_result = await execute_workflow_rss_source(
                    binding_input,
                    allowed_domains=body.project.agentPermissions.allowedDomains,
                    max_items=body.project.settings.maxItemsPerRun,
                    session=session,
                )
            except WorkflowRSSSourceExecutionError as exc:
                reason = WorkflowRunBlockReason(
                    code=exc.code,
                    message=exc.message,
                    source="workflow_rss_source",
                    details={"nodeId": node.id, **exc.details},
                )
                emitter.emit(
                    node,
                    "blocked" if exc.status == "blocked" else "failed",
                    message=reason.message,
                    block_reason=reason,
                )
                continue

            if rss_result is not None:
                live_items = _live_source_items(
                    node,
                    rss_result.items,
                    artifact="live_rss_source",
                )
                outputs_by_node[node.id] = live_items
                emitter.emit(
                    node,
                    "partial",
                    message="Live RSS source loaded as workflow items",
                    batch=_node_batch_reference(
                        body.project.id,
                        run_id,
                        node,
                        item_count=len(live_items),
                    ),
                    details={
                        "bindingId": SOURCE_FETCH_BINDING_ID,
                        "itemCount": len(live_items),
                        "outputPort": "items[]",
                        "channelType": "rss",
                        "url": rss_result.url,
                        "feedTitle": rss_result.feed_title,
                        "totalEntries": rss_result.total_entries,
                        "lineage": _lineage_pointer(node),
                    },
                )
                emitter.emit(node, "completed", message="Live RSS source completed")
                continue

            try:
                live_result = await execute_workflow_http_source(
                    binding_input,
                    allowed_domains=body.project.agentPermissions.allowedDomains,
                    max_items=body.project.settings.maxItemsPerRun,
                )
            except WorkflowHTTPSourceExecutionError as exc:
                reason = WorkflowRunBlockReason(
                    code=exc.code,
                    message=exc.message,
                    source="workflow_http_source",
                    details={"nodeId": node.id, **exc.details},
                )
                emitter.emit(
                    node,
                    "blocked" if exc.status == "blocked" else "failed",
                    message=reason.message,
                    block_reason=reason,
                )
                continue

            if live_result is not None:
                live_items = _live_source_items(node, live_result.items)
                outputs_by_node[node.id] = live_items
                emitter.emit(
                    node,
                    "partial",
                    message="Live HTTP source loaded as workflow items",
                    batch=_node_batch_reference(
                        body.project.id,
                        run_id,
                        node,
                        item_count=len(live_items),
                    ),
                    details={
                        "bindingId": SOURCE_FETCH_BINDING_ID,
                        "itemCount": len(live_items),
                        "outputPort": "items[]",
                        "method": live_result.method,
                        "statusCode": live_result.status_code,
                        "url": live_result.url,
                        "resultPath": live_result.result_path,
                        "lineage": _lineage_pointer(node),
                    },
                )
                emitter.emit(node, "completed", message="Live HTTP source completed")
                continue

            reason = _source_fetch_block_reason(node, body.project.agentPermissions)
            emitter.emit(
                node,
                "blocked",
                message=reason.message,
                block_reason=reason,
            )
            for ancestor_id in _package_ancestor_ids(node):
                blocked_by_package.setdefault(ancestor_id, []).append(reason)
            continue

        if _is_turbopush_publish_node(node):
            if not body.project.agentPermissions.canSendNotifications:
                reason = WorkflowRunBlockReason(
                    code="send_permission_required",
                    message=(
                        "TurboPush Publish is bound, but workflow "
                        "agentPermissions.canSendNotifications is false."
                    ),
                    source="workflow_permissions",
                    details={
                        "nodeId": node.id,
                        "requiredPermission": "canSendNotifications",
                    },
                )
                emitter.emit(
                    node,
                    "blocked",
                    message=reason.message,
                    block_reason=reason,
                )
                continue

            binding = _read_dict(node.runtime.get("binding"))
            binding_input = _read_dict(binding.get("input"))
            emitter.emit(node, "started", message="TurboPush publish binding started")
            try:
                # execute_turbopush_publish uses a synchronous httpx.Client
                # with a 600s timeout; run it off the event loop thread so a
                # slow/hung TurboPush endpoint can't freeze the single-worker
                # server for the whole timeout.
                result = await asyncio.to_thread(execute_turbopush_publish, binding_input)
            except TurboPushPublishError as exc:
                reason = WorkflowRunBlockReason(
                    code=exc.code,
                    message=exc.message,
                    source="turbopush_runtime",
                    details=exc.details,
                )
                emitter.emit(
                    node,
                    "blocked" if exc.status == "blocked" else "failed",
                    message=exc.message,
                    block_reason=reason,
                )
                continue

            emitter.emit(
                node,
                "partial",
                message="TurboPush publish SSE result received",
                details={
                    "bindingId": TURBOPUSH_BINDING_ID,
                    **result,
                },
            )
            emitter.emit(node, "completed", message="TurboPush publish completed")
            continue

        if _is_workflow_notify_node(node) or _is_webhook_notify_node(node):
            reason = _notify_send_block_reason(
                node,
                body.project.agentPermissions,
                outputs_by_node=outputs_by_node,
            )
            if reason is not None:
                emitter.emit(node, "started", message="Workflow notification binding started")
                emitter.emit(
                    node,
                    "blocked",
                    message=reason.message,
                    block_reason=reason,
                )
                continue

        if _is_capability_native_node(node):
            emitter.emit(node, "started", message="Native workflow node started")
            try:
                details, output_items = _execute_capability_native_node(
                    node,
                    outputs_by_node,
                    workflow_input=body.input.payload,
                )
            except (NativeNodeValidationError, ValueError, TypeError) as exc:
                reason = WorkflowRunBlockReason(
                    code="native_node_execution_failed",
                    message=str(exc),
                    source="native_node_runtime",
                    details={
                        "nodeId": node.id,
                        "bindingId": _binding_id(node),
                    },
                )
                emitter.emit(
                    node,
                    "failed",
                    message=reason.message,
                    block_reason=reason,
                    details=reason.details,
                )
                continue

            outputs_by_node[node.id] = output_items
            emitter.emit(
                node,
                "partial",
                message="Native workflow node produced output",
                details=details,
            )
            emitter.emit(
                node,
                "completed",
                message="Native workflow node completed",
                details=details,
            )
            continue

        if _is_first_loop_native_node(node):
            browser_tool_block = _opentabs_tool_block_reason(
                node,
                body.project.agentPermissions,
            ) or _bbx_tool_block_reason(node, body.project.agentPermissions)
            if browser_tool_block is not None:
                emitter.emit(
                    node,
                    "blocked",
                    message=browser_tool_block.message,
                    block_reason=browser_tool_block,
                )
                outputs_by_node[node.id] = []
                continue
            emitter.emit(node, "started", message=_native_node_started_message(node))
            if _is_external_tool_node(node):
                emitter.emit(
                    node,
                    "tool_call_started",
                    message=f"{_external_tool_runtime_label(node)} tool call started",
                    details=_tool_call_trace_details(
                        _external_tool_call_details(
                            node,
                            input_item_count=len(
                                _upstream_outputs(node, outputs_by_node)
                            ),
                            output_item_count=0,
                        )
                    ),
                )
            await _persist_emitter_events(run_id, emitter, session=session)
            if session is not None and _is_native_intelligence_node(node):
                await commit_session(session)
            try:
                details, output_items = await _execute_native_node(
                    node,
                    outputs_by_node,
                    run_id,
                    workflow_id=body.project.id,
                    trace_id=trace_id,
                    session=session,
                    runtime_nodes_by_id=runtime_nodes_by_id,
                    materialized_source_tasks=materialized_source_tasks,
                )
            except WorkflowWebhookDeliveryError as exc:
                reason = WorkflowRunBlockReason(
                    code=exc.code,
                    message=exc.message,
                    source="workflow_webhook_delivery",
                    details=exc.details,
                )
                emitter.emit(
                    node,
                    "failed",
                    message=exc.message,
                    block_reason=reason,
                    details=reason.details,
                )
                await _persist_emitter_events(run_id, emitter, session=session)
                if session is not None and _is_native_intelligence_node(node):
                    await commit_session(session)
                continue
            except (HygieneConfigError, HygieneInvariantError) as exc:
                reason = WorkflowRunBlockReason(
                    code="record_hygiene_execution_failed",
                    message=str(exc),
                    source="record_hygiene",
                    details={"nodeId": node.id, "bindingId": _binding_id(node)},
                )
                emitter.emit(
                    node,
                    "failed",
                    message=str(exc),
                    block_reason=reason,
                    details=reason.details,
                )
                await _persist_emitter_events(run_id, emitter, session=session)
                continue
            except OpenTabsToolExecutionError as exc:
                reason = WorkflowRunBlockReason(
                    code="opentabs_tool_call_failed",
                    message=str(exc),
                    source="opentabs_runtime",
                    details={"nodeId": node.id},
                )
                emitter.emit(
                    node,
                    "failed",
                    message=str(exc),
                    block_reason=reason,
                    details=reason.details,
                )
                await _persist_emitter_events(run_id, emitter, session=session)
                continue
            except BbxToolExecutionError as exc:
                reason = WorkflowRunBlockReason(
                    code="bbx_tool_call_failed",
                    message=str(exc),
                    source="bbx_runtime",
                    details={"nodeId": node.id},
                )
                emitter.emit(
                    node,
                    "failed",
                    message=str(exc),
                    block_reason=reason,
                    details=reason.details,
                )
                await _persist_emitter_events(run_id, emitter, session=session)
                continue
            except (IntelligenceStoreError, ValueError) as exc:
                if _binding_id(node) in _DATA_OPERATOR_BINDING_IDS and not isinstance(
                    exc, IntelligenceStoreError
                ):
                    # Data-operator config/runtime errors must surface with the
                    # data_operator_execution_failed contract, not the native
                    # intelligence block shape this handler emits.
                    binding_input = _read_dict(
                        _read_dict(node.runtime.get("binding")).get("input")
                    )
                    reason = WorkflowRunBlockReason(
                        code="data_operator_execution_failed",
                        message="Data operator execution failed",
                        source="data_operator_runtime",
                        details={
                            "bindingId": _binding_id(node),
                            "operatorId": binding_input.get("operatorId"),
                            "errorType": type(exc).__name__,
                        },
                    )
                    emitter.emit(
                        node,
                        "failed",
                        message=reason.message,
                        block_reason=reason,
                        details=reason.details,
                    )
                    await _persist_emitter_events(run_id, emitter, session=session)
                    continue
                if session is not None and _is_native_intelligence_node(node):
                    await rollback_session_preserving_primary(session, exc)
                code = getattr(exc, "code", None) or str(exc) or "native_intelligence_error"
                event_type: WorkflowNodeRunEventType = (
                    "blocked"
                    if any(
                        token in code
                        for token in (
                            "required",
                            "missing",
                            "not_found",
                            "not_available",
                            "not_registered",
                            "unavailable",
                        )
                    )
                    else "failed"
                )
                reason = WorkflowRunBlockReason(
                    code=code,
                    message=str(exc),
                    source="native_intelligence",
                    details={"exceptionType": type(exc).__name__},
                )
                emitter.emit(
                    node,
                    event_type,
                    message=str(exc),
                    block_reason=reason,
                    details=reason.details,
                )
                await _persist_emitter_events(run_id, emitter, session=session)
                if session is not None and _is_native_intelligence_node(node):
                    await commit_session(session)
                continue
            except Exception as exc:
                if _binding_id(node) not in _DATA_OPERATOR_BINDING_IDS:
                    raise
                binding_input = _read_dict(
                    _read_dict(node.runtime.get("binding")).get("input")
                )
                reason = WorkflowRunBlockReason(
                    code="data_operator_execution_failed",
                    message="Data operator execution failed",
                    source="data_operator_runtime",
                    details={
                        "bindingId": _binding_id(node),
                        "operatorId": binding_input.get("operatorId"),
                        "errorType": type(exc).__name__,
                    },
                )
                emitter.emit(
                    node,
                    "failed",
                    message=reason.message,
                    block_reason=reason,
                    details=reason.details,
                )
                await _persist_emitter_events(run_id, emitter, session=session)
                continue
            outputs_by_node[node.id] = output_items
            emitter.emit(
                node,
                "partial",
                message=_native_node_partial_message(node),
                batch=(
                    _node_batch_reference(
                        body.project.id,
                        run_id,
                        node,
                        item_count=int(details.get("inputItemCount", 0)),
                        record_count=len(output_items),
                    )
                    if _binding_id(node) == NORMALIZE_BINDING_ID
                    else None
                ),
                details=details,
            )
            if _is_external_tool_node(node):
                emitter.emit(
                    node,
                    "tool_call_completed",
                    message=f"{_external_tool_runtime_label(node)} tool call completed",
                    details=_tool_call_trace_details(details),
                )
            emitter.emit(node, "completed", message=_native_node_completed_message(node))
            await _persist_emitter_events(run_id, emitter, session=session)
            if session is not None and _is_native_intelligence_node(node):
                await commit_session(session)
            continue

        dispatch = dispatches_by_node.get(node.id)
        if dispatch is None:
            outputs_by_node.setdefault(node.id, [])
            emitter.emit(node, "started", message="Node started")
            emitter.emit(node, "completed", message="Node completed")
            continue

        mutation_block = _opencli_mutation_block_reason(
            node,
            body.project.agentPermissions,
        )
        if mutation_block is not None:
            emitter.emit(
                node,
                "blocked",
                message=mutation_block.message,
                block_reason=mutation_block,
            )
            outputs_by_node[node.id] = []
            continue

        is_write = _is_opencli_write_node(node)
        emitter.emit(
            node,
            "started",
            message=(
                "OpenCLI action dispatch started"
                if is_write
                else "OpenCLI source dispatch started"
            ),
        )
        fleet_match = await _match_dispatch_fleet_target(
            dispatch,
            node,
            session=session,
        )
        fleet_match_details = _fleet_match_trace_details(fleet_match)
        resource_requirement, resource_resolution = resolve_runtime_resources(
            dispatch,
            node,
            fleet_match,
        )
        resource_details = {
            "resourceRequirement": resource_requirement.model_dump(
                mode="json",
                exclude_none=True,
            ),
            "resourceResolution": resource_resolution.model_dump(
                mode="json",
                exclude_none=True,
            ),
        }
        if fleet_match_details:
            emitter.events[-1].details["fleetMatch"] = fleet_match_details
        emitter.events[-1].details.update(resource_details)

        if resource_resolution.status == "blocked":
            reason = resource_resolution.blockReason
            assert reason is not None
            emitter.emit(
                node,
                "blocked",
                message=reason.message,
                block_reason=reason,
                details=resource_details,
            )
            for ancestor_id in _package_ancestor_ids(node):
                blocked_by_package.setdefault(ancestor_id, []).append(reason)
            outputs_by_node[node.id] = []
            continue

        batch = _batch_reference(body.project.id, run_id, dispatch)
        if is_write:
            emitter.emit(
                node,
                "tool_call_started",
                message="OpenCLI action call started",
                batch=batch,
                details={
                    "functionId": OPENCLI_FUNCTION_ID,
                    "worker": OPENCLI_WORKER,
                    **(
                        {"fleetMatch": fleet_match_details}
                        if fleet_match_details
                        else {}
                    ),
                    **resource_details,
                },
            )
        dispatch_call = _dispatch_opencli_source_to_fleet
        dispatch_kwargs = {"node": node} if "node" in signature(dispatch_call).parameters else {}
        output_items, agent_dispatch_details = await dispatch_call(
            dispatch,
            fleet_match,
            **dispatch_kwargs,
        )
        if not is_write:
            output_items, agent_dispatch_details = _bounded_opencli_dispatch_result(
                output_items,
                agent_dispatch_details,
                max_items=body.project.settings.maxItemsPerRun,
            )
        output_items = _opencli_dispatch_source_items(node, dispatch, output_items)
        batch = _batch_reference(body.project.id, run_id, dispatch)
        if output_items:
            batch = batch.model_copy(update={"itemCount": len(output_items)})
        dispatch_trace_details = {
            **({"fleetMatch": fleet_match_details} if fleet_match_details else {}),
            **resource_details,
            **({"agentDispatch": agent_dispatch_details} if agent_dispatch_details else {}),
        }
        if not is_write:
            emitter.emit(
                node,
                "batch_ready",
                message="OpenCLI batch reference ready",
                batch=batch,
                details={
                    "functionId": OPENCLI_FUNCTION_ID,
                    "worker": OPENCLI_WORKER,
                    **dispatch_trace_details,
                },
            )
        if agent_dispatch_details and agent_dispatch_details.get("success") is False:
            reason = WorkflowRunBlockReason(
                code="fleet_agent_dispatch_failed",
                message=str(agent_dispatch_details.get("error") or "Fleet agent dispatch failed"),
                source="workflow_fleet",
                details={
                    "adapterTaskId": dispatch.taskId,
                    "sourceGroup": dispatch.sourceGroup,
                    "agentDispatch": agent_dispatch_details,
                    **({"fleetMatch": fleet_match_details} if fleet_match_details else {}),
                },
            )
            emitter.emit(
                node,
                "failed",
                message="OpenCLI source dispatch failed on selected fleet agent",
                block_reason=reason,
                details=reason.details,
            )
            for ancestor_id in _package_ancestor_ids(node):
                blocked_by_package.setdefault(ancestor_id, []).append(reason)
            outputs_by_node[node.id] = []
            continue

        emitter.emit(
            node,
            "partial",
            message=(
                "OpenCLI action completed through selected fleet agent"
                if is_write and agent_dispatch_details
                else (
                    "OpenCLI action result received"
                    if is_write
                    else (
                        "OpenCLI source items collected through local OpenCLI"
                        if _is_local_opencli_dispatch(agent_dispatch_details)
                        else (
                            "OpenCLI source items collected through selected fleet agent"
                            if agent_dispatch_details
                            else "OpenCLI dispatch envelope is ready for worker fanout"
                        )
                    )
                )
            ),
            details={
                "adapterTaskId": dispatch.taskId,
                "sourceGroup": dispatch.sourceGroup,
                "itemCount": len(output_items),
                "outputPort": "items[]",
                **dispatch_trace_details,
            },
        )
        if is_write:
            emitter.emit(
                node,
                "tool_call_completed",
                message="OpenCLI action call completed",
                details={
                    "functionId": OPENCLI_FUNCTION_ID,
                    "worker": OPENCLI_WORKER,
                    **dispatch_trace_details,
                },
            )
        emitter.emit(
            node,
            "completed",
            message=(
                "OpenCLI action dispatch completed"
                if is_write
                else (
                    "OpenCLI source dispatch completed through local OpenCLI"
                    if _is_local_opencli_dispatch(agent_dispatch_details)
                    else (
                        "OpenCLI source dispatch completed through selected fleet agent"
                        if agent_dispatch_details
                        else "OpenCLI source dispatch completed"
                    )
                )
            ),
        )
        outputs_by_node[node.id] = output_items

    for package_node in reversed(package_nodes):
        if package_node.id in managed_package_terminal_ids:
            continue
        trace_errors = [
            error
            for error in (trace.errors if trace else [])
            if error.node_id in {None, package_node.id}
        ]
        if trace_errors:
            emitter.emit(
                package_node,
                "blocked",
                message=trace_errors[0].message,
                block_reason=_reason_from_compile_error(trace_errors[0]),
            )
            continue

        internal_reasons = blocked_by_package.get(package_node.id, [])
        if internal_reasons:
            descendant_ids = {
                node.id
                for node in runtime_nodes
                if package_node.id in _package_ancestor_ids(node)
            }
            source_node_ids = {
                node.id
                for node in runtime_nodes
                if node.id in descendant_ids
                and (
                    _read_string(node.params.get("sourceGroup"))
                    or _read_string(node.params.get("source_group"))
                )
            }
            terminal_events_by_node: dict[str, WorkflowNodeRunEvent] = {}
            for event in emitter.events:
                if (
                    event.nodeId in descendant_ids
                    and event.eventType in {"completed", "failed", "blocked"}
                ):
                    terminal_events_by_node[event.nodeId] = event
            source_terminal_events = [
                event
                for event in terminal_events_by_node.values()
                if event.nodeId in source_node_ids
            ]
            has_successful_source = any(
                event.eventType == "completed" for event in source_terminal_events
            )
            has_source_failure = any(
                event.eventType in {"failed", "blocked"}
                for event in source_terminal_events
            )
            has_non_source_failure = any(
                event.eventType in {"failed", "blocked"}
                and event.nodeId not in source_node_ids
                for event in terminal_events_by_node.values()
            )
            tolerates_internal_reasons = (
                _collects_per_source_failures(package_node)
                and has_successful_source
                and has_source_failure
                and not has_non_source_failure
            )
            emitter.emit(
                package_node,
                "partial",
                message=(
                    "Package collected available source results with per-source failures"
                    if tolerates_internal_reasons
                    else "Package produced partial source results before an internal block"
                ),
            )
            if tolerates_internal_reasons:
                emitter.emit(
                    package_node,
                    "completed",
                    message="Package completed with per-source failures preserved in the trace",
                    details={"sourceFailureCount": len(internal_reasons)},
                )
                continue
            emitter.emit(
                package_node,
                "blocked",
                message="Package has blocked internal runtime nodes",
                block_reason=WorkflowRunBlockReason(
                    code="internal_node_blocked",
                    message="Package has blocked internal runtime nodes",
                    source="workflow_runtime",
                    details={
                        "blockedReasons": [
                            reason.model_dump(mode="json") for reason in internal_reasons
                        ],
                    },
                ),
            )
            continue

        emitter.emit(package_node, "completed", message="Package node completed")

    events = [*prior_events, *emitter.events]
    projection = _build_projection(
        workflow_id=body.project.id,
        run_id=run_id,
        trace_id=trace_id,
        package_node_id=(trace.packageNodeId if trace else None) or body.packageNodeId,
        started_at=started_at,
        valid=trace.valid if trace else True,
        errors=trace.errors if trace else [],
        runtime_nodes=runtime_nodes,
        events=events,
    )
    await _store_workflow_run(
        run_id,
        request=body,
        projection=projection,
        events=events,
        session=session,
        workflow_version_id=workflow_version_id,
        studio_workflow_version_id=studio_workflow_version_id,
    )
    if session is not None:
        stored = await _load_workflow_run(run_id, session=session, cache=False)
        if stored is not None:
            projection = _build_projection(
                workflow_id=body.project.id,
                run_id=run_id,
                trace_id=trace_id,
                package_node_id=(trace.packageNodeId if trace else None)
                or body.packageNodeId,
                started_at=started_at,
                valid=trace.valid if trace else True,
                errors=trace.errors if trace else [],
                runtime_nodes=runtime_nodes,
                events=stored.events,
            )
            await _store_workflow_run(
                run_id,
                request=body,
                projection=projection,
                events=stored.events,
                session=session,
                workflow_version_id=workflow_version_id,
                studio_workflow_version_id=studio_workflow_version_id,
            )
    await _materialize_waiting_image_jobs(
        body,
        run_id=run_id,
        events=events,
        session=session,
    )
    return projection


async def _materialize_waiting_image_jobs(
    request: WorkflowRunStartRequest,
    *,
    run_id: str,
    events: list[WorkflowNodeRunEvent],
    session: AsyncSession | None,
) -> None:
    """Persist durable generation intent in the same transaction as waiting.

    Stateless preview runs may still use synthetic snapshot ids. They retain
    the waiting projection, but only a scope-valid platform snapshot can create
    a job that a worker is allowed to dispatch.
    """

    if session is None:
        return

    from backend.models.image_studio import CanvasSnapshot
    from backend.services import image_studio_service

    waiting_events = [
        event
        for event in events
        if event.eventType == "waiting"
        and event.details.get("bindingId") == IMAGE_GENERATION_BINDING_ID
    ]
    for event in waiting_events:
        snapshot_id = _read_string(event.details.get("canvasSnapshotId"))
        attempt = event.details.get("attempt")
        idempotency_key = _read_string(event.details.get("idempotencyKey"))
        if snapshot_id is None or not isinstance(attempt, int) or idempotency_key is None:
            continue
        snapshot = await session.scalar(
            select(CanvasSnapshot).where(
                CanvasSnapshot.id == snapshot_id,
                CanvasSnapshot.workflow_id == request.project.id,
                CanvasSnapshot.node_id == event.nodeId,
            )
        )
        if snapshot is None:
            continue
        await image_studio_service.create_job(
            session,
            snapshot=snapshot,
            run_id=run_id,
            node_id=event.nodeId,
            attempt=attempt,
            idempotency_key=idempotency_key,
            mode="workflow",
        )


async def get_workflow_run_projection(
    run_id: str,
    *,
    session: AsyncSession | None = None,
) -> WorkflowRunProjection | None:
    stored = (
        await _load_workflow_run(run_id, session=session)
        if session is not None
        else _RUNS.get(run_id)
    )
    return stored.projection if stored else None


async def list_workflow_run_events(
    run_id: str,
    *,
    session: AsyncSession | None = None,
    after_sequence: int | None = None,
    node_id: str | None = None,
    event_type: WorkflowNodeRunEventType | None = None,
    limit: int | None = None,
) -> list[WorkflowNodeRunEvent] | None:
    if session is not None:
        if await session.get(WorkflowRunRow, run_id) is None:
            return None
        statement = (
            select(WorkflowRunEventRow)
            .where(WorkflowRunEventRow.run_id == run_id)
            .order_by(WorkflowRunEventRow.sequence)
        )
        if after_sequence is not None:
            statement = statement.where(WorkflowRunEventRow.sequence > after_sequence)
        if node_id is not None:
            statement = statement.where(WorkflowRunEventRow.node_id == node_id)
        if event_type is not None:
            statement = statement.where(WorkflowRunEventRow.event_type == event_type)
        if limit is not None:
            statement = statement.limit(limit)
        rows = (await session.execute(statement)).scalars().all()
        return [
            WorkflowNodeRunEvent.model_validate(event_row.payload)
            for event_row in rows
        ]

    stored = _RUNS.get(run_id)
    if not stored:
        return None
    events = _filter_workflow_run_events(
        stored.events,
        after_sequence=after_sequence,
        node_id=node_id,
        event_type=event_type,
        limit=limit,
    )
    return events


async def get_workflow_run_checkpoint(
    run_id: str,
    *,
    session: AsyncSession | None = None,
) -> WorkflowRunCheckpoint | None:
    stored = (
        await _load_workflow_run(run_id, session=session)
        if session is not None
        else _RUNS.get(run_id)
    )
    if stored is None:
        return None
    return _build_checkpoint(stored.request, stored.projection, stored.events)


async def continue_workflow_run_with_source_outputs(
    run_id: str,
    body: WorkflowRunSourceOutputsRequest,
    *,
    session: AsyncSession | None = None,
) -> WorkflowRunProjection | None:
    # Hold the per-run_id lock across the read of prior stored state through
    # the write in _store_workflow_run (invoked inside start_workflow_run).
    # Without this, two concurrent continuation requests for the same run_id
    # both read the same prior event list, then each does a delete+reinsert
    # of WorkflowRunEventRows — last writer wins and the other request's
    # events are silently dropped.
    lock = await _get_run_lock(run_id)
    async with lock:
        # Session-first: the in-memory mirror only refreshes via
        # queue_after_commit callbacks, so mid-run commits can leave _RUNS
        # holding an early (even empty) event snapshot until the next commit.
        # The database transcript is authoritative for continuations; memory
        # is only trusted when there is no session at all.
        stored = (
            await _load_workflow_run(run_id, session=session)
            if session is not None
            else _RUNS.get(run_id)
        )
        if stored is None:
            return None

        incoming_node_ids = set(body.sourceOutputs)
        duplicate_image_node_ids = {
            node_id
            for node_id in incoming_node_ids
            if _is_image_generation_project_node(stored.request.project, node_id)
            and stored.request.sourceOutputs.get(node_id) == body.sourceOutputs.get(node_id)
            and _projection_node_status(stored.projection, node_id) == "completed"
        }
        if incoming_node_ids and duplicate_image_node_ids == incoming_node_ids:
            return stored.projection

        merged_outputs = _merge_source_outputs(
            stored.request.sourceOutputs,
            body.sourceOutputs,
        )
        request = stored.request.model_copy(
            update={
                "runId": run_id,
                "traceId": stored.projection.traceId,
                "sourceOutputs": merged_outputs,
            },
            deep=True,
        )
        return await start_workflow_run(
            request,
            session=session,
            existing_events=stored.events,
            workflow_version_id=stored.workflow_version_id,
            studio_workflow_version_id=stored.studio_workflow_version_id,
        )


async def _store_workflow_run(
    run_id: str,
    *,
    request: WorkflowRunStartRequest,
    projection: WorkflowRunProjection,
    events: list[WorkflowNodeRunEvent],
    session: AsyncSession | None,
    workflow_version_id: str | None = None,
    studio_workflow_version_id: str | None = None,
) -> None:
    events_to_mirror = list(events)
    stored_events = list(events)
    if session is not None:
        row = await session.get(WorkflowRunRow, run_id)
        if row is None:
            row = WorkflowRunRow(id=run_id)
            session.add(row)

        row.workflow_id = projection.workflowId
        row.trace_id = projection.traceId
        row.status = projection.status
        row.valid = projection.valid
        row.package_node_id = projection.packageNodeId
        row.workflow_version_id = workflow_version_id
        row.studio_workflow_version_id = studio_workflow_version_id
        row.request = request.model_dump(mode="json")
        row.projection = projection.model_dump(mode="json")

        append_result = await append_workflow_run_events(
            session,
            run_id=run_id,
            events=events,
        )
        stored_events = append_result.events
        events_to_mirror = append_result.appended_events

    stored = _StoredWorkflowRun(
        request,
        projection,
        stored_events,
        workflow_version_id,
        studio_workflow_version_id,
    )
    if session is None:
        _RUNS[run_id] = stored
        await publish_workflow_run_event_mirror(events_to_mirror)
        return

    queue_after_commit(session, lambda: _RUNS.__setitem__(run_id, stored))
    if events_to_mirror:
        queue_after_commit(
            session,
            lambda: publish_workflow_run_event_mirror(events_to_mirror),
        )


async def _persist_emitter_events(
    run_id: str,
    emitter: _WorkflowRunEventEmitter,
    *,
    session: AsyncSession | None,
) -> None:
    if session is None or not emitter.events:
        return
    result = await append_workflow_run_events(
        session,
        run_id=run_id,
        events=emitter.events,
    )
    emitter.events[:] = result.events
    if result.appended_events:
        queue_after_commit(
            session,
            lambda: publish_workflow_run_event_mirror(result.appended_events),
        )


async def _load_workflow_run(
    run_id: str,
    *,
    session: AsyncSession | None,
    cache: bool = True,
) -> _StoredWorkflowRun | None:
    if session is None:
        return None

    row = await session.get(WorkflowRunRow, run_id)
    if row is None:
        return None

    event_rows = (
        (
            await session.execute(
                select(WorkflowRunEventRow)
                .where(WorkflowRunEventRow.run_id == run_id)
                .order_by(WorkflowRunEventRow.sequence)
            )
        )
        .scalars()
        .all()
    )
    stored = _StoredWorkflowRun(
        request=WorkflowRunStartRequest.model_validate(row.request),
        projection=WorkflowRunProjection.model_validate(row.projection),
        events=[WorkflowNodeRunEvent.model_validate(event_row.payload) for event_row in event_rows],
        workflow_version_id=row.workflow_version_id,
        studio_workflow_version_id=row.studio_workflow_version_id,
    )
    if cache:
        _RUNS[run_id] = stored
    return stored


def _merge_source_outputs(
    existing: dict[str, list[dict[str, Any]]],
    incoming: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    merged = {node_id: [dict(item) for item in items] for node_id, items in existing.items()}
    for node_id, items in incoming.items():
        merged.setdefault(node_id, []).extend(dict(item) for item in items)
    return merged


def _filter_workflow_run_events(
    events: list[WorkflowNodeRunEvent],
    *,
    after_sequence: int | None,
    node_id: str | None,
    event_type: WorkflowNodeRunEventType | None,
    limit: int | None,
) -> list[WorkflowNodeRunEvent]:
    filtered = [
        event
        for event in events
        if (after_sequence is None or event.sequence > after_sequence)
        and (node_id is None or event.nodeId == node_id)
        and (event_type is None or event.eventType == event_type)
    ]
    if limit is not None:
        filtered = filtered[:limit]
    return list(filtered)


def _build_checkpoint(
    request: WorkflowRunStartRequest,
    projection: WorkflowRunProjection,
    events: list[WorkflowNodeRunEvent],
) -> WorkflowRunCheckpoint:
    source_outputs = request.sourceOutputs
    source_output_node_ids = sorted(source_outputs)
    source_output_item_count = sum(len(items) for items in source_outputs.values())
    last_sequence = max((event.sequence for event in events), default=0)
    checkpoint_id = f"{projection.runId}:{last_sequence:04d}"
    waiting_node_ids = sorted(
        state.nodeId for state in projection.nodeStates if state.status == "waiting"
    )
    pending_jobs: list[dict[str, Any]] = []
    for node_id in waiting_node_ids:
        waiting_event = next(
            (
                event
                for event in reversed(events)
                if event.nodeId == node_id and event.eventType == "waiting"
            ),
            None,
        )
        if waiting_event is not None:
            pending_jobs.append(dict(waiting_event.details))
    return WorkflowRunCheckpoint(
        checkpointId=checkpoint_id,
        workflowId=projection.workflowId,
        runId=projection.runId,
        traceId=projection.traceId,
        status=projection.status,
        valid=projection.valid,
        eventCount=projection.eventCount,
        lastSequence=last_sequence,
        updatedAt=projection.updatedAt,
        nodeStates=projection.nodeStates,
        sourceOutputNodeIds=source_output_node_ids,
        sourceOutputItemCount=source_output_item_count,
        waitingNodeIds=waiting_node_ids,
        pendingJobs=pending_jobs,
        canContinueWithSourceOutputs=True,
        continuationPath=f"/api/v1/workflows/runs/{projection.runId}/source-outputs",
        tracePath=f"/api/v1/workflows/runs/{projection.runId}/trace",
    )


class _WorkflowRunEventEmitter:
    def __init__(
        self,
        *,
        workflow_id: str,
        run_id: str,
        trace_id: str,
        source_id: str | None = None,
        initial_sequence: int = 0,
    ) -> None:
        self._workflow_id = workflow_id
        self._run_id = run_id
        self._trace_id = trace_id
        self._source_id = source_id
        self._initial_sequence = initial_sequence
        self.events: list[WorkflowNodeRunEvent] = []

    def emit(
        self,
        node: CompiledWorkflowNode,
        event_type: WorkflowNodeRunEventType,
        *,
        message: str | None = None,
        block_reason: WorkflowRunBlockReason | None = None,
        batch: WorkflowRunBatchReference | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        sequence = self._initial_sequence + len(self.events) + 1
        node_path = _compiled_node_path(node)
        package_node_id, internal_node_id = _legacy_location_from_node_path(node_path)
        source_group = _read_string(node.params.get("sourceGroup")) or _read_string(
            node.params.get("source_group")
        )
        if batch and batch.sourceGroup:
            source_group = batch.sourceGroup

        self.events.append(
            WorkflowNodeRunEvent(
                id=f"{self._run_id}:{sequence:04d}:{event_type}:{node.id}",
                sequence=sequence,
                workflowId=self._workflow_id,
                workflowRunId=self._run_id,
                traceId=self._trace_id,
                nodeId=node.id,
                sourceId=self._source_id,
                eventType=event_type,
                createdAt=_utcnow(),
                nodePath=node_path,
                packageNodeId=package_node_id,
                internalNodeId=internal_node_id,
                sourceGroup=source_group,
                message=message,
                blockReason=block_reason,
                batch=batch,
                details=details or {},
            )
        )

    def emit_nested(
        self,
        package_node: CompiledWorkflowNode,
        internal_node_id: str,
        event_type: WorkflowNodeRunEventType,
        *,
        message: str | None = None,
        block_reason: WorkflowRunBlockReason | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        sequence = self._initial_sequence + len(self.events) + 1
        node_id = f"{package_node.id}{INTERNAL_ID_SEPARATOR}{internal_node_id}"
        self.events.append(
            WorkflowNodeRunEvent(
                id=f"{self._run_id}:{sequence:04d}:{event_type}:{node_id}",
                sequence=sequence,
                workflowId=self._workflow_id,
                workflowRunId=self._run_id,
                traceId=self._trace_id,
                nodeId=node_id,
                sourceId=self._source_id,
                eventType=event_type,
                createdAt=_utcnow(),
                nodePath=[package_node.id, internal_node_id],
                packageNodeId=package_node.id,
                internalNodeId=internal_node_id,
                message=message,
                blockReason=block_reason,
                details=details or {},
            )
        )


def _build_projection(
    *,
    workflow_id: str,
    run_id: str,
    trace_id: str,
    package_node_id: str | None,
    started_at: str,
    valid: bool,
    errors: list[WorkflowCompileError],
    runtime_nodes: list[CompiledWorkflowNode],
    events: list[WorkflowNodeRunEvent],
) -> WorkflowRunProjection:
    states: dict[str, WorkflowRunNodeState] = {}
    ordered_ids: list[str] = []
    for node in runtime_nodes:
        node_path = _compiled_node_path(node)
        package_parent_id, internal_node_id = _legacy_location_from_node_path(node_path)
        states[node.id] = WorkflowRunNodeState(
            nodeId=node.id,
            status="queued",
            nodePath=node_path,
            packageNodeId=package_parent_id,
            internalNodeId=internal_node_id,
        )
        ordered_ids.append(node.id)

    for event in events:
        state = states.setdefault(
            event.nodeId,
            WorkflowRunNodeState(
                nodeId=event.nodeId,
                nodePath=event.nodePath,
                packageNodeId=event.packageNodeId,
                internalNodeId=event.internalNodeId,
            ),
        )
        if event.nodeId not in ordered_ids:
            ordered_ids.append(event.nodeId)
        state.latestEventId = event.id
        state.eventCount += 1
        state.status = _status_after_event(event.eventType)
        if event.sourceGroup and event.sourceGroup not in state.sourceGroups:
            state.sourceGroups.append(event.sourceGroup)
        if event.blockReason:
            state.blockReasons.append(event.blockReason)
        if event.batch:
            if all(batch.batchId != event.batch.batchId for batch in state.batches):
                state.batches.append(event.batch)

    node_states = [states[node_id] for node_id in ordered_ids]
    status = _run_status(node_states, valid, runtime_nodes)
    updated_at = events[-1].createdAt if events else started_at
    return WorkflowRunProjection(
        workflowId=workflow_id,
        runId=run_id,
        traceId=trace_id,
        valid=valid,
        status=status,
        packageNodeId=package_node_id,
        startedAt=started_at,
        updatedAt=updated_at,
        eventCount=len(events),
        nodeStates=node_states,
        errors=errors,
    )


def _compile_failure_events(
    *,
    workflow_id: str,
    run_id: str,
    trace_id: str,
    errors: list[WorkflowCompileError],
) -> list[WorkflowNodeRunEvent]:
    events: list[WorkflowNodeRunEvent] = []
    for error in errors:
        if not error.node_id:
            continue
        sequence = len(events) + 1
        events.append(
            WorkflowNodeRunEvent(
                id=f"{run_id}:{sequence:04d}:failed:{error.node_id}",
                sequence=sequence,
                workflowId=workflow_id,
                workflowRunId=run_id,
                traceId=trace_id,
                nodeId=error.node_id,
                eventType="failed",
                createdAt=_utcnow(),
                message=error.message,
                blockReason=_reason_from_compile_error(error),
                details={
                    "edgeId": error.edge_id,
                    "path": error.path,
                },
            )
        )
    return events


def _batch_reference(
    workflow_id: str,
    run_id: str,
    dispatch: WorkflowOpenCLIHDATraceDispatch,
) -> WorkflowRunBatchReference:
    batch_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"opencli-admin/workflow/{workflow_id}/run/{run_id}/batch/{dispatch.taskId}",
        )
    )
    return WorkflowRunBatchReference(
        batchId=batch_id,
        itemCount=0,
        recordCount=0,
        sourceGroup=dispatch.sourceGroup,
        adapterTaskId=dispatch.taskId,
        odpRef=(
            f"odp://workflow-runs/{run_id}/nodes/{dispatch.nodeId}"
            f"/sources/{dispatch.sourceGroup}/batches/{batch_id}"
        ),
        manifestUri=f"/api/v1/workflows/runs/{run_id}/evidence-batches/{batch_id}",
    )


def _node_batch_reference(
    workflow_id: str,
    run_id: str,
    node: CompiledWorkflowNode,
    *,
    item_count: int,
    record_count: int = 0,
    adapter_task_id: str | None = None,
) -> WorkflowRunBatchReference:
    source_group = _source_group(node, node.id)
    batch_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"opencli-admin/workflow/{workflow_id}/run/{run_id}/batch/{node.id}",
        )
    )
    return WorkflowRunBatchReference(
        batchId=batch_id,
        itemCount=item_count,
        recordCount=record_count,
        sourceGroup=source_group,
        adapterTaskId=adapter_task_id,
        odpRef=(
            f"odp://workflow-runs/{run_id}/nodes/{node.id}"
            f"/sources/{source_group}/batches/{batch_id}"
        ),
        manifestUri=f"/api/v1/workflows/runs/{run_id}/evidence-batches/{batch_id}",
    )


async def _match_dispatch_fleet_target(
    dispatch: WorkflowOpenCLIHDATraceDispatch,
    node: CompiledWorkflowNode,
    *,
    session: AsyncSession | None,
) -> WorkflowFleetCapabilityMatchResponse | None:
    if session is None:
        return None

    adapter_node_id = _read_string(node.params.get("opencliAdapterNodeId"))
    if adapter_node_id:
        from backend.workflow.opencli_adapter_nodes import resolve_opencli_adapter_node

        adapter_node = resolve_opencli_adapter_node(adapter_node_id)
        if adapter_node is not None and not adapter_node.browser:
            # Public/non-browser commands are safest and fastest on the local OpenCLI
            # runtime. Browser-backed commands continue through fleet/profile matching.
            return None
    request = WorkflowFleetCapabilityMatchRequest(
        adapterNodeId=adapter_node_id,
        site=None if adapter_node_id else dispatch.site,
        command=None if adapter_node_id else dispatch.command,
    )
    return await match_workflow_fleet_capability(session, request)


def _fleet_match_trace_details(
    match: WorkflowFleetCapabilityMatchResponse | None,
) -> dict[str, Any] | None:
    if match is None:
        return None
    return match.model_dump(mode="json", exclude_none=True)


async def _dispatch_opencli_source_to_fleet(
    dispatch: WorkflowOpenCLIHDATraceDispatch,
    match: WorkflowFleetCapabilityMatchResponse | None,
    *,
    node: CompiledWorkflowNode | None = None,
) -> tuple[list[dict[str, Any]], dict[str, object] | None]:
    target = _fleet_agent_dispatch_target(dispatch, match)
    if target is None:
        payload = _read_dict(dispatch.iii.get("payload"))
        dispatch_policy = _read_string(payload.get("dispatch_policy"))
        adapter_node_id = (
            _read_string(node.params.get("opencliAdapterNodeId"))
            if node is not None
            else None
        )
        local_adapter = False
        if adapter_node_id:
            from backend.workflow.opencli_adapter_nodes import resolve_opencli_adapter_node

            adapter_node = resolve_opencli_adapter_node(adapter_node_id)
            local_adapter = adapter_node is not None and not adapter_node.browser
        if (
            dispatch.packageNodeId is not None
            and dispatch_policy != "inline"
            and not local_adapter
        ):
            # Packaged HDA fanout retains its asynchronous worker-envelope
            # contract unless the package explicitly exposes raw items to a
            # downstream node in the same run.
            return [], None
        from backend.channels.registry import get_channel

        config: dict[str, Any] = {
            "site": dispatch.site,
            "command": dispatch.command,
            "format": _read_string(payload.get("format")) or "json",
            "args": dispatch.args,
        }
        positional_args = payload.get("positional_args", payload.get("positionalArgs"))
        config["positional_args"] = positional_args if isinstance(positional_args, list) else []
        result = await get_channel("opencli").collect(config, {})
        details: dict[str, object] = {
            "attempted": True,
            "protocol": "local",
            "endpoint": "local-opencli",
            "mode": "direct",
            "site": dispatch.site,
            "command": dispatch.command,
            "format": config["format"],
            "success": result.success,
            "itemCount": len(result.items) if result.success else 0,
        }
        if result.error:
            details["error"] = result.error
        if result.error_type:
            details["errorType"] = result.error_type
        if result.metadata:
            details["metadata"] = result.metadata
        return (result.items if result.success else []), details

    from backend.channels.opencli_channel import (
        OpenCLIChannel,
        _collect_via_agent,
        _collect_via_ws_agent,
    )

    protocol = str(target["protocol"])
    mode = str(target["mode"])
    output_format = str(target["format"])
    positional_args = target["positionalArgs"]
    if not isinstance(positional_args, list):
        positional_args = []
    positional_args = [str(item) for item in positional_args]

    details: dict[str, object] = {
        "attempted": True,
        "protocol": protocol,
        "endpoint": target["endpoint"],
        "mode": mode,
        "site": dispatch.site,
        "command": dispatch.command,
        "format": output_format,
    }
    agent_url = str(target["agentUrl"])
    if agent_url:
        details["agentUrl"] = agent_url
    try:
        if protocol == "local":
            result = await OpenCLIChannel().collect(
                {
                    "site": dispatch.site,
                    "command": dispatch.command,
                    "args": dispatch.args,
                    "positional_args": positional_args,
                    "format": output_format,
                },
                {},
            )
        elif protocol == "ws":
            result = await _collect_via_ws_agent(
                agent_url,
                dispatch.site,
                dispatch.command,
                dispatch.args,
                positional_args,
                output_format,
                mode,
            )
        else:
            result = await _collect_via_agent(
                agent_url,
                dispatch.site,
                dispatch.command,
                dispatch.args,
                positional_args,
                output_format,
                mode,
            )
    except Exception as exc:
        details.update(
            {
                "success": False,
                "itemCount": 0,
                "error": str(exc),
                "errorType": type(exc).__name__,
            }
        )
        return [], details

    details.update(
        {
            "success": result.success,
            "itemCount": len(result.items) if result.success else 0,
        }
    )
    if result.error:
        details["error"] = result.error
    if result.error_type:
        details["errorType"] = result.error_type
    if result.metadata:
        details["metadata"] = result.metadata
    return (result.items if result.success else []), details


def _fleet_agent_dispatch_target(
    dispatch: WorkflowOpenCLIHDATraceDispatch,
    match: WorkflowFleetCapabilityMatchResponse | None,
) -> dict[str, object] | None:
    if match is None or not match.matched or match.selected is None:
        return None
    selected = match.selected
    protocol = (selected.agentProtocol or "").lower()
    if protocol not in {"http", "local", "ws"}:
        return None
    agent_url = ""
    if protocol != "local":
        agent_url = (selected.agentUrl or selected.endpoint or "").rstrip("/")
    if protocol != "local" and not agent_url:
        return None
    payload = _read_dict(dispatch.iii.get("payload"))
    positional_args = payload.get("positional_args", payload.get("positionalArgs"))
    if not isinstance(positional_args, list):
        positional_args = []
    return {
        "endpoint": selected.endpoint,
        "agentUrl": agent_url,
        "protocol": protocol,
        "mode": _read_string(payload.get("mode")) or selected.mode or "cdp",
        "format": _read_string(payload.get("format")) or "json",
        "positionalArgs": positional_args,
    }


def _reason_from_compile_error(error: WorkflowCompileError) -> WorkflowRunBlockReason:
    return WorkflowRunBlockReason(
        code=error.code,
        message=error.message,
        source="workflow_compile",
        details=error.model_dump(mode="json"),
    )


def _status_after_event(event_type: WorkflowNodeRunEventType) -> WorkflowRunStatus:
    if event_type == "started":
        return "running"
    if event_type in {"batch_ready", "tool_call_started", "tool_call_completed"}:
        return "partial"
    if event_type == "failed":
        return "failed"
    if event_type in {"blocked", "waiting", "partial", "completed", "queued"}:
        return event_type
    return "partial"


def _run_status(
    node_states: list[WorkflowRunNodeState],
    valid: bool,
    runtime_nodes: list[CompiledWorkflowNode] | None = None,
) -> WorkflowRunStatus:
    if not valid:
        return "failed"
    statuses = {state.status for state in node_states}
    collect_per_source_package_ids_by_state = (
        {
            state.nodeId: _collect_per_source_package_ids(state, runtime_nodes)
            for state in node_states
        }
        if runtime_nodes
        else {}
    )
    successful_source_package_ids = {
        package_id
        for state in node_states
        if state.status == "completed"
        for package_id in collect_per_source_package_ids_by_state.get(state.nodeId, set())
    }
    tolerated_source_failure_ids = (
        {
            state.nodeId
            for state in node_states
            if state.status in {"failed", "blocked"}
            and (
                collect_per_source_package_ids_by_state.get(state.nodeId, set())
                & successful_source_package_ids
            )
        }
        if runtime_nodes
        else set()
    )
    effective_statuses = {
        state.status
        for state in node_states
        if state.nodeId not in tolerated_source_failure_ids
    }
    if runtime_nodes:
        terminal_ids = {node.id for node in runtime_nodes if _is_builder_output(node)}
        terminal_statuses = {state.status for state in node_states if state.nodeId in terminal_ids}
        if (
            len(terminal_ids) > 1
            and len(terminal_statuses) == len(terminal_ids)
            and terminal_statuses <= {"completed", "failed", "blocked"}
            and "completed" in terminal_statuses
            and terminal_statuses.intersection({"failed", "blocked"})
        ):
            return "partial_success"
    if "failed" in effective_statuses:
        return "failed"
    if "blocked" in effective_statuses:
        return "blocked"
    if "waiting" in effective_statuses:
        return "waiting"
    if "running" in effective_statuses or "partial" in effective_statuses:
        return "partial"
    if tolerated_source_failure_ids and effective_statuses and effective_statuses <= {"completed"}:
        return "partial_success"
    if statuses and statuses <= {"completed"}:
        return "completed"
    return "queued"


def _collects_per_source_failures(node: CompiledWorkflowNode) -> bool:
    execution = _read_dict(node.params.get("execution"))
    return _read_string(execution.get("failureMode")) == "collect-per-source"


def _collect_per_source_package_ids(
    state: WorkflowRunNodeState,
    runtime_nodes: list[CompiledWorkflowNode],
) -> set[str]:
    source_groups = getattr(state, "sourceGroups", [])
    node_path = getattr(state, "nodePath", [])
    runtime_node = next(
        (node for node in runtime_nodes if node.id == state.nodeId),
        None,
    )
    if (
        not source_groups
        or runtime_node is None
        or not (
            _read_string(runtime_node.params.get("sourceGroup"))
            or _read_string(runtime_node.params.get("source_group"))
        )
    ):
        return set()
    tolerant_package_ids = {
        node.id for node in runtime_nodes if _collects_per_source_failures(node)
    }
    return {
        INTERNAL_ID_SEPARATOR.join(node_path[:depth])
        for depth in range(1, len(node_path))
        if INTERNAL_ID_SEPARATOR.join(node_path[:depth]) in tolerant_package_ids
    }


def _is_builder_output(node: CompiledWorkflowNode) -> bool:
    builder = _read_dict(node.params.get("builder"))
    return _read_string(builder.get("nodeType")) in {
        "records-output",
        "email-output",
        "webhook-output",
    }


def _select_runtime_nodes_for_trigger(
    nodes: list[CompiledWorkflowNode],
    *,
    trigger_kind: str,
    trigger_node_id: str | None,
) -> tuple[list[CompiledWorkflowNode], WorkflowCompileError | None]:
    """Select one trigger entry and its reachable subgraph for an independent Run."""

    trigger_nodes = [node for node in nodes if _runtime_trigger_kind(node) is not None]
    if not trigger_nodes:
        return nodes, None

    selected: CompiledWorkflowNode | None = None
    if trigger_node_id:
        selected = next((node for node in nodes if node.id == trigger_node_id), None)
        if selected is None:
            return [], WorkflowCompileError(
                code="workflow_trigger_not_found",
                message=f'Workflow trigger node "{trigger_node_id}" was not found.',
                node_id=trigger_node_id,
                path=["trigger", "triggerNodeId"],
            )
        selected_kind = _runtime_trigger_kind(selected)
        if selected_kind is None:
            return [], WorkflowCompileError(
                code="unsupported_workflow_trigger",
                message=f'Node "{trigger_node_id}" is not a workflow trigger entry.',
                node_id=trigger_node_id,
                path=["trigger", "triggerNodeId"],
            )
        requested_kind = "manual" if trigger_kind == "ai" else trigger_kind
        if selected_kind != requested_kind:
            return [], WorkflowCompileError(
                code="workflow_trigger_kind_mismatch",
                message=(
                    f'Workflow trigger node "{trigger_node_id}" is "{selected_kind}", '
                    f'not "{trigger_kind}".'
                ),
                node_id=trigger_node_id,
                path=["trigger", "kind"],
            )
    else:
        requested_kind = "manual" if trigger_kind == "ai" else trigger_kind
        candidates = [
            node for node in trigger_nodes if _runtime_trigger_kind(node) == requested_kind
        ]
        if len(candidates) == 1:
            selected = candidates[0]
        elif len(candidates) > 1:
            return [], WorkflowCompileError(
                code="workflow_trigger_ambiguous",
                message=(
                    f'Workflow has multiple "{trigger_kind}" trigger entries; '
                    "triggerNodeId is required."
                ),
                path=["trigger", "triggerNodeId"],
            )
        else:
            return [], WorkflowCompileError(
                code="workflow_trigger_kind_mismatch",
                message=f'Workflow has no "{trigger_kind}" trigger entry.',
                path=["trigger", "kind"],
            )

    adjacency: dict[str, list[str]] = {node.id: [] for node in nodes}
    for node in nodes:
        for dependency_id in node.depends_on:
            adjacency.setdefault(dependency_id, []).append(node.id)

    active_ids = {selected.id}
    pending = [selected.id]
    while pending:
        current_id = pending.pop()
        for downstream_id in adjacency.get(current_id, []):
            if downstream_id not in active_ids:
                active_ids.add(downstream_id)
                pending.append(downstream_id)

    # External-runtime imports predate trigger-scoped execution and are
    # intentionally represented as governed OpenCLI nodes rather than opaque
    # executors. Their imported subgraph is not yet wired to the host trigger,
    # so keep explicitly marked externalWorkflow nodes runnable while ordinary
    # disconnected/orphan nodes remain excluded.
    active_ids.update(
        node.id for node in nodes if isinstance(node.params.get("externalWorkflow"), dict)
    )

    selected_nodes = [
        node.model_copy(
            update={
                "depends_on": [
                    dependency_id
                    for dependency_id in node.depends_on
                    if dependency_id in active_ids
                ]
            }
        )
        for node in nodes
        if node.id in active_ids
    ]
    return selected_nodes, None


def _runtime_trigger_kind(node: CompiledWorkflowNode) -> str | None:
    binding_id = _binding_id(node)
    if binding_id == WEBHOOK_TRIGGER_BINDING_ID:
        return "webhook"
    if binding_id != SCHEDULE_TRIGGER_BINDING_ID:
        return None

    builder = _read_dict(node.params.get("builder"))
    node_type = _read_string(builder.get("nodeType"))
    mode = _read_string(node.params.get("mode"))
    return "manual" if node_type == "manual-trigger" or mode == "manual" else "schedule"


def _is_image_generation_project_node(project: WorkflowProject, node_id: str) -> bool:
    return any(
        node.id == node_id and node.kind == "media" and node.capability == "generate"
        for node in project.nodes
    )


def _projection_node_status(
    projection: WorkflowRunProjection,
    node_id: str,
) -> WorkflowRunStatus | None:
    return next(
        (state.status for state in projection.nodeStates if state.nodeId == node_id),
        None,
    )


def _latest_waiting_details(
    events: list[WorkflowNodeRunEvent],
    node_id: str,
) -> dict[str, Any]:
    event = next(
        (
            event
            for event in reversed(events)
            if event.nodeId == node_id and event.eventType == "waiting"
        ),
        None,
    )
    return dict(event.details) if event is not None else {}


def _select_package_id(
    nodes: list[CompiledWorkflowNode],
    package_node_id: str | None,
) -> str | None:
    if package_node_id:
        return (
            package_node_id
            if any(node.id == package_node_id and node.package is not None for node in nodes)
            else None
        )

    package_ids = {
        str(node.runtime.get("package_parent_id"))
        for node in nodes
        if _is_opencli_internal_source(node, str(node.runtime.get("package_parent_id")))
    }
    package_ids.discard("")
    package_ids.discard("None")
    return sorted(package_ids)[0] if len(package_ids) == 1 else None


def _is_opencli_internal_source(node: CompiledWorkflowNode, package_node_id: str | None) -> bool:
    if not package_node_id or node.runtime.get("package_parent_id") != package_node_id:
        return False
    binding = node.runtime.get("binding")
    return isinstance(binding, dict) and binding.get("function_id") == OPENCLI_FUNCTION_ID


def _is_turbopush_publish_node(node: CompiledWorkflowNode) -> bool:
    binding = node.runtime.get("binding")
    return isinstance(binding, dict) and binding.get("binding_id") == TURBOPUSH_BINDING_ID


def _is_workflow_source_fetch_node(node: CompiledWorkflowNode) -> bool:
    return _binding_id(node) == SOURCE_FETCH_BINDING_ID


def _is_workflow_notify_node(node: CompiledWorkflowNode) -> bool:
    return _binding_id(node) == NOTIFY_SEND_BINDING_ID


def _is_webhook_notify_node(node: CompiledWorkflowNode) -> bool:
    return _binding_id(node) == WEBHOOK_NOTIFY_BINDING_ID


def _is_capability_native_node(node: CompiledWorkflowNode) -> bool:
    return _binding_id(node) in NATIVE_BINDING_IDS.values()


def _is_opentabs_tool_node(node: CompiledWorkflowNode) -> bool:
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    return (
        binding.get("binding_id") == EXTERNAL_TOOL_BINDING_ID
        and binding_input.get("executorMode") == OPENTABS_EXECUTOR_MODE
        and binding_input.get("toolCapabilityId") == OPENTABS_TOOL_CAPABILITY_ID
    )


def _is_bbx_tool_node(node: CompiledWorkflowNode) -> bool:
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    return (
        binding.get("binding_id") == EXTERNAL_TOOL_BINDING_ID
        and binding_input.get("executorMode") == BBX_EXECUTOR_MODE
        and binding_input.get("toolCapabilityId") == BBX_TOOL_CAPABILITY_ID
    )


def _external_tool_runtime_label(node: CompiledWorkflowNode) -> str:
    if _is_opentabs_tool_node(node):
        return "OpenTabs"
    if _is_bbx_tool_node(node):
        return "BBX"
    return "OpenCLI Tool Capability"


def _opentabs_tool_block_reason(
    node: CompiledWorkflowNode,
    permissions: object,
) -> WorkflowRunBlockReason | None:
    if not _is_opentabs_tool_node(node):
        return None
    binding_input = _read_dict(_read_dict(node.runtime.get("binding")).get("input"))
    executor_params = _read_dict(binding_input.get("executorParams"))
    read_only = executor_params.get("readOnly") is True
    if read_only:
        if bool(getattr(permissions, "canFetchNetwork", False)):
            return None
        return WorkflowRunBlockReason(
            code=FETCH_PERMISSION_REQUIRED,
            message=(
                "OpenTabs read tool is bound, but "
                "agentPermissions.canFetchNetwork is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": EXTERNAL_TOOL_BINDING_ID,
                "requiredPermission": "canFetchNetwork",
            },
        )

    proposal_state = _read_string(node.runtime.get("proposal_state"))
    if proposal_state != "accepted":
        return WorkflowRunBlockReason(
            code=OPENCLI_WRITE_APPROVAL_REQUIRED,
            message="OpenTabs write tool must be explicitly accepted before it can run.",
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": EXTERNAL_TOOL_BINDING_ID,
                "proposalState": proposal_state or "proposed",
            },
        )
    if not bool(getattr(permissions, "canMutateExternalSites", False)):
        return WorkflowRunBlockReason(
            code=OPENCLI_WRITE_PERMISSION_REQUIRED,
            message=(
                "OpenTabs write tool is accepted, but "
                "agentPermissions.canMutateExternalSites is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": EXTERNAL_TOOL_BINDING_ID,
                "requiredPermission": "canMutateExternalSites",
            },
        )
    return None


def _bbx_tool_block_reason(
    node: CompiledWorkflowNode,
    permissions: object,
) -> WorkflowRunBlockReason | None:
    if not _is_bbx_tool_node(node):
        return None
    binding_input = _read_dict(_read_dict(node.runtime.get("binding")).get("input"))
    executor_params = _read_dict(binding_input.get("executorParams"))
    read_only = executor_params.get("readOnly") is True
    if read_only:
        if bool(getattr(permissions, "canFetchNetwork", False)):
            return None
        return WorkflowRunBlockReason(
            code=FETCH_PERMISSION_REQUIRED,
            message=(
                "BBX read tool is bound, but "
                "agentPermissions.canFetchNetwork is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": EXTERNAL_TOOL_BINDING_ID,
                "requiredPermission": "canFetchNetwork",
            },
        )

    proposal_state = _read_string(node.runtime.get("proposal_state"))
    if proposal_state != "accepted":
        return WorkflowRunBlockReason(
            code=OPENCLI_WRITE_APPROVAL_REQUIRED,
            message="BBX write tool must be explicitly accepted before it can run.",
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": EXTERNAL_TOOL_BINDING_ID,
                "proposalState": proposal_state or "proposed",
            },
        )
    if not bool(getattr(permissions, "canMutateExternalSites", False)):
        return WorkflowRunBlockReason(
            code=OPENCLI_WRITE_PERMISSION_REQUIRED,
            message=(
                "BBX write tool is accepted, but "
                "agentPermissions.canMutateExternalSites is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": EXTERNAL_TOOL_BINDING_ID,
                "requiredPermission": "canMutateExternalSites",
            },
        )
    return None


def _is_first_loop_native_node(node: CompiledWorkflowNode) -> bool:
    binding = node.runtime.get("binding")
    if not isinstance(binding, dict):
        return False
    return _is_external_tool_node(node) or binding.get("binding_id") in {
        NORMALIZE_BINDING_ID,
        *_DATA_OPERATOR_BINDING_IDS,
        DEDUPE_BINDING_ID,
        MERGE_BINDING_ID,
        ROUTER_ROUTE_BINDING_ID,
        RECORD_ACCEPTANCE_BINDING_ID,
        RECORD_SINK_BINDING_ID,
        COLLECTION_OUTPUT_BINDING_ID,
        INBOX_STORE_BINDING_ID,
        NOTIFY_SEND_BINDING_ID,
        WEBHOOK_NOTIFY_BINDING_ID,
    }


def _fixture_source_items(node: CompiledWorkflowNode) -> list[dict[str, Any]]:
    raw_items = _read_dict_list(
        node.params.get("fixtureItems", node.params.get("sampleItems", node.params.get("items")))
    )
    if not raw_items:
        return []
    source_group = _source_group(node, node.id)
    return [
        {
            "raw": item,
            "lineage": [
                {
                    "nodeId": node.id,
                    "sourceGroup": source_group,
                    "artifact": "fixtureItems",
                    "index": index,
                }
            ],
        }
        for index, item in enumerate(raw_items)
    ]


def _request_source_items(
    node: CompiledWorkflowNode,
    source_outputs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    raw_items = _read_dict_list(source_outputs.get(node.id))
    if not raw_items:
        return []
    source_group = _source_group(node, node.id)
    return [
        {
            "raw": item,
            "lineage": [
                {
                    "nodeId": node.id,
                    "sourceGroup": source_group,
                    "artifact": "sourceOutputs",
                    "index": index,
                }
            ],
        }
        for index, item in enumerate(raw_items)
    ]


def _live_source_items(
    node: CompiledWorkflowNode,
    raw_items: list[dict[str, Any]],
    *,
    artifact: str = "live_http_source",
) -> list[dict[str, Any]]:
    source_group = _source_group(node, node.id)
    return [
        {
            "raw": item,
            "lineage": [
                {
                    "nodeId": node.id,
                    "sourceGroup": source_group,
                    "artifact": artifact,
                    "index": index,
                }
            ],
        }
        for index, item in enumerate(raw_items)
    ]


def _opencli_dispatch_source_items(
    node: CompiledWorkflowNode,
    dispatch: WorkflowOpenCLIHDATraceDispatch,
    raw_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "raw": item,
            "lineage": [
                {
                    "nodeId": node.id,
                    "sourceGroup": dispatch.sourceGroup,
                    "artifact": "opencliDispatch",
                    "adapterTaskId": dispatch.taskId,
                    "index": index,
                }
            ],
        }
        for index, item in enumerate(raw_items)
    ]


def _bounded_opencli_dispatch_result(
    raw_items: list[dict[str, Any]],
    details: dict[str, object] | None,
    *,
    max_items: int,
) -> tuple[list[dict[str, Any]], dict[str, object] | None]:
    """Enforce the workflow-level item cap when an adapter cannot push it down."""

    limit = max(1, max_items)
    received_count = len(raw_items)
    items = raw_items[:limit]
    if details is None or received_count <= limit:
        return items, details
    return items, {
        **details,
        "itemCount": len(items),
        "receivedItemCount": received_count,
        "maxItemsPerRun": limit,
        "truncated": True,
    }


def _is_local_opencli_dispatch(details: dict[str, object] | None) -> bool:
    return bool(details and details.get("protocol") == "local")


async def _bound_source_record_items(
    node: CompiledWorkflowNode,
    *,
    session: AsyncSession | None,
) -> list[dict[str, Any]]:
    if session is None:
        return []
    task_id = _bound_task_id(node)
    if not task_id:
        return []
    result = await session.execute(
        select(CollectedRecord)
        .where(CollectedRecord.task_id == task_id)
        .order_by(CollectedRecord.created_at, CollectedRecord.id)
    )
    records = result.scalars().all()
    source_group = _source_group(node, node.id)
    return [
        {
            "raw": dict(record.raw_data or {}),
            "normalizedData": dict(record.normalized_data or {}),
            "contentHash": record.content_hash,
            "recordId": record.id,
            "lineage": [
                {
                    "nodeId": node.id,
                    "sourceGroup": source_group,
                    "artifact": "collected_records",
                    "recordId": record.id,
                    "taskId": record.task_id,
                    "sourceId": record.source_id,
                    "index": index,
                }
            ],
        }
        for index, record in enumerate(records)
    ]


def _bound_task_id(node: CompiledWorkflowNode) -> str | None:
    return (
        _read_string(node.params.get("taskId"))
        or _read_string(node.params.get("collectionTaskId"))
        or _read_string(node.params.get("boundTaskId"))
    )


def _bound_source_id_from_items(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        for entry in _read_dict_list(item.get("lineage")):
            source_id = _read_string(entry.get("sourceId"))
            if source_id:
                return source_id
    return None


def _bind_research_evidence_refs(
    items: list[dict[str, Any]],
    *,
    workflow_id: str,
    run_id: str,
    runtime_nodes_by_id: dict[str, CompiledWorkflowNode] | None,
) -> list[dict[str, Any]]:
    if not runtime_nodes_by_id:
        return items
    bound: list[dict[str, Any]] = []
    for item in items:
        lineage = _read_dict_list(item.get("lineage"))
        upstream_node = next(
            (
                candidate
                for entry in reversed(lineage)
                if (node_id := _read_string(entry.get("nodeId")))
                if (candidate := runtime_nodes_by_id.get(node_id)) is not None
                if _binding_id(candidate) == NORMALIZE_BINDING_ID
            ),
            None,
        )
        item_key = _read_string(item.get("candidateId")) or _read_string(item.get("contentHash"))
        if upstream_node is None or item_key is None:
            bound.append(item)
            continue
        batch = _node_batch_reference(
            workflow_id,
            run_id,
            upstream_node,
            item_count=len(items),
        )
        normalized = dict(_read_dict(item.get("normalizedData")))
        evidence_id = _read_string(normalized.get("evidenceId")) or item_key
        reference: dict[str, Any] = {
            "evidenceId": evidence_id,
            "itemKey": item_key,
            "batchId": batch.batchId,
            "runId": run_id,
            "nodeId": upstream_node.id,
            "manifestUri": batch.manifestUri,
            "odpRef": batch.odpRef,
        }
        source_id = _bound_source_id_from_items([item])
        if source_id:
            reference["sourceId"] = source_id
        normalized["evidenceRef"] = reference
        updated = dict(item)
        updated["normalizedData"] = normalized
        bound.append(updated)
    return bound


async def _execute_native_node(
    node: CompiledWorkflowNode,
    outputs_by_node: dict[str, list[dict[str, Any]]],
    run_id: str,
    *,
    workflow_id: str,
    trace_id: str,
    session: AsyncSession | None = None,
    runtime_nodes_by_id: dict[str, CompiledWorkflowNode] | None = None,
    materialized_source_tasks: dict[str, tuple[str, str]] | None = None,
) -> tuple[dict[str, object], list[dict[str, Any]]]:
    binding_id = _binding_id(node)
    input_items = _upstream_outputs(node, outputs_by_node)
    if binding_id == COLLECTION_OUTPUT_BINDING_ID:
        exposed = [
            _append_lineage(item, node, step="collection_output", run_id=run_id)
            for item in input_items
        ]
        return (
            {
                "bindingId": binding_id,
                "artifact": "items[]",
                "inputItemCount": len(input_items),
                "outputItemCount": len(exposed),
                "lineage": _lineage_pointer(node),
            },
            exposed,
        )
    if binding_id == NORMALIZE_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        normalize_config = dict(binding_input)
        if source_id := _read_string(node.params.get("sourceId")):
            normalize_config["sourceId"] = source_id
        result = execute_record_hygiene(
            "normalize",
            input_items,
            normalize_config,
            {"runId": run_id, "nodeId": node.id},
        )
        candidates = result.records
        return (
            {
                "bindingId": binding_id,
                "inputPort": "items[]",
                "outputPort": "recordCandidate[]",
                "inputItemCount": len(input_items),
                "recordCandidateCount": len(candidates),
                "rejectedCount": len(result.rejected),
                "rejected": result.rejected,
                "metrics": result.metrics,
                "lineage": _lineage_pointer(node),
            },
            candidates,
        )
    if binding_id in _DATA_OPERATOR_BINDING_IDS:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        operator_id = _read_string(binding_input.get("operatorId"))
        if operator_id is None:
            raise ValueError(f"Data operator binding {binding_id} is missing operatorId")
        pack_version = (
            _read_string(binding_input.get("packVersion"))
            or _LEGACY_DATA_OPERATOR_PACK_VERSION
        )
        config = binding_input.get("config")
        if not isinstance(config, dict):
            raise ValueError("Data operator config must be an object")
        if operator_id == "research.claim-project":
            input_items = _bind_research_evidence_refs(
                input_items,
                workflow_id=workflow_id,
                run_id=run_id,
                runtime_nodes_by_id=runtime_nodes_by_id,
            )
        result = execute_data_operator(
            operator_id,
            input_items,
            config,
            pack_version=pack_version,
        )
        output_items = [
            _append_data_operator_lineage(
                item,
                node,
                operator_id=result.operator_id,
                run_id=run_id,
            )
            for item in result.items
        ]
        result_details = result.to_details()
        rejected_candidate_ids = list(result.rejected_candidate_ids)
        return (
            {
                **result_details,
                "packVersion": result.pack_version,
                "bindingId": binding_id,
                "inputPort": binding_input.get("inputPort", "recordCandidate[]"),
                "outputPort": binding_input.get("outputPort", "recordCandidate[]"),
                "inputItemCount": len(input_items),
                "outputItemCount": len(output_items),
                "rejectedCount": result.metrics.get(
                    "rejectedCount", len(rejected_candidate_ids)
                ),
                "rejectedCandidateIds": rejected_candidate_ids[:100],
                "rejectedCandidateIdsTruncated": len(rejected_candidate_ids) > 100,
                "metrics": result.metrics,
                "lineage": _lineage_pointer(node),
            },
            output_items,
        )
    if binding_id == DEDUPE_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        result = execute_record_hygiene(
            "dedupe",
            input_items,
            binding_input,
            {"runId": run_id, "nodeId": node.id},
        )
        return (
            {
                "bindingId": binding_id,
                "key": binding_input.get("key", "title+source+publishedAt"),
                "window": binding_input.get("window", "24h"),
                "windowHours": binding_input.get("windowHours", 24),
                "inputCandidateCount": len(input_items),
                "deduplicatedCandidateCount": len(result.records),
                "rejectedCount": len(result.rejected),
                "rejected": result.rejected,
                "metrics": result.metrics,
                "lineage": _lineage_pointer(node),
            },
            result.records,
        )
    if binding_id == MERGE_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        merged = [_append_lineage(item, node, step="merge", run_id=run_id) for item in input_items]
        return (
            {
                "bindingId": binding_id,
                "strategy": binding_input.get("strategy", "concat"),
                "inputType": binding_input.get("inputType", "recordCandidate[]"),
                "outputType": binding_input.get("outputType", "recordCandidate[]"),
                "preserveLineage": binding_input.get("preserveLineage", True),
                "inputCandidateCount": len(input_items),
                "mergedCandidateCount": len(merged),
                "lineage": _lineage_pointer(node),
            },
            merged,
        )
    if binding_id == ROUTER_ROUTE_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        expression = _read_string(binding_input.get("expression")) or "true"
        routed = _route_runtime_items(node, input_items, run_id, expression=expression)
        return (
            {
                "bindingId": binding_id,
                "expression": expression,
                "inputType": binding_input.get("inputPort", "recordCandidate[]"),
                "outputType": binding_input.get("outputPort", "recordCandidate[]"),
                "inputCandidateCount": len(input_items),
                "routedCandidateCount": len(routed),
                "lineage": _lineage_pointer(node),
            },
            routed,
        )
    if binding_id == RECORD_ACCEPTANCE_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        result = execute_record_hygiene(
            "accept",
            input_items,
            binding_input,
            {"runId": run_id, "nodeId": node.id},
        )
        accepted = result.records
        review_required = len(result.rejected)
        return (
            {
                "bindingId": binding_id,
                "schema": binding_input.get("schema", "record.v1"),
                "dedupe": binding_input.get("dedupe", "required"),
                "lineageRequired": binding_input.get("lineageRequired", True),
                "inputCandidateCount": len(input_items),
                "acceptedRecordCount": len(accepted),
                "reviewRequiredCount": review_required,
                "rejectedCount": len(result.rejected),
                "rejected": result.rejected,
                "metrics": result.metrics,
                "lineage": _lineage_pointer(node),
            },
            accepted,
        )
    if binding_id in {RECORD_SINK_BINDING_ID, INBOX_STORE_BINDING_ID}:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        target = (
            _read_string(binding_input.get("target"))
            or _read_string(binding_input.get("queue"))
            or "records"
        )
        stored_refs, skipped_count = await _store_record_sink_outputs(
            node,
            input_items,
            run_id=run_id,
            workflow_id=workflow_id,
            target=target,
            session=session,
            runtime_nodes_by_id=runtime_nodes_by_id or {},
            materialized_source_tasks=materialized_source_tasks or {},
        )
        stored_refs = [
            _append_lineage(item, node, step="store", run_id=run_id)
            for item in stored_refs
        ]
        return (
            {
                "bindingId": binding_id,
                "target": target,
                "writeMode": binding_input.get("writeMode", "append"),
                "inputRecordCount": len(input_items),
                "storedRecordCount": len(stored_refs),
                "skippedRecordCount": skipped_count,
                "storedRefs": stored_refs,
                "lineage": _lineage_pointer(node),
            },
            stored_refs,
        )
    if binding_id == NOTIFY_SEND_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        return (
            {
                "bindingId": binding_id,
                "notifierType": binding_input.get("notifier_type", "workflow"),
                "target": binding_input.get("target", "workflow"),
                "template": binding_input.get("template", "brief"),
                "deliveryConfigured": binding_input.get("delivery_configured", False),
                "inputItemCount": len(input_items),
                "lineage": _lineage_pointer(node),
            },
            input_items,
        )
    if binding_id == WEBHOOK_NOTIFY_BINDING_ID:
        binding = _read_dict(node.runtime.get("binding"))
        binding_input = _read_dict(binding.get("input"))
        delivery = await execute_workflow_webhook_delivery(
            binding_input,
            input_items,
            workflow_id=workflow_id,
            run_id=run_id,
            node_id=node.id,
        )
        return (
            {
                "bindingId": binding_id,
                **delivery,
                "lineage": _lineage_pointer(node),
            },
            input_items,
        )
    if _is_external_tool_node(node):
        binding_input = _binding_input(node)
        output_items = await _execute_external_tool_capability(
            node,
            input_items,
            run_id=run_id,
            workflow_id=workflow_id,
            trace_id=trace_id,
            session=session,
            binding_input=binding_input,
        )
        return (
            {
                **_external_tool_call_details(
                    node,
                    input_item_count=len(input_items),
                    output_item_count=len(output_items),
                ),
                "outputPort": binding_input.get("outputPort", "unknown"),
                "sampleOutputs": [_trace_sample_output(item) for item in output_items[:3]],
            },
            output_items,
        )
    return ({"bindingId": binding_id or "", "lineage": _lineage_pointer(node)}, [])


def _execute_capability_native_node(
    node: CompiledWorkflowNode,
    outputs_by_node: dict[str, list[dict[str, Any]]],
    *,
    workflow_input: dict[str, Any],
) -> tuple[dict[str, object], list[dict[str, Any]]]:
    binding_id = _binding_id(node)
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    node_type = _read_string(binding_input.get("nodeType"))
    if binding_id not in NATIVE_BINDING_IDS.values() or node_type is None:
        raise ValueError(f'Unsupported native binding "{binding_id}"')

    upstream_items = _upstream_outputs(node, outputs_by_node)
    native_input = _capability_native_input(
        node_type,
        upstream_items,
        workflow_input=workflow_input,
    )
    result = execute_native_node(
        f"primitive.core.{node_type}",
        native_input,
        {"config": _read_dict(binding_input.get("config"))},
    )
    output_items = _capability_native_output_items(result.output)
    details: dict[str, object] = {
        "bindingId": binding_id,
        "nodeType": result.node_type,
        "input": _json_safe(native_input),
        "output": _json_safe(result.output),
        "outputItemCount": len(output_items),
        "meta": _json_safe(result.meta),
    }
    if result.route is not None:
        details["route"] = result.route
    return details, output_items


def _capability_native_input(
    node_type: str,
    upstream_items: list[dict[str, Any]],
    *,
    workflow_input: dict[str, Any],
) -> object:
    if node_type in {"list-filter", "list-sort", "iteration"}:
        return upstream_items
    if len(upstream_items) == 1:
        return upstream_items[0]
    if upstream_items:
        return {"items": upstream_items}
    return workflow_input


def _capability_native_output_items(output: object) -> list[dict[str, Any]]:
    if isinstance(output, list):
        return [item if isinstance(item, dict) else {"value": item} for item in output]
    if isinstance(output, dict):
        return [output]
    return [{"value": output}]


async def _execute_external_tool_capability(
    node: CompiledWorkflowNode,
    input_items: list[dict[str, Any]],
    *,
    run_id: str,
    workflow_id: str,
    trace_id: str,
    session: AsyncSession | None,
    binding_input: dict[str, Any],
) -> list[dict[str, Any]]:
    if binding_input.get("executorMode") == NATIVE_INTELLIGENCE_EXECUTOR:
        tool_id = binding_input.get("toolCapabilityId")
        action = (
            NATIVE_INTELLIGENCE_ACTION_BY_TOOL_ID.get(tool_id)
            if isinstance(tool_id, str)
            else None
        )
        if action is None:
            raise ValueError("native_intelligence_action_not_registered")
        if session is None:
            raise ValueError("native_intelligence_store_unavailable")
        bind = session.bind
        if bind is None:
            raise ValueError("native_intelligence_store_unavailable")
        session_factory = async_sessionmaker(
            bind,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        async def execute_transaction(retry_store):
            return await execute_native_intelligence_action(
                action_name=action.name,
                input_items=input_items,
                params=_merged_tool_params(binding_input),
                session=retry_store.session,
                workflow_id=workflow_id,
                run_id=run_id,
                trace_id=trace_id,
                node_id=node.id,
                commit_each_command=False,
            )

        output = await run_intelligence_transaction(
            session_factory,
            execute_transaction,
        )
        return [_external_tool_output(node, output, input_items, run_id, 0, binding_input)]

    if (
        binding_input.get("executorMode") == BBX_EXECUTOR_MODE
        and binding_input.get("toolCapabilityId") == BBX_TOOL_CAPABILITY_ID
    ):
        executor_params = _read_dict(binding_input.get("executorParams"))
        tool_name = _read_string(executor_params.get("tool"))
        if not tool_name:
            raise BbxToolExecutionError(
                "BBX tool node is missing toolCapability.executor.params.tool"
            )
        result = await invoke_bbx_tool(
            tool_name,
            _read_dict(binding_input.get("toolParams")),
            task_id=f"{run_id}:{node.id}",
        )
        return [
            _external_tool_output(node, output, input_items, run_id, index, binding_input)
            for index, output in enumerate(bbx_result_items(result))
        ]

    if (
        binding_input.get("executorMode") == OPENTABS_EXECUTOR_MODE
        and binding_input.get("toolCapabilityId") == OPENTABS_TOOL_CAPABILITY_ID
    ):
        executor_params = _read_dict(binding_input.get("executorParams"))
        tool_name = _read_string(executor_params.get("tool"))
        if not tool_name:
            raise OpenTabsToolExecutionError(
                "OpenTabs tool node is missing toolCapability.executor.params.tool"
            )
        result = await invoke_opentabs_tool(
            tool_name,
            _read_dict(binding_input.get("toolParams")),
            task_id=f"{run_id}:{node.id}",
        )
        return [
            _external_tool_output(node, output, input_items, run_id, index, binding_input)
            for index, output in enumerate(opentabs_result_items(result))
        ]

    if (
        binding_input.get("executorMode") == OKX_MARKET_TICKER_SNAPSHOT_EXECUTOR
        and binding_input.get("toolCapabilityId") == "tool.realtime.stream.subscribe"
    ):
        output = await _execute_okx_market_tool(binding_input)
        return [_external_tool_output(node, output, input_items, run_id, 0, binding_input)]

    if (
        binding_input.get("executorMode") == JOYAI_VL_INTERACTION_EXECUTOR
        and binding_input.get("toolCapabilityId") == JOYAI_VL_TOOL_CAPABILITY_ID
    ):
        output = _execute_joyai_vl_tool(binding_input)
        return [_external_tool_output(node, output, input_items, run_id, 0, binding_input)]

    if (
        binding_input.get("executorMode") == SITUATION_AWARENESS_EXECUTOR
        and binding_input.get("toolCapabilityId") == SITUATION_AWARENESS_TOOL_CAPABILITY_ID
    ):
        output = _execute_situation_awareness_tool(input_items, binding_input)
        return [_external_tool_output(node, output, input_items, run_id, 0, binding_input)]

    if (
        binding_input.get("executorMode") == SWARM_SIMULATION_EXECUTOR
        and binding_input.get("toolCapabilityId") == SWARM_SIMULATION_TOOL_CAPABILITY_ID
    ):
        output = _execute_swarm_simulation_tool(input_items, binding_input)
        return [_external_tool_output(node, output, input_items, run_id, 0, binding_input)]

    if (
        binding_input.get("executorMode") == KATS_EXECUTOR_MODE
        and binding_input.get("toolCapabilityId") in KATS_TOOL_IDS
    ):
        operation, params = _resolved_kats_params(binding_input)
        try:
            output = await execute_kats_operation(operation, input_items, params)
        except KatsRuntimeError as exc:
            output = {
                "schema": "kats.error.v1",
                "source": "facebookresearch/kats",
                "eventType": "kats.operation.error",
                "status": "error",
                "operation": operation,
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        return [_external_tool_output(node, output, input_items, run_id, 0, binding_input)]

    fixture_outputs = _read_dict_list(binding_input.get("fixtureOutputs"))
    fixture_output = _read_dict(binding_input.get("fixtureOutput"))
    if not fixture_outputs and fixture_output:
        fixture_outputs = [fixture_output]
    if not fixture_outputs:
        fixture_outputs = [{"inputItemCount": len(input_items)}]

    return [
        _external_tool_output(node, output, input_items, run_id, index, binding_input)
        for index, output in enumerate(fixture_outputs)
    ]


async def _execute_okx_market_tool(binding_input: dict[str, Any]) -> dict[str, Any]:
    params = _merged_tool_params(binding_input)
    try:
        # execute_okx_market_ticker_snapshot is a blocking urllib call; run
        # it off the event loop thread so it can't stall the single-worker
        # server for other in-flight requests.
        return await asyncio.to_thread(execute_okx_market_ticker_snapshot, params)
    except RealtimeMarketExecutionError as exc:
        return {
            "schema": "event.market.ticker.error.v1",
            "source": "okx",
            "eventType": "market.ticker.error",
            "status": "error",
            "message": str(exc),
        }


def _execute_joyai_vl_tool(binding_input: dict[str, Any]) -> dict[str, Any]:
    params = _merged_tool_params(binding_input)
    try:
        return execute_joyai_vl_interaction(params)
    except JoyAIVLExecutionError as exc:
        return {
            "schema": "event.vl.interaction.error.v1",
            "source": "joyai-vl",
            "eventType": "vl.interaction.error",
            "status": "error",
            "message": str(exc),
        }


def _execute_swarm_simulation_tool(
    input_items: list[dict[str, Any]],
    binding_input: dict[str, Any],
) -> dict[str, Any]:
    try:
        return execute_swarm_simulation(input_items, _merged_tool_params(binding_input))
    except SwarmSimulationExecutionError as exc:
        return {
            "schema": "swarm.provider-operation.error.v1",
            "source": "swarm-simulation",
            "eventType": "swarm.simulation.error",
            "status": "error",
            "simulated": True,
            "message": str(exc),
        }


def _execute_situation_awareness_tool(
    input_items: list[dict[str, Any]],
    binding_input: dict[str, Any],
) -> dict[str, Any]:
    try:
        return execute_situation_awareness(input_items, _merged_tool_params(binding_input))
    except Last30DaysProviderError as exc:
        return {
            "schema": "recent-research.provider.error.v1",
            "source": "situation-awareness",
            "eventType": "recent.research.error",
            "status": "error",
            "message": str(exc),
        }


def _merged_tool_params(binding_input: dict[str, Any]) -> dict[str, Any]:
    return {
        **_read_dict(binding_input.get("executorParams")),
        **_read_dict(binding_input.get("toolParams")),
    }


def _resolved_kats_params(
    binding_input: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    params = _merged_tool_params(binding_input)
    operation = _read_string(
        _read_dict(binding_input.get("executorParams")).get("operation")
    )
    params["operation"] = operation
    return operation, params


def _external_tool_output(
    node: CompiledWorkflowNode,
    output: dict[str, Any],
    input_items: list[dict[str, Any]],
    run_id: str,
    index: int,
    binding_input: dict[str, Any],
) -> dict[str, Any]:
    return {
        "raw": output,
        "normalizedData": output,
        "lineage": [
            *[lineage for item in input_items for lineage in _read_dict_list(item.get("lineage"))],
            {
                "nodeId": node.id,
                "step": "external_tool_capability",
                "runId": run_id,
                "toolCapabilityId": binding_input.get("toolCapabilityId"),
                "index": index,
            },
        ],
    }


def _trace_sample_output(item: dict[str, Any]) -> dict[str, Any]:
    raw = _read_dict(item.get("raw"))
    if not raw:
        return {}
    return {
        key: raw[key]
        for key in (
            "schema",
            "source",
            "channel",
            "instId",
            "eventType",
            "eventTime",
            "latencyMs",
            "market",
            "status",
            "message",
            "query",
            "counts",
            "window",
            "provider",
            "simulated",
            "run",
            "outcomes",
            "canonicalState",
            "handles",
            "action",
            "sessionId",
            "intelligenceSessionRef",
            "state",
            "version",
            "result",
            "readiness",
            "provenance",
            "id",
            "title",
            "name",
            "url",
            "ok",
            "text",
            "value",
            "truncated",
            "omitted",
            "length",
        )
        if key in raw
    }


def _tool_call_trace_details(details: dict[str, object]) -> dict[str, object]:
    return {
        "bindingId": details.get("bindingId"),
        "toolCapabilityId": details.get("toolCapabilityId"),
        "executorMode": details.get("executorMode"),
        "inputItemCount": details.get("inputItemCount"),
        "outputItemCount": details.get("outputItemCount"),
        "externalWorkflow": details.get("externalWorkflow", {}),
        "lineage": details.get("lineage", {}),
    }


def _binding_input(node: CompiledWorkflowNode) -> dict[str, Any]:
    binding = _read_dict(node.runtime.get("binding"))
    return _read_dict(binding.get("input"))


def _external_tool_call_details(
    node: CompiledWorkflowNode,
    *,
    input_item_count: int,
    output_item_count: int,
) -> dict[str, object]:
    binding_input = _binding_input(node)
    return {
        "bindingId": _binding_id(node),
        "toolCapabilityId": binding_input.get("toolCapabilityId"),
        "executorMode": binding_input.get("executorMode"),
        "inputItemCount": input_item_count,
        "outputItemCount": output_item_count,
        "externalWorkflow": binding_input.get("externalWorkflow", {}),
        "lineage": _lineage_pointer(node),
    }


def _is_native_intelligence_node(node: CompiledWorkflowNode) -> bool:
    return _binding_input(node).get("executorMode") == NATIVE_INTELLIGENCE_EXECUTOR


def _is_external_tool_node(node: CompiledWorkflowNode) -> bool:
    binding_input = _binding_input(node)
    return (
        _binding_id(node) == EXTERNAL_TOOL_BINDING_ID
        or binding_input.get("transportBindingId") == EXTERNAL_TOOL_BINDING_ID
    )


async def _store_record_sink_outputs(
    node: CompiledWorkflowNode,
    input_items: list[dict[str, Any]],
    *,
    run_id: str,
    workflow_id: str,
    target: str,
    session: AsyncSession | None,
    runtime_nodes_by_id: dict[str, CompiledWorkflowNode],
    materialized_source_tasks: dict[str, tuple[str, str]],
) -> tuple[list[dict[str, Any]], int]:
    if session is None:
        return (
            [
                {
                    "recordId": _read_string(item.get("recordId"))
                    or _stable_id("record", run_id, node.id, str(index)),
                    "target": target,
                    "lineage": item.get("lineage", []),
                }
                for index, item in enumerate(input_items)
            ],
            0,
        )

    triples_by_source_node: dict[str, list[tuple[dict, dict, str, list[dict[str, Any]]]]] = {}
    for item in input_items:
        source_node_id = _origin_source_node_id(item, runtime_nodes_by_id)
        if not source_node_id:
            continue
        source_id, _task_id = await _materialize_source_task(
            session,
            runtime_nodes_by_id[source_node_id],
            run_id=run_id,
            workflow_id=workflow_id,
            sink_node_id=node.id,
            cache=materialized_source_tasks,
        )
        raw = dict(_read_dict(item.get("raw")))
        lineage = _read_dict_list(item.get("lineage"))
        raw["_workflowLineage"] = lineage
        raw["_workflowRunId"] = run_id
        raw["_workflowSinkNodeId"] = node.id
        normalized, content_hash = normalize_item(raw, source_id)
        accepted_normalized = _read_dict(item.get("normalizedData"))
        normalized.update(
            {key: value for key, value in accepted_normalized.items() if key not in {"source_id"}}
        )
        normalized["source_id"] = source_id
        content_hash = _read_string(item.get("contentHash")) or content_hash
        triples_by_source_node.setdefault(source_node_id, []).append(
            (raw, normalized, content_hash, lineage)
        )

    stored_refs: list[dict[str, Any]] = []
    skipped_total = 0
    for source_node_id, triples_with_lineage in triples_by_source_node.items():
        source_id, task_id = await _materialize_source_task(
            session,
            runtime_nodes_by_id[source_node_id],
            run_id=run_id,
            workflow_id=workflow_id,
            sink_node_id=node.id,
            cache=materialized_source_tasks,
        )
        records, skipped = await store_records(
            session,
            task_id,
            source_id,
            [
                (raw, normalized, content_hash)
                for raw, normalized, content_hash, _lineage in triples_with_lineage
            ],
            channel_type=_workflow_source_channel_type(runtime_nodes_by_id[source_node_id]),
            forward_to_odp=False,
            workflow_id=workflow_id,
            workflow_run_id=run_id,
        )
        skipped_total += skipped
        for record, (raw, normalized, content_hash, lineage) in zip(
            records, triples_with_lineage, strict=False
        ):
            stored_refs.append(
                {
                    "recordId": record.id,
                    "target": target,
                    "sourceId": source_id,
                    "taskId": task_id,
                    "raw": raw,
                    "normalizedData": normalized,
                    "contentHash": content_hash,
                    "lineage": lineage,
                }
            )

    await session.flush()
    return stored_refs, skipped_total


async def _materialize_source_task(
    session: AsyncSession,
    source_node: CompiledWorkflowNode,
    *,
    run_id: str,
    workflow_id: str,
    sink_node_id: str,
    cache: dict[str, tuple[str, str]],
) -> tuple[str, str]:
    cached = cache.get(source_node.id)
    if cached:
        return cached

    task_id = _read_string(source_node.params.get("taskId")) or _read_string(
        source_node.params.get("collectionTaskId")
    )
    if task_id:
        task = await session.get(CollectionTask, task_id)
        if task is not None:
            cache[source_node.id] = (task.source_id, task.id)
            return task.source_id, task.id

    source_id = _read_string(source_node.params.get("sourceId")) or _read_string(
        source_node.params.get("dataSourceId")
    )
    source = await session.get(DataSource, source_id) if source_id else None
    source_name = _workflow_source_display_name(source_node)
    source_description = _workflow_source_description(source_name)
    source_key = _workflow_source_key(source_node)
    source_config = _workflow_source_config(
        source_node,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    if source is None and not source_id:
        source = await _find_materialized_workflow_source(
            session,
            workflow_id=workflow_id,
            source_node_id=source_key,
            channel_type=_workflow_source_channel_type(source_node),
        )
    if source is None:
        source = DataSource(
            name=source_name,
            description=source_description,
            channel_type=_workflow_source_channel_type(source_node),
            channel_config=source_config,
            enabled=True,
            tags=["workflow", "record-sink", "scanned-source"],
        )
        session.add(source)
        await session.flush()
    elif not source_id:
        source.name = source_name
        source.description = source_description
        source.channel_config = source_config

    task = CollectionTask(
        source_id=source.id,
        trigger_type="workflow",
        parameters={
            "workflowId": workflow_id,
            "workflowRunId": run_id,
            "sourceNodeId": source_node.id,
            "sinkNodeId": sink_node_id,
        },
        status="completed",
    )
    session.add(task)
    await session.flush()
    cache[source_node.id] = (source.id, task.id)
    return source.id, task.id


async def _find_materialized_workflow_source(
    session: AsyncSession,
    *,
    workflow_id: str,
    source_node_id: str,
    channel_type: str,
) -> DataSource | None:
    candidates = (
        await session.scalars(
            select(DataSource).where(DataSource.channel_type == channel_type)
        )
    ).all()
    for candidate in candidates:
        config = candidate.channel_config if isinstance(candidate.channel_config, dict) else {}
        if config.get("workflowId") == workflow_id and config.get("sourceNodeId") == source_node_id:
            return candidate
    return None


def _workflow_source_display_name(node: CompiledWorkflowNode) -> str:
    label = _read_string(node.runtime.get("display_name")) or _read_string(
        node.params.get("displayName")
    )
    if not label:
        source_group = _read_string(node.params.get("sourceGroup"))
        label = source_group.replace("-", " ").title() if source_group else "工作流数据源"
    return f"{label} · 工作流扫描数据源"


def _workflow_source_description(source_name: str) -> str:
    return f"由工作流扫描写入记录库：{source_name}。技术节点标识仅保留在运行配置中。"


def _workflow_source_config(
    node: CompiledWorkflowNode,
    *,
    workflow_id: str,
    run_id: str,
) -> dict[str, object]:
    source_key = _workflow_source_key(node)
    return {
        "workflowId": workflow_id,
        "workflowRunId": run_id,
        "sourceNodeId": source_key,
        **({"runtimeNodeId": node.id} if source_key != node.id else {}),
        "displayName": _workflow_source_display_name(node),
        "adapter": _adapter_reference(node),
        "params": _json_safe(node.params),
    }


def _workflow_source_key(node: CompiledWorkflowNode) -> str:
    return (
        _read_string(node.params.get("sourceKey"))
        or _read_string(node.params.get("source_key"))
        or node.id
    )


def _origin_source_node_id(
    item: dict[str, Any],
    runtime_nodes_by_id: dict[str, CompiledWorkflowNode],
) -> str | None:
    for entry in _read_dict_list(item.get("lineage")):
        node_id = _read_string(entry.get("nodeId"))
        if not node_id:
            continue
        node = runtime_nodes_by_id.get(node_id)
        if node and node.kind == "source":
            return node.id
    return None


def _workflow_source_channel_type(node: CompiledWorkflowNode) -> str:
    adapter = _read_string(node.adapter)
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    channel_type = _read_string(binding_input.get("channelType"))
    if channel_type:
        return channel_type
    channel = _read_string(binding.get("channel"))
    if channel and channel != "source":
        return channel
    if adapter and adapter.startswith("opencli"):
        return "opencli"
    site = _read_string(node.params.get("site"))
    return site or "workflow"


def _adapter_reference(node: CompiledWorkflowNode) -> str | None:
    adapter = _read_string(node.adapter)
    if adapter:
        return adapter
    if hasattr(node.adapter, "id"):
        return _read_string(getattr(node.adapter, "id"))
    if hasattr(node.adapter, "model_dump"):
        dumped = node.adapter.model_dump(mode="json")
        if isinstance(dumped, dict):
            return _read_string(dumped.get("id"))
    return None


def _json_safe(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _upstream_outputs(
    node: CompiledWorkflowNode,
    outputs_by_node: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        item for upstream_id in node.depends_on for item in outputs_by_node.get(upstream_id, [])
    ]


def _append_lineage(
    item: dict[str, Any],
    node: CompiledWorkflowNode,
    *,
    step: str,
    run_id: str,
) -> dict[str, Any]:
    updated = dict(item)
    lineage = list(_read_dict_list(updated.get("lineage")))
    lineage.append({"nodeId": node.id, "step": step, "runId": run_id})
    updated["lineage"] = lineage
    return updated


def _append_data_operator_lineage(
    item: dict[str, Any],
    node: CompiledWorkflowNode,
    *,
    operator_id: str,
    run_id: str,
) -> dict[str, Any]:
    updated = _append_lineage(item, node, step=operator_id, run_id=run_id)
    if operator_id == "text.deduplicate":
        updated["dedupe"] = {
            "type": "dedupe",
            "operatorId": operator_id,
            "status": "unique",
        }
    return updated


def _route_runtime_items(
    node: CompiledWorkflowNode,
    input_items: list[dict[str, Any]],
    run_id: str,
    *,
    expression: str,
) -> list[dict[str, Any]]:
    return [
        _append_lineage(item, node, step="route", run_id=run_id)
        for item in input_items
        if _matches_route_expression(item, expression)
    ]


def _matches_route_expression(item: dict[str, Any], expression: str) -> bool:
    normalized_expression = expression.strip()
    if not normalized_expression or normalized_expression.lower() == "true":
        return True
    if normalized_expression.lower() == "false":
        return False

    or_terms = [term.strip() for term in normalized_expression.split("||")]
    if len(or_terms) > 1:
        return any(_matches_route_expression(item, term) for term in or_terms)

    and_terms = [term.strip() for term in normalized_expression.split("&&")]
    if len(and_terms) > 1:
        return all(_matches_route_expression(item, term) for term in and_terms)

    for operator in (">=", "<=", "===", "==", ">", "<"):
        if operator not in normalized_expression:
            continue
        left, right = [part.strip() for part in normalized_expression.split(operator, 1)]
        if not left.startswith("item."):
            return True
        value = _item_value(item, left.removeprefix("item."))
        expected = _parse_expression_literal(right)
        if operator in {"===", "=="}:
            return value == expected
        if not isinstance(value, int | float) or not isinstance(expected, int | float):
            return False
        if operator == ">=":
            return value >= expected
        if operator == "<=":
            return value <= expected
        if operator == ">":
            return value > expected
        if operator == "<":
            return value < expected

    if normalized_expression.startswith("item."):
        return bool(_item_value(item, normalized_expression.removeprefix("item.")))
    return True


def _parse_expression_literal(raw: str) -> object:
    value = raw.strip().strip('"').strip("'")
    if value == "true":
        return True
    if value == "false":
        return False
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _item_value(item: dict[str, Any], path: str) -> object:
    raw = _read_dict(item.get("raw"))
    normalized = _read_dict(item.get("normalizedData"))
    for source in (raw, normalized, item):
        value: object = source
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value is not None:
            return value
    return None


def _required_source_credential_key(binding_input: dict[str, Any]) -> str | None:
    config = _read_dict(binding_input.get("adapterConfig"))
    params = _read_dict(binding_input.get("params"))
    for source in (params, config, binding_input):
        for field in (
            "requiredCredentialKey",
            "requiredCredential",
            "credentialKey",
            "credentialName",
        ):
            value = _read_string(source.get(field))
            if value:
                return value

        required = source.get("requiresCredential")
        if isinstance(required, str) and required.strip():
            return required.strip()
        if required is True:
            return "default"
    return None


def _source_credential_configured(binding_input: dict[str, Any]) -> bool:
    config = _read_dict(binding_input.get("adapterConfig"))
    params = _read_dict(binding_input.get("params"))
    for source in (params, config, binding_input):
        if source.get("credentialConfigured") is True:
            return True
        for field in ("credentialRef", "credentialId", "authRef", "secretRef"):
            if _read_string(source.get(field)):
                return True
        auth = source.get("auth")
        if isinstance(auth, dict) and auth:
            return True
    return False


def _source_fetch_block_reason(
    node: CompiledWorkflowNode,
    permissions: object,
) -> WorkflowRunBlockReason:
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    if not bool(getattr(permissions, "canFetchNetwork", False)):
        return WorkflowRunBlockReason(
            code=FETCH_PERMISSION_REQUIRED,
            message=(
                "Workflow source fetch is bound, but agentPermissions.canFetchNetwork is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": SOURCE_FETCH_BINDING_ID,
                "requiredPermission": "canFetchNetwork",
            },
        )

    credential_key = _required_source_credential_key(binding_input)
    if credential_key and not _source_credential_configured(binding_input):
        return WorkflowRunBlockReason(
            code=MISSING_SOURCE_CREDENTIAL,
            message=(
                "Workflow source fetch is bound, but the required source "
                "credential is not configured."
            ),
            source="workflow_source_credentials",
            details={
                "nodeId": node.id,
                "bindingId": SOURCE_FETCH_BINDING_ID,
                "provider": binding_input.get("provider"),
                "channelType": binding_input.get("channelType"),
                "requiredCredentialKey": credential_key,
            },
        )

    live_mode = _read_string(binding_input.get("liveMode")) or "live"
    if live_mode in {"fixture", "mock"}:
        return WorkflowRunBlockReason(
            code=SOURCE_OUTPUT_REQUIRED,
            message=(
                "Fixture/mock source fetch requires sourceOutputs, fixtureItems, "
                "or bound source records before downstream nodes can run."
            ),
            source="workflow_source",
            details={
                "nodeId": node.id,
                "bindingId": SOURCE_FETCH_BINDING_ID,
                "liveMode": live_mode,
            },
        )

    return WorkflowRunBlockReason(
        code="live_source_executor_pending",
        message=(
            "Workflow source fetch is bound, but this source provider does not "
            "yet have a live executor in the workflow run service."
        ),
        source="workflow_source",
        details={
            "nodeId": node.id,
            "bindingId": SOURCE_FETCH_BINDING_ID,
            "provider": binding_input.get("provider"),
            "channelType": binding_input.get("channelType"),
        },
    )


def _notify_send_block_reason(
    node: CompiledWorkflowNode,
    permissions: object,
    *,
    outputs_by_node: dict[str, list[dict[str, Any]]] | None = None,
) -> WorkflowRunBlockReason | None:
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    binding_id = _binding_id(node) or NOTIFY_SEND_BINDING_ID
    if not bool(getattr(permissions, "canSendNotifications", False)):
        return WorkflowRunBlockReason(
            code=SEND_PERMISSION_REQUIRED,
            message=(
                "Workflow notification is bound, but "
                "agentPermissions.canSendNotifications is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": binding_id,
                "requiredPermission": "canSendNotifications",
            },
        )
    if not bool(binding_input.get("delivery_configured")):
        return WorkflowRunBlockReason(
            code=MISSING_DELIVERY_PROJECTION,
            message=(
                "Workflow notification is bound, but delivery requires a "
                "configured notifier target."
            ),
            source="workflow_notifier",
            details={
                "nodeId": node.id,
                "bindingId": binding_id,
                "required_params": ["webhook_url"],
            },
        )
    if binding_id == WEBHOOK_NOTIFY_BINDING_ID and not _upstream_outputs(
        node,
        outputs_by_node or {},
    ):
        return WorkflowRunBlockReason(
            code=MISSING_DELIVERY_PROJECTION,
            message=(
                "Webhook delivery is bound, but EvidenceBatch/resource projection is not available."
            ),
            source="workflow_webhook_delivery",
            details={
                "nodeId": node.id,
                "bindingId": WEBHOOK_NOTIFY_BINDING_ID,
                "required_params": [
                    "evidencebatch_projection_api",
                    "delivery_projection",
                ],
            },
        )
    return None


def _is_opencli_write_node(node: CompiledWorkflowNode) -> bool:
    return (
        _binding_id(node) == OPENCLI_BINDING_ID
        and (
            _read_string(node.params.get("opencliAccess")) == "write"
            or (node.kind == "action" and node.capability == "store")
        )
    )


def _opencli_mutation_block_reason(
    node: CompiledWorkflowNode,
    permissions: object,
) -> WorkflowRunBlockReason | None:
    if not _is_opencli_write_node(node):
        return None

    proposal_state = _read_string(node.runtime.get("proposal_state"))
    if proposal_state != "accepted":
        return WorkflowRunBlockReason(
            code=OPENCLI_WRITE_APPROVAL_REQUIRED,
            message="OpenCLI write action must be explicitly accepted before it can run.",
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": OPENCLI_BINDING_ID,
                "proposalState": proposal_state or "proposed",
            },
        )
    if not bool(getattr(permissions, "canMutateExternalSites", False)):
        return WorkflowRunBlockReason(
            code=OPENCLI_WRITE_PERMISSION_REQUIRED,
            message=(
                "OpenCLI write action is accepted, but "
                "agentPermissions.canMutateExternalSites is false."
            ),
            source="workflow_permissions",
            details={
                "nodeId": node.id,
                "bindingId": OPENCLI_BINDING_ID,
                "requiredPermission": "canMutateExternalSites",
            },
        )
    return None


def _native_node_started_message(node: CompiledWorkflowNode) -> str:
    binding_id = _binding_id(node)
    if binding_id in _DATA_OPERATOR_BINDING_IDS:
        return "Data operator started"
    if binding_id == NORMALIZE_BINDING_ID:
        return "Normalize transform started"
    if binding_id == DEDUPE_BINDING_ID:
        return "Dedupe transform started"
    if binding_id == MERGE_BINDING_ID:
        return "Merge node started"
    if binding_id == ROUTER_ROUTE_BINDING_ID:
        return "Router node started"
    if binding_id == RECORD_ACCEPTANCE_BINDING_ID:
        return "Record acceptance gate started"
    if binding_id == RECORD_SINK_BINDING_ID:
        return "Record sink started"
    if binding_id == INBOX_STORE_BINDING_ID:
        return "Inbox store started"
    if binding_id == NOTIFY_SEND_BINDING_ID:
        return "Notification send started"
    if binding_id == WEBHOOK_NOTIFY_BINDING_ID:
        return "Webhook delivery started"
    if _is_external_tool_node(node):
        return "OpenCLI Tool Capability started"
    return "Native workflow node started"


def _native_node_partial_message(node: CompiledWorkflowNode) -> str:
    binding_id = _binding_id(node)
    if binding_id in _DATA_OPERATOR_BINDING_IDS:
        return "Data operator emitted items"
    if binding_id == NORMALIZE_BINDING_ID:
        return "Record Candidates projected"
    if binding_id == DEDUPE_BINDING_ID:
        return "Duplicate candidates rejected with evidence"
    if binding_id == MERGE_BINDING_ID:
        return "Candidate streams merged with lineage"
    if binding_id == ROUTER_ROUTE_BINDING_ID:
        return "Candidates routed with lineage"
    if binding_id == RECORD_ACCEPTANCE_BINDING_ID:
        return "Record Candidates accepted as Records"
    if binding_id == RECORD_SINK_BINDING_ID:
        return "Accepted Records stored through Record Sink boundary"
    if binding_id == INBOX_STORE_BINDING_ID:
        return "Items stored through Inbox boundary"
    if binding_id == NOTIFY_SEND_BINDING_ID:
        return "Notification payload projected"
    if binding_id == WEBHOOK_NOTIFY_BINDING_ID:
        return "Webhook delivery evidence emitted"
    if _is_external_tool_node(node):
        return "OpenCLI Tool Capability emitted output"
    return "Native workflow node emitted trace evidence"


def _native_node_completed_message(node: CompiledWorkflowNode) -> str:
    binding_id = _binding_id(node)
    if binding_id in _DATA_OPERATOR_BINDING_IDS:
        return "Data operator completed"
    if binding_id == NORMALIZE_BINDING_ID:
        return "Normalize transform completed"
    if binding_id == DEDUPE_BINDING_ID:
        return "Dedupe transform completed"
    if binding_id == MERGE_BINDING_ID:
        return "Merge node completed"
    if binding_id == ROUTER_ROUTE_BINDING_ID:
        return "Router node completed"
    if binding_id == RECORD_ACCEPTANCE_BINDING_ID:
        return "Record acceptance gate completed"
    if binding_id == RECORD_SINK_BINDING_ID:
        return "Record sink completed"
    if binding_id == INBOX_STORE_BINDING_ID:
        return "Inbox store completed"
    if binding_id == NOTIFY_SEND_BINDING_ID:
        return "Notification send completed"
    if binding_id == WEBHOOK_NOTIFY_BINDING_ID:
        return "Webhook delivery completed"
    if _is_external_tool_node(node):
        return "OpenCLI Tool Capability completed"
    return "Native workflow node completed"


def _lineage_pointer(node: CompiledWorkflowNode) -> dict[str, object]:
    return {
        "nodeId": node.id,
        "nodePath": _compiled_node_path(node),
        "dependsOn": node.depends_on,
        "packageParentId": node.runtime.get("package_parent_id"),
        "packageInternalId": node.runtime.get("package_internal_id"),
    }


def _compiled_node_path(node: CompiledWorkflowNode) -> list[str]:
    value = node.runtime.get("node_path")
    if isinstance(value, list) and value and all(isinstance(part, str) and part for part in value):
        return list(value)
    return node.id.split(INTERNAL_ID_SEPARATOR)


def _legacy_location_from_node_path(node_path: list[str]) -> tuple[str | None, str | None]:
    if len(node_path) <= 1:
        return None, None
    return INTERNAL_ID_SEPARATOR.join(node_path[:-1]), node_path[-1]


def _package_ancestor_ids(node: CompiledWorkflowNode) -> list[str]:
    node_path = _compiled_node_path(node)
    return [INTERNAL_ID_SEPARATOR.join(node_path[:depth]) for depth in range(1, len(node_path))]


def _webhook_runtime_input_envelope(
    body: WorkflowRunStartRequest,
    node: CompiledWorkflowNode,
) -> dict[str, Any]:
    binding = _read_dict(node.runtime.get("binding"))
    binding_input = _read_dict(binding.get("input"))
    return {
        "workflowId": body.project.id,
        "trigger": {
            "kind": "webhook",
            "triggerNodeId": node.id,
            "requestId": body.trigger.requestId,
            "idempotencyKey": body.trigger.idempotencyKey,
        },
        "request": {
            "method": _read_string(binding_input.get("method")) or "POST",
            "path": _read_string(binding_input.get("path")) or "/hook",
            "payload": body.input.payload,
            "headers": body.input.headers,
            "query": body.input.query,
            "source": body.input.source,
            "sourceId": body.input.sourceId or body.input.source,
        },
        "responseMode": body.responseMode,
    }


def _binding_id(node: CompiledWorkflowNode) -> str | None:
    binding = node.runtime.get("binding")
    if not isinstance(binding, dict):
        return None
    return _read_string(binding.get("binding_id"))


def _to_dispatch(
    project: WorkflowProject,
    node: CompiledWorkflowNode,
    *,
    package_node_id: str | None,
    run_id: str,
    trace_id: str,
) -> WorkflowOpenCLIHDATraceDispatch:
    binding = node.runtime.get("binding")
    binding_input = binding.get("input") if isinstance(binding, dict) else {}
    site = _read_string(binding_input.get("site")) if isinstance(binding_input, dict) else None
    command = (
        _read_string(binding_input.get("command")) if isinstance(binding_input, dict) else None
    )
    if site is None or command is None:
        site = _read_string(node.params.get("site")) or ""
        command = _read_string(node.params.get("command")) or ""

    internal_node_id = _internal_node_id(node.id, package_node_id) if package_node_id else None
    source_group = _source_group(node, internal_node_id or node.id)
    args = _read_dict(node.params.get("args"))
    task_id = _task_id(project.id, run_id, node.id, source_group)
    payload: dict[str, object] = {
        "workflow_id": project.id,
        "workflow_run_id": run_id,
        "node_id": node.id,
        "node_path": _compiled_node_path(node),
        "source_group": source_group,
        "site": site,
        "command": command,
        "args": args,
        "format": _read_string(node.params.get("format")) or "json",
        "task_id": task_id,
        "trace_id": trace_id,
    }
    if package_node_id:
        payload["package_node_id"] = package_node_id
    if internal_node_id:
        payload["internal_node_id"] = internal_node_id
    positional_args = node.params.get("positional_args", node.params.get("positionalArgs"))
    if isinstance(positional_args, list) and positional_args:
        payload["positional_args"] = positional_args
    mode = _read_string(node.params.get("mode"))
    if mode:
        payload["mode"] = mode
    source_binding_id = _read_string(
        node.params.get("sourceBindingId", node.params.get("source_binding_id"))
    )
    source_binding_revision_id = _read_string(
        node.params.get(
            "sourceBindingRevisionId",
            node.params.get("source_binding_revision_id"),
        )
    )
    source_binding_revision_number = node.params.get(
        "sourceBindingRevisionNumber",
        node.params.get("source_binding_revision_number"),
    )
    if source_binding_id:
        payload["source_binding_id"] = source_binding_id
    if source_binding_revision_id:
        payload["source_binding_revision_id"] = source_binding_revision_id
    if (
        isinstance(source_binding_revision_number, int)
        and not isinstance(source_binding_revision_number, bool)
        and source_binding_revision_number >= 1
    ):
        payload["source_binding_revision_number"] = source_binding_revision_number
    dispatch_policy = _read_string(
        node.params.get("dispatchPolicy", node.params.get("dispatch_policy"))
    )
    if dispatch_policy:
        payload["dispatch_policy"] = dispatch_policy

    return WorkflowOpenCLIHDATraceDispatch(
        taskId=task_id,
        nodeId=node.id,
        nodePath=_compiled_node_path(node),
        packageNodeId=package_node_id,
        internalNodeId=internal_node_id,
        sourceGroup=source_group,
        site=site,
        command=command,
        args=args,
        sourceBindingId=source_binding_id,
        sourceBindingRevisionId=source_binding_revision_id,
        sourceBindingRevisionNumber=(
            source_binding_revision_number
            if (
                isinstance(source_binding_revision_number, int)
                and not isinstance(source_binding_revision_number, bool)
                and source_binding_revision_number >= 1
            )
            else None
        ),
        iii={"function_id": OPENCLI_FUNCTION_ID, "payload": payload},
    )


def _dispatch_metadata() -> dict[str, str]:
    return {
        "runtime": "iii",
        "worker": OPENCLI_WORKER,
        "functionId": OPENCLI_FUNCTION_ID,
        "mode": "trigger_envelope",
    }


def _internal_node_id(node_id: str, package_node_id: str) -> str:
    prefix = f"{package_node_id}{INTERNAL_ID_SEPARATOR}"
    return node_id.removeprefix(prefix)


def _optional_internal_node_id(node_id: str, package_node_id: str | None) -> str | None:
    if not package_node_id:
        return None
    prefix = f"{package_node_id}{INTERNAL_ID_SEPARATOR}"
    return node_id.removeprefix(prefix) if node_id.startswith(prefix) else None


def _source_group(node: CompiledWorkflowNode, internal_node_id: str) -> str:
    return (
        _read_string(node.params.get("sourceGroup"))
        or _read_string(node.params.get("source_group"))
        or (node.adapter.id if node.adapter else None)
        or internal_node_id
    )


def _task_id(workflow_id: str, run_id: str, node_id: str, source_group: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"opencli-admin/workflow/{workflow_id}/run/{run_id}/node/{node_id}/source/{source_group}",
        )
    )


def _read_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _read_dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _stable_id(prefix: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/{prefix}/{'/'.join(parts)}"))


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()
