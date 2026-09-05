"""Response schemas for the read-only, advisory control-state endpoint
(PR-Control-3, building on PR-Control-2 + C0 Control Room v0).

PINNED CONTRACT: ``SourceControlStateRead`` is the exact response shape the
frontend agent builds against in parallel — field names and nesting must not
change without both sides agreeing. See the PR-Control-3 task brief for the
authoritative JSON shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.control.coverage import SensorCoverage
from backend.control.measurements import SourceMeasurement
from backend.control.models import SourceControlState
from backend.control.objectives import SourceObjective


class TrendRead(BaseModel):
    """Rolling-window summary over a source's recent source_measurements rows.

    PINNED: this exact four-field shape is what measurement-backed trends
    serialize as — no provenance key (its absence means measurement-backed;
    see :class:`FallbackTrendRead`). Null on the parent response only when
    the source has never run at all (no measurement rows AND no task-run
    history) — see ``backend.control.aggregation.build_trend_with_fallback``.
    """

    window: int
    zero_accepted_streak: int
    avg_error_rate: float
    rate_limited_runs: int


class FallbackTrendRead(TrendRead):
    """Issue 06 (additive): the trend shape for a PRE-MEASUREMENT source —
    zero source_measurements rows, trend derived from recent
    TaskRun/TaskRunEvent history instead (``backend.control.aggregation``'s
    run-history fallback).

    Identical four pinned fields plus a REQUIRED ``provenance`` marker, so a
    fallback trend is always distinguishable in the response and can never
    masquerade as measurement-backed sensor coverage. Coverage/confidence on
    the parent response are unaffected by which trend shape appears — they
    are derived from the measurement only.
    """

    provenance: Literal["run_history"]


class SystemContextRead(BaseModel):
    """Shared-infrastructure signals distinct from any one source's own
    measurement — the ODP data plane's backpressure state, as classified by
    comparing ``backend.control.collectors.odp_metrics`` against the source's
    objective.

    ``available=False`` means the ODP collector itself could not be reached
    (down Redis, unreachable odp-ingest, etc — see odp_metrics.collect()'s
    per-section degrade-not-raise contract); in that case
    ``odp_backpressured`` is always False (never fabricated as True) and
    ``stream_lag``/``pending`` are None.
    """

    odp_backpressured: bool
    stream_lag: int | None = None
    pending: int | None = None
    available: bool


class SuggestedActionRead(BaseModel):
    """One advisory suggestion — see backend.control.policies.suggest_actions.

    ADVISORY ONLY: nothing in this PR executes these. Surfacing them is the
    entire scope of PR-Control-3; PR-Control-4 (actuators.py) is a later PR
    that would read suggestions like these and (gated by
    Settings.control_mode == "automatic") actually perform them.
    """

    action_type: str
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class KillSwitchRead(BaseModel):
    """Snapshot of the actuator's global kill switch (issue 03).

    ``engaged`` is the effective state the Control Cycle actually checks:
    the in-memory runtime override when one has been set via
    ``POST /control/kill-switch``, else ``Settings.control_kill_switch``.
    ``runtime_override`` is null when no runtime toggle has been set this
    process lifetime (i.e. purely following config).
    """

    engaged: bool
    runtime_override: bool | None = None
    config_default: bool


class KillSwitchUpdate(BaseModel):
    """Body for ``POST /control/kill-switch``: set the in-memory runtime
    override explicitly. Resets to ``Settings.control_kill_switch`` on
    process restart — a single-operator fleet does not need this persisted."""

    engaged: bool


class OutcomeEvaluationRead(BaseModel):
    """Counts from one ``backend.control.outcomes.evaluate_pending_outcomes``
    pass — how many pending ledger rows were judged, and to which verdict.

    ``evaluated`` is the sum of the three verdict buckets from THIS pass;
    ``still_pending`` counts rows left for a later pass (too young, or no
    post-decision measurement yet without being stale).
    """

    evaluated: int
    recovered: int
    persisted: int
    insufficient_data: int
    still_pending: int


class AdvisoryReportTotalsRead(BaseModel):
    """Outcome tallies over a set of advisory-ledger rows.

    ``recovery_rate`` = recovered / (recovered + persisted); null when no row
    in the set has reached a recovered/persisted verdict yet (a 0-of-0 rate
    would be a fabricated signal, not a measurement).
    """

    total: int
    pending: int
    evaluated: int
    recovered: int
    persisted: int
    insufficient_data: int
    recovery_rate: float | None = None


class AdvisoryReportBucketRead(AdvisoryReportTotalsRead):
    """Outcome tallies for one (state, action_type) pair of the advisory
    evidence ledger — e.g. "everything we suggested pause_source for while
    auth_failed"."""

    state: str
    action_type: str


class AdvisoryReportRead(BaseModel):
    """Agreement/recovery report over the ``control_actions`` evidence ledger
    (PR-Control-3.5) — the gate data for ever flipping
    ``Settings.control_mode`` to "automatic" per state class.

    ``buckets`` groups rows by (state, action_type); ``totals`` is the same
    tally over the whole ledger; ``mode_breakdown`` counts rows per decision
    mode ("advisory" today — "automatic" rows appear only once PR-Control-4's
    actuator exists and shares this ledger). ``evaluation`` reports the lazy
    outcome pass this report ran before aggregating.
    """

    buckets: list[AdvisoryReportBucketRead] = Field(default_factory=list)
    totals: AdvisoryReportTotalsRead
    mode_breakdown: dict[str, int] = Field(default_factory=dict)
    evaluation: OutcomeEvaluationRead


class SourceControlStateRead(BaseModel):
    """Read-only, advisory control view of a source.

    ``measurement``/``control_state``/``confidence``/``sensor_coverage``/
    ``trend`` are null when the source has never run (no run evidence to
    aggregate, nothing to evaluate, nothing to trend). ``objective`` is
    always the RESOLVED setpoints the measurement was (or would be) compared
    against — the source's stored objective override (issue 02), if any,
    merged over the global default ``SourceObjective()`` via
    ``backend.control.objectives.resolve_objective``. See
    ``PATCH /sources/{source_id}/objective`` to set/update/clear the
    override.

    ``system_context`` is always present (never null) — it reflects the
    shared ODP data plane's state, which exists independent of whether this
    particular source has ever run.

    ``suggested_actions`` is always a list (possibly empty) — advisory
    ControlAction suggestions from ``backend.control.policies``. Empty means
    "the policy engine has nothing to suggest for this state", not "unknown".

    ``control_mode`` mirrors ``Settings.control_mode`` — "advisory" today.
    Even when set to "automatic", THIS ENDPOINT NEVER EXECUTES ANYTHING; that
    field only signals what a future actuator PR would be allowed to do.
    """

    source_id: str
    control_state: SourceControlState | None = None
    confidence: str | None = None
    sensor_coverage: SensorCoverage | None = None
    missing_signals: list[str] = Field(default_factory=list)
    measurement: SourceMeasurement | None = None
    objective: SourceObjective
    # FallbackTrendRead FIRST: it is the stricter member (requires the
    # provenance key), so union resolution can never silently drop a fallback
    # trend's provenance into the plain TrendRead shape.
    trend: FallbackTrendRead | TrendRead | None = None
    system_context: SystemContextRead
    suggested_actions: list[SuggestedActionRead] = Field(default_factory=list)
    control_mode: str


class SourceMeasurementRecordRead(BaseModel):
    """One persisted source_measurements row (Source Control Room trend
    endpoint — ``GET /sources/{source_id}/measurements``).

    Mirrors ``backend.models.source_measurement.SourceMeasurement`` field for
    field (the DB row shape), NOT ``backend.control.measurements.
    SourceMeasurement`` (the in-memory pydantic contract embedded as
    ``SourceControlStateRead.measurement`` — that one is derived per-decision
    and lacks ``id``/``created_at``/``updated_at``). This is the raw time
    series an operator drills into; the control-state endpoint only ever
    exposes the latest reading plus a folded trend, never the full history.
    """

    id: str
    source_id: str
    run_id: str
    measured_at: datetime

    accepted: int
    duplicates: int
    rejected: int

    error_rate: float
    duplicate_rate: float
    error_kinds: dict[str, int] = Field(default_factory=dict)

    fetch_latency_ms: int | None = None
    ingest_latency_ms: int | None = None
    store_latency_ms: int | None = None

    cursor_advanced: bool

    newest_source_ts: datetime | None = None
    newest_observed_at: datetime | None = None
    freshness_lag_seconds: int | None = None
    source_ts_quality: str

    raw: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ControlActionRecordRead(BaseModel):
    """One Evidence Ledger row (issue 07 — read-only action history listing).

    Mirrors ``backend.models.control_action.ControlActionRecord`` field for
    field; see that module's docstring for the column-group breakdown. This
    is deliberately row-level (unlike ``AdvisoryReportRead``'s folded
    buckets) — the action history view's job is to let an operator inspect
    individual suggestion/execution rows, not just aggregate rates.

    ``outcome`` is null until ``backend.control.outcomes`` judges the row
    (the "pending" verdict shown in the UI is the absence of this field, not
    a stored value — see ``control_ledger_service.list_control_actions``'s
    ``outcome=pending`` filter for the matching query-side convention).
    """

    id: str
    source_id: str
    run_id: str | None = None
    measurement_id: str | None = None
    mode: str
    state: str
    action_type: str
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    executed: bool
    evaluated_at: datetime | None = None
    outcome: str | None = None
    outcome_detail: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
