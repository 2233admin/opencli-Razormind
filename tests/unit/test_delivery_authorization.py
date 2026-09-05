import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from backend.models.delivery_authorization import DeliveryTarget
from backend.schemas.delivery_authorization import (
    DeliveryAuthorizationCreateV1,
    DeliveryTargetConfigureV1,
)
from backend.schemas.research_graph_v2 import (
    ResearchGraphV2ActorEvidence,
    ResearchGraphV2ClaimRead,
    ResearchGraphV2PinnedFoldRead,
    ResearchGraphV2Read,
)
from backend.workflow import delivery_authorization
from backend.workflow.delivery_authorization import (
    DeliveryAuthorizationConflictError,
    DeliveryAuthorizationScope,
    _decode_cursor,
    _policy_hash,
)


def _pin() -> dict:
    return {
        "sequence": 3,
        "researchRevisionId": "revision-3",
        "manifestSetHash": "m" * 64,
    }


def _request() -> DeliveryAuthorizationCreateV1:
    return DeliveryAuthorizationCreateV1.model_validate(
        {
            "operationId": "operation-1",
            "idempotencyKey": "key-1",
            "nodeId": "node-1",
            "targetId": "target-1",
            "pinnedReference": _pin(),
            "selectedClaimIds": ["claim-1"],
        }
    )


@pytest.fixture(autouse=True)
def bypass_database_run_lock(monkeypatch, request):
    if request.node.name == "test_run_lock_is_a_scoped_postgres_write_lock":
        return

    async def lock(_db, *, scope):
        return object()

    monkeypatch.setattr(delivery_authorization, "_lock_scoped_run", lock)


def test_authorization_contract_rejects_client_authority_payload_and_duplicate_claims() -> None:
    base = _request().model_dump(by_alias=True)
    for forbidden_field in (
        "actorId",
        "capability",
        "policySnapshot",
        "publishAllowed",
        "targetRevision",
    ):
        with pytest.raises(ValidationError):
            DeliveryAuthorizationCreateV1.model_validate(
                {**base, forbidden_field: "client-controlled"}
            )
    with pytest.raises(ValidationError):
        DeliveryAuthorizationCreateV1.model_validate(
            {**base, "selectedClaimIds": ["claim-1", "claim-1"]}
        )


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("receiverIdentity", "sk_live_raw-secret"),
        ("endpointIdentity", "https://receiver.example?token=unsafe"),
        ("credentialReference", "credential-ref:raw-secret"),
    ],
)
def test_target_configuration_rejects_nonopaque_values_and_policy_is_stable(
    field, unsafe_value
) -> None:
    values = {
        "receiverIdentity": "controlled-receiver-1",
        "endpointIdentity": "receiver-channel-1",
        "credentialReference": "credential-reference-1",
    }
    with pytest.raises(ValidationError):
        DeliveryTargetConfigureV1.model_validate({**values, field: unsafe_value})
    assert _policy_hash() == _policy_hash()
    with pytest.raises(ValidationError):
        DeliveryTargetConfigureV1.model_validate({**values, "nonSecretConfigHash": "c" * 64})


def test_controlled_receiver_v2_policy_snapshot_is_complete_and_hash_bound(monkeypatch) -> None:
    version, snapshot, policy_hash = delivery_authorization._current_policy()
    assert version == "controlled-receiver-policy-v2"
    assert snapshot == {
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
    assert policy_hash == delivery_authorization._canonical_hash(
        {"version": version, "snapshot": snapshot}
    )
    monkeypatch.setitem(
        delivery_authorization._CONTROLLED_RECEIVER_POLICY,
        "timeout",
        {"perAttemptSeconds": 31},
    )
    assert delivery_authorization._policy_hash() != policy_hash


@pytest.mark.parametrize("cursor", ("%%not-a-cursor%%", "!!!!", ""))
def test_cursor_rejects_malformed_values_without_widening_a_scoped_list(cursor) -> None:
    with pytest.raises(Exception, match="Invalid cursor"):
        _decode_cursor(cursor)

class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _ConcurrentTargetSession:
    def __init__(self, winner: DeliveryTarget):
        self._values = iter([object(), None, winner, 1])
        self._pending = None
        self.target_insert_attempts = 0

    async def scalar(self, *_args, **_kwargs):
        return next(self._values)

    def begin_nested(self):
        return _NestedTransaction()

    def add(self, value):
        self._pending = value

    async def flush(self):
        if isinstance(self._pending, DeliveryTarget):
            self.target_insert_attempts += 1
            raise IntegrityError(
                "insert delivery target", {}, RuntimeError("unique target identity")
            )
        self._pending.created_at = datetime.now(UTC)


@pytest.mark.asyncio
async def test_concurrent_new_receiver_reselects_unique_winner_before_revision(monkeypatch) -> None:
    endpoint = SimpleNamespace(
        identity="receiver-channel-1",
        receiver_identity="controlled-receiver-1",
        credential_reference="credential-reference-1",
    )
    monkeypatch.setattr(delivery_authorization, "resolve_endpoint", lambda *_args: endpoint)
    monkeypatch.setattr(delivery_authorization, "endpoint_config_hash", lambda _endpoint: "c" * 64)
    winner = DeliveryTarget(
        id="target-1",
        workspace_id="workspace-1",
        receiver_identity="controlled-receiver-1",
        target_kind="controlled-receiver-v1",
    )
    session = _ConcurrentTargetSession(winner)
    configured = await delivery_authorization.configure_delivery_target(
        session,
        scope=DeliveryAuthorizationScope(
            "workspace-1", "project-1", "workflow-1", "version-1", "run-1"
        ),
        request=DeliveryTargetConfigureV1(
            receiver_identity="controlled-receiver-1",
            endpoint_identity="receiver-channel-1",
            credential_reference="credential-reference-1",
        ),
    )
    assert session.target_insert_attempts == 1
    assert configured.target_id == winner.id
    assert configured.revision == 2



@pytest.mark.asyncio
async def test_run_lock_is_a_scoped_postgres_write_lock() -> None:
    class Session:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def scalar(self, statement):
            compiled = str(statement.compile(dialect=postgresql.dialect()))
            assert "FOR UPDATE" in compiled
            assert "workflow_runs" in compiled
            return object()

    await delivery_authorization._lock_scoped_run(
        Session(),
        scope=DeliveryAuthorizationScope(
            "workspace", "project", "workflow", "version", "run"
        ),
    )


@pytest.mark.asyncio
async def test_authorization_decision_commits_before_shared_run_lock_allows_stale_pin_mutation(
    monkeypatch,
) -> None:
    run_lock = asyncio.Lock()
    amendment_committed = asyncio.Event()

    class Rows:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        def __init__(self):
            self.decision = None
            self.decision_committed = False

        async def execute(self, *_args, **_kwargs):
            return Rows()

        def begin_nested(self):
            return _NestedTransaction()

        def add(self, value):
            value.id = "decision-1"
            self.decision = value

        async def flush(self):
            assert run_lock.locked()

        async def commit(self):
            assert self.decision is not None
            self.decision_committed = True
            run_lock.release()

    async def lock_run(*_args, **_kwargs):
        await run_lock.acquire()
        return object()

    async def current_target(*_args, **_kwargs):
        return SimpleNamespace(
            target_id="target-1",
            id="target-revision-1",
            revision=1,
            endpoint_identity="receiver-channel-1",
            non_secret_config_hash="h" * 64,
        )

    async def graph_read(*_args, **_kwargs):
        assert run_lock.locked()
        return ResearchGraphV2Read(
            sequence=3,
            research_revision_id="revision-3",
            claims=[
                ResearchGraphV2ClaimRead(
                    claim_id="claim-1",
                    content_hash="c" * 64,
                    state="verified",
                    proposer_actor_id="other-user",
                    manifest_refs=[
                        {
                            "batchId": "batch-1",
                            "derivation": "dispatch-task-v1",
                            "reconciliationRevision": 1,
                            "manifestSchemaVersion": "v1",
                            "manifestHash": "m" * 64,
                            "expectedRecordKeySetHash": "k" * 64,
                            "recordRefSetHash": "r" * 64,
                            "materializationStatus": "completed",
                            "recordRefs": [
                                {"sourceId": "source-1", "eventId": "event-1", "odpRecordId": 1}
                            ],
                        }
                    ],
                )
            ],
            pinned_fold=ResearchGraphV2PinnedFoldRead(
                sequence=3,
                research_revision_id="revision-3",
                manifest_set_hash="m" * 64,
            ),
        )

    async def amend_pin():
        async with run_lock:
            assert session.decision_committed
            amendment_committed.set()

    monkeypatch.setattr(delivery_authorization, "_lock_scoped_run", lock_run)
    monkeypatch.setattr(delivery_authorization, "_current_target_revision", current_target)
    monkeypatch.setattr(delivery_authorization, "read_research_graph_v2", graph_read)
    session = Session()
    await delivery_authorization.authorize_delivery(
        session,
        scope=DeliveryAuthorizationScope("workspace", "project", "workflow", "version", "run"),
        actor=ResearchGraphV2ActorEvidence(
            actor_type="user",
            actor_id="approver",
            principal="approver",
            capability="actions.approve",
            policy_version="workspace-rbac-v1",
            authorized_at="2026-08-30T00:00:00Z",
        ),
        request=_request(),
    )
    amendment = asyncio.create_task(amend_pin())
    await asyncio.sleep(0)
    assert not amendment_committed.is_set()
    await session.commit()
    await amendment
    assert amendment_committed.is_set()

@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["proposed", "superseded", "rejected", "retracted"])
async def test_authorization_rejects_nonverified_v2_claims(monkeypatch, state: str) -> None:
    async def current_target(*_args, **_kwargs):
        return object()

    async def graph_read(*_args, **_kwargs):
        return ResearchGraphV2Read(
            sequence=3,
            research_revision_id="revision-3",
            claims=[
                ResearchGraphV2ClaimRead(
                    claim_id="claim-1",
                    content_hash="c" * 64,
                    state=state,
                    manifest_refs=[],
                    proposer_actor_id="other-user",
                )
            ],
            pinned_fold=ResearchGraphV2PinnedFoldRead(
                sequence=3,
                research_revision_id="revision-3",
                manifest_set_hash="m" * 64,
            ),
        )

    monkeypatch.setattr(delivery_authorization, "_current_target_revision", current_target)
    monkeypatch.setattr(delivery_authorization, "read_research_graph_v2", graph_read)
    with pytest.raises(DeliveryAuthorizationConflictError, match="verified claims"):
        await delivery_authorization.authorize_delivery(
            None,
            scope=DeliveryAuthorizationScope("w", "p", "wf", "v", "run"),
            actor=ResearchGraphV2ActorEvidence(
                actor_type="user",
                actor_id="approver",
                principal="approver",
                capability="actions.approve",
                policy_version="workspace-rbac-v1",
                authorized_at="2026-08-30T00:00:00Z",
            ),
            request=_request(),
        )


@pytest.mark.asyncio
async def test_authorization_rejects_all_excluded_partial_without_record_evidence(
    monkeypatch,
) -> None:
    async def current_target(*_args, **_kwargs):
        return object()

    async def graph_read(*_args, **_kwargs):
        return ResearchGraphV2Read(
            sequence=3,
            research_revision_id="revision-3",
            claims=[
                ResearchGraphV2ClaimRead(
                    claim_id="claim-1",
                    content_hash="c" * 64,
                    state="verified",
                    proposer_actor_id="other-user",
                    manifest_refs=[
                        {
                            "batchId": "batch-1",
                            "derivation": "dispatch-task-v1",
                            "reconciliationRevision": 1,
                            "manifestSchemaVersion": "v1",
                            "manifestHash": "m" * 64,
                            "expectedRecordKeySetHash": "k" * 64,
                            "recordRefSetHash": "r" * 64,
                            "materializationStatus": "partial",
                        }
                    ],
                )
            ],
            pinned_fold=ResearchGraphV2PinnedFoldRead(
                sequence=3,
                research_revision_id="revision-3",
                manifest_set_hash="m" * 64,
            ),
        )

    monkeypatch.setattr(delivery_authorization, "_current_target_revision", current_target)
    monkeypatch.setattr(delivery_authorization, "read_research_graph_v2", graph_read)
    with pytest.raises(DeliveryAuthorizationConflictError, match="materialized record evidence"):
        await delivery_authorization.authorize_delivery(
            None,
            scope=DeliveryAuthorizationScope("w", "p", "wf", "v", "run"),
            actor=ResearchGraphV2ActorEvidence(
                actor_type="user",
                actor_id="approver",
                principal="approver",
                capability="actions.approve",
                policy_version="workspace-rbac-v1",
                authorized_at="2026-08-30T00:00:00Z",
            ),
            request=_request(),
        )



def test_evidence_bearing_partial_manifest_is_eligible() -> None:
    manifest = delivery_authorization._manifest_read(
        ResearchGraphV2ClaimRead(
            claim_id="claim-1",
            content_hash="c" * 64,
            state="verified",
            proposer_actor_id="other-user",
            manifest_refs=[
                {
                    "batchId": "batch-1",
                    "derivation": "dispatch-task-v1",
                    "reconciliationRevision": 1,
                    "manifestSchemaVersion": "v1",
                    "manifestHash": "m" * 64,
                    "expectedRecordKeySetHash": "k" * 64,
                    "recordRefSetHash": "r" * 64,
                    "materializationStatus": "partial",
                    "recordRefs": [
                        {"sourceId": "source-1", "eventId": "event-1", "odpRecordId": 1}
                    ],
                }
            ],
        ).manifest_refs[0]
    )
    assert manifest.materialization_status == "partial"


@pytest.mark.asyncio
async def test_authorization_pages_exact_pinned_graph_until_selected_claim_is_found(
    monkeypatch,
) -> None:
    calls: list[str | None] = []

    async def current_target(*_args, **_kwargs):
        return object()

    async def graph_read(*_args, **kwargs):
        cursor = kwargs["cursor"]
        calls.append(cursor)
        if cursor is None:
            return ResearchGraphV2Read(
                sequence=3,
                research_revision_id="revision-3",
                next_cursor="claim-page-2",
                pinned_fold=ResearchGraphV2PinnedFoldRead(
                    sequence=3,
                    research_revision_id="revision-3",
                    manifest_set_hash="m" * 64,
                ),
            )
        return ResearchGraphV2Read(
            sequence=3,
            research_revision_id="revision-3",
            claims=[
                ResearchGraphV2ClaimRead(
                    claim_id="claim-1",
                    content_hash="c" * 64,
                    state="verified",
                    proposer_actor_id="approver",
                    manifest_refs=[
                        {
                            "batchId": "batch-1",
                            "derivation": "dispatch-task-v1",
                            "reconciliationRevision": 1,
                            "manifestSchemaVersion": "v1",
                            "manifestHash": "m" * 64,
                            "expectedRecordKeySetHash": "k" * 64,
                            "recordRefSetHash": "r" * 64,
                            "materializationStatus": "completed",
                            "recordRefs": [
                                {"sourceId": "source-1", "eventId": "event-1", "odpRecordId": 1}
                            ],
                        }
                    ],
                )
            ],
            pinned_fold=ResearchGraphV2PinnedFoldRead(
                sequence=3,
                research_revision_id="revision-3",
                manifest_set_hash="m" * 64,
            ),
        )

    monkeypatch.setattr(delivery_authorization, "_current_target_revision", current_target)
    monkeypatch.setattr(delivery_authorization, "read_research_graph_v2", graph_read)
    with pytest.raises(DeliveryAuthorizationConflictError, match="own proposed claims"):
        await delivery_authorization.authorize_delivery(
            None,
            scope=DeliveryAuthorizationScope("w", "p", "wf", "v", "run"),
            actor=ResearchGraphV2ActorEvidence(
                actor_type="user",
                actor_id="approver",
                principal="approver",
                capability="actions.approve",
                policy_version="workspace-rbac-v1",
                authorized_at="2026-08-30T00:00:00Z",
            ),
            request=_request(),
        )
    assert calls == [None, "claim-page-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "graph",
    [
        ResearchGraphV2Read(
            sequence=0,
            research_revision_id="root",
            blocker="pinned_reference_mismatch",
            recovery_action="re_review",
        ),
        ResearchGraphV2Read(
            sequence=3,
            research_revision_id="revision-3",
            pinned_fold=ResearchGraphV2PinnedFoldRead(
                sequence=3,
                research_revision_id="revision-3",
                manifest_set_hash="m" * 64,
                blocked=True,
            ),
        ),
        ResearchGraphV2Read(
            sequence=3,
            research_revision_id="revision-3",
            pinned_fold=ResearchGraphV2PinnedFoldRead(
                sequence=3,
                research_revision_id="revision-3",
                manifest_set_hash="m" * 64,
            ),
        ),
    ],
)
async def test_authorization_fails_closed_for_blocked_or_empty_graph(monkeypatch, graph) -> None:
    async def current_target(*_args, **_kwargs):
        return object()

    async def graph_read(*_args, **_kwargs):
        return graph

    monkeypatch.setattr(delivery_authorization, "_current_target_revision", current_target)
    monkeypatch.setattr(delivery_authorization, "read_research_graph_v2", graph_read)
    with pytest.raises(DeliveryAuthorizationConflictError):
        await delivery_authorization.authorize_delivery(
            None,
            scope=DeliveryAuthorizationScope("w", "p", "wf", "v", "run"),
            actor=ResearchGraphV2ActorEvidence(
                actor_type="user",
                actor_id="approver",
                principal="approver",
                capability="actions.approve",
                policy_version="workspace-rbac-v1",
                authorized_at="2026-08-30T00:00:00Z",
            ),
            request=_request(),
        )
