"""Govern agent-originated mutations through one proposal and execution path.

The registry in this module is the single source of truth for Agent Control
write actions.  Transports (the first-party chat dock today, MCP/SDK adapters
later) may preview an action, but only :class:`AgentControlService` may persist
and execute it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import canonicalize_studio_graph, get_workflow
from backend.api.v1.studio_schemas import DraftUpdate, ProjectBootstrapCreate
from backend.models.agent_conversation import (
    AgentConversation,
    AgentConversationStatus,
    AgentConversationTurn,
    AgentConversationTurnStatus,
)
from backend.models.identity import User, Workspace, WorkspaceMembership
from backend.models.operations_work_item import (
    OperationsWorkItem,
    Priority,
    Severity,
    WorkItemStatus,
    WorkItemType,
)
from backend.models.provider import ModelProvider
from backend.models.studio import StudioProject, StudioWorkflowDraft, StudioWorkspace
from backend.schemas import workflow as workflow_schemas
from backend.schemas.schedule import CronScheduleUpdate
from backend.schemas.source import DataSourceUpdate
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import (
    WorkspaceAccess,
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services import schedule_service, source_service, task_service
from backend.services.agent_project_service import (
    create_project_bundle,
    update_workflow_draft,
)

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION = "agent-control-evidence/v1"
POLICY_STATE_VERSION = "agent-control-policy/v1"


@dataclass(frozen=True)
class ActionPreview:
    action_name: str
    args: dict[str, Any]
    summary: str
    diff: str
    target_kind: str
    target_id: str
    target_resource_version: str


@dataclass(frozen=True)
class RecordedActionProposal:
    work_item_id: str
    workspace_id: str
    proposal_version: str
    preview: ActionPreview


@dataclass(frozen=True)
class ActionContext:
    workspace_id: str
    actor_user_id: str


@dataclass(frozen=True)
class ProposalProvenance:
    """Server-stamped conversation origin; never populated from chat request JSON."""

    conversation_id: str
    turn_id: str
    context: dict[str, str]


PrepareAction = Callable[
    [AsyncSession, dict[str, Any], ActionContext | None], Awaitable[ActionPreview]
]
ExecuteAction = Callable[[AsyncSession, dict[str, Any], ActionContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RegisteredAction:
    name: str
    permission: WorkspacePermission
    severity: Severity
    prepare: PrepareAction
    execute: ExecuteAction


class AgentControlActionRegistry:
    """Reusable registry for every governed Agent Control mutation."""

    def __init__(self) -> None:
        self._actions: dict[str, RegisteredAction] = {}

    def register(self, action: RegisteredAction) -> None:
        if action.name in self._actions:
            raise ValueError(f"Agent Control action already registered: {action.name}")
        self._actions[action.name] = action

    def get(self, action_name: str) -> RegisteredAction:
        action = self._actions.get(action_name)
        if action is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown proposal tool: {action_name}",
            )
        return action

    @property
    def action_names(self) -> frozenset[str]:
        return frozenset(self._actions)


class CommittedActionError(Exception):
    """An action committed authoritative state but its follow-up failed."""

    def __init__(self, *, status_code: int, detail: str, result: dict[str, Any]) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.result = result


def _resource_version(
    target_kind: str,
    target_id: str,
    updated_at: datetime,
    state: dict[str, Any],
) -> str:
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    else:
        updated_at = updated_at.astimezone(UTC)
    payload = {
        "target_kind": target_kind,
        "target_id": target_id,
        "updated_at": updated_at.isoformat(),
        "state": state,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"{target_kind}-state/v1:{digest}"


def _permission_state_version(
    workspace_id: str,
    access: WorkspaceAccess,
) -> str:
    payload = f"workspace-rbac/v1:{workspace_id}:{access.user_id}:{access.role.value}"
    return f"workspace-permission/v1:{hashlib.sha256(payload.encode()).hexdigest()}"


async def _prepare_toggle_source(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext | None = None,
) -> ActionPreview:
    source_id = str(args.get("source_id", ""))
    enabled = bool(args.get("enabled"))
    source = await source_service.get_source(db, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"数据源 {source_id} 不存在")
    verb = "启用" if enabled else "停用"
    return ActionPreview(
        action_name="toggle_source",
        args={"source_id": source_id, "enabled": enabled},
        summary=f"{verb}数据源「{source.name}」",
        diff=f"{source.name}: enabled {source.enabled} → {enabled}",
        target_kind="source",
        target_id=source.id,
        target_resource_version=_resource_version(
            "source",
            source.id,
            source.updated_at,
            {"enabled": source.enabled},
        ),
    )


async def _execute_toggle_source(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext,
) -> dict[str, Any]:
    source = await source_service.get_source(db, str(args.get("source_id", "")))
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "数据源不存在")
    await source_service.update_source(
        db,
        source,
        DataSourceUpdate.model_validate({"enabled": bool(args.get("enabled"))}),
    )
    return {}


async def _prepare_trigger_task(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext | None = None,
) -> ActionPreview:
    source_id = str(args.get("source_id", ""))
    source = await source_service.get_source(db, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"数据源 {source_id} 不存在")
    return ActionPreview(
        action_name="trigger_task",
        args={"source_id": source_id},
        summary=f"立即采集「{source.name}」",
        diff=f"触发一次手动采集: {source.name} ({'已启用' if source.enabled else '已停用'})",
        target_kind="source",
        target_id=source.id,
        target_resource_version=_resource_version(
            "source",
            source.id,
            source.updated_at,
            {"enabled": source.enabled},
        ),
    )


async def _execute_trigger_task(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext,
) -> dict[str, Any]:
    source = await source_service.get_source(db, str(args.get("source_id", "")))
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "数据源不存在")
    if not source.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "数据源已停用, 无法采集")
    task = await task_service.create_task(
        db,
        source_id=source.id,
        trigger_type="manual",
        parameters={},
        priority=0,
        agent_id=None,
    )

    # Preserve the existing dispatch contract: the task must be durable before
    # it is handed to the executor.
    await db.commit()
    from backend.executor import get_executor

    try:
        dispatch = await get_executor().dispatch_collection(task.id, {})
    except Exception as exc:
        logger.exception(
            "agent control | trigger_task dispatch failed source=%s task=%s",
            source.id,
            task.id,
        )
        raise CommittedActionError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"任务已创建但派发失败 (task_id={task.id}), 请到工作项里重试",
            result={"task_id": task.id, "dispatch_error": type(exc).__name__},
        ) from exc
    return {"task_id": task.id, "dispatch": dispatch}


async def _prepare_update_schedule(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext | None = None,
) -> ActionPreview:
    schedule_id = str(args.get("schedule_id", ""))
    schedule = await schedule_service.get_schedule(db, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"调度 {schedule_id} 不存在")

    normalized: dict[str, Any] = {"schedule_id": schedule_id}
    changes: list[str] = []
    if args.get("cron_expression") is not None:
        new_cron = str(args["cron_expression"])
        if not schedule_service.validate_cron_expression(new_cron):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"非法 cron 表达式: {new_cron}",
            )
        normalized["cron_expression"] = new_cron
        changes.append(f"cron {schedule.cron_expression} → {new_cron}")
    if args.get("enabled") is not None:
        enabled = bool(args["enabled"])
        normalized["enabled"] = enabled
        changes.append(f"enabled {schedule.enabled} → {enabled}")
    if not changes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "update_schedule 未指定要改的字段 (cron_expression 或 enabled)",
        )

    return ActionPreview(
        action_name="update_schedule",
        args=normalized,
        summary=f"修改调度「{schedule.name}」",
        diff="; ".join(changes),
        target_kind="schedule",
        target_id=schedule.id,
        target_resource_version=_resource_version(
            "schedule",
            schedule.id,
            schedule.updated_at,
            {
                "cron_expression": schedule.cron_expression,
                "enabled": schedule.enabled,
            },
        ),
    )


async def _execute_update_schedule(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext,
) -> dict[str, Any]:
    schedule = await schedule_service.get_schedule(db, str(args.get("schedule_id", "")))
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "调度不存在")
    fields = {key: args[key] for key in ("cron_expression", "enabled") if key in args}
    await schedule_service.update_schedule(db, schedule, CronScheduleUpdate(**fields))
    return {}


async def _prepare_update_provider(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext | None = None,
) -> ActionPreview:
    provider_id = str(args.get("provider_id", ""))
    provider = await db.get(ModelProvider, provider_id)
    if provider is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"模型提供商 {provider_id} 不存在",
        )

    normalized: dict[str, Any] = {"provider_id": provider_id}
    changes: list[str] = []
    if args.get("default_model") is not None:
        new_model = str(args["default_model"])
        normalized["default_model"] = new_model
        changes.append(f"default_model {provider.default_model} → {new_model}")
    if args.get("enabled") is not None:
        enabled = bool(args["enabled"])
        normalized["enabled"] = enabled
        state = "启用" if enabled else "停用"
        changes.append(f"{state} (enabled {provider.enabled} → {enabled})")
    if not changes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "update_provider 未指定要改的字段 (default_model 或 enabled)",
        )

    return ActionPreview(
        action_name="update_provider",
        args=normalized,
        summary=f"配置 AI 模型提供商「{provider.name}」",
        diff="; ".join(changes),
        target_kind="model_provider",
        target_id=provider.id,
        target_resource_version=_resource_version(
            "model_provider",
            provider.id,
            provider.updated_at,
            {
                "default_model": provider.default_model,
                "enabled": provider.enabled,
            },
        ),
    )


async def _execute_update_provider(
    db: AsyncSession,
    args: dict[str, Any],
    _context: ActionContext,
) -> dict[str, Any]:
    provider = await db.get(ModelProvider, str(args.get("provider_id", "")))
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型提供商不存在")
    if "default_model" in args:
        provider.default_model = str(args["default_model"])
    if "enabled" in args:
        provider.enabled = bool(args["enabled"])
    await db.flush()
    return {}


def _require_action_context(context: ActionContext | None) -> ActionContext:
    if context is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A governed Workspace is required for this action",
        )
    return context


def _validation_error(detail: str, exc: ValidationError) -> HTTPException:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    suffix = f" ({location}: {message})" if location else f" ({message})"
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{detail}{suffix}")


def _validate_agent_workflow_graph(value: object) -> dict[str, Any]:
    """Validate model-authored graphs without tightening the manual Studio API."""

    try:
        graph = workflow_schemas.WorkflowProject.model_validate(value)
    except ValidationError as exc:
        raise _validation_error("Invalid Agent Workflow graph", exc) from exc
    return graph.model_dump(mode="json", exclude_none=True)


async def _prepare_create_project(
    db: AsyncSession,
    args: dict[str, Any],
    context: ActionContext | None = None,
) -> ActionPreview:
    action_context = _require_action_context(context)
    workflow_args = args.get("workflow")
    validated_graph = None
    if isinstance(workflow_args, dict) and "graph" in workflow_args:
        validated_graph = _validate_agent_workflow_graph(workflow_args["graph"])
    try:
        body = ProjectBootstrapCreate.model_validate(args)
    except ValidationError as exc:
        raise _validation_error("Invalid Project bootstrap", exc) from exc

    governed_workspace = await db.get(Workspace, action_context.workspace_id)
    if governed_workspace is None or not governed_workspace.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    studio_workspace = await db.get(StudioWorkspace, action_context.workspace_id)
    existing = await db.scalar(
        select(StudioProject.id).where(
            StudioProject.workspace_id == action_context.workspace_id,
            StudioProject.slug == body.project.slug,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Project slug already exists")

    normalized = body.model_dump(mode="json", exclude_none=True)
    if validated_graph is not None:
        normalized["workflow"]["graph"] = validated_graph
    return ActionPreview(
        action_name="create_project",
        args=normalized,
        summary=f"创建项目「{body.project.name}」及主工作流「{body.workflow.name}」",
        diff=(
            f"Project {body.project.slug}: absent → draft; "
            f"primary workflow: {body.workflow.name}; published: false"
        ),
        target_kind="studio_project_slot",
        target_id=f"{action_context.workspace_id}:{body.project.slug}",
        target_resource_version=_resource_version(
            "studio_project_slot",
            f"{action_context.workspace_id}:{body.project.slug}",
            (studio_workspace or governed_workspace).updated_at,
            {
                "studio_workspace_linked": studio_workspace is not None,
                "slug": body.project.slug,
                "occupied": False,
            },
        ),
    )


async def _execute_create_project(
    db: AsyncSession,
    args: dict[str, Any],
    context: ActionContext,
) -> dict[str, Any]:
    try:
        body = ProjectBootstrapCreate.model_validate(args)
    except ValidationError as exc:
        raise _validation_error("Invalid Project bootstrap", exc) from exc
    created = await create_project_bundle(
        db,
        workspace_id=context.workspace_id,
        body=body,
        actor_user_id=context.actor_user_id,
    )
    return {
        "project_id": created.project.id,
        "workflow_id": created.workflow.id,
        "draft_revision": created.draft.revision,
    }


async def _prepare_update_workflow_draft(
    db: AsyncSession,
    args: dict[str, Any],
    context: ActionContext | None = None,
) -> ActionPreview:
    action_context = _require_action_context(context)
    project_id = args.get("project_id")
    workflow_id = args.get("workflow_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "project_id is required")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workflow_id is required")
    validated_graph = _validate_agent_workflow_graph(args.get("graph"))
    try:
        body = DraftUpdate.model_validate(
            {"revision": args.get("revision"), "graph": validated_graph}
        )
    except ValidationError as exc:
        raise _validation_error("Invalid Workflow draft update", exc) from exc

    workflow = await get_workflow(
        db,
        action_context.workspace_id,
        project_id,
        workflow_id,
    )
    draft = await db.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == workflow.id)
    )
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    if draft.revision != body.revision:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow draft revision conflict")

    graph = canonicalize_studio_graph(
        validated_graph,
        workflow_id=workflow.id,
    )
    normalized = {
        "project_id": project_id,
        "workflow_id": workflow_id,
        "revision": body.revision,
        "graph": graph,
    }
    return ActionPreview(
        action_name="update_workflow_draft",
        args=normalized,
        summary=f"更新工作流草稿「{workflow.name}」",
        diff=(
            f"draft revision {body.revision} → {body.revision + 1}; "
            f"nodes={len(graph.get('nodes', []))}; edges={len(graph.get('edges', []))}; "
            "published: unchanged"
        ),
        target_kind="studio_workflow_draft",
        target_id=workflow.id,
        target_resource_version=_resource_version(
            "studio_workflow_draft",
            workflow.id,
            draft.updated_at,
            {"revision": draft.revision, "graph": draft.graph},
        ),
    )


async def _execute_update_workflow_draft(
    db: AsyncSession,
    args: dict[str, Any],
    context: ActionContext,
) -> dict[str, Any]:
    try:
        body = DraftUpdate.model_validate(
            {"revision": args.get("revision"), "graph": args.get("graph")}
        )
    except ValidationError as exc:
        raise _validation_error("Invalid Workflow draft update", exc) from exc
    row = await update_workflow_draft(
        db,
        workspace_id=context.workspace_id,
        project_id=str(args["project_id"]),
        workflow_id=str(args["workflow_id"]),
        body=body,
        actor_user_id=context.actor_user_id,
    )
    return {
        "project_id": str(args["project_id"]),
        "workflow_id": row.workflow_id,
        "draft_revision": row.revision,
    }


def _build_registry() -> AgentControlActionRegistry:
    registry = AgentControlActionRegistry()
    registry.register(
        RegisteredAction(
            name="toggle_source",
            permission=WorkspacePermission.MANAGE_CONFIGURATION,
            severity=Severity.MEDIUM,
            prepare=_prepare_toggle_source,
            execute=_execute_toggle_source,
        )
    )
    registry.register(
        RegisteredAction(
            name="trigger_task",
            permission=WorkspacePermission.RUN_OPERATIONS_AGENTS,
            severity=Severity.LOW,
            prepare=_prepare_trigger_task,
            execute=_execute_trigger_task,
        )
    )
    registry.register(
        RegisteredAction(
            name="update_schedule",
            permission=WorkspacePermission.MANAGE_CONFIGURATION,
            severity=Severity.MEDIUM,
            prepare=_prepare_update_schedule,
            execute=_execute_update_schedule,
        )
    )
    registry.register(
        RegisteredAction(
            name="update_provider",
            permission=WorkspacePermission.MANAGE_CONFIGURATION,
            severity=Severity.MEDIUM,
            prepare=_prepare_update_provider,
            execute=_execute_update_provider,
        )
    )
    registry.register(
        RegisteredAction(
            name="create_project",
            permission=WorkspacePermission.MANAGE_CONFIGURATION,
            severity=Severity.MEDIUM,
            prepare=_prepare_create_project,
            execute=_execute_create_project,
        )
    )
    registry.register(
        RegisteredAction(
            name="update_workflow_draft",
            permission=WorkspacePermission.MANAGE_CONFIGURATION,
            severity=Severity.MEDIUM,
            prepare=_prepare_update_workflow_draft,
            execute=_execute_update_workflow_draft,
        )
    )
    return registry


class AgentControlService:
    def __init__(self, registry: AgentControlActionRegistry) -> None:
        self.registry = registry

    async def resolve_workspace_id(
        self,
        db: AsyncSession,
        identity: RequestIdentity,
        requested_workspace_id: str | None,
    ) -> str:
        """Resolve legacy chat requests without weakening Workspace scope."""

        if requested_workspace_id:
            await get_workspace_access(db, requested_workspace_id, identity)
            return requested_workspace_id

        rows = (
            await db.scalars(
                select(WorkspaceMembership.workspace_id)
                .join(User, User.id == WorkspaceMembership.user_id)
                .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                .where(User.subject == identity.subject)
                .where(User.disabled.is_(False))
                .where(Workspace.active.is_(True))
            )
        ).all()
        workspace_ids = list(dict.fromkeys(rows))
        if not workspace_ids:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Workspace membership required",
            )
        if len(workspace_ids) != 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "workspace_id is required when the actor belongs to multiple workspaces",
            )
        return workspace_ids[0]

    async def preview(
        self,
        db: AsyncSession,
        action_name: str,
        args: dict[str, Any],
    ) -> ActionPreview:
        return await self.registry.get(action_name).prepare(db, args, None)

    async def _validate_provenance(
        self,
        db: AsyncSession,
        *,
        workspace_id: str,
        actor_user_id: str,
        provenance: ProposalProvenance,
    ) -> None:
        conversation = await db.scalar(
            select(AgentConversation).where(
                AgentConversation.id == provenance.conversation_id,
                AgentConversation.workspace_id == workspace_id,
                AgentConversation.created_by_user_id == actor_user_id,
                AgentConversation.status == AgentConversationStatus.ACTIVE.value,
            )
        )
        if conversation is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent proposal provenance is not bound to this conversation",
            )
        turn = await db.scalar(
            select(AgentConversationTurn).where(
                AgentConversationTurn.id == provenance.turn_id,
                AgentConversationTurn.conversation_id == conversation.id,
                AgentConversationTurn.workspace_id == workspace_id,
                AgentConversationTurn.status == AgentConversationTurnStatus.RUNNING.value,
            )
        )
        if turn is None or dict(turn.context_binding or {}) != provenance.context:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent proposal provenance does not match the active conversation turn",
            )

    async def create_proposal(
        self,
        db: AsyncSession,
        *,
        workspace_id: str,
        identity: RequestIdentity,
        action_name: str,
        args: dict[str, Any],
        origin: str,
        provenance: ProposalProvenance | None = None,
    ) -> RecordedActionProposal:
        action = self.registry.get(action_name)
        access = await get_workspace_access(db, workspace_id, identity)
        require_permission(access, action.permission)
        action_context = ActionContext(workspace_id=workspace_id, actor_user_id=access.user_id)
        preview = await action.prepare(db, args, action_context)
        if provenance is not None:
            await self._validate_provenance(
                db,
                workspace_id=workspace_id,
                actor_user_id=access.user_id,
                provenance=provenance,
            )
        proposal_version = f"agent-control-proposal/v1:{uuid.uuid4()}"
        now = datetime.now(UTC)
        evidence: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "proposal_version": proposal_version,
            "target_resource_version": preview.target_resource_version,
            "policy_state_version": POLICY_STATE_VERSION,
            "permission_state_version": _permission_state_version(workspace_id, access),
            "diff": {"summary": preview.diff},
            "observations": [
                "Agent-originated mutation requires explicit confirmation before execution."
            ],
            "agent_control": {
                "action": preview.action_name,
                "args": preview.args,
                "target": {
                    "kind": preview.target_kind,
                    "id": preview.target_id,
                },
                "required_permission": action.permission.value,
                "origin": origin,
                "context": dict(provenance.context) if provenance is not None else {},
            },
            "confirmation": {
                "required": True,
                "state": "pending",
                "created_at": now.isoformat(),
            },
            "actor_identity": {
                "subject": identity.subject,
                "auth_method": identity.auth_method,
            },
        }
        if provenance is not None:
            evidence["conversation_id"] = provenance.conversation_id
            evidence["agent_control"]["provenance"] = {
                "conversation_id": provenance.conversation_id,
                "turn_id": provenance.turn_id,
            }
            for field in ("project_id", "workflow_id", "run_id"):
                value = provenance.context.get(field)
                if isinstance(value, str) and value:
                    evidence[field] = value
        for field in ("project_id", "workflow_id"):
            value = preview.args.get(field)
            if isinstance(value, str) and value:
                evidence[field] = value
        work_item = OperationsWorkItem(
            workspace_id=workspace_id,
            type=WorkItemType.CHANGE_PROPOSAL,
            status=WorkItemStatus.OPEN,
            severity=action.severity,
            priority=Priority.NORMAL,
            author_actor_type="user",
            author_actor_id=access.user_id,
            evidence=evidence,
            reason=preview.summary,
        )
        db.add(work_item)
        await db.flush()
        return RecordedActionProposal(
            work_item_id=work_item.id,
            workspace_id=workspace_id,
            proposal_version=proposal_version,
            preview=preview,
        )

    async def execute_confirmed(
        self,
        db: AsyncSession,
        *,
        workspace_id: str,
        identity: RequestIdentity,
        work_item_id: str,
        proposal_version: str,
        confirmation_path: str,
        expected_action: str | None = None,
    ) -> dict[str, Any]:
        access = await get_workspace_access(db, workspace_id, identity)
        work_item = await db.scalar(
            select(OperationsWorkItem)
            .where(OperationsWorkItem.id == work_item_id)
            .where(OperationsWorkItem.workspace_id == workspace_id)
            .where(OperationsWorkItem.type == WorkItemType.CHANGE_PROPOSAL)
            .with_for_update()
        )
        if work_item is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Agent Control proposal not found",
            )
        if work_item.status != WorkItemStatus.OPEN:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent Control proposal is not actionable",
            )

        evidence = dict(work_item.evidence or {})
        recorded_version = evidence.get("proposal_version")
        if recorded_version != proposal_version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent Control proposal version changed",
            )
        control = evidence.get("agent_control")
        if not isinstance(control, dict):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent Control evidence is missing",
            )
        action_name = control.get("action")
        args = control.get("args")
        if not isinstance(action_name, str) or not isinstance(args, dict):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent Control action evidence is invalid",
            )
        if expected_action is not None and action_name != expected_action:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Confirmed action does not match the recorded proposal",
            )

        origin_conversation: AgentConversation | None = None
        origin_turn: AgentConversationTurn | None = None
        conversation_id = evidence.get("conversation_id")
        if conversation_id is not None:
            provenance = control.get("provenance")
            if (
                not isinstance(conversation_id, str)
                or not isinstance(provenance, dict)
                or provenance.get("conversation_id") != conversation_id
                or not isinstance(provenance.get("turn_id"), str)
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Agent proposal conversation provenance is invalid",
                )
            origin_conversation = await db.scalar(
                select(AgentConversation)
                .where(
                    AgentConversation.id == conversation_id,
                    AgentConversation.workspace_id == workspace_id,
                    AgentConversation.created_by_user_id == work_item.author_actor_id,
                    AgentConversation.status == AgentConversationStatus.ACTIVE.value,
                )
                .with_for_update()
            )
            if origin_conversation is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Agent proposal conversation is no longer actionable",
                )
            origin_turn = await db.scalar(
                select(AgentConversationTurn)
                .where(
                    AgentConversationTurn.id == provenance["turn_id"],
                    AgentConversationTurn.conversation_id == origin_conversation.id,
                    AgentConversationTurn.workspace_id == workspace_id,
                    AgentConversationTurn.status == AgentConversationTurnStatus.PROPOSAL.value,
                )
                .with_for_update()
            )
            if origin_turn is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Agent proposal turn is no longer actionable",
                )
            stored_response = origin_turn.response
            stored_proposal = (
                stored_response.get("proposal") if isinstance(stored_response, dict) else None
            )
            if (
                not isinstance(stored_proposal, dict)
                or stored_proposal.get("work_item_id") != work_item_id
                or stored_proposal.get("proposal_version") != proposal_version
                or stored_proposal.get("tool") != action_name
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Agent proposal turn does not match the confirmed proposal",
                )

        action = self.registry.get(action_name)
        require_permission(access, action.permission)
        action_context = ActionContext(workspace_id=workspace_id, actor_user_id=access.user_id)
        preview = await action.prepare(db, args, action_context)
        if evidence.get("target_resource_version") != preview.target_resource_version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent Control target changed; create a new proposal",
            )

        now = datetime.now(UTC)
        evidence["confirmation"] = {
            "required": True,
            "state": "confirmed",
            "path": confirmation_path,
            "confirmed_at": now.isoformat(),
            "actor_user_id": access.user_id,
            "actor_subject": identity.subject,
            "actor_role": access.role.value,
        }
        evidence["approval_grant"] = {
            "grant_type": "explicit_confirmation",
            "proposal_version": proposal_version,
            "granted_at": now.isoformat(),
            "approver_user_ids": [access.user_id],
            "confirmation_path": confirmation_path,
        }
        work_item.evidence = evidence
        work_item.status = WorkItemStatus.IN_PROGRESS
        await db.flush()

        try:
            action_result = await action.execute(db, preview.args, action_context)
        except CommittedActionError as exc:
            failure_evidence = dict(evidence)
            failure_evidence["execution"] = {
                "status": "failed_after_commit",
                "failed_at": datetime.now(UTC).isoformat(),
                "result": exc.result,
            }
            work_item.evidence = failure_evidence
            await db.commit()
            raise HTTPException(exc.status_code, exc.detail) from exc
        except Exception:
            await db.rollback()
            raise

        result = {
            "applied": True,
            "tool": action_name,
            "summary": work_item.reason or preview.summary,
            "work_item_id": work_item.id,
            "proposal_version": proposal_version,
            "workspace_id": workspace_id,
            **action_result,
        }
        if origin_conversation is not None and origin_turn is not None:
            result["conversation_id"] = origin_conversation.id
            result["conversation_turn_id"] = origin_turn.id
        if origin_conversation is not None:
            binding = dict(origin_conversation.context_binding or {})
            project_id = action_result.get("project_id")
            workflow_id = action_result.get("workflow_id")
            if isinstance(project_id, str) and project_id:
                if binding.get("project_id") != project_id:
                    binding.pop("workflow_id", None)
                    binding.pop("run_id", None)
                    binding.pop("source_id", None)
                binding["project_id"] = project_id
            if isinstance(workflow_id, str) and workflow_id:
                if binding.get("workflow_id") != workflow_id:
                    binding.pop("run_id", None)
                binding["workflow_id"] = workflow_id
            if binding != origin_conversation.context_binding:
                origin_conversation.context_binding = binding
                origin_conversation.revision += 1
        if origin_turn is not None:
            origin_turn.response = {
                "type": "message",
                "content": f"{result['summary']}，已确认并应用。",
                "proposal": None,
                "execution": {"status": "applied", "result": result},
            }
            origin_turn.tool_trace = [
                *list(origin_turn.tool_trace or []),
                {
                    "name": action_name,
                    "kind": "write",
                    "status": "applied",
                    "result_keys": sorted(result),
                },
            ]
            origin_turn.status = AgentConversationTurnStatus.COMPLETED.value
        completed_evidence = dict(evidence)
        for field in ("project_id", "workflow_id", "run_id", "draft_revision"):
            value = result.get(field)
            if isinstance(value, str | int) and not isinstance(value, bool):
                completed_evidence[field] = value
        completed_evidence["execution"] = {
            "status": "applied",
            "executed_at": datetime.now(UTC).isoformat(),
            "result": result,
        }
        work_item.evidence = completed_evidence
        work_item.status = WorkItemStatus.RESOLVED
        await db.commit()
        logger.info(
            "agent control | applied action=%s proposal=%s workspace=%s actor=%s",
            action_name,
            work_item.id,
            workspace_id,
            identity.subject,
        )
        return result


ACTION_REGISTRY = _build_registry()
agent_control_service = AgentControlService(ACTION_REGISTRY)
