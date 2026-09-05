"""WorkflowProject compile and runtime endpoints."""

import json
import uuid
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.dify_imports import get_dify_graphon_client
from backend.api.v1.workflow_run_access import reject_workspace_scoped_run
from backend.config import get_settings
from backend.database import get_db
from backend.image_studio.worker_runtime import dispatch_block_reason
from backend.models.image_studio import ImageGenerationJob, ImageGenerationJobStatus
from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import ApiResponse
from backend.schemas.workflow_evidence import (
    WorkflowEvidenceBatchDetail,
    WorkflowEvidenceBatchListResponse,
    WorkflowEvidenceProjection,
)
from backend.schemas.workflow_research import (
    WorkflowResearchContinuationRequest,
    WorkflowResearchContinuationResponse,
    WorkflowResearchLedgerResponse,
)
from backend.schemas.workflow_runtime import (
    WorkflowNodeRunEventType,
    WorkflowRunCheckpoint,
    WorkflowRunProjection,
    WorkflowRunTraceResponse,
)
from backend.services import image_studio_service
from backend.services.gaojixing_collection_service import (
    GaojixingCollectionConflictError,
    resume_collection,
)
from backend.services.plugin_registry_service import list_plugin_installations
from backend.workflow.bbx_tool_nodes import list_bbx_tool_nodes
from backend.workflow.capability_projection import build_workflow_capabilities
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.demand_assembler import draft_workflow_demand
from backend.workflow.dify_compile import compile_managed_dify_workflow_project
from backend.workflow.dify_graphon_client import DifyGraphonClient
from backend.workflow.evidence_projection import (
    build_evidence_projection,
    get_evidence_batch,
    list_evidence_batches,
    parse_projection_includes,
)
from backend.workflow.external_importer import import_external_workflow
from backend.workflow.fleet_inventory import (
    build_workflow_fleet_inventory,
    match_workflow_fleet_capability,
)
from backend.workflow.managed_gaojixing_question_batches import (
    MAX_QUESTION_BANK_BYTES,
    ManagedQuestionBatchConflictError,
    ManagedQuestionBatchError,
    UnsupportedQuestionBatchFormatError,
    accepts_managed_question_batch,
    cleanup_managed_question_batch,
    stage_managed_question_batch,
)
from backend.workflow.opencli_adapter_nodes import list_opencli_adapter_nodes
from backend.workflow.opencli_hda_tracer import (
    build_opencli_hda_trace,
    continue_workflow_run_with_source_outputs,
    get_workflow_run_checkpoint,
    get_workflow_run_projection,
    list_workflow_run_events,
    start_workflow_run,
)
from backend.workflow.opentabs_tool_nodes import list_opentabs_tool_nodes
from backend.workflow.patcher import preview_workflow_patch
from backend.workflow.research_continuation import (
    ResearchContinuationError,
    continue_research_workflow_run,
    get_research_ledger,
)
from backend.workflow.runtime_registry import WEBHOOK_TRIGGER_BINDING_ID
from backend.workflow.tool_capabilities import list_workflow_tool_capabilities

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/compile", response_model=ApiResponse[workflow_schemas.WorkflowCompileResponse])
async def compile_workflow(
    body: workflow_schemas.WorkflowCompileRequest,
    db: AsyncSession = Depends(get_db),
    graphon_client: DifyGraphonClient = Depends(get_dify_graphon_client),
) -> ApiResponse[workflow_schemas.WorkflowCompileResponse]:
    """Compile a Canvas-authored WorkflowProject into an executable preview.

    This endpoint is stateless: it validates and compiles in memory, but it
    does not create tasks, persist a plan, or dispatch workers.
    """

    return ApiResponse.ok(
        await compile_managed_dify_workflow_project(
            body.project,
            graphon_client=graphon_client,
            session=db,
        )
    )


@router.get(
    "/capabilities",
    response_model=ApiResponse[workflow_schemas.WorkflowCapabilitiesResponse],
)
async def get_workflow_capabilities(
    db: AsyncSession = Depends(get_db),
    graphon_client: DifyGraphonClient = Depends(get_dify_graphon_client),
) -> ApiResponse[workflow_schemas.WorkflowCapabilitiesResponse]:
    """Return Canvas-visible workflow capabilities and their runtime status."""

    installations = await list_plugin_installations(
        db,
        dify_runtime_ready=await graphon_client.is_healthy(),
    )
    return ApiResponse.ok(build_workflow_capabilities(installations))


@router.get(
    "/tool-capabilities",
    response_model=ApiResponse[workflow_schemas.WorkflowToolCapabilitiesResponse],
)
async def get_workflow_tool_capabilities() -> ApiResponse[
    workflow_schemas.WorkflowToolCapabilitiesResponse
]:
    """Return registered OpenCLI Admin Tool Capabilities."""

    return ApiResponse.ok(list_workflow_tool_capabilities())


@router.get(
    "/fleet/inventory",
    response_model=ApiResponse[workflow_schemas.WorkflowFleetInventoryResponse],
)
async def get_workflow_fleet_inventory(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[workflow_schemas.WorkflowFleetInventoryResponse]:
    """Project existing browser pool, agents, WS links, and site bindings."""

    return ApiResponse.ok(await build_workflow_fleet_inventory(db))


@router.post(
    "/fleet/match",
    response_model=ApiResponse[workflow_schemas.WorkflowFleetCapabilityMatchResponse],
)
async def match_workflow_fleet_target(
    body: workflow_schemas.WorkflowFleetCapabilityMatchRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[workflow_schemas.WorkflowFleetCapabilityMatchResponse]:
    """Match an OpenCLI adapter capability to an existing fleet endpoint."""

    return ApiResponse.ok(await match_workflow_fleet_capability(db, body))


@router.get(
    "/opencli-adapter-nodes",
    response_model=ApiResponse[workflow_schemas.WorkflowOpenCLIAdapterNodesResponse],
)
def get_opencli_adapter_nodes(
    site: str | None = None,
    q: str | None = None,
    include_write: bool = Query(True, alias="includeWrite"),
    access: Literal["read", "write"] | None = None,
    capability: Literal["fetch", "store"] | None = None,
    browser: bool | None = None,
    preset_kind: workflow_schemas.WorkflowOpenCLIAdapterPresetKind | None = Query(
        None, alias="presetKind"
    ),
    runtime_readiness: workflow_schemas.WorkflowOpenCLIAdapterReadiness | None = Query(
        None, alias="runtimeReadiness"
    ),
    limit: int = Query(2000, ge=1, le=5000),
    refresh: bool = False,
) -> ApiResponse[workflow_schemas.WorkflowOpenCLIAdapterNodesResponse]:
    """Return OpenCLI adapter commands projected as node-capability manifests."""

    return ApiResponse.ok(
        list_opencli_adapter_nodes(
            site=site,
            q=q,
            include_write=include_write,
            access=access,
            capability=capability,
            browser=browser,
            preset_kind=preset_kind,
            runtime_readiness=runtime_readiness,
            limit=limit,
            refresh=refresh,
        )
    )


@router.get(
    "/bbx-tool-nodes",
    response_model=ApiResponse[workflow_schemas.WorkflowBbxToolNodesResponse],
)
async def get_bbx_tool_nodes(
    group: str | None = None,
    q: str | None = None,
    include_write: bool = Query(True, alias="includeWrite"),
    limit: int = Query(2000, ge=1, le=5000),
) -> ApiResponse[workflow_schemas.WorkflowBbxToolNodesResponse]:
    """Return Browser Bridge (BBX) methods as callable Canvas nodes."""

    return ApiResponse.ok(
        await list_bbx_tool_nodes(
            group=group,
            q=q,
            include_write=include_write,
            limit=limit,
        )
    )


@router.get(
    "/opentabs-tool-nodes",
    response_model=ApiResponse[workflow_schemas.WorkflowOpenTabsToolNodesResponse],
)
async def get_opentabs_tool_nodes(
    plugin: str | None = None,
    q: str | None = None,
    include_write: bool = Query(True, alias="includeWrite"),
    limit: int = Query(2000, ge=1, le=5000),
) -> ApiResponse[workflow_schemas.WorkflowOpenTabsToolNodesResponse]:
    """Return live OpenTabs tools projected as callable Canvas nodes."""

    return ApiResponse.ok(
        await list_opentabs_tool_nodes(
            plugin=plugin,
            q=q,
            include_write=include_write,
            limit=limit,
        )
    )


@router.post(
    "/opencli-hda/trace",
    response_model=ApiResponse[workflow_schemas.WorkflowOpenCLIHDATraceResponse],
)
async def trace_opencli_hda(
    body: workflow_schemas.WorkflowOpenCLIHDATraceRequest,
) -> ApiResponse[workflow_schemas.WorkflowOpenCLIHDATraceResponse]:
    """Build III trigger envelopes for a Multi Source OpenCLI HDA workflow run."""

    return ApiResponse.ok(
        build_opencli_hda_trace(
            body.project,
            package_node_id=body.packageNodeId,
            run_id=body.runId,
            trace_id=body.traceId,
        )
    )


@router.post("/patch", response_model=ApiResponse[workflow_schemas.WorkflowPatchResponse])
async def patch_workflow(
    body: workflow_schemas.WorkflowPatchRequest,
) -> ApiResponse[workflow_schemas.WorkflowPatchResponse]:
    """Preview structured AI-authored WorkflowProject patch operations."""

    return ApiResponse.ok(preview_workflow_patch(body.project, body.operations))


@router.post(
    "/demand-draft",
    response_model=ApiResponse[workflow_schemas.WorkflowPatchResponse],
)
async def draft_demand_workflow(
    body: workflow_schemas.WorkflowDemandDraftRequest,
) -> ApiResponse[workflow_schemas.WorkflowPatchResponse]:
    """Assemble a user collection need into reviewable WorkflowProject patches."""

    return ApiResponse.ok(draft_workflow_demand(body))


@router.post(
    "/import/external-runtime",
    response_model=ApiResponse[workflow_schemas.WorkflowPatchResponse],
)
async def import_external_runtime_workflow(
    body: workflow_schemas.WorkflowExternalImportRequest,
) -> ApiResponse[workflow_schemas.WorkflowPatchResponse]:
    """Import supported external graphs as reviewable OpenCLI Admin native nodes."""

    try:
        return ApiResponse.ok(import_external_workflow(body))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/runs",
    response_model=ApiResponse[WorkflowRunProjection],
    status_code=202,
)
async def start_run(
    body: workflow_schemas.WorkflowRunStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    graphon_client: DifyGraphonClient = Depends(get_dify_graphon_client),
) -> ApiResponse[WorkflowRunProjection]:
    """Start a WorkflowProject run and emit replayable node-level events."""

    projection = await start_workflow_run(
        body,
        session=db,
        graphon_client=graphon_client,
        plugins=request.app.state.workflow_plugins,
    )
    await dispatch_materialized_image_jobs(db, projection.runId)
    return ApiResponse.ok(projection)


@router.post(
    "/runs/question-bank",
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
    status_code=202,
)
async def start_run_from_question_bank(
    question_bank: UploadFile = File(..., alias="questionBank"),
    request: str = Form(...),
    db: AsyncSession = Depends(get_db),
    graphon_client: DifyGraphonClient = Depends(get_dify_graphon_client),
) -> ApiResponse[workflow_schemas.WorkflowRunProjection]:
    """Start a draft Run from one server-managed Gaojixing question package."""

    try:
        run_request = workflow_schemas.WorkflowRunStartRequest.model_validate_json(request)
        if run_request.runId is not None:
            raise HTTPException(
                status_code=400,
                detail="Question bank uploads always create a new server-assigned Run",
            )
        if not accepts_managed_question_batch(run_request.project):
            raise HTTPException(
                status_code=422,
                detail="Question bank uploads require the governed Gaojixing workflow packages",
            )
        resolved_run_id = str(uuid.uuid4())
        payload = await question_bank.read(MAX_QUESTION_BANK_BYTES + 1)
        staged = stage_managed_question_batch(
            payload,
            filename=question_bank.filename or "",
            run_id=resolved_run_id,
        )
    except UnsupportedQuestionBatchFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ManagedQuestionBatchConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except (ManagedQuestionBatchError, ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await question_bank.close()

    try:
        projection = await start_workflow_run(
            run_request.model_copy(
                update={
                    "runId": resolved_run_id,
                    "sourceOutputs": {},
                    "input": workflow_schemas.WorkflowRunInput(
                        payload={"questionBatchRef": staged.question_batch_ref},
                        source="operator",
                    ),
                },
                deep=True,
            ),
            session=db,
            graphon_client=graphon_client,
        )
    except Exception:
        if staged.created:
            cleanup_managed_question_batch(
                staged.question_batch_ref,
                expected_run_id=resolved_run_id,
            )
        raise
    await dispatch_materialized_image_jobs(db, projection.runId)
    return ApiResponse.ok(projection)


@router.post(
    "/{workflow_id}/webhooks/{trigger_node_id}",
    response_model=ApiResponse[workflow_schemas.WorkflowWebhookIngressResponse],
    status_code=202,
)
async def start_run_from_webhook(
    workflow_id: str,
    trigger_node_id: str,
    body: workflow_schemas.WorkflowWebhookIngressRequest,
    request: Request,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    request_id_header: str | None = Header(default=None, alias="X-Request-ID"),
    source_id_header: str | None = Header(default=None, alias="X-Source-ID"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[workflow_schemas.WorkflowWebhookIngressResponse]:
    """Normalize an inbound webhook into the canonical workflow run input."""

    if workflow_id != body.workflowProject.id:
        raise _webhook_validation_error(
            "workflow_id_mismatch",
            "Path workflow id does not match workflowProject.id.",
            workflow_id=workflow_id,
            trigger_node_id=trigger_node_id,
        )

    compile_result = compile_workflow_project(body.workflowProject)
    if not compile_result.valid or compile_result.plan is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_workflow_project",
                "workflowId": workflow_id,
                "nodeId": trigger_node_id,
                "errors": [error.model_dump(mode="json") for error in compile_result.errors],
            },
        )

    trigger_node = next(
        (node for node in compile_result.plan.runtime.nodes if node.id == trigger_node_id),
        None,
    )
    if trigger_node is None:
        raise _webhook_validation_error(
            "workflow_trigger_not_found",
            "Webhook trigger node was not found in the compiled workflow.",
            workflow_id=workflow_id,
            trigger_node_id=trigger_node_id,
        )

    binding = trigger_node.runtime.get("binding")
    binding_id = binding.get("binding_id") if isinstance(binding, dict) else None
    if binding_id != WEBHOOK_TRIGGER_BINDING_ID:
        raise _webhook_validation_error(
            "unsupported_webhook_trigger",
            "The selected node does not implement the workflow webhook input contract.",
            workflow_id=workflow_id,
            trigger_node_id=trigger_node_id,
        )

    binding_input = binding.get("input") if isinstance(binding, dict) else None
    configured_method = (
        str(binding_input.get("method", "POST")).upper()
        if isinstance(binding_input, dict)
        else "POST"
    )
    if configured_method != "POST":
        raise _webhook_validation_error(
            "unsupported_webhook_method",
            f"Webhook trigger expects {configured_method}, but this ingress accepts POST.",
            workflow_id=workflow_id,
            trigger_node_id=trigger_node_id,
        )

    request_id = body.requestId or request_id_header or str(uuid.uuid4())
    idempotency_key = body.idempotencyKey or idempotency_header
    run_id = body.runId
    if run_id is None and idempotency_key:
        run_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"opencli-admin:workflow-webhook:{workflow_id}:{trigger_node_id}:{idempotency_key}",
            )
        )

    if run_id is not None and idempotency_key:
        existing = await get_workflow_run_projection(run_id, session=db)
        if existing is not None:
            return ApiResponse.ok(
                _webhook_ingress_response(
                    existing,
                    trigger_node_id=trigger_node_id,
                    request_id=request_id,
                    source_id=body.input.sourceId or source_id_header or "external",
                    idempotency_key=idempotency_key,
                )
            )

    runtime_input = body.input.model_copy(
        update={
            "source": "external",
            "sourceId": body.input.sourceId or source_id_header or "external",
        }
    )
    projection = await start_workflow_run(
        workflow_schemas.WorkflowRunStartRequest(
            project=body.workflowProject,
            runId=run_id,
            traceId=body.traceId,
            trigger=workflow_schemas.WorkflowRunTrigger(
                kind="webhook",
                triggerNodeId=trigger_node_id,
                requestId=request_id,
                idempotencyKey=idempotency_key,
            ),
            input=runtime_input,
            responseMode=body.responseMode,
        ),
        session=db,
        graphon_client=get_dify_graphon_client(),
        plugins=request.app.state.workflow_plugins,
    )
    await dispatch_materialized_image_jobs(db, projection.runId)
    return ApiResponse.ok(
        _webhook_ingress_response(
            projection,
            trigger_node_id=trigger_node_id,
            request_id=request_id,
            source_id=runtime_input.sourceId,
            idempotency_key=idempotency_key,
        )
    )


async def dispatch_materialized_image_jobs(db: AsyncSession, run_id: str) -> None:
    """Commit durable job intent before handing it to Celery.

    With no attested runtime or durable worker, jobs are persisted as blocked
    instead. This keeps the public run checkpoint truthful and fail-closed.
    """

    jobs = list(
        (await db.execute(select(ImageGenerationJob).where(ImageGenerationJob.run_id == run_id)))
        .scalars()
        .all()
    )
    if not jobs:
        return

    block_reason = dispatch_block_reason(get_settings())
    if block_reason is not None:
        for job in jobs:
            if job.status == ImageGenerationJobStatus.QUEUED.value:
                await image_studio_service.transition_job(
                    db,
                    job,
                    ImageGenerationJobStatus.BLOCKED,
                    error_code=block_reason,
                    error_detail=(
                        "Image generation requires the configured private runtime "
                        "and durable worker"
                    ),
                )
        return

    dispatch_ids = [
        job.id
        for job in jobs
        if job.status
        in {
            ImageGenerationJobStatus.QUEUED.value,
            ImageGenerationJobStatus.SUBMITTED.value,
            ImageGenerationJobStatus.RUNNING.value,
            ImageGenerationJobStatus.INGESTING.value,
        }
    ]
    if not dispatch_ids:
        return

    await db.commit()
    from backend.worker.tasks import run_image_generation_job

    for job_id in dispatch_ids:
        run_image_generation_job.delay(job_id)


@router.get(
    "/runs/{run_id}",
    response_model=ApiResponse[WorkflowRunProjection],
)
async def get_run_projection(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowRunProjection]:
    """Return the latest node-state projection for a workflow run."""

    await reject_workspace_scoped_run(db, run_id)
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(projection)


@router.get(
    "/runs/{run_id}/evidence-batches",
    response_model=ApiResponse[WorkflowEvidenceBatchListResponse],
)
async def get_run_evidence_batches(
    run_id: str,
    node_id: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowEvidenceBatchListResponse]:
    """List compact EvidenceBatch metadata without returning raw records."""

    await reject_workspace_scoped_run(db, run_id)
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        batches = list_evidence_batches(
            projection,
            node_id=node_id,
            source_group=source_group,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse.ok(batches)


@router.get(
    "/runs/{run_id}/evidence-batches/{batch_id}",
    response_model=ApiResponse[WorkflowEvidenceBatchDetail],
)
async def get_run_evidence_batch(
    run_id: str,
    batch_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowEvidenceBatchDetail]:
    """Return one compact EvidenceBatch manifest and source coverage projection."""

    await reject_workspace_scoped_run(db, run_id)
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    batch = get_evidence_batch(projection, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Evidence batch not found")
    return ApiResponse.ok(batch)


@router.get(
    "/runs/{run_id}/projection",
    response_model=ApiResponse[WorkflowEvidenceProjection],
)
async def get_run_evidence_projection(
    run_id: str,
    node_id: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    include: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowEvidenceProjection]:
    """Project run results for Canvas and AI consumers from replayable metadata."""

    await reject_workspace_scoped_run(db, run_id)
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        includes = parse_projection_includes(include)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse.ok(
        build_evidence_projection(
            projection,
            node_id=node_id,
            source_group=source_group,
            includes=includes,
        )
    )


@router.post(
    "/runs/{run_id}/source-outputs",
    response_model=ApiResponse[WorkflowRunProjection],
    status_code=202,
)
async def continue_run_with_source_outputs(
    run_id: str,
    body: workflow_schemas.WorkflowRunSourceOutputsRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowRunProjection]:
    """Continue a workflow run after external source batches arrive."""

    await reject_workspace_scoped_run(db, run_id)
    checkpoint = await get_workflow_run_checkpoint(run_id, session=db)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    worker_owned_nodes = {
        str(pending.get("nodeId"))
        for pending in checkpoint.pendingJobs
        if pending.get("bindingId") == "workflow.media.image-generation" and pending.get("nodeId")
    }
    if worker_owned_nodes.intersection(body.sourceOutputs):
        raise HTTPException(
            status_code=409,
            detail=("Image generation outputs are accepted only from the platform job worker"),
        )
    projection = await continue_workflow_run_with_source_outputs(run_id, body, session=db)
    if projection is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(projection)


@router.post(
    "/runs/{run_id}/gaojixing/resume",
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
    status_code=202,
)
async def resume_gaojixing_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[workflow_schemas.WorkflowRunProjection]:
    """Explicitly requeue a human-cleared governed checkpoint."""

    await reject_workspace_scoped_run(db, run_id)
    from backend.models.gaojixing_collection import GaojixingCollectionRun

    job = await db.scalar(
        select(GaojixingCollectionRun).where(GaojixingCollectionRun.workflow_run_id == run_id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Gaojixing collection not found")
    try:
        resumed = await resume_collection(db, job_id=job.id)
    except GaojixingCollectionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if resumed is None:
        raise HTTPException(status_code=404, detail="Gaojixing collection not found")
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(projection)


@router.get(
    "/runs/{run_id}/research-ledger",
    response_model=ApiResponse[WorkflowResearchLedgerResponse],
)
async def get_run_research_ledger(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowResearchLedgerResponse]:
    """Return the immutable parent/child revision chain for one research run."""

    await reject_workspace_scoped_run(db, run_id)
    ledger = await get_research_ledger(run_id, session=db)
    if ledger is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(ledger)


@router.post(
    "/runs/{run_id}/research-continuations",
    response_model=ApiResponse[WorkflowResearchContinuationResponse],
    status_code=202,
)
async def continue_research_run(
    run_id: str,
    body: WorkflowResearchContinuationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowResearchContinuationResponse]:
    """Start one bounded child Run from an accepted collect_more proposal."""

    await reject_workspace_scoped_run(db, run_id)
    try:
        result = await continue_research_workflow_run(run_id, body, session=db)
    except ResearchContinuationError as exc:
        status_code = 413 if "too_large" in exc.code else 409
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(result)


@router.get(
    "/runs/{run_id}/checkpoint",
    response_model=ApiResponse[WorkflowRunCheckpoint],
)
async def get_run_checkpoint(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowRunCheckpoint]:
    """Return the latest durable checkpoint descriptor for a workflow run."""

    await reject_workspace_scoped_run(db, run_id)
    checkpoint = await get_workflow_run_checkpoint(run_id, session=db)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(checkpoint)


@router.get(
    "/runs/{run_id}/trace",
    response_model=ApiResponse[WorkflowRunTraceResponse],
)
async def query_run_trace(
    run_id: str,
    after_sequence: int | None = Query(default=None, ge=0, alias="afterSequence"),
    node_id: str | None = Query(default=None, alias="nodeId"),
    event_type: WorkflowNodeRunEventType | None = Query(
        default=None,
        alias="eventType",
    ),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowRunTraceResponse]:
    """Query persisted run trace events with a checkpoint for resume/replay."""

    await reject_workspace_scoped_run(db, run_id)
    projection = await get_workflow_run_projection(run_id, session=db)
    checkpoint = await get_workflow_run_checkpoint(run_id, session=db)
    events = await list_workflow_run_events(
        run_id,
        session=db,
        after_sequence=after_sequence,
        node_id=node_id,
        event_type=event_type,
        limit=limit,
    )
    if projection is None or checkpoint is None or events is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    next_after_sequence = max((event.sequence for event in events), default=after_sequence or 0)
    return ApiResponse.ok(
        WorkflowRunTraceResponse(
            projection=projection,
            checkpoint=checkpoint,
            events=events,
            filters={
                "afterSequence": after_sequence,
                "nodeId": node_id,
                "eventType": event_type,
                "limit": limit,
            },
            nextAfterSequence=next_after_sequence,
        )
    )


@router.get(
    "/runs/{run_id}/events",
    response_model=ApiResponse[list[workflow_schemas.WorkflowNodeRunEvent]],
)
async def get_run_events(
    run_id: str,
    after_sequence: int | None = Query(default=None, ge=0, alias="afterSequence"),
    node_id: str | None = Query(default=None, alias="nodeId"),
    event_type: WorkflowNodeRunEventType | None = Query(
        default=None,
        alias="eventType",
    ),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[workflow_schemas.WorkflowNodeRunEvent]]:
    """Replay node-level events already emitted for a workflow run."""

    await reject_workspace_scoped_run(db, run_id)
    events = await list_workflow_run_events(
        run_id,
        session=db,
        after_sequence=after_sequence,
        node_id=node_id,
        event_type=event_type,
        limit=limit,
    )
    if events is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(events)


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Replay node events as a server-sent event response."""

    await reject_workspace_scoped_run(db, run_id)
    projection = await get_workflow_run_projection(run_id, session=db)
    events = await list_workflow_run_events(run_id, session=db)
    if projection is None or events is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    body = "".join(
        [_sse("node_event", event.model_dump_json()) for event in events]
        + [_sse("run_state", projection.model_dump_json())]
    )
    return Response(
        content=body,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _webhook_validation_error(
    code: str,
    message: str,
    *,
    workflow_id: str,
    trigger_node_id: str,
) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": code,
            "message": message,
            "workflowId": workflow_id,
            "nodeId": trigger_node_id,
        },
    )


def _webhook_ingress_response(
    projection: WorkflowRunProjection,
    *,
    trigger_node_id: str,
    request_id: str,
    source_id: str | None,
    idempotency_key: str | None,
) -> workflow_schemas.WorkflowWebhookIngressResponse:
    base_path = f"/api/v1/workflows/runs/{projection.runId}"
    return workflow_schemas.WorkflowWebhookIngressResponse(
        workflowId=projection.workflowId,
        runId=projection.runId,
        traceId=projection.traceId,
        triggerNodeId=trigger_node_id,
        requestId=request_id,
        sourceId=source_id,
        idempotencyKey=idempotency_key,
        projectionPath=base_path,
        eventsPath=f"{base_path}/events",
        projection=projection,
    )
