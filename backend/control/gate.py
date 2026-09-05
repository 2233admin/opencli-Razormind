"""gate: the AND-ed execution gate the Control Cycle evaluates per
suggested action before the actuator may execute it (issue 03 / PR-Control-4).

See docs/CONTROL_THEORY_ARCHITECTURE.md, ADR-0007. Every condition below
must pass; the first failing condition determines ``GateResult.blocked_by``.
Order matters only for diagnostics (which reason a caller sees) — every gate
is independently sufficient to block, so evaluation order does not change
correctness, but kill-switch is checked first since it is meant to be an
instant, unconditional short-circuit.

This module reads (never writes) the Evidence Ledger and the kill switch; it
never itself executes anything — see ``backend.control.actuator`` for that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control import kill_switch
from backend.control.ledger import ensure_utc
from backend.control.report import tally
from backend.models.control_action import ControlActionRecord


@dataclass
class GateResult:
    allowed: bool
    blocked_by: str | None = None
    detail: dict | None = None

    @classmethod
    def allow(cls) -> GateResult:
        return cls(allowed=True)

    @classmethod
    def block(cls, reason: str, **detail) -> GateResult:
        return cls(allowed=False, blocked_by=reason, detail=detail or None)


async def evaluate_gate(
    session: AsyncSession,
    *,
    source_id: str,
    action_type: str,
    state: str,
    now: datetime,
    control_mode: str,
    min_samples: int,
    min_recovery_rate: float,
    cooldown_seconds: int,
    max_actions_per_hour: int,
) -> GateResult:
    """Evaluate every AND-ed execution-gate condition for one candidate
    (source_id, action_type, state) execution. All reads only; the caller
    (``backend.control.cycle``) decides what to do with the result.
    """
    # 1. Kill switch — instant, unconditional short-circuit.
    if kill_switch.is_engaged():
        return GateResult.block("kill_switch")

    # 2. Automatic Mode must be explicitly on.
    if control_mode != "automatic":
        return GateResult.block("control_mode_not_automatic", control_mode=control_mode)

    # 3. Evidence gate: the (state, action_type) advisory-report bucket must
    #    clear both a minimum sample size and a minimum recovery rate — the
    #    SAME bucketing/tally math the human-facing advisory report uses
    #    (backend.control.report), so the actuator can never execute on
    #    numbers the operator's report disagrees with.
    rows = (
        (
            await session.execute(
                select(ControlActionRecord)
                .where(ControlActionRecord.state == state)
                .where(ControlActionRecord.action_type == action_type)
            )
        )
        .scalars()
        .all()
    )
    bucket_totals = tally(rows)
    samples = bucket_totals["evaluated"]
    recovery_rate = bucket_totals["recovery_rate"]

    if samples < min_samples:
        return GateResult.block(
            "below_min_samples", samples=samples, required=min_samples
        )
    if recovery_rate is None or recovery_rate < min_recovery_rate:
        return GateResult.block(
            "below_min_recovery_rate",
            recovery_rate=recovery_rate,
            required=min_recovery_rate,
        )

    # 4. Per-(source, action_type) cooldown: the most recent EXECUTED row for
    #    this exact pair must be older than cooldown_seconds (or not exist).
    latest_executed = (
        (
            await session.execute(
                select(ControlActionRecord)
                .where(ControlActionRecord.source_id == source_id)
                .where(ControlActionRecord.action_type == action_type)
                .where(ControlActionRecord.executed.is_(True))
                .order_by(ControlActionRecord.created_at.desc())
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if latest_executed is not None:
        age = (now - ensure_utc(latest_executed.created_at)).total_seconds()
        if age < cooldown_seconds:
            return GateResult.block(
                "cooldown_active", age_seconds=age, required=cooldown_seconds
            )

    # 5. Global max-actions-per-hour cap, across every source/action_type.
    hour_ago = now - timedelta(hours=1)
    executed_last_hour = (
        (
            await session.execute(
                select(ControlActionRecord)
                .where(ControlActionRecord.executed.is_(True))
                .where(ControlActionRecord.created_at >= hour_ago)
            )
        )
        .scalars()
        .all()
    )
    if len(executed_last_hour) >= max_actions_per_hour:
        return GateResult.block(
            "hourly_cap_exhausted",
            executed_last_hour=len(executed_last_hour),
            cap=max_actions_per_hour,
        )

    # 6. Idempotency / in-flight dedup: no unresolved identical executed row
    #    for the same (source, action_type, state) — evaluated_at IS NULL
    #    means its outcome hasn't been judged yet, i.e. still "in flight".
    unresolved = (
        (
            await session.execute(
                select(ControlActionRecord)
                .where(ControlActionRecord.source_id == source_id)
                .where(ControlActionRecord.action_type == action_type)
                .where(ControlActionRecord.state == state)
                .where(ControlActionRecord.executed.is_(True))
                .where(ControlActionRecord.evaluated_at.is_(None))
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if unresolved is not None:
        return GateResult.block("unresolved_in_flight", row_id=unresolved.id)

    return GateResult.allow()
