from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel


class PlanCreate(BaseModel):
    """Body for ``POST /plans``. ``graph`` is the raw Plan IR JSON document
    (validated through ``backend.plan_ir.validation.validate_plan_graph``
    before it is ever written) — accepted as a free-form dict rather than
    the ``PlanGraph`` model itself so the byte-faithful round-trip guarantee
    (issue 02 acceptance criterion) doesn't depend on Pydantic's own
    re-serialization of the graph."""

    name: str = Field(..., min_length=1, max_length=255)
    graph: dict[str, Any]


class PlanUpdate(BaseModel):
    """Body for ``PATCH /plans/{plan_id}``. Both fields optional so a caller
    can rename a Plan without resubmitting its graph, or vice versa; when
    ``graph`` is supplied it goes through the same validator as create and
    bumps ``version``."""

    name: str | None = Field(None, min_length=1, max_length=255)
    graph: dict[str, Any] | None = None


class PlanRead(UTCModel):
    id: str
    name: str
    graph: dict[str, Any]
    version: int
    #: True if the graph contains any unmaterialized Draft Source Node
    #: (no source_id, draft=True) — draft Plans save fine but never enter
    #: any control loop (issue 02 acceptance criterion; scheduling hooks
    #: are issue 05's scope, not this one's).
    draft: bool
    #: True only when every source node in the graph is materialized
    #: (source_id set, draft=False) — the PRD's "runnable end-to-end by the
    #: backend" bar (story 10). A Plan with zero source nodes at all is not
    #: runnable either (nothing to execute).
    runnable: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceSegmentRead(BaseModel):
    """One source node's dispatch outcome within a multi-source Plan run
    (issue 04). Mirrors ``backend.plan_ir.executor.SourceSegmentResult``
    field-for-field."""

    node_id: str
    source_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None

    success: bool
    collected: int
    stored: int
    skipped: int
    error: str | None = None


    model_config = {"from_attributes": True}


class SharedSegmentRead(BaseModel):
    """The shared segment's run-scoped outcome (issue 04). Mirrors
    ``backend.plan_ir.executor.SharedSegmentResult`` field-for-field;
    node-level detail is Plan Health (``GET /plans/{plan_id}/health``), not
    this summary."""

    run_key: str
    success: bool
    failed_node_id: str | None = None
    error: str | None = None

    items_in: int
    stored: int
    skipped: int

    model_config = {"from_attributes": True}


class PlanRunRead(BaseModel):
    """Response body for ``POST /plans/{plan_id}/run`` (issue 03 degenerate,
    issue 04 multi-source). Mirrors ``backend.plan_ir.executor.PlanRunResult``
    field-for-field — the executor body's return value IS the HTTP response
    shape, no separate projection. ``source_results``/``shared_segment`` are
    empty/``None`` for a degenerate (single-source) Plan run, unchanged from
    issue 03's response shape in that case."""

    plan_id: str
    source_id: str
    task_id: str
    run_id: str | None = None
    success: bool
    collected: int
    stored: int
    skipped: int
    error: str | None = None
    source_results: list[SourceSegmentRead] = Field(default_factory=list)
    shared_segment: SharedSegmentRead | None = None

    model_config = {"from_attributes": True}


class PlanHealthRead(BaseModel):
    """One recorded Plan Health row (issue 04, ADR-0009 Two-Tier
    Attribution) — read-only projection of
    ``backend.models.plan_health.PlanHealthRecord``."""

    id: str
    plan_id: str
    run_key: str
    node_id: str
    node_type: str
    success: bool
    duration_ms: int
    items_in: int
    items_out: int
    error_message: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime

    model_config = {"from_attributes": True}
