from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class PlanHealthRecord(TimestampMixin):
    """One shared-segment node's health for one Plan run (issue 04, ADR-0009
    Two-Tier Attribution).

    Plan Health is the shared-segment's OWN observability dimension — it is
    to a shared node (merge/transform/sink) what ``SourceMeasurement`` is to a
    source segment, and the two are deliberately never the same table: a
    dedupe node raising must never touch ``source_measurements`` or any
    ``DataSource`` control-state column (that is the hard attribution
    contract this table exists to make checkable — a shared-segment failure
    is visible ONLY here and in this table's own rows).

    One row per (plan, node, run): ``run_key`` groups every node's row for a
    single ``run_plan_once`` pass (not a DB foreign key — a Plan run touches
    no other Plan-run table row) so a caller can fetch "this run's health"
    without needing a separate PlanRun table (issue 04 scope: node-level
    health rows are all that's required; whole-run bookkeeping already lives
    on ``PlanRunResult``, returned directly by the executor and the HTTP
    response body, never persisted).
    """

    __tablename__ = "plan_health"

    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Groups every node's row for one ``run_plan_once`` pass (not a DB FK —
    #: see class docstring).
    run_key: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: The Plan IR node id (``PlanNode.id``) this row reports on — always a
    #: shared-segment node (merge / transform / sink), never a source node
    #: (source nodes report through SourceMeasurement, untouched by this
    #: table).
    node_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: merge | dedupe | store | transform-node-type — PlanNode.type verbatim,
    #: kept alongside node_id so a report doesn't need to re-parse the graph
    #: to know what kind of node failed.
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Item count the node received on its input (e.g. merge's combined
    #: upstream count, dedupe's pre-dedupe count) — 0 for a node that never
    #: ran because an upstream node in the same shared segment already
    #: failed.
    items_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Item count the node produced on its output (post-dedupe survivors,
    #: records actually stored by the store sink, ...).
    items_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Extra per-node-type context (e.g. dedupe's dropped-count, store's
    #: skipped-count) for debugging without reconstructing from logs —
    #: mirrors SourceMeasurement.raw / ControlActionRecord.payload precedent.
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
