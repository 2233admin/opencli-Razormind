from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.acquisition import AcquisitionExecution, AcquisitionExecutionStatus
from backend.schemas.common import UTCModel


class CapabilityIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=50)


class AcquisitionRunCorrelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)


class AcquisitionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    capability: CapabilityIdentity
    output_schema_version: str = Field(min_length=1, max_length=50)
    input: dict[str, Any]
    environment: dict[str, Any] = Field(default_factory=dict)
    required_artifacts: list[Literal["trace"]] = Field(default_factory=list)
    geo_refs: dict[str, str] = Field(default_factory=dict)
    workflow_run_correlation: AcquisitionRunCorrelation | None = None


class CapabilityDescriptor(BaseModel):
    capability_id: str
    capability_version: str
    output_schema_version: str
    ready: bool
    runtime: dict[str, str] = Field(default_factory=dict)
    unavailable_reason: str | None = None


class CapabilityList(BaseModel):
    capabilities: list[CapabilityDescriptor]


class AcquisitionExecutionRead(UTCModel):
    execution_id: str
    request_id: str
    capability_id: str
    capability_version: str
    output_schema_version: str
    status: AcquisitionExecutionStatus
    result: dict[str, Any] | None
    failure: dict[str, Any] | None
    trace_ref: str | None
    artifact_refs: list[Any]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    workflow_run_correlation: AcquisitionRunCorrelation | None

    @classmethod
    def from_execution(cls, execution: AcquisitionExecution) -> "AcquisitionExecutionRead":
        correlation_values = (
            execution.workspace_id,
            execution.project_id,
            execution.workflow_id,
            execution.run_id,
        )
        correlation = None
        if all(value is not None for value in correlation_values):
            workspace_id, project_id, workflow_id, run_id = correlation_values
            assert workspace_id is not None
            assert project_id is not None
            assert workflow_id is not None
            assert run_id is not None
            correlation = AcquisitionRunCorrelation(
                workspace_id=workspace_id,
                project_id=project_id,
                workflow_id=workflow_id,
                run_id=run_id,
            )
        return cls(
            execution_id=execution.id,
            request_id=execution.request_id,
            capability_id=execution.capability_id,
            capability_version=execution.capability_version,
            output_schema_version=execution.output_schema_version,
            status=execution.status,
            result=execution.result_payload,
            failure=execution.failure,
            trace_ref=execution.trace_ref,
            artifact_refs=execution.artifact_refs,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            workflow_run_correlation=correlation,
        )
