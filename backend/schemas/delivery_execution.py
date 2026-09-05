"""Redacted Studio and controlled-receiver v2 delivery contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class DeliveryExecutionCreateV1(_Model):
    decision_id: str = Field(min_length=1, max_length=36)


class DeliveryExecutionAttemptEvidenceV1(_Model):
    attempt_number: int = Field(ge=1, le=3)
    transport: str = Field(min_length=1, max_length=32)
    http_status: int | None = Field(default=None, ge=100, le=599)
    receipt: str = Field(min_length=1, max_length=32)
    protocol: str = Field(min_length=1, max_length=32)
    outcome: Literal["accepted", "rejected", "unknown"]
    receipt_id: str | None = Field(default=None, min_length=1, max_length=128)
    receipt_hash: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    observed_at: datetime


class DeliveryExecutionReconciliationEvidenceV1(_Model):
    outcome: Literal["accepted", "rejected"]
    receipt_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime


class DeliveryExecutionReadV1(_Model):
    execution_id: str
    decision_id: str
    operation_id: str
    decision_hash: str
    payload_hash: str
    state: str
    outcome: Literal["accepted", "rejected", "unknown"] | None = None
    attempt_count: int
    attempts: list[DeliveryExecutionAttemptEvidenceV1] = Field(max_length=3)
    reconciliations: list[DeliveryExecutionReconciliationEvidenceV1] = Field(
        default_factory=list, max_length=10
    )
    created_at: datetime
    updated_at: datetime

class DeliveryExecutionListV1(_Model):
    items: list[DeliveryExecutionReadV1]
    next_cursor: str | None = None


Hash64 = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
Identifier128 = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
]
OperationId = Annotated[str, Field(min_length=1, max_length=255)]
BodyClaimId = Annotated[str, Field(min_length=1, max_length=255)]


class DeliveryClaimManifestClaimV1(_Model):
    claim_id: BodyClaimId
    content_hash: Hash64


class DeliveryClaimManifestV1(_Model):
    schema_version: Literal["delivery-claim-manifest-v1"]
    claims: list[DeliveryClaimManifestClaimV1] = Field(min_length=1, max_length=200)
    manifest_hashes: list[Hash64] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def distinct_claims_and_manifests(self) -> DeliveryClaimManifestV1:
        if len({claim.claim_id for claim in self.claims}) != len(self.claims):
            raise ValueError("Controlled receiver claims must be distinct")
        if len(set(self.manifest_hashes)) != len(self.manifest_hashes):
            raise ValueError("Controlled receiver manifests must be distinct")
        return self


class ControlledReceiverDeliveryV2(_Model):
    version: Literal["v2"] = "v2"
    receiver_identity: Identifier128
    operation_id: OperationId
    decision_hash: Hash64
    payload_hash: Hash64
    payload: DeliveryClaimManifestV1


class ControlledReceiverReceiptV2(_Model):
    version: Literal["v2"]
    receiver_identity: Identifier128
    operation_id: OperationId
    decision_hash: Hash64
    payload_hash: Hash64
    durable_status: Literal["accepted", "rejected"]
    receipt_id: Identifier128
    timestamp: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")]
    key_id: Identifier128
    signature: Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9+/=]+$")]
