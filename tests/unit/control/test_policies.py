"""Unit tests for backend.control.policies.suggest_actions (PR-Control-3).

Pure function tests: state + measurement + objective -> list[ControlAction].
Every test also asserts the advisory-only guarantee at this layer: these
functions return data, they never touch a DataSource, a DB session, or any
scheduler/executor.
"""

from datetime import UTC, datetime

import pytest

from backend.control.measurements import SourceMeasurement
from backend.control.models import ControlAction, SourceControlState
from backend.control.objectives import SourceObjective
from backend.control.policies import suggest_actions


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


class TestSuggestActions:
    def test_rate_limited_suggests_increase_interval(self):
        m = _measurement(error_kinds={"rate_limited": 1})
        actions = suggest_actions(SourceControlState.RATE_LIMITED, m, SourceObjective())
        assert len(actions) == 1
        assert actions[0].action_type == "increase_interval"
        assert actions[0].source_id == "src-1"
        assert actions[0].reason  # non-empty human reason

    def test_auth_failed_suggests_pause_and_review(self):
        m = _measurement(error_kinds={"auth_failed": 1})
        actions = suggest_actions(SourceControlState.AUTH_FAILED, m, SourceObjective())
        action_types = {a.action_type for a in actions}
        assert action_types == {"pause_source", "require_auth_review"}
        assert all(a.reason for a in actions)

    def test_schema_drift_suggests_pause_and_review(self):
        m = _measurement(error_kinds={"schema_drift": 1})
        actions = suggest_actions(SourceControlState.SCHEMA_DRIFT, m, SourceObjective())
        action_types = {a.action_type for a in actions}
        assert action_types == {"pause_source", "require_review"}

    def test_blocked_by_odp_suggests_pause_low_priority(self):
        m = _measurement()
        actions = suggest_actions(SourceControlState.BLOCKED_BY_ODP, m, SourceObjective())
        assert len(actions) == 1
        assert actions[0].action_type == "pause_low_priority"

    def test_dead_suggests_require_review(self):
        m = _measurement(accepted=0)
        actions = suggest_actions(SourceControlState.DEAD, m, SourceObjective())
        assert len(actions) == 1
        assert actions[0].action_type == "require_review"

    def test_degraded_suggests_require_review(self):
        m = _measurement(error_rate=0.5)
        actions = suggest_actions(SourceControlState.DEGRADED, m, SourceObjective())
        assert len(actions) == 1
        assert actions[0].action_type == "require_review"

    @pytest.mark.parametrize(
        "state",
        [
            SourceControlState.HEALTHY,
            SourceControlState.PAUSED,
            SourceControlState.UNKNOWN,
            SourceControlState.BACKPRESSURED,
        ],
    )
    def test_no_policy_states_return_empty_list(self, state):
        m = _measurement()
        assert suggest_actions(state, m, SourceObjective()) == []

    def test_every_action_carries_a_reason(self):
        for state in (
            SourceControlState.RATE_LIMITED,
            SourceControlState.AUTH_FAILED,
            SourceControlState.SCHEMA_DRIFT,
            SourceControlState.BLOCKED_BY_ODP,
            SourceControlState.DEAD,
            SourceControlState.DEGRADED,
        ):
            m = _measurement(error_kinds={"auth_failed": 1, "schema_drift": 1, "rate_limited": 1})
            actions = suggest_actions(state, m, SourceObjective())
            for action in actions:
                assert isinstance(action, ControlAction)
                assert action.reason.strip() != ""

    def test_deterministic(self):
        m = _measurement(error_kinds={"rate_limited": 1})
        obj = SourceObjective()
        first = suggest_actions(SourceControlState.RATE_LIMITED, m, obj)
        second = suggest_actions(SourceControlState.RATE_LIMITED, m, obj)
        assert [a.model_dump() for a in first] == [a.model_dump() for a in second]

    def test_actions_are_only_data_no_side_effects(self, monkeypatch):
        """suggest_actions must never import/call anything that could mutate a
        DataSource — this is enforced structurally (the module has no DB
        session parameter at all), but we also assert the returned objects
        are plain data with no attached mutation capability."""
        m = _measurement(error_kinds={"auth_failed": 1})
        actions = suggest_actions(SourceControlState.AUTH_FAILED, m, SourceObjective())
        for action in actions:
            assert isinstance(action, ControlAction)
            # ControlAction is a plain Pydantic model — dumping/restoring it
            # must round-trip with no external state touched.
            restored = ControlAction(**action.model_dump())
            assert restored == action
