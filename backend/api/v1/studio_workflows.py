"""Workflow asset and mutable Draft routes for Studio."""

import json
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import (
    LOCAL_USER_ID,
    canonicalize_studio_graph,
    get_project,
    get_workflow,
)
from backend.api.v1.studio_schemas import (
    DraftRead,
    DraftUpdate,
    ProjectRuntimeLogRead,
    ProjectRuntimeSummaryRead,
    ProjectRuntimeTraceRead,
    PublishedWorkflowRunStart,
    WorkflowCreate,
    WorkflowRead,
)
from backend.api.v1.workflows import (
    build_evidence_projection,
    dispatch_materialized_image_jobs,
    get_evidence_batch,
    list_evidence_batches,
    parse_projection_includes,
)
from backend.models.gaojixing_collection_run import GaojixingCollectionRun
from backend.schemas.workflow_runtime import WorkflowRunStatus, WorkflowRunTraceResponse
from backend.database import get_db, rollback_session
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowDraft,
    StudioWorkflowVersion,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import ApiResponse, PaginationMeta
from backend.services.gaojixing_collection_service import (
    GaojixingCollectionConflictError,
    resume_collection,
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
from backend.workflow.opencli_hda_tracer import (
    get_workflow_run_checkpoint,
    get_workflow_run_projection,
    list_workflow_run_events,
    replay_downstream_from_persisted_gaojixing_source,
    start_workflow_run,
)

router = APIRouter()


def _canonical_run_identity(*, inputs: dict, user: str) -> str:
    return json.dumps(
        {"inputs": inputs, "user": user},
        sort_keys=True,
        separators=(",", ":"),
    )


def _runtime_log(
    row: WorkflowRun,
    *,
    workflow_names: dict[str, str],
    version_numbers: dict[str, int],
) -> ProjectRuntimeLogRead:
    projection = row.projection or {}
    request = row.request or {}
    duration_ms = max(0, int((row.updated_at - row.created_at).total_seconds() * 1000))
    return ProjectRuntimeLogRead(
        run_id=row.id,
        workflow_id=row.workflow_id,
        workflow_name=workflow_names.get(row.workflow_id, "未知工作流"),
        workflow_version=version_numbers.get(row.studio_workflow_version_id or ""),
        trace_id=row.trace_id,
        status=row.status,
        trigger=str((request.get("trigger") or {}).get("kind") or "manual"),
        response_mode=request.get("responseMode") or "async",
        event_count=int(projection.get("eventCount", 0)),
        node_count=len(projection.get("nodeStates", []) or []),
        error_count=len(projection.get("errors", []) or []),
        duration_ms=duration_ms,
        started_at=row.created_at,
        updated_at=row.updated_at,
    )


def _default_published_trigger_kind(
    project: workflow_schemas.WorkflowProject,
    trigger_node_id: str | None,
) -> workflow_schemas.WorkflowRunTriggerKind:
    """Choose the graph's real trigger when Studio Run omits one.

    The authoring UI historically posted ``manual`` unconditionally.  That
    silently makes schedule-only workflows fail before any node executes.  A
    direct Studio/CLI run is still an explicit run, but it must enter through
    the trigger entry that actually exists in the published graph.
    """
    nodes = project.nodes
    if trigger_node_id:
        selected = next((node for node in nodes if node.id == trigger_node_id), None)
        if selected is not None:
            if selected.kind == "webhook":
                return "webhook"
            if selected.kind == "schedule":
                params = selected.params
                builder = params.get("builder")
                if params.get("mode") == "manual" or (
                    isinstance(builder, dict) and builder.get("nodeType") == "manual-trigger"
                ):
                    return "manual"
                return "schedule"

    for node in nodes:
        if node.kind == "schedule" and (
            node.params.get("mode") == "manual"
            or (
                isinstance(node.params.get("builder"), dict)
                and node.params["builder"].get("nodeType") == "manual-trigger"
            )
        ):
            return "manual"
    if any(node.kind == "schedule" for node in nodes):
        return "schedule"
    if any(node.kind == "webhook" for node in nodes):
        return "webhook"
    return "manual"


async def _project_runtime_scope(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
) -> tuple[dict[str, str], dict[str, int]]:
    await get_project(db, workspace_id, project_id)
    workflows = list(
        (
            await db.execute(
                select(StudioWorkflow).where(
                    StudioWorkflow.project_id == project_id,
                    StudioWorkflow.archived.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    workflow_names = {workflow.id: workflow.name for workflow in workflows}
    if not workflow_names:
        return workflow_names, {}
    versions = list(
        (
            await db.execute(
                select(StudioWorkflowVersion).where(
                    StudioWorkflowVersion.workflow_id.in_(workflow_names)
                )
            )
        )
        .scalars()
        .all()
    )
    return workflow_names, {version.id: version.version for version in versions}


async def _get_project_workflow_run(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
) -> WorkflowRun:
    await get_workflow(db, workspace_id, project_id, workflow_id)
    row = await db.get(WorkflowRun, run_id)
    if row is None or row.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return row


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows",
    response_model=ApiResponse[list[WorkflowRead]],
)
async def list_workflows(
    workspace_id: str, project_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    await get_project(db, workspace_id, project_id)
    rows = (
        (
            await db.execute(
                select(StudioWorkflow)
                .where(
                    StudioWorkflow.project_id == project_id,
                    StudioWorkflow.archived.is_(False),
                )
                .order_by(StudioWorkflow.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([WorkflowRead.model_validate(row) for row in rows])


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/runtime-summary",
    response_model=ApiResponse[ProjectRuntimeSummaryRead],
)
async def get_project_runtime_summary(
    workspace_id: str,
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Summarize persisted workflow runs for one Studio Project."""

    workflow_names, version_numbers = await _project_runtime_scope(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if not workflow_names:
        return ApiResponse.ok(
            ProjectRuntimeSummaryRead(
                total_runs=0,
                successful_runs=0,
                failed_runs=0,
                blocked_runs=0,
                running_runs=0,
                total_events=0,
                recent_logs=[],
            )
        )

    # ponytail: scan compact projection rows here; add maintained counters only
    # when project run volume makes this operator-page query measurable.
    aggregate_rows = list(
        (
            await db.execute(
                select(WorkflowRun.status, WorkflowRun.projection).where(
                    WorkflowRun.workflow_id.in_(workflow_names)
                )
            )
        ).all()
    )
    recent_rows = list(
        (
            await db.execute(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id.in_(workflow_names))
                .order_by(WorkflowRun.created_at.desc())
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    successful = {"completed", "partial_success"}
    failed = {"failed"}
    blocked = {"blocked"}
    running = {"queued", "running", "waiting", "partial"}

    return ApiResponse.ok(
        ProjectRuntimeSummaryRead(
            total_runs=len(aggregate_rows),
            successful_runs=sum(1 for row in aggregate_rows if row.status in successful),
            failed_runs=sum(1 for row in aggregate_rows if row.status in failed),
            blocked_runs=sum(1 for row in aggregate_rows if row.status in blocked),
            running_runs=sum(1 for row in aggregate_rows if row.status in running),
            total_events=sum(
                int((row.projection or {}).get("eventCount", 0)) for row in aggregate_rows
            ),
            recent_logs=[
                _runtime_log(
                    row,
                    workflow_names=workflow_names,
                    version_numbers=version_numbers,
                )
                for row in recent_rows
            ],
        )
    )


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/runtime-logs",
    response_model=ApiResponse[list[ProjectRuntimeLogRead]],
)
async def list_project_runtime_logs(
    workspace_id: str,
    project_id: str,
    run_status: WorkflowRunStatus | None = Query(
        default=None,
        alias="status",
    ),
    search: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """List durable Workflow Runs owned by one Studio Project."""

    workflow_names, version_numbers = await _project_runtime_scope(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if not workflow_names:
        return ApiResponse.ok(
            [],
            meta=PaginationMeta(total=0, page=page, limit=limit, pages=1),
        )

    filters = [WorkflowRun.workflow_id.in_(workflow_names)]
    if run_status:
        filters.append(WorkflowRun.status == run_status)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                WorkflowRun.id.ilike(pattern),
                WorkflowRun.trace_id.ilike(pattern),
                WorkflowRun.workflow_id.ilike(pattern),
            )
        )

    total = int(await db.scalar(select(func.count()).select_from(WorkflowRun).where(*filters)) or 0)
    rows = list(
        (
            await db.execute(
                select(WorkflowRun)
                .where(*filters)
                .order_by(WorkflowRun.created_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok(
        [
            _runtime_log(
                row,
                workflow_names=workflow_names,
                version_numbers=version_numbers,
            )
            for row in rows
        ],
        meta=PaginationMeta(
            total=total,
            page=page,
            limit=limit,
            pages=max(1, -(-total // limit)),
        ),
    )


async def _get_project_workflow_run(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
) -> tuple[WorkflowRun, int]:
    """Load one workflow run only through its owning project and version."""
    await get_workflow(db, workspace_id, project_id, workflow_id)
    result = await db.execute(
        select(WorkflowRun, StudioWorkflowVersion.version)
        .join(
            StudioWorkflowVersion,
            WorkflowRun.studio_workflow_version_id == StudioWorkflowVersion.id,
        )
        .where(
            WorkflowRun.id == run_id,
            WorkflowRun.workflow_id == workflow_id,
            StudioWorkflowVersion.workflow_id == workflow_id,
        )
    )
    scoped_run = result.one_or_none()
    if scoped_run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return scoped_run


async def _published_workflow_version(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
) -> StudioWorkflowVersion:
    workflow = await get_workflow(db, workspace_id, project_id, workflow_id)
    if workflow.current_published_version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow must be published before API execution",
        )
    version = await db.scalar(
        select(StudioWorkflowVersion).where(
            StudioWorkflowVersion.workflow_id == workflow_id,
            StudioWorkflowVersion.version == workflow.current_published_version,
        )
    )
    if version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published workflow version is unavailable",
        )
    return version


def _published_run_id(
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    version_id: str,
    idempotency_key: str | None,
) -> str | None:
    if not idempotency_key:
        return None
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "opencli-admin:studio-run:"
                f"{workspace_id}:{project_id}:{workflow_id}:{version_id}:{idempotency_key}"
            ),
        )
    )


async def _existing_published_run_projection(
    db: AsyncSession,
    *,
    run_id: str,
    workflow_id: str,
    version_id: str,
    requested_identity: str,
) -> workflow_schemas.WorkflowRunProjection | None:
    existing = await db.get(WorkflowRun, run_id)
    if existing is None:
        return None
    existing_input = existing.request.get("input") if isinstance(existing.request, dict) else None
    existing_payload = existing_input.get("payload") if isinstance(existing_input, dict) else None
    existing_user = existing_input.get("sourceId") if isinstance(existing_input, dict) else None
    identity_matches = (
        isinstance(existing_payload, dict)
        and isinstance(existing_user, str)
        and _canonical_run_identity(inputs=existing_payload, user=existing_user)
        == requested_identity
    )
    if (
        existing.workflow_id != workflow_id
        or existing.studio_workflow_version_id != version_id
        or not identity_matches
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Idempotency key collides with another workflow run",
        )
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Stored idempotent workflow run is unavailable",
        )
    return projection


async def _start_published_version_run(
    *,
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    version: StudioWorkflowVersion,
    run_input: workflow_schemas.WorkflowRunInput,
    user: str,
    request_id: str,
    response_mode: workflow_schemas.WorkflowRunResponseMode,
    trigger_kind: workflow_schemas.WorkflowRunTriggerKind | None = None,
    trigger_node_id: str | None = None,
    idempotency_key: str | None = None,
    run_id: str | None = None,
    plugins=None,
) -> ApiResponse:
    version_id = version.id
    resolved_run_id = run_id or _published_run_id(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        version_id=version_id,
        idempotency_key=idempotency_key,
    )

    requested_identity = _canonical_run_identity(
        inputs=run_input.payload,
        user=user,
    )
    if idempotency_key:
        existing_projection = await _existing_published_run_projection(
            db,
            run_id=resolved_run_id,
            workflow_id=workflow_id,
            version_id=version_id,
            requested_identity=requested_identity,
        )
        if existing_projection is not None:
            return ApiResponse.ok(existing_projection)

    project = workflow_schemas.WorkflowProject.model_validate(version.graph)
    resolved_trigger_kind = trigger_kind or _default_published_trigger_kind(
        project,
        trigger_node_id,
    )
    try:
        projection = await start_workflow_run(
            workflow_schemas.WorkflowRunStartRequest(
                project=project,
                runId=resolved_run_id,
                trigger=workflow_schemas.WorkflowRunTrigger(
                    kind=resolved_trigger_kind,
                    triggerNodeId=trigger_node_id,
                    requestId=request_id,
                    idempotencyKey=idempotency_key,
                ),
                input=run_input,
                responseMode=response_mode,
            ),
            session=db,
            studio_workflow_version_id=version_id,
            plugins=plugins,
        )
    except IntegrityError:
        if not idempotency_key:
            raise
        await rollback_session(db)
        projection = await _existing_published_run_projection(
            db,
            run_id=resolved_run_id,
            workflow_id=workflow_id,
            version_id=version_id,
            requested_identity=requested_identity,
        )
        if projection is None:
            raise
        return ApiResponse.ok(projection)
    await dispatch_materialized_image_jobs(db, projection.runId)
    return ApiResponse.ok(projection)


@router.post(
    ("/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs"),
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
    status_code=202,
)
async def start_published_workflow_run(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    body: PublishedWorkflowRunStart,
    request: Request,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    request_id_header: str | None = Header(default=None, alias="X-Request-ID"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Run the immutable published graph without accepting graph replacement."""

    version = await _published_workflow_version(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    request_id = body.request_id or request_id_header or str(uuid.uuid4())
    idempotency_key = body.idempotency_key or idempotency_header
    return await _start_published_version_run(
        plugins=request.app.state.workflow_plugins,
        db=db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        version=version,
        run_input=workflow_schemas.WorkflowRunInput(
            payload=body.inputs,
            source="external",
            sourceId=body.user,
        ),
        user=body.user,
        request_id=request_id,
        idempotency_key=idempotency_key,
        response_mode=body.response_mode,
        trigger_kind=body.trigger_kind,
        trigger_node_id=body.trigger_node_id,
    )


@router.post(
    ("/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/question-bank"),
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
    status_code=202,
)
async def start_published_workflow_run_from_question_bank(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    question_bank: UploadFile = File(..., alias="questionBank"),
    request: str = Form(...),
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    request_id_header: str | None = Header(default=None, alias="X-Request-ID"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Run the immutable published graph from one managed question package."""

    version = await _published_workflow_version(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    project = workflow_schemas.WorkflowProject.model_validate(version.graph)
    if not accepts_managed_question_batch(project):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Question bank uploads require the governed Gaojixing workflow packages",
        )
    try:
        body = PublishedWorkflowRunStart.model_validate_json(request)
        request_id = body.request_id or request_id_header or str(uuid.uuid4())
        idempotency_key = body.idempotency_key or idempotency_header
        request_owns_run_directory = idempotency_key is None
        run_id = _published_run_id(
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            version_id=version.id,
            idempotency_key=idempotency_key,
        ) or str(uuid.uuid4())
        payload = await question_bank.read(MAX_QUESTION_BANK_BYTES + 1)
        staged = stage_managed_question_batch(
            payload,
            filename=question_bank.filename or "",
            run_id=run_id,
        )
    except UnsupportedQuestionBatchFormatError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except ManagedQuestionBatchConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (ManagedQuestionBatchError, ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    finally:
        await question_bank.close()

    try:
        return await _start_published_version_run(
            db=db,
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            version=version,
            run_input=workflow_schemas.WorkflowRunInput(
                payload={"questionBatchRef": staged.question_batch_ref},
                source="external",
                sourceId=body.user,
            ),
            user=body.user,
            request_id=request_id,
            idempotency_key=idempotency_key,
            response_mode=body.response_mode,
            trigger_kind=body.trigger_kind,
            trigger_node_id=body.trigger_node_id,
            run_id=run_id,
        )
    except Exception:
        if request_owns_run_directory and staged.created:
            cleanup_managed_question_batch(
                staged.question_batch_ref,
                expected_run_id=run_id,
            )
        raise


@router.post(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/runs/{run_id}/downstream-replay"
    ),
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
    status_code=202,
)
async def replay_persisted_gaojixing_source_downstream(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Replay normalization through sink from completed persisted Gaojixing evidence."""

    workflow = await get_workflow(db, workspace_id, project_id, workflow_id)
    if workflow.current_published_version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Workflow must be published before downstream replay"
        )
    version = await db.scalar(
        select(StudioWorkflowVersion).where(
            StudioWorkflowVersion.workflow_id == workflow_id,
            StudioWorkflowVersion.version == workflow.current_published_version,
        )
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published workflow version is unavailable")
    await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    try:
        projection = await replay_downstream_from_persisted_gaojixing_source(
            run_id,
            expected_workflow_id=workflow_id,
            expected_studio_workflow_version_id=version.id,
            session=db,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ApiResponse.ok(projection)


@router.get(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/runs/{run_id}/trace"
    ),
    response_model=ApiResponse[ProjectRuntimeTraceRead],
)
async def get_project_runtime_trace(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    after_sequence: int | None = Query(default=None, ge=0, alias="afterSequence"),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Return one project-owned run trace without opening generic run access."""

    row, workflow_version = await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    projection = await get_workflow_run_projection(run_id, session=db)
    checkpoint = await get_workflow_run_checkpoint(run_id, session=db)
    events = await list_workflow_run_events(
        run_id,
        session=db,
        after_sequence=after_sequence,
        limit=limit,
    )

    if projection is None or checkpoint is None or events is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    request = row.request or {}
    input_payload = request.get("input") or {}
    return ApiResponse.ok(
        ProjectRuntimeTraceRead(
            workflow_version=workflow_version,
            inputs=input_payload.get("payload") or {},
            user=input_payload.get("sourceId"),
            response_mode=request.get("responseMode") or "async",
            trace=WorkflowRunTraceResponse(
                projection=projection,
                checkpoint=checkpoint,
                events=events,
                filters={"afterSequence": after_sequence, "limit": limit},
                nextAfterSequence=max(
                    (event.sequence for event in events),
                    default=after_sequence or 0,
                ),
            ),
        )
    )


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}",
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
)
async def get_project_workflow_run_projection(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return ApiResponse.ok(projection)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/events",
    response_model=ApiResponse[list[workflow_schemas.WorkflowNodeRunEvent]],
)
async def list_project_workflow_run_events(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    after_sequence: int | None = Query(default=None, ge=0, alias="afterSequence"),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    events = await list_workflow_run_events(
        run_id, session=db, after_sequence=after_sequence, limit=limit
    )
    if events is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return ApiResponse.ok(events)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/evidence-batches",
    response_model=ApiResponse[workflow_schemas.WorkflowEvidenceBatchListResponse],
)
async def list_project_workflow_evidence_batches(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    node_id: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    try:
        batches = list_evidence_batches(
            projection,
            node_id=node_id,
            source_group=source_group,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ApiResponse.ok(batches)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/evidence-batches/{batch_id}",
    response_model=ApiResponse[workflow_schemas.WorkflowEvidenceBatchDetail],
)
async def get_project_workflow_evidence_batch(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    batch_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    batch = get_evidence_batch(projection, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence batch not found")
    return ApiResponse.ok(batch)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/projection",
    response_model=ApiResponse[workflow_schemas.WorkflowEvidenceProjection],
)
async def get_project_workflow_evidence_projection(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    node_id: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    include: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _get_project_workflow_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    try:
        includes = parse_projection_includes(include)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ApiResponse.ok(
        build_evidence_projection(
            projection,
            node_id=node_id,
            source_group=source_group,
            includes=includes,
        )
    )


@router.post(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/runs/{run_id}/gaojixing/resume"
    ),
    response_model=ApiResponse[workflow_schemas.WorkflowRunProjection],
    status_code=202,
)
async def resume_published_gaojixing_run(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[workflow_schemas.WorkflowRunProjection]:
    """Resume only a run owned by the requested Studio workflow scope."""

    await get_workflow(db, workspace_id, project_id, workflow_id)
    row = await db.get(WorkflowRun, run_id)
    if row is None or row.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    job = await db.scalar(
        select(GaojixingCollectionRun).where(GaojixingCollectionRun.workflow_run_id == run_id)
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gaojixing collection not found")
    try:
        await resume_collection(db, job_id=job.id)
    except GaojixingCollectionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return ApiResponse.ok(projection)


@router.post(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows",
    response_model=ApiResponse[WorkflowRead],
    status_code=201,
)
async def create_workflow(
    workspace_id: str,
    project_id: str,
    body: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    project = await db.scalar(
        select(StudioProject)
        .where(
            StudioProject.id == project_id,
            StudioProject.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    row = StudioWorkflow(project_id=project_id, name=body.name, description=body.description)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow name already exists") from exc
    graph = canonicalize_studio_graph(body.graph, workflow_id=row.id)
    db.add(
        StudioWorkflowDraft(
            workflow_id=row.id,
            graph=graph,
            updated_by_user_id=LOCAL_USER_ID,
        )
    )
    await db.flush()
    if project.primary_workflow_id is None:
        project.primary_workflow_id = row.id
        await db.flush()
    return ApiResponse.ok(WorkflowRead.model_validate(row))


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/draft",
    response_model=ApiResponse[DraftRead],
)
async def get_draft(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await get_workflow(db, workspace_id, project_id, workflow_id)
    row = await db.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == workflow_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    draft = DraftRead.model_validate(row, from_attributes=True)
    return ApiResponse.ok(
        draft.model_copy(
            update={
                "graph": workflow_schemas.WorkflowProject.model_validate(
                    canonicalize_studio_graph(
                        draft.graph,
                        workflow_id=workflow_id,
                    )
                )
            }
        )
    )


@router.put(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/draft",
    response_model=ApiResponse[DraftRead],
)
async def update_draft(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    body: DraftUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await get_workflow(db, workspace_id, project_id, workflow_id)
    row = await db.scalar(
        select(StudioWorkflowDraft)
        .where(StudioWorkflowDraft.workflow_id == workflow_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    if row.revision != body.revision:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow draft revision conflict")
    row.graph = canonicalize_studio_graph(body.graph, workflow_id=workflow_id)
    row.revision += 1
    row.updated_by_user_id = LOCAL_USER_ID
    await db.flush()
    return ApiResponse.ok(DraftRead.model_validate(row, from_attributes=True))
