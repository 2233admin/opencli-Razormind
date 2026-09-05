import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.main import app
from backend.models.delivery_authorization import (
    DeliveryAuthorizationDecisionV1,
    DeliveryTargetRevision,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.iii_collection import EvidenceBatchMaterializationManifestV1
from backend.models.notification import NotificationLog
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow import delivery_authorization
from tests.integration.iii_collection_test_support import create_scoped_run


def _record_ref_set_hash() -> str:
    return hashlib.sha256(
        json.dumps([["source-1", "event-1", 1]], separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _route(scope: dict) -> str:
    return (
        f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}"
        f"/workflows/{scope['workflow'].id}/runs/{scope['run'].id}"
    )



@pytest.fixture(autouse=True)
def controlled_receiver_registry(monkeypatch):
    def resolve(endpoint_identity: str, credential_reference: str):
        receiver_endpoint = endpoint_identity.removesuffix("-revised").removesuffix("-drifted")
        return SimpleNamespace(
            identity=endpoint_identity,
            receiver_identity=receiver_endpoint.replace("receiver-channel", "controlled-receiver"),
            credential_reference=credential_reference,
        )

    monkeypatch.setattr(delivery_authorization, "resolve_endpoint", resolve)
    monkeypatch.setattr(
        delivery_authorization,
        "endpoint_config_hash",
        lambda endpoint: hashlib.sha256(endpoint.identity.encode()).hexdigest(),
    )

@pytest.mark.asyncio
async def test_authenticated_authorization_freezes_pinned_claims_replays_and_redacts(
    client, db_session, monkeypatch
):
    scope = await create_scoped_run(db_session)
    workspace = await db_session.get(Workspace, scope["workspace"].id)
    assert workspace is not None
    proposer = User(id="delivery-proposer", subject="delivery-proposer")
    approver = User(id="delivery-approver", subject="delivery-approver")
    manager = User(id="delivery-manager", subject="delivery-manager")
    db_session.add_all(
        [
            workspace,
            proposer,
            approver,
            manager,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=proposer.id, role=WorkspaceRole.OPERATOR
            ),
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=approver.id, role=WorkspaceRole.OPERATOR
            ),
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=manager.id, role=WorkspaceRole.MAINTAINER
            ),
            EvidenceBatchMaterializationManifestV1(
                version="v1",
                batch_id="delivery-batch",
                derivation="dispatch-task-v1",
                reconciliation_revision=1,
                workspace_id=workspace.id,
                project_id=scope["project"].id,
                workflow_id=scope["workflow"].id,
                studio_workflow_version_id=scope["version"].id,
                run_id=scope["run"].id,
                node_id="delivery-node",
                command_id="delivery-command",
                attempt_id="delivery-attempt",
                task_id="delivery-task",
                trace_id=scope["run"].trace_id,
                item_count=1,
                counts={"expected": 1, "record_present": 1, "rejected": 0, "dlq": 0, "unknown": 0},
                materialization_status="completed",
                record_references=[
                    {"source_id": "source-1", "event_id": "event-1", "odp_record_id": 1}
                ],
                retention_state="retained",
                finalization_reason="complete",
                manifest_hash="m" * 64,
                expected_key_set_hash="k" * 64,
            ),
        ]
    )
    await db_session.commit()
    current_identity = RequestIdentity(subject=manager.subject)

    async def override_identity():
        return current_identity

    app.dependency_overrides[get_request_identity] = override_identity
    route = _route(scope)
    manifest = {
        "batchId": "delivery-batch",
        "derivation": "dispatch-task-v1",
        "reconciliationRevision": 1,
        "manifestSchemaVersion": "v1",
        "manifestHash": "m" * 64,
        "expectedRecordKeySetHash": "k" * 64,
        "recordRefSetHash": _record_ref_set_hash(),
        "materializationStatus": "completed",
        "recordRefs": [{"sourceId": "source-1", "eventId": "event-1", "odpRecordId": 1}],
    }
    try:
        target_response = await client.post(
            f"{route}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-1",
                "endpointIdentity": "receiver-channel-1",
                "credentialReference": "credential-reference-1",
            },
        )
        assert target_response.status_code == 201
        target = target_response.json()["data"]
        assert "endpointIdentity" not in target and "credentialReference" not in target
        revised_target_response = await client.post(
            f"{route}/delivery-targets",
            json={
                "targetId": target["targetId"],
                "receiverIdentity": "controlled-receiver-1",
                "endpointIdentity": "receiver-channel-1-revised",
                "credentialReference": "credential-reference-1",
            },
        )
        assert revised_target_response.status_code == 201
        current_target = revised_target_response.json()["data"]
        assert current_target["revision"] == target["revision"] + 1
        second_target_response = await client.post(
            f"{route}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-2",
                "endpointIdentity": "receiver-channel-2",
                "credentialReference": "credential-reference-2",
            },
        )
        assert second_target_response.status_code == 201
        paged_targets = await client.get(f"{route}/delivery-targets?limit=1")
        assert paged_targets.status_code == 200
        first_target_page = paged_targets.json()["data"]
        assert first_target_page["nextCursor"] is not None
        second_target_page = await client.get(
            f"{route}/delivery-targets?limit=1&cursor={first_target_page['nextCursor']}"
        )
        assert second_target_page.status_code == 200
        assert second_target_page.json()["data"]["nextCursor"] is None
        assert {
            first_target_page["items"][0]["targetId"],
            second_target_page.json()["data"]["items"][0]["targetId"],
        } == {target["targetId"], second_target_response.json()["data"]["targetId"]}
        assert "endpointIdentity" not in json.dumps(paged_targets.json())
        read_target = await client.get(f"{route}/delivery-targets/{target['targetId']}")
        assert read_target.status_code == 200
        read_target_data = read_target.json()["data"]
        assert read_target_data["targetId"] == current_target["targetId"]
        assert read_target_data["revision"] == current_target["revision"]


        current_identity = RequestIdentity(subject=proposer.subject)
        proposed = await client.post(
            f"{route}/research-graph-v2/mutations",
            json={
                "idempotencyKey": "delivery-propose",
                "action": "propose",
                "expectedSequence": 0,
                "expectedRevision": "root",
                "nodeId": "delivery-node",
                "claimId": "delivery-claim",
                "claimContentHash": "h" * 64,
                "manifestRefs": [manifest],
            },
        )
        assert proposed.status_code == 201
        current_identity = RequestIdentity(subject=approver.subject)
        verified = await client.post(
            f"{route}/research-graph-v2/mutations",
            json={
                "idempotencyKey": "delivery-verify",
                "action": "verify",
                "expectedSequence": proposed.json()["data"]["sequence"],
                "expectedRevision": proposed.json()["data"]["researchRevisionId"],
                "nodeId": "delivery-node",
                "claimId": "delivery-claim",
            },
        )
        assert verified.status_code == 201
        pinned = await client.post(
            f"{route}/research-graph-v2/mutations",
            json={
                "idempotencyKey": "delivery-pin",
                "action": "pin",
                "expectedSequence": verified.json()["data"]["sequence"],
                "expectedRevision": verified.json()["data"]["researchRevisionId"],
                "nodeId": "delivery-node",
            },
        )
        assert pinned.status_code == 201
        pin = pinned.json()["data"]["pinnedFold"]

        body = {
            "operationId": "delivery-operation-1",
            "idempotencyKey": "delivery-idempotency-1",
            "nodeId": "delivery-node",
            "targetId": target["targetId"],
            "pinnedReference": {
                "sequence": pin["sequence"],
                "researchRevisionId": pin["researchRevisionId"],
                "manifestSetHash": pin["manifestSetHash"],
            },
            "selectedClaimIds": ["delivery-claim"],
        }
        current_identity = RequestIdentity(subject=proposer.subject)
        self_approval = await client.post(f"{route}/delivery-authorizations", json=body)
        assert self_approval.status_code == 409
        current_identity = RequestIdentity(subject="delivery-approver")
        created = await client.post(f"{route}/delivery-authorizations", json=body)
        assert created.status_code == 201
        decision = created.json()["data"]
        assert decision["targetRevision"] == current_target["revision"]
        serialized = json.dumps(decision)
        for private_field in (
            "endpointIdentity",
            "credentialReference",
            "policySnapshot",
            "payloadReference",
            "odpRecordId",
        ):
            assert private_field not in serialized
        assert "execution" not in serialized.lower() and "outcome" not in serialized.lower()
        read_decision = await client.get(
            f"{route}/delivery-authorizations/{decision['decisionId']}"
        )
        assert read_decision.status_code == 200
        stored_decision = await db_session.get(
            DeliveryAuthorizationDecisionV1, decision["decisionId"]
        )
        assert stored_decision is not None
        read_decision_data = read_decision.json()["data"]
        assert read_decision_data["decisionId"] == decision["decisionId"]
        assert read_decision_data["decisionHash"] == decision["decisionHash"]
        assert read_decision_data["claims"] == decision["claims"]
        assert stored_decision.selected_claims == [
            {"claimId": "delivery-claim", "contentHash": "h" * 64}
        ]
        assert stored_decision.manifest_set == [
            {
                "batchId": "delivery-batch",
                "derivation": "dispatch-task-v1",
                "reconciliationRevision": 1,
                "manifestSchemaVersion": "v1",
                "manifestHash": "m" * 64,
                "expectedRecordKeySetHash": "k" * 64,
                "recordRefSetHash": _record_ref_set_hash(),
                "materializationStatus": "completed",
            }
        ]
        assert stored_decision.sanitized_payload_manifest == {
            "payloadSchemaVersion": "delivery-claim-manifest-v1",
            "payloadReference": "frozen-claim-manifest",
            "payloadHash": delivery_authorization._canonical_hash(
                {
                    "schemaVersion": "delivery-claim-manifest-v1",
                    "claims": stored_decision.selected_claims,
                    "manifestHashes": ["m" * 64],
                }
            ),
            "sanctionedReferenceHashes": ["h" * 64, "m" * 64],
            "redactionProfileVersion": "delivery-authorization-redaction-v1",
        }
        assert stored_decision.approval_evidence[0]["actorId"] == approver.id
        assert "odpRecordId" not in json.dumps(stored_decision.sanitized_payload_manifest)

        stored_decision.operation_id = "mutated-operation"
        with pytest.raises(ValueError, match="append-only"):
            await db_session.flush()
        await db_session.rollback()
        stored_decision = await db_session.get(
            DeliveryAuthorizationDecisionV1, decision["decisionId"]
        )
        assert stored_decision is not None
        await db_session.delete(stored_decision)
        with pytest.raises(ValueError, match="append-only"):
            await db_session.flush()
        await db_session.rollback()

        target_revision = await db_session.scalar(
            select(DeliveryTargetRevision)
            .where(DeliveryTargetRevision.target_id == target["targetId"])
            .order_by(DeliveryTargetRevision.revision.desc())
        )
        assert target_revision is not None
        target_revision.endpoint_identity = "receiver-channel-mutated"
        with pytest.raises(ValueError, match="append-only"):
            await db_session.flush()
        await db_session.rollback()
        target_revision = await db_session.scalar(
            select(DeliveryTargetRevision)
            .where(DeliveryTargetRevision.target_id == target["targetId"])
            .order_by(DeliveryTargetRevision.revision.desc())
        )
        assert target_revision is not None
        await db_session.delete(target_revision)
        with pytest.raises(ValueError, match="append-only"):
            await db_session.flush()
        await db_session.rollback()

        replay = await client.post(f"{route}/delivery-authorizations", json=body)
        assert replay.status_code == 201
        assert replay.json()["data"]["decisionId"] == decision["decisionId"]
        changed = await client.post(
            f"{route}/delivery-authorizations", json={**body, "nodeId": "changed-node"}
        )
        assert changed.status_code == 409
        client_expanded_target = await client.post(
            f"{route}/delivery-authorizations",
            json={**body, "targetRevision": target["revision"]},
        )
        assert client_expanded_target.status_code == 422

        current_identity = RequestIdentity(subject="delivery-manager")
        target_drift_revision = await client.post(
            f"{route}/delivery-targets",
            json={
                "targetId": target["targetId"],
                "receiverIdentity": "controlled-receiver-1",
                "endpointIdentity": "receiver-channel-1-drifted",
                "credentialReference": "credential-reference-1",
            },
        )
        assert target_drift_revision.status_code == 201
        current_identity = RequestIdentity(subject="delivery-approver")
        target_drift = await client.post(f"{route}/delivery-authorizations", json=body)
        assert target_drift.status_code == 409
        policy_body = {
            **body,
            "operationId": "delivery-operation-policy",
            "idempotencyKey": "delivery-idempotency-policy",
        }
        policy_created = await client.post(f"{route}/delivery-authorizations", json=policy_body)
        assert policy_created.status_code == 201
        monkeypatch.setitem(
            delivery_authorization._CONTROLLED_RECEIVER_POLICY,
            "timeout",
            "receiver-v2-required",
        )
        policy_drift = await client.post(f"{route}/delivery-authorizations", json=policy_body)
        assert policy_drift.status_code == 409
        assert await db_session.scalar(select(func.count(DeliveryAuthorizationDecisionV1.id))) == 2
        assert await db_session.scalar(select(func.count(NotificationLog.id))) == 0
        db_session.add(
            EvidenceBatchMaterializationManifestV1(
                version="v1",
                batch_id="delivery-batch",
                derivation="dispatch-task-v1",
                reconciliation_revision=2,
                workspace_id="iii-workspace",
                project_id="iii-project",
                workflow_id="iii-workflow",
                studio_workflow_version_id="iii-version",
                run_id="iii-run",
                node_id="delivery-node",
                command_id="delivery-command",
                attempt_id="delivery-attempt",
                task_id="delivery-task",
                trace_id="iii-trace",
                item_count=1,
                counts={"expected": 1, "record_present": 1, "rejected": 0, "dlq": 0, "unknown": 0},
                materialization_status="completed",
                record_references=[
                    {"source_id": "source-1", "event_id": "event-1", "odp_record_id": 1}
                ],
                retention_state="retained",
                finalization_reason="complete",
                manifest_hash="n" * 64,
                expected_key_set_hash="k" * 64,
            )
        )
        await db_session.commit()
        amended_manifest = await client.post(
            f"{route}/delivery-authorizations",
            json={
                **body,
                "operationId": "delivery-operation-amended-manifest",
                "idempotencyKey": "delivery-idempotency-amended-manifest",
            },
        )
        assert amended_manifest.status_code == 409

        listed = await client.get(f"{route}/delivery-authorizations?limit=1")
        assert listed.status_code == 200
        first_page_id = listed.json()["data"]["items"][0]["decisionId"]
        cursor = listed.json()["data"]["nextCursor"]
        assert cursor is not None
        next_page = await client.get(f"{route}/delivery-authorizations?limit=1&cursor={cursor}")
        assert next_page.status_code == 200
        second_page_id = next_page.json()["data"]["items"][0]["decisionId"]
        assert {first_page_id, second_page_id} == {
            decision["decisionId"],
            policy_created.json()["data"]["decisionId"],
        }
    finally:
        app.dependency_overrides.pop(get_request_identity, None)


@pytest.mark.asyncio
async def test_authorization_rejects_self_approval_and_unpinned_graph(client, db_session):
    scope = await create_scoped_run(db_session)
    workspace = await db_session.get(Workspace, scope["workspace"].id)
    assert workspace is not None
    member = User(id="delivery-member", subject="delivery-member")
    db_session.add_all(
        [
            workspace,
            member,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=member.id, role=WorkspaceRole.MAINTAINER
            ),
        ]
    )
    await db_session.commit()

    async def override_identity():
        return RequestIdentity(subject=member.subject)

    app.dependency_overrides[get_request_identity] = override_identity
    route = _route(scope)
    try:
        target = await client.post(
            f"{route}/delivery-targets",
            json={
                "receiverIdentity": "controlled-receiver-denial",
                "endpointIdentity": "receiver-channel-denial",


                "credentialReference": "credential-reference-denial",
            },
        )
        assert target.status_code == 201
        denied = await client.post(
            f"{route}/delivery-authorizations",
            json={
                "operationId": "denied-operation",
                "idempotencyKey": "denied-key",
                "nodeId": "node-1",
                "targetId": target.json()["data"]["targetId"],
                "pinnedReference": {
                    "sequence": 1,
                    "researchRevisionId": "missing",
                    "manifestSetHash": "m" * 64,
                },
                "selectedClaimIds": ["missing-claim"],
            },
        )
        assert denied.status_code == 409
        assert await db_session.scalar(select(func.count(DeliveryAuthorizationDecisionV1.id))) == 0
    finally:
        app.dependency_overrides.pop(get_request_identity, None)


@pytest.mark.asyncio
async def test_delivery_routes_enforce_mutation_permissions(client, db_session):
    scope = await create_scoped_run(db_session)
    workspace = await db_session.get(Workspace, scope["workspace"].id)
    assert workspace is not None
    viewer = User(id="delivery-scope-viewer", subject="delivery-scope-viewer")
    outsider = User(id="delivery-scope-outsider", subject="delivery-scope-outsider")
    db_session.add_all(
        [
            workspace,
            viewer,
            outsider,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=viewer.id, role=WorkspaceRole.VIEWER
            ),
        ]
    )
    await db_session.commit()
    current_identity = RequestIdentity(subject=viewer.subject)

    async def override_identity():
        return current_identity

    target_body = {
        "receiverIdentity": "controlled-receiver-scope",
        "endpointIdentity": "receiver-channel-scope",
        "credentialReference": "credential-reference-scope",
    }
    denied_authorization = {
        "operationId": "scope-denied-operation",
        "idempotencyKey": "scope-denied-key",
        "nodeId": "node-1",
        "targetId": "missing-target",
        "pinnedReference": {
            "sequence": 1,
            "researchRevisionId": "missing",
            "manifestSetHash": "m" * 64,
        },
        "selectedClaimIds": ["missing-claim"],
    }
    app.dependency_overrides[get_request_identity] = override_identity
    route = _route(scope)
    try:
        assert (await client.post(f"{route}/delivery-targets", json=target_body)).status_code == 403
        assert (
            await client.post(f"{route}/delivery-authorizations", json=denied_authorization)
        ).status_code == 403
        current_identity = RequestIdentity(subject=outsider.subject)
        assert (await client.post(f"{route}/delivery-targets", json=target_body)).status_code == 403
        assert (
            await client.post(f"{route}/delivery-authorizations", json=denied_authorization)
        ).status_code == 403
        assert await db_session.scalar(select(func.count(DeliveryAuthorizationDecisionV1.id))) == 0
    finally:
        app.dependency_overrides.pop(get_request_identity, None)
