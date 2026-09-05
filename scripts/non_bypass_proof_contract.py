"""Strict redacted DTO contract for the isolated non-bypass proof."""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

PINNED_III = (
    "iiidev/iii:0.19.4@sha256:14ed48b463d8a2e0d3583512acf106b3514f406c5e9965a5854710ff936e1e86"
)
ALLOWED_BUNDLE_KEYS = frozenset(
    {
        "schemaVersion",
        "run",
        "image",
        "topology",
        "command",
        "attempt",
        "lifecycleHashes",
        "reportHash",
        "ingressReceiptHash",
        "researchGraphManifestRef",
        "pin",
        "decision",
        "execution",
        "receiverReceipt",
        "redactionProfile",
    }
)
_SECRET_NAME = re.compile(r"(?:secret|token|password|credential|private|key)", re.I)
_SAFE_NAMES = frozenset(
    {"expectedRecordKeySetHash", "keyId", "nonSecretConfigHash", "excludedItemKeys"}
)
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ProofRejectedError(RuntimeError):  # noqa: N818 - public contract name
    """A non-authoritative, substituted, or unsafe proof input was rejected."""


def source_binding_hash(
    *,
    run: str,
    workflow_id: str,
    workflow_run_id: str,
    command_id: str,
    attempt_id: str,
    attempt_number: int,
    task_id: str,
    payload_hash: str,
    batch_id: str,
    manifest_hash: str,
    lifecycle_hashes: dict[str, str],
    report_hash: str,
    ingress_receipt_hash: str,
) -> str:
    """Return the canonical immutable collection/materialization binding."""

    value = {
        "run": run,
        "workflowId": workflow_id,
        "workflowRunId": workflow_run_id,
        "commandId": command_id,
        "attemptId": attempt_id,
        "attemptNumber": attempt_number,
        "taskId": task_id,
        "payloadHash": payload_hash,
        "batchId": batch_id,
        "manifestHash": manifest_hash,
        "lifecycleHashes": lifecycle_hashes,
        "reportHash": report_hash,
        "ingressReceiptHash": ingress_receipt_hash,
    }
    return sha256(_canonical_json(value).encode()).hexdigest()


def delivery_payload_hash(claims: list[dict[str, Any]], manifests: list[dict[str, Any]]) -> str:
    payload = {
        "schemaVersion": "delivery-claim-manifest-v1",
        "claims": claims,
        "manifestHashes": [manifest["manifestHash"] for manifest in manifests],
    }
    return sha256(_canonical_json(payload).encode()).hexdigest()


# Kept as a compatibility alias for the proof runner and acceptance tests.
ProofRejected = ProofRejectedError


def _object(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProofRejectedError(f"{name} does not have the exact redacted DTO shape")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProofRejectedError(f"{name} is not a non-empty identifier")
    return value


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ProofRejectedError(f"{name} is not an immutable SHA-256 hash")
    return value


def _assert_no_secrets(value: Any, path: str = "bundle") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in _SAFE_NAMES and _SECRET_NAME.search(str(key)):
                raise ProofRejectedError(f"secret-bearing field is forbidden at {path}.{key}")
            _assert_no_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str) and "-----BEGIN" in value:
        raise ProofRejectedError(f"private transport material is forbidden at {path}")


def validate_evidence(
    evidence: dict[str, Any],
    *,
    fixture_digest: str,
    run: str,
    strict_receipt: bool = False,
    receipt_keys_json: str | None = None,
) -> None:
    if not isinstance(evidence, dict) or set(evidence) != ALLOWED_BUNDLE_KEYS:
        raise ProofRejectedError("proof bundle keys are not the acceptance allowlist")
    _assert_no_secrets(evidence)
    if (
        evidence["schemaVersion"] != "NonBypassHappyVerticalProofV1"
        or evidence["run"] != run
        or evidence["image"] != PINNED_III
    ):
        raise ProofRejectedError("proof identity or pinned engine changed")
    topology = _object(
        evidence["topology"],
        "topology",
        {
            "fixtureDigest",
            "iiiCliPath",
            "iiiUrl",
            "relay",
            "containerTransport",
            "callbackRoute",
            "receiverEndpoint",
            "receiverExposure",
            "receiverKind",
        },
    )
    if _hash(topology["fixtureDigest"], "topology.fixtureDigest") != fixture_digest:
        raise ProofRejectedError("fixture digest was substituted")
    if topology["iiiCliPath"] != "/opt/iii/iii" or topology["iiiUrl"] != "ws://proof-iii:49134":
        raise ProofRejectedError("III admission facts are missing")
    if (
        topology["relay"] != "three-fixed-callback-paths"
        or topology["containerTransport"] != "docker-internal"
        or topology["callbackRoute"] != "relay-only"
        or topology["receiverEndpoint"] != "https://8.8.8.8:8000"
        or topology["receiverExposure"] != "internal-only"
        or topology["receiverKind"] != "controlled-receiver-v2"
    ):
        raise ProofRejectedError("proof topology has bypassed isolated relay or receiver transport")
    command = _object(
        evidence["command"], "command", {"id", "workflowId", "workflowRunId", "payloadHash"}
    )
    command_id = _identifier(command["id"], "command.id")
    workflow_id = _identifier(command["workflowId"], "command.workflowId")
    if _identifier(command["workflowRunId"], "command.workflowRunId") == run:
        raise ProofRejectedError("proof run was used as the workflow run")
    _hash(command["payloadHash"], "command.payloadHash")
    attempt = _object(
        evidence["attempt"], "attempt", {"id", "commandId", "attemptNumber", "taskId"}
    )
    _identifier(attempt["id"], "attempt.id")
    task_id = _identifier(attempt["taskId"], "attempt.taskId")
    if (
        attempt["commandId"] != command_id
        or not isinstance(attempt["attemptNumber"], int)
        or attempt["attemptNumber"] < 1
    ):
        raise ProofRejectedError("command/attempt correlation failed")
    lifecycle = _object(
        evidence["lifecycleHashes"],
        "lifecycleHashes",
        {"bridgeAccepted", "collectorStarted", "collectorReturned"},
    )
    for name, value in lifecycle.items():
        _hash(value, f"lifecycleHashes.{name}")
    report_hash = _hash(evidence["reportHash"], "reportHash")
    ingress_hash = _hash(evidence["ingressReceiptHash"], "ingressReceiptHash")
    if len({*lifecycle.values(), report_hash, ingress_hash}) != 5:
        raise ProofRejectedError("vertical evidence hashes are not distinct immutable facts")
    manifest = _object(
        evidence["researchGraphManifestRef"],
        "researchGraphManifestRef",
        {
            "batchId",
            "derivation",
            "reconciliationRevision",
            "manifestSchemaVersion",
            "manifestHash",
            "expectedRecordKeySetHash",
            "recordRefSetHash",
            "materializationStatus",
            "materializationAuthority",
            "sourceCorrelation",
        },
    )
    if (
        _identifier(manifest["batchId"], "manifest.batchId") is None
        or manifest["derivation"] != "dispatch-task-v1"
        or manifest["manifestSchemaVersion"] != "v1"
        or manifest["materializationStatus"] != "completed"
        or manifest["materializationAuthority"] != "scoped-admin-api"
        or not isinstance(manifest["reconciliationRevision"], int)
        or manifest["reconciliationRevision"] < 1
    ):
        raise ProofRejectedError(
            "materialized manifest is not a completed authoritative scoped reference"
        )
    for name in ("manifestHash", "expectedRecordKeySetHash", "recordRefSetHash"):
        _hash(manifest[name], f"manifest.{name}")
    expected_batch_id = str(
        uuid5(
            NAMESPACE_URL,
            f"opencli-admin/workflow/{workflow_id}/run/{command['workflowRunId']}/batch/{task_id}",
        )
    )
    if manifest["batchId"] != expected_batch_id:
        raise ProofRejected("materialized manifest batch is not bound to command and attempt")
    source = _object(
        manifest["sourceCorrelation"],
        "researchGraphManifestRef.sourceCorrelation",
        {
            "workflowRunId",
            "commandId",
            "attemptId",
            "payloadHash",
            "batchId",
            "manifestHash",
            "reportHash",
            "ingressReceiptHash",
            "lifecycleHashes",
        },
    )
    if (
        source["workflowRunId"] != command["workflowRunId"]
        or source["commandId"] != command_id
        or source["attemptId"] != attempt["id"]
        or source["payloadHash"] != command["payloadHash"]
        or source["batchId"] != manifest["batchId"]
        or source["manifestHash"] != manifest["manifestHash"]
        or source["reportHash"] != evidence["reportHash"]
        or source["ingressReceiptHash"] != evidence["ingressReceiptHash"]
    ):
        raise ProofRejected(
            "source correlation does not match collection and materialization facts"
        )
    _hash(source["payloadHash"], "sourceCorrelation.payloadHash")
    _hash(source["reportHash"], "sourceCorrelation.reportHash")
    _hash(source["ingressReceiptHash"], "sourceCorrelation.ingressReceiptHash")
    source_lifecycle = _object(
        source["lifecycleHashes"],
        "sourceCorrelation.lifecycleHashes",
        {"bridgeAccepted", "collectorStarted", "collectorReturned"},
    )
    if source_lifecycle != lifecycle:
        raise ProofRejected("source correlation lifecycle facts were substituted")
    expected_operation_id = "delivery-{}-{}".format(
        run,
        source_binding_hash(
            run=run,
            workflow_id=workflow_id,
            workflow_run_id=command["workflowRunId"],
            command_id=command_id,
            attempt_id=attempt["id"],
            attempt_number=attempt["attemptNumber"],
            task_id=task_id,
            payload_hash=command["payloadHash"],
            batch_id=manifest["batchId"],
            manifest_hash=manifest["manifestHash"],
            lifecycle_hashes=lifecycle,
            report_hash=evidence["reportHash"],
            ingress_receipt_hash=evidence["ingressReceiptHash"],
        ),
    )
    pin = _object(evidence["pin"], "pin", {"sequence", "researchRevisionId", "manifestSetHash"})
    if not isinstance(pin["sequence"], int) or pin["sequence"] < 1:
        raise ProofRejectedError("pin.sequence is invalid")
    _identifier(pin["researchRevisionId"], "pin.researchRevisionId")
    pin_hash = _hash(pin["manifestSetHash"], "pin.manifestSetHash")
    decision = _object(
        evidence["decision"],
        "decision",
        {
            "operationId",
            "decisionId",
            "decisionHash",
            "payloadHash",
            "manifestSetHash",
            "claims",
            "manifests",
        },
    )
    operation_id, decision_id = (
        _identifier(decision["operationId"], "decision.operationId"),
        _identifier(decision["decisionId"], "decision.decisionId"),
    )
    if operation_id != expected_operation_id:
        raise ProofRejectedError("delivery operation is not bound to the proof run")
    decision_hash, decision_payload = (
        _hash(decision["decisionHash"], "decision.decisionHash"),
        _hash(decision["payloadHash"], "decision.payloadHash"),
    )
    if _hash(decision["manifestSetHash"], "decision.manifestSetHash") != pin_hash:
        raise ProofRejectedError("frozen decision is not bound to the pinned graph")
    claims = decision["claims"]
    if not isinstance(claims, list) or not claims:
        raise ProofRejectedError("delivery claims are absent")
    for item in claims:
        claim = _object(item, "decision.claims[]", {"claimId", "contentHash"})
        _identifier(claim["claimId"], "decision.claims[].claimId")
        _hash(claim["contentHash"], "decision.claims[].contentHash")
    if len({claim["claimId"] for claim in claims}) != len(claims):
        raise ProofRejectedError("delivery claims are not distinct")
    if not isinstance(decision["manifests"], list) or not decision["manifests"]:
        raise ProofRejectedError("decision manifests are absent")
    decision_manifests = []
    for item in decision["manifests"]:
        selected = _object(
            item,
            "decision.manifests[]",
            {
                "batchId",
                "derivation",
                "reconciliationRevision",
                "manifestSchemaVersion",
                "manifestHash",
                "expectedRecordKeySetHash",
                "recordRefSetHash",
                "materializationStatus",
            },
        )
        _identifier(selected["batchId"], "decision.manifests[].batchId")
        _hash(selected["manifestHash"], "decision.manifests[].manifestHash")
        _hash(selected["expectedRecordKeySetHash"], "decision.manifests[].expectedRecordKeySetHash")
        _hash(selected["recordRefSetHash"], "decision.manifests[].recordRefSetHash")
        decision_manifests.append(selected)
    manifest_hashes = {item["manifestHash"] for item in decision_manifests}
    if manifest["manifestHash"] not in manifest_hashes:
        raise ProofRejectedError("decision does not retain the materialized manifest")
    matching_manifest = next(
        item for item in decision_manifests if item["manifestHash"] == manifest["manifestHash"]
    )
    for name in (
        "batchId",
        "derivation",
        "reconciliationRevision",
        "manifestSchemaVersion",
        "manifestHash",
        "expectedRecordKeySetHash",
        "recordRefSetHash",
        "materializationStatus",
    ):
        if matching_manifest[name] != manifest[name]:
            raise ProofRejectedError("decision manifest does not match materialized manifest")
    if decision_payload != delivery_payload_hash(claims, decision_manifests):
        raise ProofRejectedError("delivery payload hash does not match frozen claims and manifests")
    execution = _object(
        evidence["execution"],
        "execution",
        {
            "executionId",
            "operationId",
            "decisionId",
            "decisionHash",
            "payloadHash",
            "outcome",
            "attemptCount",
        },
    )
    if (
        _identifier(execution["executionId"], "execution.executionId") is None
        or execution["operationId"] != operation_id
        or execution["decisionId"] != decision_id
        or _hash(execution["decisionHash"], "execution.decisionHash") != decision_hash
        or _hash(execution["payloadHash"], "execution.payloadHash") != decision_payload
        or execution["outcome"] != "accepted"
        or not isinstance(execution["attemptCount"], int)
        or execution["attemptCount"] < 1
    ):
        raise ProofRejectedError("delivery execution is not terminally accepted and frozen")
    receipt = _object(
        evidence["receiverReceipt"],
        "receiverReceipt",
        {
            "attemptNumber",
            "receiptId",
            "receiptHash",
            "httpStatus",
            "receipt",
            "durableReceipt",
            "outcome",
            "receiverIdentity",
            "signedReceipt",
            "receiptPreimage",
        },
    )
    if (
        not isinstance(receipt["attemptNumber"], int)
        or receipt["attemptNumber"] != execution["attemptCount"]
        or receipt["attemptNumber"] < 1
        or not _identifier(receipt["receiptId"], "receiverReceipt.receiptId")
        or receipt["httpStatus"] != 200
        or receipt["receipt"] != "verified"
        or receipt["durableReceipt"] != "verified"
        or receipt["outcome"] != "accepted"
    ):
        raise ProofRejectedError("accepted delivery lacks its matching verified durable receipt")
    signed_receipt = _object(
        receipt["signedReceipt"],
        "receiverReceipt.signedReceipt",
        {
            "version",
            "receiverIdentity",
            "operationId",
            "decisionHash",
            "payloadHash",
            "durableStatus",
            "receiptId",
            "timestamp",
            "keyId",
            "signature",
        },
    )
    if (
        signed_receipt["version"] != "v2"
        or receipt["receiverIdentity"] != "controlled-receiver-proof"
        or signed_receipt["receiverIdentity"] != receipt["receiverIdentity"]
        or signed_receipt["operationId"] != operation_id
        or signed_receipt["decisionHash"] != decision_hash
        or signed_receipt["payloadHash"] != decision_payload
        or signed_receipt["durableStatus"] != "accepted"
        or signed_receipt["receiptId"] != receipt["receiptId"]
        or signed_receipt["keyId"] != "receipt-key-proof"
        or not isinstance(signed_receipt["timestamp"], str)
        or not signed_receipt["timestamp"].isdigit()
        or not isinstance(signed_receipt["signature"], str)
        or not signed_receipt["signature"]
        or len(signed_receipt["signature"]) > 512
    ):
        raise ProofRejectedError("signed durable receipt is not bound to frozen delivery facts")
    try:
        base64.b64decode(signed_receipt["signature"], validate=True)
    except (ValueError, TypeError):
        raise ProofRejectedError("signed durable receipt signature encoding is invalid")
    receipt_fields = {
        key: signed_receipt[key]
        for key in (
            "version",
            "receiverIdentity",
            "operationId",
            "decisionHash",
            "payloadHash",
            "durableStatus",
            "receiptId",
            "timestamp",
        )
    }
    if receipt["receiptPreimage"] != _canonical_json(receipt_fields):
        raise ProofRejectedError("receipt canonical preimage was substituted")
    if (
        _hash(receipt["receiptHash"], "receiverReceipt.receiptHash")
        != sha256(_canonical_json(signed_receipt).encode()).hexdigest()
    ):
        raise ProofRejectedError("receipt hash does not match the signed receiver receipt")
    if strict_receipt:
        keys_json = receipt_keys_json or os.environ.get("CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON")
        if not keys_json:
            raise ProofRejectedError("strict receipt validation requires the receipt key configuration")
        try:
            keys = json.loads(keys_json)
        except json.JSONDecodeError as exc:
            raise ProofRejectedError(
                "strict receipt validation has invalid receipt key configuration"
            ) from exc
        if not isinstance(keys, dict) or not isinstance(keys.get(signed_receipt["keyId"]), str):
            raise ProofRejectedError("strict receipt validation could not resolve the receipt key")
        key = keys[signed_receipt["keyId"]].encode()
        if len(key) < 32:
            raise ProofRejectedError("strict receipt validation has an undersized receipt key")
        expected_signature = base64.b64encode(
            hmac.new(key, receipt["receiptPreimage"].encode(), sha256).digest()
        ).decode()
        if not hmac.compare_digest(expected_signature, signed_receipt["signature"]):
            raise ProofRejectedError("signed durable receipt HMAC verification failed")
    if not isinstance(evidence["redactionProfile"], str) or not evidence["redactionProfile"]:
        raise ProofRejectedError("redaction profile is absent")


def assert_substitutions_rejected(
    evidence: dict[str, Any], *, fixture_digest: str, run: str
) -> None:
    """The five provenance substitutes must fail before signing."""
    substitutions = (
        (
            "mock_transport",
            lambda proof: proof["topology"].__setitem__("containerTransport", "mock"),
        ),
        (
            "public_webhook_receiver",
            lambda proof: proof["topology"].update(
                receiverEndpoint="https://public-webhook.invalid", receiverExposure="public"
            ),
        ),
        (
            "projection_only_manifest_source",
            lambda proof: proof["researchGraphManifestRef"].__setitem__(
                "materializationAuthority", "projection-only"
            ),
        ),
        (
            "direct_admin_fallback",
            lambda proof: proof["topology"].__setitem__("callbackRoute", "direct-admin"),
        ),
        (
            "bare_http_2xx",
            lambda proof: proof["receiverReceipt"].__setitem__("durableReceipt", "missing"),
        ),
    )
    for name, mutate in substitutions:
        candidate = json.loads(json.dumps(evidence))
        mutate(candidate)
        try:
            validate_evidence(candidate, fixture_digest=fixture_digest, run=run)
        except ProofRejectedError:
            continue
        raise AssertionError(f"unsafe {name} substitution reached signing")
