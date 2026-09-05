"""Controlled receiver v2 authentication and registry security contracts."""

import json

import pytest

from backend.config import get_settings
from backend.security import controlled_receiver as receiver


@pytest.fixture(autouse=True)
def controlled_receiver_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "controlled_receiver_registry_json",
        json.dumps(
            {
                "receiver-primary": {
                    "url": "https://receiver.example/deliver",
                    "receiverIdentity": "receiver-a",
                    "credentialReference": "credential-a",
                    "requestKeyId": "request-a",
                    "receiptKeyId": "receipt-a",
                    "allowedNetworks": ["93.184.216.0/24"],
                    "durableStatus": "accepted",
                }
            }
        ),
    )
    request_secret = "request-secret-that-is-at-least-thirty-two-bytes"
    receipt_secret = "receipt-secret-that-is-at-least-thirty-two-bytes"
    monkeypatch.setattr(
        settings,
        "controlled_receiver_credentials_json",
        json.dumps({"credential-a": request_secret}),
    )
    monkeypatch.setattr(
        settings,
        "controlled_receiver_inbound_keys_json",
        json.dumps({"request-a": request_secret}),
    )
    monkeypatch.setattr(
        settings,
        "controlled_receiver_receipt_keys_json",
        json.dumps({"receipt-a": receipt_secret}),
    )
    monkeypatch.setattr(settings, "controlled_receiver_max_clock_skew_seconds", 300)


def _body():
    return receiver.canonical_json(
        {
            "version": "v2",
            "receiverIdentity": "receiver-a",
            "operationId": "op-1",
            "decisionHash": "d" * 64,
            "payloadHash": "e" * 64,
            "payload": {"schemaVersion": "delivery-claim-manifest-v1"},
        }
    )


def test_registry_requires_server_identity_and_fixed_scope():
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    assert endpoint.url == "https://receiver.example/deliver"
    with pytest.raises(receiver.ControlledReceiverSecurityError):
        receiver.resolve_endpoint("receiver-primary", "client-supplied-credential")


def test_request_mac_binds_canonical_body_and_identity():
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    body = _body()
    headers = receiver.request_headers(
        body=body,
        endpoint=endpoint,
        operation_id="op-1",
        decision_hash="d" * 64,
        payload_hash="e" * 64,
    )
    assert receiver.verify_request(
        body=body,
        headers=headers,
        receiver_identity="receiver-a",
        operation_id="op-1",
        decision_hash="d" * 64,
        payload_hash="e" * 64,
    ) == ("request-a", headers["X-Controlled-Receiver-Nonce"])
    with pytest.raises(receiver.ControlledReceiverSecurityError):
        receiver.verify_request(
            body=body + b" ",
            headers=headers,
            receiver_identity="receiver-a",
            operation_id="op-1",
            decision_hash="d" * 64,
            payload_hash="e" * 64,
        )


def test_signed_receipt_alone_controls_rejected_outcome():
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    payload = receiver.receipt_payload(
        receiver_identity="receiver-a",
        operation_id="op-1",
        decision_hash="d" * 64,
        payload_hash="e" * 64,
        durable_status="rejected",
        receipt_id="receipt-1",
        issued_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    receipt = {
        **payload,
        "keyId": "receipt-a",
        "signature": receiver.sign_receipt(payload, "receipt-a"),
    }
    assert receiver.verify_receipt(
        receipt=receipt,
        endpoint=endpoint,
        operation_id="op-1",
        decision_hash="d" * 64,
        payload_hash="e" * 64,
    ) == "rejected"
    receipt["signature"] = "tampered"
    with pytest.raises(receiver.ControlledReceiverSecurityError):
        receiver.verify_receipt(
            receipt=receipt,
            endpoint=endpoint,
            operation_id="op-1",
            decision_hash="d" * 64,
            payload_hash="e" * 64,
        )


@pytest.mark.parametrize(
    "network",
    ["127.0.0.0/24", "10.0.0.0/24", "0.0.0.0/0", "::/0", "fc00::/64"],
)
def test_registry_rejects_non_global_or_overbroad_networks(monkeypatch, network):
    settings = get_settings()
    registry = json.loads(settings.controlled_receiver_registry_json)
    registry["receiver-primary"]["allowedNetworks"] = [network]
    monkeypatch.setattr(settings, "controlled_receiver_registry_json", json.dumps(registry))
    with pytest.raises(receiver.ControlledReceiverSecurityError, match="network scope"):
        receiver.resolve_endpoint("receiver-primary", "credential-a")


def test_request_verification_binds_receiver_identity_to_its_registry_key(monkeypatch):
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    body = _body()
    headers = receiver.request_headers(
        body=body,
        endpoint=endpoint,
        operation_id="op-1",
        decision_hash="d" * 64,
        payload_hash="e" * 64,
    )
    settings = get_settings()
    registry = json.loads(settings.controlled_receiver_registry_json)
    registry["receiver-primary"]["requestKeyId"] = "other-request-key"
    monkeypatch.setattr(settings, "controlled_receiver_registry_json", json.dumps(registry))
    with pytest.raises(receiver.ControlledReceiverSecurityError):
        receiver.verify_request(
            body=body,
            headers=headers,
            receiver_identity="receiver-a",
            operation_id="op-1",
            decision_hash="d" * 64,
            payload_hash="e" * 64,
        )


def test_registry_rejects_missing_durable_status_and_weak_hmac_keys(monkeypatch):
    settings = get_settings()
    complete = json.loads(settings.controlled_receiver_registry_json)
    registry = json.loads(settings.controlled_receiver_registry_json)
    del registry["receiver-primary"]["durableStatus"]
    monkeypatch.setattr(settings, "controlled_receiver_registry_json", json.dumps(registry))
    with pytest.raises(receiver.ControlledReceiverSecurityError, match="incomplete"):
        receiver.resolve_endpoint("receiver-primary", "credential-a")
    monkeypatch.setattr(settings, "controlled_receiver_registry_json", json.dumps(complete))
    monkeypatch.setattr(
        settings,
        "controlled_receiver_credentials_json",
        json.dumps({"credential-a": "too-short"}),
    )
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    with pytest.raises(receiver.ControlledReceiverSecurityError, match="too short"):
        receiver.request_headers(
            body=_body(),
            endpoint=endpoint,
            operation_id="op-1",
            decision_hash="d" * 64,
            payload_hash="e" * 64,
        )
