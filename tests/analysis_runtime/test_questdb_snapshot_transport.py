from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from backend.analysis_runtime import (
    AcquisitionExecutionSnapshotRow,
    QuestDBSnapshotOperationCode,
    QuestDBSnapshotOperationError,
    QuestDBSnapshotRuntime,
    WorkflowTraceSnapshotRow,
)
from backend.config import Settings
from backend.schemas.analysis_snapshot import AnalysisSnapshotRangeV1


def _runtime(handler) -> QuestDBSnapshotRuntime:
    return QuestDBSnapshotRuntime(
        Settings(
            questdb_analysis_runtime_enabled=True,
            questdb_analysis_runtime_url="http://questdb:9000",
            questdb_analysis_runtime_health_url="http://questdb:9003",
        ),
        transport=httpx.MockTransport(handler),
    )


async def test_export_uses_only_versioned_scalar_allowlists_and_escapes_values() -> None:
    statements: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        statements.append(request.url.params["query"])
        return httpx.Response(200, json={"ddl": "OK"})

    runtime = _runtime(handler)
    occurred_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
    await runtime.export_snapshot(
        snapshot_id="snapshot-1",
        trace_rows=[
            WorkflowTraceSnapshotRow(
                source_id="event-'quoted",
                run_id="run-1",
                workflow_id="workflow-1",
                trace_id="trace-1",
                node_id="node-1",
                sequence=1,
                event_type="completed",
                occurred_at=occurred_at,
            )
        ],
        acquisition_rows=[
            AcquisitionExecutionSnapshotRow(
                source_id="acquisition-1",
                run_id="run-1",
                capability_id="official-site.observe",
                capability_version="1.0.0",
                output_schema_version="1",
                status="succeeded",
                runtime_kind="managed-opencli",
                started_at=occurred_at,
                finished_at=occurred_at,
                duration_ms=0,
            )
        ],
    )

    joined = "\n".join(statements)
    assert "opencli_analysis_trace_v1" in joined
    assert "opencli_analysis_acquisition_v1" in joined
    assert "event-''quoted" in joined
    assert "DEDUP UPSERT KEYS(event_ts, snapshot_id, source_id)" in joined
    for forbidden in (
        "raw-message-secret",
        "details",
        "input_payload",
        "result_payload",
        "environment",
        "artifact_refs",
        "failure",
        "endpoint",
        "credential",
        "token",
    ):
        assert forbidden not in joined


async def test_summary_is_derived_from_three_fixed_bounded_queries() -> None:
    statements: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        statement = request.url.params["query"]
        statements.append(statement)
        if "GROUP BY event_type" in statement:
            return httpx.Response(
                200,
                json={
                    "dataset": [
                        ["completed", "node-a", 2],
                        ["failed", "node-a", 1],
                        ["blocked", "node-b", 1],
                    ]
                },
            )
        if "GROUP BY status" in statement:
            return httpx.Response(
                200,
                json={"dataset": [["succeeded", 3], ["failed", 1]]},
            )
        return httpx.Response(200, json={"dataset": [[10], [20], [100]]})

    summary = await _runtime(handler).read_summary(
        snapshot_id="snapshot-1",
        start_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        end_at=datetime(2026, 9, 1, 12, 2, tzinfo=UTC),
    )

    assert len(statements) == 3
    assert all("snapshot-1" in statement for statement in statements)
    assert all("event_ts >=" in statement and "event_ts <" in statement for statement in statements)
    assert summary.total == 8
    assert summary.per_minute == 4
    assert summary.failed == 3
    assert summary.latency_values_ms == (10, 20, 100)
    assert summary.average_ms == pytest.approx(130 / 3)
    assert summary.p95_ms == 100
    assert summary.max_ms == 100
    assert summary.event_types == (("blocked", 1), ("completed", 2), ("failed", 1))
    assert summary.nodes == (("node-a", 3, 1), ("node-b", 1, 1))


async def test_summary_transport_failure_is_bounded_and_redacted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="credential=secret database response")

    with pytest.raises(QuestDBSnapshotOperationError) as exc_info:
        await _runtime(handler).read_summary(
            snapshot_id="snapshot-1",
            start_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
        )

    assert exc_info.value.code == QuestDBSnapshotOperationCode.SUMMARY_FAILED
    assert str(exc_info.value) == "summary_failed"


@pytest.mark.parametrize(
    "payload",
    [
        {"startAt": "2026-09-01T00:00:00", "endAt": "2026-09-01T01:00:00Z"},
        {"startAt": "2026-09-01T00:00:00+08:00", "endAt": "2026-09-01T01:00:00Z"},
        {"startAt": "2026-09-01T01:00:00Z", "endAt": "2026-09-01T01:00:00Z"},
    ],
)
def test_snapshot_range_requires_strictly_ordered_utc_values(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AnalysisSnapshotRangeV1.model_validate(payload)
