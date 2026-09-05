"""Highest scoped Admin/III seam tests for the durable collection vertical."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from backend.models.iii_collection import (
    IIICollectionAttemptV1,
    IIICollectionCommandV1,
    IIICollectionExpectedKeyReportV1,
    IIICollectionIngressReceiptV1,
    IIICollectionLifecycleObservationV1,
    IIICollectionOutboundV1,
)
from backend.models.workflow_run import WorkflowRunEvent
from backend.schemas.iii_collection import IIICollectionRequestV1
from backend.workflow.iii_collection_dispatch import (
    IIIBridgeUnavailableError,
    collector_trigger_payload,
    dispatch_collection_attempt,
)
from backend.workflow.iii_collection_store import (
    CollectionScope,
    _attempt_and_outbound,
    cancel_collection,
    submit_collection,
)
from tests.integration.iii_collection_test_support import (
    create_scoped_run as _create_scoped_run,
)
from tests.integration.iii_collection_test_support import (
    receipt_body as _receipt_body,
)
from tests.integration.iii_collection_test_support import (
    report_body as _report_body,
)
from tests.integration.iii_collection_test_support import (
    route as _route,
)
from tests.integration.iii_collection_test_support import (
    sign_receipt_body as _sign_receipt_body,
)
from tests.integration.iii_collection_test_support import (
    submit_body as _submit_body,
)


@pytest.mark.asyncio
async def test_submit_commits_admin_ledger_before_iii_trigger(client, db_session, monkeypatch):
    scope = await _create_scoped_run(db_session)
    calls: list[dict] = []
    commits = 0
    original_commit = db_session.commit

    async def commit_with_boundary() -> None:
        nonlocal commits
        commits += 1
        await original_commit()

    monkeypatch.setattr(db_session, "commit", commit_with_boundary)


    async def fake_dispatch(db, *, command):
        attempt = (
            await db.execute(
                select(IIICollectionAttemptV1).where(
                    IIICollectionAttemptV1.command_id == command.id
                )
            )
        ).scalar_one()
        outbound = (
            await db.execute(
                select(IIICollectionOutboundV1).where(
                    IIICollectionOutboundV1.attempt_id == attempt.id
                )
            )
        ).scalar_one()
        event = (
            await db.execute(
                select(WorkflowRunEvent).where(WorkflowRunEvent.run_id == command.run_id)
            )
        ).scalar_one()
        assert commits == 1
        assert outbound.state == "pending"
        assert event.payload["details"]["iiiCollection"]["stage"] == "admin_requested"
        payload = collector_trigger_payload(command, attempt)
        assert payload["task_id"] == attempt.task_id
        assert payload["trace_id"] == scope["run"].trace_id
        assert payload["admin_collection"]["payload_sha256"] == command.payload_sha256
        calls.append(payload)
        return outbound

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", fake_dispatch)
    response = await client.post(_route(scope), json=_submit_body())

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["created"] is True
    assert len(calls) == 1
    assert calls[0]["site"] == "bilibili"
    assert calls[0]["command"] == "search"


@pytest.mark.asyncio
async def test_pending_resume_reuses_same_attempt_and_precommit_failure_never_dispatches(
    db_session, monkeypatch
):
    scope_rows = await _create_scoped_run(db_session)
    scope = CollectionScope(
        workspace_id=scope_rows["workspace"].id,
        project_id=scope_rows["project"].id,
        workflow_id=scope_rows["workflow"].id,
        studio_workflow_version_id=scope_rows["version"].id,
        run_id=scope_rows["run"].id,
    )
    collection = IIICollectionRequestV1(
        site="bilibili",
        command="search",
        args={"keyword": "AI"},
    )
    submitted = await submit_collection(
        db_session,
        scope=scope,
        run=scope_rows["run"],
        node_id="opencli-source",
        idempotency_key="restart-key",
        collection=collection,
    )
    captured: list[dict] = []

    async def fake_invoke(payload, *, function_id):
        assert function_id == "odp.collect::opencli_snapshot"
        captured.append(payload)

    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.invoke_iii_collection",
        fake_invoke,
    )
    resumed = await dispatch_collection_attempt(db_session, command=submitted.command)
    assert resumed.state == "submitted_to_iii"
    assert len(captured) == 1
    assert captured[0]["task_id"] == submitted.attempt.task_id
    assert captured[0]["admin_collection"]["attempt_id"] == submitted.attempt.id
    assert captured[0]["admin_collection"]["payload_sha256"] == submitted.command.payload_sha256

    before = len(captured)
    with pytest.raises(Exception):
        await submit_collection(
            db_session,
            scope=CollectionScope(
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                workflow_id=scope.workflow_id,
                studio_workflow_version_id=scope.studio_workflow_version_id,
                run_id="missing-run",
            ),
            run=scope_rows["run"],
            node_id="opencli-source",
            idempotency_key="precommit-failure",
            collection=collection,
        )
    await db_session.rollback()
    assert len(captured) == before


@pytest.mark.asyncio
async def test_lifecycle_replay_conflict_unavailable_status_and_redaction(
    client, db_session, monkeypatch
):
    scope = await _create_scoped_run(db_session)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    lifecycle_headers = {"x-iii-bridge-token": "bridge-token"}


    async def unavailable(_payload, *, function_id):
        assert function_id == "odp.collect::opencli_snapshot"
        raise IIIBridgeUnavailableError("offline")

    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.invoke_iii_collection",
        unavailable,
    )
    submit_response = await client.post(_route(scope), json=_submit_body())
    assert submit_response.status_code == 202
    submit = submit_response.json()["data"]
    command = await db_session.get(IIICollectionCommandV1, submit["commandId"])
    assert command is not None
    attempt = await db_session.get(IIICollectionAttemptV1, submit["attemptId"])
    assert attempt is not None
    unavailable_status = await client.get(f"{_route(scope)}/{command.id}")
    assert unavailable_status.status_code == 200
    assert unavailable_status.json()["data"]["state"] == "bridge_unavailable"
    assert unavailable_status.json()["data"]["recoveryAction"] == "resume_dispatch"


    lifecycle = {
        "version": "v1",
        "workspace_id": command.workspace_id,
        "project_id": command.project_id,
        "workflow_id": command.workflow_id,
        "studio_workflow_version_id": command.studio_workflow_version_id,
        "run_id": command.run_id,
        "node_id": command.node_id,
        "command_id": command.id,
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "task_id": attempt.task_id,
        "trace_id": attempt.trace_id,
        "source_id": command.odp_source_id,
        "source_binding_id": command.source_binding_id,
        "source_binding_revision_id": command.source_binding_revision_id,
        "source_binding_revision_number": command.source_binding_revision_number,
        "payload_sha256": command.payload_sha256,
        "sequence": 1,
        "event_type": "bridge_accepted",
        "summary": {},
    }
    first = await client.post(
        "/api/v1/iii-collections/lifecycle", json=lifecycle, headers=lifecycle_headers
    )
    assert first.status_code == 200
    first_event_hash = first.json()["data"]["eventHash"]
    assert len(first_event_hash) == 64
    assert first.json()["data"]["duplicate"] is False
    replay = await client.post(
        "/api/v1/iii-collections/lifecycle", json=lifecycle, headers=lifecycle_headers
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["eventHash"] == first_event_hash
    assert replay.json()["data"]["duplicate"] is True
    changed = {**lifecycle, "summary": {"items_fetched": 1}}
    conflict = await client.post(
        "/api/v1/iii-collections/lifecycle", json=changed, headers=lifecycle_headers
    )
    assert conflict.status_code == 409
    started = {
        **lifecycle,
        "sequence": 2,
        "event_type": "collector_started",
    }
    returned = {
        **lifecycle,
        "sequence": 3,
        "event_type": "collector_returned",
        "summary": {"items_fetched": 0},
    }
    assert (
        await client.post(
            "/api/v1/iii-collections/lifecycle", json=started, headers=lifecycle_headers
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/iii-collections/lifecycle", json=returned, headers=lifecycle_headers
        )
    ).status_code == 200


    status_response = await client.get(f"{_route(scope)}/{command.id}")
    assert status_response.status_code == 200
    vertical = status_response.json()["data"]
    assert vertical["state"] == "collector_returned"
    assert vertical["blockingStage"] == "collector_report"
    assert vertical["recoveryAction"] == "await_collector_report"
    assert vertical["sideEffectUncertainty"] is True
    rendered = status_response.text
    assert "bilibili" not in rendered
    assert "keyword" not in rendered
    assert "admin_command_json" not in rendered
    lifecycle_refs = {
        reference["eventType"]: reference["hash"]
        for reference in vertical["evidenceReferences"]
        if reference["kind"] == "lifecycle"
    }
    assert set(lifecycle_refs) == {
        "bridge_accepted", "collector_started", "collector_returned",
    }
    assert lifecycle_refs["bridge_accepted"] == first_event_hash
    assert all(len(value) == 64 for value in lifecycle_refs.values())
    persisted = (
        await db_session.execute(
            select(IIICollectionLifecycleObservationV1).where(
                IIICollectionLifecycleObservationV1.command_id == command.id
            )
        )
    ).scalars().all()
    assert len(persisted) == 3


@pytest.mark.asyncio
async def test_cancellation_before_dispatch_never_invokes_iii(db_session, monkeypatch):
    scope_rows = await _create_scoped_run(db_session)
    scope = CollectionScope(
        workspace_id=scope_rows["workspace"].id,
        project_id=scope_rows["project"].id,
        workflow_id=scope_rows["workflow"].id,
        studio_workflow_version_id=scope_rows["version"].id,
        run_id=scope_rows["run"].id,
    )
    submitted = await submit_collection(
        db_session,
        scope=scope,
        run=scope_rows["run"],
        node_id="opencli-source",
        idempotency_key="cancel-key",
        collection=IIICollectionRequestV1(site="bilibili", command="search"),
    )
    await cancel_collection(db_session, command=submitted.command)
    invoked = False

    async def should_not_invoke(_payload, *, function_id):
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.invoke_iii_collection",
        should_not_invoke,
    )
    outbound = await dispatch_collection_attempt(db_session, command=submitted.command)
    assert outbound.state == "cancelled"
    assert invoked is False


@pytest.mark.asyncio
async def test_lifecycle_ingress_requires_bridge_token_and_rejects_scope_hash_conflicts(
    client, db_session, monkeypatch
):
    scope = await _create_scoped_run(db_session)

    async def no_dispatch(_db, *, command):
        _, outbound = await _attempt_and_outbound(_db, command.id)
        return outbound

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    submitted = await client.post(_route(scope), json=_submit_body())
    assert submitted.status_code == 202
    command = await db_session.get(IIICollectionCommandV1, submitted.json()["data"]["commandId"])
    attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
    assert command is not None and attempt is not None
    lifecycle = {
        "version": "v1",
        "workspace_id": command.workspace_id,
        "project_id": command.project_id,
        "workflow_id": command.workflow_id,
        "studio_workflow_version_id": command.studio_workflow_version_id,
        "run_id": command.run_id,
        "node_id": command.node_id,
        "command_id": command.id,
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "task_id": attempt.task_id,
        "trace_id": attempt.trace_id,
        "source_id": command.odp_source_id,
        "source_binding_id": command.source_binding_id,
        "source_binding_revision_id": command.source_binding_revision_id,
        "source_binding_revision_number": command.source_binding_revision_number,
        "payload_sha256": command.payload_sha256,
        "sequence": 1,
        "event_type": "bridge_accepted",
        "summary": {},
    }

    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token=""),
    )
    assert (
        await client.post("/api/v1/iii-collections/lifecycle", json=lifecycle)
    ).status_code == 401
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    assert (
        await client.post("/api/v1/iii-collections/lifecycle", json=lifecycle)
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/iii-collections/lifecycle",
            json=lifecycle,
            headers={"x-iii-bridge-token": "wrong-token"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/iii-collections/lifecycle",
            json=lifecycle,
            headers={"x-iii-bridge-token": "bridge-token"},
        )
    ).status_code == 200

    wrong_hash = {
        **lifecycle,
        "sequence": 2,
        "event_type": "collector_started",
        "payload_sha256": "0" * 64,
    }
    assert (
        await client.post(
            "/api/v1/iii-collections/lifecycle",
            json=wrong_hash,
            headers={"x-iii-bridge-token": "bridge-token"},
        )
    ).status_code == 409
    assert (
        await client.get(
            f"/api/v1/workspaces/other/projects/{scope['project'].id}/workflows/"
            f"{scope['workflow'].id}/runs/{scope['run'].id}/iii-collections/{command.id}"
        )
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}/workflows/"
            f"{scope['workflow'].id}/runs/other-run/iii-collections/{command.id}"
        )
    ).status_code == 404

@pytest.mark.asyncio
async def test_signed_receipts_and_expected_reports_are_replay_safe_and_nonterminal(
    client, db_session, monkeypatch
):
    scope = await _create_scoped_run(db_session)

    async def no_dispatch(_db, *, command):
        _, outbound = await _attempt_and_outbound(_db, command.id)
        return outbound

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(
            iii_lifecycle_token="bridge-token", iii_ingress_receipt_secret="receipt-secret"
        ),
    )
    monkeypatch.setattr(
        "backend.workflow.iii_collection_store.get_settings",
        lambda: SimpleNamespace(iii_ingress_receipt_secret="receipt-secret"),
    )
    submitted = await client.post(_route(scope), json=_submit_body())
    assert submitted.status_code == 202
    command = await db_session.get(IIICollectionCommandV1, submitted.json()["data"]["commandId"])
    attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
    assert command is not None and attempt is not None
    headers = {"x-iii-bridge-token": "bridge-token"}
    report = _report_body(command, attempt)
    receipt = _receipt_body(command, attempt, report)

    # Receipt-first evidence is retained but cannot claim collection completion.
    first_receipt = await client.post(
        "/api/v1/iii-collections/ingress-receipts", json=receipt, headers=headers
    )
    assert first_receipt.status_code == 200
    assert first_receipt.json()["data"]["duplicate"] is False
    before_report = await client.get(f"{_route(scope)}/{command.id}")
    assert before_report.json()["data"]["blockingStage"] == "collector_report"
    assert before_report.json()["data"]["sideEffectUncertainty"] is True

    report_response = await client.post(
        "/api/v1/iii-collections/expected-key-reports", json=report, headers=headers
    )
    assert report_response.status_code == 200
    assert report_response.json()["data"]["duplicate"] is False
    status_response = await client.get(f"{_route(scope)}/{command.id}")
    status = status_response.json()["data"]
    assert status["blockingStage"] == "reconciliation"
    assert status["sideEffectUncertainty"] is True
    assert "event-1" not in status_response.text
    assert "receipt-secret" not in status_response.text
    assert len(
        (
            await db_session.execute(
                select(IIICollectionExpectedKeyReportV1).where(
                    IIICollectionExpectedKeyReportV1.attempt_id == attempt.id
                )
            )
        ).scalars().all()
    ) == 1
    assert len(
        (
            await db_session.execute(
                select(IIICollectionIngressReceiptV1).where(
                    IIICollectionIngressReceiptV1.attempt_id == attempt.id
                )
            )
        ).scalars().all()
    ) == 1

    assert (
        await client.post(
            "/api/v1/iii-collections/ingress-receipts", json=receipt, headers=headers
        )
    ).json()["data"]["duplicate"] is True
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports", json=report, headers=headers
        )
    ).json()["data"]["duplicate"] is True

    tampered = {**receipt, "signature": "sha256=" + "0" * 64}
    assert (
        await client.post(
            "/api/v1/iii-collections/ingress-receipts", json=tampered, headers=headers
        )
    ).status_code == 409
    changed_report = {**report, "itemCount": 2}
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports", json=changed_report, headers=headers
        )
    ).status_code == 422

    duplicate_outcome = _sign_receipt_body(
        {
            **receipt,
            "receiptId": "receipt-duplicate-key",
            "idempotencyKey": "odp-ingest:receipt-duplicate-key",
            "outcomes": receipt["outcomes"] * 2,
        }
    )
    assert (
        await client.post(
            "/api/v1/iii-collections/ingress-receipts", json=duplicate_outcome, headers=headers
        )
    ).status_code == 409
    mismatched_rejected_count = _sign_receipt_body(
        {
            **receipt,
            "receiptId": "receipt-rejected-key",
            "idempotencyKey": "odp-ingest:receipt-rejected-key",
            "outcomes": [
                {
                    **receipt["outcomes"][0],
                    "outcome": "rejected",
                    "rejectionReason": "validation failed",
                }
            ],
        }
    )
    assert (
        await client.post(
            "/api/v1/iii-collections/ingress-receipts",
            json=mismatched_rejected_count,
            headers=headers,
        )
    ).status_code == 409
    wrong_producer = _sign_receipt_body(
        {
            **receipt,
            "receiptId": "receipt-wrong-producer",
            "idempotencyKey": "other:receipt-wrong-producer",
            "producerId": "other-producer",
        }
    )
    assert (
        await client.post(
            "/api/v1/iii-collections/ingress-receipts", json=wrong_producer, headers=headers
        )
    ).status_code == 409

    duplicate_key_report = {
        **report,
        "reportId": "report-duplicate-key",
        "expectedKeys": report["expectedKeys"] * 2,
        "itemCount": 2,
        "reportHash": "0" * 64,
    }
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports",
            json=duplicate_key_report,
            headers=headers,
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_zero_expected_key_report_without_receipt_remains_reconciling(
    client, db_session, monkeypatch
):
    scope = await _create_scoped_run(db_session)

    async def no_dispatch(_db, *, command):
        _, outbound = await _attempt_and_outbound(_db, command.id)
        return outbound

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    submitted = await client.post(_route(scope), json=_submit_body())
    assert submitted.status_code == 202
    command = await db_session.get(IIICollectionCommandV1, submitted.json()["data"]["commandId"])
    attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
    assert command is not None and attempt is not None
    report = _report_body(command, attempt, event_id=None)
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports",
            json=report,
            headers={"x-iii-bridge-token": "bridge-token"},
        )
    ).status_code == 200
    status_response = await client.get(f"{_route(scope)}/{command.id}")
    assert status_response.status_code == 200
    status = status_response.json()["data"]
    assert status["blockingStage"] == "reconciliation"



@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ODP_INGEST_INTEROP_URL"),
    reason="requires real odp-ingest interop endpoint",
)
async def test_real_odp_ingest_receipts_are_accepted_by_admin(client, db_session, monkeypatch):
    scope = await _create_scoped_run(db_session)

    async def no_dispatch(_db, *, command):
        _, outbound = await _attempt_and_outbound(_db, command.id)
        return outbound

    secret = os.environ["III_INGRESS_RECEIPT_SECRET"]
    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    monkeypatch.setattr(
        "backend.workflow.iii_collection_store.get_settings",
        lambda: SimpleNamespace(iii_ingress_receipt_secret=secret),
    )
    submitted = await client.post(_route(scope), json=_submit_body())
    command = await db_session.get(IIICollectionCommandV1, submitted.json()["data"]["commandId"])
    attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
    assert command is not None and attempt is not None
    event_id = f"interop-{uuid.uuid4()}"
    report = _report_body(command, attempt, event_id=event_id)
    context = {
        "workspace_id": str(command.workspace_id),
        "project_id": str(command.project_id),
        "workflow_id": str(command.workflow_id),
        "studio_workflow_version_id": str(command.studio_workflow_version_id),
        "node_id": command.node_id,
        "run_id": str(command.run_id),
        "command_id": str(command.id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "task_id": attempt.task_id,
        "trace_id": attempt.trace_id,
        "source_id": command.odp_source_id,
        "source_binding_id": str(command.source_binding_id) if command.source_binding_id else None,
        "source_binding_revision_id": (
            str(command.source_binding_revision_id) if command.source_binding_revision_id else None
        ),
        "source_binding_revision_number": command.source_binding_revision_number,
        "payload_sha256": command.payload_sha256,
        "expected_key_set_sha256": report["expectedKeySetSha256"],
    }
    event = {
        "schema_version": 1,
        "provider": "interop",
        "source_id": command.odp_source_id,
        "event_id": event_id,
        "ingest_mode": "snapshot",
        "source_ts": datetime.now(UTC).isoformat(),
        "payload": {},
    }
    async with httpx.AsyncClient() as odp:
        first = await odp.post(
            f"{os.environ['ODP_INGEST_INTEROP_URL'].rstrip('/')}/v1/ingest/batch",
            json={"events": [event], "receipt_context": context},
        )
        second = await odp.post(
            f"{os.environ['ODP_INGEST_INTEROP_URL'].rstrip('/')}/v1/ingest/batch",
            json={"events": [event], "receipt_context": context},
        )
    first.raise_for_status()
    second.raise_for_status()
    first_response = first.json()
    second_response = second.json()
    assert first_response["outcomes"] == []
    assert second_response["outcomes"] == []
    first_receipt = first_response["ingress_receipt"]
    second_receipt = second_response["ingress_receipt"]
    assert first_receipt["outcomes"][0] == {
        "source_id": command.odp_source_id,
        "event_id": event_id,
        "outcome": "accepted",
        "rejection_reason": None,
    }
    assert second_receipt["outcomes"][0]["outcome"] == "duplicate"
    assert second_receipt["outcomes"][0]["rejection_reason"] is None
    headers = {"x-iii-bridge-token": "bridge-token"}
    for receipt in (first_receipt, second_receipt):
        assert (
            await client.post(
                "/api/v1/iii-collections/ingress-receipts",
                json=receipt,
                headers=headers,
            )
        ).status_code == 200
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports",
            json=report,
            headers=headers,
        )
    ).status_code == 200
    status = (await client.get(f"{_route(scope)}/{command.id}")).json()["data"]
    assert status["blockingStage"] == "reconciliation"
    assert status["recoveryAction"] == "await_reconciliation"
    assert status["sideEffectUncertainty"] is True
