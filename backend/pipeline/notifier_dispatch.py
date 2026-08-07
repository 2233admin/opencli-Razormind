"""Pipeline Step 5: Dispatch notifications based on rules.

Restructured (AUDIT C1/C23/C12/C18) into three phases so a slow notifier
send never holds the SQLite write lock, and the caller gets a real
sent/failed aggregate instead of an unconditional "done":

* Phase A (``session``, caller-provided, same contract as before): query
  matching rules, create every NotificationLog row as "pending", flush +
  COMMIT. No network I/O happens here.
* Phase B (no session open at all): perform the actual sends, sequentially,
  reusing one resolved notifier instance per rule (not re-looked-up per
  record).
* Phase C (a brand-new short-lived session opened internally): bulk-apply
  the phase B outcomes onto the phase A NotificationLog rows, single commit.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.notification import NotificationLog, NotificationRule
from backend.models.record import CollectedRecord
from backend.notifiers.base import AbstractNotifier, NotificationPayload, NotificationSendResult
from backend.notifiers.registry import get_notifier

logger = logging.getLogger(__name__)


def _ack_secret(config: dict) -> str:
    return str(config.get("ack_secret") or "")


def _normalize_send_result(result: bool | NotificationSendResult) -> tuple[bool, dict | None]:
    if isinstance(result, NotificationSendResult):
        return result.success, result.response_data
    return bool(result), None


@dataclass
class _PendingSend:
    """One (rule, record) send queued in phase A, executed in phase B.

    Plain data only (no ORM objects) — phase B runs with no session open, so
    nothing here may depend on a live session/identity map.
    """

    log_id: str
    notifier: AbstractNotifier
    notifier_config: dict[str, Any]
    payload: NotificationPayload
    ack_required: bool


@dataclass
class _SendOutcome:
    log_id: str
    status: str
    response_data: dict[str, Any] | None
    error_message: str | None
    ack_status: str


async def _send_one(task: _PendingSend) -> _SendOutcome:
    """Perform a single notifier send, never raising — a bad send degrades to
    a "failed" outcome so one broken rule can't abort the rest of phase B."""
    try:
        success, response_data = _normalize_send_result(
            await task.notifier.send(task.notifier_config, task.payload)
        )
        status = "sent" if success else "failed"
        error_msg = None
    except Exception as exc:
        status = "failed"
        response_data = None
        error_msg = str(exc)

    ack_status = "pending" if status == "sent" and task.ack_required else "not_required"
    return _SendOutcome(
        log_id=task.log_id,
        status=status,
        response_data=response_data,
        error_message=error_msg,
        ack_status=ack_status,
    )


async def dispatch_notifications(
    session: AsyncSession,
    source_id: str,
    records: list[CollectedRecord],
    trigger_event: str = "on_new_record",
    failure_payload: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Find matching notification rules and dispatch. Returns ``{"sent": n,
    "failed": m}`` — the real aggregate outcome (AUDIT C12: this used to be
    silent per-row-only bookkeeping the caller never inspected).

    ``failure_payload`` lets the ``on_task_failed`` producer fire with NO
    collected records: when ``records`` is empty and a failure payload is
    given, a synthetic record (never persisted) is used so the normal
    rule-matching / logging / send pipeline applies unchanged to task-failure
    notifications.
    """
    if not records and failure_payload:
        records = [
            CollectedRecord(
                task_id=str(failure_payload.get("task_id") or ""),
                source_id=source_id,
                raw_data={},
                normalized_data={
                    "title": f"Task failed: {source_id}",
                    "error": failure_payload.get("error"),
                    "error_type": failure_payload.get("error_type"),
                    "source_id": source_id,
                    "task_id": failure_payload.get("task_id"),
                },
                ai_enrichment=None,
                content_hash=f"task-failed-{uuid.uuid4().hex}",
            )
        ]
    if not records:
        return {"sent": 0, "failed": 0}

    # ── Phase A: query rules + create pending logs (caller's session) ───────
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.enabled.is_(True),
            NotificationRule.trigger_event == trigger_event,
            (NotificationRule.source_id == source_id)
            | (NotificationRule.source_id.is_(None)),
        )
    )
    rules = result.scalars().all()

    send_plan: list[_PendingSend] = []
    for rule in rules:
        try:
            notifier = get_notifier(rule.notifier_type)
        except ValueError:
            # AUDIT C18: a misconfigured rule used to die silently forever —
            # at least surface which rule/type so it's discoverable.
            logger.warning(
                "notification rule %s references unknown notifier_type=%r; skipping",
                rule.id, rule.notifier_type,
            )
            continue

        ack_required = bool(_ack_secret(rule.notifier_config))
        for record in records:
            log_id = str(uuid.uuid4())
            session.add(NotificationLog(
                id=log_id,
                rule_id=rule.id,
                record_id=record.id,
                status="pending",
                ack_status="not_required",
            ))
            send_plan.append(_PendingSend(
                log_id=log_id,
                notifier=notifier,
                notifier_config=rule.notifier_config,
                payload=NotificationPayload(
                    event=trigger_event,
                    source_id=source_id,
                    delivery_id=log_id,
                    record_id=record.id,
                    data=record.normalized_data,
                    ai_enrichment=record.ai_enrichment,
                ),
                ack_required=ack_required,
            ))

    if not send_plan:
        return {"sent": 0, "failed": 0}

    await session.flush()
    await session.commit()

    # ── Phase B: sends, sequential, no session open ──────────────────────────
    outcomes = [await _send_one(task) for task in send_plan]

    sent = sum(1 for outcome in outcomes if outcome.status == "sent")
    failed = len(outcomes) - sent

    # ── Phase C: persist outcomes in a fresh short-lived session ─────────────
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as write_session:
        for outcome in outcomes:
            log = await write_session.get(NotificationLog, outcome.log_id)
            if log is None:
                continue
            log.status = outcome.status
            log.response_data = outcome.response_data
            log.error_message = outcome.error_message
            log.ack_status = outcome.ack_status
        await write_session.commit()

    return {"sent": sent, "failed": failed}
