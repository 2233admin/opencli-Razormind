"""Narrow, redacted contracts for frozen delivery authorization decisions."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from backend.schemas.research_graph_v2 import ResearchGraphV2PinnedReference


class _V1Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")



class DeliveryAuthorizingActor(Protocol):
    """Authorization evidence consumed by the delivery boundary."""

    actor_type: str
    actor_id: str
    principal: str
    capability: str
    policy_version: str



_CONTROLLED_IDENTIFIER_PATTERNS = {
    "receiver_identity": re.compile(r"^controlled-receiver-[a-z0-9-]+$"),
    "endpoint_identity": re.compile(r"^receiver-channel-[a-z0-9-]+$"),
    "credential_reference": re.compile(r"^credential-reference-[a-z0-9-]+$"),
}


def _validate_controlled_identifier(field: str, value: str) -> str:
    if _CONTROLLED_IDENTIFIER_PATTERNS[field].fullmatch(value) is None:
        raise ValueError("Only opaque controlled-receiver references are accepted")
    return value


class DeliveryTargetConfigureV1(_V1Model):
    """Configuration inputs are persisted only as a protected target revision."""

    target_id: str | None = Field(default=None, min_length=1, max_length=36)
    receiver_identity: str = Field(min_length=1, max_length=255)
    endpoint_identity: str = Field(min_length=1, max_length=255)
    credential_reference: str = Field(min_length=1, max_length=255)

    @field_validator("receiver_identity")
    @classmethod
    def validate_receiver_identity(cls, value: str) -> str:
        return _validate_controlled_identifier("receiver_identity", value)

    @field_validator("endpoint_identity")
    @classmethod
    def validate_endpoint_identity(cls, value: str) -> str:
        return _validate_controlled_identifier("endpoint_identity", value)

    @field_validator("credential_reference")
    @classmethod
    def validate_credential_reference(cls, value: str) -> str:
        return _validate_controlled_identifier("credential_reference", value)


class DeliveryTargetReadV1(_V1Model):
    """Deliberately omits endpoint and credential material."""

    target_id: str
    receiver_identity: str
    target_kind: Literal["controlled-receiver-v1"]
    revision: int
    configured_at: datetime


class DeliveryTargetListV1(_V1Model):
    items: list[DeliveryTargetReadV1]
    next_cursor: str | None = None


class DeliveryAuthorizationCreateV1(_V1Model):
    version: Literal["v1"] = "v1"
    operation_id: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    node_id: str = Field(min_length=1, max_length=255)
    target_id: str = Field(min_length=1, max_length=36)
    pinned_reference: ResearchGraphV2PinnedReference
    selected_claim_ids: list[str] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_distinct_claims(self) -> DeliveryAuthorizationCreateV1:
        if len(self.selected_claim_ids) != len(set(self.selected_claim_ids)):
            raise ValueError("Selected claim IDs must be distinct")
        return self


class DeliveryClaimReadV1(_V1Model):
    claim_id: str
    content_hash: str


class DeliveryManifestReadV1(_V1Model):
    batch_id: str
    derivation: Literal["dispatch-task-v1"]
    reconciliation_revision: int
    manifest_schema_version: Literal["v1"]
    manifest_hash: str
    expected_record_key_set_hash: str
    record_ref_set_hash: str
    materialization_status: Literal["completed", "partial"]


class DeliveryAuthorizationReadV1(_V1Model):
    """Studio-safe summary; no destination config, policy body, payload, ODP, or secrets."""

    decision_id: str
    version: Literal["v1"]
    operation_id: str
    target_id: str
    target_revision: int
    non_secret_config_hash: str
    policy_version: str
    policy_hash: str
    pin_sequence: int
    research_revision_id: str
    manifest_set_hash: str
    claims: list[DeliveryClaimReadV1]
    manifests: list[DeliveryManifestReadV1]
    payload_schema_version: str
    payload_hash: str
    redaction_profile_version: str
    approver_actor_id: str
    approval_policy_version: str
    decision_hash: str
    decisioned_at: datetime


class DeliveryAuthorizationListV1(_V1Model):
    items: list[DeliveryAuthorizationReadV1]
    next_cursor: str | None = None
