"""Public Admin/Studio EvidenceBatch materialization contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.main import app
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.iii_collection import (
    EvidenceBatchMaterializationManifestV1,
    IIICollectionAttemptV1,
    IIICollectionCommandV1,
    IIICollectionExpectedKeyReportV1,
    IIICollectionIngressReceiptV1,
)
from backend.odp.query_client import OdpQueryUnavailable, OdpRecordKey
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow.evidence_batch_materialization_facts import receipt_outcomes
from backend.workflow.evidence_batch_materializer import (
    get_materialization,
    materialize_evidence_batch,
)
from backend.workflow.iii_collection_store import (
    CollectionScope,
    IIICollectionNotFoundError,
    _attempt_and_outbound,
)
from tests.integration.iii_collection_test_support import (
    create_scoped_run,
    receipt_body,
    report_body,
    route,
    sign_receipt_body,
    submit_body,
    submit_report_and_receipt,
)


def _record(command, event_id: str = "event-1", record_id: int = 42) -> dict:
    return {
        "source_id": command.odp_source_id,
        "event_id": event_id,
        "odp_record_id": record_id,
        "committed_at": "2026-08-30T00:00:00Z",
        "provider": "test",
        "source_ts": "2026-08-30T00:00:00Z",
    }


def _base(request) -> dict:
    return {
        "query_fingerprint": request["delegation"]["query_fingerprint"],
        "retention_state": "unknown",
        "redaction_profile_version": "odp-query-reference-v1",
    }


def _research_graph_route(scope: dict) -> str:
    return (
        f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}"
        f"/workflows/{scope['workflow'].id}/runs/{scope['run'].id}/research-graph-v2"
    )


def _completed_query(command, calls: list[str] | None = None):
    record = _record(command)

    async def query(request):
        if calls is not None:
            calls.append(request["mode"])
        if request["mode"] == "exact":
            return {
                **_base(request),
                "mode": "exact",
                "records": [record],
                "results": [
                    {
                        "key": {"source_id": command.odp_source_id, "event_id": "event-1"},
                        "classification": "present",
                        "retention_state": "unknown",
                        "record": record,
                    }
                ],
            }
        return {
            **_base(request),
            "mode": "attempt_page",
            "records": [record],
            "results": [],
            "as_of": "2026-08-30T00:00:00Z",
        }

    return query


def _stored_manifest(command, attempt, *, batch_id: str, revision: int, marker: int):
    return EvidenceBatchMaterializationManifestV1(
        version="v1",
        batch_id=batch_id,
        derivation="test",
        reconciliation_revision=revision,
        workspace_id=command.workspace_id,
        project_id=command.project_id,
        workflow_id=command.workflow_id,
        studio_workflow_version_id=command.studio_workflow_version_id,
        run_id=command.run_id,
        node_id=command.node_id,
        command_id=command.id,
        attempt_id=attempt.id,
        task_id=attempt.task_id,
        trace_id=attempt.trace_id,
        source_binding_id=command.source_binding_id,
        source_binding_revision_id=command.source_binding_revision_id,
        report_id=None,
        report_hash=None,
        expected_key_set_hash=None,
        receipt_hashes=[],
        query_fingerprint=None,
        page_snapshot_as_of=None,
        redaction_profile_version=None,
        item_count=1,
        counts={
            "expected": 1,
            "record_present": 1,
            "inserted": 0,
            "duplicate_existing": 0,
            "rejected": 0,
            "dlq": 0,
            "unknown": 0,
        },
        materialization_status="completed",
        record_references=[
            {
                "source_id": command.odp_source_id,
                "event_id": f"event-{marker}",
                "odp_record_id": marker,
                "committed_at": "2026-08-30T00:00:00Z",
            }
        ],
        retention_state="unknown",
        finalization_reason="exact_presence_reconciled",
        finalized_at=datetime(2026, 8, 30, tzinfo=UTC),
        manifest_hash=f"{marker:064x}",
    )


def test_rejected_and_non_rejected_receipts_remain_indeterminate():
    key = OdpRecordKey(UUID("11111111-1111-4111-8111-111111111111"), "event-1")
    accepted = SimpleNamespace(
        outcomes=[
            {
                "source_id": str(key.source_id),
                "event_id": key.event_id,
                "outcome": "accepted",
            }
        ]
    )
    rejected = SimpleNamespace(
        outcomes=[
            {
                "source_id": str(key.source_id),
                "event_id": key.event_id,
                "outcome": "rejected",
            }
        ]
    )
    assert receipt_outcomes([accepted, rejected], [key]) is None


@pytest.mark.asyncio
async def test_materialization_requires_exact_presence_and_projects_completed(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    record = _record(command)

    async def query(request):
        if request["mode"] == "exact":
            return {
                **_base(request),
                "mode": "exact",
                "records": [record],
                "results": [
                    {
                        "key": {"source_id": command.odp_source_id, "event_id": "event-1"},
                        "classification": "present",
                        "retention_state": "unknown",
                        "record": record,
                    }
                ],
            }
        return {
            **_base(request),
            "mode": "attempt_page",
            "records": [record],
            "results": [],
            "as_of": "2026-08-30T00:00:00Z",
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        query,
    )
    response = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["materializationStatus"] == "completed"
    assert data["legacyStatus"] == "completed"
    assert data["recordReferences"] == [
        {
            "sourceId": command.odp_source_id,
            "eventId": "event-1",
            "odpRecordId": 42,
            "committedAt": "2026-08-30T00:00:00Z",
        }
    ]
    assert "receipt-secret" not in response.text


@pytest.mark.asyncio
async def test_declared_successful_zero_materializes_without_odp_query(
    client, db_session, monkeypatch
):
    scope = await create_scoped_run(db_session)

    async def no_dispatch(db, *, command):
        _, outbound = await _attempt_and_outbound(db, command.id)
        return outbound

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    submitted = await client.post(route(scope), json=submit_body())
    command = await db_session.get(IIICollectionCommandV1, submitted.json()["data"]["commandId"])
    attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
    assert command is not None and attempt is not None
    headers = {"x-iii-bridge-token": "bridge-token"}
    assert (
        await client.post(
            "/api/v1/iii-collections/expected-key-reports",
            json=report_body(command, attempt, event_id=None),
            headers=headers,
        )
    ).status_code == 200

    async def must_not_query(_request):
        raise AssertionError("successful zero must not be inferred from ODP")

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        must_not_query,
    )
    response = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert response.status_code == 200
    assert response.json()["data"]["materializationStatus"] == "completed_empty"
    assert response.json()["data"]["legacyStatus"] == "completed"


@pytest.mark.asyncio
async def test_materialization_outage_is_indeterminate_and_recovery_appends_revision(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    recovered = False

    async def query(request):
        if request["mode"] == "attempt_page":
            if not recovered:
                raise OdpQueryUnavailable("cursor snapshot unavailable")
            return {
                **_base(request),
                "mode": "attempt_page",
                "records": [],
                "results": [],
                "as_of": "2026-08-30T00:00:00Z",
            }
        record = _record(command)
        return {
            **_base(request),
            "mode": "exact",
            "records": [record],
            "results": [
                {
                    "key": {
                        "source_id": command.odp_source_id,
                        "event_id": "event-1",
                    },
                    "classification": "present",
                    "retention_state": "unknown",
                    "record": record,
                }
            ],
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        query,
    )
    first = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert first.status_code == 200
    assert first.json()["data"]["materializationStatus"] == "indeterminate"
    assert first.json()["data"]["legacyStatus"] == "blocked"
    assert first.json()["data"]["recoveryAction"] == "reconcile_evidence_batch"
    batch_id = first.json()["data"]["batchId"]
    studio_run = route(scope).removesuffix("/iii-collections")
    detail = await client.get(f"{studio_run}/evidence-batches/v1/{batch_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["researchGraphManifestRef"] is None

    recovered = True
    second = await client.post(f"{route(scope)}/{command.id}/recover")
    assert second.status_code == 200
    assert second.json()["data"]["materializationStatus"] == "completed"
    assert second.json()["data"]["reconciliationRevision"] == 2


@pytest.mark.asyncio
async def test_explicit_signed_rejection_materializes_partial_not_completed(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(
        client, db_session, monkeypatch, outcome="rejected"
    )

    async def query(request):
        assert request["mode"] == "attempt_page"
        return {
            **_base(request),
            "mode": "attempt_page",
            "records": [],
            "results": [],
            "as_of": "2026-08-30T00:00:00Z",
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        query,
    )
    response = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["materializationStatus"] == "partial"
    assert data["legacyStatus"] == "partial"
    assert data["counts"]["rejected"] == 1
    assert data["recordReferences"] == []


@pytest.mark.asyncio
async def test_materialization_chunks_exact_keys_with_one_scope_fingerprint(
    client, db_session, monkeypatch
):
    event_ids = [f"event-{index}" for index in range(101)]
    scope, command = await submit_report_and_receipt(
        client, db_session, monkeypatch, event_ids=event_ids
    )
    exact_requests: list[dict] = []

    async def query(request):
        if request["mode"] == "attempt_page":
            return {
                **_base(request),
                "mode": "attempt_page",
                "records": [],
                "results": [],
                "as_of": "2026-08-30T00:00:00Z",
            }
        exact_requests.append(request)
        records = [
            {
                "source_id": key["source_id"],
                "event_id": key["event_id"],
                "odp_record_id": index + 1,
                "committed_at": "2026-08-30T00:00:00Z",
                "provider": "test",
                "source_ts": "2026-08-30T00:00:00Z",
            }
            for index, key in enumerate(request["keys"])
        ]
        return {
            **_base(request),
            "mode": "exact",
            "records": records,
            "results": [
                {
                    "key": {
                        "source_id": record["source_id"],
                        "event_id": record["event_id"],
                    },
                    "classification": "present",
                    "retention_state": "unknown",
                    "record": record,
                }
                for record in records
            ],
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        query,
    )
    response = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["materializationStatus"] == "completed"
    assert data["recordCount"] == 101
    assert len(data["recordReferences"]) == 101
    assert [len(request["keys"]) for request in exact_requests] == [100, 1]
    assert len({request["delegation"]["query_fingerprint"] for request in exact_requests}) == 1
    assert data["pageSnapshotAsOf"] == "2026-08-30T00:00:00Z"


@pytest.mark.asyncio
async def test_retry_receipts_merge_accepted_and_duplicate_as_non_rejected(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    attempt = (
        await db_session.execute(
            select(IIICollectionAttemptV1).where(IIICollectionAttemptV1.command_id == command.id)
        )
    ).scalar_one()
    assert attempt is not None
    retry = receipt_body(command, attempt, report_body(command, attempt))
    retry.update({"receiptId": "receipt-2", "idempotencyKey": "odp-ingest:receipt-2"})
    retry["outcomes"][0]["outcome"] = "duplicate"
    retry = sign_receipt_body(retry)
    headers = {"x-iii-bridge-token": "bridge-token"}
    assert (
        await client.post("/api/v1/iii-collections/ingress-receipts", json=retry, headers=headers)
    ).status_code == 200

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command),
    )
    response = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert response.status_code == 200
    assert response.json()["data"]["materializationStatus"] == "completed"


@pytest.mark.asyncio
async def test_terminal_materialization_replays_without_querying_or_appending_revision(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    query_calls: list[str] = []
    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command, query_calls),
    )

    first = await client.post(f"{route(scope)}/{command.id}/materialize")
    second = await client.post(f"{route(scope)}/{command.id}/materialize")

    assert first.status_code == second.status_code == 200
    assert query_calls == ["exact", "attempt_page"]
    assert first.json()["data"]["reconciliationRevision"] == 1
    assert second.json()["data"]["reconciliationRevision"] == 1
    assert second.json()["data"]["pageSnapshotAsOf"] == first.json()["data"]["pageSnapshotAsOf"]


@pytest.mark.asyncio
async def test_materialization_rejects_wrong_studio_workflow_version_scope(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command),
    )
    assert (await client.post(f"{route(scope)}/{command.id}/materialize")).status_code == 200
    wrong_scope = CollectionScope(
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=scope["workflow"].id,
        studio_workflow_version_id="another-published-version",
        run_id=scope["run"].id,
    )

    assert await get_materialization(db_session, scope=wrong_scope, command_id=command.id) is None
    with pytest.raises(IIICollectionNotFoundError):
        await materialize_evidence_batch(db_session, scope=wrong_scope, command_id=command.id)


@pytest.mark.asyncio
async def test_studio_evidence_routes_project_latest_redacted_scoped_status(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command),
    )
    materialized = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert materialized.status_code == 200
    batch_id = materialized.json()["data"]["batchId"]
    studio_run = route(scope).removesuffix("/iii-collections")

    listed = await client.get(f"{studio_run}/evidence-batches/v1")
    detail = await client.get(f"{studio_run}/evidence-batches/v1/{batch_id}")
    status_response = await client.get(f"{studio_run}/evidence-batches/v1/{batch_id}/status")
    wrong_scope = await client.get(
        f"{studio_run.replace('/workspaces/iii-workspace/', '/workspaces/other-workspace/')}"
        "/evidence-batches/v1"
    )

    assert listed.status_code == detail.status_code == status_response.status_code == 200
    assert wrong_scope.status_code == 404
    list_data = listed.json()["data"]
    assert list_data["runId"] == scope["run"].id
    assert list_data["evidenceBatches"][0]["materializationStatus"] == "completed"
    assert "recordReferences" not in list_data["evidenceBatches"][0]
    assert list_data["nextCursor"] is None
    assert (
        detail.json()["data"]["recordReferences"] == materialized.json()["data"]["recordReferences"]
    )
    assert status_response.json()["data"]["legacyStatus"] == "completed"
    for forbidden in ("payloadSha256", "signature", "expectedKeySet", "rejectionReason"):
        assert forbidden not in detail.text


@pytest.mark.asyncio
async def test_scoped_materialization_detail_ref_proposes_completed_graph_claim(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    command_id = command.id
    collection_route = route(scope)
    studio_run = collection_route.removesuffix("/iii-collections")
    graph_route = _research_graph_route(scope)
    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command),
    )
    materialized = await client.post(f"{collection_route}/{command.id}/materialize")
    assert materialized.status_code == 200
    batch_id = materialized.json()["data"]["batchId"]

    detail = await client.get(f"{studio_run}/evidence-batches/v1/{batch_id}")
    assert detail.status_code == 200
    manifest_ref = detail.json()["data"]["researchGraphManifestRef"]
    assert manifest_ref["recordRefs"] == [
        {"sourceId": command.odp_source_id, "eventId": "event-1", "odpRecordId": 42}
    ]

    proposer = User(id="materialization-proposer", subject="materialization-proposer")
    proposer_subject = proposer.subject
    workspace = await db_session.get(Workspace, scope["workspace"].id)
    assert workspace is not None
    db_session.add_all(
        [
            proposer,
            workspace,
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=proposer.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await db_session.commit()

    async def override_identity():
        return RequestIdentity(subject=proposer_subject)

    app.dependency_overrides[get_request_identity] = override_identity
    try:
        graph = await client.get(graph_route)
        assert graph.status_code == 200
        graph_data = graph.json()["data"]
        tampered = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "materialization-detail-tampered",
                "action": "propose",
                "expectedSequence": graph_data["sequence"],
                "expectedRevision": graph_data["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "materialization-detail-tampered",
                "claimContentHash": "c" * 64,
                "manifestRefs": [{**manifest_ref, "recordRefSetHash": "0" * 64}],
            },
        )
        assert tampered.status_code == 409
        proposed = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "materialization-detail-proposal",
                "action": "propose",
                "expectedSequence": graph_data["sequence"],
                "expectedRevision": graph_data["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "materialization-detail-claim",
                "claimContentHash": "c" * 64,
                "manifestRefs": [manifest_ref],
            },
        )
        assert proposed.status_code == 201, proposed.text
        attempt = (
            await db_session.execute(
                select(IIICollectionAttemptV1).where(
                    IIICollectionAttemptV1.command_id == command_id
                )
            )
        ).scalar_one()
        command = await db_session.get(IIICollectionCommandV1, command_id)
        assert command is not None
        retry = receipt_body(command, attempt, report_body(command, attempt))
        retry.update({"receiptId": "receipt-2", "idempotencyKey": "odp-ingest:receipt-2"})
        retry = sign_receipt_body(retry)
        headers = {"x-iii-bridge-token": "bridge-token"}
        assert (
            await client.post(
                "/api/v1/iii-collections/ingress-receipts",
                json=retry,
                headers=headers,
            )
        ).status_code == 200
        updated = await client.post(f"{collection_route}/{command_id}/materialize")
        assert updated.status_code == 200
        assert updated.json()["data"]["reconciliationRevision"] == 2

        current = await client.get(graph_route)
        assert current.status_code == 200
        current_data = current.json()["data"]
        stale = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "materialization-detail-stale",
                "action": "propose",
                "expectedSequence": current_data["sequence"],
                "expectedRevision": current_data["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "materialization-detail-stale",
                "claimContentHash": "d" * 64,
                "manifestRefs": [manifest_ref],
            },
        )
        assert stale.status_code == 409
        assert "stale" in stale.text
    finally:
        app.dependency_overrides.pop(get_request_identity, None)


@pytest.mark.asyncio
async def test_scoped_materialization_detail_ref_projects_completed_empty(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch, event_ids=[])

    async def must_not_query(_request):
        raise AssertionError("completed-empty detail must not re-query ODP")

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query", must_not_query
    )
    materialized = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert materialized.status_code == 200
    batch_id = materialized.json()["data"]["batchId"]
    studio_run = route(scope).removesuffix("/iii-collections")

    detail = await client.get(f"{studio_run}/evidence-batches/v1/{batch_id}")
    assert detail.status_code == 200
    manifest_ref = detail.json()["data"]["researchGraphManifestRef"]
    assert manifest_ref["batchId"] == batch_id
    assert manifest_ref["derivation"] == "dispatch-task-v1"
    assert manifest_ref["reconciliationRevision"] == 1
    assert manifest_ref["manifestSchemaVersion"] == "v1"
    assert len(manifest_ref["manifestHash"]) == 64
    assert len(manifest_ref["expectedRecordKeySetHash"]) == 64
    assert manifest_ref["recordRefSetHash"] == (
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    )
    assert manifest_ref["materializationStatus"] == "completed_empty"
    assert manifest_ref["recordRefs"] == []
    assert manifest_ref["excludedItemKeys"] == []
    graph_route = _research_graph_route(scope)
    actor = User(id="empty-context-actor", subject="empty-context-actor")
    actor_subject = actor.subject
    workspace = await db_session.get(Workspace, scope["workspace"].id)
    assert workspace is not None
    db_session.add_all(
        [
            actor,
            workspace,
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=actor.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await db_session.commit()

    async def override_identity():
        return RequestIdentity(subject=actor_subject)

    app.dependency_overrides[get_request_identity] = override_identity
    try:
        graph = await client.get(graph_route)
        assert graph.status_code == 200
        graph_data = graph.json()["data"]
        context = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "completed-empty-context",
                "action": "context",
                "expectedSequence": graph_data["sequence"],
                "expectedRevision": graph_data["researchRevisionId"],
                "nodeId": "opencli-source",
                "manifestRefs": [manifest_ref],
            },
        )
        assert context.status_code == 201, context.text
    finally:
        app.dependency_overrides.pop(get_request_identity, None)


@pytest.mark.asyncio
async def test_scoped_materialization_detail_ref_projects_exact_partial_exclusions(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(
        client,
        db_session,
        monkeypatch,
        event_ids=["event-1", "event-2"],
        outcome="rejected",
    )
    record = _record(command, event_id="event-2", record_id=43)

    async def query(request):
        if request["mode"] == "exact":
            assert request["keys"] == [{"source_id": command.odp_source_id, "event_id": "event-2"}]
            return {
                **_base(request),
                "mode": "exact",
                "records": [record],
                "results": [
                    {
                        "key": {"source_id": command.odp_source_id, "event_id": "event-2"},
                        "classification": "present",
                        "retention_state": "unknown",
                        "record": record,
                    }
                ],
            }
        return {
            **_base(request),
            "mode": "attempt_page",
            "records": [record],
            "results": [],
            "as_of": "2026-08-30T00:00:00Z",
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        query,
    )
    materialized = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert materialized.status_code == 200
    batch_id = materialized.json()["data"]["batchId"]
    studio_run = route(scope).removesuffix("/iii-collections")

    detail = await client.get(f"{studio_run}/evidence-batches/v1/{batch_id}")
    assert detail.status_code == 200
    manifest_ref = detail.json()["data"]["researchGraphManifestRef"]
    assert manifest_ref["materializationStatus"] == "partial"
    assert manifest_ref["recordRefs"] == [
        {"sourceId": command.odp_source_id, "eventId": "event-2", "odpRecordId": 43}
    ]
    assert manifest_ref["excludedItemKeys"] == [
        {"sourceId": command.odp_source_id, "eventId": "event-1"}
    ]

    proposer = User(id="partial-proposer", subject="partial-proposer")
    workspace = await db_session.get(Workspace, scope["workspace"].id)
    assert workspace is not None
    db_session.add_all(
        [
            proposer,
            workspace,
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=proposer.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await db_session.commit()

    async def override_identity():
        return RequestIdentity(subject=proposer.subject)

    app.dependency_overrides[get_request_identity] = override_identity
    try:
        graph = await client.get(_research_graph_route(scope))
        assert graph.status_code == 200
        graph_data = graph.json()["data"]
        proposed = await client.post(
            f"{_research_graph_route(scope)}/mutations",
            json={
                "idempotencyKey": "partial-detail-proposal",
                "action": "propose",
                "expectedSequence": graph_data["sequence"],
                "expectedRevision": graph_data["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "partial-detail-claim",
                "claimContentHash": "c" * 64,
                "manifestRefs": [manifest_ref],
            },
        )
        assert proposed.status_code == 201, proposed.text
    finally:
        app.dependency_overrides.pop(get_request_identity, None)


@pytest.mark.asyncio
async def test_integrity_race_returns_matching_persisted_winner(client, db_session, monkeypatch):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    attempt = (
        await db_session.execute(
            select(IIICollectionAttemptV1).where(IIICollectionAttemptV1.command_id == command.id)
        )
    ).scalar_one()
    report = (
        await db_session.execute(
            select(IIICollectionExpectedKeyReportV1).where(
                IIICollectionExpectedKeyReportV1.attempt_id == attempt.id
            )
        )
    ).scalar_one()
    receipt = (
        await db_session.execute(
            select(IIICollectionIngressReceiptV1).where(
                IIICollectionIngressReceiptV1.attempt_id == attempt.id
            )
        )
    ).scalar_one()
    winner = SimpleNamespace(
        materialization_status="completed",
        batch_id="00000000-0000-0000-0000-000000000001",
        reconciliation_revision=1,
        manifest_hash="0" * 64,
        item_count=report.item_count,
        counts={
            "expected": 1,
            "record_present": 1,
            "inserted": 0,
            "duplicate_existing": 0,
            "rejected": 0,
            "dlq": 0,
            "unknown": 0,
        },
        record_references=[],
        finalization_reason="exact_presence_reconciled",
        query_fingerprint="0" * 64,
        page_snapshot_as_of="2026-08-30T00:00:00Z",
        redaction_profile_version="odp-query-reference-v1",
        finalized_at=datetime(2026, 8, 30, tzinfo=UTC),
        report_id=report.report_id,
        report_hash=report.report_hash,
        expected_key_set_hash=report.key_set_sha256,
        receipt_hashes=[receipt.receipt_hash],
    )
    latest_calls = 0

    async def latest_after_race(*_args):
        nonlocal latest_calls
        latest_calls += 1
        return winner if latest_calls == 3 else None

    async def duplicate_revision_flush():
        raise IntegrityError("insert materialization", {}, RuntimeError("concurrent winner"))

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command),
    )
    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer._latest_manifest",
        latest_after_race,
    )
    monkeypatch.setattr(db_session, "flush", duplicate_revision_flush)
    result = await materialize_evidence_batch(
        db_session,
        scope=CollectionScope(
            workspace_id=scope["workspace"].id,
            project_id=scope["project"].id,
            workflow_id=scope["workflow"].id,
            studio_workflow_version_id=scope["version"].id,
            run_id=scope["run"].id,
        ),
        command_id=command.id,
    )

    assert latest_calls == 3
    assert result.reconciliation_revision == 1
    assert result.materialization_status == "completed"


@pytest.mark.asyncio
async def test_studio_materialization_list_pages_sql_latest_summaries(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    first_attempt = (
        await db_session.execute(
            select(IIICollectionAttemptV1).where(IIICollectionAttemptV1.command_id == command.id)
        )
    ).scalar_one()
    second_attempt = IIICollectionAttemptV1(
        command_id=command.id,
        attempt_number=2,
        task_id="00000000-0000-4000-8000-000000000002",
        trace_id="test-trace-2",
    )
    third_attempt = IIICollectionAttemptV1(
        command_id=command.id,
        attempt_number=3,
        task_id="00000000-0000-4000-8000-000000000003",
        trace_id="test-trace-3",
    )
    db_session.add_all([second_attempt, third_attempt])
    await db_session.flush()
    batch_one = "00000000-0000-0000-0000-000000000001"
    batch_two = "00000000-0000-0000-0000-000000000002"
    batch_three = "00000000-0000-0000-0000-000000000003"
    db_session.add_all(
        [
            _stored_manifest(command, first_attempt, batch_id=batch_one, revision=1, marker=10),
            _stored_manifest(command, first_attempt, batch_id=batch_one, revision=2, marker=11),
            _stored_manifest(command, second_attempt, batch_id=batch_two, revision=1, marker=12),
            _stored_manifest(command, third_attempt, batch_id=batch_three, revision=1, marker=13),
        ]
    )
    await db_session.commit()
    studio_run = route(scope).removesuffix("/iii-collections")

    first = await client.get(f"{studio_run}/evidence-batches/v1?limit=1")
    first_data = first.json()["data"]
    second = await client.get(
        f"{studio_run}/evidence-batches/v1?limit=1&cursor={first_data['nextCursor']}"
    )
    second_data = second.json()["data"]
    third = await client.get(
        f"{studio_run}/evidence-batches/v1?limit=1&cursor={second_data['nextCursor']}"
    )
    detail = await client.get(f"{studio_run}/evidence-batches/v1/{batch_one}")
    invalid_limit = await client.get(f"{studio_run}/evidence-batches/v1?limit=201")

    assert first.status_code == second.status_code == third.status_code == detail.status_code == 200
    assert invalid_limit.status_code == 422
    assert first_data["evidenceBatches"] == [
        {
            "version": "v1",
            "batchId": batch_one,
            "reconciliationRevision": 2,
            "materializationStatus": "completed",
            "legacyStatus": "completed",
            "itemCount": 1,
            "recordCount": 1,
            "counts": {
                "expected": 1,
                "record_present": 1,
                "inserted": 0,
                "duplicate_existing": 0,
                "rejected": 0,
                "dlq": 0,
                "unknown": 0,
            },
            "blocker": None,
            "recoveryAction": "none",
            "queryFingerprint": None,
            "pageSnapshotAsOf": None,
            "redactionProfileVersion": None,
            "finalizedAt": "2026-08-30T00:00:00",
        }
    ]
    assert first_data["nextCursor"] == batch_one
    assert second_data["evidenceBatches"][0]["batchId"] == batch_two
    assert second_data["nextCursor"] == batch_two
    assert third.json()["data"]["evidenceBatches"][0]["batchId"] == batch_three
    assert third.json()["data"]["nextCursor"] is None
    assert "recordReferences" not in first_data["evidenceBatches"][0]
    assert detail.json()["data"]["recordReferences"][0]["eventId"] == "event-11"


@pytest.mark.asyncio
async def test_materialization_classifies_durable_dlq_as_exact_partial_without_payload(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(
        client, db_session, monkeypatch, event_ids=["present", "durable-dlq"]
    )
    calls: list[str] = []
    record = {**_record(command, event_id="present"), "payload": {"cookie": "secret"}}

    async def query(request):
        calls.append(request["mode"])
        if request["mode"] == "exact":
            return {
                **_base(request),
                "mode": "exact",
                "records": [record],
                "results": [
                    {
                        "key": {
                            "source_id": command.odp_source_id,
                            "event_id": "present",
                        },
                        "classification": "present",
                        "retention_state": "unknown",
                        "record": record,
                    },
                    {
                        "key": {
                            "source_id": command.odp_source_id,
                            "event_id": "durable-dlq",
                        },
                        "classification": "unknown",
                        "retention_state": "unknown",
                    },
                ],
            }
        if request["mode"] == "dlq":
            assert request["keys"] == [
                {"source_id": command.odp_source_id, "event_id": "durable-dlq"}
            ]
            return {
                **_base(request),
                "mode": "dlq",
                "retention_state": "retained",
                "records": [],
                "results": [
                    {
                        "key": {
                            "source_id": command.odp_source_id,
                            "event_id": "durable-dlq",
                        },
                        "classification": "dlq",
                        "retention_state": "retained",
                        "payload": {"cookie": "secret"},
                    }
                ],
            }
        return {
            **_base(request),
            "mode": "attempt_page",
            "records": [],
            "results": [],
            "as_of": "2026-08-30T00:00:00Z",
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query", query
    )
    response = await client.post(f"{route(scope)}/{command.id}/materialize")

    assert response.status_code == 200
    data = response.json()["data"]
    assert calls == ["exact", "dlq", "attempt_page"]
    assert data["materializationStatus"] == "partial"
    assert data["counts"]["dlq"] == 1
    assert data["counts"]["unknown"] == 0
    assert data["recordReferences"] == [
        {
            "sourceId": command.odp_source_id,
            "eventId": "present",
            "odpRecordId": 42,
            "committedAt": "2026-08-30T00:00:00Z",
        }
    ]
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_missing_dlq_remains_indeterminate_and_recover_requeries_terminal_facts(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    late_record = False
    calls: list[str] = []

    async def query(request):
        calls.append(request["mode"])
        key = {"source_id": command.odp_source_id, "event_id": "event-1"}
        if request["mode"] == "exact":
            if late_record:
                record = _record(command)
                return {
                    **_base(request),
                    "mode": "exact",
                    "records": [record],
                    "results": [
                        {
                            "key": key,
                            "classification": "present",
                            "retention_state": "unknown",
                            "record": record,
                        }
                    ],
                }
            return {
                **_base(request),
                "mode": "exact",
                "records": [],
                "results": [
                    {"key": key, "classification": "unknown", "retention_state": "unknown"}
                ],
            }
        if request["mode"] == "dlq":
            return {
                **_base(request),
                "mode": "dlq",
                "records": [],
                "results": [
                    {"key": key, "classification": "unknown", "retention_state": "unknown"}
                ],
            }
        return {
            **_base(request),
            "mode": "attempt_page",
            "records": [],
            "results": [],
            "as_of": "2026-08-30T00:00:00Z",
        }

    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query", query
    )
    first = await client.post(f"{route(scope)}/{command.id}/materialize")
    assert first.status_code == 200
    assert first.json()["data"]["materializationStatus"] == "indeterminate"
    assert calls == ["exact", "dlq", "attempt_page"]
    first_hash = first.json()["data"]["manifestHash"]

    late_record = True
    calls.clear()
    recovered = await client.post(f"{route(scope)}/{command.id}/recover")
    assert recovered.status_code == 200
    assert calls == ["exact", "attempt_page"]
    assert recovered.json()["data"]["materializationStatus"] == "completed"
    assert recovered.json()["data"]["reconciliationRevision"] == 2
    assert recovered.json()["data"]["manifestHash"] != first_hash

    manifests = (
        (
            await db_session.execute(
                select(EvidenceBatchMaterializationManifestV1)
                .where(EvidenceBatchMaterializationManifestV1.command_id == command.id)
                .order_by(EvidenceBatchMaterializationManifestV1.reconciliation_revision)
            )
        )
        .scalars()
        .all()
    )
    assert [manifest.reconciliation_revision for manifest in manifests] == [1, 2]
    assert manifests[0].manifest_hash == first_hash

    calls.clear()
    unchanged = await client.post(f"{route(scope)}/{command.id}/recover")
    assert unchanged.status_code == 200
    assert calls == ["exact", "attempt_page"]
    assert unchanged.json()["data"]["reconciliationRevision"] == 2


@pytest.mark.asyncio
async def test_terminal_amendment_recover_keeps_pinned_completed_revision_immutable(
    client, db_session, monkeypatch
):
    scope, command = await submit_report_and_receipt(client, db_session, monkeypatch)
    collection_route = route(scope)
    studio_run_route = collection_route.removesuffix("/iii-collections")
    graph_route = _research_graph_route(scope)
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.workflow.evidence_batch_materializer.post_reconciliation_query",
        _completed_query(command, calls),
    )
    first = await client.post(f"{collection_route}/{command.id}/materialize")
    assert first.status_code == 200
    assert first.json()["data"]["materializationStatus"] == "completed"
    first_manifest = (
        await db_session.execute(
            select(EvidenceBatchMaterializationManifestV1).where(
                EvidenceBatchMaterializationManifestV1.command_id == command.id,
                EvidenceBatchMaterializationManifestV1.reconciliation_revision == 1,
            )
        )
    ).scalar_one()
    first_hash = first_manifest.manifest_hash
    batch_id = first.json()["data"]["batchId"]
    manifest = (await client.get(f"{studio_run_route}/evidence-batches/v1/{batch_id}")).json()[
        "data"
    ]["researchGraphManifestRef"]

    proposer = User(id="terminal-amend-proposer", subject="terminal-amend-proposer")
    reviewer = User(id="terminal-amend-reviewer", subject="terminal-amend-reviewer")
    db_session.add_all(
        [
            proposer,
            reviewer,
            WorkspaceMembership(
                workspace_id=scope["workspace"].id,
                user_id=proposer.id,
                role=WorkspaceRole.OPERATOR,
            ),
            WorkspaceMembership(
                workspace_id=scope["workspace"].id,
                user_id=reviewer.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await db_session.commit()
    current_identity = RequestIdentity(subject=proposer.subject)

    async def override_identity():
        return current_identity

    app.dependency_overrides[get_request_identity] = override_identity
    try:
        graph = (await client.get(graph_route)).json()["data"]
        proposed = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "terminal-amend-propose",
                "action": "propose",
                "expectedSequence": graph["sequence"],
                "expectedRevision": graph["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "terminal-amend-claim",
                "claimContentHash": "c" * 64,
                "manifestRefs": [manifest],
            },
        )
        assert proposed.status_code == 201
        current_identity = RequestIdentity(subject=reviewer.subject)
        verified = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "terminal-amend-verify",
                "action": "verify",
                "expectedSequence": proposed.json()["data"]["sequence"],
                "expectedRevision": proposed.json()["data"]["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "terminal-amend-claim",
            },
        )
        assert verified.status_code == 201
        pinned = await client.post(
            f"{graph_route}/mutations",
            json={
                "idempotencyKey": "terminal-amend-pin",
                "action": "pin",
                "expectedSequence": verified.json()["data"]["sequence"],
                "expectedRevision": verified.json()["data"]["researchRevisionId"],
                "nodeId": "opencli-source",
            },
        )
        assert pinned.status_code == 201
        assert pinned.json()["data"]["pinnedFold"]["blocked"] is False

        attempt = (
            await db_session.execute(
                select(IIICollectionAttemptV1).where(
                    IIICollectionAttemptV1.command_id == command.id
                )
            )
        ).scalar_one()
        duplicate = receipt_body(command, attempt, report_body(command, attempt))
        duplicate.update(
            {
                "receiptId": "terminal-amend-duplicate",
                "idempotencyKey": "odp-ingest:terminal-amend-duplicate",
            }
        )
        duplicate["outcomes"][0]["outcome"] = "duplicate"
        duplicate = sign_receipt_body(duplicate)
        assert duplicate["receiptHash"]
        callback = await client.post(
            "/api/v1/iii-collections/ingress-receipts",
            json=duplicate,
            headers={"x-iii-bridge-token": "bridge-token"},
        )
        assert callback.status_code == 200

        calls.clear()
        replayed = await client.post(f"{collection_route}/{command.id}/materialize")
        assert replayed.status_code == 200
        assert calls == ["exact", "attempt_page"]
        assert replayed.json()["data"]["reconciliationRevision"] == 2
        assert replayed.json()["data"]["counts"]["duplicate_existing"] == 1

        current_identity = RequestIdentity(subject=proposer.subject)
        calls.clear()
        recovered = await client.post(f"{collection_route}/{command.id}/recover")
        assert recovered.status_code == 200
        assert calls == ["exact", "attempt_page"]
        assert recovered.json()["data"]["materializationStatus"] == "completed"
        assert recovered.json()["data"]["reconciliationRevision"] == 2
        assert recovered.json()["data"]["counts"]["duplicate_existing"] == 1
        assert recovered.json()["data"]["counts"]["record_present"] == 1

        old = (
            await db_session.execute(
                select(EvidenceBatchMaterializationManifestV1).where(
                    EvidenceBatchMaterializationManifestV1.command_id == command.id,
                    EvidenceBatchMaterializationManifestV1.reconciliation_revision == 1,
                )
            )
        ).scalar_one()
        assert old.manifest_hash == first_hash
        latest = (
            await db_session.execute(
                select(EvidenceBatchMaterializationManifestV1).where(
                    EvidenceBatchMaterializationManifestV1.command_id == command.id,
                    EvidenceBatchMaterializationManifestV1.reconciliation_revision == 2,
                )
            )
        ).scalar_one()
        assert latest.manifest_hash != first_hash
        stale = await client.get(graph_route)
        assert stale.status_code == 200
        assert stale.json()["data"]["pinnedFold"]["blocked"] is True
        assert stale.json()["data"]["blocker"] == "manifest_superseded"
        assert stale.json()["data"]["recoveryAction"] == "re_review"
    finally:
        app.dependency_overrides.pop(get_request_identity, None)
