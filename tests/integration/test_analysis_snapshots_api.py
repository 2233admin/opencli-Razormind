from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.analysis_runtime import (
    QuestDBSnapshotSummary,
    RuntimeCapabilityReasonCode,
    RuntimeCapabilityState,
    RuntimeCapabilityStatus,
)
from backend.api.v1.analysis_snapshots import get_analysis_snapshot_runtime
from backend.main import app
from backend.models.acquisition import AcquisitionExecution, AcquisitionExecutionStatus
from backend.models.analysis_snapshot import AnalysisSnapshotReceipt
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.workflow_run import WorkflowRunEvent
from backend.schemas.workflow import WorkflowNodeRunEvent
from backend.security.identity import RequestIdentity, get_request_identity
from tests.integration.iii_collection_test_support import create_scoped_run


class FakeSnapshotRuntime:
    def __init__(self, *, fail_status: bool = False) -> None:
        self.fail_status = fail_status
        self.exports: list[dict] = []

    async def get_status(self) -> RuntimeCapabilityStatus:
        if self.fail_status:
            raise RuntimeError("private-runtime-endpoint")
        return RuntimeCapabilityStatus(
            state=RuntimeCapabilityState.READY,
            reason_code=RuntimeCapabilityReasonCode.READY,
        )

    async def export_snapshot(self, **payload) -> None:
        self.exports.append(payload)

    async def read_summary(self, **_payload) -> QuestDBSnapshotSummary:
        return QuestDBSnapshotSummary(
            total=2,
            per_minute=2,
            latency_values_ms=(250,),
            failed=0,
            event_types=(("completed", 1),),
            nodes=(("opencli-source", 1, 0),),
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _range_body(scope: dict) -> dict[str, str]:
    return {
        "startAt": _as_utc(scope["run"].created_at).isoformat().replace("+00:00", "Z"),
        "endAt": _as_utc(scope["run"].updated_at).isoformat().replace("+00:00", "Z"),
    }


def _route(scope: dict, *, project_id: str | None = None, run_id: str | None = None) -> str:
    return (
        f"/api/v1/workspaces/{scope['workspace'].id}"
        f"/projects/{project_id or scope['project'].id}"
        f"/workflows/{scope['workflow'].id}"
        f"/runs/{run_id or scope['run'].id}/analysis-snapshots"
    )


async def _seed_scope(
    db_session,
    *,
    role: WorkspaceRole,
    run_status: str = "completed",
) -> tuple[dict, User]:
    scope = await create_scoped_run(db_session)
    start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    scope["run"].status = run_status
    scope["run"].created_at = start
    scope["run"].updated_at = end

    event = WorkflowNodeRunEvent(
        id="analysis-api-event",
        sequence=1,
        workflowId=scope["workflow"].id,
        workflowRunId=scope["run"].id,
        traceId=scope["run"].trace_id,
        nodeId="opencli-source",
        eventType="completed",
        createdAt=(start + timedelta(minutes=10)).isoformat(),
        message="trace-message-secret",
        details={"credential": "credential-secret", "url": "https://private.invalid"},
    )
    event_row = WorkflowRunEvent(
        id="analysis-api-event-row",
        run_id=scope["run"].id,
        workflow_id=scope["workflow"].id,
        trace_id=scope["run"].trace_id,
        event_id=event.id,
        node_id=event.nodeId,
        sequence=event.sequence,
        event_type=event.eventType,
        payload=event.model_dump(mode="json"),
        created_at=end,
        updated_at=end,
    )
    acquisition = AcquisitionExecution(
        id="analysis-api-acquisition",
        request_id="analysis-api-request",
        idempotency_key="analysis-api-acquisition",
        request_fingerprint="a" * 64,
        capability_id="official-site.observe",
        capability_version="1.0.0",
        output_schema_version="1",
        input_payload={"token": "input-token-secret"},
        environment={"endpoint": "https://private.invalid"},
        required_artifacts=[],
        geo_refs={},
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=scope["workflow"].id,
        run_id=scope["run"].id,
        status=AcquisitionExecutionStatus.SUCCEEDED,
        result_payload={"raw": "result-secret"},
        failure=None,
        artifact_refs=[{"secret": "artifact-secret"}],
        started_at=start + timedelta(minutes=20),
        finished_at=start + timedelta(minutes=20, milliseconds=250),
        created_at=start,
        updated_at=end,
    )
    identity_workspace = Workspace(
        id=scope["workspace"].id,
        name="Analysis API",
        slug=f"analysis-api-{role.value}",
    )
    user = User(id=f"analysis-{role.value}", subject=f"analysis-{role.value}")
    membership = WorkspaceMembership(
        workspace_id=identity_workspace.id,
        user_id=user.id,
        role=role,
    )
    db_session.add_all([event_row, acquisition, identity_workspace, user, membership])
    await db_session.commit()
    await db_session.refresh(scope["run"])
    return scope, user


def _override_dependencies(user: User, runtime: FakeSnapshotRuntime) -> None:
    async def override_identity() -> RequestIdentity:
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_request_identity] = override_identity
    app.dependency_overrides[get_analysis_snapshot_runtime] = lambda: runtime


def _clear_dependencies() -> None:
    app.dependency_overrides.pop(get_request_identity, None)
    app.dependency_overrides.pop(get_analysis_snapshot_runtime, None)


@pytest.mark.asyncio
async def test_operator_can_export_restore_and_read_curated_summary(client, db_session) -> None:
    scope, user = await _seed_scope(db_session, role=WorkspaceRole.OPERATOR)
    runtime = FakeSnapshotRuntime()
    _override_dependencies(user, runtime)
    route = _route(scope)
    request_body = _range_body(scope)
    responses = []
    try:
        capability = await client.get(f"{route}/capability")
        preview = await client.post(f"{route}/preview", json=request_body)
        exported = await client.post(route, json=request_body)
        snapshot_id = exported.json()["data"]["snapshotId"]
        listed = await client.get(route)
        receipt = await client.get(f"{route}/{snapshot_id}")
        summary = await client.get(f"{route}/{snapshot_id}/summary")
        responses = [capability, preview, exported, listed, receipt, summary]
    finally:
        _clear_dependencies()

    assert [response.status_code for response in responses] == [200, 200, 201, 200, 200, 200]
    assert capability.json()["data"] == {
        "runtime": "questdb",
        "state": "ready",
        "reasonCode": "ready",
    }
    assert preview.json()["data"]["rowCounts"] == {
        "workflowTraceEvents": 1,
        "acquisitionExecutionMetrics": 1,
        "total": 2,
    }
    assert exported.json()["data"]["status"] == "completed"
    assert listed.json()["data"] == [exported.json()["data"]]
    assert receipt.json()["data"] == exported.json()["data"]
    assert summary.json()["data"]["snapshotId"] == exported.json()["data"]["snapshotId"]
    assert summary.json()["data"]["latency"] == {
        "sampleCount": 1,
        "averageMs": 250.0,
        "p95Ms": 250.0,
        "maxMs": 250.0,
    }
    assert len(runtime.exports) == 1
    serialized = "".join(response.text for response in responses)
    for forbidden in (
        "trace-message-secret",
        "credential-secret",
        "private.invalid",
        "input-token-secret",
        "result-secret",
        "artifact-secret",
        "snapshot_id",
    ):
        assert forbidden not in serialized
    for secret in (
        "trace-message-secret",
        "credential-secret",
        "private.invalid",
        "input-token-secret",
        "result-secret",
        "artifact-secret",
    ):
        assert secret not in repr(runtime.exports)


@pytest.mark.asyncio
async def test_viewer_can_preview_but_cannot_export(client, db_session) -> None:
    scope, user = await _seed_scope(db_session, role=WorkspaceRole.VIEWER)
    runtime = FakeSnapshotRuntime()
    _override_dependencies(user, runtime)
    route = _route(scope)
    try:
        capability = await client.get(f"{route}/capability")
        preview = await client.post(f"{route}/preview", json=_range_body(scope))
        denied = await client.post(route, json=_range_body(scope))
    finally:
        _clear_dependencies()

    assert capability.status_code == 200
    assert preview.status_code == 200
    assert denied.status_code == 403
    assert await db_session.scalar(select(func.count()).select_from(AnalysisSnapshotReceipt)) == 0
    assert runtime.exports == []


@pytest.mark.asyncio
async def test_scope_is_exact_and_noncompleted_runs_are_rejected(client, db_session) -> None:
    scope, user = await _seed_scope(
        db_session,
        role=WorkspaceRole.OPERATOR,
        run_status="partial_success",
    )
    runtime = FakeSnapshotRuntime()
    _override_dependencies(user, runtime)
    try:
        incomplete = await client.post(f"{_route(scope)}/preview", json=_range_body(scope))
        wrong_project = await client.get(
            f"{_route(scope, project_id='another-project')}/capability"
        )
        wrong_run = await client.get(f"{_route(scope, run_id='another-run')}/capability")
    finally:
        _clear_dependencies()

    assert incomplete.status_code == 409
    assert incomplete.json()["detail"] == "analysis_run_not_completed"
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"] == "analysis_project_not_found"
    assert wrong_run.status_code == 404
    assert wrong_run.json()["detail"] == "analysis_run_not_found"


@pytest.mark.asyncio
async def test_capability_failure_is_bounded_and_redacted(client, db_session) -> None:
    scope, user = await _seed_scope(db_session, role=WorkspaceRole.VIEWER)
    runtime = FakeSnapshotRuntime(fail_status=True)
    _override_dependencies(user, runtime)
    try:
        response = await client.get(f"{_route(scope)}/capability")
    finally:
        _clear_dependencies()

    assert response.status_code == 200
    assert response.json()["data"] == {
        "runtime": "questdb",
        "state": "unavailable",
        "reasonCode": "connection_failed",
    }
    assert "private-runtime-endpoint" not in response.text
