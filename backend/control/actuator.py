"""actuator: execute exactly three whitelisted control actions
(issue 03 / PR-Control-4).

See docs/CONTROL_THEORY_ARCHITECTURE.md, ADR-0004, ADR-0007. This module is
the ONLY code in the control layer allowed to mutate a ``DataSource`` or a
``CronSchedule`` on the control system's behalf — every other module in
``backend.control`` (evaluator, policies, ledger, outcomes, service) is
advisory-only by hard rule.

Whitelist (exactly three, ADR-0004): ``increase_interval``, ``pause``,
``require_review``. A suggested ``ControlAction.action_type`` outside this
whitelist (or one of the two aliases below) never executes as itself —
``execute_action`` downgrades it to ``require_review`` (the Require-Review
Downgrade) while preserving the ORIGINAL suggested action_type/payload in the
ledger row it writes, so an audit of what was suggested is never lost to the
downgrade.

Alias mapping (``backend.control.policies.suggest_actions`` predates this
module and already named its suggestions before the whitelist was fixed):
``pause_source`` and ``pause_low_priority`` -> ``pause``;
``require_auth_review`` -> ``require_review``. Both are semantically already
what the whitelist entry does, so they execute AS the whitelisted action
(not a downgrade) — see :func:`resolve_action_type`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control.models import ControlAction
from backend.models.schedule import CronSchedule
from backend.models.source import DataSource

#: The exactly-three whitelist (ADR-0004).
INCREASE_INTERVAL = "increase_interval"
PAUSE = "pause"
REQUIRE_REVIEW = "require_review"
WHITELIST = frozenset({INCREASE_INTERVAL, PAUSE, REQUIRE_REVIEW})

#: Suggested action_types that already MEAN a whitelisted action under a
#: different historical name (backend.control.policies) — these execute AS
#: the mapped whitelist entry, not as a Require-Review Downgrade.
_ALIASES = {
    "pause_source": PAUSE,
    "pause_low_priority": PAUSE,
    "require_auth_review": REQUIRE_REVIEW,
}

_STEP_CRON_RE = re.compile(r"^\*/(\d+) \* \* \* \*$")


def resolve_action_type(suggested_action_type: str) -> tuple[str, bool]:
    """Map a suggested action_type onto (whitelisted_action_type, downgraded).

    ``downgraded`` is True only for the Require-Review Downgrade path
    (ADR-0004) — a suggestion outside the whitelist AND outside the known
    aliases. Whitelisted names and known aliases resolve with
    ``downgraded=False``: they execute as themselves/their mapped action,
    not as a downgrade.
    """
    if suggested_action_type in WHITELIST:
        return suggested_action_type, False
    if suggested_action_type in _ALIASES:
        return _ALIASES[suggested_action_type], False
    return REQUIRE_REVIEW, True


@dataclass
class ExecutionResult:
    """What actually happened when :func:`execute_action` ran — enough for
    the caller (the Control Cycle) to build the executed ledger row."""

    executed_action_type: str
    downgraded: bool
    original_action_type: str
    original_payload: dict[str, Any]
    detail: dict[str, Any] = field(default_factory=dict)
    inverse: Optional["ExecutionResult"] = None


def _parse_step_minutes(cron_expression: str) -> Optional[int]:
    """Extract N from a ``*/N * * * *`` step-minute cron expression, or None
    for any other shape (fixed times, day-of-week constraints, etc — the
    bounded-backoff math below only knows how to widen a simple step)."""
    match = _STEP_CRON_RE.match(cron_expression.strip())
    return int(match.group(1)) if match else None


async def _apply_increase_interval(
    session: AsyncSession, *, source_id: str, factor: float, max_minutes: int
) -> dict[str, Any]:
    """Bounded multiplicative backoff on every enabled CronSchedule row
    belonging to ``source_id`` whose cron_expression is a simple
    ``*/N * * * *`` step. Schedules with any other shape are left untouched
    and reported as skipped — the actuator degrades to a no-op for a shape it
    cannot safely widen rather than guessing.
    """
    schedules = (
        (
            await session.execute(
                select(CronSchedule)
                .where(CronSchedule.source_id == source_id)
                .where(CronSchedule.enabled.is_(True))
            )
        )
        .scalars()
        .all()
    )

    widened: list[dict[str, Any]] = []
    skipped: list[str] = []
    for sched in schedules:
        minutes = _parse_step_minutes(sched.cron_expression)
        if minutes is None:
            skipped.append(sched.id)
            continue
        new_minutes = min(max(1, round(minutes * factor)), max_minutes)
        if new_minutes == minutes:
            continue
        before = sched.cron_expression
        sched.cron_expression = f"*/{new_minutes} * * * *"
        widened.append({"schedule_id": sched.id, "before": before, "after": sched.cron_expression})

    if widened:
        await session.flush()

    return {"widened": widened, "skipped_unparseable": skipped}


async def _apply_pause(
    session: AsyncSession, *, source: DataSource, now: datetime, ttl_seconds: int
) -> dict[str, Any]:
    """Disable the source and set its TTL. Idempotent-ish: re-pausing an
    already-paused source just refreshes the TTL from ``now``."""
    was_enabled = source.enabled
    source.enabled = False
    source.paused_until = now + timedelta(seconds=ttl_seconds)
    await session.flush()
    return {
        "was_enabled": was_enabled,
        "paused_until": source.paused_until.isoformat(),
    }


async def _apply_resume(session: AsyncSession, *, source: DataSource) -> dict[str, Any]:
    """The inverse of :func:`_apply_pause`: re-enable, clear the TTL."""
    source.enabled = True
    source.paused_until = None
    await session.flush()
    return {"resumed": True}


async def _apply_require_review(session: AsyncSession, *, source: DataSource) -> dict[str, Any]:
    already = source.review_required
    source.review_required = True
    await session.flush()
    return {"already_flagged": already}


async def execute_action(
    session: AsyncSession,
    *,
    source: DataSource,
    action: ControlAction,
    now: datetime,
    increase_interval_factor: float,
    increase_interval_max_minutes: int,
    pause_ttl_seconds: int,
) -> ExecutionResult:
    """Execute ``action`` against ``source``, applying the Require-Review
    Downgrade (ADR-0004) for anything outside the whitelist/alias set.

    Never raises on a normal execution path — DB flush errors propagate (the
    caller's transaction handles them), but there is no "action failed,
    swallow it" branch here: by the time this is called, every gate has
    already passed, so the only remaining outcomes are "executed" or
    "downgraded-and-executed-as-require_review".
    """
    executed_type, downgraded = resolve_action_type(action.action_type)

    if executed_type == INCREASE_INTERVAL:
        detail = await _apply_increase_interval(
            session,
            source_id=source.id,
            factor=increase_interval_factor,
            max_minutes=increase_interval_max_minutes,
        )
    elif executed_type == PAUSE:
        detail = await _apply_pause(session, source=source, now=now, ttl_seconds=pause_ttl_seconds)
    else:  # REQUIRE_REVIEW (whitelisted or downgraded)
        detail = await _apply_require_review(session, source=source)

    return ExecutionResult(
        executed_action_type=executed_type,
        downgraded=downgraded,
        original_action_type=action.action_type,
        original_payload=dict(action.payload),
        detail=detail,
    )


async def pause_source_for_captcha(
    session: AsyncSession, *, source: DataSource, now: datetime, ttl_seconds: int
) -> dict[str, Any]:
    """Pause a source that hit a human-cleared challenge wall (captcha) and
    flag it for review.

    The pipeline calls this when a channel classifies a collect failure as
    ``captcha_challenge`` (see ``backend.pipeline.error_taxonomy.is_captcha``):
    automatic retry would burn budget on the same wall, so instead the source
    is disabled for ``ttl_seconds`` (the normal pause TTL semantics — the
    scheduler already skips disabled sources, and
    :func:`auto_resume_expired_pauses` re-enables it when the wall should have
    cooled down) and ``review_required`` is set so the UI surfaces it for a
    human to confirm/clear.

    Stays inside the actuator: this module remains the ONLY code allowed to
    mutate a ``DataSource`` on the control system's behalf.
    """
    detail = await _apply_pause(session, source=source, now=now, ttl_seconds=ttl_seconds)
    review_detail = await _apply_require_review(session, source=source)
    detail["review_required"] = review_detail["already_flagged"] or True
    return detail


async def auto_resume_expired_pauses(
    session: AsyncSession, *, now: datetime
) -> list[tuple[DataSource, ExecutionResult]]:
    """Resume every source whose ``paused_until`` TTL has passed. Returns the
    (source, inverse ExecutionResult) pairs so the caller can write one
    ledger row per auto-resume — the "inverse action" the issue requires."""
    sources = (
        (
            await session.execute(
                select(DataSource)
                .where(DataSource.paused_until.isnot(None))
                .where(DataSource.paused_until <= now)
            )
        )
        .scalars()
        .all()
    )

    results: list[tuple[DataSource, ExecutionResult]] = []
    for source in sources:
        detail = await _apply_resume(session, source=source)
        results.append(
            (
                source,
                ExecutionResult(
                    executed_action_type="resume",
                    downgraded=False,
                    original_action_type="resume",
                    original_payload={},
                    detail=detail,
                ),
            )
        )
    return results
