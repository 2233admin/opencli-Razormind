"""Unit tests for backend.control.evaluator.

The bottom half of this file (PR-Control-2 section) still exercises the
original minimal-placeholder behavior, which PR-Control-3's full evaluator
deliberately preserves unchanged (odp_pending -> BACKPRESSURED, the C0
honesty gate). The top half (PR-Control-3 section) exercises the new
multi-signal rules: AUTH_FAILED / RATE_LIMITED / SCHEMA_DRIFT / BLOCKED_BY_ODP
/ DEAD, and confirms every state-detection branch is a pure function
(deterministic: same input -> same output) that never mutates anything.

C0 (Control Room v0, docs/CONTROL_THEORY_ARCHITECTURE.md §0): a source cannot
render as a confident HEALTHY when sensor coverage is incomplete. Since
`_measurement()` below leaves odp/freshness/cursor unpopulated by default (the
same as real aggregation.py output today), the "would-be-healthy" tests below
assert UNKNOWN unless the test explicitly fills in full coverage.
"""

from datetime import UTC, datetime

from backend.control.evaluator import evaluate
from backend.control.measurements import SourceMeasurement
from backend.control.models import SourceControlState
from backend.control.objectives import SourceObjective


def _measurement(**overrides) -> SourceMeasurement:
    kwargs = dict(
        source_id="src-1",
        run_id="run-1",
        accepted=10,
        duplicates=0,
        rejected=0,
        fetch_latency_ms=100,
        error_rate=0.0,
        duplicate_rate=0.0,
        cursor_advanced=False,
        observed_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    kwargs.update(overrides)
    return SourceMeasurement(**kwargs)


class TestEvaluate:
    def test_low_coverage_default_is_unknown_not_healthy(self):
        # Nothing looks wrong, but odp/error_kinds coverage is missing (the
        # real aggregation.py default) -> confidence "low" -> UNKNOWN, never
        # a fake HEALTHY.
        m = _measurement()
        assert evaluate(m, SourceObjective()) is SourceControlState.UNKNOWN

    def test_healthy_is_unreachable_until_error_kinds_and_cursor_are_wired(self):
        # error_kinds has no SourceMeasurement field at all yet (always missing)
        # and cursor is a structural constant (coverage.py never reports it as
        # present) — so even with freshness+odp fully populated, two signals
        # (one critical: error_kinds) are always missing -> "low" -> UNKNOWN.
        # This is intentional: C0 must not invent a HEALTHY path the real
        # sensors can't back up yet. PR-Control-3+ makes HEALTHY reachable once
        # error_kinds/cursor land as real signals.
        m = _measurement(freshness_lag_seconds=5, odp_pending=0, odp_stream_lag=0)
        assert evaluate(m, SourceObjective()) is SourceControlState.UNKNOWN

    def test_degraded_when_error_rate_exceeds_objective(self):
        # default max_error_rate = 0.05 — DEGRADED is positive evidence, never
        # gated by coverage (even with low coverage it must surface, not hide).
        m = _measurement(rejected=1, accepted=1, error_rate=0.5)
        assert evaluate(m, SourceObjective()) is SourceControlState.DEGRADED

    def test_error_rate_at_setpoint_is_not_degraded(self):
        # strictly-greater comparison: equal to setpoint does not trip DEGRADED,
        # but low coverage still gates the fallthrough to UNKNOWN.
        m = _measurement(error_rate=0.05)
        assert evaluate(m, SourceObjective(max_error_rate=0.05)) is SourceControlState.UNKNOWN

    def test_backpressured_when_odp_pending_exceeds_max_pending(self):
        m = _measurement(odp_pending=5000)
        obj = SourceObjective(max_pending=1000)
        assert evaluate(m, obj) is SourceControlState.BACKPRESSURED

    def test_none_odp_pending_never_backpressured(self):
        # PR-Control-2 leaves odp_pending unpopulated (None) — that branch can't
        # fire, and the fallthrough is gated to UNKNOWN by low coverage.
        m = _measurement(odp_pending=None)
        assert evaluate(m, SourceObjective()) is SourceControlState.UNKNOWN

    def test_degraded_takes_precedence_over_backpressure(self):
        m = _measurement(error_rate=0.9, odp_pending=99999)
        assert evaluate(m, SourceObjective()) is SourceControlState.DEGRADED

    def test_healthy_passes_through_once_coverage_is_not_low(self, monkeypatch):
        # Isolates the evaluator's gate from coverage.py's specific signal
        # semantics: once PR-Control-3+ wires up error_kinds/cursor such that
        # derive_confidence can return "medium"/"high", HEALTHY must pass
        # through unmodified. Patch derive_confidence at its evaluator
        # import site so this test doesn't depend on which signals compute.py
        # currently considers present.
        import backend.control.evaluator as evaluator_module

        monkeypatch.setattr(evaluator_module, "derive_confidence", lambda _cov: "medium")
        m = _measurement()
        assert evaluate(m, SourceObjective()) is SourceControlState.HEALTHY

    def test_unknown_only_when_confidence_is_low(self, monkeypatch):
        import backend.control.evaluator as evaluator_module

        monkeypatch.setattr(evaluator_module, "derive_confidence", lambda _cov: "high")
        m = _measurement()
        assert evaluate(m, SourceObjective()) is SourceControlState.HEALTHY


# ---------------------------------------------------------------------------
# PR-Control-3: full multi-signal evaluator
# ---------------------------------------------------------------------------


class TestPRControl3StateBranches:
    """One test per new state-detection branch. Every case is deterministic:
    calling evaluate() twice with the same input yields the same result, and
    none of these tests touch a DataSource — evaluate() is a pure function
    over its arguments only."""

    def test_auth_failed_takes_top_precedence(self):
        m = _measurement(error_kinds={"auth_failed": 1, "rate_limited": 1, "schema_drift": 1})
        assert evaluate(m, SourceObjective()) is SourceControlState.AUTH_FAILED

    def test_rate_limited_from_this_runs_error_kinds(self):
        m = _measurement(error_kinds={"rate_limited": 1})
        assert evaluate(m, SourceObjective()) is SourceControlState.RATE_LIMITED

    def test_rate_limited_from_dominant_trend_even_without_this_runs_error_kind(self):
        m = _measurement(error_kinds={})
        trend = {
            "window": 5,
            "zero_accepted_streak": 0,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 3,
        }
        assert evaluate(m, SourceObjective(), trend=trend) is SourceControlState.RATE_LIMITED

    def test_rate_limited_trend_not_dominant_does_not_trigger(self):
        # 2 of 5 is not a majority (2*2=4 is not > 5) -> falls through instead.
        m = _measurement(error_kinds={}, freshness_lag_seconds=1, source_ts_quality="source")
        trend = {
            "window": 5,
            "zero_accepted_streak": 0,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 2,
        }
        result = evaluate(m, SourceObjective(), trend=trend)
        assert result is not SourceControlState.RATE_LIMITED

    def test_schema_drift(self):
        m = _measurement(error_kinds={"schema_drift": 1})
        assert evaluate(m, SourceObjective()) is SourceControlState.SCHEMA_DRIFT

    def test_schema_drift_precedes_degraded(self):
        m = _measurement(error_kinds={"schema_drift": 1}, error_rate=0.9)
        assert evaluate(m, SourceObjective()) is SourceControlState.SCHEMA_DRIFT

    def test_blocked_by_odp_when_system_context_backpressured_and_available(self):
        m = _measurement()
        ctx = {"odp_backpressured": True, "available": True}
        assert (
            evaluate(m, SourceObjective(), system_context=ctx)
            is SourceControlState.BLOCKED_BY_ODP
        )

    def test_not_blocked_by_odp_when_system_context_unavailable(self):
        # An unavailable ODP collector must never be treated as backpressured,
        # even if odp_backpressured was somehow set True by a caller — the
        # "available" gate wins, so this falls through to the honesty-gated
        # UNKNOWN/HEALTHY branch instead of a fabricated BLOCKED_BY_ODP.
        m = _measurement()
        ctx = {"odp_backpressured": True, "available": False}
        result = evaluate(m, SourceObjective(), system_context=ctx)
        assert result is not SourceControlState.BLOCKED_BY_ODP

    def test_blocked_by_odp_precedes_degraded(self):
        m = _measurement(error_rate=0.9)
        ctx = {"odp_backpressured": True, "available": True}
        assert (
            evaluate(m, SourceObjective(), system_context=ctx)
            is SourceControlState.BLOCKED_BY_ODP
        )

    def test_legacy_backpressured_still_reachable_via_odp_pending(self):
        # PR-Control-2's per-measurement odp_pending signal is preserved
        # (rule 4b) for callers that don't populate system_context.
        m = _measurement(odp_pending=5000)
        assert evaluate(m, SourceObjective(max_pending=1000)) is SourceControlState.BACKPRESSURED

    def test_dead_when_zero_streak_and_terminal_error(self):
        m = _measurement(accepted=0, error_kinds={"network": 1})
        trend = {
            "window": 5,
            "zero_accepted_streak": 3,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 0,
        }
        assert evaluate(m, SourceObjective(), trend=trend) is SourceControlState.DEAD

    def test_not_dead_below_streak_threshold(self):
        m = _measurement(
            accepted=0,
            error_kinds={"network": 1},
            freshness_lag_seconds=1,
            source_ts_quality="source",
        )
        trend = {
            "window": 5,
            "zero_accepted_streak": 2,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 0,
        }
        result = evaluate(m, SourceObjective(), trend=trend)
        assert result is not SourceControlState.DEAD

    def test_not_dead_when_zero_streak_but_no_terminal_error(self):
        # A clean, error-free zero-accepted streak (e.g. a polling source with
        # nothing new every cycle) must not be flagged DEAD — that would
        # falsely alarm on a perfectly healthy polling source.
        m = _measurement(
            accepted=0,
            error_kinds={},
            freshness_lag_seconds=1,
            source_ts_quality="source",
        )
        trend = {
            "window": 5,
            "zero_accepted_streak": 5,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 0,
        }
        result = evaluate(m, SourceObjective(), trend=trend)
        assert result is not SourceControlState.DEAD

    def test_dead_precedes_the_honesty_gate_fallthrough(self):
        # DEAD is positive evidence — it must surface even though this
        # measurement's coverage would otherwise gate a would-be HEALTHY down
        # to UNKNOWN.
        m = _measurement(accepted=0, error_kinds={"network": 1})
        trend = {
            "window": 3,
            "zero_accepted_streak": 3,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 0,
        }
        assert evaluate(m, SourceObjective(), trend=trend) is SourceControlState.DEAD

    def test_rule_precedence_auth_beats_everything(self):
        m = _measurement(
            error_kinds={"auth_failed": 1, "schema_drift": 1, "rate_limited": 1},
            error_rate=0.9,
            accepted=0,
        )
        trend = {
            "window": 5,
            "zero_accepted_streak": 5,
            "avg_error_rate": 0.9,
            "rate_limited_runs": 5,
        }
        ctx = {"odp_backpressured": True, "available": True}
        result = evaluate(m, SourceObjective(), trend=trend, system_context=ctx)
        assert result is SourceControlState.AUTH_FAILED

    def test_deterministic_same_input_same_output(self):
        m = _measurement(error_kinds={"schema_drift": 1})
        obj = SourceObjective()
        first = evaluate(m, obj)
        second = evaluate(m, obj)
        assert first is second is SourceControlState.SCHEMA_DRIFT

    def test_evaluate_never_mutates_its_inputs(self):
        m = _measurement(error_kinds={"rate_limited": 1})
        obj = SourceObjective()
        trend = {
            "window": 5,
            "zero_accepted_streak": 0,
            "avg_error_rate": 0.0,
            "rate_limited_runs": 1,
        }
        ctx = {"odp_backpressured": False, "available": True}
        m_copy = m.model_copy(deep=True)
        obj_copy = obj.model_copy(deep=True)
        trend_copy = dict(trend)
        ctx_copy = dict(ctx)
        evaluate(m, obj, trend=trend, system_context=ctx)
        assert m == m_copy
        assert obj == obj_copy
        assert trend == trend_copy
        assert ctx == ctx_copy
