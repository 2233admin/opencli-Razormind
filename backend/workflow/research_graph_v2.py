"""Append-only, authorized ResearchGraph V2 overlay over WorkflowRunEvent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.iii_collection import (
    EvidenceBatchMaterializationManifestV1,
    IIICollectionExpectedKeyReportV1,
)
from backend.models.workflow_run import WorkflowRun, WorkflowRunEvent
from backend.schemas.evidence_manifest import ResearchGraphV2ManifestRef, record_ref_set_hash
from backend.schemas.research_graph_v2 import (
    AuthorizedResearchGraphEventV2,
    ResearchGraphV2ActorEvidence,
    ResearchGraphV2ClaimRead,
    ResearchGraphV2MutationRequest,
    ResearchGraphV2PinnedFoldRead,
    ResearchGraphV2PinnedReference,
    ResearchGraphV2Read,
)
from backend.schemas.workflow import WorkflowNodeRunEvent
from backend.workflow.workflow_run_events import (
    append_workflow_run_events,
    lock_scoped_workflow_run,
)

_EVENT_KEY = "authorizedResearchGraphV2"
_POLICY_VERSION = "workspace-rbac-v1"


class ResearchGraphV2ConflictError(RuntimeError):
    """A V2 mutation cannot safely advance the graph transcript."""


@dataclass(frozen=True)
class ResearchGraphV2Scope:
    workspace_id: str
    project_id: str
    workflow_id: str
    studio_workflow_version_id: str
    run_id: str


@dataclass
class _Claim:
    claim_id: str
    content_hash: str
    state: str
    proposer_actor_id: str
    manifest_refs: list[ResearchGraphV2ManifestRef]


@dataclass
class _Fold:
    sequence: int = 0
    tail_sequence: int = 0
    research_revision_id: str = "root"
    claims: dict[str, _Claim] = field(default_factory=dict)
    pinned: ResearchGraphV2PinnedFoldRead | None = None
    blocker: str | None = None


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _envelope(event: WorkflowNodeRunEvent) -> AuthorizedResearchGraphEventV2 | None:
    value = event.details.get(_EVENT_KEY)
    if not isinstance(value, dict):
        return None
    try:
        return AuthorizedResearchGraphEventV2.model_validate(value)
    except ValueError:
        return None

def _actor_is_authorized_for_action(envelope: AuthorizedResearchGraphEventV2) -> bool:
    required_capability = (
        "inbox.work" if envelope.action in {"context", "propose"} else "actions.approve"
    )
    return envelope.actor.capability == required_capability


def _manifest_set_hash(claims: dict[str, _Claim]) -> str:
    values = sorted(
        {
            (ref.batch_id, ref.derivation, ref.reconciliation_revision, ref.manifest_hash)
            for claim in claims.values()
            if claim.state == "verified"
            for ref in claim.manifest_refs
        }
    )
    return _canonical_hash(values)
def fold_authorized_research_graph_events(events: list[WorkflowNodeRunEvent]) -> _Fold:
    """Pure V2 replay; malformed or unauthorized historical rows never advance authority."""

    folded = _Fold()
    prior_sequence = 0
    for event in sorted(events, key=lambda item: item.sequence):
        envelope = _envelope(event)
        if (
            envelope is None
            or envelope.run_id != event.workflowRunId
            or envelope.workflow_id != event.workflowId
            or envelope.node_id != event.nodeId
            or envelope.expected_sequence != prior_sequence
            or not _actor_is_authorized_for_action(envelope)
        ):
            prior_sequence = event.sequence
            folded.tail_sequence = event.sequence
            continue
        if envelope.expected_revision != folded.research_revision_id:
            prior_sequence = event.sequence
            folded.tail_sequence = event.sequence
            continue
        claim = folded.claims.get(envelope.claim_id or "")
        accepted = False
        if envelope.action == "context":
            if (
                not envelope.claim_id
                and not envelope.claim_content_hash
                and envelope.manifest_refs
                and all(
                    ref.materialization_status == "completed_empty"
                    for ref in envelope.manifest_refs
                )
            ):
                accepted = True
        elif envelope.action == "propose":
            if (
                envelope.claim_id
                and envelope.claim_content_hash
                and envelope.claim_id not in folded.claims
                and envelope.manifest_refs
            ):
                folded.claims[envelope.claim_id] = _Claim(
                    claim_id=envelope.claim_id,
                    content_hash=envelope.claim_content_hash,
                    state="proposed",
                    proposer_actor_id=envelope.actor.actor_id,
                    manifest_refs=envelope.manifest_refs,
                )
                accepted = True
        elif envelope.action == "verify":
            if claim is not None and claim.state in {"proposed", "superseded"}:
                if claim.proposer_actor_id == envelope.actor.actor_id:
                    folded.blocker = "independent_review_required"
                else:
                    claim.state = "verified"
                    if folded.blocker == "independent_review_required":
                        folded.blocker = None
                    accepted = True
        elif envelope.action in {"reject", "retract"}:
            if (
                claim is not None
                and claim.state in {"proposed", "verified", "superseded"}
                and claim.proposer_actor_id != envelope.actor.actor_id
            ):
                claim.state = "rejected" if envelope.action == "reject" else "retracted"
                if folded.pinned is not None:
                    folded.pinned = folded.pinned.model_copy(update={"blocked": True})
                folded.blocker = "review_not_verified"
                accepted = True
        elif envelope.action == "pin":
            active_claims = [
                item
                for item in folded.claims.values()
                if item.state in {"proposed", "verified", "superseded"}
            ]
            if (
                envelope.pinned_sequence == event.sequence
                and active_claims
                and all(item.state == "verified" for item in active_claims)
            ):
                folded.pinned = ResearchGraphV2PinnedFoldRead(
                    sequence=event.sequence,
                    research_revision_id=envelope.research_revision_id,
                    manifest_set_hash=_manifest_set_hash(folded.claims),
                )
                folded.blocker = None
                accepted = True
            else:
                folded.blocker = "review_not_verified"
        elif envelope.action == "supersede":
            if (
                claim is not None
                and claim.state in {"proposed", "verified", "superseded"}
                and envelope.manifest_refs
            ):
                claim.manifest_refs = envelope.manifest_refs
                claim.proposer_actor_id = envelope.actor.actor_id
                claim.state = "superseded"
                if folded.pinned is not None:
                    folded.pinned = folded.pinned.model_copy(update={"blocked": True})
                folded.blocker = "manifest_superseded"
                accepted = True
        if accepted:
            folded.sequence = event.sequence
            folded.research_revision_id = envelope.research_revision_id
        prior_sequence = event.sequence
        folded.tail_sequence = event.sequence
    return folded


def _read(folded: _Fold, *, cursor: str | None = None, limit: int = 50) -> ResearchGraphV2Read:
    claims = sorted(folded.claims.values(), key=lambda item: item.claim_id)
    if cursor is not None:
        claims = [claim for claim in claims if claim.claim_id > cursor]
    page = claims[:limit]
    recovery = (
        "re_review"
        if folded.blocker in {
            "independent_review_required",
            "manifest_superseded",
            "review_not_verified",
        }
        else "none"
    )
    return ResearchGraphV2Read(
        sequence=folded.tail_sequence,
        research_revision_id=folded.research_revision_id,
        claims=[
            ResearchGraphV2ClaimRead(
                claim_id=claim.claim_id,
                content_hash=claim.content_hash,
                state=claim.state,
                proposer_actor_id=claim.proposer_actor_id,
                manifest_refs=claim.manifest_refs,
            )
            for claim in page
        ],
        next_cursor=page[-1].claim_id if len(claims) > len(page) else None,
        pinned_fold=folded.pinned,
        blocker=folded.blocker,
        recovery_action=recovery,
    )


async def read_research_graph_v2(
    db: AsyncSession,
    *,
    scope: ResearchGraphV2Scope,
    cursor: str | None = None,
    limit: int = 50,
    pinned_reference: ResearchGraphV2PinnedReference | None = None,
    require_pinned_reference: bool = False,
) -> ResearchGraphV2Read:
    rows = (
        await db.execute(
            select(WorkflowRunEvent)
            .where(WorkflowRunEvent.run_id == scope.run_id)
            .order_by(WorkflowRunEvent.sequence)
        )
    ).scalars().all()
    events = [WorkflowNodeRunEvent.model_validate(row.payload) for row in rows]
    read = await _read_with_manifest_freshness(
        db,
        scope=scope,
        folded=fold_authorized_research_graph_events(events),
        cursor=cursor,
        limit=max(1, min(limit, 200)),
    )
    return exact_pinned_reference(
        read, reference=pinned_reference, required=require_pinned_reference
    )



def exact_pinned_reference(
    read: ResearchGraphV2Read,
    *,
    reference: ResearchGraphV2PinnedReference | None,
    required: bool,
) -> ResearchGraphV2Read:
    if not required:
        return read
    pinned = read.pinned_fold
    matches = bool(
        reference
        and pinned
        and pinned.sequence == reference.sequence
        and pinned.research_revision_id == reference.research_revision_id
        and pinned.manifest_set_hash == reference.manifest_set_hash
        and not pinned.blocked
    )
    if matches:
        return read
    return read.model_copy(
        update={
            "pinned_fold": pinned.model_copy(update={"blocked": True}) if pinned else None,
            "blocker": "pinned_reference_mismatch",
            "recovery_action": "re_review",
        }
    )

def _key_set(values: list[dict]) -> set[tuple[str, str]]:
    return {
        (
            str(item.get("source_id", item.get("sourceId", ""))),
            str(item.get("event_id", item.get("eventId", ""))),
        )
        for item in values
    }


async def _validate_manifest_refs(
    db: AsyncSession,
    *,
    scope: ResearchGraphV2Scope,
    refs: list[ResearchGraphV2ManifestRef],
) -> None:
    if not refs:
        raise ResearchGraphV2ConflictError("A claim requires a terminal manifest reference")
    for ref in refs:
        manifest = (
            await db.execute(
                select(EvidenceBatchMaterializationManifestV1).where(
                    EvidenceBatchMaterializationManifestV1.workspace_id == scope.workspace_id,
                    EvidenceBatchMaterializationManifestV1.project_id == scope.project_id,
                    EvidenceBatchMaterializationManifestV1.workflow_id == scope.workflow_id,
                    EvidenceBatchMaterializationManifestV1.studio_workflow_version_id
                    == scope.studio_workflow_version_id,
                    EvidenceBatchMaterializationManifestV1.run_id == scope.run_id,
                    EvidenceBatchMaterializationManifestV1.batch_id == ref.batch_id,
                    EvidenceBatchMaterializationManifestV1.reconciliation_revision
                    == ref.reconciliation_revision,
                )
            )
        ).scalar_one_or_none()
        if manifest is None or manifest.version != ref.manifest_schema_version:
            raise ResearchGraphV2ConflictError("Manifest scope or schema version mismatch")
        latest = (
            await db.execute(
                select(EvidenceBatchMaterializationManifestV1)
                .where(
                    EvidenceBatchMaterializationManifestV1.workspace_id == scope.workspace_id,
                    EvidenceBatchMaterializationManifestV1.project_id == scope.project_id,
                    EvidenceBatchMaterializationManifestV1.workflow_id == scope.workflow_id,
                    EvidenceBatchMaterializationManifestV1.studio_workflow_version_id
                    == scope.studio_workflow_version_id,
                    EvidenceBatchMaterializationManifestV1.run_id == scope.run_id,
                    EvidenceBatchMaterializationManifestV1.batch_id == ref.batch_id,
                )
                .order_by(EvidenceBatchMaterializationManifestV1.reconciliation_revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is None or latest.id != manifest.id:
            raise ResearchGraphV2ConflictError(
                "Manifest revision is stale; append an explicit supersession"
            )
        if (
            manifest.derivation != ref.derivation
            or manifest.manifest_hash != ref.manifest_hash
            or manifest.expected_key_set_hash != ref.expected_record_key_set_hash
            or manifest.materialization_status != ref.materialization_status
        ):
            raise ResearchGraphV2ConflictError("Manifest tuple mismatch")
        if manifest.materialization_status not in {"completed", "completed_empty", "partial"}:
            raise ResearchGraphV2ConflictError("Manifest is not eligible for ResearchGraph V2")
        if record_ref_set_hash(manifest.record_references) != ref.record_ref_set_hash:
            raise ResearchGraphV2ConflictError("Manifest record reference hash mismatch")
        present = {
            (
                str(item.get("source_id", item.get("sourceId", ""))),
                str(item.get("event_id", item.get("eventId", ""))),
                int(item.get("odp_record_id", item.get("odpRecordId", 0))),
            )
            for item in manifest.record_references
        }
        supplied = {(item.source_id, item.event_id, item.odp_record_id) for item in ref.record_refs}
        if supplied != present:
            raise ResearchGraphV2ConflictError(
                "Claim record references must exactly match the manifest"
            )
        if manifest.materialization_status == "completed_empty" and ref.record_refs:
            raise ResearchGraphV2ConflictError("Empty manifest cannot contribute claim evidence")
        if manifest.materialization_status == "partial":
            report = (
                await db.execute(
                    select(IIICollectionExpectedKeyReportV1).where(
                        IIICollectionExpectedKeyReportV1.report_id == manifest.report_id
                    )
                )
            ).scalar_one_or_none()
            if report is None:
                raise ResearchGraphV2ConflictError(
                    "Partial manifest has no final expected-key report"
                )
            missing = _key_set(report.expected_keys) - {
                (source_id, event_id) for source_id, event_id, _ in present
            }
            excluded = {(item.source_id, item.event_id) for item in ref.excluded_item_keys}
            if excluded != missing:
                raise ResearchGraphV2ConflictError("Partial manifest exclusions are incomplete")


async def _latest_manifest(
    db: AsyncSession, *, scope: ResearchGraphV2Scope, batch_id: str
) -> EvidenceBatchMaterializationManifestV1 | None:
    return (
        await db.execute(
            select(EvidenceBatchMaterializationManifestV1)
            .where(
                EvidenceBatchMaterializationManifestV1.workspace_id == scope.workspace_id,
                EvidenceBatchMaterializationManifestV1.project_id == scope.project_id,
                EvidenceBatchMaterializationManifestV1.workflow_id == scope.workflow_id,
                EvidenceBatchMaterializationManifestV1.studio_workflow_version_id
                == scope.studio_workflow_version_id,
                EvidenceBatchMaterializationManifestV1.run_id == scope.run_id,
                EvidenceBatchMaterializationManifestV1.batch_id == batch_id,
            )
            .order_by(EvidenceBatchMaterializationManifestV1.reconciliation_revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _matches_manifest(
    manifest: EvidenceBatchMaterializationManifestV1 | None, ref: ResearchGraphV2ManifestRef
) -> bool:
    return bool(
        manifest
        and manifest.reconciliation_revision == ref.reconciliation_revision
        and manifest.version == ref.manifest_schema_version
        and manifest.derivation == ref.derivation
        and manifest.manifest_hash == ref.manifest_hash
        and manifest.expected_key_set_hash == ref.expected_record_key_set_hash
        and manifest.materialization_status == ref.materialization_status
        and record_ref_set_hash(manifest.record_references) == ref.record_ref_set_hash
    )


async def _read_with_manifest_freshness(
    db: AsyncSession,
    *,
    scope: ResearchGraphV2Scope,
    folded: _Fold,
    cursor: str | None = None,
    limit: int = 50,
) -> ResearchGraphV2Read:
    read = _read(folded, cursor=cursor, limit=limit)
    active_claims = [
        claim
        for claim in folded.claims.values()
        if claim.state in {"proposed", "verified", "superseded"}
    ]
    stale = False
    for claim in active_claims:
        for ref in claim.manifest_refs:
            latest = await _latest_manifest(db, scope=scope, batch_id=ref.batch_id)
            if not _matches_manifest(latest, ref):
                stale = True
                break
        if stale:
            break
    if read.pinned_fold is not None and (
        read.pinned_fold.research_revision_id != folded.research_revision_id
        or read.pinned_fold.manifest_set_hash != _manifest_set_hash(folded.claims)
    ):
        stale = True
    if not stale:
        return read
    pinned = (
        read.pinned_fold.model_copy(update={"blocked": True})
        if read.pinned_fold is not None
        else None
    )
    return read.model_copy(
        update={
            "pinned_fold": pinned,
            "blocker": "manifest_superseded",
            "recovery_action": "re_review",
        }
    )


def _event_from_envelope(
    *, run: WorkflowRun, node_id: str, envelope: AuthorizedResearchGraphEventV2
) -> WorkflowNodeRunEvent:
    return WorkflowNodeRunEvent(
        id=f"research-graph-v2:{envelope.event_id}",
        sequence=envelope.expected_sequence + 1,
        workflowId=run.workflow_id,
        workflowRunId=run.id,
        traceId=run.trace_id,
        nodeId=node_id,
        eventType="completed",
        createdAt=datetime.now(UTC).isoformat(),
        details={_EVENT_KEY: envelope.model_dump(by_alias=True, mode="json")},
    )


async def append_research_graph_v2_mutation(
    db: AsyncSession,
    *,
    scope: ResearchGraphV2Scope,
    actor: ResearchGraphV2ActorEvidence,
    request: ResearchGraphV2MutationRequest,
) -> ResearchGraphV2Read:
    """Authorize, CAS-bind, and append exactly one V2 event in the run transaction."""

    run = await lock_scoped_workflow_run(
        db,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
    )
    if run is None:
        raise ResearchGraphV2ConflictError("Scoped workflow run not found")
    rows = (
        await db.execute(
            select(WorkflowRunEvent)
            .where(WorkflowRunEvent.run_id == run.id)
            .order_by(WorkflowRunEvent.sequence)
        )
    ).scalars().all()
    events = [WorkflowNodeRunEvent.model_validate(row.payload) for row in rows]
    folded = fold_authorized_research_graph_events(events)
    event_id = f"research-graph-v2:{request.idempotency_key}"
    existing = next((event for event in events if event.id == event_id), None)
    if existing is not None:
        envelope = _envelope(existing)
        if envelope is None or (
            envelope.action != request.action
            or envelope.expected_sequence != request.expected_sequence
            or envelope.expected_revision != request.expected_revision
            or envelope.node_id != request.node_id
            or envelope.claim_id != request.claim_id
            or envelope.claim_content_hash != request.claim_content_hash
            or envelope.manifest_refs != request.manifest_refs
            or envelope.supersedes_event_id != request.supersedes_event_id
            or envelope.actor.actor_id != actor.actor_id
            or envelope.actor.capability != actor.capability
        ):
            raise ResearchGraphV2ConflictError(
                "Idempotency key was reused with changed content or authority"
            )
        return await _read_with_manifest_freshness(db, scope=scope, folded=folded)
    tail_sequence = events[-1].sequence if events else 0
    if (
        request.expected_sequence != tail_sequence
        or request.expected_revision != folded.research_revision_id
    ):
        raise ResearchGraphV2ConflictError("ResearchGraph V2 sequence or revision is stale")
    if request.action == "context":
        if request.claim_id or request.claim_content_hash:
            raise ResearchGraphV2ConflictError("Empty-manifest context cannot create a claim")
        await _validate_manifest_refs(db, scope=scope, refs=request.manifest_refs)
        if not request.manifest_refs or any(
            ref.materialization_status != "completed_empty" for ref in request.manifest_refs
        ):
            raise ResearchGraphV2ConflictError("Context requires completed-empty manifests only")
    elif request.action == "propose":
        if not request.claim_id or not request.claim_content_hash:
            raise ResearchGraphV2ConflictError(
                "Proposal requires a claim identity and content hash"
            )
        await _validate_manifest_refs(db, scope=scope, refs=request.manifest_refs)
        if any(ref.materialization_status == "completed_empty" for ref in request.manifest_refs):
            raise ResearchGraphV2ConflictError(
                "Empty manifests cannot create a ResearchGraph claim"
            )
    elif request.action in {"verify", "reject", "retract", "supersede"}:
        claim = folded.claims.get(request.claim_id or "")
        if claim is None:
            raise ResearchGraphV2ConflictError("Claim does not exist in this graph")
        allowed_states = {
            "verify": {"proposed", "superseded"},
            "reject": {"proposed", "verified", "superseded"},
            "retract": {"proposed", "verified", "superseded"},
            "supersede": {"proposed", "verified", "superseded"},
        }
        if claim.state not in allowed_states[request.action]:
            raise ResearchGraphV2ConflictError("Invalid claim transition")
        if (
            request.action in {"verify", "reject", "retract"}
            and claim.proposer_actor_id == actor.actor_id
        ):
            raise ResearchGraphV2ConflictError("Independent review prohibits self-review")
        if request.action == "verify":
            await _validate_manifest_refs(db, scope=scope, refs=claim.manifest_refs)
        if request.action == "supersede":
            if request.supersedes_event_id is None:
                raise ResearchGraphV2ConflictError(
                    "Supersession requires its prior graph event identity"
                )
            prior = next(
                (event for event in events if event.id == request.supersedes_event_id),
                None,
            )
            if (
                prior is None
                or (_envelope(prior) is None)
                or _envelope(prior).claim_id != claim.claim_id
            ):
                raise ResearchGraphV2ConflictError(
                    "Supersession does not bind the claimed prior event"
                )
            if {ref.batch_id for ref in request.manifest_refs} != {
                ref.batch_id for ref in claim.manifest_refs
            } or request.manifest_refs == claim.manifest_refs:
                raise ResearchGraphV2ConflictError(
                    "Supersession requires changed evidence for the same manifest batch"
                )
            await _validate_manifest_refs(db, scope=scope, refs=request.manifest_refs)
    elif request.action == "pin":
        active_claims = [
            claim
            for claim in folded.claims.values()
            if claim.state in {"proposed", "verified", "superseded"}
        ]
        if not active_claims or any(claim.state != "verified" for claim in active_claims):
            raise ResearchGraphV2ConflictError(
                "Only a fully independently reviewed fold may be pinned"
            )
        for claim in active_claims:
            await _validate_manifest_refs(db, scope=scope, refs=claim.manifest_refs)
    next_revision = _canonical_hash(
        {
            "previous": folded.research_revision_id,
            "event": request.idempotency_key,
            "action": request.action,
        }
    )
    envelope = AuthorizedResearchGraphEventV2(
        event_id=request.idempotency_key,
        action=request.action,
        expected_sequence=tail_sequence,
        expected_revision=folded.research_revision_id,
        research_revision_id=next_revision,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
        node_id=request.node_id,
        claim_id=request.claim_id,
        claim_content_hash=request.claim_content_hash,
        manifest_refs=request.manifest_refs,
        actor=actor,
        supersedes_event_id=request.supersedes_event_id,
        pinned_sequence=tail_sequence + 1 if request.action == "pin" else None,
    )
    appended = await append_workflow_run_events(
        db,
        run_id=run.id,
        events=[_event_from_envelope(run=run, node_id=request.node_id, envelope=envelope)],
    )
    result_events = [*events, *appended.appended_events]
    return await _read_with_manifest_freshness(
        db, scope=scope, folded=fold_authorized_research_graph_events(result_events)
    )


def actor_evidence(
    *, actor_id: str, principal: str, capability: str
) -> ResearchGraphV2ActorEvidence:
    return ResearchGraphV2ActorEvidence(
        actor_type="user",
        actor_id=actor_id,
        principal=principal,
        capability=capability,
        policy_version=_POLICY_VERSION,
        authorized_at=datetime.now(UTC),
    )
