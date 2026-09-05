"""Resolve and persist frozen delivery authorization without performing delivery."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.delivery_authorization import (
    DeliveryAuthorizationDecisionV1,
    DeliveryTarget,
    DeliveryTargetRevision,
)
from backend.models.studio import StudioWorkspace
from backend.schemas.delivery_authorization import (
    DeliveryAuthorizationCreateV1,
    DeliveryAuthorizationListV1,
    DeliveryAuthorizationReadV1,
    DeliveryAuthorizingActor,
    DeliveryClaimReadV1,
    DeliveryManifestReadV1,
    DeliveryTargetConfigureV1,
    DeliveryTargetListV1,
    DeliveryTargetReadV1,
)
from backend.schemas.evidence_manifest import ResearchGraphV2ManifestRef
from backend.security.controlled_receiver import (
    ControlledReceiverSecurityError,
    endpoint_config_hash,
    resolve_endpoint,
)
from backend.workflow.research_graph_v2 import (
    ResearchGraphV2Scope,
    lock_scoped_workflow_run,
    read_research_graph_v2,
)

_POLICY_VERSION = "controlled-receiver-policy-v2"
_PAYLOAD_SCHEMA_VERSION = "delivery-claim-manifest-v1"
_REDACTION_PROFILE_VERSION = "delivery-authorization-redaction-v1"
_CONTROLLED_RECEIVER_POLICY = {
    "receipt": {
        "required": True,
        "schemaVersion": "signed-receipt-v2",
        "invalidOrMissing": "unknown-fail-closed",
        "validRejected": "terminal",
    },
    "redirects": {"mode": "forbidden"},
    "timeout": {"perAttemptSeconds": 30},
    "retry": {
        "mode": "exact-idempotent",
        "retryOn": ["transport-timeout", "network-error", "http-5xx"],
        "maxAttempts": 3,
        "backoff": {
            "kind": "deterministic-exponential",
            "initialDelaySeconds": 1,
            "multiplier": 2,
            "maxDelaySeconds": 4,
        },
        "terminalOn": ["http-4xx", "valid-rejected-receipt"],
    },
    "continuation": {
        "exhaustedUnknown": "requires-reconciliation-blocked",
        "cancellation": "stop-new-attempts",
    },
    "compensation": {"automatic": False},
}


class DeliveryAuthorizationConflictError(RuntimeError):
    """No authorization was persisted because a required frozen binding failed."""


@dataclass(frozen=True)
class DeliveryAuthorizationScope:
    workspace_id: str
    project_id: str
    workflow_id: str
    studio_workflow_version_id: str
    run_id: str

    def graph_scope(self) -> ResearchGraphV2Scope:
        return ResearchGraphV2Scope(
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            workflow_id=self.workflow_id,
            studio_workflow_version_id=self.studio_workflow_version_id,
            run_id=self.run_id,
        )


async def _lock_scoped_run(db: AsyncSession, *, scope: DeliveryAuthorizationScope) -> None:
    run = await lock_scoped_workflow_run(
        db,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
    )
    if run is None:
        raise DeliveryAuthorizationConflictError("Scoped workflow run not found")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        ).decode()
    except Exception as exc:  # malformed cursor must not widen a scoped list
        raise DeliveryAuthorizationConflictError("Invalid cursor") from exc
    if not decoded or _cursor(decoded) != value:
        raise DeliveryAuthorizationConflictError("Invalid cursor")
    return decoded


def _current_policy() -> tuple[str, dict, str]:
    snapshot = dict(_CONTROLLED_RECEIVER_POLICY)
    return _POLICY_VERSION, snapshot, _canonical_hash(
        {"version": _POLICY_VERSION, "snapshot": snapshot}
    )


def _policy_hash() -> str:
    return _current_policy()[2]


def _target_read(revision: DeliveryTargetRevision, target: DeliveryTarget) -> DeliveryTargetReadV1:
    return DeliveryTargetReadV1(
        target_id=target.id,
        receiver_identity=target.receiver_identity,
        target_kind="controlled-receiver-v1",
        revision=revision.revision,
        configured_at=revision.created_at,
    )


def _manifest_read(ref: ResearchGraphV2ManifestRef) -> DeliveryManifestReadV1:
    if ref.materialization_status not in {"completed", "partial"}:
        raise DeliveryAuthorizationConflictError(
            "Only evidence-bearing terminal manifests may authorize delivery"
        )
    if not ref.record_refs:
        raise DeliveryAuthorizationConflictError(
            "Only manifests with materialized record evidence may authorize delivery"
        )
    return DeliveryManifestReadV1(
        batch_id=ref.batch_id,
        derivation=ref.derivation,
        reconciliation_revision=ref.reconciliation_revision,
        manifest_schema_version=ref.manifest_schema_version,
        manifest_hash=ref.manifest_hash,
        expected_record_key_set_hash=ref.expected_record_key_set_hash,
        record_ref_set_hash=ref.record_ref_set_hash,
        materialization_status=ref.materialization_status,
    )


def _decision_read(decision: DeliveryAuthorizationDecisionV1) -> DeliveryAuthorizationReadV1:
    return DeliveryAuthorizationReadV1(
        decision_id=decision.id,
        version="v1",
        operation_id=decision.operation_id,
        target_id=decision.target_id,
        target_revision=decision.target_revision,
        non_secret_config_hash=decision.non_secret_config_hash,
        policy_version=decision.policy_version,
        policy_hash=decision.policy_hash,
        pin_sequence=decision.pin_sequence,
        research_revision_id=decision.research_revision_id,
        manifest_set_hash=decision.manifest_set_hash,
        claims=[DeliveryClaimReadV1.model_validate(item) for item in decision.selected_claims],
        manifests=[DeliveryManifestReadV1.model_validate(item) for item in decision.manifest_set],
        payload_schema_version=decision.payload_schema_version,
        payload_hash=decision.payload_hash,
        redaction_profile_version=decision.redaction_profile_version,
        approver_actor_id=decision.approver_actor_id,
        approval_policy_version=decision.approval_policy_version,
        decision_hash=decision.decision_hash,
        decisioned_at=decision.decisioned_at,
    )


async def configure_delivery_target(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    request: DeliveryTargetConfigureV1,
) -> DeliveryTargetReadV1:
    """Create an immutable revision. Configuration is never accepted by authorization."""
    # Serializes target-revision append with authorization in this run; SQLite
    # also uses it as the write barrier for first-target allocation.
    await _lock_scoped_run(db, scope=scope)

    workspace = await db.scalar(
        select(StudioWorkspace)
        .where(StudioWorkspace.id == scope.workspace_id)
        .with_for_update()
    )
    if workspace is None:
        raise DeliveryAuthorizationConflictError("Controlled receiver workspace not found")
    try:
        endpoint = resolve_endpoint(request.endpoint_identity, request.credential_reference)
    except ControlledReceiverSecurityError as exc:
        raise DeliveryAuthorizationConflictError(
            "Controlled receiver registry configuration is unavailable"
        ) from exc
    if endpoint.receiver_identity != request.receiver_identity:
        raise DeliveryAuthorizationConflictError(
            "Controlled receiver identity does not match registry configuration"
        )


    target: DeliveryTarget | None = None
    if request.target_id:
        target = await db.scalar(
            select(DeliveryTarget)
            .where(
                DeliveryTarget.id == request.target_id,
                DeliveryTarget.workspace_id == scope.workspace_id,
            )
            .with_for_update()
        )
        if target is None or target.receiver_identity != request.receiver_identity:
            raise DeliveryAuthorizationConflictError("Controlled receiver target not found")
    else:
        target = await db.scalar(
            select(DeliveryTarget)
            .where(
                DeliveryTarget.workspace_id == scope.workspace_id,
                DeliveryTarget.receiver_identity == request.receiver_identity,
            )
            .with_for_update()
        )
        if target is None:
            candidate = DeliveryTarget(
                workspace_id=scope.workspace_id,
                receiver_identity=request.receiver_identity,
                target_kind="controlled-receiver-v1",
            )
            try:
                async with db.begin_nested():
                    db.add(candidate)
                    await db.flush()
            except IntegrityError:
                target = await db.scalar(
                    select(DeliveryTarget)
                    .where(
                        DeliveryTarget.workspace_id == scope.workspace_id,
                        DeliveryTarget.receiver_identity == request.receiver_identity,
                    )
                    .with_for_update()
                )
                if target is None:
                    raise
            else:
                target = candidate

    policy_version, policy_snapshot, policy_hash = _current_policy()
    for _ in range(2):
        latest = await db.scalar(
            select(DeliveryTargetRevision.revision)
            .where(DeliveryTargetRevision.target_id == target.id)
            .order_by(DeliveryTargetRevision.revision.desc())
            .limit(1)
        )
        revision = DeliveryTargetRevision(
            target_id=target.id,
            revision=(latest or 0) + 1,
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            workflow_id=scope.workflow_id,
            studio_workflow_version_id=scope.studio_workflow_version_id,
            run_id=scope.run_id,
            endpoint_identity=endpoint.identity,
            non_secret_config_hash=endpoint_config_hash(endpoint),
            credential_reference=endpoint.credential_reference,
            policy_version=policy_version,
            policy_snapshot=policy_snapshot,
            policy_hash=policy_hash,
        )
        try:
            async with db.begin_nested():
                db.add(revision)
                await db.flush()
        except IntegrityError:
            collision = await db.scalar(
                select(DeliveryTargetRevision.id).where(
                    DeliveryTargetRevision.target_id == target.id,
                    DeliveryTargetRevision.revision == revision.revision,
                )
            )
            if collision is None:
                raise
        else:
            return _target_read(revision, target)
    raise DeliveryAuthorizationConflictError(
        "Concurrent delivery target revision allocation conflicted"
    )


async def get_delivery_target(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    target_id: str,
) -> DeliveryTargetReadV1:
    row = await db.execute(
        select(DeliveryTargetRevision, DeliveryTarget)
        .join(DeliveryTarget, DeliveryTarget.id == DeliveryTargetRevision.target_id)
        .where(
            DeliveryTargetRevision.target_id == target_id,
            DeliveryTargetRevision.workspace_id == scope.workspace_id,
            DeliveryTargetRevision.project_id == scope.project_id,
            DeliveryTargetRevision.workflow_id == scope.workflow_id,
            DeliveryTargetRevision.studio_workflow_version_id == scope.studio_workflow_version_id,
            DeliveryTargetRevision.run_id == scope.run_id,
        )
        .order_by(DeliveryTargetRevision.revision.desc())
        .limit(1)
    )
    result = row.one_or_none()
    if result is None:
        raise DeliveryAuthorizationConflictError("Scoped delivery target not found")
    return _target_read(result.DeliveryTargetRevision, result.DeliveryTarget)


async def list_delivery_targets(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    cursor: str | None = None,
    limit: int = 50,
) -> DeliveryTargetListV1:
    after = _decode_cursor(cursor)
    page_limit = max(1, min(limit, 200))
    latest_revision = (
        select(
            DeliveryTargetRevision.id.label("revision_id"),
            func.row_number()
            .over(
                partition_by=DeliveryTargetRevision.target_id,
                order_by=DeliveryTargetRevision.revision.desc(),
            )
            .label("position"),
        )
        .where(
            DeliveryTargetRevision.workspace_id == scope.workspace_id,
            DeliveryTargetRevision.project_id == scope.project_id,
            DeliveryTargetRevision.workflow_id == scope.workflow_id,
            DeliveryTargetRevision.studio_workflow_version_id == scope.studio_workflow_version_id,
            DeliveryTargetRevision.run_id == scope.run_id,
        )
        .subquery()
    )
    stmt = (
        select(DeliveryTargetRevision, DeliveryTarget)
        .join(latest_revision, DeliveryTargetRevision.id == latest_revision.c.revision_id)
        .join(DeliveryTarget, DeliveryTarget.id == DeliveryTargetRevision.target_id)
        .where(latest_revision.c.position == 1)
        .order_by(DeliveryTargetRevision.target_id)
    )
    if after:
        stmt = stmt.where(DeliveryTargetRevision.target_id > after)
    rows = (await db.execute(stmt.limit(page_limit + 1))).all()
    page = rows[:page_limit]
    return DeliveryTargetListV1(
        items=[_target_read(revision, target) for revision, target in page],
        next_cursor=_cursor(page[-1][1].id) if len(rows) > len(page) and page else None,
    )


async def _current_target_revision(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    target_id: str,
) -> DeliveryTargetRevision:
    value = await db.scalar(
        select(DeliveryTargetRevision)
        .join(DeliveryTarget, DeliveryTarget.id == DeliveryTargetRevision.target_id)
        .where(
            DeliveryTargetRevision.target_id == target_id,
            DeliveryTarget.workspace_id == scope.workspace_id,
            DeliveryTargetRevision.workspace_id == scope.workspace_id,
            DeliveryTargetRevision.project_id == scope.project_id,
            DeliveryTargetRevision.workflow_id == scope.workflow_id,
            DeliveryTargetRevision.studio_workflow_version_id == scope.studio_workflow_version_id,
            DeliveryTargetRevision.run_id == scope.run_id,
        )
        .order_by(DeliveryTargetRevision.revision.desc())
        .limit(1)
        .with_for_update()
    )
    if value is None:
        raise DeliveryAuthorizationConflictError(
            "Current controlled receiver target is not in the requested scope"
        )
    return value


def _frozen_bindings(
    *,
    scope: DeliveryAuthorizationScope,
    request: DeliveryAuthorizationCreateV1,
    target: DeliveryTargetRevision,
    policy_version: str,
    policy_snapshot: dict,
    policy_hash: str,
    claims: list[DeliveryClaimReadV1],
    manifests: list[DeliveryManifestReadV1],
    actor: DeliveryAuthorizingActor,
) -> tuple[dict, str, dict]:
    projection = {
        "schemaVersion": _PAYLOAD_SCHEMA_VERSION,
        "claims": [claim.model_dump(by_alias=True) for claim in claims],
        "manifestHashes": [manifest.manifest_hash for manifest in manifests],
    }
    payload_hash = _canonical_hash(projection)
    payload_manifest = {
        "payloadSchemaVersion": _PAYLOAD_SCHEMA_VERSION,
        "payloadReference": "frozen-claim-manifest",
        "payloadHash": payload_hash,
        "sanctionedReferenceHashes": sorted(
            [claim.content_hash for claim in claims]
            + [manifest.manifest_hash for manifest in manifests]
        ),
        "redactionProfileVersion": _REDACTION_PROFILE_VERSION,
    }
    approval = {
        "policyDecisionId": _canonical_hash(
            {
                "scope": scope.__dict__,
                "actorId": actor.actor_id,
                "principal": actor.principal,
                "capability": actor.capability,
                "policyVersion": actor.policy_version,
            }
        ),
        "evidenceReference": "workspace-rbac-v1",
        "actorType": actor.actor_type,
        "actorId": actor.actor_id,
        "principal": actor.principal,
        "capability": actor.capability,
        "policyVersion": actor.policy_version,
    }
    binding = {
        "scope": scope.__dict__,
        "operationId": request.operation_id,
        "idempotencyKey": request.idempotency_key,
        "nodeId": request.node_id,
        "target": {
            "id": target.target_id,
            "revision": target.revision,
            "endpointIdentity": target.endpoint_identity,
            "configHash": target.non_secret_config_hash,
            "policyVersion": policy_version,
            "policyHash": policy_hash,
            "policySnapshot": policy_snapshot,
        },
        "pin": request.pinned_reference.model_dump(by_alias=True),
        "claims": [claim.model_dump(by_alias=True) for claim in claims],
        "manifests": [manifest.model_dump(by_alias=True) for manifest in manifests],
        "payload": payload_manifest,
        "approval": approval,
    }
    return binding, _canonical_hash(binding), payload_manifest


def _matches_binding(decision: DeliveryAuthorizationDecisionV1, binding_hash: str) -> bool:
    return decision.binding_hash == binding_hash


async def authorize_delivery(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    actor: DeliveryAuthorizingActor,
    request: DeliveryAuthorizationCreateV1,
) -> DeliveryAuthorizationReadV1:
    """Fail closed, freeze a V2 pin, and persist one idempotent authorization only."""

    if actor.capability != "actions.approve":
        raise DeliveryAuthorizationConflictError("Approval capability is required")
    # Shared first lock with V2 mutation and materialization keeps this pin fresh
    # until the caller commits the decision.
    await _lock_scoped_run(db, scope=scope)
    target = await _current_target_revision(db, scope=scope, target_id=request.target_id)
    policy_version, policy_snapshot, policy_hash = _current_policy()
    selected = {}
    cursor: str | None = None
    while True:
        graph = await read_research_graph_v2(
            db,
            scope=scope.graph_scope(),
            cursor=cursor,
            pinned_reference=request.pinned_reference,
            limit=200,
            require_pinned_reference=True,
        )
        if graph.blocker or graph.pinned_fold is None or graph.pinned_fold.blocked:
            raise DeliveryAuthorizationConflictError(
                "Pinned ResearchGraph V2 reference is absent, blocked, or stale"
            )
        selected.update(
            {
                claim.claim_id: claim
                for claim in graph.claims
                if claim.claim_id in request.selected_claim_ids
            }
        )
        if len(selected) == len(request.selected_claim_ids) or graph.next_cursor is None:
            break
        cursor = graph.next_cursor
    if len(selected) != len(request.selected_claim_ids):
        raise DeliveryAuthorizationConflictError(
            "Selected claims are unknown in the scoped pinned graph"
        )
    if any(claim.state != "verified" for claim in selected.values()):
        raise DeliveryAuthorizationConflictError(
            "Only independently verified claims may be authorized"
        )
    if any(claim.proposer_actor_id == actor.actor_id for claim in selected.values()):
        raise DeliveryAuthorizationConflictError(
            "Approvers may not authorize their own proposed claims"
        )
    claims = [
        DeliveryClaimReadV1(claim_id=claim.claim_id, content_hash=claim.content_hash)
        for claim in sorted(selected.values(), key=lambda value: value.claim_id)
    ]
    manifest_values: dict[tuple[str, str, int, str], DeliveryManifestReadV1] = {}
    for claim in selected.values():
        claim_manifests = {
            (
                ref.batch_id,
                ref.derivation,
                ref.reconciliation_revision,
                ref.manifest_hash,
            ): _manifest_read(ref)
            for ref in claim.manifest_refs
        }
        if not claim_manifests:
            raise DeliveryAuthorizationConflictError(
                "Every selected verified claim requires final manifest evidence"
            )
        manifest_values.update(claim_manifests)
    manifests = [manifest_values[key] for key in sorted(manifest_values)]
    binding, binding_hash, payload_manifest = _frozen_bindings(
        scope=scope,
        request=request,
        target=target,
        policy_version=policy_version,
        policy_snapshot=policy_snapshot,
        policy_hash=policy_hash,
        claims=claims,
        manifests=manifests,
        actor=actor,
    )
    existing_rows = (
        await db.execute(
            select(DeliveryAuthorizationDecisionV1)
            .where(
                DeliveryAuthorizationDecisionV1.workspace_id == scope.workspace_id,
                DeliveryAuthorizationDecisionV1.project_id == scope.project_id,
                DeliveryAuthorizationDecisionV1.workflow_id == scope.workflow_id,
                DeliveryAuthorizationDecisionV1.studio_workflow_version_id
                == scope.studio_workflow_version_id,
                DeliveryAuthorizationDecisionV1.run_id == scope.run_id,
                or_(
                    DeliveryAuthorizationDecisionV1.operation_id == request.operation_id,
                    DeliveryAuthorizationDecisionV1.idempotency_key == request.idempotency_key,
                ),
            )
            .with_for_update()
        )
    ).scalars().all()
    if existing_rows:
        if len(existing_rows) == 1 and _matches_binding(existing_rows[0], binding_hash):
            return _decision_read(existing_rows[0])
        raise DeliveryAuthorizationConflictError(
            "Operation or idempotency key was reused with changed frozen binding"
        )
    decided_at = datetime.now(UTC)
    decision = DeliveryAuthorizationDecisionV1(
        version="v1",
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
        node_id=request.node_id,
        operation_id=request.operation_id,
        idempotency_key=request.idempotency_key,
        target_id=target.target_id,
        target_revision_id=target.id,
        target_revision=target.revision,
        endpoint_identity=target.endpoint_identity,
        non_secret_config_hash=target.non_secret_config_hash,
        policy_version=policy_version,
        policy_snapshot=policy_snapshot,
        policy_hash=policy_hash,
        pin_sequence=request.pinned_reference.sequence,
        research_revision_id=request.pinned_reference.research_revision_id,
        manifest_set_hash=request.pinned_reference.manifest_set_hash,
        selected_claims=[claim.model_dump(by_alias=True) for claim in claims],
        manifest_set=[manifest.model_dump(by_alias=True) for manifest in manifests],
        sanitized_payload_manifest=payload_manifest,
        payload_schema_version=_PAYLOAD_SCHEMA_VERSION,
        payload_reference="frozen-claim-manifest",
        payload_hash=payload_manifest["payloadHash"],
        redaction_profile_version=_REDACTION_PROFILE_VERSION,
        approver_actor_id=actor.actor_id,
        approver_actor_type=actor.actor_type,
        approver_principal=actor.principal,
        approver_capability=actor.capability,
        approval_policy_version=actor.policy_version,
        approved_at=actor.authorized_at,
        approval_evidence=[
            {**binding["approval"], "decidedAt": actor.authorized_at.isoformat()}
        ],
        binding_hash=binding_hash,
        decision_hash=_canonical_hash(
            {
                "binding": binding,
                "approvalEvidence": [
                    {
                        **binding["approval"],
                        "decidedAt": actor.authorized_at.isoformat(),
                    }
                ],
                "decisionedAt": decided_at.isoformat(),
            }
        ),
        decisioned_at=decided_at,
    )
    try:
        async with db.begin_nested():
            db.add(decision)
            await db.flush()
    except IntegrityError:
        existing_rows = (
            await db.execute(
                select(DeliveryAuthorizationDecisionV1).where(
                    DeliveryAuthorizationDecisionV1.workspace_id == scope.workspace_id,
                    DeliveryAuthorizationDecisionV1.project_id == scope.project_id,
                    DeliveryAuthorizationDecisionV1.workflow_id == scope.workflow_id,
                    DeliveryAuthorizationDecisionV1.studio_workflow_version_id
                    == scope.studio_workflow_version_id,
                    DeliveryAuthorizationDecisionV1.run_id == scope.run_id,
                    or_(
                        DeliveryAuthorizationDecisionV1.operation_id == request.operation_id,
                        DeliveryAuthorizationDecisionV1.idempotency_key == request.idempotency_key,
                    ),
                )
            )
        ).scalars().all()
        if len(existing_rows) == 1 and _matches_binding(existing_rows[0], binding_hash):
            return _decision_read(existing_rows[0])
        raise DeliveryAuthorizationConflictError(
            "Concurrent authorization conflicted with frozen binding"
        )
    return _decision_read(decision)


async def get_delivery_authorization(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    decision_id: str,
) -> DeliveryAuthorizationReadV1:
    decision = await db.scalar(
        select(DeliveryAuthorizationDecisionV1).where(
            DeliveryAuthorizationDecisionV1.id == decision_id,
            DeliveryAuthorizationDecisionV1.workspace_id == scope.workspace_id,
            DeliveryAuthorizationDecisionV1.project_id == scope.project_id,
            DeliveryAuthorizationDecisionV1.workflow_id == scope.workflow_id,
            DeliveryAuthorizationDecisionV1.studio_workflow_version_id
            == scope.studio_workflow_version_id,
            DeliveryAuthorizationDecisionV1.run_id == scope.run_id,
        )
    )
    if decision is None:
        raise DeliveryAuthorizationConflictError("Scoped authorization decision not found")
    return _decision_read(decision)


async def list_delivery_authorizations(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    cursor: str | None = None,
    limit: int = 50,
) -> DeliveryAuthorizationListV1:
    after = _decode_cursor(cursor)
    stmt = (
        select(DeliveryAuthorizationDecisionV1)
        .where(
            DeliveryAuthorizationDecisionV1.workspace_id == scope.workspace_id,
            DeliveryAuthorizationDecisionV1.project_id == scope.project_id,
            DeliveryAuthorizationDecisionV1.workflow_id == scope.workflow_id,
            DeliveryAuthorizationDecisionV1.studio_workflow_version_id
            == scope.studio_workflow_version_id,
            DeliveryAuthorizationDecisionV1.run_id == scope.run_id,
        )
        .order_by(DeliveryAuthorizationDecisionV1.id)
    )
    if after:
        stmt = stmt.where(DeliveryAuthorizationDecisionV1.id > after)
    values = (await db.execute(stmt.limit(max(1, min(limit, 200)) + 1))).scalars().all()
    page = values[: max(1, min(limit, 200))]
    return DeliveryAuthorizationListV1(
        items=[_decision_read(value) for value in page],
        next_cursor=_cursor(page[-1].id) if len(values) > len(page) and page else None,
    )
