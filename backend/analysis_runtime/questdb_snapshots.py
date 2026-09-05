"""Fixed QuestDB transport for redacted Analysis Snapshot projections."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from backend.analysis_runtime.questdb import QuestDBAnalysisRuntime

TRACE_TABLE = "opencli_analysis_trace_v1"
ACQUISITION_TABLE = "opencli_analysis_acquisition_v1"
_SQL_PATH = "/api/v1/sql/execute"
_INSERT_BATCH_SIZE = 20
_FAILURE_EVENT_TYPES = frozenset({"failed", "blocked"})
_FAILURE_ACQUISITION_STATUSES = frozenset({"failed", "cancelled"})


class QuestDBSnapshotOperationCode(StrEnum):
    EXPORT_FAILED = "export_failed"
    SUMMARY_FAILED = "summary_failed"


class QuestDBSnapshotOperationError(RuntimeError):
    def __init__(self, code: QuestDBSnapshotOperationCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class WorkflowTraceSnapshotRow:
    source_id: str
    run_id: str
    workflow_id: str
    trace_id: str
    node_id: str
    sequence: int
    event_type: str
    occurred_at: datetime


@dataclass(frozen=True)
class AcquisitionExecutionSnapshotRow:
    source_id: str
    run_id: str
    capability_id: str
    capability_version: str
    output_schema_version: str
    status: str
    runtime_kind: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


@dataclass(frozen=True)
class QuestDBSnapshotSummary:
    total: int
    per_minute: float
    latency_values_ms: tuple[int, ...]
    failed: int
    event_types: tuple[tuple[str, int], ...]
    nodes: tuple[tuple[str | None, int, int], ...]

    @property
    def average_ms(self) -> float | None:
        if not self.latency_values_ms:
            return None
        return sum(self.latency_values_ms) / len(self.latency_values_ms)

    @property
    def p95_ms(self) -> float | None:
        if not self.latency_values_ms:
            return None
        index = max(0, math.ceil(len(self.latency_values_ms) * 0.95) - 1)
        return float(sorted(self.latency_values_ms)[index])

    @property
    def max_ms(self) -> float | None:
        if not self.latency_values_ms:
            return None
        return float(max(self.latency_values_ms))


class QuestDBSnapshotRuntime(QuestDBAnalysisRuntime):
    """Write and query only the two versioned Analysis Snapshot row families."""

    async def export_snapshot(
        self,
        *,
        snapshot_id: str,
        trace_rows: Sequence[WorkflowTraceSnapshotRow],
        acquisition_rows: Sequence[AcquisitionExecutionSnapshotRow],
    ) -> None:
        async with self._client() as client:
            await self._execute(
                client,
                _trace_table_ddl(),
                QuestDBSnapshotOperationCode.EXPORT_FAILED,
            )
            await self._execute(
                client,
                _acquisition_table_ddl(),
                QuestDBSnapshotOperationCode.EXPORT_FAILED,
            )
            for batch in _batches(trace_rows):
                await self._execute(
                    client,
                    _trace_insert(snapshot_id, batch),
                    QuestDBSnapshotOperationCode.EXPORT_FAILED,
                )
            for batch in _batches(acquisition_rows):
                await self._execute(
                    client,
                    _acquisition_insert(snapshot_id, batch),
                    QuestDBSnapshotOperationCode.EXPORT_FAILED,
                )

    async def read_summary(
        self,
        *,
        snapshot_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> QuestDBSnapshotSummary:
        snapshot = _sql_string(snapshot_id)
        start = _sql_timestamp(start_at)
        end = _sql_timestamp(end_at)
        trace_query = (
            "SELECT event_type, node_id, count() row_count "
            f"FROM {TRACE_TABLE} WHERE snapshot_id = {snapshot} "
            f"AND event_ts >= {start} AND event_ts < {end} "
            "GROUP BY event_type, node_id ORDER BY event_type, node_id"
        )
        acquisition_query = (
            "SELECT status, count() row_count "
            f"FROM {ACQUISITION_TABLE} WHERE snapshot_id = {snapshot} "
            f"AND event_ts >= {start} AND event_ts < {end} "
            "GROUP BY status ORDER BY status"
        )
        latency_query = (
            f"SELECT duration_ms FROM {ACQUISITION_TABLE} "
            f"WHERE snapshot_id = {snapshot} AND event_ts >= {start} AND event_ts < {end} "
            "AND duration_ms IS NOT NULL ORDER BY duration_ms"
        )
        async with self._client() as client:
            trace = await self._execute(
                client, trace_query, QuestDBSnapshotOperationCode.SUMMARY_FAILED
            )
            acquisition = await self._execute(
                client, acquisition_query, QuestDBSnapshotOperationCode.SUMMARY_FAILED
            )
            latency = await self._execute(
                client, latency_query, QuestDBSnapshotOperationCode.SUMMARY_FAILED
            )
        return _summary_from_datasets(
            trace.get("dataset"),
            acquisition.get("dataset"),
            latency.get("dataset"),
            start_at=start_at,
            end_at=end_at,
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.questdb_analysis_runtime_timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        )

    async def _execute(
        self,
        client: httpx.AsyncClient,
        statement: str,
        error_code: QuestDBSnapshotOperationCode,
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{self._settings.questdb_analysis_runtime_url.rstrip('/')}{_SQL_PATH}",
                params={"query": statement},
            )
            payload = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise QuestDBSnapshotOperationError(error_code) from exc
        if response.status_code != httpx.codes.OK or not isinstance(payload, dict):
            raise QuestDBSnapshotOperationError(error_code)
        return payload


def _trace_table_ddl() -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {TRACE_TABLE} ("
        "snapshot_id STRING, source_id STRING, run_id STRING, workflow_id STRING, "
        "trace_id STRING, node_id STRING, sequence LONG, event_type SYMBOL, "
        "schema_version INT, event_ts TIMESTAMP"
        ") TIMESTAMP(event_ts) PARTITION BY DAY WAL "
        "DEDUP UPSERT KEYS(event_ts, snapshot_id, source_id)"
    )


def _acquisition_table_ddl() -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {ACQUISITION_TABLE} ("
        "snapshot_id STRING, source_id STRING, run_id STRING, capability_id STRING, "
        "capability_version STRING, output_schema_version STRING, status SYMBOL, "
        "runtime_kind SYMBOL, finished_at TIMESTAMP, duration_ms LONG, "
        "schema_version INT, event_ts TIMESTAMP"
        ") TIMESTAMP(event_ts) PARTITION BY DAY WAL "
        "DEDUP UPSERT KEYS(event_ts, snapshot_id, source_id)"
    )


def _trace_insert(
    snapshot_id: str,
    rows: Sequence[WorkflowTraceSnapshotRow],
) -> str:
    values = ",".join(
        "("
        + ",".join(
            (
                _sql_string(snapshot_id),
                _sql_string(row.source_id),
                _sql_string(row.run_id),
                _sql_string(row.workflow_id),
                _sql_string(row.trace_id),
                _sql_string(row.node_id),
                str(row.sequence),
                _sql_string(row.event_type),
                "1",
                _sql_timestamp(row.occurred_at),
            )
        )
        + ")"
        for row in rows
    )
    return f"INSERT INTO {TRACE_TABLE} VALUES {values}"


def _acquisition_insert(
    snapshot_id: str,
    rows: Sequence[AcquisitionExecutionSnapshotRow],
) -> str:
    values = ",".join(
        "("
        + ",".join(
            (
                _sql_string(snapshot_id),
                _sql_string(row.source_id),
                _sql_string(row.run_id),
                _sql_string(row.capability_id),
                _sql_string(row.capability_version),
                _sql_string(row.output_schema_version),
                _sql_string(row.status),
                _sql_string(row.runtime_kind),
                _sql_timestamp(row.finished_at) if row.finished_at else "null",
                str(row.duration_ms) if row.duration_ms is not None else "null",
                "1",
                _sql_timestamp(row.started_at),
            )
        )
        + ")"
        for row in rows
    )
    return f"INSERT INTO {ACQUISITION_TABLE} VALUES {values}"


def _sql_string(value: str) -> str:
    if "\x00" in value:
        raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.EXPORT_FAILED)
    return "'" + value.replace("'", "''") + "'"


def _sql_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.EXPORT_FAILED)
    utc_value = value.astimezone(UTC)
    return _sql_string(utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z"))


def _batches(rows: Sequence[Any]) -> Iterable[Sequence[Any]]:
    for offset in range(0, len(rows), _INSERT_BATCH_SIZE):
        yield rows[offset : offset + _INSERT_BATCH_SIZE]


def _summary_from_datasets(
    trace_dataset: object,
    acquisition_dataset: object,
    latency_dataset: object,
    *,
    start_at: datetime,
    end_at: datetime,
) -> QuestDBSnapshotSummary:
    if (
        not isinstance(trace_dataset, list)
        or not isinstance(acquisition_dataset, list)
        or not isinstance(latency_dataset, list)
    ):
        raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.SUMMARY_FAILED)

    event_types: Counter[str] = Counter()
    node_totals: Counter[str | None] = Counter()
    node_failures: Counter[str | None] = Counter()
    trace_total = 0
    for item in trace_dataset:
        if (
            not isinstance(item, list)
            or len(item) != 3
            or not isinstance(item[0], str)
            or not (isinstance(item[1], str) or item[1] is None)
            or not isinstance(item[2], int)
            or item[2] < 0
        ):
            raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.SUMMARY_FAILED)
        event_type, node_id, count = item
        event_types[event_type] += count
        node_totals[node_id] += count
        if event_type in _FAILURE_EVENT_TYPES:
            node_failures[node_id] += count
        trace_total += count

    acquisition_total = 0
    acquisition_failed = 0
    for item in acquisition_dataset:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], int)
            or item[1] < 0
        ):
            raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.SUMMARY_FAILED)
        status, count = item
        acquisition_total += count
        if status in _FAILURE_ACQUISITION_STATUSES:
            acquisition_failed += count

    latency_values: list[int] = []
    for item in latency_dataset:
        if (
            not isinstance(item, list)
            or len(item) != 1
            or not isinstance(item[0], int)
            or item[0] < 0
        ):
            raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.SUMMARY_FAILED)
        latency_values.append(item[0])

    total = trace_total + acquisition_total
    trace_failed = sum(event_types[event_type] for event_type in _FAILURE_EVENT_TYPES)
    minutes = (end_at - start_at).total_seconds() / 60
    return QuestDBSnapshotSummary(
        total=total,
        per_minute=total / minutes,
        latency_values_ms=tuple(latency_values),
        failed=trace_failed + acquisition_failed,
        event_types=tuple(sorted(event_types.items())),
        nodes=tuple(
            (node_id, node_totals[node_id], node_failures[node_id])
            for node_id in sorted(node_totals, key=lambda value: value or "")
        ),
    )


__all__ = [
    "AcquisitionExecutionSnapshotRow",
    "QuestDBSnapshotOperationCode",
    "QuestDBSnapshotOperationError",
    "QuestDBSnapshotRuntime",
    "QuestDBSnapshotSummary",
    "WorkflowTraceSnapshotRow",
]
