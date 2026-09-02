from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.analysis_runtime import (
    QuestDBSnapshotOperationCode,
    QuestDBSnapshotOperationError,
    QuestDBSnapshotSummary,
    RuntimeCapabilityReasonCode,
    RuntimeCapabilityState,
    RuntimeCapabilityStatus,
)
from backend.models.acquisition import AcquisitionExecution, AcquisitionExecutionStatus
from backend.models.analysis_snapshot import AnalysisSnapshotReceipt, AnalysisSnapshotStatus
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowValidationRun,
    StudioWorkflowVersion,
    StudioWorkspace,
)
from backend.models.workflow_run import WorkflowRun, WorkflowRunEvent
from backend.schemas.analysis_snapshot import AnalysisSnapshotRangeV1
from backend.schemas.workflow import WorkflowNodeRunEvent
from backend.services import analysis_snapshot_service as service


class FakeRuntime:
    def __init__(
        self,
        state: RuntimeCapabilityState = RuntimeCapabilityState.READY,
        *,
        fail_export: bool = False,
        fail_unexpectedly: bool = False,
    ) -> None:
        self.state = state
        self.fail_export = fail_export
        self.fail_unexpectedly = fail_unexpectedly
        self.exports: list[dict] = []

    async def get_status(self) -> RuntimeCapabilityStatus:
        reason = {
            RuntimeCapabilityState.DISABLED: RuntimeCapabilityReasonCode.DISABLED_BY_CONFIGURATION,
            RuntimeCapabilityState.UNAVAILABLE: RuntimeCapabilityReasonCode.CONNECTION_FAILED,
            RuntimeCapabilityState.UNHEALTHY: RuntimeCapabilityReasonCode.HEALTH_CHECK_FAILED,
            RuntimeCapabilityState.READY: RuntimeCapabilityReasonCode.READY,
        }[self.state]
        return RuntimeCapabilityStatus(state=self.state, reason_code=reason)

    async def export_snapshot(self, **payload) -> None:
        self.exports.append(payload)
        if self.fail_unexpectedly:
            raise RuntimeError("transport-secret-must-not-escape")
        if self.fail_export:
            raise QuestDBSnapshotOperationError(
                QuestDBSnapshotOperationCode.EXPORT_FAILED
            )

    async def read_summary(self, **_payload) -> QuestDBSnapshotSummary:
        return QuestDBSnapshotSummary(
            total=4,
            per_minute=2,
            latency_values_ms=(10, 20),
            failed=1,
            event_types=(("completed", 2), ("failed", 1)),
            nodes=(("node-1", 3, 1),),
        )


async def _seed_completed_run(db_session) -> service.AnalysisSnapshotScope:
    start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    workspace = StudioWorkspace(
        id="analysis-workspace",
        name="Analysis",
        slug="analysis",
        created_at=start,
        updated_at=start,
    )
    project = StudioProject(
        id="analysis-project",
        workspace_id=workspace.id,
        name="Analysis Project",
        slug="analysis-project",
        created_by_user_id="analysis-user",
        created_at=start,
        updated_at=start,
    )
    workflow = StudioWorkflow(
        id="analysis-workflow",
        project_id=project.id,
        name="Analysis Workflow",
        created_at=start,
        updated_at=start,
    )
    validation = StudioWorkflowValidationRun(
        id="analysis-validation",
        workflow_id=workflow.id,
        draft_revision=1,
        status="valid",
        valid=True,
        errors=[],
        warnings=[],
        compile_version="v1",
        resolved_graph={"nodes": [{"id": "node-1"}]},
        created_at=start,
        updated_at=start,
    )
    version = StudioWorkflowVersion(
        id="analysis-version",
        workflow_id=workflow.id,
        version=1,
        draft_revision=1,
        graph={"nodes": [{"id": "node-1"}]},
        compile_version="v1",
        validation_run_id=validation.id,
        published_by_user_id="analysis-user",
        reason="test",
        created_at=start,
        updated_at=start,
    )
    run = WorkflowRun(
        id="analysis-run",
        workflow_id=workflow.id,
        studio_workflow_version_id=version.id,
        trace_id="analysis-trace",
        status="completed",
        request={"input": {"secret": "run-input-secret"}},
        projection={"status": "completed"},
        created_at=start,
        updated_at=end,
    )
    event = WorkflowNodeRunEvent(
        id="analysis-event",
        sequence=1,
        workflowId=workflow.id,
        workflowRunId=run.id,
        traceId=run.trace_id,
        nodeId="node-1",
        eventType="completed",
        createdAt=datetime(2026, 9, 1, 0, 10, tzinfo=UTC).isoformat(),
        message="raw-message-secret",
        details={"credential": "credential-secret", "url": "https://secret.invalid"},
    )
    event_row = WorkflowRunEvent(
        id="analysis-event-row",
        run_id=run.id,
        workflow_id=workflow.id,
        trace_id=run.trace_id,
        event_id=event.id,
        node_id=event.nodeId,
        sequence=event.sequence,
        event_type=event.eventType,
        payload=event.model_dump(mode="json"),
        created_at=end,
        updated_at=end,
    )
    linked = AcquisitionExecution(
        id="analysis-acquisition",
        request_id="request-1",
        idempotency_key="analysis-acquisition",
        request_fingerprint="a" * 64,
        capability_id="official-site.observe",
        capability_version="1.0.0",
        output_schema_version="1",
        input_payload={"token": "input-token-secret"},
        environment={"endpoint": "https://secret.invalid"},
        required_artifacts=[],
        geo_refs={},
        workspace_id=workspace.id,
        project_id=project.id,
        workflow_id=workflow.id,
        run_id=run.id,
        status=AcquisitionExecutionStatus.SUCCEEDED,
        result_payload={"raw": "result-secret"},
        failure=None,
        artifact_refs=[{"secret": "artifact-secret"}],
        started_at=datetime(2026, 9, 1, 0, 20, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, 0, 20, 0, 250000, tzinfo=UTC),
        created_at=start,
        updated_at=end,
    )
    legacy = AcquisitionExecution(
        id="legacy-unlinked-acquisition",
        request_id="request-legacy",
        idempotency_key="legacy-unlinked-acquisition",
        request_fingerprint="b" * 64,
        capability_id="official-site.observe",
        capability_version="1.0.0",
        output_schema_version="1",
        input_payload={"runId": run.id},
        environment={},
        required_artifacts=[],
        geo_refs={"runId": run.id},
        status=AcquisitionExecutionStatus.FAILED,
        failure={"message": "legacy-failure-secret"},
        artifact_refs=[],
        started_at=datetime(2026, 9, 1, 0, 30, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, 0, 31, tzinfo=UTC),
        created_at=start,
        updated_at=end,
    )
    db_session.add_all(
        [workspace, project, workflow, validation, version, run, event_row, linked, legacy]
    )
    await db_session.commit()
    return await service.resolve_scope(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        workflow_id=workflow.id,
        run_id=run.id,
    )


def _range() -> AnalysisSnapshotRangeV1:
    return AnalysisSnapshotRangeV1(
        start_at=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 9, 1, 1, 0, tzinfo=UTC),
    )


async def test_projection_uses_event_source_time_and_excludes_legacy_json_links(
    db_session,
) -> None:
    scope = await _seed_completed_run(db_session)

    projection = await service.build_projection(
        db_session,
        scope=scope,
        source_range=_range(),
    )

    assert projection.preview.row_counts.model_dump(by_alias=True) == {
        "workflowTraceEvents": 1,
        "acquisitionExecutionMetrics": 1,
        "total": 2,
    }
    assert projection.trace_rows[0].occurred_at == datetime(
        2026, 9, 1, 0, 10, tzinfo=UTC
    )
    assert projection.acquisition_rows[0].duration_ms == 250
    serialized = repr(projection)
    for forbidden in (
        "raw-message-secret",
        "credential-secret",
        "secret.invalid",
        "input-token-secret",
        "result-secret",
        "artifact-secret",
        "legacy-failure-secret",
    ):
        assert forbidden not in serialized


async def test_create_is_deterministic_idempotent_and_persists_authoritative_receipt(
    db_session,
) -> None:
    scope = await _seed_completed_run(db_session)
    runtime = FakeRuntime()

    first = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=runtime,  # type: ignore[arg-type]
    )
    replay = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=runtime,  # type: ignore[arg-type]
    )

    assert first == replay
    assert first.status == AnalysisSnapshotStatus.COMPLETED
    assert first.snapshot_id == "e2e483f4-f7bf-5a12-b06f-5d156a9c71bc"
    assert len(runtime.exports) == 1
    assert await db_session.scalar(select(func.count()).select_from(AnalysisSnapshotReceipt)) == 1


@pytest.mark.parametrize(
    ("state", "expected_failure"),
    [
        (RuntimeCapabilityState.DISABLED, "runtime_disabled"),
        (RuntimeCapabilityState.UNAVAILABLE, "runtime_unavailable"),
        (RuntimeCapabilityState.UNHEALTHY, "runtime_unhealthy"),
    ],
)
async def test_runtime_failure_leaves_sources_unchanged_and_a_bounded_failed_receipt(
    db_session,
    state: RuntimeCapabilityState,
    expected_failure: str,
) -> None:
    scope = await _seed_completed_run(db_session)
    source_counts = (
        await db_session.scalar(select(func.count()).select_from(WorkflowRunEvent)),
        await db_session.scalar(select(func.count()).select_from(AcquisitionExecution)),
    )

    result = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=FakeRuntime(state),  # type: ignore[arg-type]
    )

    assert result.status == AnalysisSnapshotStatus.FAILED
    assert result.failure_code == expected_failure
    assert source_counts == (
        await db_session.scalar(select(func.count()).select_from(WorkflowRunEvent)),
        await db_session.scalar(select(func.count()).select_from(AcquisitionExecution)),
    )


async def test_unexpected_runtime_failure_is_bounded_to_a_redacted_receipt(db_session) -> None:
    scope = await _seed_completed_run(db_session)

    receipt = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=FakeRuntime(fail_unexpectedly=True),  # type: ignore[arg-type]
    )

    assert receipt.status == AnalysisSnapshotStatus.FAILED
    assert receipt.failure_code == "export_failed"
    assert "transport-secret-must-not-escape" not in receipt.model_dump_json()


async def test_failed_export_retries_the_same_receipt_without_duplicate_facts(db_session) -> None:
    scope = await _seed_completed_run(db_session)
    runtime = FakeRuntime(fail_export=True)

    failed = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=runtime,  # type: ignore[arg-type]
    )
    runtime.fail_export = False
    completed = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=runtime,  # type: ignore[arg-type]
    )

    assert failed.status == AnalysisSnapshotStatus.FAILED
    assert completed.status == AnalysisSnapshotStatus.COMPLETED
    assert completed.snapshot_id == failed.snapshot_id
    assert completed.failure_code is None
    assert len(runtime.exports) == 2
    assert await db_session.scalar(select(func.count()).select_from(AnalysisSnapshotReceipt)) == 1
    persisted = await db_session.get(AnalysisSnapshotReceipt, completed.snapshot_id)
    assert persisted is not None
    assert persisted.attempt_count == 2


async def test_empty_preview_is_valid_but_cannot_create_a_receipt(db_session) -> None:
    scope = await _seed_completed_run(db_session)
    empty_range = AnalysisSnapshotRangeV1(
        start_at=datetime(2026, 9, 1, 0, 40, tzinfo=UTC),
        end_at=datetime(2026, 9, 1, 0, 50, tzinfo=UTC),
    )
    runtime = FakeRuntime()

    projection = await service.build_projection(
        db_session,
        scope=scope,
        source_range=empty_range,
    )
    with pytest.raises(service.AnalysisSnapshotError) as exc_info:
        await service.create_snapshot(
            db_session,
            scope=scope,
            source_range=empty_range,
            requested_by_user_id="analysis-user",
            runtime=runtime,  # type: ignore[arg-type]
        )

    assert projection.preview.eligible is False
    assert projection.preview.row_counts.total == 0
    assert exc_info.value.code == service.AnalysisSnapshotErrorCode.EMPTY
    assert runtime.exports == []
    assert await db_session.scalar(select(func.count()).select_from(AnalysisSnapshotReceipt)) == 0


async def test_submillisecond_browser_rounding_is_clamped_to_run_bounds(db_session) -> None:
    scope = await _seed_completed_run(db_session)
    rounded = AnalysisSnapshotRangeV1(
        start_at=_range().start_at - timedelta(microseconds=999),
        end_at=_range().end_at + timedelta(microseconds=999),
    )

    projection = await service.build_projection(
        db_session,
        scope=scope,
        source_range=rounded,
    )

    assert projection.preview.source_range == _range()


async def test_summary_cites_receipt_and_range_without_mutating_it(db_session) -> None:
    scope = await _seed_completed_run(db_session)
    runtime = FakeRuntime()
    receipt = await service.create_snapshot(
        db_session,
        scope=scope,
        source_range=_range(),
        requested_by_user_id="analysis-user",
        runtime=runtime,  # type: ignore[arg-type]
    )

    summary = await service.read_summary(
        db_session,
        scope=scope,
        snapshot_id=receipt.snapshot_id,
        runtime=runtime,  # type: ignore[arg-type]
    )

    assert summary.snapshot_id == receipt.snapshot_id
    assert summary.source_range == receipt.source_range
    assert summary.failure_rate.model_dump(by_alias=True) == {
        "failed": 1,
        "total": 4,
        "rate": 0.25,
    }
    persisted = await db_session.get(AnalysisSnapshotReceipt, receipt.snapshot_id)
    assert persisted is not None
    assert persisted.status == AnalysisSnapshotStatus.COMPLETED


async def test_range_must_stay_inside_the_exact_completed_run(db_session) -> None:
    scope = await _seed_completed_run(db_session)
    outside = AnalysisSnapshotRangeV1(
        start_at=datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
        end_at=datetime(2026, 9, 1, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(service.AnalysisSnapshotError) as exc_info:
        await service.build_projection(
            db_session,
            scope=scope,
            source_range=outside,
        )

    assert exc_info.value.code == service.AnalysisSnapshotErrorCode.RANGE_OUTSIDE_RUN
