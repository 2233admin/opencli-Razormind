from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.analysis_runtime import (
    QuestDBSnapshotOperationCode,
    QuestDBSnapshotOperationError,
    QuestDBSnapshotSummary,
)
from backend.api.v1.analysis_snapshots import get_analysis_snapshot_runtime
from backend.main import app
from backend.models.analysis_finding import AnalysisFinding
from backend.models.analysis_snapshot import (
    AnalysisSnapshotReceipt,
    AnalysisSnapshotStatus,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.security.identity import RequestIdentity, get_request_identity
from tests.integration.iii_collection_test_support import create_scoped_run


class FakeFindingRuntime:
    def __init__(self) -> None:
        self.read_calls = 0
        self.fail_reads = False

    async def read_summary(self, **_payload) -> QuestDBSnapshotSummary:
        self.read_calls += 1
        if self.fail_reads:
            raise QuestDBSnapshotOperationError(QuestDBSnapshotOperationCode.SUMMARY_FAILED)
        return QuestDBSnapshotSummary(
            total=12,
            per_minute=2.5,
            latency_values_ms=(10, 20, 30),
            failed=3,
            event_types=(("completed", 9), ("failed", 3)),
            nodes=(("node-1", 12, 3),),
        )


async def _seed_scope(
    db_session,
    *,
    role: WorkspaceRole = WorkspaceRole.OPERATOR,
    receipt_status: AnalysisSnapshotStatus = AnalysisSnapshotStatus.COMPLETED,
    expired: bool = False,
    suffix: str = "primary",
) -> tuple[dict, User, AnalysisSnapshotReceipt]:
    scope = await create_scoped_run(db_session)
    start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    scope["run"].status = "completed"
    scope["run"].created_at = start
    scope["run"].updated_at = end
    identity_workspace = await db_session.get(Workspace, scope["workspace"].id)
    if identity_workspace is None:
        identity_workspace = Workspace(
            id=scope["workspace"].id,
            name=f"Analysis Finding {suffix}",
            slug=f"analysis-finding-{suffix}",
        )
    user = User(id=f"finding-user-{suffix}", subject=f"finding-user-{suffix}")
    membership = WorkspaceMembership(
        workspace_id=identity_workspace.id,
        user_id=user.id,
        role=role,
    )
    receipt = AnalysisSnapshotReceipt(
        id=f"finding-snapshot-{suffix}",
        runtime="questdb",
        selection_hash=(suffix.encode().hex() + "0" * 64)[:64],
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=scope["workflow"].id,
        studio_workflow_version_id=scope["version"].id,
        run_id=scope["run"].id,
        requested_by_user_id=user.id,
        schema_version=1,
        redaction_version=1,
        source_start_at=start,
        source_end_at=end,
        workflow_trace_event_count=10,
        acquisition_execution_metric_count=2,
        total_row_count=12,
        status=receipt_status,
        failure_code=None,
        attempt_count=1,
        last_attempt_at=end,
        completed_at=end,
        expires_at=end - timedelta(minutes=1) if expired else end + timedelta(days=30),
        created_at=end,
        updated_at=end,
    )
    db_session.add_all([identity_workspace, user, membership, receipt])
    await db_session.commit()
    await db_session.refresh(scope["run"])
    return scope, user, receipt


def _route(scope: dict) -> str:
    return (
        f"/api/v1/workspaces/{scope['workspace'].id}"
        f"/projects/{scope['project'].id}"
        f"/workflows/{scope['workflow'].id}"
        f"/runs/{scope['run'].id}/analysis-findings"
    )


def _create_body(snapshot_receipt_id: str, **overrides) -> dict:
    body = {
        "snapshotReceiptId": snapshot_receipt_id,
        "selector": {"kind": "overall_throughput"},
        "observation": "  Throughput is below the expected baseline.  ",
        "interpretation": "Capacity is constrained during this UTC range.",
        "recommendation": "Review node concurrency before the next run.",
    }
    body.update(overrides)
    return body


def _override_dependencies(user: User, runtime: FakeFindingRuntime) -> None:
    async def override_identity() -> RequestIdentity:
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_request_identity] = override_identity
    app.dependency_overrides[get_analysis_snapshot_runtime] = lambda: runtime


def _clear_dependencies() -> None:
    app.dependency_overrides.pop(get_request_identity, None)
    app.dependency_overrides.pop(get_analysis_snapshot_runtime, None)


@pytest.mark.asyncio
async def test_operator_creates_and_reads_a_durable_scalar_finding(client, db_session) -> None:
    scope, user, receipt = await _seed_scope(db_session)
    runtime = FakeFindingRuntime()
    _override_dependencies(user, runtime)
    route = _route(scope)
    try:
        created = await client.post(route, json=_create_body(receipt.id))
        finding_id = created.json().get("data", {}).get("findingId", "missing")
        listed = await client.get(route)
        detailed = await client.get(f"{route}/{finding_id}")
    finally:
        _clear_dependencies()

    assert [created.status_code, listed.status_code, detailed.status_code] == [201, 200, 200]
    data = created.json()["data"]
    assert listed.json()["data"] == [data]
    assert detailed.json()["data"] == data
    assert data["workspaceId"] == scope["workspace"].id
    assert data["projectId"] == scope["project"].id
    assert data["workflowId"] == scope["workflow"].id
    assert data["workflowVersionId"] == scope["version"].id
    assert data["runId"] == scope["run"].id
    assert data["snapshotReceiptId"] == receipt.id
    assert data["authorUserId"] == user.id
    assert data["sourceRange"]["startAt"].startswith("2026-09-01T00:00:00")
    assert data["sourceRange"]["endAt"].startswith("2026-09-01T01:00:00")
    assert data["observation"] == "Throughput is below the expected baseline."
    assert data["evidence"] == {
        "selectorKind": "overall_throughput",
        "selectorKey": None,
        "metric": "events_per_minute",
        "value": 2.5,
        "unit": "events_per_minute",
    }
    assert runtime.read_calls == 1


@pytest.mark.asyncio
async def test_forbidden_evidence_json_is_rejected_without_reflection(client, db_session) -> None:
    scope, user, receipt = await _seed_scope(db_session)
    runtime = FakeFindingRuntime()
    _override_dependencies(user, runtime)
    body = _create_body(receipt.id)
    body["evidence"] = {
        "sql": "SELECT credential FROM private_table",
        "payload": "forbidden-payload-secret",
        "url": "https://private.invalid",
    }
    try:
        response = await client.post(_route(scope), json=body)
    finally:
        _clear_dependencies()

    assert response.status_code == 422
    assert response.json()["detail"] == "analysis_finding_request_invalid"
    assert "forbidden-payload-secret" not in response.text
    assert "private.invalid" not in response.text
    assert await db_session.scalar(select(func.count()).select_from(AnalysisFinding)) == 0
    assert runtime.read_calls == 0
