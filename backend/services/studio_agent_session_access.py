"""Authorization bridge for Agent sessions opened from local Studio projects.

Studio authoring still has a local-development workspace model, while durable
Agent conversations are deliberately stored under a governed Workspace with
membership-backed RBAC.  This module is the only bridge between those two
models.  It never creates membership in a Studio workspace and it only admits
the trusted local/bootstrap administrator when an explicit Studio object is
provided as the session context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control.agent_control import agent_control_service
from backend.models.identity import Workspace
from backend.models.studio import StudioWorkspace
from backend.security.identity import RequestIdentity, is_platform_admin
from backend.security.workspace_rbac import WorkspaceAccess, get_workspace_access

_STUDIO_ANCHOR_KEYS = frozenset({"project_id", "workflow_id", "run_id"})


@dataclass(frozen=True)
class AgentSessionWorkspaceScope:
    """The governed storage scope plus an optional Studio resource scope."""

    workspace_id: str
    access: WorkspaceAccess
    studio_workspace_id: str | None = None

    @property
    def is_studio_bridge(self) -> bool:
        return self.studio_workspace_id is not None


def _has_studio_anchor(context: dict[str, Any] | None) -> bool:
    if not isinstance(context, dict):
        return False
    return any(
        isinstance(context.get(key), str) and bool(context[key].strip())
        for key in _STUDIO_ANCHOR_KEYS
    )


def _is_trusted_local_studio_admin(identity: RequestIdentity) -> bool:
    """Do not turn a Studio URL into an authorization grant for OIDC users."""

    return identity.auth_method in {"local", "bootstrap"} and is_platform_admin(identity)


async def resolve_agent_session_workspace(
    db: AsyncSession,
    identity: RequestIdentity,
    requested_workspace_id: str | None,
    *,
    context: dict[str, Any] | None = None,
) -> AgentSessionWorkspaceScope:
    """Resolve normal governed access or the tightly constrained Studio bridge.

    A real governed workspace always takes precedence.  Therefore a caller
    who supplies a governed workspace id without membership gets the existing
    RBAC denial even if a Studio row happens to share that id.
    """

    if requested_workspace_id is None:
        workspace_id = await agent_control_service.resolve_workspace_id(db, identity, None)
        access = await get_workspace_access(db, workspace_id, identity)
        return AgentSessionWorkspaceScope(workspace_id=workspace_id, access=access)

    governed_workspace = await db.get(Workspace, requested_workspace_id)
    if governed_workspace is not None:
        access = await get_workspace_access(db, requested_workspace_id, identity)
        return AgentSessionWorkspaceScope(workspace_id=requested_workspace_id, access=access)

    studio_workspace = await db.get(StudioWorkspace, requested_workspace_id)
    if studio_workspace is None or not studio_workspace.active:
        # Keep the public error aligned with the existing governed endpoint.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace membership required")
    if not _is_trusted_local_studio_admin(identity):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Local platform administrator required for Studio Agent sessions",
        )
    if not _has_studio_anchor(context):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Studio Agent sessions require project, workflow, or run context",
        )

    # Conversations retain their FK and their RBAC boundary in a real governed
    # workspace.  Reuse only an unambiguous membership; never guess across
    # multiple governed workspaces.
    workspace_id = await agent_control_service.resolve_workspace_id(db, identity, None)
    access = await get_workspace_access(db, workspace_id, identity)
    return AgentSessionWorkspaceScope(
        workspace_id=workspace_id,
        access=access,
        studio_workspace_id=studio_workspace.id,
    )


async def resolve_stored_agent_session_workspace(
    db: AsyncSession,
    identity: RequestIdentity,
    *,
    workspace_id: str,
    context_binding: dict[str, Any] | None,
) -> AgentSessionWorkspaceScope:
    """Re-authorize a persisted conversation without trusting request input."""

    binding = context_binding or {}
    studio_workspace_id = binding.get("studio_workspace_id")
    if not isinstance(studio_workspace_id, str) or not studio_workspace_id:
        access = await get_workspace_access(db, workspace_id, identity)
        return AgentSessionWorkspaceScope(workspace_id=workspace_id, access=access)

    if not _is_trusted_local_studio_admin(identity):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Local platform administrator required for Studio Agent sessions",
        )
    studio_workspace = await db.get(StudioWorkspace, studio_workspace_id)
    if studio_workspace is None or not studio_workspace.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Studio Workspace is not available")
    access = await get_workspace_access(db, workspace_id, identity)
    return AgentSessionWorkspaceScope(
        workspace_id=workspace_id,
        access=access,
        studio_workspace_id=studio_workspace_id,
    )


def session_matches_studio_context(
    context_binding: dict[str, Any] | None,
    *,
    studio_workspace_id: str,
    context: dict[str, Any],
) -> bool:
    """Limit Studio session lists to the project/run context that was requested."""

    binding = context_binding or {}
    if binding.get("studio_workspace_id") != studio_workspace_id:
        return False
    return all(
        binding.get(key) == value
        for key, value in context.items()
        if key in _STUDIO_ANCHOR_KEYS and value is not None
    )
