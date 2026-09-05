"""Control-model primitives: control state enum + control action.

See docs/CONTROL_THEORY_ARCHITECTURE.md §4 for the vocabulary these types
encode. This module only defines pure data contracts; it is not wired into
any runner, pipeline, or API in this PR.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceControlState(StrEnum):
    """Control-theoretic state of a data source (the "plant").

    Derived by an evaluator (future PR-Control-3) from a SourceMeasurement
    compared against a SourceObjective. Defined here only as a vocabulary.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BACKPRESSURED = "backpressured"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    SCHEMA_DRIFT = "schema_drift"
    PAUSED = "paused"
    DEAD = "dead"
    # PR-Control-3: the source itself may be fine, but the shared ODP data
    # plane (Redis stream lag / pending, per backend.control.collectors.
    # odp_metrics) is backpressured beyond the source's objective — the
    # bottleneck is downstream/system-wide, not the source's own error rate.
    # Distinct from BACKPRESSURED (per-measurement odp_pending signal, kept
    # for backward compat) so a UI can tell "this source is struggling" apart
    # from "the whole ODP pipe is struggling and this source is just waiting".
    BLOCKED_BY_ODP = "blocked_by_odp"
    # C0 (Control Room v0, docs/CONTROL_THEORY_ARCHITECTURE.md §0): reported
    # instead of HEALTHY when sensor_coverage is incomplete enough that a
    # confident "healthy" would be a lie. See backend.control.evaluator for the
    # exact gate. Not a plant failure mode — an admission that we cannot see.
    UNKNOWN = "unknown"


class ControlAction(BaseModel):
    """A candidate control action against a source (the "actuator" input).

    Advisory-only in this PR: nothing constructs or executes a ControlAction
    yet. See docs/CONTROL_THEORY_ARCHITECTURE.md §4 for the action vocabulary
    (increase_interval / apply_backoff / pause / resume / reduce_page_size /
    switch_write_strategy / force_cursor_rescan / claim_pending /
    require_human_review).
    """

    action_type: str
    source_id: str
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
