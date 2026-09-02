"""Workspace-scoped role templates and membership checks."""

from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.security.identity import RequestIdentity


class WorkspacePermission(StrEnum):
    READ = "workspace.read"
    WORK_INBOX = "inbox.work"
    MANAGE_CONFIGURATION = "configuration.manage"
    MANAGE_MEMBERS = "members.manage"
    MANAGE_ADMIN_ASSIGNMENTS = "admins.manage"
    MANAGE_AGENT_IDENTITIES = "operations_agents.manage"
    MANAGE_CONSUMER_GRANTS = "consumer_grants.manage"
    RUN_OPERATIONS_AGENTS = "operations_agents.run"
    ASSIGN_AGENT_PROFILES = "operations_agents.profiles.assign"
    APPROVE_ACTIONS = "actions.approve"
    EXPORT_ANALYSIS = "analysis.export"
    MANAGE_HIGHEST_RISK_POLICY = "risk.highest.manage"


_ROLE_PERMISSIONS = {
    WorkspaceRole.ADMIN: frozenset(WorkspacePermission),
    WorkspaceRole.MAINTAINER: frozenset(
        {
            WorkspacePermission.READ,
            WorkspacePermission.WORK_INBOX,
            WorkspacePermission.MANAGE_CONFIGURATION,
            WorkspacePermission.MANAGE_MEMBERS,
            WorkspacePermission.MANAGE_AGENT_IDENTITIES,
            WorkspacePermission.MANAGE_CONSUMER_GRANTS,
            WorkspacePermission.RUN_OPERATIONS_AGENTS,
            WorkspacePermission.ASSIGN_AGENT_PROFILES,
            WorkspacePermission.APPROVE_ACTIONS,
            WorkspacePermission.EXPORT_ANALYSIS,
        }
    ),
    WorkspaceRole.OPERATOR: frozenset(
        {
            WorkspacePermission.READ,
            WorkspacePermission.WORK_INBOX,
            WorkspacePermission.RUN_OPERATIONS_AGENTS,
            WorkspacePermission.APPROVE_ACTIONS,
            WorkspacePermission.EXPORT_ANALYSIS,
        }
    ),
    WorkspaceRole.VIEWER: frozenset({WorkspacePermission.READ}),
}


@dataclass(frozen=True)
class WorkspaceAccess:
    user_id: str
    role: WorkspaceRole

    def allows(self, permission: WorkspacePermission) -> bool:
        return role_allows(self.role, permission)


def role_allows(role: WorkspaceRole, permission: WorkspacePermission) -> bool:
    return permission in _ROLE_PERMISSIONS[role]


async def get_workspace_access(
    db: AsyncSession,
    workspace_id: str,
    identity: RequestIdentity,
) -> WorkspaceAccess:
    row = await db.execute(
        select(WorkspaceMembership, User, Workspace)
        .join(User, User.id == WorkspaceMembership.user_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
        .where(User.subject == identity.subject)
    )
    result = row.one_or_none()
    if result is None or result.User.disabled or not result.Workspace.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace membership required")
    return WorkspaceAccess(user_id=result.User.id, role=result.WorkspaceMembership.role)


def require_permission(
    access: WorkspaceAccess,
    permission: WorkspacePermission,
) -> None:
    if not access.allows(permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace permission required")
