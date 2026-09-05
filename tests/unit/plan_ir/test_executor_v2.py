"""Unit tests for the executor v2 shared-segment shape (issue 04, ADR-0009
Two-Tier Attribution): a Plan with 2+ source nodes wired through a merge into
sequential server-side transforms (dedupe) and a store sink.

Executor-seam tests only (direct-invoke ``run_plan_once``, no HTTP) — the
HTTP-seam counterpart lives in tests/integration/test_plan_run_v2_api.py.
Covers: ordering (topological execution), provenance tagging, partial
failure (one source fails, other proceeds), and the Two-Tier Attribution
contract (the HARD TEST).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.models.plan import Plan
from backend.models.plan_health import PlanHealthRecord
from backend.models.record import CollectedRecord
from backend.models.source import DataSource
from backend.models.source_measurement import SourceMeasurement
from backend.models.task import CollectionTask
from backend.plan_ir.executor import run_plan_once
from backend.schemas.plan_ir import PLAN_IR_VERSION

# ── graph builders ──────────────────────────────────────────────────────────


def _source_node(node_id, source_id):
    return {
        "id": node_id,
        "kind": "source",
        "type": "rss_source",
        "label": "Source",
        "params": {},
        "source_id": source_id,
        "outputs": [{"name": "records", "type": "records"}],
    }


def _merge_node(node_id="merge1"):
    return {
        "id": node_id,
        "kind": "merge",
        "type": "merge",
        "label": "Merge",
        "params": {},
        "inputs": [
            {"name": "in1", "type": "records"},
            {"name": "in2", "type": "records"},
        ],
        "outputs": [{"name": "records", "type": "records"}],
    }


def _dedupe_node(node_id="dedupe1"):
    return {
        "id": node_id,
        "kind": "transform",
        "type": "dedupe",
        "label": "Dedupe",
        "params": {},
        "inputs": [{"name": "records", "type": "records"}],
        "outputs": [{"name": "records", "type": "records"}],
    }


def _sink_node(node_id="sink1"):
    return {
        "id": node_id,
        "kind": "sink",
        "type": "collection_store",
        "label": "Sink",
        "params": {},
        "inputs": [{"name": "records", "type": "records"}],
    }


def _edge(edge_id, src, src_port, tgt, tgt_port):
    return {
        "id": edge_id,
        "source_node": src,
        "source_port": src_port,
        "target_node": tgt,
        "target_port": tgt_port,
    }


def _two_source_graph(src1, src2):
    return {
        "ir_version": PLAN_IR_VERSION,
        "name": "Two-source shared segment",
        "draft": False,
        "nodes": [
            _source_node("n1", src1),
            _source_node("n2", src2),
            _merge_node(),
            _dedupe_node(),
            _sink_node(),
        ],
        "edges": [
            _edge("e1", "n1", "records", "merge1", "in1"),
            _edge("e2", "n2", "records", "merge1", "in2"),
            _edge("e3", "merge1", "records", "dedupe1", "records"),
            _edge("e4", "dedupe1", "records", "sink1", "records"),
        ],
    }


async def _make_source(session, **overrides) -> DataSource:
    source = DataSource(
        name=overrides.get("name", "Test Source"),
        channel_type=overrides.get("channel_type", "rss"),
        channel_config=overrides.get("channel_config", {"feed_url": "https://x/feed"}),
        enabled=overrides.get("enabled", True),
    )
    session.add(source)
    await session.flush()
    return source


def _make_plan(*, graph: dict, name="P1") -> Plan:
    return Plan(name=name, graph=graph, version=1, draft=False, runnable=True)


def _fake_pipeline_outcome(**overrides):
    base = {
        "task_id": "ignored",
        "run_id": "run-1",
        "success": True,
        "collected": 1,
        "stored": 1,
        "skipped": 0,
        "error": None,
    }
    base.update(overrides)
    return base


async def _stub_store_for_task(db_session, task_id: str, source_id: str, items: list[dict]):
    """Stand in for run_collection_pipeline's real store step: writes
    CollectedRecord rows directly under task_id/source_id exactly like
    backend.pipeline.storer.store_records would, so the executor's
    _load_provenanced_items readback has real rows to find without needing
    the whole channel/collector machinery."""
    for item in items:
        rec = CollectedRecord(
            task_id=task_id,
            source_id=source_id,
            raw_data=item,
            normalized_data={"title": item.get("title", ""), "source_id": source_id},
            content_hash=item["content_hash"],
            status="normalized",
        )
        db_session.add(rec)
    await db_session.flush()


# ── ordering / happy path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_source_plan_runs_end_to_end_via_manual_run(db_session):
    """Two sources -> merge -> dedupe -> store runs to completion: both
    source segments dispatch (normal per-source TaskRuns), and the shared
    segment produces a SharedSegmentResult with stored items."""
    src1 = await _make_source(db_session, name="S1")
    src2 = await _make_source(db_session, name="S2")
    plan = _make_plan(graph=_two_source_graph(src1.id, src2.id))
    db_session.add(plan)
    await db_session.flush()

    captured_task_ids = {}

    async def fake_pipeline(task_id, params):
        # Discover which source this task belongs to, and stub-store one
        # record for it (real content_hash so dedupe has something to work
        # with downstream).
        task = await db_session.get(CollectionTask, task_id)
        source_id = task.source_id
        captured_task_ids[source_id] = task_id
        content_hash = f"hash-{source_id}"
        await _stub_store_for_task(
            db_session, task_id, source_id,
            [{"title": f"item from {source_id}", "content_hash": content_hash}],
        )
        return _fake_pipeline_outcome(run_id=f"run-{source_id}")

    with patch(
        "backend.pipeline.runner.run_collection_pipeline",
        new=AsyncMock(side_effect=fake_pipeline),
    ):
        result = await run_plan_once(db_session, plan)

    assert result.success is True
    assert len(result.source_results) == 2
    assert all(r.success for r in result.source_results)

    assert result.shared_segment is not None
    assert result.shared_segment.success is True
    assert result.shared_segment.items_in == 2
    assert result.shared_segment.stored == 2  # distinct content hashes, nothing deduped
    assert result.shared_segment.skipped == 0

    # Plan Health recorded one success row per shared node (merge, dedupe, sink).
    health_query = select(PlanHealthRecord).where(PlanHealthRecord.plan_id == plan.id)
    health_rows = (await db_session.execute(health_query)).scalars().all()
    assert {r.node_id for r in health_rows} == {"merge1", "dedupe1", "sink1"}
    assert all(r.success for r in health_rows)


@pytest.mark.asyncio
async def test_shared_segment_executes_nodes_in_topological_order(db_session):
    """merge must run before dedupe, dedupe before store — asserted via the
    recorded Plan Health rows' recorded_at ordering (each node commits its
    health row immediately after running, before the next node starts)."""
    src1 = await _make_source(db_session, name="S1")
    src2 = await _make_source(db_session, name="S2")
    plan = _make_plan(graph=_two_source_graph(src1.id, src2.id))
    db_session.add(plan)
    await db_session.flush()

    async def fake_pipeline(task_id, params):
        task = await db_session.get(CollectionTask, task_id)
        source_id = task.source_id
        await _stub_store_for_task(
            db_session, task_id, source_id,
            [{"title": "x", "content_hash": f"hash-{source_id}"}],
        )
        return _fake_pipeline_outcome()

    with patch(
        "backend.pipeline.runner.run_collection_pipeline",
        new=AsyncMock(side_effect=fake_pipeline),
    ):
        result = await run_plan_once(db_session, plan)

    rows = (
        (
            await db_session.execute(
                select(PlanHealthRecord)
                .where(PlanHealthRecord.plan_id == plan.id)
                .order_by(PlanHealthRecord.recorded_at.asc(), PlanHealthRecord.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    # id order may tie on recorded_at resolution, but insertion order (primary
    # key sequencing via autoincrement-free UUID) is preserved by ORDER BY
    # recorded_at asc, created_at asc — assert the node_id sequence is exactly
    # merge -> dedupe -> sink.
    assert [r.node_id for r in rows] == ["merge1", "dedupe1", "sink1"]
    assert result.success is True


# ── provenance tagging ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stored_records_carry_source_tagged_provenance(db_session):
    src1 = await _make_source(db_session, name="S1")
    src2 = await _make_source(db_session, name="S2")
    plan = _make_plan(graph=_two_source_graph(src1.id, src2.id))
    db_session.add(plan)
    await db_session.flush()

    async def fake_pipeline(task_id, params):
        task = await db_session.get(CollectionTask, task_id)
        source_id = task.source_id
        await _stub_store_for_task(
            db_session, task_id, source_id,
            [{"title": "x", "content_hash": f"hash-{source_id}"}],
        )
        return _fake_pipeline_outcome()

    with patch(
        "backend.pipeline.runner.run_collection_pipeline",
        new=AsyncMock(side_effect=fake_pipeline),
    ):
        result = await run_plan_once(db_session, plan)

    assert result.shared_segment.stored == 2

    stored_query = select(CollectedRecord).where(CollectedRecord.source_id == plan.id)
    stored_rows = (await db_session.execute(stored_query)).scalars().all()
    assert len(stored_rows) == 2
    provenance_sources = set()
    for row in stored_rows:
        prov = row.normalized_data.get("_plan_provenance")
        assert prov is not None
        assert prov["plan_id"] == plan.id
        assert prov["source_id"] in (src1.id, src2.id)
        assert prov["source_node_id"] in ("n1", "n2")
        provenance_sources.add(prov["source_id"])
    assert provenance_sources == {src1.id, src2.id}


# ── partial failure ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_source_fails_other_proceeds(db_session):
    """A source segment's pipeline failure does not stop the other source,
    and does not raise out of run_plan_once — it's recorded in that
    source's own SourceSegmentResult (PRD "partial failure" acceptance
    criterion)."""
    src1 = await _make_source(db_session, name="S1")
    src2 = await _make_source(db_session, name="S2")
    plan = _make_plan(graph=_two_source_graph(src1.id, src2.id))
    db_session.add(plan)
    await db_session.flush()

    async def fake_pipeline(task_id, params):
        task = await db_session.get(CollectionTask, task_id)
        source_id = task.source_id
        if source_id == src1.id:
            return {"error": "boom"}
        await _stub_store_for_task(
            db_session, task_id, source_id,
            [{"title": "x", "content_hash": f"hash-{source_id}"}],
        )
        return _fake_pipeline_outcome()

    with patch(
        "backend.pipeline.runner.run_collection_pipeline",
        new=AsyncMock(side_effect=fake_pipeline),
    ):
        result = await run_plan_once(db_session, plan)

    by_source = {r.source_id: r for r in result.source_results}
    assert by_source[src1.id].success is False
    assert "boom" in by_source[src1.id].error
    assert by_source[src2.id].success is True

    # Overall Plan run reflects the partial failure but did not raise, and
    # the shared segment still ran on whatever DID land (src2's one item).
    assert result.success is False
    assert result.shared_segment is not None
    assert result.shared_segment.items_in == 1


# ── Two-Tier Attribution HARD TEST ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_shared_segment_failure_never_touches_source_state(db_session):
    """HARD TEST (issue 04 acceptance criterion): induce a shared-segment
    failure (dedupe raises) and assert Plan Health records the failure AND
    every upstream source's measurements/control-state are byte-identical
    to before the run.

    "Before" is snapshotted as plain dict rows (SourceMeasurement +
    DataSource control-state columns) BEFORE the run; "after" is the same
    query, same shape, compared for exact equality post-failure.
    """
    src1 = await _make_source(db_session, name="S1")
    src2 = await _make_source(db_session, name="S2")
    plan = _make_plan(graph=_two_source_graph(src1.id, src2.id))
    db_session.add(plan)

    # Seed a pre-existing SourceMeasurement per source (as if a prior direct
    # run already recorded one) so there is real state to prove untouched —
    # not just "still zero rows".
    for src in (src1, src2):
        db_session.add(
            SourceMeasurement(
                source_id=src.id,
                run_id="prior-run",
                measured_at=datetime(2026, 7, 1, tzinfo=UTC),
                accepted=5,
                duplicates=1,
                rejected=0,
                error_rate=0.0,
                duplicate_rate=0.2,
                error_kinds={},
                cursor_advanced=True,
                source_ts_quality="missing",
                raw={"seed": True},
            )
        )
    await db_session.flush()

    async def _snapshot():
        measurement_query = select(SourceMeasurement).order_by(
            SourceMeasurement.source_id, SourceMeasurement.id
        )
        measurement_rows = (await db_session.execute(measurement_query)).scalars().all()
        measurements_snapshot = [
            {
                "id": r.id,
                "source_id": r.source_id,
                "run_id": r.run_id,
                "accepted": r.accepted,
                "duplicates": r.duplicates,
                "rejected": r.rejected,
                "error_rate": r.error_rate,
                "duplicate_rate": r.duplicate_rate,
                "cursor_advanced": r.cursor_advanced,
                "raw": r.raw,
            }
            for r in measurement_rows
        ]

        sources = [await db_session.get(DataSource, s.id) for s in (src1, src2)]
        control_state_snapshot = [
            {
                "id": s.id,
                "enabled": s.enabled,
                "review_required": s.review_required,
                "paused_until": s.paused_until,
                "objective_override": s.objective_override,
            }
            for s in sources
        ]
        return measurements_snapshot, control_state_snapshot

    before_measurements, before_control_state = await _snapshot()

    async def fake_pipeline(task_id, params):
        task = await db_session.get(CollectionTask, task_id)
        source_id = task.source_id
        await _stub_store_for_task(
            db_session, task_id, source_id,
            [{"title": "x", "content_hash": f"hash-{source_id}"}],
        )
        return _fake_pipeline_outcome()

    with patch(
        "backend.pipeline.runner.run_collection_pipeline",
        new=AsyncMock(side_effect=fake_pipeline),
    ):
        with patch(
            "backend.plan_ir.executor.dedupe_items",
            side_effect=RuntimeError("dedupe blew up"),
        ):
            result = await run_plan_once(db_session, plan)

    # The Plan run reports the shared-segment failure, not a raised exception.
    assert result.success is False
    assert result.shared_segment is not None
    assert result.shared_segment.success is False
    assert result.shared_segment.failed_node_id == "dedupe1"
    assert "dedupe blew up" in result.shared_segment.error

    # Plan Health recorded the failure on the dedupe node.
    health_query = select(PlanHealthRecord).where(PlanHealthRecord.plan_id == plan.id)
    health_rows = (await db_session.execute(health_query)).scalars().all()
    by_node = {r.node_id: r for r in health_rows}
    assert by_node["merge1"].success is True
    assert by_node["dedupe1"].success is False
    assert "dedupe blew up" in by_node["dedupe1"].error_message
    # The sink never ran — execution stopped at the failing node.
    assert "sink1" not in by_node

    # No CollectedRecord was written under the plan's own shared-store
    # identity (plan.id) — the failure happened before store ran.
    shared_store_query = select(CollectedRecord).where(CollectedRecord.source_id == plan.id)
    shared_store_rows = (await db_session.execute(shared_store_query)).scalars().all()
    assert shared_store_rows == []

    # THE HARD ASSERTION: every upstream source's measurements/control-state
    # are byte-identical to before the run.
    after_measurements, after_control_state = await _snapshot()
    assert after_measurements == before_measurements
    assert after_control_state == before_control_state
