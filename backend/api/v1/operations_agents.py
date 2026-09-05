from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db, queue_after_commit
from backend.models.identity import Team, WorkspaceRole
from backend.models.operations_agent import (
    AgentPermissionProfile,
    AgentProfileMode,
    OperationsAgentDraft,
    OperationsAgentIdentity,
    OperationsAgentRun,
    PublishedOperationsAgentVersion,
)
from backend.models.studio import StudioProject
from backend.schemas.automation import AgentWorkHealthRead
from backend.schemas.common import ApiResponse
from backend.schemas.operations_agent import (
    AgentProfileCreate,
    AgentProfileRead,
    OperationsAgentCreate,
    OperationsAgentDraftRead,
    OperationsAgentDraftUpdate,
    OperationsAgentPatch,
    OperationsAgentPublish,
    OperationsAgentRead,
    OperationsAgentRunCreate,
    OperationsAgentRunRead,
    OperationsAgentTeamRead,
    PublishedOperationsAgentVersionRead,
    agent_contract_from_model_configuration,
    agent_runtime_binding_from_model_configuration,
    validate_agent_contract_payload,
    validated_agent_model_configuration,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services.agent_conversation_service import (
    AgentConversationError,
    validate_context_binding,
)
from backend.services.agent_runtime_selection import (
    RuntimeSelectionError,
    select_agent_runtime,
)
from backend.services.agent_work_health import get_agent_work_health
from backend.services.operations_agent_runtime_service import (
    cancel_operations_agent_run,
    schedule_operations_agent_run,
)
from backend.services.studio_agent_session_access import resolve_agent_session_workspace

router = APIRouter(
    prefix="/workspaces/{workspace_id}/operations-agents", tags=["operations-agents"]
)


@router.get("/activity", response_model=ApiResponse[list[OperationsAgentRunRead]])
async def list_operations_agent_activity(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    runs = (
        (
            await db.execute(
                select(OperationsAgentRun)
                .where(OperationsAgentRun.workspace_id == workspace_id)
                .order_by(OperationsAgentRun.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([OperationsAgentRunRead.model_validate(run) for run in runs])


@router.get("/work-health", response_model=ApiResponse[AgentWorkHealthRead])
async def read_agent_work_health(
    workspace_id: str,
    project_id: str | None = Query(default=None, min_length=1, max_length=36),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    context = {"project_id": project_id} if project_id is not None else {}
    scope = await resolve_agent_session_workspace(
        db,
        identity,
        workspace_id,
        context=context,
    )
    require_permission(scope.access, WorkspacePermission.READ)
    if project_id is not None:
        if scope.studio_workspace_id is not None:
            studio_project_id = await db.scalar(
                select(StudioProject.id).where(
                    StudioProject.id == project_id,
                    StudioProject.workspace_id == scope.studio_workspace_id,
                    StudioProject.archived.is_(False),
                )
            )
            if studio_project_id is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "project is not owned by the Workspace",
                )
        try:
            await validate_context_binding(
                db,
                scope.workspace_id,
                context,
                studio_workspace_id=scope.studio_workspace_id,
            )
        except AgentConversationError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    health = await get_agent_work_health(
        db,
        identity=identity,
        requested_workspace_id=workspace_id,
        workspace_id=scope.workspace_id,
        studio_workspace_id=scope.studio_workspace_id,
        project_id=project_id,
        can_run=scope.access.allows(WorkspacePermission.RUN_OPERATIONS_AGENTS),
        can_manage=scope.access.allows(WorkspacePermission.MANAGE_AGENT_IDENTITIES),
    )
    return ApiResponse.ok(health)


async def _get_agent(
    db: AsyncSession, workspace_id: str, agent_id: str, *, lock: bool = False
) -> OperationsAgentIdentity:
    query = (
        select(OperationsAgentIdentity)
        .where(OperationsAgentIdentity.workspace_id == workspace_id)
        .where(OperationsAgentIdentity.id == agent_id)
    )
    if lock:
        query = query.with_for_update()
    agent = await db.scalar(query)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Operations Agent not found")
    return agent


async def _get_profile(db: AsyncSession, agent: OperationsAgentIdentity) -> AgentPermissionProfile:
    profile = await db.scalar(
        select(AgentPermissionProfile)
        .where(AgentPermissionProfile.operations_agent_id == agent.id)
        .where(AgentPermissionProfile.version == agent.current_profile_version)
    )
    if profile is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Current Agent Permission Profile is missing")
    return profile


def _read_agent(
    agent: OperationsAgentIdentity, profile: AgentPermissionProfile
) -> OperationsAgentRead:
    current = AgentProfileRead.model_validate(profile)
    return OperationsAgentRead(
        id=agent.id,
        workspace_id=agent.workspace_id,
        owning_team_id=agent.owning_team_id,
        name=agent.name,
        description=agent.description,
        disabled=agent.disabled,
        current_published_version=agent.current_published_version,
        current_profile=current,
        effective_profile=None if agent.disabled else current,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.get("/teams", response_model=ApiResponse[list[OperationsAgentTeamRead]])
async def list_operations_agent_teams(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    teams = (
        await db.execute(
            select(Team).where(Team.workspace_id == workspace_id).order_by(Team.name)
        )
    ).scalars().all()
    return ApiResponse.ok(
        [OperationsAgentTeamRead.model_validate(team) for team in teams]
    )


@router.get("", response_model=ApiResponse[list[OperationsAgentRead]])
async def list_operations_agents(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    agents = (
        (
            await db.execute(
                select(OperationsAgentIdentity)
                .where(OperationsAgentIdentity.workspace_id == workspace_id)
                .order_by(OperationsAgentIdentity.created_at)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([_read_agent(agent, await _get_profile(db, agent)) for agent in agents])


@router.post("", response_model=ApiResponse[OperationsAgentRead], status_code=201)
async def create_operations_agent(
    workspace_id: str,
    body: OperationsAgentCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    team_query = select(Team).where(Team.workspace_id == workspace_id)
    if body.owning_team_id is not None:
        team_query = team_query.where(Team.id == body.owning_team_id)
        team = await db.scalar(team_query)
    else:
        teams = (await db.execute(team_query.limit(2))).scalars().all()
        team = teams[0] if len(teams) == 1 else None
    if team is None:
        detail = (
            "Owning Team must belong to Workspace"
            if body.owning_team_id is not None
            else "owning_team_id is required unless Workspace has exactly one Team"
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)

    agent = OperationsAgentIdentity(
        workspace_id=workspace_id,
        owning_team_id=team.id,
        name=body.name,
        description=body.description,
        current_profile_version=1,
    )
    db.add(agent)
    await db.flush()
    profile = AgentPermissionProfile(
        operations_agent_id=agent.id,
        version=1,
        mode=AgentProfileMode.OBSERVE_ONLY,
        assigned_by_user_id=access.user_id,
        reason="Default Observe Only profile",
    )
    draft = OperationsAgentDraft(
        operations_agent_id=agent.id,
        updated_by_user_id=access.user_id,
    )
    db.add_all((profile, draft))
    await db.flush()
    return ApiResponse.ok(_read_agent(agent, profile))


@router.patch("/{agent_id}", response_model=ApiResponse[OperationsAgentRead])
async def patch_operations_agent(
    workspace_id: str,
    agent_id: str,
    body: OperationsAgentPatch,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    agent = await _get_agent(db, workspace_id, agent_id, lock=True)
    profile = await _get_profile(db, agent)
    if (
        agent.disabled
        and not body.disabled
        and profile.mode == AgentProfileMode.LOW_RISK_AUTOMATIC
        and access.role != WorkspaceRole.ADMIN
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only Workspace Admins may re-enable automatic Operations Agents",
        )
    agent.disabled = body.disabled
    if body.disabled:
        active_run_ids = list(
            await db.scalars(
                select(OperationsAgentRun.id)
                .where(OperationsAgentRun.workspace_id == workspace_id)
                .where(OperationsAgentRun.operations_agent_id == agent.id)
                .where(OperationsAgentRun.status.in_(("queued", "running", "paused")))
            )
        )
        await db.execute(
            update(OperationsAgentRun)
            .where(OperationsAgentRun.workspace_id == workspace_id)
            .where(OperationsAgentRun.operations_agent_id == agent.id)
            .where(OperationsAgentRun.status.in_(("queued", "running", "paused")))
            .values(status="cancelled")
        )
        for run_id in active_run_ids:
            queue_after_commit(db, partial(cancel_operations_agent_run, run_id))
    await db.flush()
    return ApiResponse.ok(_read_agent(agent, profile))


async def _get_agent_draft(
    db: AsyncSession,
    agent: OperationsAgentIdentity,
    *,
    lock: bool = False,
) -> OperationsAgentDraft:
    query = select(OperationsAgentDraft).where(OperationsAgentDraft.operations_agent_id == agent.id)
    if lock:
        query = query.with_for_update()
    draft = await db.scalar(query)
    if draft is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Operations Agent draft is missing")
    return draft


@router.get(
    "/{agent_id}/draft",
    response_model=ApiResponse[OperationsAgentDraftRead],
)
async def get_agent_draft(
    workspace_id: str,
    agent_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    agent = await _get_agent(db, workspace_id, agent_id)
    draft = await _get_agent_draft(db, agent)
    return ApiResponse.ok(OperationsAgentDraftRead.model_validate(draft))


@router.put(
    "/{agent_id}/draft",
    response_model=ApiResponse[OperationsAgentDraftRead],
)
async def update_agent_draft(
    workspace_id: str,
    agent_id: str,
    body: OperationsAgentDraftUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    agent = await _get_agent(db, workspace_id, agent_id, lock=True)
    if agent.disabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Disabled Operations Agent cannot be edited")
    draft = await _get_agent_draft(db, agent)
    updated = await db.execute(
        update(OperationsAgentDraft)
        .where(OperationsAgentDraft.id == draft.id)
        .where(OperationsAgentDraft.revision == body.revision)
        .values(
            revision=body.revision + 1,
            instructions=body.instructions,
            model_configuration=body.model_configuration,
            tool_configuration=body.tool_configuration,
            updated_by_user_id=access.user_id,
        )
    )
    if getattr(updated, "rowcount", 0) != 1:
        actual_revision = await db.scalar(
            select(OperationsAgentDraft.revision).where(OperationsAgentDraft.id == draft.id)
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "operations_agent_draft_revision_conflict",
                "expected_revision": body.revision,
                "actual_revision": actual_revision,
            },
        )
    await db.refresh(draft)
    return ApiResponse.ok(OperationsAgentDraftRead.model_validate(draft))


@router.get(
    "/{agent_id}/versions",
    response_model=ApiResponse[list[PublishedOperationsAgentVersionRead]],
)
async def list_agent_versions(
    workspace_id: str,
    agent_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    agent = await _get_agent(db, workspace_id, agent_id)
    versions = (
        (
            await db.execute(
                select(PublishedOperationsAgentVersion)
                .where(PublishedOperationsAgentVersion.operations_agent_id == agent.id)
                .order_by(PublishedOperationsAgentVersion.version.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok(
        [PublishedOperationsAgentVersionRead.model_validate(version) for version in versions]
    )


@router.get(
    "/{agent_id}/versions/{version_number}",
    response_model=ApiResponse[PublishedOperationsAgentVersionRead],
)
async def get_agent_version(
    workspace_id: str,
    agent_id: str,
    version_number: int,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    agent = await _get_agent(db, workspace_id, agent_id)
    version = await db.scalar(
        select(PublishedOperationsAgentVersion)
        .where(PublishedOperationsAgentVersion.operations_agent_id == agent.id)
        .where(PublishedOperationsAgentVersion.version == version_number)
    )
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Published Agent Version not found")
    return ApiResponse.ok(PublishedOperationsAgentVersionRead.model_validate(version))


@router.post(
    "/{agent_id}/versions",
    response_model=ApiResponse[PublishedOperationsAgentVersionRead],
    status_code=201,
)
async def publish_agent_version(
    workspace_id: str,
    agent_id: str,
    body: OperationsAgentPublish,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    agent = await _get_agent(db, workspace_id, agent_id, lock=True)
    if agent.disabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Disabled Operations Agent cannot publish")
    profile = await _get_profile(db, agent)
    if profile.mode == AgentProfileMode.LOW_RISK_AUTOMATIC and access.role != WorkspaceRole.ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Automatic Operations Agent releases require Workspace Admin approval",
        )
    draft = await _get_agent_draft(db, agent)
    if not draft.instructions.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "A non-empty Agent Draft is required")
    try:
        model_configuration = validated_agent_model_configuration(draft.model_configuration)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Agent Draft contains invalid versioned configuration",
        ) from exc
    version_number = (agent.current_published_version or 0) + 1
    version = PublishedOperationsAgentVersion(
        operations_agent_id=agent.id,
        version=version_number,
        draft_revision=draft.revision,
        instructions=draft.instructions,
        model_configuration=model_configuration,
        tool_configuration=draft.tool_configuration,
        published_by_user_id=access.user_id,
        reason=body.reason,
    )
    db.add(version)
    agent.current_published_version = version_number
    await db.flush()
    return ApiResponse.ok(PublishedOperationsAgentVersionRead.model_validate(version))


@router.post(
    "/{agent_id}/runs",
    response_model=ApiResponse[OperationsAgentRunRead],
    status_code=201,
)
async def start_agent_run(
    workspace_id: str,
    agent_id: str,
    body: OperationsAgentRunCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.RUN_OPERATIONS_AGENTS)
    agent = await _get_agent(db, workspace_id, agent_id, lock=True)
    if agent.disabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Disabled Operations Agent cannot run")
    if agent.current_published_version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published Agent Version required")
    published_version = await db.scalar(
        select(PublishedOperationsAgentVersion)
        .where(PublishedOperationsAgentVersion.operations_agent_id == agent.id)
        .where(PublishedOperationsAgentVersion.version == agent.current_published_version)
    )
    if published_version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published Agent Version is missing")
    try:
        contract = agent_contract_from_model_configuration(published_version.model_configuration)
        runtime_binding = agent_runtime_binding_from_model_configuration(
            published_version.model_configuration
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published Agent Version contains an invalid AgentContractV2",
        ) from exc
    if contract is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published Agent Version requires an AgentContractV2",
        )
    if runtime_binding is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published Agent Version requires an AgentRuntimeBindingV2",
        )
    try:
        execution_binding = await select_agent_runtime(
            db,
            contract=contract,
            binding=runtime_binding,
        )
    except RuntimeSelectionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Agent Runtime Fleet precheck failed: {exc}",
        ) from exc
    try:
        validate_agent_contract_payload(contract, "input_schema", body.input_payload)
        validate_agent_contract_payload(contract, "state_schema", body.state_payload)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Operations Agent run payload violates AgentContractV2: {exc}",
        ) from exc
    profile = await _get_profile(db, agent)
    if profile.mode == AgentProfileMode.LOW_RISK_AUTOMATIC:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Automatic Operations Agent runs require the governed action gateway",
        )
    run = OperationsAgentRun(
        workspace_id=workspace_id,
        operations_agent_id=agent.id,
        published_version=agent.current_published_version,
        profile_version=agent.current_profile_version,
        trigger_type="manual",
        target_resource_type=body.target_resource_type,
        target_resource_id=body.target_resource_id,
        input_payload=body.input_payload,
        state_payload=body.state_payload,
        execution_binding=execution_binding,
        evidence_payload=None,
        status="queued",
        started_by_user_id=access.user_id,
    )
    db.add(run)
    await db.flush()
    queue_after_commit(db, lambda: schedule_operations_agent_run(run.id))
    return ApiResponse.ok(OperationsAgentRunRead.model_validate(run))


@router.post(
    "/{agent_id}/runs/{run_id}/pause",
    response_model=ApiResponse[OperationsAgentRunRead],
)
async def pause_agent_run(
    workspace_id: str,
    agent_id: str,
    run_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.RUN_OPERATIONS_AGENTS)
    run = await db.scalar(
        select(OperationsAgentRun)
        .where(OperationsAgentRun.workspace_id == workspace_id)
        .where(OperationsAgentRun.operations_agent_id == agent_id)
        .where(OperationsAgentRun.id == run_id)
        .with_for_update()
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Operations Agent Run not found")
    paused = await db.execute(
        update(OperationsAgentRun)
        .where(OperationsAgentRun.id == run.id)
        .where(OperationsAgentRun.status.in_(("queued", "running")))
        .values(status="paused")
    )
    if getattr(paused, "rowcount", 0) != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Operations Agent Run cannot be paused")
    await db.refresh(run)
    queue_after_commit(db, partial(cancel_operations_agent_run, run.id))
    return ApiResponse.ok(OperationsAgentRunRead.model_validate(run))


@router.post(
    "/{agent_id}/profiles",
    response_model=ApiResponse[AgentProfileRead],
    status_code=201,
)
async def assign_agent_profile(
    workspace_id: str,
    agent_id: str,
    body: AgentProfileCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.ASSIGN_AGENT_PROFILES)
    if body.mode == AgentProfileMode.LOW_RISK_AUTOMATIC and access.role != WorkspaceRole.ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only Workspace Admins may assign Low-Risk Automatic profiles",
        )

    agent = await _get_agent(db, workspace_id, agent_id, lock=True)
    if agent.disabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Disabled Operations Agent has no active profile",
        )
    profile = AgentPermissionProfile(
        operations_agent_id=agent.id,
        version=agent.current_profile_version + 1,
        mode=body.mode,
        tool_scope=body.tool_scope,
        resource_scope=body.resource_scope,
        action_scope=body.action_scope,
        assigned_by_user_id=access.user_id,
        reason=body.reason,
    )
    db.add(profile)
    agent.current_profile_version = profile.version
    await db.flush()
    return ApiResponse.ok(AgentProfileRead.model_validate(profile))
