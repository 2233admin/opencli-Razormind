"""Evidence-backed health projection for Agent conversations, runs, and automations."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.automation_schedule import automation_fire_times
from backend.models.agent_conversation import AgentConversation, AgentConversationTurn
from backend.models.automation import Automation
from backend.models.operations_agent import OperationsAgentIdentity, OperationsAgentRun
from backend.schemas.automation import (
    AgentWorkActionRead,
    AgentWorkBindingRead,
    AgentWorkHealthItemRead,
    AgentWorkHealthPermissionsRead,
    AgentWorkHealthRead,
    AgentWorkLatestRunRead,
)
from backend.services.automation_schedule_service import (
    AutomationBindingError,
    validate_automation_binding,
)

_ATTENTION_STATES = frozenset({"blocked", "failed", "paused"})
_MISSING_RUNTIME_CODES = frozenset(
    {"missing_binary", "missing_isolated_runner", "runtime_not_found"}
)
_AUTH_RUNTIME_CODES = frozenset(
    {"auth_required", "authenticationerror", "authorizationerror", "permissionerror"}
)
_CONFIG_RUNTIME_CODES = frozenset(
    {"configerror", "invalid_config", "provider_required", "model_required"}
)


def _project_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("project_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _latest_run_read(run: OperationsAgentRun | None) -> AgentWorkLatestRunRead | None:
    if run is None:
        return None
    return AgentWorkLatestRunRead(
        id=run.id,
        status=run.status,
        trigger_type=run.trigger_type,
        trigger_reference=run.trigger_reference,
        scheduled_for=run.scheduled_for,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _binding_read(
    run: OperationsAgentRun,
    execution_binding: dict[str, Any] | None = None,
) -> AgentWorkBindingRead:
    selected = execution_binding if execution_binding is not None else run.execution_binding
    selected = selected if isinstance(selected, dict) else {}
    runtime = selected.get("runtime")
    agent_url = selected.get("agent_url")
    return AgentWorkBindingRead(
        operations_agent_id=run.operations_agent_id,
        published_version=run.published_version,
        profile_version=run.profile_version,
        automation_revision=run.automation_revision,
        runtime=runtime if isinstance(runtime, str) else None,
        agent_url=agent_url if isinstance(agent_url, str) else None,
    )


def _runtime_error_type(run: OperationsAgentRun) -> str | None:
    evidence = run.evidence_payload
    if not isinstance(evidence, dict):
        return None
    events = evidence.get("events")
    if not isinstance(events, list):
        return None
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("type") != "error":
            continue
        payload = event.get("payload")
        error_type = payload.get("error_type") if isinstance(payload, dict) else None
        if isinstance(error_type, str) and error_type:
            return error_type
    return None


def run_failure_reason(run: OperationsAgentRun) -> str:
    """Classify only persisted runtime evidence; never infer installation state."""

    error_type = (_runtime_error_type(run) or "").lower()
    message = (run.error_message or "").lower()
    if error_type in _MISSING_RUNTIME_CODES:
        return "runtime_not_installed"
    if error_type in _AUTH_RUNTIME_CODES:
        return "runtime_permission_required"
    if error_type in _CONFIG_RUNTIME_CODES:
        return "runtime_not_configured"
    if "permission profile" in message or "governed action gateway" in message:
        return "runtime_permission_required"
    if "configuration is invalid" in message or "requires contract and runtime binding" in message:
        return "runtime_not_configured"
    if run.execution_binding is None and run.error_message:
        return "runtime_unavailable"
    return "run_failed"


def automation_blocker_reason(error: Exception) -> str:
    message = str(error)
    if "profile is missing" in message or "approval_mode must match" in message:
        return "permission_profile_required"
    if "Low-Risk Automatic profile" in message:
        return "permission_profile_unsupported"
    if "disabled" in message:
        return "agent_disabled"
    if "payload is incompatible" in message:
        return "contract_incompatible"
    if "version" in message or "contract" in message or "Runtime Binding" in message:
        return "runtime_not_configured"
    return "runtime_unavailable"


def _next_due_at(automation: Automation, now: datetime) -> datetime | None:
    if not automation.enabled:
        return None
    horizon = {
        "hourly": timedelta(hours=2),
        "daily": timedelta(days=2),
        "weekdays": timedelta(days=4),
        "weekly": timedelta(days=8),
    }[automation.schedule.split("@", 1)[0]]
    occurrences = automation_fire_times(
        automation.schedule,
        automation.timezone,
        now,
        now + horizon,
    )
    return occurrences[0] if occurrences else None


def _pause_action(run: OperationsAgentRun) -> AgentWorkActionRead:
    return AgentWorkActionRead(
        kind="pause_run",
        label="暂停运行",
        operations_agent_id=run.operations_agent_id,
        run_id=run.id,
    )


def _configure_action(
    *,
    agent_id: str | None = None,
    automation_id: str | None = None,
) -> AgentWorkActionRead:
    return AgentWorkActionRead(
        kind="configure",
        label="前往配置",
        operations_agent_id=agent_id,
        automation_id=automation_id,
    )


def _run_state(run: OperationsAgentRun) -> tuple[str, str, str | None]:
    if run.status == "queued":
        return "queued", "运行已进入队列，等待 Runtime 接收。", None
    if run.status == "running":
        return "running", "Runtime 正在执行这个工作。", None
    if run.status == "paused":
        return (
            "paused",
            "运行已暂停；当前 Agent Runtime API 不支持按运行 ID 恢复。",
            "runtime_resume_not_supported",
        )
    if run.status == "completed":
        return "completed", "运行已完成，可查看已有结果与证据。", None
    if run.status == "cancelled":
        return "cancelled", "运行已结束且不会继续执行。", "run_cancelled"
    reason = run_failure_reason(run)
    return "failed", run.error_message or "运行失败，请检查 Runtime 证据。", reason


async def _latest_automation_runs(
    session: AsyncSession,
    workspace_id: str,
) -> dict[str, OperationsAgentRun]:
    ranked = (
        select(
            OperationsAgentRun.id.label("run_id"),
            func.row_number()
            .over(
                partition_by=OperationsAgentRun.automation_id,
                order_by=(OperationsAgentRun.updated_at.desc(), OperationsAgentRun.id.desc()),
            )
            .label("position"),
        )
        .where(
            OperationsAgentRun.workspace_id == workspace_id,
            OperationsAgentRun.automation_id.is_not(None),
        )
        .subquery()
    )
    rows = list(
        await session.scalars(
            select(OperationsAgentRun)
            .join(ranked, ranked.c.run_id == OperationsAgentRun.id)
            .where(ranked.c.position == 1)
        )
    )
    return {run.automation_id: run for run in rows if run.automation_id is not None}


async def _latest_conversation_turns(
    session: AsyncSession,
    workspace_id: str,
) -> dict[str, AgentConversationTurn]:
    latest = (
        select(
            AgentConversationTurn.conversation_id.label("conversation_id"),
            func.max(AgentConversationTurn.sequence).label("sequence"),
        )
        .where(AgentConversationTurn.workspace_id == workspace_id)
        .group_by(AgentConversationTurn.conversation_id)
        .subquery()
    )
    rows = list(
        await session.scalars(
            select(AgentConversationTurn).join(
                latest,
                and_(
                    latest.c.conversation_id == AgentConversationTurn.conversation_id,
                    latest.c.sequence == AgentConversationTurn.sequence,
                ),
            )
        )
    )
    return {turn.conversation_id: turn for turn in rows}


def _conversation_health(
    conversation: AgentConversation,
    turn: AgentConversationTurn | None,
) -> AgentWorkHealthItemRead:
    project_id = _project_id(conversation.context_binding)
    if conversation.status == "closed":
        state, message, reason = "completed", "会话已结束，可打开查看持久记录。", None
    elif turn is None:
        state, message, reason = "not_started", "会话已创建，尚未提交工作。", None
    elif turn.status == "running":
        state, message, reason = "running", "会话中的最新请求正在处理。", None
    elif turn.status == "failed":
        state = "failed"
        message = turn.error_message or "会话中的最新请求失败。"
        reason = turn.error_code or "conversation_turn_failed"
    elif turn.status == "proposal":
        state = "blocked"
        message = "Agent 提案正在等待人工处理。"
        reason = "proposal_review_required"
    else:
        state = "ready"
        message = "持久会话可继续发送消息；这不会冒充原生 Runtime 进程恢复。"
        reason = None
    return AgentWorkHealthItemRead(
        id=f"conversation:{conversation.id}",
        kind="conversation",
        title=conversation.title or f"工作会话 {conversation.id[:8]}",
        state=state,
        message=message,
        reason_code=reason,
        recoverable=conversation.status == "active",
        resume_supported=False,
        project_id=project_id,
        conversation_id=conversation.id,
        actions=[
            AgentWorkActionRead(
                kind="open_conversation",
                label="打开会话",
                conversation_id=conversation.id,
            )
        ],
        updated_at=conversation.updated_at,
    )


def _run_health(
    run: OperationsAgentRun,
    agent_name: str,
    *,
    can_run: bool,
    can_manage: bool,
) -> AgentWorkHealthItemRead:
    state, message, reason = _run_state(run)
    actions: list[AgentWorkActionRead] = []
    if can_run and run.status in {"queued", "running"}:
        actions.append(_pause_action(run))
    if can_manage and run.status in {"paused", "failed"}:
        actions.append(_configure_action(agent_id=run.operations_agent_id))
    return AgentWorkHealthItemRead(
        id=f"run:{run.id}",
        kind="run",
        title=f"{agent_name} · {run.target_resource_type} {run.target_resource_id}",
        state=state,
        message=message,
        reason_code=reason,
        recoverable=False,
        resume_supported=False,
        project_id=(
            run.target_resource_id if run.target_resource_type == "project" else None
        ),
        operations_agent_id=run.operations_agent_id,
        run_id=run.id,
        binding=_binding_read(run),
        latest_run=_latest_run_read(run),
        actions=actions,
        updated_at=run.updated_at,
    )


async def _automation_health(
    session: AsyncSession,
    automation: Automation,
    latest_run: OperationsAgentRun | None,
    *,
    can_run: bool,
    can_manage: bool,
    now: datetime,
) -> AgentWorkHealthItemRead:
    execution_binding: dict[str, Any] | None = None
    binding_error: Exception | None = None
    if automation.enabled:
        try:
            _, _, profile, execution_binding = await validate_automation_binding(
                session,
                automation,
                require_online=True,
            )
        except (AutomationBindingError, ValidationError) as exc:
            binding_error = exc
            profile = None
    else:
        profile = None

    if latest_run is not None and latest_run.status in {"queued", "running"}:
        state, message, reason = _run_state(latest_run)
    elif not automation.enabled:
        state, message, reason = "inactive", "自动化已暂停，不会产生新的定时运行。", None
    elif binding_error is not None:
        state = "blocked"
        message = str(binding_error)
        reason = automation_blocker_reason(binding_error)
    elif latest_run is None:
        state = "not_started"
        message = "Automation 绑定与 Runtime 连接预检通过，尚未产生运行记录。"
        reason = None
    else:
        state, message, reason = _run_state(latest_run)

    actions: list[AgentWorkActionRead] = []
    if can_run and latest_run is not None and latest_run.status in {"queued", "running"}:
        actions.append(_pause_action(latest_run))
    if (
        can_run
        and automation.enabled
        and binding_error is None
        and (latest_run is None or latest_run.status in {"completed", "failed", "cancelled"})
    ):
        actions.append(
            AgentWorkActionRead(
                kind=(
                    "retry_automation"
                    if latest_run is not None and latest_run.status == "failed"
                    else "run_automation"
                ),
                label=(
                    "重试"
                    if latest_run is not None and latest_run.status == "failed"
                    else "立即运行"
                ),
                automation_id=automation.id,
            )
        )
    if can_manage and state in _ATTENTION_STATES | {"inactive"}:
        actions.append(_configure_action(automation_id=automation.id))

    binding: AgentWorkBindingRead | None = None
    if latest_run is not None:
        binding = _binding_read(latest_run, execution_binding)
    elif automation.operations_agent_id and automation.operations_agent_version and profile:
        selected = execution_binding or {}
        binding = AgentWorkBindingRead(
            operations_agent_id=automation.operations_agent_id,
            published_version=automation.operations_agent_version,
            profile_version=profile.version,
            automation_revision=automation.revision,
            runtime=selected.get("runtime"),
            agent_url=selected.get("agent_url"),
        )

    return AgentWorkHealthItemRead(
        id=f"automation:{automation.id}",
        kind="automation",
        title=automation.name,
        state=state,
        message=message,
        reason_code=reason,
        recoverable=(
            automation.enabled
            and binding_error is None
            and (latest_run is None or latest_run.status in {"completed", "failed", "cancelled"})
        ),
        resume_supported=False,
        project_id=_project_id(automation.project),
        operations_agent_id=automation.operations_agent_id,
        automation_id=automation.id,
        run_id=latest_run.id if latest_run else None,
        binding=binding,
        latest_run=_latest_run_read(latest_run),
        next_due_at=_next_due_at(automation, now),
        actions=actions,
        updated_at=latest_run.updated_at if latest_run else automation.updated_at,
    )


async def get_agent_work_health(
    session: AsyncSession,
    *,
    workspace_id: str,
    project_id: str | None,
    can_run: bool,
    can_manage: bool,
    now: datetime | None = None,
) -> AgentWorkHealthRead:
    """Combine authoritative records without mutating scheduler or runtime state."""

    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    automations = list(
        await session.scalars(
            select(Automation)
            .where(Automation.workspace_id == workspace_id)
            .order_by(Automation.updated_at.desc())
        )
    )
    conversations = list(
        await session.scalars(
            select(AgentConversation)
            .where(AgentConversation.workspace_id == workspace_id)
            .order_by(AgentConversation.updated_at.desc())
        )
    )
    latest_automation_runs = await _latest_automation_runs(session, workspace_id)
    latest_turns = await _latest_conversation_turns(session, workspace_id)
    agents = {
        agent.id: agent.name
        for agent in await session.scalars(
            select(OperationsAgentIdentity).where(
                OperationsAgentIdentity.workspace_id == workspace_id
            )
        )
    }

    items: list[AgentWorkHealthItemRead] = []
    for conversation in conversations:
        item = _conversation_health(conversation, latest_turns.get(conversation.id))
        if project_id is None or item.project_id == project_id:
            items.append(item)

    for automation in automations:
        automation_project_id = _project_id(automation.project)
        if project_id is not None and automation_project_id != project_id:
            continue
        items.append(
            await _automation_health(
                session,
                automation,
                latest_automation_runs.get(automation.id),
                can_run=can_run,
                can_manage=can_manage,
                now=generated_at,
            )
        )

    standalone_query = select(OperationsAgentRun).where(
        OperationsAgentRun.workspace_id == workspace_id,
        OperationsAgentRun.automation_id.is_(None),
    )
    if project_id is not None:
        standalone_query = standalone_query.where(
            OperationsAgentRun.target_resource_type == "project",
            OperationsAgentRun.target_resource_id == project_id,
        )
    standalone_runs = list(
        await session.scalars(
            standalone_query.order_by(OperationsAgentRun.updated_at.desc()).limit(25)
        )
    )
    for run in standalone_runs:
        items.append(
            _run_health(
                run,
                agents.get(run.operations_agent_id, "Operations Agent"),
                can_run=can_run,
                can_manage=can_manage,
            )
        )

    items.sort(
        key=lambda item: (
            item.updated_at.replace(tzinfo=UTC)
            if item.updated_at.tzinfo is None
            else item.updated_at
        ).timestamp(),
        reverse=True,
    )
    counts = Counter(item.state for item in items)
    counts["total"] = len(items)
    counts["needs_attention"] = sum(
        count for state, count in counts.items() if state in _ATTENTION_STATES
    )
    return AgentWorkHealthRead(
        workspace_id=workspace_id,
        project_id=project_id,
        generated_at=generated_at,
        permissions=AgentWorkHealthPermissionsRead(can_run=can_run, can_manage=can_manage),
        counts=dict(counts),
        items=items,
    )
