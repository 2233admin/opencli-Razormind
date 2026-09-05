#!/usr/bin/env python3
"""Drive the isolated proof only through Admin's public scoped HTTP APIs."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import time
from typing import Any

import httpx

from backend.database import AsyncSessionLocal
from backend.models.studio import StudioWorkspace
from backend.schemas.delivery_execution import ControlledReceiverDeliveryV2
from backend.security.controlled_receiver import (
    ControlledReceiverSecurityError,
    canonical_json,
    pinned_post,
    request_headers,
    resolve_receiver_identity,
)

BASE = "http://proof-admin:8000/api/v1"


def data(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        raise RuntimeError(f"{response.status_code} {response.request.url}: {response.text[:600]}")
    value = response.json()
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise RuntimeError(f"Admin response has no data object: {response.text[:300]}")
    return value["data"]


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
    """Mirror the proof contract's canonical collection/materialization binding."""

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
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def post(
    client: httpx.Client,
    path: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return data(client.post(f"{BASE}{path}", json=body, headers=headers))


def get(
    client: httpx.Client,
    path: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return data(client.get(f"{BASE}{path}", headers=headers))


def receipt_status(body_base64: str) -> int:
    """Re-submit the exact v2 body to the receiver's authenticated status API."""

    try:
        body = base64.b64decode(body_base64, validate=True)
        value = ControlledReceiverDeliveryV2.model_validate_json(body)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("receipt-status received an invalid canonical delivery body") from exc
    if body != canonical_json(value.model_dump(by_alias=True)):
        raise RuntimeError("receipt-status delivery body is not canonical")
    try:
        endpoint = resolve_receiver_identity(value.receiver_identity)
        headers = request_headers(
            body=body,
            endpoint=endpoint,
            operation_id=value.operation_id,
            decision_hash=value.decision_hash,
            payload_hash=value.payload_hash,
        )
        response = asyncio.run(
            pinned_post(
                endpoint,
                body,
                headers,
                timeout_seconds=60,
                status_query=True,
            )
        )
    except ControlledReceiverSecurityError as exc:
        raise RuntimeError(f"receipt-status controlled receiver request failed: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(
            f"receipt-status returned HTTP {response.status_code}: {response.text[:600]}"
        )
    try:
        receipt = response.json()["receipt"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("receipt-status response did not contain a signed receipt") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("receipt-status response receipt is not an object")
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def wait_for_materialization(
    client: httpx.Client,
    route: str,
    command_id: str,
    attempt_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    last: dict[str, Any] | None = None
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.post(
            f"{BASE}{route}/{command_id}/materialize",
            headers=headers,
        )
        if response.status_code == 200:
            last = data(response)
            last_status = get(client, f"{route}/{command_id}", headers)
            if last.get("materializationStatus") == "completed" and last.get(
                "researchGraphManifestRef"
            ):
                return last
        time.sleep(1)
    raise RuntimeError(
        "materialization stage timed out: "
        + json.dumps(
            {
                "commandId": command_id,
                "attemptId": attempt_id,
                "verticalStatus": last_status,
                "materialization": last,
            },
            sort_keys=True,
        )
    )


def graph() -> dict[str, Any]:
    return {
        "id": "proof-workflow",
        "name": "Non-bypass vertical proof",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "opencli-source",
                "kind": "source",
                "capability": "fetch",
                "adapter": "proof-opencli",
                "params": {"limit": 1},
                "sourceAnchor": {
                    "kind": "url",
                    "label": "Bilibili",
                    "href": "https://www.bilibili.com/",
                },
            },
            {
                "id": "normalize-proof",
                "kind": "agent",
                "capability": "normalize",
                "params": {"language": "en"},
            },
            {
                "id": "proof-inbox",
                "kind": "inbox",
                "capability": "store",
                "params": {"queue": "proof"},
            },
        ],
        "edges": [
            {
                "id": "proof-source-normalize",
                "source": "opencli-source",
                "target": "normalize-proof",
                "sourcePort": "records",
                "targetPort": "records",
            },
            {
                "id": "proof-normalize-inbox",
                "source": "normalize-proof",
                "target": "proof-inbox",
                "sourcePort": "records",
                "targetPort": "records",
            },
        ],
        "adapters": [
            {
                "id": "proof-opencli",
                "type": "source",
                "provider": "bilibili",
                "mode": "live",
                "config": {"command": "search"},
            }
        ],
        "agentPermissions": {
            "canFetchNetwork": True,
            "canSendNotifications": False,
            "canWriteInbox": True,
            "allowedDomains": ["bilibili.com"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run")
    parser.add_argument("mode", nargs="?", choices=("receipt-status",))
    parser.add_argument("--body-base64")
    args = parser.parse_args()
    if args.mode == "receipt-status":
        if not args.body_base64:
            parser.error("receipt-status requires --body-base64")
        return receipt_status(args.body_base64)
    if not args.run:
        parser.error("--run is required for the proof driver")
    fleet = {"X-API-Token": os.environ["API_AUTH_TOKEN"]}
    bootstrap = {**fleet, "Authorization": f"Bearer {os.environ['BOOTSTRAP_ADMIN_TOKEN']}"}
    proposer = {**fleet, "Authorization": f"Bearer {os.environ['PROOF_PROPOSER_JWT']}"}
    reviewer = {**fleet, "Authorization": f"Bearer {os.environ['PROOF_REVIEWER_JWT']}"}
    with httpx.Client(timeout=60) as client:
        workspace = post(
            client,
            "/platform/workspaces",
            {
                "name": "Non-bypass proof",
                "slug": args.run,
                "first_admin_subject": "bootstrap-admin",
                "first_admin_email": "bootstrap@proof.invalid",
                "first_admin_display_name": "Proof bootstrap",
            },
            bootstrap,
        )
        workspace_id = workspace["id"]
        for subject, role in (("proof-proposer", "operator"), ("proof-reviewer", "maintainer")):
            post(
                client,
                f"/workspaces/{workspace_id}/members",
                {
                    "subject": subject,
                    "email": f"{subject}@proof.invalid",
                    "display_name": subject,
                    "role": role,
                },
                bootstrap,
            )
        bootstrap_result = post(
            client,
            f"/workspaces/{workspace_id}/projects/bootstrap",
            {
                "project": {"name": "Non-bypass proof", "slug": args.run},
                "workflow": {"name": "Non-bypass proof", "graph": graph()},
            },
            bootstrap,
        )
        project = bootstrap_result["project"]
        workflow = bootstrap_result["primary_workflow"]
        workflow_id = workflow["id"]
        route = (
            f"/workspaces/{workspace_id}/projects/{project['id']}/workflows/{workflow['id']}/runs"
        )
        validation = post(
            client,
            route.rsplit("/runs", 1)[0] + "/draft/validation-runs",
            {},
            proposer,
        )
        if not validation.get("valid"):
            raise RuntimeError(
                f"workflow validation failed: {json.dumps(validation, sort_keys=True)}"
            )
        post(
            client,
            route.rsplit("/runs", 1)[0] + "/versions",
            {
                "reason": "isolated proof",
                "expectedRevision": 1,
                "validationRunId": validation["runId"],
            },
            proposer,
        )
        run = post(
            client,
            route,
            {
                "inputs": {},
                "responseMode": "async",
                "user": "proof-proposer",
                "requestId": args.run,
                "idempotencyKey": args.run,
            },
            proposer,
        )
        run_id = run["runId"]
        collection_route = f"{route}/{run_id}/iii-collections"
        submission = post(
            client,
            collection_route,
            {
                "version": "v1",
                "idempotencyKey": args.run,
                "nodeId": "opencli-source",
                "collection": {
                    "site": "bilibili",
                    "command": "search",
                    "args": {"keyword": "vertical-proof"},
                    "sourceBindingId": "proof-binding",
                    "sourceBindingRevisionId": "proof-binding-v1",
                    "sourceBindingRevisionNumber": 1,
                },
            },
            proposer,
        )
        command_id, attempt_id = submission["commandId"], submission["attemptId"]
        materialization = wait_for_materialization(
            client,
            collection_route,
            command_id,
            attempt_id,
            proposer,
        )
        manifest_ref = materialization["researchGraphManifestRef"]
        claim_id = f"vertical-proof-{args.run}"
        claim_hash = manifest_ref["manifestHash"]
        if manifest_ref.get("materializationStatus") != "completed":
            raise RuntimeError("only completed materialization can enter ResearchGraph")
        # The graph receives this exact scoped manifest reference. The proof
        # bundle below instead emits a deliberately narrower redacted DTO.
        graph_route = f"{route}/{run_id}/research-graph-v2"
        state = get(client, graph_route, proposer)
        proposed = post(
            client,
            graph_route + "/mutations",
            {
                "idempotencyKey": f"{args.run}-propose",
                "action": "propose",
                "expectedSequence": state["sequence"],
                "expectedRevision": state["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            proposer,
        )
        verified = post(
            client,
            graph_route + "/mutations",
            {
                "idempotencyKey": f"{args.run}-verify",
                "action": "verify",
                "expectedSequence": proposed["sequence"],
                "expectedRevision": proposed["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            reviewer,
        )
        pinned = post(
            client,
            graph_route + "/mutations",
            {
                "idempotencyKey": f"{args.run}-pin",
                "action": "pin",
                "expectedSequence": verified["sequence"],
                "expectedRevision": verified["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest_ref],
            },
            reviewer,
        )
        pinned_fold = pinned["pinnedFold"]
        vertical_status = get(client, f"{collection_route}/{command_id}", proposer)
        if vertical_status["commandId"] != command_id or vertical_status["attemptId"] != attempt_id:
            raise RuntimeError("scoped status command/attempt correlation failed")
        lifecycle = {
            item["eventType"]: item["hash"]
            for item in vertical_status["evidenceReferences"]
            if item["kind"] == "lifecycle"
        }
        expected_lifecycle = {"bridge_accepted", "collector_started", "collector_returned"}
        if set(lifecycle) != expected_lifecycle:
            raise RuntimeError(f"lifecycle facts are incomplete: {vertical_status}")
        report_hash = next(
            item["hash"]
            for item in vertical_status["evidenceReferences"]
            if item["kind"] == "expected_key_report"
        )
        ingress_receipt_hash = next(
            item["hash"]
            for item in vertical_status["evidenceReferences"]
            if item["kind"] == "ingress_receipt"
        )
        operation_binding = source_binding_hash(
            run=args.run,
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            command_id=command_id,
            attempt_id=attempt_id,
            attempt_number=submission["attemptNumber"],
            task_id=submission["taskId"],
            payload_hash=submission["payloadSha256"],
            batch_id=manifest_ref["batchId"],
            manifest_hash=manifest_ref["manifestHash"],
            lifecycle_hashes={
                "bridgeAccepted": lifecycle["bridge_accepted"],
                "collectorStarted": lifecycle["collector_started"],
                "collectorReturned": lifecycle["collector_returned"],
            },
            report_hash=report_hash,
            ingress_receipt_hash=ingress_receipt_hash,
        )
        target = post(
            client,
            f"{route}/{run_id}/delivery-targets",
            {
                "receiverIdentity": "controlled-receiver-proof",
                "endpointIdentity": "receiver-channel-proof",
                "credentialReference": "credential-reference-proof",
            },
            reviewer,
        )
        operation_id = f"delivery-{args.run}-{operation_binding}"
        decision = post(
            client,
            f"{route}/{run_id}/delivery-authorizations",
            {
                "version": "v1",
                "operationId": operation_id,
                "idempotencyKey": f"{args.run}-delivery",
                "nodeId": "opencli-source",
                "targetId": target["targetId"],
                "pinnedReference": {
                    "sequence": pinned_fold["sequence"],
                    "researchRevisionId": pinned_fold["researchRevisionId"],
                    "manifestSetHash": pinned_fold["manifestSetHash"],
                },
                "selectedClaimIds": [claim_id],
            },
            reviewer,
        )
        execution = post(
            client,
            f"{route}/{run_id}/delivery-executions",
            {"decisionId": decision["decisionId"]},
            reviewer,
        )
        if execution.get("outcome") != "accepted" or not execution.get("attempts"):
            raise RuntimeError(f"delivery execution did not reach accepted: {execution}")
        final_attempt = execution["attempts"][-1]
        if (
            final_attempt.get("receipt") != "verified"
            or final_attempt.get("outcome") != "accepted"
            or not final_attempt.get("receiptId")
            or not final_attempt.get("receiptHash")
        ):
            raise RuntimeError(f"delivery receipt was not durably verified: {execution}")
        print(
            json.dumps(
                {
                    "schemaVersion": "NonBypassHappyVerticalProofV1",
                    "run": args.run,
                    "image": (
                        "iiidev/iii:0.19.4@sha256:14ed48b463d8a2e0d3583512acf106b3514f406c5e9965a5854710ff936e1e86"
                    ),
                    "topology": {
                        "fixtureDigest": os.environ["PROOF_FIXTURE_DIGEST"],
                        "iiiCliPath": "/opt/iii/iii",
                        "iiiUrl": "ws://proof-iii:49134",
                        "relay": "three-fixed-callback-paths",
                        "containerTransport": "docker-internal",
                        "callbackRoute": "relay-only",
                        "receiverEndpoint": "https://8.8.8.8:8000",
                        "receiverExposure": "internal-only",
                        "receiverKind": "controlled-receiver-v2",
                    },
                    "command": {
                        "id": command_id,
                        "workflowId": workflow_id,
                        "workflowRunId": run_id,
                        "payloadHash": submission["payloadSha256"],
                    },
                    "attempt": {
                        "id": attempt_id,
                        "commandId": command_id,
                        "attemptNumber": submission["attemptNumber"],
                        "taskId": submission["taskId"],
                    },
                    "lifecycleHashes": {
                        "bridgeAccepted": lifecycle["bridge_accepted"],
                        "collectorStarted": lifecycle["collector_started"],
                        "collectorReturned": lifecycle["collector_returned"],
                    },
                    "reportHash": report_hash,
                    "ingressReceiptHash": ingress_receipt_hash,
                    "researchGraphManifestRef": {
                        "batchId": manifest_ref["batchId"],
                        "derivation": manifest_ref["derivation"],
                        "reconciliationRevision": manifest_ref["reconciliationRevision"],
                        "manifestSchemaVersion": manifest_ref["manifestSchemaVersion"],
                        "manifestHash": manifest_ref["manifestHash"],
                        "expectedRecordKeySetHash": manifest_ref["expectedRecordKeySetHash"],
                        "recordRefSetHash": manifest_ref["recordRefSetHash"],
                        "materializationStatus": manifest_ref["materializationStatus"],
                        "materializationAuthority": "scoped-admin-api",
                        "sourceCorrelation": {
                            "workflowRunId": run_id,
                            "commandId": command_id,
                            "attemptId": attempt_id,
                            "payloadHash": submission["payloadSha256"],
                            "batchId": manifest_ref["batchId"],
                            "manifestHash": manifest_ref["manifestHash"],
                            "reportHash": report_hash,
                            "ingressReceiptHash": ingress_receipt_hash,
                            "lifecycleHashes": {
                                "bridgeAccepted": lifecycle["bridge_accepted"],
                                "collectorStarted": lifecycle["collector_started"],
                                "collectorReturned": lifecycle["collector_returned"],
                            },
                        },
                    },
                    "pin": {
                        "sequence": pinned_fold["sequence"],
                        "researchRevisionId": pinned_fold["researchRevisionId"],
                        "manifestSetHash": pinned_fold["manifestSetHash"],
                    },
                    "decision": {
                        "operationId": decision["operationId"],
                        "decisionId": decision["decisionId"],
                        "decisionHash": decision["decisionHash"],
                        "payloadHash": decision["payloadHash"],
                        "manifestSetHash": decision["manifestSetHash"],
                        "claims": decision["claims"],
                        "manifests": decision["manifests"],
                    },
                    "execution": {
                        "executionId": execution["executionId"],
                        "operationId": execution["operationId"],
                        "decisionId": execution["decisionId"],
                        "decisionHash": execution["decisionHash"],
                        "payloadHash": execution["payloadHash"],
                        "outcome": execution["outcome"],
                        "attemptCount": execution["attemptCount"],
                    },
                    "receiverReceipt": {
                        "attemptNumber": final_attempt["attemptNumber"],
                        "receiptId": final_attempt["receiptId"],
                        "receiptHash": final_attempt["receiptHash"],
                        "httpStatus": final_attempt["httpStatus"],
                        "receipt": final_attempt["receipt"],
                        "durableReceipt": final_attempt["receipt"],
                        "outcome": final_attempt["outcome"],
                    },
                    "redactionProfile": materialization.get("redactionProfileVersion"),
                },
                sort_keys=True,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
