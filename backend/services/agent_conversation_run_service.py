"""Durable execution ledger for persistent Agent conversations.

Conversation history remains in ``AgentConversationTurn``.  This module owns
the one-to-one turn/run link, ordered public events, and in-process background
task lifetime.  It never retries interrupted work because a run may already
have reached an external read or proposal boundary.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.models.agent_conversation import (
    AgentConversation,
    AgentConversationTurn,
    AgentConversationTurnStatus,
)
from backend.models.agent_run import AgentRun, AgentRunEvent, AgentSession
from backend.security.identity import RequestIdentity

logger = logging.getLogger(__name__)

TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "interrupted"})
_background_runs: dict[str, asyncio.Task[None]] = {}
_ACTIVITY_EVENT_TYPES = frozenset(
    {"phase.changed", "tool.started", "tool.completed", "approval.required", "run.completed"}
)
_SENSITIVE_EVENT_VALUE = re.compile(
    r"(?i)(https?://\S+|(?:api[_-]?key|authorization|token|secret|password)\s*[:=]\s*\S+)"
)


def background_execution_readiness(settings: Any) -> dict[str, str | None]:
    mode = getattr(settings, "agent_conversation_execution_mode", "disabled")
    if mode != "local_single_process":
        return {
            "status": "blocked",
            "reason_code": "background_execution_disabled",
            "reason": "Background conversation execution is not enabled on this server.",
        }
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = os.environ.get(name)
        if raw:
            try:
                workers = int(raw)
            except ValueError:
                workers = 2
            if workers > 1:
                return {
                    "status": "blocked",
                    "reason_code": "multiple_workers_not_supported",
                    "reason": "Background conversation execution requires one API process.",
                }
    for index, argument in enumerate(sys.argv):
        raw_workers: str | None = None
        if argument == "--workers" and index + 1 < len(sys.argv):
            raw_workers = sys.argv[index + 1]
        elif argument.startswith("--workers="):
            raw_workers = argument.partition("=")[2]
        if raw_workers is not None:
            try:
                workers = int(raw_workers)
            except ValueError:
                workers = 2
            if workers > 1:
                return {
                    "status": "blocked",
                    "reason_code": "multiple_workers_not_supported",
                    "reason": "Background conversation execution requires one API process.",
                }
    return {"status": "ready", "reason_code": None, "reason": None}


def validate_background_execution_settings(settings: Any) -> bool:
    readiness = background_execution_readiness(settings)
    if (
        getattr(settings, "agent_conversation_execution_mode", "disabled") == "local_single_process"
        and readiness["status"] != "ready"
    ):
        raise RuntimeError(readiness["reason"] or "invalid background execution settings")
    return readiness["status"] == "ready"


def continuation_payload(run: AgentRun) -> dict[str, Any]:
    value = (
        run.request_payload.get("continuation") if isinstance(run.request_payload, dict) else None
    )
    if not isinstance(value, dict):
        return {"mode": "history_replay", "resumed": False}
    mode = value.get("mode")
    if mode not in {"history_replay", "history_compacted", "runtime_resume"}:
        mode = "history_replay"
    return {"mode": mode, "resumed": bool(value.get("resumed", False))}


async def ensure_run(
    db: AsyncSession,
    *,
    conversation: AgentConversation,
    turn: AgentConversationTurn,
    identity: RequestIdentity,
    commit: bool = True,
) -> AgentRun:
    """Create exactly one AgentRun for a turn and pin it to the conversation session."""

    if turn.agent_run_id:
        run = await db.get(AgentRun, turn.agent_run_id)
        if run is None:
            raise RuntimeError("conversation turn references a missing Agent run")
        return run

    agent_session = (
        await db.get(AgentSession, conversation.agent_session_id)
        if conversation.agent_session_id
        else None
    )
    if agent_session is None:
        agent_session = AgentSession(
            workspace_id=conversation.workspace_id,
            actor_subject=identity.subject,
            context={
                "conversation_id": conversation.id,
                "execution_binding": dict(conversation.execution_binding or {}),
            },
        )
        db.add(agent_session)
        await db.flush()
        conversation.agent_session_id = agent_session.id
    elif agent_session.actor_subject not in {None, identity.subject}:
        raise RuntimeError("conversation Agent session belongs to another identity")
    elif (
        agent_session.workspace_id != conversation.workspace_id
        or not isinstance(agent_session.context, dict)
        or agent_session.context.get("conversation_id") != conversation.id
    ):
        raise RuntimeError("conversation Agent session binding is invalid")

    run = AgentRun(
        session_id=agent_session.id,
        kind="conversation",
        status="queued",
        goal=turn.user_content,
        request_payload={
            "conversation_id": conversation.id,
            "turn_id": turn.id,
            "execution_binding": dict(conversation.execution_binding or {}),
            "continuation": {"mode": "history_replay", "resumed": False},
        },
    )
    db.add(run)
    await db.flush()
    turn.agent_run_id = run.id
    if commit:
        await db.commit()
        await db.refresh(run)
        await db.refresh(turn)
    return run


async def begin_run(db: AsyncSession, run_id: str) -> AgentRun | None:
    result = await db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "queued")
        .values(status="running")
    )
    if result.rowcount != 1:
        await db.rollback()
        return None
    run = await db.get(AgentRun, run_id)
    if run is None:
        await db.rollback()
        return None
    await append_event(
        db,
        run=run,
        event_type="run.started",
        payload={
            "label": "开始处理",
            "detail": "已接收请求，正在建立执行上下文。",
            "state": "active",
        },
    )
    await db.commit()
    return run


async def append_event(
    db: AsyncSession,
    *,
    run: AgentRun,
    event_type: str,
    payload: dict[str, Any],
) -> AgentRunEvent:
    event = AgentRunEvent(
        run_id=run.id,
        sequence=run.next_event_sequence,
        event_type=event_type,
        payload=dict(payload),
    )
    run.next_event_sequence += 1
    db.add(event)
    await db.flush()
    return event


async def record_activity(run_id: str, event: dict[str, Any]) -> None:
    sanitized = sanitize_activity_event(event)
    if sanitized is None:
        return
    event_type, payload = sanitized
    async with AsyncSessionLocal() as db:
        run = await db.get(AgentRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return
        await append_event(
            db,
            run=run,
            event_type=event_type,
            payload=payload,
        )
        await db.commit()


def sanitize_activity_event(
    event: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    event_type = event.get("type")
    if not isinstance(event_type, str) or event_type not in _ACTIVITY_EVENT_TYPES:
        return None
    return event_type, _public_activity_payload(event)


async def finish_run(
    db: AsyncSession,
    *,
    run: AgentRun,
    reply: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if run.status in TERMINAL_RUN_STATUSES:
        return
    run.status = "failed" if error else "completed"
    run.reply_payload = reply
    run.error_message = error
    await append_event(
        db,
        run=run,
        event_type="run.failed" if error else "reply",
        payload=(
            {
                "label": "处理未完成",
                "detail": error or "处理失败",
                "state": "failed",
            }
            if error
            else {
                "label": "结果已就绪",
                "detail": "本次处理已返回结果。",
                "state": "completed",
                "reply": reply or {},
            }
        ),
    )
    await db.commit()


def schedule(run_id: str, identity: RequestIdentity) -> None:
    existing = _background_runs.get(run_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_execute_background(run_id, identity))
    _background_runs[run_id] = task
    task.add_done_callback(lambda completed: _forget(run_id, completed))


def _forget(run_id: str, task: asyncio.Task[None]) -> None:
    if _background_runs.get(run_id) is task:
        _background_runs.pop(run_id, None)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        logger.error("background Agent conversation run failed: %s", exception)


def _public_activity_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("label", "detail", "state", "recovery"):
        value = event.get(key)
        if isinstance(value, str):
            payload[key] = _SENSITIVE_EVENT_VALUE.sub("[REDACTED]", value)[:4_000]
    status_value = event.get("status")
    if isinstance(status_value, int) and not isinstance(status_value, bool):
        payload["status"] = status_value
    target = event.get("target")
    if isinstance(target, dict):
        public_target = {
            key: value[:255]
            for key in ("type", "id")
            if isinstance((value := target.get(key)), str)
        }
        if public_target:
            payload["target"] = public_target
    return payload


async def _execute_background(run_id: str, identity: RequestIdentity) -> None:
    from backend.services import agent_conversation_service

    await agent_conversation_service.execute_run(run_id, identity)


async def shutdown_background_runs() -> None:
    tasks = list(_background_runs.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_runs.clear()


async def get_run_events(
    db: AsyncSession,
    *,
    run_id: str,
    after_sequence: int,
    limit: int,
) -> tuple[AgentRun, list[AgentRunEvent]]:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise RuntimeError("Agent run not found")
    rows = list(
        await db.scalars(
            select(AgentRunEvent)
            .where(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.sequence > after_sequence,
            )
            .order_by(AgentRunEvent.sequence.asc())
            .limit(limit)
        )
    )
    return run, rows


async def recover_interrupted_runs(db: AsyncSession) -> int:
    """Fail closed after process restart; never redispatch persisted work."""

    runs = list(
        await db.scalars(
            select(AgentRun).where(
                AgentRun.kind == "conversation",
                AgentRun.status.in_(("queued", "running")),
            )
        )
    )
    recovered = 0
    for run in runs:
        if run.id in _background_runs:
            continue
        run.status = "interrupted"
        run.error_message = "Agent run was interrupted before completion"
        await append_event(
            db,
            run=run,
            event_type="run.interrupted",
            payload={
                "label": "处理已中断",
                "detail": "服务重启前任务未完成；为避免重复外部操作，任务不会自动重试。",
                "state": "failed",
            },
        )
        turn_id = (
            run.request_payload.get("turn_id") if isinstance(run.request_payload, dict) else None
        )
        if isinstance(turn_id, str):
            turn = await db.get(AgentConversationTurn, turn_id)
            if turn is not None and turn.status in {
                AgentConversationTurnStatus.QUEUED.value,
                AgentConversationTurnStatus.RUNNING.value,
            }:
                turn.status = AgentConversationTurnStatus.INTERRUPTED.value
                turn.error_code = "run_interrupted"
                turn.error_message = run.error_message
                turn.active_slot = None
        recovered += 1
    orphan_turns = list(
        await db.scalars(
            select(AgentConversationTurn).where(
                AgentConversationTurn.active_slot.is_not(None),
                AgentConversationTurn.agent_run_id.is_(None),
                AgentConversationTurn.status.in_(("queued", "running")),
            )
        )
    )
    for turn in orphan_turns:
        turn.status = AgentConversationTurnStatus.INTERRUPTED.value
        turn.error_code = "run_interrupted"
        turn.error_message = "Conversation execution was interrupted before its run was recorded"
        turn.active_slot = None
        recovered += 1
    if recovered:
        await db.commit()
    return recovered
