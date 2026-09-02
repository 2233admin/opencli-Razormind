import pytest
from fastapi import HTTPException

from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    role_allows,
)


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        (WorkspaceRole.ADMIN, WorkspacePermission.MANAGE_ADMIN_ASSIGNMENTS, True),
        (WorkspaceRole.MAINTAINER, WorkspacePermission.MANAGE_ADMIN_ASSIGNMENTS, False),
        (WorkspaceRole.MAINTAINER, WorkspacePermission.MANAGE_AGENT_IDENTITIES, True),
        (WorkspaceRole.OPERATOR, WorkspacePermission.RUN_OPERATIONS_AGENTS, True),
        (WorkspaceRole.OPERATOR, WorkspacePermission.EXPORT_ANALYSIS, True),
        (WorkspaceRole.OPERATOR, WorkspacePermission.ASSIGN_AGENT_PROFILES, False),
        (WorkspaceRole.VIEWER, WorkspacePermission.READ, True),
        (WorkspaceRole.VIEWER, WorkspacePermission.EXPORT_ANALYSIS, False),
        (WorkspaceRole.VIEWER, WorkspacePermission.WORK_INBOX, False),
    ],
)
def test_permission_templates_are_role_scoped(role, permission, allowed):
    assert role_allows(role, permission) is allowed


async def test_workspace_access_requires_membership_even_for_platform_admin(db_session):
    workspace = Workspace(name="Operations", slug="operations-rbac")
    db_session.add(workspace)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await get_workspace_access(
            db_session,
            workspace.id,
            RequestIdentity(subject="platform-admin", is_platform_admin=True),
        )

    assert exc_info.value.status_code == 403


async def test_disabled_user_cannot_use_workspace_membership(db_session):
    user = User(subject="disabled-user", disabled=True)
    workspace = Workspace(name="Operations", slug="operations-disabled")
    db_session.add_all((user, workspace))
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await get_workspace_access(
            db_session,
            workspace.id,
            RequestIdentity(subject=user.subject),
        )

    assert exc_info.value.status_code == 403
