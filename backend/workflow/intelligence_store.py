"""Transactional command service for the IntelligenceSession aggregate."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.database import (
    commit_session,
    queue_after_commit,
    rollback_session_preserving_primary,
)
from backend.models.intelligence import (
    IntelligenceArtifact,
    IntelligenceArtifactReference,
    IntelligenceCommandRecord,
    IntelligenceOutbox,
    IntelligenceSession,
    IntelligenceTransition,
)
from backend.schemas.workflow_runtime import WorkflowNodeRunEvent
from backend.workflow.event_mirror import publish_workflow_run_event_mirror
from backend.workflow.intelligence_outbox import IntelligenceOutboxDispatcher
from backend.workflow.native_intelligence_contracts import (
    ARTIFACT_CONTRACTS,
    MAX_EVENT_PAYLOAD_BYTES,
    ArtifactKind,
    IntelligenceCommand,
    IntelligenceCommandName,
    NativeIntelligenceArtifact,
    OperationLease,
    canonical_hash,
    deterministic_id,
    payload_size,
)
from backend.workflow.native_intelligence_state import (
    IN_FLIGHT_STATES,
    IntelligenceState,
    IntelligenceTransitionError,
    decide_transition,
    workflow_projection,
)
from backend.workflow.workflow_run_events import append_workflow_run_events

DEFAULT_LEASE_DURATION = timedelta(minutes=5)
MAX_TRANSACTION_ATTEMPTS = 3

FaultHook = Callable[[str], None]


class IntelligenceStoreError(RuntimeError):
    code = "intelligence_store_error"


class IntelligenceSessionNotFoundError(IntelligenceStoreError):
    code = "intelligence_session_not_found"


class IntelligenceConflictError(IntelligenceStoreError):
    code = "intelligence_version_conflict"

    def __init__(
        self,
        *args: object,
        metric_reason: str = "version",
    ) -> None:
        from backend.workflow.native_intelligence_metrics import (
            record_transition_conflict,
        )

        record_transition_conflict(metric_reason)
        super().__init__(*args)


class IntelligenceIdempotencyConflictError(IntelligenceStoreError):
    code = "intelligence_idempotency_conflict"

    def __init__(self, *args: object) -> None:
        from backend.workflow.native_intelligence_metrics import (
            record_transition_conflict,
        )

        record_transition_conflict("idempotency")
        super().__init__(*args)


class IntelligenceReferenceError(IntelligenceStoreError):
    code = "intelligence_artifact_not_found"


class IntelligenceArtifactInvariantError(IntelligenceStoreError):
    code = "intelligence_artifact_invariant_violation"

    def __init__(self, *args: object) -> None:
        from backend.workflow.native_intelligence_metrics import record_rejected_contract

        record_rejected_contract("artifact_invariant")
        super().__init__(*args)


class IntelligenceLeaseConflictError(IntelligenceStoreError):
    code = "operation_in_progress"

    def __init__(self, *args: object) -> None:
        from backend.workflow.native_intelligence_metrics import (
            record_transition_conflict,
        )

        record_transition_conflict("lease")
        super().__init__(*args)


@dataclass(frozen=True)
class IntelligenceCommandResult:
    session_id: str
    state: IntelligenceState
    version: int
    transition_event_id: str | None
    artifact_ids: tuple[str, ...]
    idempotent_replay: bool = False
    no_op: bool = False


class IntelligenceStore:
    """All aggregate mutations pass through this service and caller transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        outbox_dispatcher: IntelligenceOutboxDispatcher | None = None,
        fault_hook: FaultHook | None = None,
        run_context: dict[str, str] | None = None,
        commit_each_command: bool = False,
    ) -> None:
        self.session = session
        self.outbox_dispatcher = outbox_dispatcher
        self.fault_hook = fault_hook
        self.run_context = dict(run_context or {})
        self.commit_each_command = commit_each_command

    async def create_session(
        self,
        *,
        session_id: str,
        idempotency_key: str,
        request: dict[str, Any] | None = None,
        created_by_run_id: str | None = None,
    ) -> IntelligenceCommandResult:
        request = request or {}
        request_hash = canonical_hash(
            {"command": "create", "session_id": session_id, "request": request}
        )
        existing = await self.session.get(IntelligenceSession, session_id)
        if existing is not None:
            record = await self._command_record(session_id, idempotency_key)
            if record is None or record.request_hash != request_hash:
                raise IntelligenceIdempotencyConflictError(
                    "session already exists with a different create request"
                )
            return self._record_result(existing, record, idempotent_replay=True)

        aggregate = IntelligenceSession(
            id=session_id,
            created_by_run_id=created_by_run_id,
            state=IntelligenceState.CREATED,
            version=0,
            transition_sequence=0,
            workflow_projection=workflow_projection(IntelligenceState.CREATED),
        )
        self.session.add(aggregate)
        await self.session.flush()
        self.session.add(
            IntelligenceCommandRecord(
                session_id=session_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                command="create",
                resulting_version=0,
                result_artifact_ids=[],
                result_payload={"state": IntelligenceState.CREATED.value},
            )
        )
        await self.session.flush()
        return IntelligenceCommandResult(
            session_id=session_id,
            state=IntelligenceState.CREATED,
            version=0,
            transition_event_id=None,
            artifact_ids=(),
        )

    async def load_session(self, session_id: str) -> IntelligenceSession:
        aggregate = await self.session.get(IntelligenceSession, session_id)
        if aggregate is None:
            raise IntelligenceSessionNotFoundError(session_id)
        return aggregate

    async def load_artifact(
        self, session_id: str, artifact_id: str
    ) -> NativeIntelligenceArtifact:
        """Load a typed artifact without revealing cross-session existence."""

        return (await self.load_artifacts_by_ids(session_id, [artifact_id]))[
            artifact_id
        ]

    async def load_artifacts_by_ids(
        self,
        session_id: str,
        artifact_ids: list[str],
    ) -> dict[str, NativeIntelligenceArtifact]:
        """Bulk-load a bounded same-session artifact set in constant queries."""

        unique_ids = set(artifact_ids)
        if not unique_ids:
            return {}
        artifacts = list(
            (
                await self.session.scalars(
                    select(IntelligenceArtifact).where(
                        IntelligenceArtifact.session_id == session_id,
                        IntelligenceArtifact.artifact_id.in_(unique_ids),
                    )
                )
            ).all()
        )
        if {artifact.artifact_id for artifact in artifacts} != unique_ids:
            raise IntelligenceReferenceError("artifact is not in this session")
        grounding_rows = (
            await self.session.execute(
                select(
                    IntelligenceArtifactReference.source_artifact_id,
                    IntelligenceArtifactReference.target_artifact_id,
                ).where(
                    IntelligenceArtifactReference.session_id == session_id,
                    IntelligenceArtifactReference.source_artifact_id.in_(
                        unique_ids
                    ),
                )
            )
        ).all()
        grounding_by_source: dict[str, list[str]] = {
            artifact_id: [] for artifact_id in unique_ids
        }
        for source_artifact_id, target_artifact_id in grounding_rows:
            grounding_by_source[source_artifact_id].append(target_artifact_id)
        return {
            artifact.artifact_id: self._hydrate_artifact(
                artifact,
                grounding_by_source[artifact.artifact_id],
            )
            for artifact in artifacts
        }

    def _hydrate_artifact(
        self,
        artifact: IntelligenceArtifact,
        grounding_ids: list[str],
    ) -> NativeIntelligenceArtifact:
        grounding_ids = sorted(grounding_ids)
        provenance_grounding = artifact.provenance.get("evidence_artifact_ids", [])
        if (
            isinstance(provenance_grounding, list)
            and set(provenance_grounding) == set(grounding_ids)
        ):
            grounding_ids = provenance_grounding
        try:
            contract = ARTIFACT_CONTRACTS[ArtifactKind(artifact.kind)]
            loaded = contract(
                artifact_id=artifact.artifact_id,
                session_id=artifact.session_id,
                payload=artifact.payload,
                grounding_artifact_ids=grounding_ids,
                simulated=artifact.simulated,
                provenance=artifact.provenance,
                algorithm_version=artifact.algorithm_version,
                seed=artifact.seed,
            )
        except (KeyError, ValueError, ValidationError) as exc:
            raise IntelligenceArtifactInvariantError(
                "persisted artifact violates provenance invariants"
            ) from exc
        if canonical_hash(loaded.model_dump(mode="json")) != artifact.content_hash:
            raise IntelligenceStoreError("artifact_content_hash_mismatch")
        return loaded

    async def load_latest_artifact(
        self, session_id: str, kind: ArtifactKind
    ) -> NativeIntelligenceArtifact | None:
        """Load the latest same-session artifact of a kind without leaking IDs."""

        artifact_id = await self.session.scalar(
            select(IntelligenceArtifact.artifact_id)
            .where(
                IntelligenceArtifact.session_id == session_id,
                IntelligenceArtifact.kind == kind.value,
            )
            .order_by(
                IntelligenceArtifact.created_at.desc(),
                IntelligenceArtifact.artifact_id.desc(),
            )
            .limit(1)
        )
        if artifact_id is None:
            return None
        return await self.load_artifact(session_id, artifact_id)

    async def load_command_result(
        self, session_id: str, idempotency_key: str
    ) -> IntelligenceCommandResult | None:
        """Return a previously committed command result for deterministic retry."""

        record = await self._command_record(session_id, idempotency_key)
        if record is None:
            return None
        aggregate = await self.load_session(session_id)
        return self._record_result(aggregate, record, idempotent_replay=True)

    async def load_operation_id(
        self,
        session_id: str,
        command: IntelligenceCommandName,
        resulting_version: int,
    ) -> str | None:
        """Resolve the persisted operation started at an exact aggregate version."""

        result_payload = await self.session.scalar(
            select(IntelligenceCommandRecord.result_payload)
            .where(
                IntelligenceCommandRecord.session_id == session_id,
                IntelligenceCommandRecord.command == command.value,
                IntelligenceCommandRecord.resulting_version == resulting_version,
            )
            .limit(1)
        )
        if not isinstance(result_payload, dict):
            return None
        operation_id = result_payload.get("operation_id")
        return operation_id if isinstance(operation_id, str) else None

    async def load_artifacts(
        self,
        session_id: str,
        kind: ArtifactKind,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[NativeIntelligenceArtifact]:
        """Load a bounded deterministic page of same-session artifacts."""

        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("artifact_query_bounds_invalid")
        artifact_ids = list(
            (
                await self.session.scalars(
                    select(IntelligenceArtifact.artifact_id)
                    .where(
                        IntelligenceArtifact.session_id == session_id,
                        IntelligenceArtifact.kind == kind.value,
                    )
                    .order_by(IntelligenceArtifact.artifact_id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return [
            await self.load_artifact(session_id, artifact_id)
            for artifact_id in artifact_ids
        ]

    async def apply(
        self,
        command: IntelligenceCommand,
        *,
        artifacts: list[NativeIntelligenceArtifact] | None = None,
        now: datetime | None = None,
    ) -> IntelligenceCommandResult:
        if command.run_id is None and self.run_context:
            command = command.model_copy(
                update={
                    "run_id": self.run_context.get("run_id"),
                    "workflow_id": self.run_context.get("workflow_id"),
                    "trace_id": self.run_context.get("trace_id"),
                    "node_id": self.run_context.get("node_id"),
                }
            )
        now = now or datetime.now(UTC)
        artifacts = artifacts or []
        existing_record = await self._command_record(
            command.session_id, command.idempotency_key
        )
        if existing_record is not None:
            if existing_record.request_hash != command.request_hash:
                raise IntelligenceIdempotencyConflictError(
                    "idempotency key was reused with a different canonical request"
                )
            aggregate = await self.load_session(command.session_id)
            return self._record_result(aggregate, existing_record, idempotent_replay=True)

        aggregate = await self.load_session(command.session_id)
        if aggregate.version != command.expected_version:
            raise IntelligenceConflictError(
                f"expected version {command.expected_version}, found {aggregate.version}"
            )

        try:
            decision = decide_transition(
                aggregate.state,
                command.command,
                retry_metadata=aggregate.retry_metadata,
            )
        except IntelligenceTransitionError as exc:
            raise IntelligenceConflictError(str(exc), metric_reason="state") from exc

        if decision.no_op:
            record = IntelligenceCommandRecord(
                session_id=command.session_id,
                idempotency_key=command.idempotency_key,
                request_hash=command.request_hash,
                command=command.command.value,
                resulting_version=aggregate.version,
                result_artifact_ids=[],
                result_payload={"state": aggregate.state.value, "no_op": True},
            )
            self.session.add(record)
            await self.session.flush()
            return self._record_result(aggregate, record, no_op=True)

        self._validate_lease(command, aggregate, decision.next_state, now)
        await self._validate_artifacts(command.session_id, artifacts)

        next_version = aggregate.version + 1
        next_sequence = aggregate.transition_sequence + 1
        event_id = deterministic_id(
            "intelligence_event",
            {
                "session_id": command.session_id,
                "version": next_version,
                "command": command.command,
                "request_hash": command.request_hash,
            },
        )
        values = self._session_values(
            aggregate=aggregate,
            command=command,
            next_state=decision.next_state,
            next_version=next_version,
            next_sequence=next_sequence,
            now=now,
        )
        cas = await self.session.execute(
            _session_cas_statement(
                command.session_id,
                command.expected_version,
                aggregate.state,
                values,
            )
        )
        if cas.rowcount != 1:
            raise IntelligenceConflictError("aggregate compare-and-swap lost")
        self._fault("after_cas")

        artifact_ids = await self._append_artifacts(artifacts)
        self._fault("after_artifact_append")
        result_artifact_ids = artifact_ids
        if command.command == IntelligenceCommandName.INTERVIEW_COMPLETE:
            requested_ids = command.request.get("resultArtifactIds", [])
            if not isinstance(requested_ids, list):
                raise IntelligenceReferenceError("invalid result artifact references")
            result_artifact_ids = [str(artifact_id) for artifact_id in requested_ids]
            if (
                len(result_artifact_ids) > 50
                or len(set(result_artifact_ids)) != len(result_artifact_ids)
            ):
                raise IntelligenceReferenceError("invalid result artifact references")
            if result_artifact_ids:
                existing_ids = set(
                    (
                        await self.session.scalars(
                            select(IntelligenceArtifact.artifact_id).where(
                                IntelligenceArtifact.session_id == command.session_id,
                                IntelligenceArtifact.artifact_id.in_(
                                    result_artifact_ids
                                ),
                            )
                        )
                    ).all()
                )
                if existing_ids != set(result_artifact_ids):
                    raise IntelligenceReferenceError(
                        "result artifact is not in this session"
                    )

        transition_metadata = self._transition_metadata(
            command, values, result_artifact_ids
        )
        transition = IntelligenceTransition(
            session_id=command.session_id,
            sequence=next_sequence,
            event_id=event_id,
            command=command.command.value,
            from_state=aggregate.state.value,
            to_state=decision.next_state.value,
            request_hash=command.request_hash,
            run_id=command.run_id,
            node_id=command.node_id,
            metadata_json=transition_metadata,
        )
        self.session.add(transition)
        self._fault("after_transition_append")

        if command.run_id is not None:
            append_result = await append_workflow_run_events(
                self.session,
                run_id=command.run_id,
                events=[
                    self._workflow_event(
                        command=command,
                        event_id=event_id,
                        next_state=decision.next_state,
                        artifact_ids=result_artifact_ids,
                        created_at=now,
                    )
                ],
            )
            if append_result.appended_events:
                queue_after_commit(
                    self.session,
                    lambda: publish_workflow_run_event_mirror(
                        append_result.appended_events
                    ),
                )
        self._fault("after_workflow_event_append")

        outbox_payload = {
            "schema_version": "intelligence.outbox.v1",
            "event_id": event_id,
            "session_id": command.session_id,
            "sequence": next_sequence,
            "command": command.command.value,
            "from_state": aggregate.state.value,
            "to_state": decision.next_state.value,
            "artifact_ids": result_artifact_ids,
        }
        if payload_size(outbox_payload) > MAX_EVENT_PAYLOAD_BYTES:
            raise IntelligenceStoreError("outbox_payload_too_large")
        self.session.add(
            IntelligenceOutbox(
                event_id=event_id,
                session_id=command.session_id,
                topic="intelligence.transition",
                payload=outbox_payload,
                state="pending",
                attempts=0,
                available_at=now,
            )
        )
        self._fault("after_outbox_append")

        record = IntelligenceCommandRecord(
            session_id=command.session_id,
            idempotency_key=command.idempotency_key,
            request_hash=command.request_hash,
            command=command.command.value,
            resulting_version=next_version,
            transition_event_id=event_id,
            result_artifact_ids=result_artifact_ids,
            result_payload={
                "state": decision.next_state.value,
                "operation_id": values.get("operation_id"),
            },
        )
        self.session.add(record)
        await self.session.flush()
        await self.session.refresh(aggregate)

        if self.outbox_dispatcher is not None:
            queue_after_commit(
                self.session,
                lambda: self.outbox_dispatcher.dispatch_event(event_id),
            )

        result = IntelligenceCommandResult(
            session_id=command.session_id,
            state=decision.next_state,
            version=next_version,
            transition_event_id=event_id,
            artifact_ids=tuple(result_artifact_ids),
        )
        if self.commit_each_command:
            await self.session.commit()
        return result

    async def renew_lease(
        self,
        command: IntelligenceCommand,
        *,
        owner: str,
        expires_at: datetime,
        checkpoint_manifest: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> IntelligenceCommandResult:
        aggregate = await self.load_session(command.session_id)
        if aggregate.lease_owner != owner:
            raise IntelligenceLeaseConflictError("only the current lease owner may renew")
        lease = OperationLease(
            operation_id=aggregate.operation_id or "",
            owner=owner,
            expires_at=expires_at,
            attempt=aggregate.operation_attempt or 1,
            checkpoint_manifest=checkpoint_manifest or aggregate.checkpoint_manifest or {},
        )
        return await self.apply(command.model_copy(update={"lease": lease}), now=now)

    async def recover_lease(
        self,
        command: IntelligenceCommand,
        *,
        new_owner: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> IntelligenceCommandResult:
        aggregate = await self.load_session(command.session_id)
        lease = OperationLease(
            operation_id=aggregate.operation_id or "",
            owner=new_owner,
            expires_at=expires_at,
            attempt=(aggregate.operation_attempt or 1) + 1,
            checkpoint_manifest=aggregate.checkpoint_manifest or {},
        )
        return await self.apply(command.model_copy(update={"lease": lease}), now=now)

    async def _command_record(
        self, session_id: str, idempotency_key: str
    ) -> IntelligenceCommandRecord | None:
        return await self.session.scalar(
            select(IntelligenceCommandRecord).where(
                IntelligenceCommandRecord.session_id == session_id,
                IntelligenceCommandRecord.idempotency_key == idempotency_key,
            )
        )

    async def _validate_artifacts(
        self,
        session_id: str,
        artifacts: list[NativeIntelligenceArtifact],
    ) -> None:
        new_ids = {artifact.artifact_id for artifact in artifacts}
        if len(new_ids) != len(artifacts):
            raise IntelligenceReferenceError("duplicate artifact ID in command")
        for artifact in artifacts:
            if artifact.session_id != session_id:
                raise IntelligenceReferenceError("cross-session artifact rejected")
        grounding_ids = {
            grounding_id
            for artifact in artifacts
            for grounding_id in artifact.grounding_artifact_ids
            if grounding_id not in new_ids
        }
        if grounding_ids:
            existing = set(
                (
                    await self.session.scalars(
                        select(IntelligenceArtifact.artifact_id).where(
                            IntelligenceArtifact.session_id == session_id,
                            IntelligenceArtifact.artifact_id.in_(grounding_ids),
                        )
                    )
                ).all()
            )
            if existing != grounding_ids:
                raise IntelligenceReferenceError("grounding artifact is not in this session")

    async def _append_artifacts(
        self, artifacts: list[NativeIntelligenceArtifact]
    ) -> list[str]:
        validated_artifacts: list[NativeIntelligenceArtifact] = []
        try:
            for artifact in artifacts:
                if artifact.provenance.conversation_id is not None:
                    raise ValueError("artifact producer cannot declare conversation provenance")
                conversation_id = self.run_context.get("conversation_id")
                candidate = artifact
                if conversation_id:
                    candidate = artifact.model_copy(
                        update={
                            "provenance": artifact.provenance.model_copy(
                                update={"conversation_id": conversation_id}
                            )
                        }
                    )
                contract = ARTIFACT_CONTRACTS[artifact.kind]
                validated_artifacts.append(
                    contract.model_validate(candidate.model_dump(mode="python"))
                )
        except (KeyError, ValueError, ValidationError) as exc:
            raise IntelligenceArtifactInvariantError(
                "artifact violates provenance invariants"
            ) from exc
        for artifact in validated_artifacts:
            self.session.add(
                IntelligenceArtifact(
                    session_id=artifact.session_id,
                    artifact_id=artifact.artifact_id,
                    schema_version=artifact.schema_version,
                    kind=artifact.kind.value,
                    payload=artifact.payload,
                    simulated=artifact.simulated,
                    provenance=artifact.provenance.model_dump(mode="json"),
                    algorithm_version=artifact.algorithm_version,
                    seed=artifact.seed,
                    content_hash=canonical_hash(artifact.model_dump(mode="json")),
                )
            )
        await self.session.flush()
        for artifact in validated_artifacts:
            for grounding_id in artifact.grounding_artifact_ids:
                self.session.add(
                    IntelligenceArtifactReference(
                        session_id=artifact.session_id,
                        source_artifact_id=artifact.artifact_id,
                        target_artifact_id=grounding_id,
                        relation="grounded_by",
                    )
                )
        return [artifact.artifact_id for artifact in validated_artifacts]

    def _validate_lease(
        self,
        command: IntelligenceCommand,
        aggregate: IntelligenceSession,
        next_state: IntelligenceState,
        now: datetime,
    ) -> None:
        completion_commands = {
            IntelligenceCommandName.RESEARCH_COMPLETE,
            IntelligenceCommandName.INTERVIEW_COMPLETE,
            IntelligenceCommandName.REPORT_COMPLETE,
        }
        if command.command in completion_commands:
            expiry = _aware(aggregate.lease_expires_at)
            if expiry is None or expiry <= now:
                raise IntelligenceLeaseConflictError(
                    "operation lease expired; recover the operation before completion"
                )
            if (
                command.lease is None
                or command.lease.operation_id != aggregate.operation_id
                or command.lease.owner != aggregate.lease_owner
            ):
                raise IntelligenceLeaseConflictError(
                    "completion must be written by the current operation owner"
                )
        elif command.command == IntelligenceCommandName.RENEW:
            if command.lease is None or command.lease.owner != aggregate.lease_owner:
                raise IntelligenceLeaseConflictError("only current owner may renew")
            if command.lease.operation_id != aggregate.operation_id:
                raise IntelligenceLeaseConflictError("operation identity cannot change")
            expiry = _aware(aggregate.lease_expires_at)
            if expiry is None or expiry <= now:
                raise IntelligenceLeaseConflictError(
                    "operation lease expired; recover before renewing"
                )
            if command.lease.expires_at <= now:
                raise IntelligenceLeaseConflictError(
                    "renewed operation lease must expire in the future"
                )
        elif command.command == IntelligenceCommandName.RECOVER:
            expiry = _aware(aggregate.lease_expires_at)
            if expiry is None or expiry > now:
                raise IntelligenceLeaseConflictError("operation lease has not expired")
            if command.lease is None or command.lease.operation_id != aggregate.operation_id:
                raise IntelligenceLeaseConflictError("recovery must preserve operation identity")
        elif command.command == IntelligenceCommandName.REPORT_PROGRESS:
            if (
                command.lease is None
                or command.lease.operation_id != aggregate.operation_id
                or command.lease.owner != aggregate.lease_owner
            ):
                raise IntelligenceLeaseConflictError(
                    "report progress must be written by the current operation owner"
                )
            expiry = _aware(aggregate.lease_expires_at)
            if expiry is None or expiry <= now:
                raise IntelligenceLeaseConflictError(
                    "operation lease expired; recover before report progress"
                )
            previous_sequence = int(
                (aggregate.checkpoint_manifest or {}).get("progress_sequence", 0)
            )
            next_sequence = int(
                command.lease.checkpoint_manifest.get("progress_sequence", 0)
            )
            if next_sequence <= previous_sequence:
                raise IntelligenceConflictError(
                    "report progress sequence must increase monotonically"
                )
        elif next_state in IN_FLIGHT_STATES and aggregate.state not in IN_FLIGHT_STATES:
            return
        elif aggregate.state in IN_FLIGHT_STATES and command.command not in {
            IntelligenceCommandName.CANCEL,
            IntelligenceCommandName.FAIL,
            IntelligenceCommandName.RESEARCH_COMPLETE,
            IntelligenceCommandName.INTERVIEW_COMPLETE,
            IntelligenceCommandName.REPORT_COMPLETE,
            IntelligenceCommandName.REPORT_PROGRESS,
        }:
            expiry = _aware(aggregate.lease_expires_at)
            if (
                expiry is not None
                and expiry > now
                and command.lease is not None
                and command.lease.owner != aggregate.lease_owner
            ):
                raise IntelligenceLeaseConflictError("operation is owned by another worker")

    def _session_values(
        self,
        *,
        aggregate: IntelligenceSession,
        command: IntelligenceCommand,
        next_state: IntelligenceState,
        next_version: int,
        next_sequence: int,
        now: datetime,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "state": next_state,
            "version": next_version,
            "transition_sequence": next_sequence,
            "workflow_projection": workflow_projection(next_state),
        }
        if command.command == IntelligenceCommandName.START:
            operation_id = deterministic_id(
                "operation",
                {
                    "session_id": command.session_id,
                    "command": command.command,
                    "idempotency_key": command.idempotency_key,
                    "request_hash": command.request_hash,
                },
            )
            values.update(
                {
                    "operation_id": operation_id,
                    "operation_command": command.command.value,
                    "operation_idempotency_key": command.idempotency_key,
                    "operation_request_hash": command.request_hash,
                    "operation_attempt": 1,
                    "checkpoint_manifest": command.request.get(
                        "checkpoint_manifest", {}
                    ),
                }
            )
        elif (
            command.command == IntelligenceCommandName.RESUME
            and aggregate.state == IntelligenceState.STOPPED
        ):
            values.update(
                {
                    "operation_id": aggregate.operation_id,
                    "operation_command": aggregate.operation_command,
                    "operation_idempotency_key": aggregate.operation_idempotency_key,
                    "operation_request_hash": aggregate.operation_request_hash,
                    "operation_attempt": (aggregate.operation_attempt or 0) + 1,
                    "checkpoint_manifest": aggregate.checkpoint_manifest or {},
                }
            )
        elif (
            command.command == IntelligenceCommandName.STEP
            and aggregate.state == IntelligenceState.RUNNING
        ):
            values.update(
                {
                    "operation_id": aggregate.operation_id,
                    "operation_command": aggregate.operation_command,
                    "operation_idempotency_key": aggregate.operation_idempotency_key,
                    "operation_request_hash": aggregate.operation_request_hash,
                    "operation_attempt": aggregate.operation_attempt,
                    "checkpoint_manifest": command.request.get(
                        "checkpoint_manifest",
                        aggregate.checkpoint_manifest or {},
                    ),
                }
            )
        if command.command == IntelligenceCommandName.FAIL:
            values["retry_metadata"] = {
                "failed_from_state": aggregate.state.value,
                "failed_command": aggregate.operation_command
                or command.request.get("failed_command"),
                "retryable": bool(command.request.get("retryable", True)),
                "idempotency_key": aggregate.operation_idempotency_key
                or command.idempotency_key,
                "request_hash": aggregate.operation_request_hash or command.request_hash,
                "operation_id": aggregate.operation_id,
                "checkpoint_manifest": aggregate.checkpoint_manifest or {},
                "lease_owner": aggregate.lease_owner,
                "lease_expires_at": (
                    aggregate.lease_expires_at.isoformat()
                    if aggregate.lease_expires_at
                    else None
                ),
                "operation_attempt": aggregate.operation_attempt,
            }
        elif (
            command.command == IntelligenceCommandName.RESUME
            and aggregate.state == IntelligenceState.FAILED
        ):
            retry = aggregate.retry_metadata or {}
            values.update(
                {
                    "retry_metadata": None,
                    "operation_id": retry.get("operation_id"),
                    "operation_command": retry.get("failed_command"),
                    "operation_idempotency_key": retry.get("idempotency_key"),
                    "operation_request_hash": retry.get("request_hash"),
                    "checkpoint_manifest": retry.get("checkpoint_manifest", {}),
                    "lease_owner": retry.get("lease_owner"),
                    "lease_expires_at": (
                        datetime.fromisoformat(retry["lease_expires_at"])
                        if retry.get("lease_expires_at")
                        else None
                    ),
                    "operation_attempt": retry.get("operation_attempt"),
                }
            )

        entering_in_flight = (
            next_state in IN_FLIGHT_STATES and aggregate.state not in IN_FLIGHT_STATES
            and command.command != IntelligenceCommandName.RESUME
        )
        if entering_in_flight:
            lease = command.lease or OperationLease(
                operation_id=deterministic_id(
                    "operation",
                    {
                        "session_id": command.session_id,
                        "command": command.command,
                        "idempotency_key": command.idempotency_key,
                        "request_hash": command.request_hash,
                    },
                ),
                owner="local",
                expires_at=now + DEFAULT_LEASE_DURATION,
                checkpoint_manifest={},
            )
            values.update(
                {
                    "operation_id": lease.operation_id,
                    "operation_command": command.command.value,
                    "operation_idempotency_key": command.idempotency_key,
                    "operation_request_hash": command.request_hash,
                    "lease_owner": lease.owner,
                    "lease_expires_at": lease.expires_at,
                    "operation_attempt": lease.attempt,
                    "checkpoint_manifest": lease.checkpoint_manifest,
                }
            )
        elif command.command in {
            IntelligenceCommandName.RENEW,
            IntelligenceCommandName.RECOVER,
            IntelligenceCommandName.REPORT_PROGRESS,
        }:
            assert command.lease is not None
            values.update(
                {
                    "lease_owner": command.lease.owner,
                    "lease_expires_at": command.lease.expires_at,
                    "operation_attempt": command.lease.attempt,
                    "checkpoint_manifest": command.lease.checkpoint_manifest,
                }
            )
        elif next_state not in IN_FLIGHT_STATES and command.command not in {
            IntelligenceCommandName.FAIL,
            IntelligenceCommandName.RESUME,
            IntelligenceCommandName.START,
            IntelligenceCommandName.STEP,
            IntelligenceCommandName.STOP,
        }:
            values.update(
                {
                    "operation_id": None,
                    "operation_command": None,
                    "operation_idempotency_key": None,
                    "operation_request_hash": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "operation_attempt": None,
                    "checkpoint_manifest": None,
                }
            )
        return values

    def _transition_metadata(
        self,
        command: IntelligenceCommand,
        values: dict[str, Any],
        artifact_ids: list[str],
    ) -> dict[str, Any]:
        metadata = {
            "schema_version": "intelligence.transition.v1",
            "projection": values["workflow_projection"],
            "artifact_ids": artifact_ids,
            "operation_id": values.get("operation_id"),
            "retry": values.get("retry_metadata"),
        }
        if payload_size(metadata) > MAX_EVENT_PAYLOAD_BYTES:
            raise IntelligenceStoreError("transition_metadata_too_large")
        return metadata

    def _workflow_event(
        self,
        *,
        command: IntelligenceCommand,
        event_id: str,
        next_state: IntelligenceState,
        artifact_ids: list[str],
        created_at: datetime,
    ) -> WorkflowNodeRunEvent:
        projection = workflow_projection(next_state)
        status = projection["status"]
        event_type = {
            "queued": "queued",
            "running": "started",
            "partial": "partial",
            "blocked": "blocked",
            "completed": "completed",
            "failed": "failed",
        }[status]
        return WorkflowNodeRunEvent(
            id=f"{event_id}:workflow",
            sequence=1,
            workflowId=command.workflow_id,
            workflowRunId=command.run_id,
            traceId=command.trace_id,
            nodeId=command.node_id,
            eventType=event_type,
            createdAt=created_at.isoformat(),
            message=f"Intelligence session {command.command.value}",
            details={
                "schemaVersion": "intelligence.workflow-projection.v1",
                "sessionId": command.session_id,
                "domainState": next_state.value,
                "command": command.command.value,
                "artifactIds": artifact_ids,
            },
        )

    def _record_result(
        self,
        aggregate: IntelligenceSession,
        record: IntelligenceCommandRecord,
        *,
        idempotent_replay: bool = False,
        no_op: bool = False,
    ) -> IntelligenceCommandResult:
        return IntelligenceCommandResult(
            session_id=aggregate.id,
            state=IntelligenceState(record.result_payload.get("state", aggregate.state.value)),
            version=record.resulting_version,
            transition_event_id=record.transition_event_id,
            artifact_ids=tuple(record.result_artifact_ids),
            idempotent_replay=idempotent_replay,
            no_op=no_op or bool(record.result_payload.get("no_op")),
        )

    def _fault(self, stage: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(stage)


async def run_intelligence_transaction[T](
    session_factory: async_sessionmaker[AsyncSession],
    operation: Callable[[IntelligenceStore], Awaitable[T]],
    *,
    attempts: int = MAX_TRANSACTION_ATTEMPTS,
) -> T:
    """Retry the complete transaction for transient lock/serialization errors."""

    for attempt in range(attempts):
        async with session_factory() as session:
            try:
                result = await operation(IntelligenceStore(session))
                await commit_session(session)
                return result
            except OperationalError as exc:
                await rollback_session_preserving_primary(session, exc)
                if not _is_transient_transaction_error(exc):
                    raise
                if attempt + 1 >= attempts:
                    raise IntelligenceStoreError(
                        "intelligence_transaction_retry_exhausted"
                    ) from exc
                await asyncio.sleep(0.01 * (attempt + 1))
            except Exception as exc:
                await rollback_session_preserving_primary(session, exc)
                raise
    raise AssertionError("unreachable")


def _is_transient_transaction_error(exc: OperationalError) -> bool:
    original = exc.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original,
        "pgcode",
        None,
    )
    if sqlstate in {"40001", "40P01"}:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "serialization",
            "deadlock",
        )
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _session_cas_statement(
    session_id: str,
    expected_version: int,
    expected_state: IntelligenceState,
    values: dict[str, Any],
):
    return (
        update(IntelligenceSession)
        .where(
            IntelligenceSession.id == session_id,
            IntelligenceSession.version == expected_version,
            IntelligenceSession.state == expected_state,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )


IntelligenceSessionService = IntelligenceStore

__all__ = [
    "IntelligenceCommandResult",
    "IntelligenceConflictError",
    "IntelligenceIdempotencyConflictError",
    "IntelligenceLeaseConflictError",
    "IntelligenceReferenceError",
    "IntelligenceSessionNotFoundError",
    "IntelligenceSessionService",
    "IntelligenceStore",
    "IntelligenceStoreError",
    "run_intelligence_transaction",
]
