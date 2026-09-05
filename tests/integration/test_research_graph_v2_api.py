import hashlib
import json
from datetime import UTC, datetime

import pytest

from backend.main import app
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.iii_collection import (
    EvidenceBatchMaterializationManifestV1,
    IIICollectionExpectedKeyReportV1,
)
from backend.schemas.research_graph_v2 import (
    ResearchGraphV2ActorEvidence,
    ResearchGraphV2MutationRequest,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow.research_graph_v2 import (
    ResearchGraphV2ConflictError,
    ResearchGraphV2Scope,
    append_research_graph_v2_mutation,
)
from tests.integration.iii_collection_test_support import create_scoped_run


def _record_ref_set_hash() -> str:
    return hashlib.sha256(
        json.dumps([["source-1", "event-1", 1]], separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _route(scope: dict) -> str:
    return (
        f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}"
        f"/workflows/{scope['workflow'].id}/runs/{scope['run'].id}/research-graph-v2"
    )


@pytest.mark.asyncio
async def test_authenticated_studio_review_rejects_self_review_and_pins_fold(client, db_session):
    scope = await create_scoped_run(db_session)
    route = _route(scope)
    proposer = User(id="graph-proposer", subject="graph-proposer")
    reviewer = User(id="graph-reviewer", subject="graph-reviewer")
    workspace = Workspace(id=scope["workspace"].id, name="Graph", slug="graph")
    db_session.add_all(
        [
            proposer,
            reviewer,
            workspace,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=proposer.id, role=WorkspaceRole.OPERATOR
            ),
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=reviewer.id, role=WorkspaceRole.OPERATOR
            ),
            EvidenceBatchMaterializationManifestV1(
                version="v1",
                batch_id="graph-batch",
                derivation="dispatch-task-v1",
                reconciliation_revision=1,
                workspace_id=workspace.id,
                project_id=scope["project"].id,
                workflow_id=scope["workflow"].id,
                studio_workflow_version_id=scope["version"].id,
                run_id=scope["run"].id,
                node_id="opencli-source",
                command_id="graph-command",
                attempt_id="graph-attempt",
                task_id="graph-task",
                trace_id=scope["run"].trace_id,
                item_count=1,
                counts={"expected": 1, "record_present": 1, "rejected": 0, "dlq": 0, "unknown": 0},
                materialization_status="completed",
                record_references=[
                    {
                        "source_id": "source-1",
                        "event_id": "event-1",
                        "odp_record_id": 1,
                        "committed_at": "2026-08-30T00:00:00+00:00",
                    }
                ],
                retention_state="retained",
                finalization_reason="complete",
                manifest_hash="m" * 64,
                expected_key_set_hash="k" * 64,
            ),
        ]
    )
    await db_session.commit()
    current_identity = RequestIdentity(subject=proposer.subject)

    async def override_identity():
        return current_identity

    app.dependency_overrides[get_request_identity] = override_identity
    manifest = {
        "batchId": "graph-batch",
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
        proposed = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "proposal",
                "action": "propose",
                "expectedSequence": 0,
                "expectedRevision": "root",
                "nodeId": "opencli-source",
                "claimId": "claim-1",
                "claimContentHash": "c" * 64,
                "manifestRefs": [manifest],
            },
        )
        assert proposed.status_code == 201
        proposal = proposed.json()["data"]
        for action in ("reject", "retract"):
            self_review_action = await client.post(
                f"{route}/mutations",
                json={
                    "idempotencyKey": f"self-{action}",
                    "action": action,
                    "expectedSequence": proposal["sequence"],
                    "expectedRevision": proposal["researchRevisionId"],
                    "nodeId": "opencli-source",
                    "claimId": "claim-1",
                },
            )
            assert self_review_action.status_code == 409

        self_review = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "self-review",
                "action": "verify",
                "expectedSequence": proposal["sequence"],
                "expectedRevision": proposal["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "claim-1",
            },
        )
        assert self_review.status_code == 409

        current_identity = RequestIdentity(subject="graph-reviewer")
        verified = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "verify",
                "action": "verify",
                "expectedSequence": proposal["sequence"],
                "expectedRevision": proposal["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "claim-1",
            },
        )
        assert verified.status_code == 201
        review = verified.json()["data"]

        pinned = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "pin",
                "action": "pin",
                "expectedSequence": review["sequence"],
                "expectedRevision": review["researchRevisionId"],
                "nodeId": "opencli-source",
            },
        )
        assert pinned.status_code == 201
        pinned_graph = pinned.json()["data"]
        assert pinned_graph["pinnedFold"]["blocked"] is False
        assert "signature" not in json.dumps(pinned.json())
        exact = pinned_graph["pinnedFold"]
        matched = await client.get(
            f"{route}?expected_pin_sequence={exact['sequence']}&expected_pin_revision={exact['researchRevisionId']}&expected_pin_manifest_set_hash={exact['manifestSetHash']}"
        )
        assert matched.json()["data"]["pinnedFold"]["blocked"] is False
        mismatch = await client.get(
            f"{route}?expected_pin_sequence=999&expected_pin_revision={exact['researchRevisionId']}&expected_pin_manifest_set_hash={exact['manifestSetHash']}"
        )
        assert mismatch.json()["data"]["blocker"] == "pinned_reference_mismatch"
        partial = await client.get(f"{route}?expected_pin_sequence={exact['sequence']}")
        assert partial.json()["data"]["blocker"] == "pinned_reference_mismatch"

        current_identity = RequestIdentity(subject="graph-proposer")
        replay = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "proposal",
                "action": "propose",
                "expectedSequence": 0,
                "expectedRevision": "root",
                "nodeId": "opencli-source",
                "claimId": "claim-1",
                "claimContentHash": "c" * 64,
                "manifestRefs": [manifest],
            },
        )
        assert replay.status_code == 201
        assert replay.json()["data"]["sequence"] == pinned_graph["sequence"]
        changed_replay = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "proposal",
                "action": "propose",
                "expectedSequence": 0,
                "expectedRevision": "root",
                "nodeId": "opencli-source",
                "claimId": "claim-1",
                "claimContentHash": "d" * 64,
                "manifestRefs": [manifest],
            },
        )
        assert changed_replay.status_code == 409

        current_identity = RequestIdentity(subject="graph-reviewer")
        stale_retract = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "stale-retract",
                "action": "retract",
                "expectedSequence": 0,
                "expectedRevision": "root",
                "nodeId": "opencli-source",
                "claimId": "claim-1",
            },
        )
        assert stale_retract.status_code == 409

        db_session.add(
            EvidenceBatchMaterializationManifestV1(
                version="v1",
                batch_id="graph-batch",
                derivation="dispatch-task-v1",
                reconciliation_revision=2,
                workspace_id="iii-workspace",
                project_id="iii-project",
                workflow_id="iii-workflow",
                studio_workflow_version_id="iii-version",
                run_id="iii-run",
                node_id="opencli-source",
                command_id="graph-command",
                attempt_id="graph-attempt",
                task_id="graph-task",
                trace_id="iii-trace",
                item_count=1,
                counts={"expected": 1, "record_present": 1, "rejected": 0, "dlq": 0, "unknown": 0},
                materialization_status="completed",
                record_references=[
                    {
                        "source_id": "source-1",
                        "event_id": "event-1",
                        "odp_record_id": 1,
                        "committed_at": "2026-08-30T00:00:00+00:00",
                    }
                ],
                retention_state="retained",
                finalization_reason="complete",
                manifest_hash="n" * 64,
                expected_key_set_hash="k" * 64,
            )
        )
        await db_session.commit()
        stale_read = await client.get(route)
        assert stale_read.status_code == 200
        assert stale_read.json()["data"]["pinnedFold"]["blocked"] is True
        assert stale_read.json()["data"]["blocker"] == "manifest_superseded"
        assert stale_read.json()["data"]["recoveryAction"] == "re_review"
        amended_manifest = {**manifest, "reconciliationRevision": 2, "manifestHash": "n" * 64}
        superseded = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "supersede",
                "action": "supersede",
                "expectedSequence": pinned_graph["sequence"],
                "expectedRevision": pinned_graph["researchRevisionId"],
                "nodeId": "opencli-source",
                "supersedesEventId": "research-graph-v2:proposal",
                "claimId": "claim-1",
                "manifestRefs": [amended_manifest],
            },
        )
        assert superseded.status_code == 201
        assert superseded.json()["data"]["pinnedFold"]["blocked"] is True
        superseder_self_verify = await client.post(
            f"{route}/mutations",
            json={
                "idempotencyKey": "superseder-self-verify",
                "action": "verify",
                "expectedSequence": superseded.json()["data"]["sequence"],
                "expectedRevision": superseded.json()["data"]["researchRevisionId"],
                "nodeId": "opencli-source",
                "claimId": "claim-1",
            },
        )
        assert superseder_self_verify.status_code == 409
        read = await client.get(route)
        assert read.status_code == 200
        assert read.json()["data"]["claims"][0]["state"] == "superseded"
        assert read.json()["data"]["claims"][0]["manifestRefs"][0]["manifestHash"] == "n" * 64
        assert read.json()["data"]["pinnedFold"]["blocked"] is True
        assert superseded.json()["data"]["claims"][0]["state"] == "superseded"
    finally:
        app.dependency_overrides.pop(get_request_identity, None)


@pytest.mark.asyncio
async def test_zero_and_partial_manifest_policy_fail_closed(db_session):
    scope_rows = await create_scoped_run(db_session)
    scope = ResearchGraphV2Scope(
        workspace_id=scope_rows["workspace"].id,
        project_id=scope_rows["project"].id,
        workflow_id=scope_rows["workflow"].id,
        studio_workflow_version_id=scope_rows["version"].id,
        run_id=scope_rows["run"].id,
    )
    report = IIICollectionExpectedKeyReportV1(
        version="v1",
        report_id="partial-report",
        command_id="partial-command",
        attempt_id="partial-attempt",
        report_sequence=1,
        payload_sha256="p" * 64,
        key_set_sha256="k" * 64,
        item_count=2,
        zero_count=0,
        rejected_count=1,
        expected_keys=[
            {"source_id": "source-1", "event_id": "event-1"},
            {"source_id": "source-1", "event_id": "event-2"},
        ],
        reported_at=datetime(2026, 8, 30, tzinfo=UTC),
        report_hash="h" * 64,
    )
    db_session.add_all(
        [
            report,
            EvidenceBatchMaterializationManifestV1(
                version="v1",
                batch_id="empty-batch",
                derivation="dispatch-task-v1",
                reconciliation_revision=1,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                workflow_id=scope.workflow_id,
                studio_workflow_version_id=scope.studio_workflow_version_id,
                run_id=scope.run_id,
                node_id="opencli-source",
                command_id="empty-command",
                attempt_id="empty-attempt",
                task_id="empty-task",
                trace_id="iii-trace",
                item_count=0,
                counts={"expected": 0, "record_present": 0, "rejected": 0, "dlq": 0, "unknown": 0},
                materialization_status="completed_empty",
                record_references=[],
                retention_state="retained",
                finalization_reason="zero",
                manifest_hash="e" * 64,
                expected_key_set_hash="z" * 64,
            ),
            EvidenceBatchMaterializationManifestV1(
                version="v1",
                batch_id="partial-batch",
                derivation="dispatch-task-v1",
                reconciliation_revision=1,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                workflow_id=scope.workflow_id,
                studio_workflow_version_id=scope.studio_workflow_version_id,
                run_id=scope.run_id,
                node_id="opencli-source",
                command_id="partial-command",
                attempt_id="partial-attempt",
                task_id="partial-task",
                trace_id="iii-trace",
                report_id=report.report_id,
                item_count=2,
                counts={"expected": 2, "record_present": 1, "rejected": 1, "dlq": 0, "unknown": 0},
                materialization_status="partial",
                record_references=[
                    {
                        "source_id": "source-1",
                        "event_id": "event-1",
                        "odp_record_id": 1,
                        "committed_at": "2026-08-30T00:00:00+00:00",
                    }
                ],
                retention_state="retained",
                finalization_reason="rejected",
                manifest_hash="q" * 64,
                expected_key_set_hash="k" * 64,
            ),
        ]
    )
    await db_session.commit()
    db_session.add(
        EvidenceBatchMaterializationManifestV1(
            version="v1",
            batch_id="complete-batch",
            derivation="dispatch-task-v1",
            reconciliation_revision=1,
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            workflow_id=scope.workflow_id,
            studio_workflow_version_id=scope.studio_workflow_version_id,
            run_id=scope.run_id,
            node_id="opencli-source",
            command_id="complete-command",
            attempt_id="complete-attempt",
            task_id="complete-task",
            trace_id="iii-trace",
            item_count=2,
            counts={"expected": 2, "record_present": 2, "rejected": 0, "dlq": 0, "unknown": 0},
            materialization_status="completed",
            record_references=[
                {"source_id": "source-1", "event_id": "event-1", "odp_record_id": 1},
                {"source_id": "source-1", "event_id": "event-2", "odp_record_id": 2},
            ],
            retention_state="retained",
            finalization_reason="complete",
            manifest_hash="v" * 64,
            expected_key_set_hash="w" * 64,
        )
    )
    await db_session.commit()
    actor = ResearchGraphV2ActorEvidence(
        actor_type="user",
        actor_id="actor",
        principal="subject",
        capability="inbox.work",
        policy_version="workspace-rbac-v1",
        authorized_at=datetime(2026, 8, 30, tzinfo=UTC),
    )
    empty = {
        "batchId": "empty-batch",
        "derivation": "dispatch-task-v1",
        "reconciliationRevision": 1,
        "manifestSchemaVersion": "v1",
        "manifestHash": "e" * 64,
        "expectedRecordKeySetHash": "z" * 64,
        "recordRefSetHash": hashlib.sha256(b"[]").hexdigest(),
        "materializationStatus": "completed_empty",
    }
    context = await append_research_graph_v2_mutation(
        db_session,
        scope=scope,
        actor=actor,
        request=ResearchGraphV2MutationRequest(
            idempotency_key="empty-context",
            action="context",
            expected_sequence=0,
            expected_revision="root",
            node_id="opencli-source",
            manifest_refs=[empty],
        ),
    )
    assert context.claims == []
    subset = {
        "batchId": "complete-batch",
        "derivation": "dispatch-task-v1",
        "reconciliationRevision": 1,
        "manifestSchemaVersion": "v1",
        "manifestHash": "v" * 64,
        "expectedRecordKeySetHash": "w" * 64,
        "recordRefSetHash": hashlib.sha256(
            json.dumps(
                [["source-1", "event-1", 1], ["source-1", "event-2", 2]],
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "materializationStatus": "completed",
        "recordRefs": [{"sourceId": "source-1", "eventId": "event-1", "odpRecordId": 1}],
    }
    with pytest.raises(ResearchGraphV2ConflictError, match="exactly match"):
        await append_research_graph_v2_mutation(
            db_session,
            scope=scope,
            actor=actor,
            request=ResearchGraphV2MutationRequest(
                idempotency_key="complete-subset",
                action="propose",
                expected_sequence=context.sequence,
                expected_revision=context.research_revision_id,
                node_id="opencli-source",
                claim_id="subset-claim",
                claim_content_hash="c" * 64,
                manifest_refs=[subset],
            ),
        )
    partial = {
        "batchId": "partial-batch",
        "derivation": "dispatch-task-v1",
        "reconciliationRevision": 1,
        "manifestSchemaVersion": "v1",
        "manifestHash": "q" * 64,
        "expectedRecordKeySetHash": "k" * 64,
        "recordRefSetHash": _record_ref_set_hash(),
        "materializationStatus": "partial",
        "recordRefs": [{"sourceId": "source-1", "eventId": "event-1", "odpRecordId": 1}],
    }
    with pytest.raises(ResearchGraphV2ConflictError, match="exclusions"):
        await append_research_graph_v2_mutation(
            db_session,
            scope=scope,
            actor=actor,
            request=ResearchGraphV2MutationRequest(
                idempotency_key="partial-missing",
                action="propose",
                expected_sequence=context.sequence,
                expected_revision=context.research_revision_id,
                node_id="opencli-source",
                claim_id="partial-claim",
                claim_content_hash="c" * 64,
                manifest_refs=[partial],
            ),
        )
    partial["excludedItemKeys"] = [{"sourceId": "source-1", "eventId": "event-2"}]
    accepted = await append_research_graph_v2_mutation(
        db_session,
        scope=scope,
        actor=actor,
        request=ResearchGraphV2MutationRequest(
            idempotency_key="partial-complete",
            action="propose",
            expected_sequence=context.sequence,
            expected_revision=context.research_revision_id,
            node_id="opencli-source",
            claim_id="partial-claim",
            claim_content_hash="c" * 64,
            manifest_refs=[partial],
        ),
    )
    assert accepted.claims[0].state == "proposed"
