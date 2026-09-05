"""Authorize a client-supplied Agent conversation as workflow run provenance."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent_conversation import AgentConversation, AgentConversationStatus
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import WorkspacePermission, require_permission
from backend.services.studio_agent_session_access import resolve_stored_agent_session_workspace
from backend.workflow.native_intelligence_contracts import WorkflowConversationOrigin


async def resolve_workflow_conversation_origin(
    db: AsyncSession,
    identity: RequestIdentity,
    *,
    conversation_id: str,
    studio_workspace_id: str,
    project_id: str,
    workflow_id: str,
) -> WorkflowConversationOrigin:
    """Reauthorize a persisted session and bind its exact Studio context."""

    conversation = await db.get(AgentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation not found")
    scope = await resolve_stored_agent_session_workspace(
        db,
        identity,
        workspace_id=conversation.workspace_id,
        context_binding=conversation.context_binding,
    )
    require_permission(scope.access, WorkspacePermission.READ)
    if scope.access.user_id != conversation.created_by_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent conversation owner required")
    if conversation.status != AgentConversationStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent conversation is closed")
    binding = conversation.context_binding if isinstance(conversation.context_binding, dict) else {}
    if (
        scope.studio_workspace_id != studio_workspace_id
        or binding.get("studio_workspace_id") != studio_workspace_id
        or binding.get("project_id") != project_id
        or binding.get("workflow_id") != workflow_id
        or binding.get("run_id") is not None
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Agent conversation is not bound to this workflow draft",
        )
    return WorkflowConversationOrigin(
        conversation_id=conversation.id,
        conversation_revision=conversation.revision,
        governed_workspace_id=conversation.workspace_id,
        studio_workspace_id=studio_workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        user_id=scope.access.user_id,
    )
