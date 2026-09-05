"""SourceObjective: per-source control setpoints.

See docs/CONTROL_THEORY_ARCHITECTURE.md §4. Pure data contract only — no
evaluator/policy logic lives here (that's future PR-Control-3).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class SourceObjective(BaseModel):
    """Setpoints a source's measurements are compared against.

    All fields have defaults so an objective can be constructed with no
    per-source overrides and still be meaningful.
    """

    max_error_rate: float = 0.05
    max_duplicate_rate: float = 0.50
    max_freshness_lag_seconds: int | None = None
    max_run_latency_ms: int = 30_000
    max_pending: int = 1000
    min_accepted_per_run: int | None = None


class SourceObjectiveOverride(BaseModel):
    """A partial ``SourceObjective`` stored on ``DataSource.objective_override``.

    Every field is optional (unset = "use the default for this field"), and
    unknown field names are rejected — the same validation surface a PATCH
    request hits (issue 02: per-source objective override). ``model_dump``
    with ``exclude_unset=True`` is what actually gets persisted to the
    nullable JSON column, so a field explicitly set to its own default value
    is still recorded as an override (distinct from "not set").
    """

    model_config = ConfigDict(extra="forbid")

    max_error_rate: float | None = None
    max_duplicate_rate: float | None = None
    max_freshness_lag_seconds: int | None = None
    max_run_latency_ms: int | None = None
    max_pending: int | None = None
    min_accepted_per_run: int | None = None


def resolve_objective(override: dict[str, Any] | None) -> SourceObjective:
    """Merge a source's stored objective-override dict over the default
    ``SourceObjective()`` and return the resolved objective.

    The single shared helper for BOTH consumption sites named in issue 02:
    the control-state decision path (``backend.control.service`` via
    ``backend.api.v1.sources.get_source_control_state``) and outcome
    judgment (``backend.control.outcomes.evaluate_pending_outcomes``) — so
    neither site can disagree with the other about which setpoints apply to
    a source, the same "one path" principle already applied to measurement
    aggregation and state evaluation.

    ``override`` is ``None`` (no row stored, or explicitly cleared) ->
    plain defaults. A non-empty dict is validated against
    ``SourceObjectiveOverride`` (unknown keys / wrong types raise
    ``ValidationError``, mirroring the PATCH endpoint's 422 contract) and
    merged over the defaults field-by-field, keeping only the keys the
    override actually sets.
    """
    if not override:
        return SourceObjective()

    validated = SourceObjectiveOverride.model_validate(override)
    overrides = validated.model_dump(exclude_none=True)
    return SourceObjective(**overrides)


__all__ = [
    "SourceObjective",
    "SourceObjectiveOverride",
    "resolve_objective",
]
