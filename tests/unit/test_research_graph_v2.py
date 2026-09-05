from backend.schemas.research_graph_v2 import (
    AuthorizedResearchGraphEventV2,
    ResearchGraphV2ActorEvidence,
    ResearchGraphV2PinnedReference,
)
from backend.schemas.workflow import WorkflowNodeRunEvent
from backend.workflow.research_graph_v2 import (
    _read,
    exact_pinned_reference,
    fold_authorized_research_graph_events,
)


def _event(
    *, sequence: int, action: str, event_id: str, manifest_hash: str = "a" * 64
) -> WorkflowNodeRunEvent:
    envelope = AuthorizedResearchGraphEventV2(
        version="v2",
        event_id=event_id,
        action=action,
        expected_sequence=sequence - 1,
        expected_revision="root" if sequence == 1 else "revision-1",
        research_revision_id="revision-1",
        workspace_id="workspace-1",
        project_id="project-1",
        workflow_id="workflow-1",
        studio_workflow_version_id="version-1",
        run_id="run-1",
        node_id="node-1",
        claim_id="claim-1",
        claim_content_hash="c" * 64,
        manifest_refs=[
            {
                "batchId": "batch-1",
                "derivation": "dispatch-task-v1",
                "reconciliationRevision": 1,
                "manifestSchemaVersion": "v1",
                "manifestHash": manifest_hash,
                "expectedRecordKeySetHash": "k" * 64,
                "recordRefSetHash": "r" * 64,
                "materializationStatus": "completed",
            }
        ],
        actor=ResearchGraphV2ActorEvidence(
            actor_type="user",
            actor_id="actor-1",
            principal="subject-1",
            capability="inbox.work" if action in {"context", "propose"} else "actions.approve",
            policy_version="workspace-rbac-v1",
            authorized_at="2026-08-30T00:00:00+00:00",
        ),
        pinned_sequence=sequence if action == "pin" else None,
    )
    return WorkflowNodeRunEvent(
        id=f"rgv2:{event_id}",
        sequence=sequence,
        workflowId="workflow-1",
        workflowRunId="run-1",
        traceId="trace-1",
        nodeId="node-1",
        eventType="completed",
        createdAt="2026-08-30T00:00:00+00:00",
        details={"authorizedResearchGraphV2": envelope.model_dump(by_alias=True)},
    )


def test_fold_requires_independent_reviewer_and_pins_verified_claim() -> None:
    proposed = _event(sequence=1, action="propose", event_id="proposal")
    self_verification = _event(sequence=2, action="verify", event_id="self-verify")
    self_verification.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-1"

    folded = fold_authorized_research_graph_events([proposed, self_verification])

    assert folded.sequence == 1
    assert folded.claims["claim-1"].state == "proposed"
    assert folded.blocker == "independent_review_required"

def test_fold_requires_independent_reject_and_retract() -> None:
    proposed = _event(sequence=1, action="propose", event_id="proposal")
    self_reject = _event(sequence=2, action="reject", event_id="self-reject")
    self_retract = _event(sequence=2, action="retract", event_id="self-retract")

    assert (
        fold_authorized_research_graph_events([proposed, self_reject]).claims["claim-1"].state
        == "proposed"
    )
    assert (
        fold_authorized_research_graph_events([proposed, self_retract]).claims["claim-1"].state
        == "proposed"
    )


def test_exact_pinned_reference_fails_closed_on_mismatch() -> None:
    proposed = _event(sequence=1, action="propose", event_id="proposal")
    verified = _event(sequence=2, action="verify", event_id="verify")
    verified.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-2"
    pinned = _event(sequence=3, action="pin", event_id="pin")
    read = _read(fold_authorized_research_graph_events([proposed, verified, pinned]))
    assert read.pinned_fold is not None

    matched = exact_pinned_reference(
        read,
        reference=ResearchGraphV2PinnedReference(
            sequence=read.pinned_fold.sequence,
            researchRevisionId=read.pinned_fold.research_revision_id,
            manifestSetHash=read.pinned_fold.manifest_set_hash,
        ),
        required=True,
    )
    mismatch = exact_pinned_reference(read, reference=None, required=True)

    assert matched.blocker is None
    assert mismatch.pinned_fold is not None and mismatch.pinned_fold.blocked is True

def test_fold_cas_binds_to_the_transcript_tail_not_just_v2_events() -> None:
    ordinary_event = _event(sequence=1, action="propose", event_id="ordinary")
    ordinary_event.details = {}
    proposed = _event(sequence=2, action="propose", event_id="proposal")
    proposed.details["authorizedResearchGraphV2"]["expectedSequence"] = 1
    proposed.details["authorizedResearchGraphV2"]["expectedRevision"] = "root"

    folded = fold_authorized_research_graph_events([ordinary_event, proposed])

    assert folded.sequence == 2
    assert folded.tail_sequence == 2
    assert folded.claims["claim-1"].state == "proposed"


def test_fold_rejects_wrong_capability_and_mismatched_pin_sequence() -> None:
    unauthorized = _event(sequence=1, action="propose", event_id="unauthorized")
    unauthorized.details["authorizedResearchGraphV2"]["actor"]["capability"] = "actions.approve"

    assert fold_authorized_research_graph_events([unauthorized]).claims == {}

    proposed = _event(sequence=1, action="propose", event_id="proposal")
    verified = _event(sequence=2, action="verify", event_id="verify")
    verified.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-2"
    malformed_pin = _event(sequence=3, action="pin", event_id="pin")
    malformed_pin.details["authorizedResearchGraphV2"]["pinnedSequence"] = 2

    folded = fold_authorized_research_graph_events([proposed, verified, malformed_pin])

    assert folded.claims["claim-1"].state == "verified"
    assert folded.pinned is None

def test_superseded_claim_stays_active_until_independent_reverification() -> None:
    first = _event(sequence=1, action="propose", event_id="first")
    first_verified = _event(sequence=2, action="verify", event_id="first-verify")
    first_verified.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-2"
    second = _event(sequence=3, action="propose", event_id="second")
    second.details["authorizedResearchGraphV2"]["claimId"] = "claim-2"
    second.details["authorizedResearchGraphV2"]["claimContentHash"] = "d" * 64
    superseded = _event(sequence=4, action="supersede", event_id="second-supersede")
    superseded.details["authorizedResearchGraphV2"]["claimId"] = "claim-2"
    blocked_pin = _event(sequence=5, action="pin", event_id="blocked-pin")

    assert fold_authorized_research_graph_events(
        [first, first_verified, second, superseded, blocked_pin]
    ).pinned is None

    second_verified = _event(sequence=5, action="verify", event_id="second-verify")
    second_verified.details["authorizedResearchGraphV2"]["claimId"] = "claim-2"
    second_verified.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-2"
    recovered_pin = _event(sequence=6, action="pin", event_id="recovered-pin")

    recovered = fold_authorized_research_graph_events(
        [first, first_verified, second, superseded, second_verified, recovered_pin]
    )
    first_page = _read(recovered, limit=1)
    second_page = _read(recovered, cursor=first_page.next_cursor, limit=200)
    assert first_page.next_cursor == "claim-1"
    assert [claim.claim_id for claim in second_page.claims] == ["claim-2"]
    assert recovered.claims["claim-2"].state == "verified"
    assert recovered.pinned is not None

def test_late_manifest_is_explicitly_superseded_and_blocks_prior_pin() -> None:
    proposed = _event(sequence=1, action="propose", event_id="proposal")
    verified = _event(sequence=2, action="verify", event_id="verify")
    verified.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-2"
    pinned = _event(sequence=3, action="pin", event_id="pin")
    pinned.details["authorizedResearchGraphV2"]["actor"]["actorId"] = "actor-2"
    superseded = _event(
        sequence=4, action="supersede", event_id="supersede", manifest_hash="b" * 64
    )

    folded = fold_authorized_research_graph_events([proposed, verified, pinned, superseded])

    assert folded.pinned is not None
    assert folded.pinned.blocked is True
    assert folded.claims["claim-1"].state == "superseded"
