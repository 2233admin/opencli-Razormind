from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.automation import Automation
from backend.schemas.automation import (
    AutomationCreate,
    AutomationRead,
    AutomationUpdate,
    StarterInstallationPreview,
    StarterInstallationResult,
)
from backend.schemas.common import ApiResponse
from backend.schemas.operations_agent import OperationsAgentRunRead
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services.automation_schedule_service import (AutomationBindingError, create_bound_automation_run, validate_automation_binding)
from backend.services.automation_starter_service import (
    install_starters,
    preview_starter_installation,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/automations", tags=["automations"])


@router.get(
    "/starters/preview",
    response_model=ApiResponse[StarterInstallationPreview],
)
async def preview_automation_starters(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    preview = await preview_starter_installation(db, workspace_id=workspace_id)
    return ApiResponse.ok(preview)


@router.post(
    "/starters/install",
    response_model=ApiResponse[StarterInstallationResult],
)
async def install_automation_starters(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    result = await install_starters(
        db,
        workspace_id=workspace_id,
        created_by_user_id=access.user_id,
    )
    return ApiResponse.ok(result)






@router.get("", response_model=ApiResponse[list[AutomationRead]])
async def list_automations(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    rows = (
        (
            await db.execute(
                select(Automation)
                .where(Automation.workspace_id == workspace_id)
                .order_by(Automation.created_at)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([AutomationRead.model_validate(row) for row in rows])


@router.post("", response_model=ApiResponse[AutomationRead], status_code=201)
async def create_automation(
    workspace_id: str,
    body: AutomationCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    row = Automation(
        workspace_id=workspace_id,
        created_by_user_id=access.user_id,
        **body.model_dump(),
    )
    db.add(row)
    await db.flush()
    if row.enabled:
        try:
            await validate_automation_binding(db, row)
        except AutomationBindingError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return ApiResponse.ok(AutomationRead.model_validate(row))


@router.patch("/{automation_id}", response_model=ApiResponse[AutomationRead])
async def update_automation(
    workspace_id: str,
    automation_id: str,
    body: AutomationUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_AGENT_IDENTITIES)
    row = await db.scalar(
        select(Automation)
        .where(
            Automation.workspace_id == workspace_id,
            Automation.id == automation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Automation not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.revision += 1
    if row.enabled:
        try:
            await validate_automation_binding(db, row)
        except AutomationBindingError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.flush()
    return ApiResponse.ok(AutomationRead.model_validate(row))


@router.post(
    "/{automation_id}/runs",
    response_model=ApiResponse[OperationsAgentRunRead],
    status_code=201,
)
async def start_automation_run(
    workspace_id: str,
    automation_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.RUN_OPERATIONS_AGENTS)
    row = await db.scalar(
        select(Automation)
        .where(Automation.workspace_id == workspace_id, Automation.id == automation_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Automation not found")
    if not row.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Paused Automation cannot run")
    try:
        run, _ = await create_bound_automation_run(
            db,
            row,
            trigger_type="manual",
            started_by_user_id=access.user_id,
        )
    except AutomationBindingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ApiResponse.ok(OperationsAgentRunRead.model_validate(run))
