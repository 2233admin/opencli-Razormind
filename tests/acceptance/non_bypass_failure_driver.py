#!/usr/bin/env python3
"""In-network public-API driver for the first #37 failure scenario."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections.abc import Callable

import httpx

from tests.acceptance.non_bypass_vertical import graph as _base_graph
from scripts.non_bypass_failure_proof_contract import ACTUATOR_BY_SCENARIO


def _data(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        raise RuntimeError(f"{response.status_code}: {response.text[:300]}")
    value = response.json()
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise RuntimeError("public API did not return a data object")
    return value["data"]


def _post(
    client: httpx.Client,
    base: str,
    path: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    return _data(client.post(base + path, json=body, headers=headers))


def _get(client: httpx.Client, base: str, path: str, headers: dict[str, str]) -> dict[str, Any]:
    return _data(client.get(base + path, headers=headers))


def _post_published_run(
    client: httpx.Client,
    base: str,
    path: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        response = client.post(base + path, json=body, headers=headers)
        if not (
            response.status_code == 409
            and "Workflow must be published before API execution" in response.text
            and time.monotonic() < deadline
        ):
            return _data(response)
        time.sleep(0.5)


def _graph() -> dict[str, Any]:
    return _base_graph()


def _coordination(name: str) -> Path:
    root = Path("/proof-artifacts/coordination")
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _failure_result(
    *,
    scenario: str,
    run: str,
    fault: str,
    command_id: str,
    attempt_id: str,
    workflow_run_id: str,
    hashes: dict[str, Any],
    collection: dict[str, Any],
    materialization: dict[str, Any],
    graph: dict[str, Any] | None = None,
    delivery: dict[str, Any] | None = None,
    mutation_status: str = "none",
) -> dict[str, Any]:
    emitted_at = int(time.time())
    return {
        "scenario": scenario,
        "run": run,
        "fault": fault,
        "actuator": {
            "name": ACTUATOR_BY_SCENARIO[scenario],
            "invocationHash": hashlib.sha256(command_id.encode()).hexdigest(),
        },
        "correlation": {
            "commandId": command_id,
            "attemptId": attempt_id,
            "workflowRunId": workflow_run_id,
            "hashes": hashes,
        },
        "collection": collection,
        "materialization": materialization,
        "graph": graph
        or {
            "pin": None,
            "sequence": None,
            "readBlocker": "none",
            "mutationStatus": mutation_status,
        },
        "delivery": delivery
        or {
            "state": "none",
            "outcome": "none",
            "attemptCount": 0,
            "receiptHash": None,
            "reconciliation": "none",
        },
        "redactionProfile": "failure-v1",
        "timing": {
            "startedAt": emitted_at,
            "completedAt": emitted_at,
            "deadlineSeconds": 360,
        },
        "governanceReference": {
            "artifactId": "pending",
            "keyId": "pending",
            "trustRootFingerprint": "pending",
        },
        "authority": "authenticated-scoped-public-api",
    }


def admin_crash(run: str, scenario: str = "admin-crash") -> dict[str, Any]:
    fleet = {"X-API-Token": os.environ["API_AUTH_TOKEN"]}
    bootstrap = {**fleet, "Authorization": f"Bearer {os.environ['BOOTSTRAP_ADMIN_TOKEN']}"}
    proposer = {**fleet, "Authorization": f"Bearer {os.environ['PROOF_PROPOSER_JWT']}"}
    primary = "http://proof-admin:8000/api/v1"
    control = "http://proof-admin-control:8000/api/v1"
    with httpx.Client(timeout=60) as client:
        workspace = _post(
            client,
            primary,
            "/platform/workspaces",
            {
                "name": "Failure proof",
                "slug": run,
                "first_admin_subject": "bootstrap-admin",
                "first_admin_email": "bootstrap@proof.invalid",
                "first_admin_display_name": "Proof bootstrap",
            },
            bootstrap,
        )
        workspace_id = workspace["id"]
        _post(
            client,
            primary,
            f"/workspaces/{workspace_id}/members",
            {
                "subject": "proof-proposer",
                "email": "proof-proposer@proof.invalid",
                "display_name": "proof-proposer",
                "role": "operator",
            },
            bootstrap,
        )
        boot = _post(
            client,
            primary,
            f"/workspaces/{workspace_id}/projects/bootstrap",
            {
                "project": {"name": "Failure proof", "slug": run},
                "workflow": {"name": "Failure proof", "graph": _graph()},
            },
            bootstrap,
        )
        project, workflow = boot["project"], boot["primary_workflow"]
        route = (
            f"/workspaces/{workspace_id}/projects/{project['id']}/workflows/{workflow['id']}/runs"
        )
        validation = _post(
            client, primary, route.rsplit("/runs", 1)[0] + "/draft/validation-runs", {}, proposer
        )
        if not validation.get("valid"):
            raise RuntimeError("public workflow validation failed")
        _post(
            client,
            primary,
            route.rsplit("/runs", 1)[0] + "/versions",
            {
                "reason": "failure proof",
                "expectedRevision": 1,
                "validationRunId": validation["runId"],
            },
            proposer,
        )
        workflow_run = _post_published_run(
            client,
            primary,
            route,
            {
                "inputs": {},
                "responseMode": "async",
                "user": "proof-proposer",
                "requestId": run,
                "idempotencyKey": run,
            },
            proposer,
        )
        run_id = workflow_run["runId"]
        collections = f"{route}/{run_id}/iii-collections"
        if scenario == "no-report":
            signal = _coordination(f"{run}.submitted")
            signal.write_text(json.dumps({"route": collections}), encoding="utf-8")
            release = _coordination(f"{run}.resume")
            deadline = time.monotonic() + 90
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            if not release.exists():
                raise RuntimeError("orchestrator did not arm report-drop gate")
        submission = _post(
            client,
            primary,
            collections,
            {
                "version": "v1",
                "idempotencyKey": run,
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
        if scenario == "admin-crash":
            signal = _coordination(f"{run}.submitted")
            signal.write_text(
                json.dumps(
                    {"commandId": command_id, "attemptId": attempt_id, "route": collections}
                ),
                encoding="utf-8",
            )
            release = _coordination(f"{run}.resume")
            deadline = time.monotonic() + 90
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            if not release.exists():
                raise RuntimeError("orchestrator did not complete the admin crash gate")
        if scenario == "admin-crash":
            deadline = time.monotonic() + 30
            while True:
                try:
                    _post(
                        client,
                        control,
                        f"{collections}/{command_id}/resume",
                        {"idempotencyKey": run},
                        proposer,
                    )
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.5)
            status = _get(client, control, f"{collections}/{command_id}", proposer)
        else:
            deadline = time.monotonic() + 45
            status = {}
            while time.monotonic() < deadline:
                status = _get(client, primary, f"{collections}/{command_id}", proposer)
                if status.get("state") in {"missing_report", "completed", "report_missing"}:
                    break
                time.sleep(0.5)
        materialization: dict[str, Any] | None = None
        if scenario in {"no-report", "signed-zero"}:
            materialization = _post(
                client, primary, f"{collections}/{command_id}/materialize", {}, proposer
            )
            batch_id = materialization["batchId"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                materialization = _get(
                    client,
                    primary,
                    f"{route}/{run_id}/evidence-batches/v1/{batch_id}/status",
                    proposer,
                )
                if (
                    scenario != "signed-zero"
                    or materialization.get("materializationStatus") == "completed_empty"
                ):
                    break
                time.sleep(0.5)
    hashes = {"submission": submission["payloadSha256"]}

    for item in status.get("evidenceReferences", []):
        if item.get("hash"):
            hashes[item.get("kind", "public")] = item["hash"]
    if scenario == "no-report":
        if (
            materialization is None
            or materialization.get("materializationStatus") != "indeterminate"
        ):
            raise RuntimeError("scoped materialization did not expose indeterminate missing report")
        return _failure_result(
            scenario="no-report",
            run=run,
            fault="expected-key-report-dropped",
            command_id=command_id,
            attempt_id=attempt_id,
            workflow_run_id=run_id,
            hashes=hashes,
            collection={
                "blockingStage": "callback_missing",
                "recoveryAction": "recover",
                "sideEffectUncertainty": True,
            },
            materialization={
                "status": "indeterminate",
                "blocker": "missing_report",
                "recoveryAction": "recover",
                "manifestHash": None,
                "reconciliationRevision": materialization["reconciliationRevision"],
                "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
            },
        )
    if scenario == "signed-zero":
        if (
            materialization is None
            or materialization.get("materializationStatus") != "completed_empty"
        ):
            raise RuntimeError(
                "signed zero scoped materialization did not complete empty: "
                + json.dumps(materialization, sort_keys=True)
            )
        return _failure_result(
            scenario="signed-zero",
            run=run,
            fault="pinned-zero-fixture",
            command_id=command_id,
            attempt_id=attempt_id,
            workflow_run_id=run_id,
            hashes=hashes,
            collection={
                "blockingStage": "none",
                "recoveryAction": "none",
                "sideEffectUncertainty": False,
            },
            materialization={
                "status": "completed_empty",
                "blocker": "none",
                "recoveryAction": "none",
                "manifestHash": None,
                "reconciliationRevision": materialization["reconciliationRevision"],
                "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
            },
        )
    return _failure_result(
        scenario="admin-crash",
        run=run,
        fault="primary-admin-crash",
        command_id=command_id,
        attempt_id=attempt_id,
        workflow_run_id=run_id,
        hashes=hashes,
        collection={
            "blockingStage": "none",
            "recoveryAction": "resume",
            "sideEffectUncertainty": True,
        },
        materialization={
            "status": "unknown",
            "blocker": "none",
            "recoveryAction": "none",
            "manifestHash": None,
            "reconciliationRevision": None,
            "pageSnapshotAsOf": None,
        },
    )


def crash_after_ingest(run: str) -> dict[str, Any]:
    """Prove report loss only after a public ingress receipt is observable."""
    fleet = {"X-API-Token": os.environ["API_AUTH_TOKEN"]}
    bootstrap = {**fleet, "Authorization": f"Bearer {os.environ['BOOTSTRAP_ADMIN_TOKEN']}"}
    proposer = {**fleet, "Authorization": f"Bearer {os.environ['PROOF_PROPOSER_JWT']}"}
    primary = "http://proof-admin:8000/api/v1"
    with httpx.Client(timeout=60) as client:
        workspace = _post(
            client,
            primary,
            "/platform/workspaces",
            {
                "name": "Crash after ingest",
                "slug": run,
                "first_admin_subject": "bootstrap-admin",
                "first_admin_email": "bootstrap@proof.invalid",
                "first_admin_display_name": "Proof bootstrap",
            },
            bootstrap,
        )
        workspace_id = workspace["id"]
        _post(
            client,
            primary,
            f"/workspaces/{workspace_id}/members",
            {
                "subject": "proof-proposer",
                "email": "proof-proposer@proof.invalid",
                "display_name": "proof-proposer",
                "role": "operator",
            },
            bootstrap,
        )
        boot = _post(
            client,
            primary,
            f"/workspaces/{workspace_id}/projects/bootstrap",
            {
                "project": {"name": "Crash after ingest", "slug": run},
                "workflow": {"name": "Crash after ingest", "graph": _graph()},
            },
            bootstrap,
        )
        route = (
            f"/workspaces/{workspace_id}/projects/{boot['project']['id']}/"
            f"workflows/{boot['primary_workflow']['id']}/runs"
        )
        validation = _post(
            client, primary, route.rsplit("/runs", 1)[0] + "/draft/validation-runs", {}, proposer
        )
        if not validation.get("valid"):
            raise RuntimeError("public workflow validation failed")
        _post(
            client,
            primary,
            route.rsplit("/runs", 1)[0] + "/versions",
            {
                "reason": "crash after ingest",
                "expectedRevision": 1,
                "validationRunId": validation["runId"],
            },
            proposer,
        )
        workflow_run = _post_published_run(
            client,
            primary,
            route,
            {
                "inputs": {},
                "responseMode": "async",
                "user": "proof-proposer",
                "requestId": run,
                "idempotencyKey": run,
            },
            proposer,
        )
        collections = f"{route}/{workflow_run['runId']}/iii-collections"
        _coordination(f"{run}.arm-report-hold").write_text(
            json.dumps({"route": collections}), encoding="utf-8"
        )
        _wait_coordination(run, "report-hold-armed")
        submission = _post(
            client,
            primary,
            collections,
            {
                "version": "v1",
                "idempotencyKey": run,
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
        deadline = time.monotonic() + 60
        status: dict[str, Any] = {}
        receipt_hash = None
        while time.monotonic() < deadline:
            status = _get(client, primary, f"{collections}/{command_id}", proposer)
            receipt_hash = next(
                (
                    item.get("hash")
                    for item in status.get("evidenceReferences", [])
                    if item.get("kind") == "ingress_receipt" and item.get("hash")
                ),
                None,
            )
            if receipt_hash:
                break
            time.sleep(0.5)
        if not receipt_hash:
            raise RuntimeError("public status did not expose ingress receipt hash")
        _coordination(f"{run}.ingress-observed").write_text(
            json.dumps({"receiptHash": receipt_hash}), encoding="utf-8"
        )
        _wait_coordination(run, "collector-stopped")
        materialized = _post(
            client, primary, f"{collections}/{command_id}/materialize", {}, proposer
        )
        materialization = _get(
            client,
            primary,
            f"{route}/{workflow_run['runId']}/evidence-batches/v1/{materialized['batchId']}/status",
            proposer,
        )
    if materialization.get("materializationStatus") != "indeterminate":
        raise RuntimeError("public materialization was not indeterminate after collector stop")
    return _failure_result(
        scenario="crash-after-ingest",
        run=run,
        fault="collector-stopped-after-ingress",
        command_id=command_id,
        attempt_id=attempt_id,
        workflow_run_id=workflow_run["runId"],
        hashes={
            "submission": submission["payloadSha256"],
            "ingress_receipt": receipt_hash,
        },
        collection={
            "blockingStage": "callback_missing",
            "recoveryAction": "recover",
            "sideEffectUncertainty": True,
        },
        materialization={
            "status": "indeterminate",
            "blocker": "missing_report",
            "recoveryAction": "recover",
            "manifestHash": None,
            "reconciliationRevision": materialization["reconciliationRevision"],
            "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
        },
    )


def _wait_coordination(run: str, name: str, *, deadline: float | None = None) -> None:
    release = _coordination(f"{run}.{name}")
    deadline = time.monotonic() + 90 if deadline is None else deadline
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not release.exists():
        raise RuntimeError(f"orchestrator did not acknowledge {name}")


def iii_unreachable(run: str) -> dict[str, Any]:
    fleet = {"X-API-Token": os.environ["API_AUTH_TOKEN"]}
    bootstrap = {**fleet, "Authorization": f"Bearer {os.environ['BOOTSTRAP_ADMIN_TOKEN']}"}
    proposer = {**fleet, "Authorization": f"Bearer {os.environ['PROOF_PROPOSER_JWT']}"}
    primary = "http://proof-admin:8000/api/v1"
    with httpx.Client(timeout=60) as client:
        workspace = _post(
            client,
            primary,
            "/platform/workspaces",
            {
                "name": "III unreachable",
                "slug": run,
                "first_admin_subject": "bootstrap-admin",
                "first_admin_email": "bootstrap@proof.invalid",
                "first_admin_display_name": "Proof bootstrap",
            },
            bootstrap,
        )
        workspace_id = workspace["id"]
        _post(
            client,
            primary,
            f"/workspaces/{workspace_id}/members",
            {
                "subject": "proof-proposer",
                "email": "proof-proposer@proof.invalid",
                "display_name": "proof-proposer",
                "role": "operator",
            },
            bootstrap,
        )
        boot = _post(
            client,
            primary,
            f"/workspaces/{workspace_id}/projects/bootstrap",
            {
                "project": {"name": "III unreachable", "slug": run},
                "workflow": {"name": "III unreachable", "graph": _graph()},
            },
            bootstrap,
        )
        route = (
            f"/workspaces/{workspace_id}/projects/{boot['project']['id']}/"
            f"workflows/{boot['primary_workflow']['id']}/runs"
        )
        validation = _post(
            client, primary, route.rsplit("/runs", 1)[0] + "/draft/validation-runs", {}, proposer
        )
        if not validation.get("valid"):
            raise RuntimeError("public workflow validation failed")
        _post(
            client,
            primary,
            route.rsplit("/runs", 1)[0] + "/versions",
            {
                "reason": "III unreachable",
                "expectedRevision": 1,
                "validationRunId": validation["runId"],
            },
            proposer,
        )
        workflow_run = _post_published_run(
            client,
            primary,
            route,
            {
                "inputs": {},
                "responseMode": "async",
                "user": "proof-proposer",
                "requestId": run,
                "idempotencyKey": run,
            },
            proposer,
        )
        collections = f"{route}/{workflow_run['runId']}/iii-collections"
        _coordination(f"{run}.iii-ready").write_text(
            json.dumps({"route": collections}), encoding="utf-8"
        )
        release = _coordination(f"{run}.iii-release")
        deadline = time.monotonic() + 60
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        if not release.exists():
            raise RuntimeError("orchestrator did not arm the real III path gate")
        submission = _post(
            client,
            primary,
            collections,
            {
                "version": "v1",
                "idempotencyKey": run,
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
        deadline = time.monotonic() + 30
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status = _get(client, primary, f"{collections}/{command_id}", proposer)
            if status.get("state") == "bridge_unavailable":
                break
            time.sleep(0.5)
    if status.get("state") != "bridge_unavailable":
        raise RuntimeError("public collection status did not prove real III bridge unavailability")
    return _failure_result(
        scenario="iii-unreachable",
        run=run,
        fault="primary-to-iii-disconnected",
        command_id=command_id,
        attempt_id=attempt_id,
        workflow_run_id=workflow_run["runId"],
        hashes={"submission": submission["payloadSha256"]},
        collection={
            "blockingStage": "bridge_unavailable",
            "recoveryAction": "retry",
            "sideEffectUncertainty": True,
        },
        materialization={
            "status": "unknown",
            "blocker": "none",
            "recoveryAction": "none",
            "manifestHash": None,
            "reconciliationRevision": None,
            "pageSnapshotAsOf": None,
        },
        mutation_status="unchanged",
    )


def public_setup(client: httpx.Client, run: str) -> dict[str, Any]:
    """Create one public workspace/run used by the four isolated loss commands."""
    fleet = {"X-API-Token": os.environ["API_AUTH_TOKEN"]}
    bootstrap = {**fleet, "Authorization": f"Bearer {os.environ['BOOTSTRAP_ADMIN_TOKEN']}"}
    proposer = {**fleet, "Authorization": f"Bearer {os.environ['PROOF_PROPOSER_JWT']}"}
    primary = "http://proof-admin:8000/api/v1"
    workspace = _post(
        client,
        primary,
        "/platform/workspaces",
        {
            "name": "ODP loss proof",
            "slug": run,
            "first_admin_subject": "bootstrap-admin",
            "first_admin_email": "bootstrap@proof.invalid",
            "first_admin_display_name": "Proof bootstrap",
        },
        bootstrap,
    )
    workspace_id = workspace["id"]
    proposer_member = _post(
        client,
        primary,
        f"/workspaces/{workspace_id}/members",
        {
            "subject": "proof-proposer",
            "email": "proof-proposer@proof.invalid",
            "display_name": "proof-proposer",
            "role": "operator",
        },
        bootstrap,
    )
    boot = _post(
        client,
        primary,
        f"/workspaces/{workspace_id}/projects/bootstrap",
        {
            "project": {"name": "ODP loss proof", "slug": run},
            "workflow": {"name": "ODP loss proof", "graph": _graph()},
        },
        bootstrap,
    )
    route = (
        f"/workspaces/{workspace_id}/projects/{boot['project']['id']}/"
        f"workflows/{boot['primary_workflow']['id']}/runs"
    )
    validation = _post(
        client, primary, route.rsplit("/runs", 1)[0] + "/draft/validation-runs", {}, proposer
    )
    if not validation.get("valid"):
        raise RuntimeError("public workflow validation failed")
    published_version = _post(
        client,
        primary,
        route.rsplit("/runs", 1)[0] + "/versions",
        {"reason": "ODP loss proof", "expectedRevision": 1, "validationRunId": validation["runId"]},
        proposer,
    )
    workflow_run = _post_published_run(
        client,
        primary,
        route,
        {
            "inputs": {},
            "responseMode": "async",
            "user": "proof-proposer",
            "requestId": run,
            "idempotencyKey": run,
        },
        proposer,
    )
    return {
        "primary": primary,
        "proposer": proposer,
        "route": route,
        "runId": workflow_run["runId"],
        "collections": f"{route}/{workflow_run['runId']}/iii-collections",
        "workspaceId": workspace_id,
        "bootstrap": bootstrap,
        "proposerMember": proposer_member,
        "immutableScope": {
            "workspace_id": workspace_id,
            "project_id": boot["project"]["id"],
            "workflow_id": boot["primary_workflow"]["id"],
            "studio_workflow_version_id": published_version["id"],
            "node_id": "opencli-source",
        },
    }


def public_disposable_run(
    client: httpx.Client, setup: dict[str, Any], request_id: str
) -> dict[str, Any]:
    workflow_run = _post_published_run(
        client,
        setup["primary"],
        setup["route"],
        {
            "inputs": {},
            "responseMode": "async",
            "user": "proof-proposer",
            "requestId": request_id,
            "idempotencyKey": request_id,
        },
        setup["proposer"],
    )
    return {
        **setup,
        "runId": workflow_run["runId"],
        "collections": f"{setup['route']}/{workflow_run['runId']}/iii-collections",
    }


def public_submit(
    client: httpx.Client,
    setup: dict[str, Any],
    *,
    source_id: str,
    site: str,
    command: str,
    idempotency_key: str | None = None,
    stable_odp_source_id: str | None = None,
) -> dict[str, Any]:
    collection: dict[str, Any] = {
        "site": site,
        "command": command,
        "args": {"keyword": source_id},
    }
    if stable_odp_source_id is None:
        collection.update(
            {
                "sourceBindingId": source_id,
                "sourceBindingRevisionId": f"{source_id}-v1",
                "sourceBindingRevisionNumber": 1,
            }
        )
    else:
        collection["sourceId"] = stable_odp_source_id
    return _post(
        client,
        setup["primary"],
        setup["collections"],
        {
            "version": "v1",
            "idempotencyKey": idempotency_key or source_id,
            "nodeId": "opencli-source",
            "collection": collection,
        },
        setup["proposer"],
    )


def public_status(client: httpx.Client, setup: dict[str, Any], command_id: str) -> dict[str, Any]:
    return _get(client, setup["primary"], f"{setup['collections']}/{command_id}", setup["proposer"])


def public_materialize(
    client: httpx.Client, setup: dict[str, Any], command_id: str, *, recover: bool = False
) -> dict[str, Any]:
    action = "recover" if recover else "materialize"
    batch = _post(
        client,
        setup["primary"],
        f"{setup['collections']}/{command_id}/{action}",
        {},
        setup["proposer"],
    )
    return _get(
        client,
        setup["primary"],
        f"{setup['route']}/{setup['runId']}/evidence-batches/v1/{batch['batchId']}/status",
        setup["proposer"],
    )


def public_recover(client: httpx.Client, setup: dict[str, Any], command_id: str) -> dict[str, Any]:
    return public_materialize(client, setup, command_id, recover=True)


def _arm_gateway(client: httpx.Client, name: str, armed: bool) -> None:
    controls = {
        "http-schema-mutator": "http://proof-odp-http-gateway:8040",
        "ingest-redis-cut": "http://proof-odp-ingest-redis-gateway:8081",
        "ingest-redis-payload-mutator": "http://proof-odp-ingest-redis-mutator:8084",
        "store-pg-cut": "http://proof-odp-store-pg-gateway:8082",
        "store-redis-committed-xadd": "http://proof-odp-store-redis-gateway:8083",
    }
    response = client.post(
        f"{controls[name]}/_gate/{name}/arm",
        json={"armed": armed},
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"authenticated gateway arm failed: {response.status_code}")


def _set_receipt_gate(client: httpx.Client, mode: str) -> None:
    response = client.post(
        "http://proof-relay:8080/_gate/receipt",
        json={"mode": mode},
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"authenticated receipt gate update failed: {response.status_code}")


def _arm_query_page_gate(client: httpx.Client, armed: bool) -> None:
    response = client.post(
        "http://proof-odp-query-pg-gate:8000/_gate/query-page/arm",
        json={"armed": armed},
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"authenticated query-page gate arm failed: {response.status_code}")


def _release_query_page_gate(client: httpx.Client) -> None:
    response = client.post(
        "http://proof-odp-query-pg-gate:8000/_gate/query-page/release",
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"authenticated query-page gate release failed: {response.status_code}")


def _wait_for_query_page_gate(client: httpx.Client) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(
            "http://proof-odp-query-pg-gate:8000/_gate/query-page/held",
            headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
        )
        if response.status_code == 200 and response.json().get("held") is True:
            return
        time.sleep(0.2)
    raise RuntimeError("real ODP attempt-page SELECT did not reach the protocol gate")


def _wait_for_held_receipts(client: httpx.Client, expected_count: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(
            "http://proof-relay:8080/_gate/receipt-held",
            headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
        )
        if response.status_code == 200 and response.json().get("count", 0) >= expected_count:
            return
        time.sleep(0.2)
    raise RuntimeError("real actor ingress receipt did not reach the held callback path")


def _actuate_correlated_ingress(
    setup: dict[str, Any],
    submission: dict[str, Any],
    *,
    source_id: str,
    phase: str,
    event_id: str,
) -> None:
    with httpx.Client(timeout=45) as client:
        response = client.post(
            "http://proof-iii-actuator:8000/actuate/ingress",
            json={
                **setup["immutableScope"],
                "phase": phase,
                "run_id": setup["runId"],
                "command_id": submission["commandId"],
                "attempt_id": submission["attemptId"],
                "attempt_number": submission["attemptNumber"],
                "task_id": submission["taskId"],
                "trace_id": submission["traceId"],
                "source_id": source_id,
                "source_binding_id": None,
                "source_binding_revision_id": None,
                "source_binding_revision_number": None,
                "payload_sha256": submission["payloadSha256"],
                "event_id": event_id,
            },
            headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
        )
    if response.status_code != 200:
        raise RuntimeError(f"real III actor invocation failed: {response.status_code}")


def _public_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _public_response_hash(response: httpx.Response) -> str:
    return hashlib.sha256(response.content).hexdigest()


def _require_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise RuntimeError(
            f"{label} returned {response.status_code}, expected {expected}: {response.text[:300]}"
        )


def _wait_for_ingress_receipt(
    client: httpx.Client, setup: dict[str, Any], command_id: str, *, timeout: int = 90
) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = public_status(client, setup, command_id)
        for reference in last.get("evidenceReferences", []):
            if (
                reference.get("kind") == "ingress_receipt"
                and isinstance(reference.get("hash"), str)
                and len(reference["hash"]) == 64
            ):
                return last, reference["hash"]
        time.sleep(0.5)
    raise RuntimeError(
        "authenticated public status never exposed an ingress-receipt reference: "
        + json.dumps(last, sort_keys=True)
    )


def _ingress_receipt_hashes(status: dict[str, Any]) -> set[str]:
    return {
        reference["hash"]
        for reference in status.get("evidenceReferences", [])
        if reference.get("kind") == "ingress_receipt"
        and isinstance(reference.get("hash"), str)
        and len(reference["hash"]) == 64
    }


def _wait_for_new_ingress_receipt(
    client: httpx.Client,
    setup: dict[str, Any],
    command_id: str,
    *,
    prior_hashes: set[str],
    timeout: int = 30,
) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = public_status(client, setup, command_id)
        new_hashes = _ingress_receipt_hashes(last) - prior_hashes
        if len(new_hashes) == 1:
            return last, new_hashes.pop()
        time.sleep(0.5)
    raise RuntimeError(
        "authenticated public status never exposed one new signed ingress receipt: "
        + json.dumps(last, sort_keys=True)
    )


def _fixture_one_event_id(source_id: str) -> str:
    source_event = {"sourceEventKey": "failure-proof-001", "title": "one"}
    digest = hashlib.sha256(
        json.dumps(source_event, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return f"{source_id}:{digest[:32]}"


def _pinned_reference(pinned_fold: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": pinned_fold["sequence"],
        "researchRevisionId": pinned_fold["researchRevisionId"],
        "manifestSetHash": pinned_fold["manifestSetHash"],
    }


def _wait_for_expected_key_report(
    client: httpx.Client, setup: dict[str, Any], command_id: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = public_status(client, setup, command_id)
        if any(
            reference.get("kind") == "expected_key_report"
            for reference in last.get("evidenceReferences", [])
        ):
            return last
        time.sleep(0.5)
    raise RuntimeError(
        "authenticated public status never exposed an expected-key report: "
        + json.dumps(last, sort_keys=True)
    )


def _wait_for_materialization(
    client: httpx.Client,
    setup: dict[str, Any],
    command_id: str,
    *,
    predicate: Any,
    timeout: int = 180,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    result = public_materialize(client, setup, command_id)
    while not predicate(result) and time.monotonic() < deadline:
        time.sleep(2)
        result = public_recover(client, setup, command_id)
    if not predicate(result):
        raise RuntimeError(
            "authenticated materialization did not settle as required: "
            + json.dumps(result, sort_keys=True)
        )
    return result


def _completed_exact(value: dict[str, Any]) -> bool:
    counts = value.get("counts")
    return (
        value.get("materializationStatus") == "completed"
        and isinstance(counts, dict)
        and counts.get("record_present") == 1
        and counts.get("unknown") == 0
    )


def graph_stale_auth_cas_retract(run: str) -> dict[str, Any]:
    """Prove graph authorization, stale-CAS, pinned-read, and re-review guarantees."""
    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-graph/{run}"))
    keyword = f"graph-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=60) as client:
        setup = public_setup(client, run)
        reviewer = {
            "X-API-Token": os.environ["API_AUTH_TOKEN"],
            "Authorization": f"Bearer {os.environ['PROOF_REVIEWER_JWT']}",
        }
        reviewer_member_response = client.post(
            f"{setup['primary']}/workspaces/{setup['workspaceId']}/members",
            json={
                "subject": "proof-reviewer",
                "email": "proof-reviewer@proof.invalid",
                "display_name": "proof-reviewer",
                "role": "maintainer",
            },
            headers=setup["bootstrap"],
        )
        reviewer_member = _data(reviewer_member_response)
        hashes["reviewer_membership"] = _public_response_hash(reviewer_member_response)
        if reviewer_member["role"] != "maintainer":
            raise RuntimeError("proof reviewer did not receive reviewer capability")

        submission = public_submit(
            client,
            setup,
            source_id=keyword,
            stable_odp_source_id=source_id,
            site="bilibili",
            command="search",
        )
        hashes["submission"] = _public_hash(submission)
        report = _wait_for_expected_key_report(client, setup, submission["commandId"])
        hashes["expected_key_report_read"] = _public_hash(report)
        materialization = _wait_for_materialization(
            client,
            setup,
            submission["commandId"],
            predicate=_completed_exact,
        )
        hashes["completed_materialization"] = _public_hash(materialization)
        manifest_ref = materialization.get("researchGraphManifestRef")
        if (
            not isinstance(manifest_ref, dict)
            or manifest_ref.get("materializationStatus") != "completed"
            or not isinstance(manifest_ref.get("manifestHash"), str)
        ):
            raise RuntimeError("authenticated materialization lacked a completed graph manifest")

        graph_route = f"{setup['route']}/{setup['runId']}/research-graph-v2"
        initial_response = client.get(setup["primary"] + graph_route, headers=setup["proposer"])
        initial = _data(initial_response)
        hashes["graph_initial_read"] = _public_response_hash(initial_response)
        claim_id = f"graph-stale-auth-cas-retract-{run}"
        claim_hash = manifest_ref["manifestHash"]

        proposed_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-propose",
                "action": "propose",
                "expectedSequence": initial["sequence"],
                "expectedRevision": initial["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            headers=setup["proposer"],
        )
        _require_status(proposed_response, 201, "proposer graph proposal")
        proposed = _data(proposed_response)
        hashes["graph_propose_mutation"] = _public_response_hash(proposed_response)

        verified_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-verify",
                "action": "verify",
                "expectedSequence": proposed["sequence"],
                "expectedRevision": proposed["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            headers=reviewer,
        )
        _require_status(verified_response, 201, "reviewer graph verification")
        verified = _data(verified_response)
        hashes["graph_verify_mutation"] = _public_response_hash(verified_response)

        pinned_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-pin",
                "action": "pin",
                "expectedSequence": verified["sequence"],
                "expectedRevision": verified["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest_ref],
            },
            headers=reviewer,
        )
        _require_status(pinned_response, 201, "reviewer graph pin")
        pinned = _data(pinned_response)
        hashes["graph_pin_mutation"] = _public_response_hash(pinned_response)
        pinned_fold = pinned.get("pinnedFold")
        if not isinstance(pinned_fold, dict) or pinned_fold.get("blocked"):
            raise RuntimeError("reviewer pin was not publicly readable")

        pinned_read_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        pinned_read = _data(pinned_read_response)
        hashes["graph_pinned_read"] = _public_response_hash(pinned_read_response)
        if pinned_read.get("pinnedFold") != pinned_fold:
            raise RuntimeError("pinned graph read diverged from pin mutation")

        downgrade_response = client.patch(
            f"{setup['primary']}/workspaces/{setup['workspaceId']}/members/"
            f"{setup['proposerMember']['user_id']}",
            json={"role": "viewer"},
            headers=setup["bootstrap"],
        )
        _require_status(downgrade_response, 200, "proposer capability downgrade")
        hashes["proposer_capability_downgrade"] = _public_response_hash(downgrade_response)

        denied_before_response = client.get(
            setup["primary"] + graph_route, headers=setup["proposer"]
        )
        denied_before = _data(denied_before_response)
        hashes["graph_before_wrong_capability"] = _public_response_hash(denied_before_response)
        if denied_before != pinned_read:
            raise RuntimeError("capability downgrade mutated the graph")

        denied_verify_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-wrong-verify",
                "action": "verify",
                "expectedSequence": denied_before["sequence"],
                "expectedRevision": denied_before["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            headers=setup["proposer"],
        )
        _require_status(denied_verify_response, 403, "wrong-capability verification")
        hashes["graph_wrong_capability_verify_denial"] = _public_response_hash(
            denied_verify_response
        )
        denied_retract_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-wrong-retract",
                "action": "retract",
                "expectedSequence": denied_before["sequence"],
                "expectedRevision": denied_before["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            headers=setup["proposer"],
        )
        _require_status(denied_retract_response, 403, "wrong-capability retraction")
        hashes["graph_wrong_capability_retract_denial"] = _public_response_hash(
            denied_retract_response
        )
        denied_after_response = client.get(
            setup["primary"] + graph_route, headers=setup["proposer"]
        )
        denied_after = _data(denied_after_response)
        hashes["graph_after_wrong_capability"] = _public_response_hash(denied_after_response)
        if denied_before_response.content != denied_after_response.content:
            raise RuntimeError("403 graph mutation attempts changed authenticated graph bytes")
        if denied_after != denied_before:
            raise RuntimeError("403 graph mutation attempts changed public graph data")

        stale_before_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        stale_before = _data(stale_before_response)
        hashes["graph_before_stale_cas"] = _public_response_hash(stale_before_response)
        stale_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-stale-retract",
                "action": "retract",
                "expectedSequence": stale_before["sequence"] - 1,
                "expectedRevision": f"{stale_before['researchRevisionId']}-stale",
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            headers=reviewer,
        )
        _require_status(stale_response, 409, "stale reviewer retraction")
        hashes["graph_stale_cas_denial"] = _public_response_hash(stale_response)
        stale_after_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        stale_after = _data(stale_after_response)
        hashes["graph_after_stale_cas"] = _public_response_hash(stale_after_response)
        if stale_before_response.content != stale_after_response.content:
            raise RuntimeError("409 stale-CAS graph mutation changed authenticated graph bytes")
        if stale_after != stale_before:
            raise RuntimeError("409 stale-CAS graph mutation changed public graph data")

        mismatch_before_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        mismatch_before = _data(mismatch_before_response)
        hashes["graph_before_pinned_mismatch"] = _public_response_hash(mismatch_before_response)
        mismatch_response = client.get(
            setup["primary"] + graph_route,
            params={
                "expected_pin_sequence": pinned_fold["sequence"],
                "expected_pin_revision": pinned_fold["researchRevisionId"],
                "expected_pin_manifest_set_hash": "0" * 64,
            },
            headers=reviewer,
        )
        mismatch = _data(mismatch_response)
        hashes["graph_pinned_reference_mismatch_read"] = _public_response_hash(mismatch_response)
        if (
            mismatch.get("blocker") != "pinned_reference_mismatch"
            or mismatch.get("recoveryAction") != "re_review"
            or mismatch.get("pinnedFold", {}).get("blocked") is not True
        ):
            raise RuntimeError("mismatched pin did not return the required blocked read")
        mismatch_after_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        mismatch_after = _data(mismatch_after_response)
        hashes["graph_after_pinned_mismatch"] = _public_response_hash(mismatch_after_response)
        if mismatch_before_response.content != mismatch_after_response.content:
            raise RuntimeError("pinned-reference mismatch mutated graph bytes")
        if mismatch_after != mismatch_before:
            raise RuntimeError("pinned-reference mismatch mutated graph data")

        retract_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-legal-retract",
                "action": "retract",
                "expectedSequence": mismatch_after["sequence"],
                "expectedRevision": mismatch_after["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": claim_hash,
                "manifestRefs": [manifest_ref],
            },
            headers=reviewer,
        )
        _require_status(retract_response, 201, "reviewer legal retraction")
        retracted = _data(retract_response)
        hashes["graph_legal_retract_mutation"] = _public_response_hash(retract_response)
        if retracted.get("sequence") != mismatch_after["sequence"] + 1:
            raise RuntimeError("legal retract did not advance graph CAS exactly once")

        final_one_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        final_one = _data(final_one_response)
        hashes["graph_final_authenticated_read_one"] = _public_response_hash(final_one_response)
        final_two_response = client.get(setup["primary"] + graph_route, headers=setup["proposer"])
        final_two = _data(final_two_response)
        hashes["graph_final_authenticated_read_two"] = _public_response_hash(final_two_response)
        if final_one_response.content != final_two_response.content:
            raise RuntimeError("authenticated final graph reads were not byte-equivalent")
        final_claim = next(
            (claim for claim in final_one.get("claims", []) if claim.get("claimId") == claim_id),
            None,
        )
        if (
            final_one != final_two
            or final_one.get("sequence") != retracted["sequence"]
            or final_one.get("pinnedFold", {}).get("blocked") is not True
            or final_one.get("recoveryAction") != "re_review"
            or final_claim is None
            or final_claim.get("state") != "retracted"
        ):
            raise RuntimeError("retracted graph did not preserve its durable re-review blocker")

    return _failure_result(
        scenario="graph-stale-auth-cas-retract",
        run=run,
        fault="graph-stale-auth-cas-retract",
        command_id=submission["commandId"],
        attempt_id=submission["attemptId"],
        workflow_run_id=setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        },
        materialization={
            "status": "completed",
            "blocker": "none",
            "recoveryAction": "recover",
            "manifestHash": manifest_ref["manifestHash"],
            "reconciliationRevision": materialization["reconciliationRevision"],
            "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
        },
        mutation_status="re_review_required",
        graph={
            "pin": _public_hash(final_one["pinnedFold"]),
            "sequence": final_one["sequence"],
            "readBlocker": "retract",
            "mutationStatus": "re_review_required",
        },
    )


def amendment_decision_conflict(run: str) -> dict[str, Any]:
    """Prove an authenticated duplicate receipt amends evidence and invalidates pins."""
    stable_source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-amendment/{run}"))
    keyword = f"amendment-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=60) as client:
        setup = public_setup(client, run)
        reviewer = {
            "X-API-Token": os.environ["API_AUTH_TOKEN"],
            "Authorization": f"Bearer {os.environ['PROOF_REVIEWER_JWT']}",
        }
        reviewer_member_response = client.post(
            f"{setup['primary']}/workspaces/{setup['workspaceId']}/members",
            json={
                "subject": "proof-reviewer",
                "email": "proof-reviewer@proof.invalid",
                "display_name": "proof-reviewer",
                "role": "maintainer",
            },
            headers=setup["bootstrap"],
        )
        reviewer_member = _data(reviewer_member_response)
        hashes["reviewer_membership"] = _public_response_hash(reviewer_member_response)
        if reviewer_member["role"] != "maintainer":
            raise RuntimeError("proof reviewer did not receive approval capability")

        submission = public_submit(
            client,
            setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="bilibili",
            command="search",
        )
        hashes["submission"] = _public_hash(submission)
        expected_report = _wait_for_expected_key_report(client, setup, submission["commandId"])
        hashes["expected_key_report_read"] = _public_hash(expected_report)
        receipt_status, accepted_receipt = _wait_for_ingress_receipt(
            client, setup, submission["commandId"]
        )
        hashes["initial_signed_receipt"] = accepted_receipt
        n_materialization = _wait_for_materialization(
            client,
            setup,
            submission["commandId"],
            predicate=_completed_exact,
        )
        hashes["materialization_n"] = _public_hash(n_materialization)
        manifest_n = n_materialization.get("researchGraphManifestRef")
        if (
            not isinstance(manifest_n, dict)
            or manifest_n.get("materializationStatus") != "completed"
            or not isinstance(manifest_n.get("manifestHash"), str)
        ):
            raise RuntimeError("terminal N materialization lacked a graph manifest")

        graph_route = f"{setup['route']}/{setup['runId']}/research-graph-v2"
        graph_initial_response = client.get(
            setup["primary"] + graph_route, headers=setup["proposer"]
        )
        graph_initial = _data(graph_initial_response)
        hashes["graph_before_n_pin"] = _public_response_hash(graph_initial_response)
        claim_id = f"amendment-decision-conflict-{run}"

        proposed_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-amend-propose",
                "action": "propose",
                "expectedSequence": graph_initial["sequence"],
                "expectedRevision": graph_initial["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest_n["manifestHash"],
                "manifestRefs": [manifest_n],
            },
            headers=setup["proposer"],
        )
        _require_status(proposed_response, 201, "N manifest proposal")
        proposed = _data(proposed_response)
        hashes["graph_propose_n"] = _public_response_hash(proposed_response)

        verified_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-amend-verify-n",
                "action": "verify",
                "expectedSequence": proposed["sequence"],
                "expectedRevision": proposed["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest_n["manifestHash"],
                "manifestRefs": [manifest_n],
            },
            headers=reviewer,
        )
        _require_status(verified_response, 201, "independent N verification")
        verified = _data(verified_response)
        hashes["graph_verify_n"] = _public_response_hash(verified_response)

        pin_n_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-amend-pin-n",
                "action": "pin",
                "expectedSequence": verified["sequence"],
                "expectedRevision": verified["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest_n],
            },
            headers=reviewer,
        )
        _require_status(pin_n_response, 201, "N manifest pin")
        pin_n = _data(pin_n_response)
        hashes["graph_pin_n"] = _public_response_hash(pin_n_response)
        old_pin = pin_n.get("pinnedFold")
        if not isinstance(old_pin, dict) or old_pin.get("blocked"):
            raise RuntimeError("N manifest was not publicly pinned")

        graph_old_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        graph_old = _data(graph_old_response)
        hashes["graph_old_pinned_read"] = _public_response_hash(graph_old_response)
        if graph_old.get("pinnedFold") != old_pin:
            raise RuntimeError("N pinned read diverged before the amendment")

        target_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-proof",
                "endpointIdentity": "receiver-channel-proof",
                "credentialReference": "credential-reference-proof",
            },
            headers=reviewer,
        )
        _require_status(target_response, 201, "controlled delivery target creation")
        target = _data(target_response)
        hashes["old_delivery_target"] = _public_response_hash(target_response)
        old_decision_body = {
            "version": "v1",
            "operationId": f"{run}-operation-n",
            "idempotencyKey": f"{run}-decision-n",
            "nodeId": "opencli-source",
            "targetId": target["targetId"],
            "pinnedReference": _pinned_reference(old_pin),
            "selectedClaimIds": [claim_id],
        }
        old_decision_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
            json=old_decision_body,
            headers=reviewer,
        )
        _require_status(old_decision_response, 201, "N-bound delivery authorization")
        old_decision = _data(old_decision_response)
        hashes["old_delivery_decision"] = _public_response_hash(old_decision_response)

        original_event_id = _fixture_one_event_id(stable_source_id)
        prior_receipts = _ingress_receipt_hashes(receipt_status)
        _actuate_correlated_ingress(
            setup,
            submission,
            source_id=stable_source_id,
            phase="amendment_duplicate",
            event_id=original_event_id,
        )
        duplicate_status, duplicate_receipt = _wait_for_new_ingress_receipt(
            client,
            setup,
            submission["commandId"],
            prior_hashes=prior_receipts,
        )
        hashes["signed_duplicate_receipt"] = duplicate_receipt
        hashes["status_after_duplicate_receipt"] = _public_hash(duplicate_status)

        n_plus_one = public_recover(client, setup, submission["commandId"])
        hashes["materialization_n_plus_one"] = _public_hash(n_plus_one)
        manifest_n_plus_one = n_plus_one.get("researchGraphManifestRef")
        record_keys = {
            (reference.get("sourceId"), reference.get("eventId"))
            for reference in n_plus_one.get("recordReferences", [])
        }
        if (
            not _completed_exact(n_plus_one)
            or n_plus_one.get("reconciliationRevision")
            != n_materialization["reconciliationRevision"] + 1
            or not isinstance(manifest_n_plus_one, dict)
            or manifest_n_plus_one.get("manifestHash") == manifest_n["manifestHash"]
            or (stable_source_id, original_event_id) not in record_keys
        ):
            raise RuntimeError(
                "authenticated recover did not append terminal N+1 for the exact key"
            )

        graph_stale_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        graph_stale = _data(graph_stale_response)
        hashes["graph_stale_manifest_read"] = _public_response_hash(graph_stale_response)
        if (
            graph_stale.get("blocker") != "manifest_superseded"
            or graph_stale.get("recoveryAction") != "re_review"
            or graph_stale.get("pinnedFold", {}).get("blocked") is not True
        ):
            raise RuntimeError("N+1 did not block the old pin for re-review")

        old_pin_conflict_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
            json={
                **old_decision_body,
                "operationId": f"{run}-operation-old-pin-conflict",
                "idempotencyKey": f"{run}-decision-old-pin-conflict",
            },
            headers=reviewer,
        )
        _require_status(old_pin_conflict_response, 409, "old blocked-pin authorization")
        hashes["old_blocked_pin_conflict"] = _public_response_hash(old_pin_conflict_response)

        supersede_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-amend-supersede",
                "action": "supersede",
                "expectedSequence": graph_stale["sequence"],
                "expectedRevision": graph_stale["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "manifestRefs": [manifest_n_plus_one],
                "supersedesEventId": f"research-graph-v2:{run}-amend-propose",
            },
            headers=reviewer,
        )
        _require_status(supersede_response, 201, "N+1 manifest supersession")
        superseded = _data(supersede_response)
        hashes["graph_supersede_n_plus_one"] = _public_response_hash(supersede_response)

        second_review_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-amend-verify-n-plus-one",
                "action": "verify",
                "expectedSequence": superseded["sequence"],
                "expectedRevision": superseded["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest_n["manifestHash"],
                "manifestRefs": [manifest_n_plus_one],
            },
            headers=setup["proposer"],
        )
        _require_status(second_review_response, 201, "second independent verification")
        second_review = _data(second_review_response)
        hashes["graph_verify_n_plus_one"] = _public_response_hash(second_review_response)

        pin_n_plus_one_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-amend-pin-n-plus-one",
                "action": "pin",
                "expectedSequence": second_review["sequence"],
                "expectedRevision": second_review["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest_n_plus_one],
            },
            headers=setup["proposer"],
        )
        _require_status(pin_n_plus_one_response, 201, "N+1 manifest pin")
        pin_n_plus_one = _data(pin_n_plus_one_response)
        hashes["graph_pin_n_plus_one"] = _public_response_hash(pin_n_plus_one_response)
        new_pin = pin_n_plus_one.get("pinnedFold")
        if not isinstance(new_pin, dict) or new_pin.get("blocked"):
            raise RuntimeError("N+1 did not produce a fresh public graph pin")

        graph_new_response = client.get(setup["primary"] + graph_route, headers=setup["proposer"])
        graph_new = _data(graph_new_response)
        hashes["graph_new_pinned_read"] = _public_response_hash(graph_new_response)
        if (
            graph_new.get("pinnedFold") != new_pin
            or graph_new.get("blocker") is not None
            or graph_new.get("recoveryAction") != "none"
        ):
            raise RuntimeError("N+1 graph pin did not clear the stale-manifest blocker")

        new_decision_body = {
            "version": "v1",
            "operationId": f"{run}-operation-n-plus-one",
            "idempotencyKey": f"{run}-decision-n-plus-one",
            "nodeId": "opencli-source",
            "targetId": target["targetId"],
            "pinnedReference": _pinned_reference(new_pin),
            "selectedClaimIds": [claim_id],
        }
        new_decision_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
            json=new_decision_body,
            headers=setup["proposer"],
        )
        _require_status(new_decision_response, 201, "N+1-bound delivery authorization")
        new_decision = _data(new_decision_response)
        hashes["new_delivery_decision"] = _public_response_hash(new_decision_response)
        if (
            old_decision["decisionId"] == new_decision["decisionId"]
            or old_decision["decisionHash"] == new_decision["decisionHash"]
        ):
            raise RuntimeError("N and N+1 delivery decisions were not separately bound")

        revised_target_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-targets",
            json={
                "targetId": target["targetId"],
                "receiverIdentity": "controlled-receiver-proof",
                "endpointIdentity": "receiver-channel-proof",
                "credentialReference": "credential-reference-proof",
            },
            headers=reviewer,
        )
        _require_status(revised_target_response, 201, "delivery target revision")
        revised_target = _data(revised_target_response)
        hashes["revised_delivery_target"] = _public_response_hash(revised_target_response)
        if revised_target["revision"] != target["revision"] + 1:
            raise RuntimeError("new delivery target revision was not created")

        replay_conflict_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
            json=new_decision_body,
            headers=setup["proposer"],
        )
        _require_status(replay_conflict_response, 409, "changed delivery decision replay")
        hashes["changed_decision_replay_conflict"] = _public_response_hash(replay_conflict_response)

    return _failure_result(
        scenario="amendment-decision-conflict",
        run=run,
        fault="terminal-manifest-amendment-and-delivery-binding-conflict",
        command_id=submission["commandId"],
        attempt_id=submission["attemptId"],
        workflow_run_id=setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        },
        materialization={
            "status": "completed",
            "blocker": "none",
            "recoveryAction": "recover",
            "manifestHash": manifest_n_plus_one["manifestHash"],
            "reconciliationRevision": n_plus_one["reconciliationRevision"],
            "pageSnapshotAsOf": n_plus_one.get("pageSnapshotAsOf"),
        },
        graph={
            "pin": _public_hash(new_pin),
            "sequence": graph_new["sequence"],
            "readBlocker": "stale_manifest",
            "mutationStatus": "re_review_required",
        },
    )


def _set_delivery_proxy_mode(client: httpx.Client, mode: str) -> None:
    response = client.post(
        "https://proof-delivery-proxy:8000/_gate/delivery",
        json={"mode": mode},
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    _require_status(response, 200, f"delivery proxy {mode} mode")


def _require_blocked_delivery(
    execution: dict[str, Any],
    *,
    expected_attempts: int,
    expected_transport: str,
    expected_status: int | None,
) -> None:
    attempts = execution.get("attempts")
    if (
        execution.get("state") != "blocked"
        or execution.get("outcome") != "unknown"
        or execution.get("attemptCount") != expected_attempts
        or not isinstance(attempts, list)
        or len(attempts) != expected_attempts
    ):
        raise RuntimeError(f"delivery did not end in bounded unknown state: {execution}")
    for attempt in attempts:
        if (
            attempt.get("transport") != expected_transport
            or attempt.get("httpStatus") != expected_status
            or attempt.get("outcome") != "unknown"
            or attempt.get("protocol") != "unknown"
        ):
            raise RuntimeError(f"delivery attempt classification drifted: {execution}")
    expected_receipt = (
        "missing" if expected_transport == "transport-timeout" else "invalid-or-missing"
    )
    if any(attempt.get("receipt") != expected_receipt for attempt in attempts):
        raise RuntimeError(f"delivery receipt classification drifted: {execution}")


def _require_reconciled_delivery(
    execution: dict[str, Any], *, expected_attempts: int
) -> tuple[str, str]:
    reconciliations = execution.get("reconciliations")
    outcome = execution.get("outcome")
    if (
        execution.get("state") != "completed"
        or outcome not in {"accepted", "rejected"}
        or execution.get("attemptCount") != expected_attempts
        or not isinstance(reconciliations, list)
        or len(reconciliations) != 1
    ):
        raise RuntimeError(f"delivery reconciliation did not settle public execution: {execution}")
    receipt_hash = reconciliations[0].get("receiptHash")
    if not isinstance(receipt_hash, str) or len(receipt_hash) != 64:
        raise RuntimeError("reconciliation did not expose a signed receipt hash")
    return outcome, receipt_hash


def _require_within_deadline(
    deadline: float,
    label: str,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    if clock() > deadline:
        raise RuntimeError(f"{label} exceeded its bounded deadline")


def _reconcile_after_restart(
    client: httpx.Client,
    setup: dict[str, Any],
    reviewer: dict[str, str],
    execution_id: str,
    *,
    label: str,
    deadline: float,
) -> httpx.Response:
    while True:
        response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-executions/"
            f"{execution_id}/reconcile",
            json={},
            headers=reviewer,
        )
        if (
            response.status_code != 409
            or "Controlled receiver reconciliation remains unknown" not in response.text
            or time.monotonic() >= deadline
        ):
            _require_status(response, 200, f"{label} public reconciliation")
            return response
        time.sleep(0.5)


def receiver_recovery(run: str) -> dict[str, Any]:
    """Prove receiver MAC, timeout, and 5xx recovery through public APIs only."""
    stable_source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-receiver/{run}"))
    keyword = f"receiver-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=120) as client:
        setup = public_setup(client, run)
        reviewer = {
            "X-API-Token": os.environ["API_AUTH_TOKEN"],
            "Authorization": f"Bearer {os.environ['PROOF_REVIEWER_JWT']}",
        }
        reviewer_member_response = client.post(
            f"{setup['primary']}/workspaces/{setup['workspaceId']}/members",
            json={
                "subject": "proof-reviewer",
                "email": "proof-reviewer@proof.invalid",
                "display_name": "proof-reviewer",
                "role": "maintainer",
            },
            headers=setup["bootstrap"],
        )
        reviewer_member = _data(reviewer_member_response)
        hashes["reviewer_membership"] = _public_response_hash(reviewer_member_response)
        if reviewer_member.get("role") != "maintainer":
            raise RuntimeError("receiver proof reviewer lacks approval capability")

        submission = public_submit(
            client,
            setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="bilibili",
            command="search",
        )
        hashes["submission"] = _public_hash(submission)
        expected_report = _wait_for_expected_key_report(client, setup, submission["commandId"])
        hashes["expected_key_report"] = _public_hash(expected_report)
        _, ingress_receipt = _wait_for_ingress_receipt(client, setup, submission["commandId"])
        hashes["signed_ingress_receipt"] = ingress_receipt
        materialization = _wait_for_materialization(
            client, setup, submission["commandId"], predicate=_completed_exact
        )
        hashes["materialization"] = _public_hash(materialization)
        manifest = materialization.get("researchGraphManifestRef")
        if (
            not isinstance(manifest, dict)
            or manifest.get("materializationStatus") != "completed"
            or not isinstance(manifest.get("manifestHash"), str)
        ):
            raise RuntimeError("receiver proof lacks an eligible completed manifest")

        graph_route = f"{setup['route']}/{setup['runId']}/research-graph-v2"
        graph_read_response = client.get(setup["primary"] + graph_route, headers=setup["proposer"])
        graph_read = _data(graph_read_response)
        hashes["graph_before_pin"] = _public_response_hash(graph_read_response)
        claim_id = f"receiver-recovery-{run}"
        proposed_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-receiver-propose",
                "action": "propose",
                "expectedSequence": graph_read["sequence"],
                "expectedRevision": graph_read["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest["manifestHash"],
                "manifestRefs": [manifest],
            },
            headers=setup["proposer"],
        )
        _require_status(proposed_response, 201, "receiver graph proposal")
        proposed = _data(proposed_response)
        hashes["graph_propose"] = _public_response_hash(proposed_response)
        verified_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-receiver-verify",
                "action": "verify",
                "expectedSequence": proposed["sequence"],
                "expectedRevision": proposed["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest["manifestHash"],
                "manifestRefs": [manifest],
            },
            headers=reviewer,
        )
        _require_status(verified_response, 201, "receiver graph verification")
        verified = _data(verified_response)
        hashes["graph_verify"] = _public_response_hash(verified_response)
        pinned_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-receiver-pin",
                "action": "pin",
                "expectedSequence": verified["sequence"],
                "expectedRevision": verified["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest],
            },
            headers=reviewer,
        )
        _require_status(pinned_response, 201, "receiver graph pin")
        pinned = _data(pinned_response)
        hashes["graph_pin"] = _public_response_hash(pinned_response)
        pinned_fold = pinned.get("pinnedFold")
        if not isinstance(pinned_fold, dict) or pinned_fold.get("blocked"):
            raise RuntimeError("receiver delivery requires an eligible graph pin")
        graph_final_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        graph_final = _data(graph_final_response)
        hashes["graph_final"] = _public_response_hash(graph_final_response)
        if graph_final.get("pinnedFold") != pinned_fold:
            raise RuntimeError("public graph read did not retain the eligible pin")

        target_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-proof",
                "endpointIdentity": "receiver-channel-proof",
                "credentialReference": "credential-reference-proof",
            },
            headers=reviewer,
        )
        _require_status(target_response, 201, "receiver delivery target")
        target = _data(target_response)
        hashes["delivery_target"] = _public_response_hash(target_response)

        decisions: dict[str, dict[str, Any]] = {}
        for name in ("mac", "timeout", "five_xx"):
            response = client.post(
                f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
                json={
                    "version": "v1",
                    "operationId": f"{run}-receiver-{name}",
                    "idempotencyKey": f"{run}-receiver-decision-{name}",
                    "nodeId": "opencli-source",
                    "targetId": target["targetId"],
                    "pinnedReference": _pinned_reference(pinned_fold),
                    "selectedClaimIds": [claim_id],
                },
                headers=reviewer,
            )
            _require_status(response, 201, f"{name} delivery authorization")
            decisions[name] = _data(response)
            hashes[f"{name}_authorization"] = _public_response_hash(response)
        if len({decision["decisionId"] for decision in decisions.values()}) != 3:
            raise RuntimeError("receiver proof requires three distinct authorizations")
        executions: dict[str, dict[str, Any]] = {}

        for name, mode, attempts, transport, status in (
            ("mac", "corrupt_mac", 1, "http-4xx", 401),
            ("timeout", "withhold_response", 3, "transport-timeout", None),
            ("five_xx", "replace_with_503", 3, "http-5xx", 503),
        ):
            _set_delivery_proxy_mode(client, mode)
            execution_deadline = time.monotonic() + 110
            response = client.post(
                f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-executions",
                json={"decisionId": decisions[name]["decisionId"]},
                headers=reviewer,
            )
            _require_status(response, 201, f"{name} delivery execution")
            _require_within_deadline(execution_deadline, f"{name} delivery execution")
            execution = _data(response)
            _require_blocked_delivery(
                execution,
                expected_attempts=attempts,
                expected_transport=transport,
                expected_status=status,
            )
            executions[name] = execution
            hashes[f"{name}_execution"] = _public_response_hash(response)

        restart_reconciliation_deadline = time.monotonic() + 60
        _coordination(f"{run}.receiver-restart-ready").write_text("ready", encoding="utf-8")
        _wait_coordination(
            run,
            "receiver-restarted",
            deadline=restart_reconciliation_deadline,
        )

        reconciled: dict[str, dict[str, Any]] = {}
        for name in ("timeout", "five_xx"):
            response = _reconcile_after_restart(
                client,
                setup,
                reviewer,
                executions[name]["executionId"],
                label=name,
                deadline=restart_reconciliation_deadline,
            )
            reconciliation = _data(response)
            _require_reconciled_delivery(reconciliation, expected_attempts=3)
            reconciled[name] = reconciliation
            hashes[f"{name}_reconciliation"] = _public_response_hash(response)
        _require_within_deadline(
            restart_reconciliation_deadline,
            "receiver restart and reconciliation",
        )

        mac_status_response = client.get(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-executions/"
            f"{executions['mac']['executionId']}",
            headers=reviewer,
        )
        mac_status = _data(mac_status_response)
        _require_blocked_delivery(
            mac_status,
            expected_attempts=1,
            expected_transport="http-4xx",
            expected_status=401,
        )
        hashes["mac_attributable_denial"] = _public_response_hash(mac_status_response)
        final_status_response = client.get(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-executions/"
            f"{executions['timeout']['executionId']}",
            headers=reviewer,
        )
        final_status = _data(final_status_response)
        outcome, receipt_hash = _require_reconciled_delivery(final_status, expected_attempts=3)
        hashes["final_delivery_status"] = _public_response_hash(final_status_response)

    return _failure_result(
        scenario="receiver-recovery",
        run=run,
        fault="controlled-receiver-mac-timeout-and-5xx-recovery",
        command_id=submission["commandId"],
        attempt_id=submission["attemptId"],
        workflow_run_id=setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        },
        materialization={
            "status": "completed",
            "blocker": "none",
            "recoveryAction": "recover",
            "manifestHash": manifest["manifestHash"],
            "reconciliationRevision": materialization["reconciliationRevision"],
            "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
        },
        graph={
            "pin": _public_hash(pinned_fold),
            "sequence": graph_final["sequence"],
            "readBlocker": "none",
            "mutationStatus": "none",
        },
        delivery={
            "state": "settled",
            "outcome": outcome,
            "attemptCount": 3,
            "receiptHash": receipt_hash,
            "reconciliation": f"signed_{outcome}",
        },
    )


def _arm_cancel_before_dispatch_gate(client: httpx.Client, run: str) -> None:
    response = client.post(
        "http://proof-admin-pg-relay:8080/_gate/cancel-before-dispatch/arm",
        json={"run": run},
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    _require_status(response, 200, "cancel-before-dispatch relay arm")


def _release_cancel_before_dispatch_gate(client: httpx.Client) -> None:
    response = client.post(
        "http://proof-admin-pg-relay:8080/_gate/cancel-before-dispatch/release",
        json={},
        headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
    )
    _require_status(response, 200, "cancel-before-dispatch relay release")


def _require_empty_delivery_evidence(
    execution: dict[str, Any], *, state: str, outcome: str | None
) -> None:
    if (
        execution.get("state") != state
        or execution.get("outcome") != outcome
        or execution.get("attemptCount") != 0
        or execution.get("attempts") != []
        or execution.get("reconciliations") != []
    ):
        raise RuntimeError(f"delivery evidence escaped cancellation boundary: {execution}")


def cancel_before_dispatch(run: str) -> dict[str, Any]:
    """Cancel a durably reserved execution before the post-reservation lock reaches PG."""
    stable_source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-cancel/{run}"))
    keyword = f"cancel-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=120) as client:
        setup = public_setup(client, run)
        control = "http://proof-admin-control:8000/api/v1"
        reviewer = {
            "X-API-Token": os.environ["API_AUTH_TOKEN"],
            "Authorization": f"Bearer {os.environ['PROOF_REVIEWER_JWT']}",
        }
        reviewer_member_response = client.post(
            f"{setup['primary']}/workspaces/{setup['workspaceId']}/members",
            json={
                "subject": "proof-reviewer",
                "email": "proof-reviewer@proof.invalid",
                "display_name": "proof-reviewer",
                "role": "maintainer",
            },
            headers=setup["bootstrap"],
        )
        if _data(reviewer_member_response).get("role") != "maintainer":
            raise RuntimeError("cancellation reviewer lacks approval capability")
        submission = public_submit(
            client,
            setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="bilibili",
            command="search",
        )
        _wait_for_expected_key_report(client, setup, submission["commandId"])
        _wait_for_ingress_receipt(client, setup, submission["commandId"])
        materialization = _wait_for_materialization(
            client, setup, submission["commandId"], predicate=_completed_exact
        )
        manifest = materialization.get("researchGraphManifestRef")
        if (
            not isinstance(manifest, dict)
            or manifest.get("materializationStatus") != "completed"
            or not isinstance(manifest.get("manifestHash"), str)
        ):
            raise RuntimeError("cancellation proof lacks an eligible completed manifest")

        graph_route = f"{setup['route']}/{setup['runId']}/research-graph-v2"
        graph_initial = _get(client, setup["primary"], graph_route, setup["proposer"])
        claim_id = f"cancel-before-dispatch-{run}"
        proposed_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-cancel-propose",
                "action": "propose",
                "expectedSequence": graph_initial["sequence"],
                "expectedRevision": graph_initial["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest["manifestHash"],
                "manifestRefs": [manifest],
            },
            headers=setup["proposer"],
        )
        _require_status(proposed_response, 201, "cancellation graph proposal")
        proposed = _data(proposed_response)
        verified_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-cancel-verify",
                "action": "verify",
                "expectedSequence": proposed["sequence"],
                "expectedRevision": proposed["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest["manifestHash"],
                "manifestRefs": [manifest],
            },
            headers=reviewer,
        )
        _require_status(verified_response, 201, "cancellation graph verification")
        verified = _data(verified_response)
        pinned_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-cancel-pin",
                "action": "pin",
                "expectedSequence": verified["sequence"],
                "expectedRevision": verified["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest],
            },
            headers=reviewer,
        )
        _require_status(pinned_response, 201, "cancellation graph pin")
        pinned = _data(pinned_response).get("pinnedFold")
        if not isinstance(pinned, dict) or pinned.get("blocked"):
            raise RuntimeError("cancellation proof requires an eligible graph pin")
        graph_final = _get(client, setup["primary"], graph_route, reviewer)
        if graph_final.get("pinnedFold") != pinned:
            raise RuntimeError("cancellation proof public graph pin drifted")

        target_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-proof",
                "endpointIdentity": "receiver-channel-proof",
                "credentialReference": "credential-reference-proof",
            },
            headers=reviewer,
        )
        _require_status(target_response, 201, "cancellation delivery target")
        target = _data(target_response)
        decision_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
            json={
                "version": "v1",
                "operationId": f"{run}-cancel-before-dispatch",
                "idempotencyKey": f"{run}-cancel-before-dispatch-decision",
                "nodeId": "opencli-source",
                "targetId": target["targetId"],
                "pinnedReference": _pinned_reference(pinned),
                "selectedClaimIds": [claim_id],
            },
            headers=reviewer,
        )
        _require_status(decision_response, 201, "cancellation delivery authorization")
        decision = _data(decision_response)

        gate_armed = False
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            _arm_cancel_before_dispatch_gate(client, run)
            gate_armed = True

            def execute_primary() -> httpx.Response:
                with httpx.Client(timeout=120) as primary_client:
                    return primary_client.post(
                        f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-executions",
                        json={"decisionId": decision["decisionId"]},
                        headers=reviewer,
                    )

            primary_future = executor.submit(execute_primary)
            _wait_coordination(run, "cancel-before-dispatch-held")

            reserved_list_response = client.get(
                control + f"{setup['route']}/{setup['runId']}/delivery-executions",
                headers=reviewer,
            )
            reserved_list = _data(reserved_list_response)
            reserved_items = reserved_list.get("items")
            if not isinstance(reserved_items, list) or len(reserved_items) != 1:
                raise RuntimeError("control Admin did not expose one reserved execution")
            reserved = reserved_items[0]
            _require_empty_delivery_evidence(reserved, state="reserved", outcome=None)
            hashes["reserved_list"] = _public_response_hash(reserved_list_response)

            cancel_response = client.post(
                control + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                f"{reserved['executionId']}/cancel",
                json={},
                headers=reviewer,
            )
            _require_status(cancel_response, 200, "control cancellation")
            cancelled = _data(cancel_response)
            _require_empty_delivery_evidence(cancelled, state="cancelled", outcome="unknown")
            hashes["cancel"] = _public_response_hash(cancel_response)

            _release_cancel_before_dispatch_gate(client)
            gate_armed = False
            primary_response = primary_future.result(timeout=30)
            _require_status(primary_response, 201, "primary cancellation completion")
            primary_result = _data(primary_response)
            _require_empty_delivery_evidence(primary_result, state="cancelled", outcome="unknown")
            hashes["primary_result"] = _public_response_hash(primary_response)

            final_list_response = client.get(
                control + f"{setup['route']}/{setup['runId']}/delivery-executions",
                headers=reviewer,
            )
            final_list = _data(final_list_response)
            final_items = final_list.get("items")
            if not isinstance(final_items, list) or len(final_items) != 1:
                raise RuntimeError("control Admin did not retain one cancelled execution")
            _require_empty_delivery_evidence(final_items[0], state="cancelled", outcome="unknown")
            hashes["control_final_list"] = _public_response_hash(final_list_response)
            final_read_response = client.get(
                control + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                f"{reserved['executionId']}",
                headers=reviewer,
            )
            final_read = _data(final_read_response)
            _require_empty_delivery_evidence(final_read, state="cancelled", outcome="unknown")
            hashes["control_final_read"] = _public_response_hash(final_read_response)
        finally:
            if gate_armed:
                _release_cancel_before_dispatch_gate(client)
            executor.shutdown(wait=True)

    return _failure_result(
        scenario="cancel-before-dispatch",
        run=run,
        fault="durable-reservation-cancel-before-outbound-dispatch",
        command_id=submission["commandId"],
        attempt_id=submission["attemptId"],
        workflow_run_id=setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        },
        materialization={
            "status": "completed",
            "blocker": "none",
            "recoveryAction": "recover",
            "manifestHash": manifest["manifestHash"],
            "reconciliationRevision": materialization["reconciliationRevision"],
            "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
        },
        graph={
            "pin": _public_hash(pinned),
            "sequence": graph_final["sequence"],
            "readBlocker": "none",
            "mutationStatus": "none",
        },
        delivery={
            "state": "cancelled",
            "outcome": "unknown",
            "attemptCount": 0,
            "receiptHash": None,
            "reconciliation": "unknown",
        },
    )


def _wait_delivery_response_held(client: httpx.Client) -> None:
    deadline = time.monotonic() + 30
    headers = {"X-API-Token": os.environ["API_AUTH_TOKEN"]}
    while time.monotonic() < deadline:
        response = client.get(
            "https://proof-delivery-proxy:8000/_gate/delivery/status",
            headers=headers,
        )
        _require_status(response, 200, "delivery response gate status")
        value = response.json()
        if isinstance(value, dict) and value.get("responseHeld") is True:
            return
        time.sleep(0.1)
    raise RuntimeError("delivery proxy did not hold a valid signed receiver response")


def _read_public_execution_for_decision(
    client: httpx.Client,
    control: str,
    setup: dict[str, Any],
    reviewer: dict[str, str],
    decision_id: str,
    *,
    label: str,
) -> tuple[httpx.Response, dict[str, Any]]:
    response = client.get(
        control + f"{setup['route']}/{setup['runId']}/delivery-executions",
        headers=reviewer,
    )
    _require_status(response, 200, f"{label} execution list")
    items = _data(response).get("items")
    matches = (
        [item for item in items if isinstance(item, dict) and item.get("decisionId") == decision_id]
        if isinstance(items, list)
        else []
    )
    if len(matches) != 1:
        raise RuntimeError(f"{label} public execution lookup was not unique: {items}")
    return response, matches[0]


def _require_pending_in_flight_delivery(execution: dict[str, Any]) -> None:
    if (
        execution.get("state") != "in-flight"
        or execution.get("outcome") is not None
        or execution.get("attemptCount") != 0
        or execution.get("attempts") != []
        or execution.get("reconciliations") != []
    ):
        raise RuntimeError(
            f"public cancellation did not retain pending unknown delivery: {execution}"
        )


def _require_signed_direct_delivery(execution: dict[str, Any]) -> tuple[str, str]:
    outcome = execution.get("outcome")
    attempts = execution.get("attempts")
    if (
        execution.get("state") != "completed"
        or outcome not in {"accepted", "rejected"}
        or execution.get("attemptCount") != 1
        or not isinstance(attempts, list)
        or len(attempts) != 1
        or execution.get("reconciliations") != []
    ):
        raise RuntimeError(f"original signed response did not settle delivery: {execution}")
    attempt = attempts[0]
    receipt_hash = attempt.get("receiptHash")
    if (
        attempt.get("transport") != "http-success"
        or attempt.get("httpStatus") != 200
        or attempt.get("receipt") != "verified"
        or attempt.get("protocol") != "v2"
        or attempt.get("outcome") != outcome
        or not isinstance(receipt_hash, str)
        or len(receipt_hash) != 64
    ):
        raise RuntimeError(f"direct receiver signature evidence drifted: {execution}")
    return outcome, receipt_hash


def _require_cancelled_unknown_after_drop(execution: dict[str, Any]) -> None:
    attempts = execution.get("attempts")
    if (
        execution.get("state") != "cancelled"
        or execution.get("outcome") != "unknown"
        or execution.get("attemptCount") != 1
        or not isinstance(attempts, list)
        or len(attempts) != 1
        or execution.get("reconciliations") != []
    ):
        raise RuntimeError(
            f"dropped response did not leave cancelled unknown delivery: {execution}"
        )
    attempt = attempts[0]
    if (
        attempt.get("transport") != "http-5xx"
        or attempt.get("httpStatus") != 503
        or attempt.get("receipt") != "invalid-or-missing"
        or attempt.get("protocol") != "unknown"
        or attempt.get("outcome") != "unknown"
    ):
        raise RuntimeError(
            f"drop branch did not record a real dropped response attempt: {execution}"
        )


def cancel_in_flight(run: str) -> dict[str, Any]:
    """Exercise release and dropped-response cancellation across two real operations."""
    stable_source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-cancel-flight/{run}"))
    keyword = f"cancel-flight-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=120) as client:
        setup = public_setup(client, run)
        control = "http://proof-admin-control:8000/api/v1"
        reviewer = {
            "X-API-Token": os.environ["API_AUTH_TOKEN"],
            "Authorization": f"Bearer {os.environ['PROOF_REVIEWER_JWT']}",
        }
        reviewer_member_response = client.post(
            f"{setup['primary']}/workspaces/{setup['workspaceId']}/members",
            json={
                "subject": "proof-reviewer",
                "email": "proof-reviewer@proof.invalid",
                "display_name": "proof-reviewer",
                "role": "maintainer",
            },
            headers=setup["bootstrap"],
        )
        _require_status(reviewer_member_response, 201, "in-flight cancellation reviewer")
        if _data(reviewer_member_response).get("role") != "maintainer":
            raise RuntimeError("in-flight cancellation reviewer lacks approval capability")
        hashes["reviewer_membership"] = _public_response_hash(reviewer_member_response)
        submission = public_submit(
            client,
            setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="bilibili",
            command="search",
        )
        hashes["submission"] = _public_hash(submission)
        expected_report = _wait_for_expected_key_report(client, setup, submission["commandId"])
        hashes["expected_key_report"] = _public_hash(expected_report)
        _, ingress_receipt = _wait_for_ingress_receipt(client, setup, submission["commandId"])
        hashes["signed_ingress_receipt"] = ingress_receipt
        materialization = _wait_for_materialization(
            client, setup, submission["commandId"], predicate=_completed_exact
        )
        hashes["materialization"] = _public_hash(materialization)
        manifest = materialization.get("researchGraphManifestRef")
        if (
            not isinstance(manifest, dict)
            or manifest.get("materializationStatus") != "completed"
            or not isinstance(manifest.get("manifestHash"), str)
        ):
            raise RuntimeError("in-flight cancellation proof lacks an eligible manifest")

        graph_route = f"{setup['route']}/{setup['runId']}/research-graph-v2"
        graph_initial_response = client.get(
            setup["primary"] + graph_route, headers=setup["proposer"]
        )
        graph_initial = _data(graph_initial_response)
        hashes["graph_before_pin"] = _public_response_hash(graph_initial_response)
        claim_id = f"cancel-in-flight-{run}"
        proposed_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-cancel-flight-propose",
                "action": "propose",
                "expectedSequence": graph_initial["sequence"],
                "expectedRevision": graph_initial["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest["manifestHash"],
                "manifestRefs": [manifest],
            },
            headers=setup["proposer"],
        )
        _require_status(proposed_response, 201, "in-flight cancellation graph proposal")
        proposed = _data(proposed_response)
        hashes["graph_propose"] = _public_response_hash(proposed_response)
        verified_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-cancel-flight-verify",
                "action": "verify",
                "expectedSequence": proposed["sequence"],
                "expectedRevision": proposed["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": claim_id,
                "claimContentHash": manifest["manifestHash"],
                "manifestRefs": [manifest],
            },
            headers=reviewer,
        )
        _require_status(verified_response, 201, "in-flight cancellation graph verification")
        verified = _data(verified_response)
        hashes["graph_verify"] = _public_response_hash(verified_response)
        pinned_response = client.post(
            setup["primary"] + graph_route + "/mutations",
            json={
                "idempotencyKey": f"{run}-cancel-flight-pin",
                "action": "pin",
                "expectedSequence": verified["sequence"],
                "expectedRevision": verified["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest],
            },
            headers=reviewer,
        )
        _require_status(pinned_response, 201, "in-flight cancellation graph pin")
        pinned = _data(pinned_response).get("pinnedFold")
        if not isinstance(pinned, dict) or pinned.get("blocked"):
            raise RuntimeError("in-flight cancellation requires an eligible graph pin")
        hashes["graph_pin"] = _public_response_hash(pinned_response)
        graph_final_response = client.get(setup["primary"] + graph_route, headers=reviewer)
        graph_final = _data(graph_final_response)
        hashes["graph_final"] = _public_response_hash(graph_final_response)
        if graph_final.get("pinnedFold") != pinned:
            raise RuntimeError("in-flight cancellation public graph pin drifted")

        target_response = client.post(
            f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-proof",
                "endpointIdentity": "receiver-channel-proof",
                "credentialReference": "credential-reference-proof",
            },
            headers=reviewer,
        )
        _require_status(target_response, 201, "in-flight cancellation delivery target")
        target = _data(target_response)
        hashes["delivery_target"] = _public_response_hash(target_response)

        decisions: dict[str, dict[str, Any]] = {}
        for branch in ("release", "drop"):
            decision_response = client.post(
                f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-authorizations",
                json={
                    "version": "v1",
                    "operationId": f"{run}-cancel-in-flight-{branch}",
                    "idempotencyKey": f"{run}-cancel-in-flight-{branch}-decision",
                    "nodeId": "opencli-source",
                    "targetId": target["targetId"],
                    "pinnedReference": _pinned_reference(pinned),
                    "selectedClaimIds": [claim_id],
                },
                headers=reviewer,
            )
            _require_status(decision_response, 201, f"{branch} in-flight delivery authorization")
            decisions[branch] = _data(decision_response)
            hashes[f"{branch}_authorization"] = _public_response_hash(decision_response)
        if (
            decisions["release"]["decisionId"] == decisions["drop"]["decisionId"]
            or decisions["release"]["operationId"] == decisions["drop"]["operationId"]
        ):
            raise RuntimeError("release and drop cancellation branches are not independent")

        def execute_primary(decision_id: str) -> httpx.Response:
            with httpx.Client(timeout=120) as primary_client:
                return primary_client.post(
                    f"{setup['primary']}{setup['route']}/{setup['runId']}/delivery-executions",
                    json={"decisionId": decision_id},
                    headers=reviewer,
                )

        _set_delivery_proxy_mode(client, "hold_valid_response")
        release_executor = ThreadPoolExecutor(max_workers=1)
        release_future = None
        try:
            release_future = release_executor.submit(
                execute_primary, decisions["release"]["decisionId"]
            )
            _wait_delivery_response_held(client)
            release_intermediate_response, release_intermediate = (
                _read_public_execution_for_decision(
                    client,
                    control,
                    setup,
                    reviewer,
                    decisions["release"]["decisionId"],
                    label="release intermediate",
                )
            )
            _require_pending_in_flight_delivery(release_intermediate)
            hashes["release_intermediate"] = _public_response_hash(release_intermediate_response)
            release_cancel_response = client.post(
                control
                + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                + f"{release_intermediate['executionId']}/cancel",
                json={},
                headers=reviewer,
            )
            _require_status(release_cancel_response, 200, "release public cancellation")
            _require_pending_in_flight_delivery(_data(release_cancel_response))
            hashes["release_cancel"] = _public_response_hash(release_cancel_response)
            _set_delivery_proxy_mode(client, "release_valid_response")
            release_execution_response = release_future.result(timeout=30)
            _require_status(
                release_execution_response, 201, "release original execution completion"
            )
            release_result = _data(release_execution_response)
            release_outcome, _ = _require_signed_direct_delivery(release_result)
            hashes["release_execution_result"] = _public_response_hash(release_execution_response)
            release_final_response = client.get(
                control
                + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                + f"{release_intermediate['executionId']}",
                headers=reviewer,
            )
            _require_status(release_final_response, 200, "release final public read")
            release_final = _data(release_final_response)
            final_outcome, _ = _require_signed_direct_delivery(release_final)
            if final_outcome != release_outcome:
                raise RuntimeError("release final read diverged from original signed response")
            hashes["release_final"] = _public_response_hash(release_final_response)
        finally:
            if release_future is not None and not release_future.done():
                client.post(
                    "https://proof-delivery-proxy:8000/_gate/delivery",
                    json={"mode": "drop_valid_response"},
                    headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
                )
            release_executor.shutdown(wait=True)
            _set_delivery_proxy_mode(client, "pass_through")

        _set_delivery_proxy_mode(client, "hold_valid_response")
        drop_executor = ThreadPoolExecutor(max_workers=1)
        drop_future = None
        try:
            drop_future = drop_executor.submit(execute_primary, decisions["drop"]["decisionId"])
            _wait_delivery_response_held(client)
            drop_intermediate_response, drop_intermediate = _read_public_execution_for_decision(
                client,
                control,
                setup,
                reviewer,
                decisions["drop"]["decisionId"],
                label="drop intermediate",
            )
            _require_pending_in_flight_delivery(drop_intermediate)
            hashes["drop_intermediate"] = _public_response_hash(drop_intermediate_response)
            drop_cancel_response = client.post(
                control
                + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                + f"{drop_intermediate['executionId']}/cancel",
                json={},
                headers=reviewer,
            )
            _require_status(drop_cancel_response, 200, "drop public cancellation")
            _require_pending_in_flight_delivery(_data(drop_cancel_response))
            hashes["drop_cancel"] = _public_response_hash(drop_cancel_response)
            _set_delivery_proxy_mode(client, "drop_valid_response")
            drop_execution_response = drop_future.result(timeout=30)
            _require_status(drop_execution_response, 201, "drop original execution completion")
            drop_result = _data(drop_execution_response)
            _require_cancelled_unknown_after_drop(drop_result)
            hashes["drop_execution_result"] = _public_response_hash(drop_execution_response)
            _set_delivery_proxy_mode(client, "pass_through")
            drop_reconciliation_response = client.post(
                control
                + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                + f"{drop_intermediate['executionId']}/reconcile",
                json={},
                headers=reviewer,
            )
            _require_status(drop_reconciliation_response, 200, "drop Admin reconciliation")
            drop_reconciliation = _data(drop_reconciliation_response)
            drop_outcome, drop_receipt_hash = _require_reconciled_delivery(
                drop_reconciliation, expected_attempts=1
            )
            hashes["drop_reconciliation"] = _public_response_hash(drop_reconciliation_response)
            drop_final_response = client.get(
                control
                + f"{setup['route']}/{setup['runId']}/delivery-executions/"
                + f"{drop_intermediate['executionId']}",
                headers=reviewer,
            )
            _require_status(drop_final_response, 200, "drop final public read")
            drop_final = _data(drop_final_response)
            final_drop_outcome, final_drop_receipt_hash = _require_reconciled_delivery(
                drop_final, expected_attempts=1
            )
            if final_drop_outcome != drop_outcome or final_drop_receipt_hash != drop_receipt_hash:
                raise RuntimeError("drop final status diverged from signed reconciliation")
            hashes["drop_final"] = _public_response_hash(drop_final_response)
        finally:
            if drop_future is not None and not drop_future.done():
                client.post(
                    "https://proof-delivery-proxy:8000/_gate/delivery",
                    json={"mode": "drop_valid_response"},
                    headers={"X-API-Token": os.environ["API_AUTH_TOKEN"]},
                )
            drop_executor.shutdown(wait=True)
            _set_delivery_proxy_mode(client, "pass_through")

    return _failure_result(
        scenario="cancel-in-flight",
        run=run,
        fault="signed-response-release-and-dropped-response-reconciliation",
        command_id=submission["commandId"],
        attempt_id=submission["attemptId"],
        workflow_run_id=setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        },
        materialization={
            "status": "completed",
            "blocker": "none",
            "recoveryAction": "recover",
            "manifestHash": manifest["manifestHash"],
            "reconciliationRevision": materialization["reconciliationRevision"],
            "pageSnapshotAsOf": materialization.get("pageSnapshotAsOf"),
        },
        graph={
            "pin": _public_hash(pinned),
            "sequence": graph_final["sequence"],
            "readBlocker": "none",
            "mutationStatus": "none",
        },
        delivery={
            "state": "settled",
            "outcome": drop_outcome,
            "attemptCount": 1,
            "receiptHash": drop_receipt_hash,
            "reconciliation": f"signed_{drop_outcome}",
        },
    )


def duplicate_dlq(run: str) -> dict[str, Any]:
    """Prove replay, duplicate ingress, durable DLQ, and unknown retention publicly."""
    stable_source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-duplicate/{run}"))
    keyword = f"duplicate-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=60) as client:
        first_setup = public_setup(client, run)
        first = public_submit(
            client,
            first_setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="github",
            command="issues",
            idempotency_key="same-admin-replay",
        )
        if first.get("created") is not True:
            raise RuntimeError("initial authenticated Admin submission was not created")
        replay = public_submit(
            client,
            first_setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="github",
            command="issues",
            idempotency_key="same-admin-replay",
        )
        if replay.get("created") is not False or any(
            replay.get(name) != first.get(name)
            for name in ("commandId", "attemptId", "payloadSha256")
        ):
            raise RuntimeError(
                "identical authenticated replay minted a different collection intent"
            )
        first_status, first_receipt = _wait_for_ingress_receipt(
            client, first_setup, first["commandId"]
        )
        first_exact = _wait_for_materialization(
            client, first_setup, first["commandId"], predicate=_completed_exact
        )
        hashes.update(
            {
                "replay_initial": _public_hash(first),
                "replay_same_intent": _public_hash(replay),
                "replay_status": _public_hash(first_status),
                "replay_receipt": first_receipt,
                "replay_exact": _public_hash(first_exact),
            }
        )

        duplicate_setup = public_disposable_run(client, first_setup, f"{run}-duplicate")
        duplicate = public_submit(
            client,
            duplicate_setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="github",
            command="issues",
            idempotency_key="second-disposable-command",
        )
        duplicate_status, duplicate_receipt = _wait_for_ingress_receipt(
            client, duplicate_setup, duplicate["commandId"]
        )
        duplicate_exact = _wait_for_materialization(
            client, duplicate_setup, duplicate["commandId"], predicate=_completed_exact
        )
        hashes.update(
            {
                "duplicate_submission": _public_hash(duplicate),
                "duplicate_status": _public_hash(duplicate_status),
                "duplicate_signed_receipt": duplicate_receipt,
                "duplicate_exact_presence": _public_hash(duplicate_exact),
            }
        )

        dlq_setup = public_disposable_run(client, first_setup, f"{run}-dlq")
        _arm_gateway(client, "ingest-redis-payload-mutator", True)
        try:
            dlq = public_submit(
                client,
                dlq_setup,
                source_id="poison",
                stable_odp_source_id=str(uuid.uuid4()),
                site="reddit",
                command="posts",
                idempotency_key="retained-dlq",
            )
            dlq_status, dlq_receipt = _wait_for_ingress_receipt(client, dlq_setup, dlq["commandId"])
        finally:
            _arm_gateway(client, "ingest-redis-payload-mutator", False)
        retained_dlq = _wait_for_materialization(
            client,
            dlq_setup,
            dlq["commandId"],
            predicate=lambda value: (
                value.get("materializationStatus") == "partial"
                and value.get("counts", {}).get("dlq") == 1
                and value.get("counts", {}).get("unknown") == 0
            ),
            timeout=240,
        )
        hashes.update(
            {
                "retained_dlq_submission": _public_hash(dlq),
                "retained_dlq_status": _public_hash(dlq_status),
                "retained_dlq_receipt": dlq_receipt,
                "retained_dlq_materialization": _public_hash(retained_dlq),
            }
        )

        unknown_setup = public_disposable_run(client, first_setup, f"{run}-unknown")
        _arm_gateway(client, "store-pg-cut", True)
        try:
            unknown = public_submit(
                client,
                unknown_setup,
                source_id="unknown",
                stable_odp_source_id=str(uuid.uuid4()),
                site="youtube",
                command="videos",
                idempotency_key="absent-retention",
            )
            unknown_status, unknown_receipt = _wait_for_ingress_receipt(
                client, unknown_setup, unknown["commandId"]
            )
            unknown_retention = _wait_for_materialization(
                client,
                unknown_setup,
                unknown["commandId"],
                predicate=lambda value: (
                    value.get("materializationStatus") == "indeterminate"
                    and value.get("counts", {}).get("unknown") == 1
                ),
                timeout=60,
            )
        finally:
            _arm_gateway(client, "store-pg-cut", False)
        hashes.update(
            {
                "unknown_submission": _public_hash(unknown),
                "unknown_status": _public_hash(unknown_status),
                "unknown_receipt": unknown_receipt,
                "unknown_retention_materialization": _public_hash(unknown_retention),
            }
        )
    return _failure_result(
        scenario="duplicate-dlq",
        run=run,
        fault="duplicate-ingress-retained-dlq-unknown-retention",
        command_id=duplicate["commandId"],
        attempt_id=duplicate["attemptId"],
        workflow_run_id=duplicate_setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "duplicate",
            "recoveryAction": "recover",
            "sideEffectUncertainty": True,
        },
        materialization={
            "status": "indeterminate",
            "blocker": "unknown_retention",
            "recoveryAction": "recover",
            "manifestHash": None,
            "reconciliationRevision": unknown_retention["reconciliationRevision"],
            "pageSnapshotAsOf": unknown_retention.get("pageSnapshotAsOf"),
        },
    )


def _storage_loss_source_id(run: str, index: int, source: str) -> str:
    """Keep public source-binding identifiers within the API's 36-byte bound."""
    run_digest = hashlib.sha256(run.encode()).hexdigest()[:16]
    return f"loss-{index}-{source[:8]}-{run_digest}"


def _completed_hundred_exact(value: dict[str, Any]) -> bool:
    counts = value.get("counts")
    return (
        value.get("materializationStatus") == "completed"
        and isinstance(counts, dict)
        and counts.get("record_present") == 100
        and counts.get("unknown") == 0
        and isinstance(value.get("pageSnapshotAsOf"), str)
        and bool(value["pageSnapshotAsOf"])
    )


def query_page_race(run: str) -> dict[str, Any]:
    """Freeze a real ODP page snapshot between two correlated III ingresses."""
    stable_source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof-query-page/{run}"))
    keyword = f"query-page-{hashlib.sha256(run.encode()).hexdigest()[:16]}"
    hashes: dict[str, str] = {}
    with httpx.Client(timeout=60) as client:
        setup = public_setup(client, run)
        submission = public_submit(
            client,
            setup,
            source_id=keyword,
            stable_odp_source_id=stable_source_id,
            site="github",
            command="issues",
            idempotency_key="query-page-race",
        )
        _wait_for_expected_key_report(client, setup, submission["commandId"])
        before_actor, original_receipt = _wait_for_ingress_receipt(
            client, setup, submission["commandId"], timeout=30
        )

        page_gate_armed = False
        actor_futures = []
        executor = ThreadPoolExecutor(max_workers=3)
        try:
            _set_receipt_gate(client, "hold")
            actor_futures.append(
                executor.submit(
                    _actuate_correlated_ingress,
                    setup,
                    submission,
                    source_id=stable_source_id,
                    phase="pre_snapshot_101",
                    event_id=f"actor-101-{submission['attemptId']}",
                )
            )
            _wait_for_held_receipts(client, 1)

            _arm_query_page_gate(client, True)
            page_gate_armed = True

            def materialize() -> dict[str, Any]:
                with httpx.Client(timeout=60) as materialize_client:
                    return public_materialize(materialize_client, setup, submission["commandId"])

            materialization_future = executor.submit(materialize)
            _wait_for_query_page_gate(client)

            actor_futures.append(
                executor.submit(
                    _actuate_correlated_ingress,
                    setup,
                    submission,
                    source_id=stable_source_id,
                    phase="late_102",
                    event_id=f"actor-102-{submission['attemptId']}",
                )
            )
            _wait_for_held_receipts(client, 2)
            _release_query_page_gate(client)
            materialized = materialization_future.result(timeout=30)
            page_gate_armed = False

            recovered = materialized
            deadline = time.monotonic() + 30
            while not _completed_hundred_exact(recovered) and time.monotonic() < deadline:
                time.sleep(1)
                recovered = public_recover(client, setup, submission["commandId"])
            if not _completed_hundred_exact(recovered):
                raise RuntimeError(
                    "authenticated materialize/recover did not prove exact presence of 100 keys: "
                    + json.dumps(recovered, sort_keys=True)
                )
            final_status = public_status(client, setup, submission["commandId"])
            hashes.update(
                {
                    "collection_before_actor": _public_hash(before_actor),
                    "collection_after_materialization": _public_hash(final_status),
                    "materialization_after_page_release": _public_hash(materialized),
                    "materialization_after_recover": _public_hash(recovered),
                    "original_ingress_receipt": original_receipt,
                }
            )
            result = _failure_result(
                scenario="query-page-race",
                run=run,
                fault="real-odp-attempt-page-snapshot-race",
                command_id=submission["commandId"],
                attempt_id=submission["attemptId"],
                workflow_run_id=setup["runId"],
                hashes=hashes,
                collection={
                    "blockingStage": "none",
                    "recoveryAction": "recover",
                    "sideEffectUncertainty": False,
                },
                materialization={
                    "status": "completed",
                    "blocker": "none",
                    "recoveryAction": "recover",
                    "manifestHash": recovered.get("manifestHash"),
                    "reconciliationRevision": recovered["reconciliationRevision"],
                    "pageSnapshotAsOf": recovered["pageSnapshotAsOf"],
                },
            )
        finally:
            if page_gate_armed:
                _release_query_page_gate(client)
            _arm_query_page_gate(client, False)
            _set_receipt_gate(client, "forward")
            executor.shutdown(wait=True)
    return result


def ingest_redis_store_loss(run: str) -> dict[str, Any]:
    """Exercise four real loss seams while preserving only public API facts."""
    cases = (
        ("http-schema-mutator", "http-schema-mutator", "bilibili", "search"),
        ("ingest-redis-cut", "ingest-redis-cut", "youtube", "videos"),
        ("store-pg-cut", "store-pg-cut", "github", "issues"),
        ("store-redis-committed-xadd", "store-redis-committed-xadd", "reddit", "posts"),
    )
    observations: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=60) as client:
        setup = public_setup(client, run)
        for index, (gateway, source, site, command) in enumerate(cases):
            source_id = _storage_loss_source_id(run, index, source)
            if gateway == "store-redis-committed-xadd":
                marker = Path("/proof-artifacts/gateway-coordination/store-commit-ready")
                marker.unlink(missing_ok=True)
                time.sleep(1)
            _arm_gateway(client, gateway, True)
            try:
                submission = public_submit(
                    client, setup, source_id=source_id, site=site, command=command
                )
                command_id = submission["commandId"]
                initial_status = public_status(client, setup, command_id)
                if initial_status.get("commandId") != command_id:
                    raise RuntimeError("initial public status did not bind the submitted command")
                if gateway == "store-redis-committed-xadd":
                    deadline = time.monotonic() + 60
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.2)
                    if not marker.exists():
                        raise RuntimeError("store commit did not make notification loss eligible")
                initial_materialization = public_materialize(client, setup, command_id)
                if not isinstance(initial_materialization.get("reconciliationRevision"), int):
                    raise RuntimeError("initial materialization did not expose a revision")
            finally:
                _arm_gateway(client, gateway, False)
            recovered = public_recover(client, setup, command_id)
            if (
                not isinstance(recovered.get("reconciliationRevision"), int)
                or not recovered.get("materializationStatus")
                or recovered["reconciliationRevision"]
                < initial_materialization["reconciliationRevision"]
            ):
                raise RuntimeError("public recovery did not reconcile the initial public state")
            observations[gateway] = {
                "submission": submission,
                "initialStatus": initial_status,
                "initialMaterialization": initial_materialization,
                "materialization": recovered,
                "commandId": command_id,
                "attemptId": submission["attemptId"],
            }
    final = observations["store-redis-committed-xadd"]
    hashes = (
        {
            f"{name}_initial_status": _public_hash(value["initialStatus"])
            for name, value in observations.items()
        }
        | {
            f"{name}_initial_materialization": _public_hash(value["initialMaterialization"])
            for name, value in observations.items()
        }
        | {
            f"{name}_materialization": _public_hash(value["materialization"])
            for name, value in observations.items()
        }
    )
    final_status = final["materialization"]
    status = final_status.get("materializationStatus")
    normalized = (
        status
        if status
        in {
            "indeterminate",
            "completed_empty",
            "rejected",
            "unknown",
            "completed",
        }
        else "unknown"
    )
    return _failure_result(
        scenario="ingest-redis-store-loss",
        run=run,
        fault="ingest-redis-store-notification-loss",
        command_id=final["commandId"],
        attempt_id=final["attemptId"],
        workflow_run_id=setup["runId"],
        hashes=hashes,
        collection={
            "blockingStage": "ingress_unknown",
            "recoveryAction": "recover",
            "sideEffectUncertainty": True,
        },
        materialization={
            "status": normalized,
            "blocker": "none",
            "recoveryAction": "recover",
            "manifestHash": None,
            "reconciliationRevision": final_status["reconciliationRevision"],
            "pageSnapshotAsOf": final_status.get("pageSnapshotAsOf"),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    started_at = int(time.time())
    if args.scenario in {"admin-crash", "no-report", "signed-zero"}:
        result = admin_crash(args.run, args.scenario)
    elif args.scenario == "iii-unreachable":
        result = iii_unreachable(args.run)
    elif args.scenario == "crash-after-ingest":
        result = crash_after_ingest(args.run)
    elif args.scenario == "ingest-redis-store-loss":
        result = ingest_redis_store_loss(args.run)
    elif args.scenario == "duplicate-dlq":
        result = duplicate_dlq(args.run)
    elif args.scenario == "query-page-race":
        result = query_page_race(args.run)
    elif args.scenario == "graph-stale-auth-cas-retract":
        result = graph_stale_auth_cas_retract(args.run)
    elif args.scenario == "amendment-decision-conflict":
        result = amendment_decision_conflict(args.run)
    elif args.scenario == "receiver-recovery":
        result = receiver_recovery(args.run)
    elif args.scenario == "cancel-before-dispatch":
        result = cancel_before_dispatch(args.run)
    elif args.scenario == "cancel-in-flight":
        result = cancel_in_flight(args.run)
    else:
        raise RuntimeError("scenario driver is not implemented")
    completed_at = int(time.time())
    if completed_at - started_at > 360:
        raise RuntimeError("scenario dispatch exceeded the 360 second bound")
    result["timing"] = {
        "startedAt": started_at,
        "completedAt": completed_at,
        "deadlineSeconds": 360,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
