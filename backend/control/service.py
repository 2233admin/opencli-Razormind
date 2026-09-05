"""service: the one measure -> evaluate -> record decision path (PR-Control-3,
PR-Control-3.5, prefactored ahead of PR-Control-4).

See docs/CONTROL_THEORY_ARCHITECTURE.md. The control-state endpoint
(``backend.api.v1.sources.get_source_control_state``) and the future Control
Cycle (issue 03) must never disagree about a source's state because one of
them read the sensors differently — the same principle already documented in
``backend.control.outcomes`` for outcome judgment. This module is that single
path: aggregate the source's latest sensor reading (preferring the persisted
``source_measurements`` table, falling back to the PR-Control-2 TaskRunEvent
path — see ``backend.control.aggregation``), compute a rolling trend, derive
a full ``SourceControlState`` (``backend.control.evaluator``), map that state
onto advisory ``ControlAction`` suggestions (``backend.control.policies``),
and persist a best-effort record to the Evidence Ledger
(``backend.control.ledger.record_advisory_actions``).

HARD RULE, unchanged from the endpoint's original docstring: this function is
ADVISORY ONLY. It never mutates the source's ``DataSource`` row and never
calls the scheduler. Ledger recording is best-effort — a ledger write failure
is logged and swallowed, never raised, so a caller that only wants a read
(e.g. a GET endpoint) can never be turned into a 500 by evidence bookkeeping.
Callers that need read-only decision data without writing evidence should
pass ``record_evidence=False`` (the Control Cycle records evidence itself
after this call, keyed off its own tick semantics, so it does not need this
function to do it twice).

system_context is NOT computed here — it is a source-independent snapshot of
shared ODP-plane state that callers already have their own idioms for
building (see ``backend.api.v1.sources._build_system_context``). Callers pass
it in as plain dict data (schema mirrors ``SystemContextRead``'s two fields
the evaluator consumes: ``odp_backpressured``, ``available``); this keeps the
function's imports free of API-layer collector wiring so it stays cleanly
callable from the future Control Cycle too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.control import aggregation, evaluator
from backend.control.coverage import (
    SensorCoverage,
    compute_sensor_coverage,
    derive_confidence,
    missing_signals,
)
from backend.control.ledger import record_advisory_actions
from backend.control.measurements import SourceMeasurement
from backend.control.models import ControlAction, SourceControlState
from backend.control.objectives import SourceObjective

logger = logging.getLogger(__name__)


@dataclass
class TrendSummaryData:
    """Plain-data mirror of ``backend.control.aggregation.TrendSummary`` —
    kept here so callers (the endpoint's response schema, a future Control
    Cycle) don't need to import the aggregation module's internal type.

    ``provenance`` (issue 06) mirrors ``SourceTrend.provenance``:
    ``"measurements"`` when the trend summarizes persisted
    ``source_measurements`` rows, ``"run_history"`` when it was derived from
    TaskRun/TaskRunEvent evidence because the source has no measurement rows
    yet. Informational only — it never feeds the evaluator and never changes
    coverage/confidence."""

    window: int
    zero_accepted_streak: int
    avg_error_rate: float
    rate_limited_runs: int
    provenance: str = "measurements"


@dataclass
class SourceDecision:
    """Everything a caller of :func:`decide_for_source` needs: the sensor
    reading, its provenance, the derived trend, the evaluated state, the
    advisory suggestions, and the coverage/confidence signals describing how
    much of that state to trust.

    """
    measurement: SourceMeasurement | None
    measurement_row_id: str | None
    run_id: str | None
    trend: TrendSummaryData | None
    control_state: SourceControlState | None
    confidence: str | None
    coverage: SensorCoverage | None

    missing_signals: list[str] = field(default_factory=list)
    suggested_actions: list[ControlAction] = field(default_factory=list)
    ledger_rows_written: int = 0


async def decide_for_source(
    session: AsyncSession,
    *,
    source_id: str,
    objective: SourceObjective,
    system_context: dict[str, Any],
    mode: str,
    dedup_seconds: int,
    record_evidence: bool = True,
) -> SourceDecision:
    """Run the shared measure -> evaluate -> (best-effort) record path for one
    source and return everything a caller needs to build a response or act on.

    ``system_context`` is passed through unchanged to ``evaluator.evaluate``
    (only the ``odp_backpressured``/``available`` keys are read); callers
    build it however fits their context (the endpoint uses
    ``_build_system_context``; a background cycle could reuse the same
    collector directly).

    ``mode``/``dedup_seconds`` are forwarded to
    ``backend.control.ledger.record_advisory_actions`` verbatim — pass
    ``Settings.control_mode`` / ``Settings.control_advisory_dedup_seconds``
    (or a Control Cycle's own tick-scoped equivalents).

    Never raises on ledger-write failure: recording evidence is best-effort,
    exactly as it was inline in the control-state endpoint before this
    function existed. Returns a :class:`SourceDecision` with
    ``ledger_rows_written=0`` when there was nothing to suggest, evidence
    recording was skipped (``record_evidence=False``), or the write failed.
    """
    measurement_row = await aggregation.latest_measurement_row(session, source_id)
    measurement = (
        aggregation.row_to_measurement(measurement_row)
        if measurement_row is not None
        else await aggregation.build_measurement(session, source_id)
    )
    # Issue 06: prefer the measurement-rows trend; a source with ZERO
    # source_measurements rows falls back to a trend derived from its recent
    # TaskRun/TaskRunEvent history (same evidence build_measurement's fallback
    # reads), tagged provenance="run_history" so coverage stays honest.
    trend_summary = await aggregation.build_trend_with_fallback(session, source_id)

    measurement_row_id = measurement_row.id if measurement_row is not None else None
    run_id = measurement_row.run_id if measurement_row is not None else None

    trend_data = (
        TrendSummaryData(
            window=trend_summary.window,
            zero_accepted_streak=trend_summary.zero_accepted_streak,
            avg_error_rate=trend_summary.avg_error_rate,
            rate_limited_runs=trend_summary.rate_limited_runs,
            provenance=trend_summary.provenance,
        )
        if trend_summary is not None
        else None
    )

    if measurement is None:
        return SourceDecision(
            measurement=None,
            measurement_row_id=measurement_row_id,
            run_id=run_id,
            trend=trend_data,
            control_state=None,
            confidence=None,
            coverage=None,
            missing_signals=[],
            suggested_actions=[],
            ledger_rows_written=0,
        )

    control_state = evaluator.evaluate(
        measurement,
        objective,
        trend=(
            {
                "window": trend_summary.window,
                "zero_accepted_streak": trend_summary.zero_accepted_streak,
                "avg_error_rate": trend_summary.avg_error_rate,
                "rate_limited_runs": trend_summary.rate_limited_runs,
            }
            if trend_summary is not None
            else None
        ),
        system_context=system_context,
    )
    coverage = compute_sensor_coverage(measurement)
    confidence = derive_confidence(coverage)
    missing = missing_signals(coverage)

    from backend.control.policies import suggest_actions

    actions = suggest_actions(control_state, measurement, objective)

    written = 0
    if actions:
        # Advisory activity is observable (docs §2 principle 8:
        # "自动化必须可解释") — emit a TaskRunEvent-shaped audit trail via the
        # existing pipeline event sink. Best-effort: events.emit() already
        # swallows its own errors and never raises, and emitting is not
        # required for the decision to be correct.
        from backend.pipeline import events

        await events.emit(
            run_id=measurement.run_id,
            step="control_advisory",
            message=(
                f"advisory: {control_state.value} -> "
                f"{[a.action_type for a in actions]}"
            ),
            level="info",
            detail={
                "source_id": source_id,
                "control_state": control_state.value,
                "suggested_actions": [
                    {"action_type": a.action_type, "reason": a.reason, "payload": a.payload}
                    for a in actions
                ],
                "control_mode": mode,
            },
        )

        if record_evidence:
            # PR-Control-3.5: persist each suggestion to the advisory evidence
            # ledger (backend.control.ledger) so a later outcome pass can
            # judge whether it was justified. Same session/transaction as the
            # caller — the ledger write shares the caller's commit/rollback
            # fate — and best-effort: recording evidence must never turn a
            # read-only, advisory caller (e.g. the control-state GET
            # endpoint) into a 500.
            try:
                written = await record_advisory_actions(
                    session,
                    source_id=source_id,
                    state=control_state,
                    actions=actions,
                    measurement=measurement,
                    measurement_row_id=measurement_row_id,
                    run_id=run_id,
                    mode=mode,
                    dedup_seconds=dedup_seconds,
                )
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning("control decision: ledger write failed: %s", exc)

    return SourceDecision(
        measurement=measurement,
        measurement_row_id=measurement_row_id,
        run_id=run_id,
        trend=trend_data,
        control_state=control_state,
        confidence=confidence,
        coverage=coverage,
        missing_signals=missing,
        suggested_actions=actions,
        ledger_rows_written=written,
    )
