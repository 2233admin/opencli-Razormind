"""Unit tests for backend.control (PR-Control-1): pure data contracts only.

No DB, no ODP, no runner/pipeline wiring — these tests only exercise the
Pydantic models and the pure rate-derivation classmethod.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.control.measurements import SourceMeasurement
from backend.control.models import ControlAction, SourceControlState
from backend.control.objectives import SourceObjective

# ---------------------------------------------------------------------------
# SourceControlState
# ---------------------------------------------------------------------------


class TestSourceControlState:
    def test_expected_members_present(self):
        expected = {
            "HEALTHY",
            "DEGRADED",
            "BACKPRESSURED",
            "RATE_LIMITED",
            "AUTH_FAILED",
            "SCHEMA_DRIFT",
            "PAUSED",
            "DEAD",
            "UNKNOWN",
            "BLOCKED_BY_ODP",
        }
        assert {m.name for m in SourceControlState} == expected

    def test_is_str_enum(self):
        assert SourceControlState.HEALTHY == "healthy"
        assert isinstance(SourceControlState.HEALTHY, str)

    def test_values_are_lowercase_snake(self):
        for member in SourceControlState:
            assert member.value == member.value.lower()

    def test_construct_from_value(self):
        assert SourceControlState("degraded") is SourceControlState.DEGRADED

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            SourceControlState("not_a_real_state")


# ---------------------------------------------------------------------------
# ControlAction
# ---------------------------------------------------------------------------


class TestControlAction:
    def test_minimal_construction_defaults_payload(self):
        action = ControlAction(
            action_type="pause",
            source_id="src-1",
            reason="error_rate exceeded objective",
        )
        assert action.action_type == "pause"
        assert action.source_id == "src-1"
        assert action.reason == "error_rate exceeded objective"
        assert action.payload == {}

    def test_payload_roundtrip(self):
        action = ControlAction(
            action_type="reduce_page_size",
            source_id="src-2",
            reason="backpressure",
            payload={"new_page_size": 10},
        )
        dumped = action.model_dump()
        restored = ControlAction(**dumped)
        assert restored == action
        assert restored.payload["new_page_size"] == 10

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            ControlAction(action_type="pause", reason="no source id")

    def test_default_payload_not_shared_mutable(self):
        a1 = ControlAction(action_type="pause", source_id="s1", reason="r")
        a2 = ControlAction(action_type="pause", source_id="s2", reason="r")
        a1.payload["x"] = 1
        assert a2.payload == {}


# ---------------------------------------------------------------------------
# SourceMeasurement — direct construction
# ---------------------------------------------------------------------------


class TestSourceMeasurementConstruction:
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            source_id="src-1",
            run_id="run-1",
            accepted=10,
            duplicates=2,
            rejected=1,
            fetch_latency_ms=500,
            ingest_latency_ms=None,
            store_latency_ms=None,
            error_rate=0.1,
            duplicate_rate=0.2,
            freshness_lag_seconds=None,
            cursor_advanced=True,
            odp_stream_lag=None,
            odp_pending=None,
            dlq_count=0,
            observed_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        kwargs.update(overrides)
        return kwargs

    def test_valid_construction(self):
        m = SourceMeasurement(**self._base_kwargs())
        assert m.source_id == "src-1"
        assert m.run_id == "run-1"
        assert m.accepted == 10
        assert m.dlq_count == 0

    def test_dlq_count_defaults_to_zero(self):
        kwargs = self._base_kwargs()
        del kwargs["dlq_count"]
        m = SourceMeasurement(**kwargs)
        assert m.dlq_count == 0

    def test_optional_fields_accept_none(self):
        m = SourceMeasurement(**self._base_kwargs())
        assert m.ingest_latency_ms is None
        assert m.store_latency_ms is None
        assert m.freshness_lag_seconds is None
        assert m.odp_stream_lag is None
        assert m.odp_pending is None

    def test_missing_required_field_raises(self):
        kwargs = self._base_kwargs()
        del kwargs["accepted"]
        with pytest.raises(ValidationError):
            SourceMeasurement(**kwargs)

    def test_roundtrip_via_model_dump(self):
        m = SourceMeasurement(**self._base_kwargs())
        restored = SourceMeasurement(**m.model_dump())
        assert restored == m


# ---------------------------------------------------------------------------
# SourceMeasurement.derive — the only logic in this PR
# ---------------------------------------------------------------------------


class TestSourceMeasurementDerive:
    def test_zero_items_gives_zero_rates_not_zero_division(self):
        m = SourceMeasurement.derive(
            source_id="src-1",
            run_id="run-1",
            accepted=0,
            duplicates=0,
            rejected=0,
            fetch_latency_ms=100,
            observed_at=datetime.now(UTC),
            cursor_advanced=False,
        )
        assert m.error_rate == 0.0
        assert m.duplicate_rate == 0.0

    def test_normal_ratios(self):
        # total_seen = 10 + 2 + 1 = 13
        m = SourceMeasurement.derive(
            source_id="src-1",
            run_id="run-1",
            accepted=10,
            duplicates=2,
            rejected=1,
            fetch_latency_ms=250,
            observed_at=datetime.now(UTC),
            cursor_advanced=True,
        )
        assert m.error_rate == pytest.approx(1 / 13)
        assert m.duplicate_rate == pytest.approx(2 / 13)

    def test_all_rejected_gives_error_rate_one(self):
        m = SourceMeasurement.derive(
            source_id="src-1",
            run_id="run-1",
            accepted=0,
            duplicates=0,
            rejected=5,
            fetch_latency_ms=100,
            observed_at=datetime.now(UTC),
            cursor_advanced=False,
        )
        assert m.error_rate == 1.0
        assert m.duplicate_rate == 0.0

    def test_all_duplicates_gives_duplicate_rate_one(self):
        m = SourceMeasurement.derive(
            source_id="src-1",
            run_id="run-1",
            accepted=0,
            duplicates=5,
            rejected=0,
            fetch_latency_ms=100,
            observed_at=datetime.now(UTC),
            cursor_advanced=False,
        )
        assert m.duplicate_rate == 1.0
        assert m.error_rate == 0.0

    def test_optional_passthrough_fields(self):
        now = datetime.now(UTC)
        m = SourceMeasurement.derive(
            source_id="src-1",
            run_id="run-1",
            accepted=3,
            duplicates=0,
            rejected=0,
            fetch_latency_ms=42,
            observed_at=now,
            cursor_advanced=True,
            ingest_latency_ms=10,
            store_latency_ms=20,
            freshness_lag_seconds=5,
            odp_stream_lag=1,
            odp_pending=2,
            dlq_count=3,
        )
        assert m.ingest_latency_ms == 10
        assert m.store_latency_ms == 20
        assert m.freshness_lag_seconds == 5
        assert m.odp_stream_lag == 1
        assert m.odp_pending == 2
        assert m.dlq_count == 3
        assert m.observed_at == now

    def test_dlq_count_defaults_to_zero_via_derive(self):
        m = SourceMeasurement.derive(
            source_id="src-1",
            run_id="run-1",
            accepted=1,
            duplicates=0,
            rejected=0,
            fetch_latency_ms=1,
            observed_at=datetime.now(UTC),
            cursor_advanced=True,
        )
        assert m.dlq_count == 0


# ---------------------------------------------------------------------------
# SourceObjective
# ---------------------------------------------------------------------------


class TestSourceObjective:
    def test_defaults(self):
        obj = SourceObjective()
        assert obj.max_error_rate == 0.05
        assert obj.max_duplicate_rate == 0.50
        assert obj.max_freshness_lag_seconds is None
        assert obj.max_run_latency_ms == 30_000
        assert obj.max_pending == 1000
        assert obj.min_accepted_per_run is None

    def test_override_all_fields(self):
        obj = SourceObjective(
            max_error_rate=0.1,
            max_duplicate_rate=0.9,
            max_freshness_lag_seconds=60,
            max_run_latency_ms=5_000,
            max_pending=50,
            min_accepted_per_run=1,
        )
        assert obj.max_error_rate == 0.1
        assert obj.max_duplicate_rate == 0.9
        assert obj.max_freshness_lag_seconds == 60
        assert obj.max_run_latency_ms == 5_000
        assert obj.max_pending == 50
        assert obj.min_accepted_per_run == 1

    def test_roundtrip(self):
        obj = SourceObjective(max_error_rate=0.2)
        restored = SourceObjective(**obj.model_dump())
        assert restored == obj
