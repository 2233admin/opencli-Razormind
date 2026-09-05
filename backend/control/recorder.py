"""record_run_measurement: persist one truthful SourceMeasurement row per run.

See docs/CONTROL_THEORY_ARCHITECTURE.md §0: "a controller cannot be built on a
sensor that lies." This module is the write side of that fix — it turns the
REAL outcome of one run (success or failure) into a
``backend.models.source_measurement.SourceMeasurement`` row, reusing
``backend.control.measurements.SourceMeasurement.derive()`` for the rate math
so the derivation logic lives in exactly one place.

Called from the pipeline AFTER a run's outcome is known (see
``backend/pipeline/pipeline.py``) — including failed runs: a failed run is
evidence too (empty accepted/duplicates, the terminal error_kind, whatever
latency was measured before the failure), not something to skip recording.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.control import error_kinds as _error_kinds
from backend.control.error_kinds import ErrorKind
from backend.control.measurements import SourceMeasurement as MeasurementContract

logger = logging.getLogger(__name__)


@dataclass
class FreshnessInfo:
    """What the caller knows about item recency for this run — see TASK item 5.

    ``quality`` MUST be one of: source | observed_fallback | missing | invalid
    | synthetic. Defaults to "missing": the honest default when item
    timestamps were not wired through for a channel, rather than fabricating
    a freshness signal.
    """

    newest_source_ts: datetime | None = None
    newest_observed_at: datetime | None = None
    freshness_lag_seconds: int | None = None
    quality: str = "missing"


_VALID_QUALITIES = frozenset({"source", "observed_fallback", "missing", "invalid", "synthetic"})


async def record_run_measurement(
    session: Any,
    *,
    source_id: str,
    run_id: str,
    accepted: int = 0,
    duplicates: int = 0,
    rejected: int = 0,
    fetch_latency_ms: int = 0,
    ingest_latency_ms: int | None = None,
    store_latency_ms: int | None = None,
    cursor_advanced: bool = False,
    error_type: str | None = None,
    error_kind: ErrorKind | None = None,
    freshness: FreshnessInfo | None = None,
    raw: dict[str, Any] | None = None,
    measured_at: datetime | None = None,
) -> Any:
    """Build a SourceMeasurement (contract) via ``.derive()``, then insert the
    persisted row. Does NOT commit — callers already hold a session with its
    own commit boundary (mirrors every other write in this pipeline: sinks,
    cursor_store, events).

    ``error_kind`` wins over ``error_type`` when both are given (a caller that
    already resolved the kind shouldn't have it re-derived); otherwise
    ``error_type`` is mapped via ``error_kinds.map_error_type`` — a pure
    lookup against the EXISTING structured error_type/taxonomy, never a parse
    of ``error_message`` strings. v1 records the run's single terminal error
    as ``{kind: 1}`` (empty dict on a fully successful run with no terminal
    error) — a per-item error histogram is a future extension, not this PR.
    """
    from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow

    now = measured_at or datetime.now(UTC)
    fresh = freshness or FreshnessInfo()
    quality = fresh.quality if fresh.quality in _VALID_QUALITIES else "missing"

    contract = MeasurementContract.derive(
        source_id=source_id,
        run_id=run_id,
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        fetch_latency_ms=fetch_latency_ms,
        observed_at=now,
        cursor_advanced=cursor_advanced,
        ingest_latency_ms=ingest_latency_ms,
        store_latency_ms=store_latency_ms,
        freshness_lag_seconds=fresh.freshness_lag_seconds,
    )

    # No terminal error at all (neither an explicit ErrorKind nor a structured
    # error_type) → an empty histogram, not a fabricated "unknown" entry. A
    # caller that DOES have a terminal error (even an unmapped one) passes
    # error_type/error_kind explicitly and gets {"unknown": 1} instead.
    error_kinds_payload: dict[str, int] = {}
    if error_kind is not None:
        error_kinds_payload = {error_kind.value: 1}
    elif error_type is not None:
        error_kinds_payload = {_error_kinds.map_error_type(error_type).value: 1}

    row = SourceMeasurementRow(
        source_id=contract.source_id,
        run_id=contract.run_id,
        measured_at=contract.observed_at,
        accepted=contract.accepted,
        duplicates=contract.duplicates,
        rejected=contract.rejected,
        error_rate=contract.error_rate,
        duplicate_rate=contract.duplicate_rate,
        error_kinds=error_kinds_payload,
        fetch_latency_ms=contract.fetch_latency_ms,
        ingest_latency_ms=contract.ingest_latency_ms,
        store_latency_ms=contract.store_latency_ms,
        cursor_advanced=contract.cursor_advanced,
        newest_source_ts=fresh.newest_source_ts,
        newest_observed_at=fresh.newest_observed_at,
        freshness_lag_seconds=contract.freshness_lag_seconds,
        source_ts_quality=quality,
        raw=raw or {},
    )
    session.add(row)
    await session.flush()
    logger.debug(
        "recorded SourceMeasurement | source=%s run=%s accepted=%d rejected=%d "
        "error_kinds=%s cursor_advanced=%s quality=%s",
        source_id, run_id, accepted, rejected, error_kinds_payload, cursor_advanced, quality,
    )
    return row
