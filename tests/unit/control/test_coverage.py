"""Unit tests for backend.control.coverage (C0 — Control Room v0, extended by
PR-Control-3).

Pure function tests, no DB. See docs/CONTROL_THEORY_ARCHITECTURE.md §0: the
whole point of this module is that an incomplete-sensor system must never be
reported as a confident HEALTHY — these tests pin exactly which signals are
"present" today and how missing signals roll up into a confidence level.

PR-Control-3: cursor/error_kinds coverage is now driven by whether the
measurement came from a real ``source_measurements`` row (signaled by
``source_ts_quality`` being set) rather than being permanently False/False —
see coverage.py's module docstring for why ``source_ts_quality`` is the
discriminator.
"""

from datetime import UTC, datetime

from backend.control.coverage import (
    compute_sensor_coverage,
    derive_confidence,
    missing_signals,
)
from backend.control.measurements import SourceMeasurement


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


class TestComputeSensorCoverage:
    def test_run_is_always_true(self):
        # compute_sensor_coverage is only ever called once a measurement exists.
        cov = compute_sensor_coverage(_measurement())
        assert cov["run"] is True

    def test_cursor_false_for_taskevent_fallback_measurement(self):
        # A measurement with no source_ts_quality came from the pre-C1
        # TaskRunEvent fallback path (aggregation.py hardcodes
        # cursor_advanced=False there) — even if some caller sets
        # cursor_advanced=True by hand without a real quality signal, coverage
        # treats it as not-yet-real.
        cov = compute_sensor_coverage(_measurement(cursor_advanced=True))
        assert cov["cursor"] is False

    def test_cursor_true_when_source_ts_quality_present(self):
        # A measurement built from a real source_measurements row (C1) always
        # carries a source_ts_quality string — that's the signal cursor
        # coverage is genuinely observed, regardless of the boolean's value.
        cov = compute_sensor_coverage(
            _measurement(cursor_advanced=False, source_ts_quality="missing")
        )
        assert cov["cursor"] is True

    def test_freshness_true_when_populated(self):
        cov = compute_sensor_coverage(_measurement(freshness_lag_seconds=42))
        assert cov["freshness"] is True

    def test_freshness_false_when_none(self):
        cov = compute_sensor_coverage(_measurement(freshness_lag_seconds=None))
        assert cov["freshness"] is False

    def test_error_kinds_false_for_taskevent_fallback_measurement(self):
        # No source_ts_quality -> pre-C1 fallback path -> error_kinds coverage
        # absent even if the dict happens to be empty (ambiguous: could mean
        # "no error" or "never recorded").
        cov = compute_sensor_coverage(_measurement())
        assert cov["error_kinds"] is False

    def test_error_kinds_true_when_source_ts_quality_present_even_if_empty(self):
        # A real source_measurements row with an EMPTY error_kinds dict means
        # "no terminal error this run" — a genuine signal, not a gap.
        cov = compute_sensor_coverage(_measurement(source_ts_quality="source", error_kinds={}))
        assert cov["error_kinds"] is True

    def test_odp_true_when_either_odp_field_populated(self):
        assert compute_sensor_coverage(_measurement(odp_pending=0))["odp"] is True
        assert compute_sensor_coverage(_measurement(odp_stream_lag=0))["odp"] is True

    def test_odp_false_when_both_none(self):
        cov = compute_sensor_coverage(_measurement(odp_pending=None, odp_stream_lag=None))
        assert cov["odp"] is False

    def test_default_measurement_matches_real_aggregation_output(self):
        # Mirrors what backend.control.aggregation.build_measurement produces
        # today (cursor_advanced=False, freshness/odp all None).
        cov = compute_sensor_coverage(_measurement())
        assert cov == {
            "run": True,
            "cursor": False,
            "freshness": False,
            "error_kinds": False,
            "odp": False,
        }


class TestMissingSignals:
    def test_empty_when_all_present(self):
        cov = {"run": True, "cursor": True, "freshness": True, "error_kinds": True, "odp": True}
        assert missing_signals(cov) == []

    def test_lists_only_false_entries(self):
        cov = {"run": True, "cursor": False, "freshness": True, "error_kinds": False, "odp": False}
        assert missing_signals(cov) == ["cursor", "error_kinds", "odp"]


class TestDeriveConfidence:
    def test_high_when_nothing_missing(self):
        cov = {"run": True, "cursor": True, "freshness": True, "error_kinds": True, "odp": True}
        assert derive_confidence(cov) == "high"

    def test_low_when_odp_missing_alone(self):
        cov = {"run": True, "cursor": True, "freshness": True, "error_kinds": True, "odp": False}
        assert derive_confidence(cov) == "low"

    def test_low_when_error_kinds_missing_alone(self):
        cov = {"run": True, "cursor": True, "freshness": True, "error_kinds": False, "odp": True}
        assert derive_confidence(cov) == "low"

    def test_medium_when_one_noncritical_signal_missing(self):
        # only 'cursor' missing — not a critical signal, count == 1
        cov = {"run": True, "cursor": False, "freshness": True, "error_kinds": True, "odp": True}
        assert derive_confidence(cov) == "medium"

    def test_low_when_two_or_more_missing_even_if_noncritical(self):
        # cursor + freshness missing (neither is "critical" alone), but count >= 2
        cov = {"run": True, "cursor": False, "freshness": False, "error_kinds": True, "odp": True}
        assert derive_confidence(cov) == "low"

    def test_low_for_real_aggregation_default(self):
        # today's real build_measurement() output: cursor/freshness/error_kinds/odp
        # all missing -> unambiguously "low".
        cov = compute_sensor_coverage(_measurement())
        assert derive_confidence(cov) == "low"
