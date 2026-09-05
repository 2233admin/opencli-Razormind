from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_projects import bootstrap_project
from backend.api.v1.studio_schemas import ProjectBootstrapCreate
from backend.database import get_db
from backend.models.identity import (
    Team,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from backend.models.studio import StudioWorkspace
from backend.models.workflow import Project
from backend.schemas.common import ApiResponse
from backend.schemas.workflow_asset import ProjectRead
from backend.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceCreatedRead,
    WorkspaceMemberCreate,
    WorkspaceMemberRead,
    WorkspaceMemberRoleUpdate,
    WorkspaceRead,
    WorkspaceStatusUpdate,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)

router = APIRouter(tags=["workspaces"])


def _member_read(user: User, membership: WorkspaceMembership) -> WorkspaceMemberRead:
    return WorkspaceMemberRead(
        user_id=user.id,
        subject=user.subject,
        email=user.email,
        display_name=user.display_name,
        disabled=user.disabled,
        role=membership.role,
        created_at=membership.created_at,
    )


async def _get_or_create_user(
    db: AsyncSession,
    *,
    subject: str,
    email: str | None,
    display_name: str | None,
) -> User:
    user = await db.scalar(select(User).where(User.subject == subject))
    if user is None:
        user = User(subject=subject, email=email, display_name=display_name)
        db.add(user)
        await db.flush()
    elif user.disabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Disabled user cannot join a Workspace")
    return user


async def _ensure_local_admin_workspace(
    db: AsyncSession,
    identity: RequestIdentity,
) -> None:
    if identity.auth_method != "local":
        return

    user = await db.scalar(select(User).where(User.subject == identity.subject))
    if user is None:
        user = User(
            subject=identity.subject,
            display_name=identity.name or "本地管理员",
        )
        db.add(user)
        await db.flush()

    workspace = await db.scalar(select(Workspace).where(Workspace.slug == "opencli-default"))
    if workspace is None:
        workspace = Workspace(name="OpenCLI 工作区", slug="opencli-default")
        db.add(workspace)
        await db.flush()

    membership = await db.scalar(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.workspace_id == workspace.id)
        .where(WorkspaceMembership.user_id == user.id)
    )
    if membership is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.ADMIN,
            )
        )

    team = await db.scalar(
        select(Team).where(Team.workspace_id == workspace.id).where(Team.slug == "default")
    )
    if team is None:
        db.add(Team(workspace_id=workspace.id, name="默认团队", slug="default"))
        await db.flush()


@router.get(
    "/governance/workspaces",
    response_model=ApiResponse[list[WorkspaceRead]],
)
async def list_accessible_workspaces(
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _ensure_local_admin_workspace(db, identity)
    rows = (
        (
            await db.execute(
                select(Workspace)
                .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(
                    User.subject == identity.subject,
                    User.disabled.is_(False),
                    Workspace.active.is_(True),
                )
                .order_by(Workspace.name)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([WorkspaceRead.model_validate(row) for row in rows])


@router.get(
    "/governance/workspaces/{workspace_id}/projects",
    response_model=ApiResponse[list[ProjectRead]],
)
async def list_governance_projects(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    rows = (
        (
            await db.execute(
                select(Project)
                .where(Project.workspace_id == workspace_id, Project.archived.is_(False))
                .order_by(Project.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([ProjectRead.model_validate(row) for row in rows])


@router.post(
    "/governance/workspaces/{workspace_id}/projects/bootstrap",
    response_model=ApiResponse,
    status_code=201,
)
async def bootstrap_governance_project(
    workspace_id: str,
    body: ProjectBootstrapCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    return await bootstrap_project(workspace_id, body, db)


@router.post(
    "/platform/workspaces",
    response_model=ApiResponse[WorkspaceCreatedRead],
    status_code=201,
)
async def create_workspace(
    body: WorkspaceCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    if not identity.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform Admin required")
    if await db.scalar(select(Workspace.id).where(Workspace.slug == body.slug)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workspace slug already exists")

    first_admin = await _get_or_create_user(
        db,
        subject=body.first_admin_subject,
        email=body.first_admin_email,
        display_name=body.first_admin_display_name,
    )
    workspace = Workspace(name=body.name, slug=body.slug)
    db.add(workspace)
    await db.flush()
    db.add(StudioWorkspace(id=workspace.id, name=workspace.name, slug=workspace.slug))
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=first_admin.id,
        role=WorkspaceRole.ADMIN,
    )
    db.add(membership)
    await db.flush()
    return ApiResponse.ok(
        WorkspaceCreatedRead(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            active=workspace.active,
            first_admin=_member_read(first_admin, membership),
            created_at=workspace.created_at,
        )
    )


@router.patch(
    "/platform/workspaces/{workspace_id}",
    response_model=ApiResponse[WorkspaceRead],
)
async def update_workspace_status(
    workspace_id: str,
    body: WorkspaceStatusUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    if not identity.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform Admin required")
    workspace = await db.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    workspace.active = body.active
    await db.flush()
    return ApiResponse.ok(WorkspaceRead.model_validate(workspace))


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=ApiResponse[list[WorkspaceMemberRead]],
)
async def list_workspace_members(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    rows = (
        await db.execute(
            select(User, WorkspaceMembership)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .where(WorkspaceMembership.workspace_id == workspace_id)
            .order_by(User.subject)
        )
    ).all()
    return ApiResponse.ok([_member_read(row.User, row.WorkspaceMembership) for row in rows])


@router.post(
    "/workspaces/{workspace_id}/members",
    response_model=ApiResponse[WorkspaceMemberRead],
    status_code=201,
)
async def add_workspace_member(
    workspace_id: str,
    body: WorkspaceMemberCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_MEMBERS)
    if body.role == WorkspaceRole.ADMIN:
        require_permission(access, WorkspacePermission.MANAGE_ADMIN_ASSIGNMENTS)
    user = await _get_or_create_user(
        db,
        subject=body.subject,
        email=body.email,
        display_name=body.display_name,
    )
    existing = await db.scalar(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.workspace_id == workspace_id)
        .where(WorkspaceMembership.user_id == user.id)
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a Workspace member")
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=body.role,
    )
    db.add(membership)
    await db.flush()
    return ApiResponse.ok(_member_read(user, membership))


@router.patch(
    "/workspaces/{workspace_id}/members/{user_id}",
    response_model=ApiResponse[WorkspaceMemberRead],
)
async def update_workspace_member_role(
    workspace_id: str,
    user_id: str,
    body: WorkspaceMemberRoleUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_MEMBERS)
    # Serialize role changes within one Workspace so concurrent Admin
    # demotions cannot both observe a stale admin count and remove the last
    # administrator.
    await db.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    row = await db.execute(
        select(User, WorkspaceMembership)
        .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
        .where(User.id == user_id)
        .with_for_update()
    )
    result = row.one_or_none()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace member not found")
    membership = result.WorkspaceMembership
    if membership.role == WorkspaceRole.ADMIN or body.role == WorkspaceRole.ADMIN:
        require_permission(access, WorkspacePermission.MANAGE_ADMIN_ASSIGNMENTS)
    if membership.role == WorkspaceRole.ADMIN and body.role != WorkspaceRole.ADMIN:
        admin_count = await db.scalar(
            select(func.count())
            .select_from(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == workspace_id)
            .where(WorkspaceMembership.role == WorkspaceRole.ADMIN)
        )
        if (admin_count or 0) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace must retain an Admin")
    membership.role = body.role
    await db.flush()
    return ApiResponse.ok(_member_read(result.User, membership))
