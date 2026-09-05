"""ControlActionRecord (DB row): the advisory evidence ledger (PR-Control-3.5).

See docs/CONTROL_THEORY_ARCHITECTURE.md §4-5. PR-Control-3's feedback law
(``backend.control.policies``) suggests ControlActions but nothing records
whether those suggestions were RIGHT. This table closes the observation half
of that loop: every advisory suggestion the control-state endpoint surfaces
lands here as one row, and ``backend.control.outcomes`` later judges it
against the source's subsequent ``source_measurements`` rows — did the
triggering state clear on its own ("recovered"), stay broken ("persisted"),
or did the source simply never run again ("insufficient_data")?

This is EVIDENCE, not execution: writing a row here never touches the
``DataSource``, the scheduler, or any channel behavior. The ``mode`` and
``executed`` columns exist so a future PR-Control-4 actuator can reuse the
same ledger for actions it actually performs — in this PR ``mode`` is always
"advisory" and ``executed`` is always False.

Naming note: ``backend.control.models.ControlAction`` is the pure Pydantic
suggestion contract (in-memory, one decision); this ORM row is that
suggestion PLUS its decision context (state/mode/measurement snapshot) and
its eventual outcome judgment — a superset, hence the distinct name.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class ControlActionRecord(TimestampMixin):
    """One suggested (future: executed) control action, with enough context
    to judge later whether the suggestion was justified.

    Column groups:
      * identity: source_id, run_id, measurement_id (the source_measurements
        row that fed the decision — null when the measurement came from the
        PR-Control-2 TaskRunEvent fallback path, which has no persisted row)
      * decision: mode ("advisory" | "automatic"), state (SourceControlState
        value at decision time), action_type, reason, payload — mirrors
        ``backend.control.models.ControlAction``
      * execution: executed — always False in this PR (nothing acts)
      * outcome judgment (written later by ``backend.control.outcomes``):
        evaluated_at, outcome ("recovered" | "persisted" |
        "insufficient_data"), outcome_detail
      * evidence: measurement_before — the full SourceMeasurement contract
        dump at decision time, kept even when measurement_id is set so the
        decision stays replayable without a join (and stays available at all
        for fallback-path decisions).
    """

    __tablename__ = "control_actions"

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    measurement_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    #: "advisory" in this PR; "automatic" reserved for PR-Control-4's actuator.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    #: SourceControlState.value at decision time — the trigger this row's
    #: outcome is later judged against.
    state: Mapped[str] = mapped_column(String(32), nullable=False)

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    #: Whether anything actually acted on this. Always False in this PR —
    #: the column exists so PR-Control-4 can write executed=True rows into
    #: the SAME ledger and the advisory-report can compare the two.
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: SourceMeasurement (pure contract) dump at decision time.
    measurement_before: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: recovered | persisted | insufficient_data — see backend.control.outcomes.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
