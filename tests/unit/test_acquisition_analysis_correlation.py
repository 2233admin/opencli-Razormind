from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.acquisition import (
    AcquisitionExecutionRead,
    AcquisitionRunCorrelation,
    AcquisitionSubmission,
    CapabilityIdentity,
)
from backend.services import acquisition_service
from tests.integration.iii_collection_test_support import create_scoped_run


def _submission(correlation: AcquisitionRunCorrelation | None) -> AcquisitionSubmission:
    return AcquisitionSubmission(
        request_id="analysis-request",
        idempotency_key="analysis-acquisition-1",
        capability=CapabilityIdentity(id="official-site.observe", version="1.0.0"),
        output_schema_version="1",
        input={"url": "https://example.com/private-input"},
        workflow_run_correlation=correlation,
    )


async def test_submission_persists_exact_typed_workflow_run_correlation(db_session) -> None:
    scope = await create_scoped_run(db_session)
    correlation = AcquisitionRunCorrelation(
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=scope["workflow"].id,
        run_id=scope["run"].id,
    )

    outcome = await acquisition_service.submit_execution(
        db_session,
        _submission(correlation),
    )

    assert outcome.execution.workspace_id == scope["workspace"].id
    assert outcome.execution.project_id == scope["project"].id
    assert outcome.execution.workflow_id == scope["workflow"].id
    assert outcome.execution.run_id == scope["run"].id
    assert (
        AcquisitionExecutionRead.from_execution(outcome.execution).workflow_run_correlation
        == correlation
    )


async def test_submission_rejects_a_cross_project_or_run_correlation(db_session) -> None:
    scope = await create_scoped_run(db_session)
    correlation = AcquisitionRunCorrelation(
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=scope["workflow"].id,
        run_id="run-from-another-scope",
    )

    with pytest.raises(
        acquisition_service.AcquisitionRunCorrelationError,
        match="workflow_run_correlation_invalid",
    ):
        await acquisition_service.submit_execution(db_session, _submission(correlation))


async def test_legacy_submission_remains_unlinked_without_json_inference(db_session) -> None:
    outcome = await acquisition_service.submit_execution(db_session, _submission(None))

    assert outcome.execution.workspace_id is None
    assert outcome.execution.project_id is None
    assert outcome.execution.workflow_id is None
    assert outcome.execution.run_id is None
    assert (
        AcquisitionExecutionRead.from_execution(outcome.execution).workflow_run_correlation
        is None
    )


def test_workflow_run_correlation_requires_all_four_typed_ids() -> None:
    with pytest.raises(ValidationError):
        AcquisitionRunCorrelation.model_validate(
            {
                "workspace_id": "workspace-1",
                "project_id": "project-1",
                "workflow_id": "workflow-1",
            }
        )
