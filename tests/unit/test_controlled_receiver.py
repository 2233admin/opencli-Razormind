"""Durable controlled receiver table invariants."""

import pytest
from pydantic import ValidationError

from backend.models.delivery_execution import (
    ControlledReceiverDelivery,
    ControlledReceiverNonce,
    DeliveryExecutionResult,
    _reject_immutable_mutation,
)
from backend.schemas.delivery_authorization import DeliveryAuthorizationCreateV1
from backend.schemas.delivery_execution import ControlledReceiverDeliveryV2


def test_receiver_durability_keys_are_database_constraints():
    delivery_constraints = {
        constraint.name for constraint in ControlledReceiverDelivery.__table__.constraints
    }
    nonce_constraints = {
        constraint.name for constraint in ControlledReceiverNonce.__table__.constraints
    }
    result_constraints = {
        constraint.name for constraint in DeliveryExecutionResult.__table__.constraints
    }
    assert "uq_controlled_receiver_delivery" in delivery_constraints
    assert "uq_controlled_receiver_nonce" in nonce_constraints
    assert "uq_delivery_execution_result_attempt" in result_constraints


def test_receiver_evidence_rejects_mutation():
    with pytest.raises(ValueError, match="append-only"):
        _reject_immutable_mutation()


def test_receiver_payload_is_exact_bounded_claim_manifest():
    payload = {
        "version": "v2",
        "receiverIdentity": "receiver-a",
        "operationId": "op-1",
        "decisionHash": "d" * 64,
        "payloadHash": "e" * 64,
        "payload": {
            "schemaVersion": "delivery-claim-manifest-v1",
            "claims": [{"claimId": "claim-1", "contentHash": "a" * 64}],
            "manifestHashes": ["b" * 64],
        },
    }
    assert (
        ControlledReceiverDeliveryV2.model_validate(payload).payload.claims[0].claim_id
        == "claim-1"
    )
    for mutation in (
        {"extra": "forbidden"},
        {"payload": {**payload["payload"], "claims": []}},
        {"payload": {**payload["payload"], "claims": payload["payload"]["claims"] * 2}},
        {"payload": {**payload["payload"], "manifestHashes": ["not-a-hash"]}},
    ):
        candidate = {**payload, **mutation}
        with pytest.raises(ValidationError):
            ControlledReceiverDeliveryV2.model_validate(candidate)


def test_receiver_and_authorization_share_255_character_header_safe_operation_contract():
    operation_id = "delivery/" + "x" * 246
    payload = {
        "version": "v2", "receiverIdentity": "receiver-a", "operationId": operation_id,
        "decisionHash": "d" * 64, "payloadHash": "e" * 64,
        "payload": {
            "schemaVersion": "delivery-claim-manifest-v1",
            "claims": [{"claimId": "claim/" + "x" * 249, "contentHash": "a" * 64}],
            "manifestHashes": ["b" * 64],
        },
    }
    assert ControlledReceiverDeliveryV2.model_validate(payload).operation_id == operation_id
    assert DeliveryAuthorizationCreateV1.model_validate({
        "operationId": operation_id, "idempotencyKey": "key", "nodeId": "node", "targetId": "t",
        "pinnedReference": {"sequence": 1, "researchRevisionId": "r", "manifestSetHash": "b" * 64},
        "selectedClaimIds": ["claim/" + "x" * 249],
    }).operation_id == operation_id
