import asyncio
from contextlib import AsyncExitStack

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models.workflow_run import WorkflowRun, WorkflowRunEvent
from backend.schemas.research_graph import WorkflowResearchGraphMutationRequest
from backend.schemas.workflow_runtime import WorkflowNodeRunEvent
from backend.workflow.research_graph_mutations import (
    append_workflow_research_graph_mutation,
)
from backend.workflow.workflow_run_events import (
    WorkflowRunEventConflictError,
    _counter_reconciliation_statement,
    _postgres_allocator_lock_statement,
    _sequence_reservation_statement,
    append_workflow_run_events,
)
from tests.postgres_conformance import temporary_postgres_database


@pytest.fixture(
    params=(
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.postgres_conformance,
        ),
    )
)
async def event_spine_sessions(request, tmp_path):
    async with AsyncExitStack() as resources:
        if request.param == "sqlite":
            database_path = (tmp_path / "workflow-event-spine.db").as_posix()
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 30},
            )

            @event.listens_for(engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")
                dbapi_connection.execute("PRAGMA journal_mode=WAL")
        else:
            database_url = await resources.enter_async_context(
                temporary_postgres_database("workflow_event_allocator")
            )
            engine = create_async_engine(database_url)

        resources.push_async_callback(engine.dispose)
        sessions = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield sessions


def _run(run_id: str = "run-event-spine") -> WorkflowRun:
    return WorkflowRun(
        id=run_id,
        workflow_id="workflow-event-spine",
        trace_id=f"trace-{run_id}",
        status="running",
        valid=True,
        request={},
        projection={},
    )


def _event(
    event_id: str,
    *,
    run_id: str = "run-event-spine",
    sequence: int = 1,
    message: str | None = None,
) -> WorkflowNodeRunEvent:
    return WorkflowNodeRunEvent(
        id=event_id,
        sequence=sequence,
        workflowId="workflow-event-spine",
        workflowRunId=run_id,
        traceId=f"trace-{run_id}",
        nodeId="node-1",
        eventType="partial",
        createdAt="2026-07-23T00:00:00Z",
        message=message or event_id,
    )


def _simultaneous_start():
    both_ready = asyncio.Event()
    ready_count = 0

    async def wait():
        nonlocal ready_count
        ready_count += 1
        if ready_count == 2:
            both_ready.set()
        await both_ready.wait()

    return wait


@pytest.mark.asyncio
async def test_append_is_idempotent_and_only_adds_the_unseen_suffix(
    event_spine_sessions,
):
    async with event_spine_sessions() as session:
        session.add(_run())
        first = [_event("event-1"), _event("event-2")]

        initial = await append_workflow_run_events(
            session,
            run_id="run-event-spine",
            events=first,
        )
        original_row_ids = (
            await session.scalars(select(WorkflowRunEvent.id).order_by(WorkflowRunEvent.sequence))
        ).all()

        replay = await append_workflow_run_events(
            session,
            run_id="run-event-spine",
            events=first,
        )
        continued = await append_workflow_run_events(
            session,
            run_id="run-event-spine",
            events=[*first, _event("event-3")],
        )
        persisted = (
            await session.scalars(select(WorkflowRunEvent).order_by(WorkflowRunEvent.sequence))
        ).all()
        run = await session.get(WorkflowRun, "run-event-spine")

        assert [event.sequence for event in initial.appended_events] == [1, 2]
        assert replay.appended_events == []
        assert [event.sequence for event in continued.appended_events] == [3]
        assert [row.id for row in persisted[:2]] == original_row_ids
        assert [row.event_id for row in persisted] == ["event-1", "event-2", "event-3"]
        assert [row.sequence for row in persisted] == [1, 2, 3]
        assert run is not None
        await session.refresh(run)
        assert run.next_event_sequence == 4


@pytest.mark.asyncio
async def test_same_event_id_with_different_canonical_payload_is_stable_conflict(
    event_spine_sessions,
):
    async with event_spine_sessions() as session:
        session.add(_run())
        await append_workflow_run_events(
            session,
            run_id="run-event-spine",
            events=[_event("event-1", message="original")],
        )

        with pytest.raises(WorkflowRunEventConflictError) as exc_info:
            await append_workflow_run_events(
                session,
                run_id="run-event-spine",
                events=[_event("event-1", sequence=99, message="changed")],
            )

        assert exc_info.value.code == "workflow_run_event_conflict"
        assert "different canonical payload" in str(exc_info.value)


@pytest.mark.asyncio
async def test_event_id_is_globally_stable_across_runs(event_spine_sessions):
    async with event_spine_sessions() as session:
        session.add_all([_run("run-a"), _run("run-b")])
        await append_workflow_run_events(
            session,
            run_id="run-a",
            events=[_event("stable-event", run_id="run-a")],
        )

        with pytest.raises(WorkflowRunEventConflictError):
            await append_workflow_run_events(
                session,
                run_id="run-b",
                events=[_event("stable-event", run_id="run-b")],
            )


@pytest.mark.asyncio
async def test_two_transactions_reserve_disjoint_contiguous_ranges(
    event_spine_sessions,
):
    async with event_spine_sessions() as setup:
        setup.add(_run())
        await setup.commit()

    async def append(event_id: str) -> int:
        async with event_spine_sessions() as session:
            result = await append_workflow_run_events(
                session,
                run_id="run-event-spine",
                events=[_event(event_id)],
            )
            await session.commit()
            return result.appended_events[0].sequence

    reserved = await asyncio.gather(append("event-a"), append("event-b"))
    async with event_spine_sessions() as verification:
        rows = (
            await verification.scalars(select(WorkflowRunEvent).order_by(WorkflowRunEvent.sequence))
        ).all()
        run = await verification.get(WorkflowRun, "run-event-spine")
    assert sorted(reserved) == [1, 2]
    assert [row.sequence for row in rows] == [1, 2]
    assert run is not None
    assert run.next_event_sequence == 3


@pytest.mark.asyncio
async def test_concurrent_same_event_and_payload_returns_one_persisted_event(
    event_spine_sessions,
):
    async with event_spine_sessions() as setup:
        setup.add(_run())
        await setup.commit()
    wait_for_peer = _simultaneous_start()

    async def append():
        async with event_spine_sessions() as session:
            await wait_for_peer()
            result = await append_workflow_run_events(
                session,
                run_id="run-event-spine",
                events=[_event("event-replay", message="same")],
            )
            await session.commit()
            return result

    first, second = await asyncio.wait_for(
        asyncio.gather(append(), append()),
        timeout=10,
    )

    assert first.events == second.events
    assert first.events[0].id == "event-replay"
    assert first.events[0].sequence == 1
    assert sorted((len(first.appended_events), len(second.appended_events))) == [0, 1]
    async with event_spine_sessions() as verification:
        rows = (await verification.scalars(select(WorkflowRunEvent))).all()
        run = await verification.get(WorkflowRun, "run-event-spine")
    assert len(rows) == 1
    assert rows[0].event_id == "event-replay"
    assert run is not None
    assert run.next_event_sequence == 2


@pytest.mark.asyncio
async def test_concurrent_same_event_with_different_payload_is_stable_conflict(
    event_spine_sessions,
):
    async with event_spine_sessions() as setup:
        setup.add(_run())
        await setup.commit()
    wait_for_peer = _simultaneous_start()

    async def append(message):
        async with event_spine_sessions() as session:
            await wait_for_peer()
            result = await append_workflow_run_events(
                session,
                run_id="run-event-spine",
                events=[_event("event-conflict", message=message)],
            )
            await session.commit()
            return result

    results = await asyncio.wait_for(
        asyncio.gather(
            append("first"),
            append("second"),
            return_exceptions=True,
        ),
        timeout=10,
    )

    conflicts = [result for result in results if isinstance(result, WorkflowRunEventConflictError)]
    successes = [result for result in results if not isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].code == "workflow_run_event_conflict"
    assert "different canonical payload" in str(conflicts[0])
    async with event_spine_sessions() as verification:
        rows = (await verification.scalars(select(WorkflowRunEvent))).all()
        run = await verification.get(WorkflowRun, "run-event-spine")
    assert len(rows) == 1
    assert rows[0].event_id == "event-conflict"
    assert run is not None
    assert run.next_event_sequence == 2


def test_allocator_and_reconciliation_statements_compile_for_sqlite_and_postgresql():
    next_sequence = (
        select(func.coalesce(func.max(WorkflowRunEvent.sequence), 0) + 1)
        .where(WorkflowRunEvent.run_id == WorkflowRun.id)
        .correlate(WorkflowRun)
        .scalar_subquery()
    )
    statements = (
        _sequence_reservation_statement("run-1", 1, 2),
        _counter_reconciliation_statement(next_sequence),
    )

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        compiled = [str(statement.compile(dialect=dialect)) for statement in statements]
        assert all("UPDATE workflow_runs" in sql for sql in compiled)
        assert "next_event_sequence" in compiled[0]
        assert "max(workflow_run_events.sequence)" in compiled[1]


def test_postgresql_allocator_lock_uses_a_namespaced_64_bit_hash():
    compiled = str(
        _postgres_allocator_lock_statement("run-1").compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "pg_advisory_xact_lock(hashtextextended('run-1', 22363277192072265))" in compiled


@pytest.mark.asyncio
async def test_graph_mutation_replays_and_rejects_stale_transcript_guards(
    event_spine_sessions,
):
    def semantic_event(sequence: int, event_type: str, entity: dict) -> WorkflowNodeRunEvent:
        event_id = f"semantic-{sequence}"
        return WorkflowNodeRunEvent(
            id=event_id,
            sequence=sequence,
            workflowId="workflow-event-spine",
            workflowRunId="run-event-spine",
            traceId="trace-run-event-spine",
            nodeId="node-1",
            eventType="partial",
            createdAt="2026-08-29T00:00:00Z",
            details={
                "researchGraph": {
                    "schemaVersion": 1,
                    "eventId": event_id,
                    "idempotencyKey": event_id,
                    "eventType": event_type,
                    "runId": "run-event-spine",
                    "traceId": "trace-run-event-spine",
                    "nodeId": "node-1",
                    "revisionId": "rev-1",
                    "lineage": {"source": "test"},
                    "entity": entity,
                }
            },
        )

    request = WorkflowResearchGraphMutationRequest(
        schemaVersion=1,
        idempotencyKey="proposal-1",
        expectedRevision="rev-1",
        expectedSequence=2,
        action="propose",
        traceId="trace-run-event-spine",
        nodeId="node-1",
        revisionId="rev-1",
        entity={"id": "claim-1", "kind": "claim", "evidenceIds": ["evidence-1"]},
    )
    async with event_spine_sessions() as session:
        session.add(_run())
        await session.flush()
        await append_workflow_run_events(
            session,
            run_id="run-event-spine",
            events=[
                semantic_event(1, "source/recorded", {"id": "source-1", "kind": "source"}),
                semantic_event(
                    2,
                    "evidence/linked",
                    {"id": "evidence-1", "kind": "evidence", "sourceIds": ["source-1"]},
                ),
            ],
        )
        first = await append_workflow_research_graph_mutation(
            session,
            run_id="run-event-spine",
            workflow_id="workflow-event-spine",
            trace_id="trace-run-event-spine",
            request=request,
        )
        replay = await append_workflow_research_graph_mutation(
            session,
            run_id="run-event-spine",
            workflow_id="workflow-event-spine",
            trace_id="trace-run-event-spine",
            request=request,
        )
        assert first.events[0].id == replay.events[0].id == "proposal-1"
        with pytest.raises(WorkflowRunEventConflictError, match="expectedSequence is stale"):
            await append_workflow_research_graph_mutation(
                session,
                run_id="run-event-spine",
                workflow_id="workflow-event-spine",
                trace_id="trace-run-event-spine",
                request=request.model_copy(
                    update={"idempotencyKey": "proposal-stale"},
                    deep=True,
                ),
            )
