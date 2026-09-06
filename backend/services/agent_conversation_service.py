"""Persistence and execution boundary for Global Agent conversations.

Conversation rows contain bounded, redacted continuity data only. Product state and
proposal execution remain owned by their existing services.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.v1 import chat
from backend.control.agent_control import agent_control_service  # noqa: F401
from backend.llm.base import LlmAdapterError
from backend.llm.resolver import ResolverError
from backend.models.agent_conversation import (
    AgentConversation,
    AgentConversationStatus,
    AgentConversationTurn,
    AgentConversationTurnStatus,
)
from backend.models.agent_run import AgentRun, AgentSession
from backend.models.edge_node import EdgeNode
from backend.models.identity import Workspace as GovernedWorkspace
from backend.models.operations_agent import (
    OperationsAgentIdentity,
    PublishedOperationsAgentVersion,
)
from backend.models.provider import ModelProvider
from backend.models.provider_model import ProviderModel
from backend.models.source_binding import Source, SourceBinding
from backend.models.studio import StudioProject, StudioWorkflow
from backend.models.workflow import Project as GovernedProject
from backend.models.workflow import Workflow as GovernedWorkflow
from backend.models.workflow_run import WorkflowRun
from backend.schemas.operations_agent import agent_runtime_binding_from_model_configuration
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    require_permission,
)
from backend.services import agent_conversation_run_service as run_service
from backend.services.studio_agent_session_access import (
    AgentSessionWorkspaceScope,
    resolve_agent_session_workspace,
    resolve_stored_agent_session_workspace,
    session_matches_studio_context,
)

MAX_USER_CONTENT = 20_000
MAX_HISTORY_TURNS = 20
MAX_HISTORY_CHARS = 32_000
MAX_ERROR_MESSAGE = 4_000
_ALLOWED_CONTEXT_KEYS = frozenset({"project_id", "workflow_id", "run_id", "source_id", "surface"})
_STORED_CONTEXT_KEYS = _ALLOWED_CONTEXT_KEYS | {"studio_workspace_id"}
_SECRET_PATTERN = re.compile(
    r"(?ix)(?:"
    r"(?:api[_ -]?key|access[_ -]?token|authorization|password|secret|credential|"
    r"connection[_ -]?string|token)\s*(?:[:=]|is)\s*(?:bearer\s+)?[^\s,;]+"
    r"|bearer\s+[^\s,;]+"
    r")"
)
_URL_PATTERN = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)


class AgentConversationError(ValueError):
    """Stable client-facing validation failure before a turn is written."""


async def _resolve_workspace_scope(
    db: AsyncSession,
    identity: RequestIdentity,
    workspace_id: str | None,
    *,
    context: dict[str, Any] | None,
) -> AgentSessionWorkspaceScope:
    """Keep the legacy ambiguous-membership response stable at this API edge."""

    try:
        return await resolve_agent_session_workspace(
            db,
            identity,
            workspace_id,
            context=context,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "workspace_id is required when the actor belongs to multiple workspaces",
            ) from exc
        raise


async def validate_context_binding(
    db: AsyncSession,
    workspace_id: str,
    context: dict[str, Any] | None,
    *,
    studio_workspace_id: str | None = None,
    allow_stored_studio_workspace: bool = False,
) -> dict[str, str]:
    """Validate object ownership and return an immutable, bounded snapshot."""

    context = context or {}
    if not isinstance(context, dict):
        raise AgentConversationError("context must be an object")
    allowed_keys = _STORED_CONTEXT_KEYS if allow_stored_studio_workspace else _ALLOWED_CONTEXT_KEYS
    unknown = set(context) - allowed_keys
    if unknown:
        raise AgentConversationError("context contains unsupported fields")

    normalized: dict[str, str] = {}
    for key in _ALLOWED_CONTEXT_KEYS:
        value = context.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 255:
            raise AgentConversationError(f"context.{key} must be a bounded non-empty string")
        normalized_value = value.strip()
        _reject_unsafe_content(normalized_value)
        normalized[key] = normalized_value
    project_id = normalized.get("project_id")
    workflow_id = normalized.get("workflow_id")
    run_id = normalized.get("run_id")
    source_id = normalized.get("source_id")
    stored_studio_workspace_id = context.get("studio_workspace_id")
    if allow_stored_studio_workspace and stored_studio_workspace_id is not None:
        if not isinstance(stored_studio_workspace_id, str) or not stored_studio_workspace_id:
            raise AgentConversationError("context.studio_workspace_id must be a non-empty string")
        if studio_workspace_id is not None and stored_studio_workspace_id != studio_workspace_id:
            raise AgentConversationError("Studio Workspace context cannot change")
        studio_workspace_id = stored_studio_workspace_id
    if studio_workspace_id is not None and not (project_id or workflow_id or run_id):
        raise AgentConversationError(
            "Studio Agent sessions require project, workflow, or run context"
        )

    studio_workspace_mapped = studio_workspace_id is not None
    if project_id or workflow_id or run_id:
        studio_workspace_mapped = studio_workspace_mapped or (
            await db.scalar(
                select(GovernedWorkspace.id).where(
                    GovernedWorkspace.id == workspace_id,
                    GovernedWorkspace.active.is_(True),
                )
            )
            is not None
        )

    project: GovernedProject | StudioProject | None = None
    studio_scope_id = studio_workspace_id or workspace_id
    if project_id:
        project = await db.scalar(
            select(GovernedProject).where(
                GovernedProject.id == project_id,
                GovernedProject.workspace_id == workspace_id,
            )
        )
        if project is None and studio_workspace_mapped:
            project = await db.scalar(
                select(StudioProject).where(
                    StudioProject.id == project_id,
                    StudioProject.workspace_id == studio_scope_id,
                )
            )
        if project is None:
            raise AgentConversationError("project is not owned by the Workspace")

    workflow: GovernedWorkflow | StudioWorkflow | None = None
    if workflow_id:
        workflow = await db.scalar(
            select(GovernedWorkflow)
            .join(GovernedProject, GovernedProject.id == GovernedWorkflow.project_id)
            .where(
                GovernedWorkflow.id == workflow_id,
                GovernedProject.workspace_id == workspace_id,
            )
        )
        if workflow is None and studio_workspace_mapped:
            workflow = await db.scalar(
                select(StudioWorkflow)
                .join(StudioProject, StudioProject.id == StudioWorkflow.project_id)
                .where(
                    StudioWorkflow.id == workflow_id,
                    StudioProject.workspace_id == studio_scope_id,
                )
            )
        if workflow is None:
            raise AgentConversationError("workflow is not owned by the Workspace")
        if project_id and workflow.project_id != project_id:
            raise AgentConversationError("workflow does not belong to project")

    if run_id:
        run = await db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id))
        if run is None:
            raise AgentConversationError("run is not available in the Workspace")
        if workflow_id and run.workflow_id != workflow_id:
            raise AgentConversationError("run does not belong to workflow")
        if workflow is None:
            workflow = await db.scalar(
                select(GovernedWorkflow)
                .join(GovernedProject, GovernedProject.id == GovernedWorkflow.project_id)
                .where(
                    GovernedWorkflow.id == run.workflow_id,
                    GovernedProject.workspace_id == workspace_id,
                )
            )
            if workflow is None and studio_workspace_mapped:
                workflow = await db.scalar(
                    select(StudioWorkflow)
                    .join(StudioProject, StudioProject.id == StudioWorkflow.project_id)
                    .where(
                        StudioWorkflow.id == run.workflow_id,
                        StudioProject.workspace_id == studio_scope_id,
                    )
                )
            if workflow is None:
                raise AgentConversationError("run is not owned by the Workspace")
        if project is None:
            project = await db.get(GovernedProject, workflow.project_id)
            if project is None and studio_workspace_mapped:
                project = await db.get(StudioProject, workflow.project_id)
        if project is None or project.workspace_id != studio_scope_id:
            raise AgentConversationError("run is not owned by the Workspace")

    if source_id:
        if studio_workspace_id is not None:
            raise AgentConversationError("source is not available in a Studio Agent session")
        source = await db.scalar(
            select(Source).where(Source.id == source_id, Source.workspace_id == workspace_id)
        )
        if source is None:
            # A SourceBinding is also a valid proof of Workspace ownership when
            # callers identify the project-scoped binding rather than the source.
            source = await db.scalar(
                select(Source)
                .join(SourceBinding, SourceBinding.source_id == Source.id)
                .join(GovernedProject, GovernedProject.id == SourceBinding.project_id)
                .where(
                    Source.id == source_id,
                    GovernedProject.workspace_id == workspace_id,
                )
            )
        if source is None:
            raise AgentConversationError("source is not owned by the Workspace")

    if studio_workspace_id is not None:
        normalized["studio_workspace_id"] = studio_workspace_id
    return dict(normalized)


def _reject_unsafe_content(content: str) -> None:
    if not content.strip():
        raise AgentConversationError("content must not be empty")
    if len(content) > MAX_USER_CONTENT:
        raise AgentConversationError("content exceeds the 20000 character limit")
    if _SECRET_PATTERN.search(content):
        raise AgentConversationError("content contains a credential-like value")


def _redact_error(value: str) -> str:
    value = _SECRET_PATTERN.sub("[REDACTED]", value)
    return _URL_PATTERN.sub("[REDACTED_URL]", value)[:MAX_ERROR_MESSAGE]


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key|access[_ -]?token|authorization|bearer|password|"
    r"secret|credential|connection|token|cookie|header|endpoint|profile|html|url)"
)


def _redact_json(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            name: "[REDACTED]"
            if _SENSITIVE_KEY_PATTERN.search(name)
            else _redact_json(item, key=name)
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item, key=key) for item in value]
    if isinstance(value, str):
        return _redact_error(value)
    return value


def _ensure_studio_context_continuity(
    previous: dict[str, Any] | None,
    next_binding: dict[str, str],
) -> None:
    """A Studio session may refine its context, but never switch its target."""

    for key in ("project_id", "workflow_id", "run_id"):
        previous_value = (previous or {}).get(key)
        next_value = next_binding.get(key)
        if previous_value is not None and previous_value != next_value:
            raise AgentConversationError("Studio Agent session context cannot switch projects")


def _is_studio_session(conversation: AgentConversation) -> bool:
    binding = conversation.context_binding
    return isinstance(binding, dict) and bool(binding.get("studio_workspace_id"))


def _assistant_history(response: dict[str, Any] | None) -> str:
    if not response:
        return ""
    if response.get("type") == "message":
        content = response.get("content")
        return content if isinstance(content, str) else ""
    proposal = response.get("proposal")
    if isinstance(proposal, dict):
        summary = proposal.get("summary")
        return summary if isinstance(summary, str) else ""
    return ""


def bounded_history(
    turns: list[AgentConversationTurn], current_content: str
) -> list[dict[str, str]]:
    """Return complete history or fail explicitly; never silently truncate it."""

    pairs: list[tuple[str, str]] = []
    for turn in turns:
        assistant = _assistant_history(turn.response)
        if assistant:
            pairs.append((turn.user_content, assistant))

    total_chars = sum(len(user) + len(assistant) for user, assistant in pairs) + len(
        current_content
    )
    if len(pairs) > MAX_HISTORY_TURNS or total_chars > MAX_HISTORY_CHARS:
        raise AgentConversationError(
            "conversation history exceeds the continuation limit; start a new session"
        )

    messages: list[dict[str, str]] = []
    for user, assistant in pairs:
        messages.extend(
            ({"role": "user", "content": user}, {"role": "assistant", "content": assistant})
        )
    messages.append({"role": "user", "content": current_content})
    return messages


def _safe_response(reply: chat.ChatReply) -> dict[str, Any]:
    """Persist only the public reply shape, never an SDK/model message."""

    response: dict[str, Any] = {"type": reply.type}
    if reply.content is not None:
        response["content"] = _redact_error(reply.content[:MAX_USER_CONTENT])
    if reply.proposal is not None:
        response["proposal"] = _redact_json(reply.proposal.model_dump(exclude_none=True))
    return response


async def list_execution_targets(
    db: AsyncSession,
    identity: RequestIdentity,
    *,
    workspace_id: str | None,
    context: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Project the selectable execution plane without credentials or node URLs."""

    scope = await _resolve_workspace_scope(db, identity, workspace_id, context=context)
    require_permission(scope.access, WorkspacePermission.READ)

    providers = list(
        await db.scalars(
            select(ModelProvider)
            .where(ModelProvider.enabled.is_(True))
            .order_by(ModelProvider.created_at.asc())
        )
    )
    provider_ids = [provider.id for provider in providers]
    model_rows = (
        list(
            await db.scalars(
                select(ProviderModel)
                .where(
                    ProviderModel.provider_id.in_(provider_ids),
                    ProviderModel.enabled.is_(True),
                    ProviderModel.model_type == "llm",
                )
                .order_by(ProviderModel.provider_id, ProviderModel.model_id)
            )
        )
        if provider_ids
        else []
    )
    models_by_provider: dict[str, list[str]] = {provider_id: [] for provider_id in provider_ids}
    for model in model_rows:
        models_by_provider.setdefault(model.provider_id, []).append(model.model_id)

    targets: list[dict[str, Any]] = []
    for provider in providers:
        model_ids = models_by_provider.get(provider.id, [])
        if not model_ids:
            continue
        default_model_id = (
            provider.default_model if provider.default_model in model_ids else model_ids[0]
        )
        targets.append(
            {
                "id": f"provider:{provider.id}",
                "kind": "provider",
                "label": provider.name,
                "agent": {"type": "model_provider", "name": provider.name},
                "runtime": None,
                "provider": {
                    "id": provider.id,
                    "name": provider.name,
                    "provider_type": provider.provider_type,
                },
                "models": [{"id": model_id, "label": model_id} for model_id in model_ids],
                "default_model_id": default_model_id,
                "readiness": {
                    "status": "unverified",
                    "reason_code": "connection_not_probed",
                    "reason": "Provider is enabled but has not been probed by this request.",
                },
                "setup_url": "/providers",
            }
        )

    from backend import ws_agent_manager

    agents = list(
        await db.scalars(
            select(OperationsAgentIdentity)
            .where(
                OperationsAgentIdentity.workspace_id == scope.workspace_id,
                OperationsAgentIdentity.disabled.is_(False),
                OperationsAgentIdentity.current_published_version.is_not(None),
            )
            .order_by(OperationsAgentIdentity.created_at.asc())
        )
    )
    for agent in agents:
        version = await db.scalar(
            select(PublishedOperationsAgentVersion).where(
                PublishedOperationsAgentVersion.operations_agent_id == agent.id,
                PublishedOperationsAgentVersion.version == agent.current_published_version,
            )
        )
        if version is None:
            continue
        try:
            runtime_binding = agent_runtime_binding_from_model_configuration(
                version.model_configuration
            )
        except Exception:
            runtime_binding = None
        if runtime_binding is None:
            continue
        node = await db.scalar(select(EdgeNode).where(EdgeNode.url == runtime_binding.agent_url))
        advertised = (
            (node.runtime_capabilities or {}).get(runtime_binding.runtime, []) if node else []
        )
        capabilities = sorted({item for item in advertised if isinstance(item, str) and item})
        node_connected = bool(
            node
            and node.status == "online"
            and node.protocol == "ws"
            and ws_agent_manager.is_connected(node.url)
        )
        reason_code = (
            "native_session_contract_unavailable" if node_connected else "runtime_node_unavailable"
        )
        targets.append(
            {
                "id": f"native:{agent.id}",
                "kind": "native",
                "label": f"{agent.name} · {runtime_binding.runtime}",
                "agent": {"type": "operations_agent", "name": agent.name},
                "runtime": {
                    "agent_id": agent.id,
                    "name": runtime_binding.runtime,
                    "capabilities": capabilities,
                    "resume_by_id": "resumable" in capabilities,
                },
                "provider": None,
                "models": [],
                "default_model_id": None,
                "readiness": {
                    "status": "blocked",
                    "reason_code": reason_code,
                    "reason": (
                        "The published Agent binding is not connected."
                        if not node_connected
                        else (
                            "The Agent binding lacks trusted authentication, isolation, "
                            "and resumable-session readiness evidence."
                        )
                    ),
                },
                "setup_url": "/operations-agents",
            }
        )
    return scope.workspace_id, targets


async def resolve_execution_binding(
    db: AsyncSession,
    *,
    execution_target_id: str | None,
    model_id: str | None,
    allow_unbound: bool = False,
) -> dict[str, Any]:
    if execution_target_id is None:
        if model_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "model_id requires execution_target_id",
            )
        return {} if allow_unbound else await _default_provider_binding(db)
    if execution_target_id.startswith("native:"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "native_runtime_blocked",
                "message": "Native runtime is not ready for governed chat execution.",
                "setup_url": "/nodes",
            },
        )
    prefix, separator, provider_id = execution_target_id.partition(":")
    if prefix != "provider" or not separator or not provider_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid execution target")
    provider = await db.get(ModelProvider, provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "selected provider is unavailable")
    selected_model = model_id or provider.default_model
    model = (
        await db.scalar(
            select(ProviderModel).where(
                ProviderModel.provider_id == provider.id,
                ProviderModel.model_id == selected_model,
                ProviderModel.model_type == "llm",
                ProviderModel.enabled.is_(True),
            )
        )
        if isinstance(selected_model, str) and selected_model
        else None
    )
    if model is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "provider_required",
                "message": "Selected provider has no enabled model catalog pair.",
                "setup_url": "/providers",
            },
        )
    return {
        "schema_version": "chat.execution-binding.v1",
        "kind": "provider",
        "target_id": execution_target_id,
        "provider_id": provider.id,
        "model_id": selected_model,
        "catalog_verified": True,
    }


async def _default_provider_binding(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ModelProvider, ProviderModel)
            .join(ProviderModel, ProviderModel.provider_id == ModelProvider.id)
            .where(
                ModelProvider.enabled.is_(True),
                ProviderModel.enabled.is_(True),
                ProviderModel.model_type == "llm",
            )
            .order_by(ModelProvider.created_at.asc(), ProviderModel.model_id.asc())
        )
    ).first()
    if row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "provider_required",
                "message": "No enabled provider is available for this conversation.",
                "setup_url": "/providers",
            },
        )
    provider, model = row
    preferred_model = provider.default_model
    if preferred_model:
        preferred = await db.scalar(
            select(ProviderModel).where(
                ProviderModel.provider_id == provider.id,
                ProviderModel.model_id == preferred_model,
                ProviderModel.enabled.is_(True),
                ProviderModel.model_type == "llm",
            )
        )
        if preferred is not None:
            model = preferred
    return {
        "schema_version": "chat.execution-binding.v1",
        "kind": "provider",
        "target_id": f"provider:{provider.id}",
        "provider_id": provider.id,
        "model_id": model.model_id,
        "catalog_verified": True,
    }


async def _ensure_execution_binding(
    db: AsyncSession,
    conversation: AgentConversation,
    *,
    allow_legacy_default: bool = False,
) -> dict[str, Any]:
    binding = conversation.execution_binding
    if isinstance(binding, dict) and binding.get("kind") == "provider":
        provider_id = binding.get("provider_id")
        provider = (
            await db.get(ModelProvider, provider_id) if isinstance(provider_id, str) else None
        )
        if provider is None or not provider.enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "pinned provider is unavailable")
        model_id = binding.get("model_id")
        if binding.get("catalog_verified") is False:
            current_legacy_model = provider.default_model or "gpt-4o-mini"
            if (
                allow_legacy_default
                and model_id is None
                and binding.get("legacy_effective_model") == current_legacy_model
            ):
                return dict(binding)
            raise HTTPException(status.HTTP_409_CONFLICT, "pinned model catalog is unavailable")
        model = (
            await db.scalar(
                select(ProviderModel).where(
                    ProviderModel.provider_id == provider.id,
                    ProviderModel.model_id == model_id,
                    ProviderModel.model_type == "llm",
                    ProviderModel.enabled.is_(True),
                )
            )
            if isinstance(model_id, str) and model_id
            else None
        )
        if model is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "pinned model is unavailable")
        return dict(binding)
    if isinstance(binding, dict) and binding.get("kind") == "native":
        raise HTTPException(status.HTTP_409_CONFLICT, "pinned native runtime is blocked")
    if allow_legacy_default:
        provider = await db.scalar(
            select(ModelProvider)
            .where(ModelProvider.enabled.is_(True))
            .order_by(ModelProvider.created_at.asc())
        )
        if provider is not None:
            resolved = {
                "schema_version": "chat.execution-binding.v1",
                "kind": "provider",
                "target_id": f"provider:{provider.id}",
                "provider_id": provider.id,
                "model_id": None,
                "catalog_verified": False,
                "legacy_effective_model": provider.default_model or "gpt-4o-mini",
            }
            conversation.execution_binding = resolved
            return resolved
    resolved = await _default_provider_binding(db)
    conversation.execution_binding = resolved
    return resolved


async def create_conversation(
    db: AsyncSession,
    identity: RequestIdentity,
    *,
    workspace_id: str | None,
    title: str | None,
    context: dict[str, Any] | None,
    execution_target_id: str | None = None,
    model_id: str | None = None,
) -> AgentConversation:
    scope = await _resolve_workspace_scope(db, identity, workspace_id, context=context)
    require_permission(scope.access, WorkspacePermission.READ)
    title_value = title.strip() if title else None
    try:
        binding = await validate_context_binding(
            db,
            scope.workspace_id,
            context,
            studio_workspace_id=scope.studio_workspace_id,
        )
        if title_value:
            _reject_unsafe_content(title_value)
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    execution_binding = await resolve_execution_binding(
        db,
        execution_target_id=execution_target_id,
        model_id=model_id,
        allow_unbound=execution_target_id is None,
    )
    conversation = AgentConversation(
        workspace_id=scope.workspace_id,
        title=title_value,
        created_by_user_id=scope.access.user_id,
        context_binding=binding,
        execution_binding=execution_binding,
        status=AgentConversationStatus.ACTIVE.value,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def list_conversations(
    db: AsyncSession,
    identity: RequestIdentity,
    *,
    workspace_id: str | None,
    limit: int,
    context: dict[str, Any] | None = None,
    include_project_sessions: bool = False,
) -> list[AgentConversation]:
    scope = await _resolve_workspace_scope(db, identity, workspace_id, context=context)
    require_permission(scope.access, WorkspacePermission.READ)
    rows = await db.scalars(
        select(AgentConversation)
        .where(AgentConversation.workspace_id == scope.workspace_id)
        .order_by(AgentConversation.updated_at.desc())
        .limit(50 if scope.is_studio_bridge or include_project_sessions else limit)
    )
    conversations = list(rows)
    if include_project_sessions:
        authorized: list[AgentConversation] = []
        for conversation in conversations:
            try:
                stored_scope = await resolve_stored_agent_session_workspace(
                    db,
                    identity,
                    workspace_id=conversation.workspace_id,
                    context_binding=conversation.context_binding,
                )
                require_permission(stored_scope.access, WorkspacePermission.READ)
                await validate_context_binding(
                    db,
                    stored_scope.workspace_id,
                    conversation.context_binding,
                    studio_workspace_id=stored_scope.studio_workspace_id,
                    allow_stored_studio_workspace=True,
                )
            except (HTTPException, AgentConversationError):
                continue
            authorized.append(conversation)
            if len(authorized) >= limit:
                break
        return authorized
    if not scope.is_studio_bridge:
        # Studio sessions share governed storage for the FK/RBAC boundary, but
        # their project context must never leak into ordinary workspace lists.
        return [
            conversation for conversation in conversations if not _is_studio_session(conversation)
        ]
    assert scope.studio_workspace_id is not None
    return [
        row
        for row in conversations
        if session_matches_studio_context(
            row.context_binding,
            studio_workspace_id=scope.studio_workspace_id,
            context=context or {},
        )
    ][:limit]


async def get_conversation(
    db: AsyncSession,
    identity: RequestIdentity,
    conversation_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 50,
    for_update: bool = False,
) -> tuple[AgentConversation, list[AgentConversationTurn]]:
    statement = select(AgentConversation).where(AgentConversation.id == conversation_id)
    if for_update:
        statement = statement.with_for_update()
    conversation = await db.scalar(statement)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation not found")
    scope = await resolve_stored_agent_session_workspace(
        db,
        identity,
        workspace_id=conversation.workspace_id,
        context_binding=conversation.context_binding,
    )
    require_permission(scope.access, WorkspacePermission.READ)
    try:
        await validate_context_binding(
            db,
            scope.workspace_id,
            conversation.context_binding,
            studio_workspace_id=scope.studio_workspace_id,
            allow_stored_studio_workspace=True,
        )
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    turns = await db.scalars(
        select(AgentConversationTurn)
        .where(
            AgentConversationTurn.conversation_id == conversation_id,
            AgentConversationTurn.sequence > after_sequence,
        )
        .order_by(AgentConversationTurn.sequence.asc())
        .limit(limit)
    )
    return conversation, list(turns)


async def close_conversation(
    db: AsyncSession, identity: RequestIdentity, conversation_id: str
) -> AgentConversation:
    conversation, _ = await get_conversation(
        db, identity, conversation_id, limit=1, for_update=True
    )
    scope = await resolve_stored_agent_session_workspace(
        db,
        identity,
        workspace_id=conversation.workspace_id,
        context_binding=conversation.context_binding,
    )
    if conversation.created_by_user_id != scope.access.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the conversation creator can close it")
    active = await db.scalar(
        select(AgentConversationTurn.id).where(
            AgentConversationTurn.conversation_id == conversation.id,
            AgentConversationTurn.active_slot == conversation.id,
        )
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn is still active")
    if conversation.status != AgentConversationStatus.CLOSED.value:
        conversation.status = AgentConversationStatus.CLOSED.value
        conversation.revision += 1
        await db.commit()
        await db.refresh(conversation)
    return conversation


async def reopen_conversation(
    db: AsyncSession, identity: RequestIdentity, conversation_id: str
) -> AgentConversation:
    conversation, _ = await get_conversation(
        db, identity, conversation_id, limit=1, for_update=True
    )
    scope = await resolve_stored_agent_session_workspace(
        db,
        identity,
        workspace_id=conversation.workspace_id,
        context_binding=conversation.context_binding,
    )
    if conversation.created_by_user_id != scope.access.user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the conversation creator can reopen it"
        )
    active = await db.scalar(
        select(AgentConversationTurn.id).where(
            AgentConversationTurn.conversation_id == conversation.id,
            AgentConversationTurn.active_slot == conversation.id,
        )
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn is still active")
    if conversation.status != AgentConversationStatus.ACTIVE.value:
        conversation.status = AgentConversationStatus.ACTIVE.value
        conversation.revision += 1
        await db.commit()
        await db.refresh(conversation)
    return conversation


async def _insert_turn(
    db: AsyncSession,
    conversation: AgentConversation,
    request_id: str,
    content: str,
    binding: dict[str, str],
    initial_status: str,
    identity: RequestIdentity | None = None,
) -> tuple[AgentConversationTurn | None, AgentConversationTurn | None]:
    """Insert once; return (new_turn, existing_turn) under duplicate races."""

    for _ in range(2):
        sequence = (
            await db.scalar(
                select(func.max(AgentConversationTurn.sequence)).where(
                    AgentConversationTurn.conversation_id == conversation.id
                )
            )
            or 0
        ) + 1
        turn = AgentConversationTurn(
            conversation_id=conversation.id,
            workspace_id=conversation.workspace_id,
            sequence=sequence,
            request_id=request_id,
            active_slot=conversation.id,
            user_content=content,
            context_binding=binding,
            tool_trace=[],
            status=initial_status,
        )
        db.add(turn)
        try:
            await db.flush()
            if identity is not None:
                await run_service.ensure_run(
                    db,
                    conversation=conversation,
                    turn=turn,
                    identity=identity,
                    commit=False,
                )
            await db.commit()
            await db.refresh(turn)
            return turn, None
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(AgentConversationTurn).where(
                    AgentConversationTurn.conversation_id == conversation.id,
                    AgentConversationTurn.request_id == request_id,
                )
            )
            if existing is not None:
                return None, existing
            active = await db.scalar(
                select(AgentConversationTurn).where(
                    AgentConversationTurn.active_slot == conversation.id
                )
            )
            if active is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "another conversation turn is already queued or running",
                )
    raise HTTPException(status.HTTP_409_CONFLICT, "Could not allocate conversation turn")


async def _model_session(db: AsyncSession) -> AsyncSession:
    bind = db.bind
    if bind is None:
        raise RuntimeError("conversation database session has no bind")
    return async_sessionmaker(bind=bind, expire_on_commit=False)()


class ConnectorConversationAccessProtocol(Protocol):
    @property
    def conversation_id(self) -> str: ...

    @property
    def actor_identity(self) -> RequestIdentity: ...

    @property
    def context_binding(self) -> dict[str, str]: ...

    @property
    def expected_revision(self) -> int: ...

    def assert_sealed(self) -> None: ...

    async def owns_execution_fence(self, db: AsyncSession) -> bool: ...

    async def mark_turn_failed(
        self,
        db: AsyncSession,
        *,
        turn_id: str,
        code: str,
        error_message: str,
    ) -> bool: ...

    async def reauthorize_for_finalize(self, db: AsyncSession) -> None: ...

    async def advance_revision_cursor(self, db: AsyncSession, *, next_revision: int) -> bool: ...


async def _mark_connector_turn_failed(
    db: AsyncSession,
    access: ConnectorConversationAccessProtocol,
    turn_id: str,
    *,
    code: str,
    message: str,
    execution_fence_lost: Callable[[], bool] | None = None,
) -> None:
    await db.rollback()
    if execution_fence_lost is not None and execution_fence_lost():
        return
    async with db.begin():
        if execution_fence_lost is not None and execution_fence_lost():
            return
        await access.mark_turn_failed(
            db,
            turn_id=turn_id,
            code=code,
            error_message=_redact_error(message),
        )
        failed_turn = await db.get(AgentConversationTurn, turn_id)
        if failed_turn is not None:
            failed_turn.active_slot = None


async def send_connector_message(
    db: AsyncSession,
    access: ConnectorConversationAccessProtocol,
    *,
    request_id: str,
    content: str,
    chat_runner: Callable[..., Any] | None = None,
    execution_fence_lost: Callable[[], bool] | None = None,
) -> tuple[AgentConversation, AgentConversationTurn]:
    """Run one connector turn using a sealed, freshly authorized capability."""

    access.assert_sealed()
    if not request_id.strip() or len(request_id) > 64:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "request_id must be 1..64 characters"
        )
    try:
        _reject_unsafe_content(content)
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    conversation = await db.scalar(
        select(AgentConversation)
        .where(AgentConversation.id == access.conversation_id)
        .with_for_update()
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation not found")
    if conversation.status != AgentConversationStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent conversation is closed")
    if conversation.revision != access.expected_revision:
        raise HTTPException(status.HTTP_409_CONFLICT, "conversation_revision_changed")
    try:
        studio_workspace_id = access.context_binding.get("studio_workspace_id")
        binding = await validate_context_binding(
            db,
            conversation.workspace_id,
            access.context_binding,
            studio_workspace_id=studio_workspace_id,
            allow_stored_studio_workspace=True,
        )
        _ensure_studio_context_continuity(conversation.context_binding, binding)
        if binding != conversation.context_binding:
            raise AgentConversationError("Connector conversation context changed")
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    existing = await db.scalar(
        select(AgentConversationTurn).where(
            AgentConversationTurn.conversation_id == conversation.id,
            AgentConversationTurn.request_id == request_id,
        )
    )
    if existing is not None:
        if existing.status == AgentConversationTurnStatus.RUNNING.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn is already running")
        if existing.status == AgentConversationTurnStatus.FAILED.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn previously failed")
        return conversation, existing
    history_rows = list(
        await db.scalars(
            select(AgentConversationTurn)
            .where(
                AgentConversationTurn.conversation_id == conversation.id,
                AgentConversationTurn.status.in_(
                    (
                        AgentConversationTurnStatus.COMPLETED.value,
                        AgentConversationTurnStatus.PROPOSAL.value,
                    )
                ),
            )
            .order_by(AgentConversationTurn.sequence.asc())
        )
    )
    try:
        messages = bounded_history(history_rows, content)
    except AgentConversationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "history_limit_exceeded",
                "message": str(exc),
                "recovery": "start_new_session",
            },
        ) from exc
    turn, raced = await _insert_turn(
        db,
        conversation,
        request_id,
        content,
        binding,
        AgentConversationTurnStatus.RUNNING.value,
    )
    if raced is not None:
        if raced.status in {
            AgentConversationTurnStatus.RUNNING.value,
            AgentConversationTurnStatus.FAILED.value,
        }:
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn is unavailable")
        return conversation, raced
    assert turn is not None
    turn_id = turn.id
    conversation_id = conversation.id
    workspace_id = conversation.workspace_id
    body = chat.ChatRequest(
        messages=messages,
        workspace_id=workspace_id,
        context=binding,
    )
    # _insert_turn commits and refreshes. End the refresh transaction
    # before any model network wait; finalization reloads every mutable row.
    await db.rollback()
    trace: list[dict[str, Any]] = []
    model_db = await _model_session(db)
    try:
        runner = chat_runner or chat.run_chat_request
        result = await runner(
            model_db,
            body,
            access.actor_identity,
            tool_trace=trace,
            proposal_provenance=chat.ProposalProvenance(
                conversation_id=conversation_id,
                turn_id=turn_id,
                context=binding,
            ),
        )
        reply = result.data if isinstance(result, chat.ApiResponse) else result
        if not isinstance(reply, chat.ChatReply):
            raise RuntimeError("chat runner returned an invalid reply")
        if reply.type == "proposal":
            proposal = reply.proposal
            if (
                proposal is None
                or proposal.workspace_id != workspace_id
                or not proposal.work_item_id
                or not proposal.proposal_version
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Agent proposal is not bound to the conversation Workspace",
                )
        await model_db.commit()
        if execution_fence_lost is not None and execution_fence_lost():
            raise HTTPException(status.HTTP_409_CONFLICT, "connector_lease_lost")
        await db.rollback()
        async with db.begin():
            fresh_conversation = await db.scalar(
                select(AgentConversation)
                .where(AgentConversation.id == conversation_id)
                .with_for_update()
            )
            fresh_turn = await db.scalar(
                select(AgentConversationTurn)
                .where(AgentConversationTurn.id == turn_id)
                .with_for_update()
            )
            if (
                fresh_conversation is None
                or fresh_turn is None
                or fresh_conversation.status != AgentConversationStatus.ACTIVE.value
                or fresh_conversation.revision != access.expected_revision
                or fresh_conversation.context_binding != binding
                or fresh_turn.status != AgentConversationTurnStatus.RUNNING.value
                or (execution_fence_lost is not None and execution_fence_lost())
            ):
                raise HTTPException(status.HTTP_409_CONFLICT, "conversation_revision_changed")
            await access.reauthorize_for_finalize(db)
            next_revision = access.expected_revision + 1
            if not await access.advance_revision_cursor(db, next_revision=next_revision):
                raise HTTPException(status.HTTP_409_CONFLICT, "connector_lease_lost")
            fresh_turn.response = _safe_response(reply)
            fresh_turn.tool_trace = trace
            fresh_turn.status = (
                AgentConversationTurnStatus.PROPOSAL.value
                if reply.type == "proposal"
                else AgentConversationTurnStatus.COMPLETED.value
            )
            fresh_turn.active_slot = None
            fresh_conversation.revision = next_revision
        return fresh_conversation, fresh_turn
    except (LlmAdapterError, ResolverError) as exc:
        await model_db.rollback()
        await _mark_connector_turn_failed(
            db,
            access,
            turn_id,
            code=(
                "model_unavailable"
                if isinstance(exc, LlmAdapterError) and exc.retryable
                else "model_error"
            ),
            message=str(exc),
            execution_fence_lost=execution_fence_lost,
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型调用失败") from exc
    except HTTPException:
        await model_db.rollback()
        await _mark_connector_turn_failed(
            db,
            access,
            turn_id,
            code="connector_authorization_changed",
            message="connector turn rejected",
            execution_fence_lost=execution_fence_lost,
        )
        raise
    except Exception as exc:
        await model_db.rollback()
        await _mark_connector_turn_failed(
            db,
            access,
            turn_id,
            code="model_error",
            message=str(exc),
            execution_fence_lost=execution_fence_lost,
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型调用失败") from exc
    finally:
        await model_db.close()


async def send_message(
    db: AsyncSession,
    identity: RequestIdentity,
    conversation_id: str,
    *,
    request_id: str,
    content: str,
    context: dict[str, Any] | None,
    chat_runner: Callable[..., Any] | None = None,
) -> tuple[AgentConversation, AgentConversationTurn]:
    conversation, turn, messages, existing = await _prepare_message(
        db,
        identity,
        conversation_id,
        request_id=request_id,
        content=content,
        context=context,
        initial_status=AgentConversationTurnStatus.RUNNING.value,
        allow_legacy_default=True,
    )
    if existing:
        return conversation, turn
    run = await run_service.ensure_run(
        db,
        conversation=conversation,
        turn=turn,
        identity=identity,
    )
    await _execute_turn(
        db,
        identity,
        conversation=conversation,
        turn=turn,
        run=run,
        messages=messages,
        chat_runner=chat_runner,
    )
    await db.refresh(turn)
    await db.refresh(conversation)
    return conversation, turn


async def queue_message(
    db: AsyncSession,
    identity: RequestIdentity,
    conversation_id: str,
    *,
    request_id: str,
    content: str,
    context: dict[str, Any] | None,
) -> tuple[AgentConversation, AgentConversationTurn, Any]:
    conversation, turn, _messages, existing = await _prepare_message(
        db,
        identity,
        conversation_id,
        request_id=request_id,
        content=content,
        context=context,
        initial_status=AgentConversationTurnStatus.QUEUED.value,
    )
    run = await run_service.ensure_run(
        db,
        conversation=conversation,
        turn=turn,
        identity=identity,
    )
    if not existing:
        run_service.schedule(run.id, identity)
    return conversation, turn, run


async def _prepare_message(
    db: AsyncSession,
    identity: RequestIdentity,
    conversation_id: str,
    *,
    request_id: str,
    content: str,
    context: dict[str, Any] | None,
    initial_status: str,
    allow_legacy_default: bool = False,
) -> tuple[AgentConversation, AgentConversationTurn, list[dict[str, str]], bool]:
    if not request_id.strip() or len(request_id) > 64:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "request_id must be 1..64 characters"
        )
    try:
        _reject_unsafe_content(content)
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    conversation = await db.scalar(
        select(AgentConversation).where(AgentConversation.id == conversation_id).with_for_update()
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation not found")
    scope = await resolve_stored_agent_session_workspace(
        db,
        identity,
        workspace_id=conversation.workspace_id,
        context_binding=conversation.context_binding,
    )
    require_permission(scope.access, WorkspacePermission.READ)
    if conversation.created_by_user_id != scope.access.user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the conversation creator can send messages to this session",
        )
    existing = await db.scalar(
        select(AgentConversationTurn).where(
            AgentConversationTurn.conversation_id == conversation_id,
            AgentConversationTurn.request_id == request_id,
        )
    )
    if existing is not None:
        if existing.status in {
            AgentConversationTurnStatus.FAILED.value,
            AgentConversationTurnStatus.INTERRUPTED.value,
        }:
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn previously failed")
        return conversation, existing, [], True
    if conversation.status != AgentConversationStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent conversation is closed")

    try:
        binding = await validate_context_binding(
            db,
            scope.workspace_id,
            context if context is not None else conversation.context_binding,
            studio_workspace_id=scope.studio_workspace_id,
            allow_stored_studio_workspace=context is None,
        )
        if scope.is_studio_bridge:
            _ensure_studio_context_continuity(conversation.context_binding, binding)
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _ensure_execution_binding(db, conversation, allow_legacy_default=allow_legacy_default)
    active = await db.scalar(
        select(AgentConversationTurn).where(AgentConversationTurn.active_slot == conversation_id)
    )
    if active is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "another conversation turn is already queued or running",
        )
    history_rows = list(
        await db.scalars(
            select(AgentConversationTurn)
            .where(
                AgentConversationTurn.conversation_id == conversation_id,
                AgentConversationTurn.status.in_(
                    (
                        AgentConversationTurnStatus.COMPLETED.value,
                        AgentConversationTurnStatus.PROPOSAL.value,
                    )
                ),
            )
            .order_by(AgentConversationTurn.sequence.asc())
        )
    )
    try:
        messages = bounded_history(history_rows, content)
    except AgentConversationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "history_limit_exceeded",
                "message": str(exc),
                "recovery": "start_new_session",
            },
        ) from exc
    turn, existing = await _insert_turn(
        db,
        conversation,
        request_id,
        content,
        binding,
        initial_status,
        identity,
    )
    if existing is not None:
        if existing.status in {
            AgentConversationTurnStatus.FAILED.value,
            AgentConversationTurnStatus.INTERRUPTED.value,
        }:
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation turn previously failed")
        return conversation, existing, [], True
    assert turn is not None
    return conversation, turn, messages, False


async def execute_run(run_id: str, identity: RequestIdentity) -> None:
    async with run_service.AsyncSessionLocal() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.status != "queued":
            return
        payload = run.request_payload if isinstance(run.request_payload, dict) else {}
        conversation_id = payload.get("conversation_id")
        turn_id = payload.get("turn_id")
        if not isinstance(conversation_id, str) or not isinstance(turn_id, str):
            await run_service.finish_run(db, run=run, error="Agent run binding is invalid")
            return
        conversation = await db.get(AgentConversation, conversation_id)
        turn = await db.get(AgentConversationTurn, turn_id)
        agent_session = await db.get(AgentSession, run.session_id)
        if (
            conversation is None
            or turn is None
            or agent_session is None
            or turn.agent_run_id != run.id
            or turn.conversation_id != conversation.id
            or turn.workspace_id != conversation.workspace_id
            or conversation.agent_session_id != run.session_id
            or agent_session.workspace_id != conversation.workspace_id
            or agent_session.actor_subject != identity.subject
            or not isinstance(agent_session.context, dict)
            or agent_session.context.get("conversation_id") != conversation.id
        ):
            if turn is not None and turn.agent_run_id == run.id:
                turn.status = AgentConversationTurnStatus.FAILED.value
                turn.active_slot = None
                turn.error_code = "run_binding_invalid"
                turn.error_message = "Agent run binding is invalid"
            await run_service.finish_run(db, run=run, error="Agent run binding is missing")
            return
        if conversation.status != AgentConversationStatus.ACTIVE.value:
            turn.status = AgentConversationTurnStatus.FAILED.value
            turn.active_slot = None
            turn.error_code = "conversation_closed"
            turn.error_message = "Agent conversation is closed"
            await run_service.finish_run(db, run=run, error=turn.error_message)
            return
        payload_binding = payload.get("execution_binding")
        session_binding = agent_session.context.get("execution_binding")
        if (
            not isinstance(payload_binding, dict)
            or payload_binding != conversation.execution_binding
            or session_binding != conversation.execution_binding
        ):
            turn.status = AgentConversationTurnStatus.FAILED.value
            turn.active_slot = None
            turn.error_code = "execution_binding_changed"
            turn.error_message = "Agent run execution binding changed"
            await run_service.finish_run(db, run=run, error=turn.error_message)
            return
        try:
            await _ensure_execution_binding(db, conversation)
        except HTTPException:
            turn.status = AgentConversationTurnStatus.FAILED.value
            turn.active_slot = None
            turn.error_code = "model_unavailable"
            turn.error_message = "Pinned provider or model is unavailable"
            await run_service.finish_run(db, run=run, error=turn.error_message)
            return
        try:
            scope = await resolve_stored_agent_session_workspace(
                db,
                identity,
                workspace_id=conversation.workspace_id,
                context_binding=conversation.context_binding,
            )
            require_permission(scope.access, WorkspacePermission.READ)
        except HTTPException:
            turn.status = AgentConversationTurnStatus.FAILED.value
            turn.active_slot = None
            turn.error_code = "authorization_changed"
            turn.error_message = "Conversation authorization is no longer valid"
            await run_service.finish_run(db, run=run, error=turn.error_message)
            return
        history_rows = list(
            await db.scalars(
                select(AgentConversationTurn)
                .where(
                    AgentConversationTurn.conversation_id == conversation.id,
                    AgentConversationTurn.sequence < turn.sequence,
                    AgentConversationTurn.status.in_(
                        (
                            AgentConversationTurnStatus.COMPLETED.value,
                            AgentConversationTurnStatus.PROPOSAL.value,
                        )
                    ),
                )
                .order_by(AgentConversationTurn.sequence.asc())
            )
        )
        try:
            messages = bounded_history(history_rows, turn.user_content)
        except AgentConversationError as exc:
            turn.status = AgentConversationTurnStatus.FAILED.value
            turn.active_slot = None
            turn.error_code = "history_limit_exceeded"
            turn.error_message = _redact_error(str(exc))
            await run_service.finish_run(db, run=run, error=turn.error_message)
            return
        await _execute_turn(
            db,
            identity,
            conversation=conversation,
            turn=turn,
            run=run,
            messages=messages,
        )


async def _execute_turn(
    db: AsyncSession,
    identity: RequestIdentity,
    *,
    conversation: AgentConversation,
    turn: AgentConversationTurn,
    run: Any,
    messages: list[dict[str, str]],
    chat_runner: Callable[..., Any] | None = None,
) -> None:
    started = await run_service.begin_run(db, run.id)
    if started is None:
        return
    run = started
    turn.status = AgentConversationTurnStatus.RUNNING.value
    await db.commit()
    trace: list[dict[str, Any]] = []
    body = chat.ChatRequest(
        messages=messages,
        provider_id=conversation.execution_binding.get("provider_id"),
        model_id=(
            conversation.execution_binding.get("model_id")
            if conversation.execution_binding.get("catalog_verified") is not False
            else None
        ),
        workspace_id=conversation.workspace_id,
        context=turn.context_binding,
    )
    model_db = await _model_session(db)
    activity_events: list[tuple[str, dict[str, Any]]] = []

    async def buffer_activity(event: dict[str, Any]) -> None:
        sanitized = run_service.sanitize_activity_event(event)
        if sanitized is None:
            return
        if sanitized[0] in {"approval.required", "run.completed"}:
            activity_events.append(sanitized)
            return
        await run_service.record_activity(run.id, event)

    async def flush_activity_events() -> None:
        await db.refresh(run)
        for event_type, payload in activity_events:
            await run_service.append_event(
                db,
                run=run,
                event_type=event_type,
                payload=payload,
            )
        activity_events.clear()

    activity_token = chat._activity_sink.set(buffer_activity)
    try:
        runner = chat_runner or chat.run_chat_request
        result = await runner(
            model_db,
            body,
            identity,
            tool_trace=trace,
            proposal_provenance=chat.ProposalProvenance(
                conversation_id=conversation.id,
                turn_id=turn.id,
                context=turn.context_binding,
            ),
        )
        reply = result.data if isinstance(result, chat.ApiResponse) else result
        if not isinstance(reply, chat.ChatReply):
            raise RuntimeError("chat runner returned an invalid reply")
        if reply.type == "proposal":
            proposal = reply.proposal
            if (
                proposal is None
                or proposal.workspace_id != conversation.workspace_id
                or not proposal.work_item_id
                or not proposal.proposal_version
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Agent proposal is not bound to the conversation Workspace",
                )
        await db.rollback()
        fresh_conversation = await db.scalar(
            select(AgentConversation)
            .where(AgentConversation.id == conversation.id)
            .execution_options(populate_existing=True)
        )
        fresh_turn = await db.scalar(
            select(AgentConversationTurn)
            .where(AgentConversationTurn.id == turn.id)
            .execution_options(populate_existing=True)
        )
        fresh_run = await db.get(AgentRun, run.id, populate_existing=True)
        if fresh_conversation is None or fresh_turn is None or fresh_run is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation execution fence was lost")
        conversation = fresh_conversation
        turn = fresh_turn
        run = fresh_run
        if (
            conversation.status != AgentConversationStatus.ACTIVE.value
            or conversation.agent_session_id != run.session_id
            or turn.conversation_id != conversation.id
            or turn.workspace_id != conversation.workspace_id
            or turn.agent_run_id != run.id
            or turn.active_slot != conversation.id
            or turn.status != AgentConversationTurnStatus.RUNNING.value
            or run.status != "running"
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "conversation execution fence was lost")
        scope = await resolve_stored_agent_session_workspace(
            db,
            identity,
            workspace_id=fresh_conversation.workspace_id,
            context_binding=fresh_conversation.context_binding,
        )
        require_permission(scope.access, WorkspacePermission.READ)
        await model_db.commit()
        await flush_activity_events()
        turn.response = _safe_response(reply)
        turn.tool_trace = trace
        turn.status = (
            AgentConversationTurnStatus.PROPOSAL.value
            if reply.type == "proposal"
            else AgentConversationTurnStatus.COMPLETED.value
        )
        turn.active_slot = None
        conversation.revision += 1
        for entry in trace:
            await run_service.append_event(
                db,
                run=run,
                event_type="tool.trace",
                payload={
                    key: value
                    for key, value in entry.items()
                    if key in {"name", "kind", "status", "argument_keys"}
                },
            )
        await run_service.finish_run(db, run=run, reply=turn.response)
    except (LlmAdapterError, ResolverError) as exc:
        await model_db.rollback()
        activity_events.clear()
        turn.status = AgentConversationTurnStatus.FAILED.value
        turn.error_code = (
            "model_unavailable"
            if isinstance(exc, LlmAdapterError) and exc.retryable
            else "model_error"
        )
        turn.error_message = _redact_error(str(exc))
        turn.active_slot = None
        await db.refresh(run)
        await run_service.finish_run(db, run=run, error=turn.error_message)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型调用失败") from exc
    except HTTPException as exc:
        await model_db.rollback()
        activity_events.clear()
        turn.status = AgentConversationTurnStatus.FAILED.value
        turn.error_code = (
            "authorization_changed"
            if exc.status_code == status.HTTP_403_FORBIDDEN
            else (
                "execution_fence_lost"
                if exc.status_code == status.HTTP_409_CONFLICT
                else "model_error"
            )
        )
        turn.error_message = _redact_error(str(exc.detail))
        turn.active_slot = None
        await db.refresh(run)
        await run_service.finish_run(db, run=run, error=turn.error_message)
        raise
    except Exception as exc:
        await model_db.rollback()
        activity_events.clear()
        turn.status = AgentConversationTurnStatus.FAILED.value
        turn.error_code = "model_error"
        turn.error_message = _redact_error(str(exc))
        turn.active_slot = None
        await db.refresh(run)
        await run_service.finish_run(db, run=run, error=turn.error_message)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型调用失败") from exc
    finally:
        chat._activity_sink.reset(activity_token)
        await model_db.close()
