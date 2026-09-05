"""cycle: the Control Cycle body (issue 03 / PR-Control-4).

See docs/CONTROL_THEORY_ARCHITECTURE.md, ADR-0007. ``run_control_cycle_once``
is a plain async function taking a session and ``now`` — directly invocable
in tests without the asyncio wrapper (``backend.control.cycle_task``). Each
tick:

    1. For every (enabled) source: run the shared decision path
       (``backend.control.service.decide_for_source``) with the source's
       resolved objective (issue 02) and a freshly-built system_context
       (``backend.control.system_context.build_system_context`` — the ONE
       builder the endpoint and this cycle both use).
    2. Judge ripe pending outcomes (``backend.control.outcomes.
       evaluate_pending_outcomes``) — previously lazy, now runs every tick.
    3. Auto-resume any source whose pause TTL has expired, recording the
       inverse action in the ledger.
    4. For every suggestion decide_for_source produced this tick, evaluate
       the execution gate (``backend.control.gate.evaluate_gate``); execute
       (``backend.control.actuator.execute_action``) and record an executed
       ledger row (``backend.control.ledger.record_executed_action``) only
       when the gate allows.

HARD RULE: in Advisory Mode (``Settings.control_mode != "automatic"``) or
with the kill switch engaged, this function writes ledger rows exactly like
today (via decide_for_source's own best-effort advisory recording) but NEVER
mutates a DataSource/CronSchedule — the gate's mode/kill-switch checks are
evaluated before ANY actuator call. Mirrors the zero-mutation guarantee the
control-state endpoint has always had.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.control import actuator
from backend.control.gate import evaluate_gate
from backend.control.ledger import record_executed_action
from backend.control.measurements import SourceMeasurement
from backend.control.models import SourceControlState
from backend.control.objectives import resolve_objective
from backend.control.outcomes import evaluate_pending_outcomes
from backend.control.service import decide_for_source
from backend.control.system_context import build_system_context
from backend.models.source import DataSource

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """Everything one ``run_control_cycle_once`` pass did — for tests and
    for structured logging by the asyncio wrapper."""

    sources_decided: int = 0
    suggestions_seen: int = 0
    executions: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    auto_resumed: list[str] = field(default_factory=list)
    outcome_counts: dict = field(default_factory=dict)


async def run_control_cycle_once(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> CycleResult:
    """Run one full Control Cycle tick. Flushes but never commits — callers
    (the asyncio wrapper) own the session's commit/rollback lifecycle, same
    convention as every other control-layer write path."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    result = CycleResult()

    sources = (
        await session.execute(select(DataSource).where(DataSource.enabled.is_(True)))
    ).scalars().all()

    for source in sources:
        objective = resolve_objective(source.objective_override)
        system_context = await build_system_context(objective)

        decision = await decide_for_source(
            session,
            source_id=source.id,
            objective=objective,
            system_context={
                "odp_backpressured": system_context.odp_backpressured,
                "available": system_context.available,
            },
            mode=settings.control_mode,
            dedup_seconds=settings.control_advisory_dedup_seconds,
        )
        result.sources_decided += 1

        if decision.measurement is None or not decision.suggested_actions:
            continue

        for action in decision.suggested_actions:
            result.suggestions_seen += 1
            executed_type, _downgraded = actuator.resolve_action_type(action.action_type)

            gate_result = await evaluate_gate(
                session,
                source_id=source.id,
                action_type=executed_type,
                state=decision.control_state.value,
                now=now,
                control_mode=settings.control_mode,
                min_samples=settings.control_gate_min_samples,
                min_recovery_rate=settings.control_gate_min_recovery_rate,
                cooldown_seconds=settings.control_action_cooldown_seconds,
                max_actions_per_hour=settings.control_max_actions_per_hour,
            )

            if not gate_result.allowed:
                result.blocked.append(
                    {
                        "source_id": source.id,
                        "action_type": action.action_type,
                        "resolved_action_type": executed_type,
                        "state": decision.control_state.value,
                        "blocked_by": gate_result.blocked_by,
                        "detail": gate_result.detail,
                    }
                )
                continue

            exec_result = await actuator.execute_action(
                session,
                source=source,
                action=action,
                now=now,
                increase_interval_factor=settings.control_increase_interval_factor,
                increase_interval_max_minutes=settings.control_increase_interval_max_minutes,
                pause_ttl_seconds=settings.control_pause_ttl_seconds,
            )

            ledger_row = await record_executed_action(
                session,
                source_id=source.id,
                state=decision.control_state,
                action_type=exec_result.executed_action_type,
                reason=action.reason,
                payload={
                    **exec_result.detail,
                    "original_action_type": exec_result.original_action_type,
                    "original_payload": exec_result.original_payload,
                    "downgraded": exec_result.downgraded,
                },
                measurement=decision.measurement,
                measurement_row_id=decision.measurement_row_id,
                run_id=decision.run_id,
            )

            result.executions.append(
                {
                    "source_id": source.id,
                    "action_type": exec_result.executed_action_type,
                    "original_action_type": exec_result.original_action_type,
                    "downgraded": exec_result.downgraded,
                    "ledger_row_id": ledger_row.id,
                }
            )

    # Auto-resume expired pauses BEFORE outcome judgment so a resumed
    # source's inverse action is itself part of this tick's ledger, and its
    # own outcome can be judged on a later tick like anything else.
    resumed = await actuator.auto_resume_expired_pauses(session, now=now)
    for source, inverse in resumed:
        await record_executed_action(
            session,
            source_id=source.id,
            state=SourceControlState.PAUSED,
            action_type="resume",
            reason="Pause TTL expired — Control Cycle auto-resumed the source.",
            payload=inverse.detail,
            # No live sensor reading drives an auto-resume (it fires purely
            # off the TTL, independent of this tick's per-source decisions) —
            # an explicit zero-evidence placeholder is the honest
            # measurement_before snapshot, not a stale reuse of whichever
            # source happened to be decided last in the loop above.
            measurement=_empty_measurement(source.id, now=now),
            measurement_row_id=None,
            run_id=None,
        )
        result.auto_resumed.append(source.id)

    result.outcome_counts = await evaluate_pending_outcomes(session, now=now)

    return result


def _empty_measurement(source_id: str, *, now: datetime) -> SourceMeasurement:
    """A zero-evidence SourceMeasurement placeholder for the auto-resume
    ledger row: an auto-resume is TTL-driven, not sensor-driven, so there is
    no real reading to snapshot — "no evidence" is itself honest evidence."""
    return SourceMeasurement(
        source_id=source_id,
        run_id="control-cycle-auto-resume",
        accepted=0,
        duplicates=0,
        rejected=0,
        fetch_latency_ms=0,
        error_rate=0.0,
        duplicate_rate=0.0,
        cursor_advanced=False,
        observed_at=now,
    )
