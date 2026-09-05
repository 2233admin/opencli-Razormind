"""Reusable scoped Admin/III collection fixtures for integration tests."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from backend.main import app
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.iii_collection import IIICollectionAttemptV1, IIICollectionCommandV1
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowValidationRun,
    StudioWorkflowVersion,
    StudioWorkspace,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas.iii_collection import (
    CollectorFinalExpectedKeyReportV1,
    ODPIngressOutcomeReceiptV1,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow.iii_collection_store import (
    _attempt_and_outbound,
    _expected_key_set_hash,
    _receipt_hash,
    _report_hash,
)


async def create_scoped_run(db_session):
    workspace = StudioWorkspace(id="iii-workspace", name="III", slug="iii")
    project = StudioProject(
        id="iii-project",
        workspace_id=workspace.id,
        name="III Project",
        slug="iii-project",
        created_by_user_id="operator",
    )
    workflow = StudioWorkflow(id="iii-workflow", project_id=project.id, name="III Workflow")
    validation = StudioWorkflowValidationRun(
        id="iii-validation",
        workflow_id=workflow.id,
        draft_revision=1,
        status="valid",
        valid=True,
        errors=[],
        warnings=[],
        compile_version="v1",
        resolved_graph={"nodes": [{"id": "opencli-source"}]},
    )
    version = StudioWorkflowVersion(
        id="iii-version",
        workflow_id=workflow.id,
        version=1,
        draft_revision=1,
        graph={"nodes": [{"id": "opencli-source"}]},
        compile_version="v1",
        validation_run_id=validation.id,
        published_by_user_id="operator",
        reason="test",
    )
    run = WorkflowRun(
        id="iii-run",
        workflow_id=workflow.id,
        studio_workflow_version_id=version.id,
        trace_id="iii-trace",
        status="queued",
        request={},
        projection={},
    )
    identity_workspace = Workspace(id=workspace.id, name="III", slug="iii")
    operator = User(id="iii-test-operator", subject="iii-test-operator")
    db_session.add_all(
        [
            workspace,
            project,
            workflow,
            validation,
            version,
            run,
            identity_workspace,
            operator,
            WorkspaceMembership(
                workspace_id=identity_workspace.id,
                user_id=operator.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await db_session.commit()

    async def override_identity() -> RequestIdentity:
        return RequestIdentity(subject=operator.subject)

    app.dependency_overrides[get_request_identity] = override_identity
    return {
        "workspace": workspace,
        "project": project,
        "workflow": workflow,
        "version": version,
        "run": run,
    }


def route(scope: dict) -> str:
    return (
        f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}"
        f"/workflows/{scope['workflow'].id}/runs/{scope['run'].id}/iii-collections"
    )


def submit_body() -> dict:
    return {
        "version": "v1",
        "idempotencyKey": "collection-key",
        "nodeId": "opencli-source",
        "collection": {
            "site": "bilibili",
            "command": "search",
            "args": {"keyword": "AI"},
            "sourceBindingId": "binding-1",
            "sourceBindingRevisionId": "binding-revision-1",
            "sourceBindingRevisionNumber": 1,
        },
    }


def _fact_identity(command, attempt) -> dict:
    return {
        "version": "v1",
        "workspaceId": command.workspace_id,
        "projectId": command.project_id,
        "workflowId": command.workflow_id,
        "studioWorkflowVersionId": command.studio_workflow_version_id,
        "runId": command.run_id,
        "nodeId": command.node_id,
        "commandId": command.id,
        "attemptId": attempt.id,
        "attemptNumber": attempt.attempt_number,
        "taskId": attempt.task_id,
        "traceId": attempt.trace_id,
        "sourceId": command.odp_source_id,
        "sourceBindingId": command.source_binding_id,
        "sourceBindingRevisionId": command.source_binding_revision_id,
        "sourceBindingRevisionNumber": command.source_binding_revision_number,
        "payloadSha256": command.payload_sha256,
    }


def report_body(
    command,
    attempt,
    *,
    event_id: str | None = "event-1",
    event_ids: list[str] | None = None,
    rejected_count: int = 0,
) -> dict:
    expected_keys = (
        [{"sourceId": command.odp_source_id, "eventId": value} for value in event_ids]
        if event_ids is not None
        else [{"sourceId": command.odp_source_id, "eventId": event_id}]
        if event_id is not None
        else []
    )
    body = {
        **_fact_identity(command, attempt),
        "reportId": "report-1",
        "reportSequence": 1,
        "expectedKeys": expected_keys,
        "expectedKeySetSha256": "0" * 64,
        "itemCount": len(expected_keys),
        "zeroCount": int(not expected_keys),
        "rejectedCount": rejected_count,
        "reportedAt": datetime(2026, 8, 30, tzinfo=UTC).isoformat(),
        "reportHash": "0" * 64,
    }
    report = CollectorFinalExpectedKeyReportV1.model_validate(body)
    body["expectedKeySetSha256"] = _expected_key_set_hash(report)
    report = CollectorFinalExpectedKeyReportV1.model_validate(body)
    body["reportHash"] = _report_hash(report)
    return body


def sign_receipt_body(body: dict) -> dict:
    signed = {
        **body,
        "receiptHash": "0" * 64,
        "signature": "sha256=placeholder",
    }
    receipt = ODPIngressOutcomeReceiptV1.model_validate(signed)
    signed["receiptHash"] = _receipt_hash(receipt)
    signed["signature"] = (
        "sha256="
        + hmac.new(b"receipt-secret", signed["receiptHash"].encode(), hashlib.sha256).hexdigest()
    )
    return signed


def receipt_body(command, attempt, report: dict) -> dict:
    return sign_receipt_body(
        {
            **_fact_identity(command, attempt),
            "receiptId": "receipt-1",
            "idempotencyKey": "odp-ingest:receipt-1",
            "producerId": "odp-ingest",
            "producerKeyId": "odp-ingest-v1",
            "expectedKeySetSha256": report["expectedKeySetSha256"],
            "outcomes": [
                {
                    "sourceId": key["sourceId"],
                    "eventId": key["eventId"],
                    "outcome": "accepted",
                }
                for key in report["expectedKeys"]
            ],
            "issuedAt": datetime(2026, 8, 30, tzinfo=UTC).isoformat(),
        }
    )


async def submit_report_and_receipt(
    client,
    db_session,
    monkeypatch,
    *,
    outcome: str = "accepted",
    event_ids: list[str] | None = None,
):
    scope = await create_scoped_run(db_session)
    scope["run"].trace_id = str(uuid.uuid4())
    await db_session.commit()

    async def no_dispatch(db, *, command):
        _, outbound = await _attempt_and_outbound(db, command.id)
        return outbound

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(
            iii_lifecycle_token="bridge-token",
            iii_ingress_receipt_secret="receipt-secret",
        ),
    )
    monkeypatch.setattr(
        "backend.workflow.iii_collection_store.get_settings",
        lambda: SimpleNamespace(iii_ingress_receipt_secret="receipt-secret"),
    )
    submitted = await client.post(route(scope), json=submit_body())
    command = await db_session.get(IIICollectionCommandV1, submitted.json()["data"]["commandId"])
    attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
    assert command is not None and attempt is not None
    report = report_body(
        command,
        attempt,
        event_ids=event_ids,
        rejected_count=int(outcome == "rejected"),
    )
    headers = {"x-iii-bridge-token": "bridge-token"}
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports",
            json=report,
            headers=headers,
        )
    ).status_code == 200
    receipt = receipt_body(command, attempt, report)
    if outcome == "rejected":
        receipt["outcomes"][0] = {
            "sourceId": command.odp_source_id,
            "eventId": "event-1",
            "outcome": "rejected",
            "rejectionReason": "validation_failed",
        }
        receipt = sign_receipt_body(receipt)
    assert (
        await client.post(
            "/api/v1/iii-collections/ingress-receipts",
            json=receipt,
            headers=headers,
        )
    ).status_code == 200
    return scope, command
