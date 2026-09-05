"""SourceMeasurement (DB row): a persisted, per-run sensor reading.

See docs/CONTROL_THEORY_ARCHITECTURE.md §0/§4 and
``backend/control/measurements.py`` (the pure Pydantic contract this table
mirrors). This is PR-Control-1's "sensor honesty" companion: every run — success
or failure — leaves one row here, built by ``backend.control.recorder`` from
real run outcomes (never guessed), so a future controller (PR-Control-3+) reads
truthful history instead of nothing.

Naming note: this module's ``SourceMeasurement`` is the SQLAlchemy ORM model
(a persisted row). ``backend.control.measurements.SourceMeasurement`` is the
pure Pydantic data contract (in-memory, one run/window). They share a name
because they share a shape; import one or both under an alias where a module
needs both (see ``backend.control.recorder``).
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class SourceMeasurement(TimestampMixin):
    """One sensor reading for one source's run — the persisted time series
    a future control loop (evaluator/policy engine) reads instead of guessing.

    Column groups mirror ``backend.control.measurements.SourceMeasurement``:
      * identity: source_id, run_id, measured_at
      * throughput/quality: accepted, duplicates, rejected, error_rate,
        duplicate_rate, error_kinds
      * latency: fetch_latency_ms, ingest_latency_ms, store_latency_ms
      * control state: cursor_advanced (the REAL commit result, see
        ``backend.pipeline.cursor_store.CommitResult``)
      * freshness: newest_source_ts, newest_observed_at,
        freshness_lag_seconds, source_ts_quality
      * raw: the full derivation inputs, for debugging/replay without
        reconstructing them from logs.
    """

    __tablename__ = "source_measurements"

    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duplicate_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: {ErrorKind.value: count} — v1 records the run's single terminal error
    #: (mapped from the existing structured error_type), not a full per-item
    #: histogram. See backend/control/error_kinds.py.
    error_kinds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    fetch_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingest_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    store_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The REAL commit result (backend.pipeline.cursor_store.CommitResult.advanced),
    #: not a guess — False for non-incremental channels and for runs where the
    #: store returned no new value.
    cursor_advanced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    newest_source_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    newest_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    freshness_lag_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    #: source | observed_fallback | missing | invalid | synthetic — see
    #: docs/CONTROL_THEORY_ARCHITECTURE.md and TASK item 5. "missing" is the
    #: honest default when item timestamps aren't wired through for a channel,
    #: rather than fabricating a freshness signal.
    source_ts_quality: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")

    #: Raw derivation inputs / extra context (e.g. the terminal error message,
    #: pipeline metadata) for debugging without reconstructing from logs.
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
