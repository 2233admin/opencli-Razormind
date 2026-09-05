"""Acceptance-only, redacted ScenarioResultV1 contract for failure recovery."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA_VERSION = "ScenarioResultV1"
ACTUATOR_BY_SCENARIO = {
    "admin-crash": "proof-public-driver",
    "iii-unreachable": "proof-public-driver",
    "no-report": "proof-public-driver",
    "signed-zero": "proof-public-driver",
    "crash-after-ingest": "proof-public-driver",
    "ingest-redis-store-loss": "proof-public-driver",
    "duplicate-dlq": "proof-public-driver",
    "query-page-race": "proof-iii-actuator",
    "graph-stale-auth-cas-retract": "proof-public-driver",
    "amendment-decision-conflict": "proof-iii-actuator",
    "receiver-recovery": "proof-public-driver",
    "cancel-before-dispatch": "proof-public-driver",
    "cancel-in-flight": "proof-public-driver",
}
_ALLOWED = frozenset(
    {
        "schemaVersion",
        "scenario",
        "run",
        "fault",
        "actuator",
        "correlation",
        "collection",
        "materialization",
        "graph",
        "delivery",
        "forbiddenFacts",
        "redactionProfile",
        "timing",
        "governance",
    }
)
_SECRET = re.compile(
    r"(?:bearer|token|secret|password|credential|private|transport|proxy|gate|payload)",
    re.I,
)
_HASH = re.compile(r"^[0-9a-f]{64}$")


class FailureProofRejectedError(RuntimeError):
    """A non-authoritative, unsafe, or internally-derived certificate candidate."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _object(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise FailureProofRejectedError(f"{name} must have the exact acceptance allowlist")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise FailureProofRejectedError(f"{name} is not bounded")
    return value


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise FailureProofRejectedError(f"{name} is not a SHA-256 hash")
    return value


def _redacted(value: Any, path: str = "result") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SECRET.search(str(key)):
                raise FailureProofRejectedError(f"forbidden control or secret fact at {path}.{key}")
            _redacted(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _redacted(child, f"{path}[{index}]")
    elif isinstance(value, str) and ("-----BEGIN" in value or value.lower().startswith("bearer ")):
        raise FailureProofRejectedError(f"private material at {path}")


def validate(result: dict[str, Any], *, scenario: str | None = None) -> None:
    if not isinstance(result, dict) or set(result) != _ALLOWED:
        raise FailureProofRejectedError("result is not ScenarioResultV1")
    _redacted(result)
    if result["schemaVersion"] != SCHEMA_VERSION:
        raise FailureProofRejectedError("schema version is not accepted")
    if scenario is not None and result["scenario"] != scenario:
        raise FailureProofRejectedError("scenario correlation changed")
    scenario_name = _identifier(result["scenario"], "scenario")
    if scenario_name not in ACTUATOR_BY_SCENARIO:
        raise FailureProofRejectedError("scenario is not accepted")
    _identifier(result["run"], "run")
    _identifier(result["fault"], "fault")
    actuator = _object(result["actuator"], "actuator", {"name", "invocationHash"})
    actuator_name = _identifier(actuator["name"], "actuator.name")
    if actuator_name != ACTUATOR_BY_SCENARIO[scenario_name]:
        raise FailureProofRejectedError("scenario actuator is not authoritative")
    _hash(actuator["invocationHash"], "actuator.invocationHash")
    correlation = _object(
        result["correlation"],
        "correlation",
        {"commandId", "attemptId", "workflowRunId", "hashes"},
    )
    for name in ("commandId", "attemptId", "workflowRunId"):
        _identifier(correlation[name], f"correlation.{name}")
    if not isinstance(correlation["hashes"], dict) or not correlation["hashes"]:
        raise FailureProofRejectedError("correlated immutable hashes are required")
    for name, value in correlation["hashes"].items():
        _identifier(name, "correlation.hashes key")
        _hash(value, f"correlation.hashes.{name}")
    collection = _object(
        result["collection"],
        "collection",
        {"blockingStage", "recoveryAction", "sideEffectUncertainty"},
    )
    if collection["blockingStage"] not in {
        "bridge_unavailable",
        "callback_missing",
        "ingress_unknown",
        "duplicate",
        "cancelled",
        "none",
    }:
        raise FailureProofRejectedError("collection blocking stage is not normalized")
    if collection["recoveryAction"] not in {"retry", "resume", "recover", "none"}:
        raise FailureProofRejectedError("collection recovery action is not normalized")
    if not isinstance(collection["sideEffectUncertainty"], bool):
        raise FailureProofRejectedError("collection uncertainty is not explicit")
    materialization = _object(
        result["materialization"],
        "materialization",
        {
            "status",
            "blocker",
            "recoveryAction",
            "manifestHash",
            "reconciliationRevision",
            "pageSnapshotAsOf",
        },
    )
    if materialization["status"] not in {
        "indeterminate",
        "completed_empty",
        "rejected",
        "unknown",
        "completed",
    }:
        raise FailureProofRejectedError("materialization status is not normalized")
    if materialization["blocker"] not in {
        "none",
        "retained_dlq",
        "unknown_retention",
        "missing_report",
        "stale_manifest",
        "retract",
    }:
        raise FailureProofRejectedError("materialization blocker is not normalized")
    if materialization["recoveryAction"] not in {"recover", "none"}:
        raise FailureProofRejectedError("materialization recovery action is invalid")
    if materialization["manifestHash"] is not None:
        _hash(materialization["manifestHash"], "materialization.manifestHash")
    revision = materialization["reconciliationRevision"]
    if revision is not None and (type(revision) is not int or revision < 1):
        raise FailureProofRejectedError("materialization reconciliation revision is invalid")
    snapshot = materialization["pageSnapshotAsOf"]
    if snapshot is not None and not isinstance(snapshot, str):
        raise FailureProofRejectedError("page lineage must remain a nonterminal string")
    graph = _object(
        result["graph"],
        "graph",
        {"pin", "sequence", "readBlocker", "mutationStatus"},
    )
    if graph["pin"] is not None:
        _hash(graph["pin"], "graph.pin")
    sequence = graph["sequence"]
    if sequence is not None and (type(sequence) is not int or sequence < 0):
        raise FailureProofRejectedError("graph sequence is invalid")
    if graph["readBlocker"] not in {"none", "stale_manifest", "retract", "forbidden"}:
        raise FailureProofRejectedError("graph read blocker is invalid")
    if graph["mutationStatus"] not in {"none", "denied", "unchanged", "re_review_required"}:
        raise FailureProofRejectedError("graph mutation status is invalid")
    delivery = _object(
        result["delivery"],
        "delivery",
        {"state", "outcome", "attemptCount", "receiptHash", "reconciliation"},
    )
    if delivery["state"] not in {
        "none",
        "reserved",
        "cancelled",
        "unknown",
        "pending",
        "settled",
    }:
        raise FailureProofRejectedError("delivery state is invalid")
    if delivery["outcome"] not in {"none", "accepted", "rejected", "unknown"}:
        raise FailureProofRejectedError("delivery outcome is invalid")
    if type(delivery["attemptCount"]) is not int or delivery["attemptCount"] < 0:
        raise FailureProofRejectedError("delivery attempt count is invalid")
    if delivery["receiptHash"] is not None:
        _hash(delivery["receiptHash"], "delivery.receiptHash")
    if delivery["reconciliation"] not in {
        "none",
        "signed_accepted",
        "signed_rejected",
        "unknown",
    }:
        raise FailureProofRejectedError("delivery reconciliation is invalid")
    forbidden = _object(
        result["forbiddenFacts"],
        "forbiddenFacts",
        {"adminCreatedFallback", "lateEffectAbsenceClaim", "containerAuthority", "pageFinality"},
    )
    if any(value is not False for value in forbidden.values()):
        raise FailureProofRejectedError("certificate asserts a forbidden fact")
    if not isinstance(result["redactionProfile"], str) or not result["redactionProfile"]:
        raise FailureProofRejectedError("redaction profile is missing")
    timing = _object(result["timing"], "timing", {"startedAt", "completedAt", "deadlineSeconds"})
    if (
        not all(type(timing[key]) is int for key in timing)
        or timing["completedAt"] < timing["startedAt"]
        or not 0 < timing["deadlineSeconds"] <= 360
        or timing["completedAt"] - timing["startedAt"] > timing["deadlineSeconds"]
    ):
        raise FailureProofRejectedError("scenario timing is invalid")
    governance = _object(
        result["governance"],
        "governance",
        {"artifactId", "contentHash", "keyId", "trustRootFingerprint"},
    )
    for name in governance:
        _identifier(governance[name], f"governance.{name}")
    unsigned = {key: value for key, value in result.items() if key != "governance"}
    if governance["contentHash"] != content_hash(unsigned):
        raise FailureProofRejectedError("governance content hash does not bind the result")
    if collection["sideEffectUncertainty"] and forbidden["lateEffectAbsenceClaim"]:
        raise FailureProofRejectedError("uncertain effects cannot be certified absent")
    if delivery["attemptCount"] == 0 and delivery["state"] not in {
        "cancelled",
        "unknown",
        "none",
    }:
        raise FailureProofRejectedError(
            "zero delivery attempts have no public cancellation boundary"
        )
