import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models.workflow_run import WorkflowRun
from backend.workflow.workflow_run_events import lock_scoped_workflow_run


@pytest.mark.asyncio
async def test_sqlite_run_barrier_commits_authorization_before_concurrent_pin_retraction(
    tmp_path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workflow-lock.db'}",
        connect_args={"timeout": 5},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(WorkflowRun.__table__.create)
    async with session_factory() as setup:
        setup.add(
            WorkflowRun(
                id="run-1",
                workflow_id="workflow-1",
                studio_workflow_version_id="version-1",
                trace_id="trace-1",
                status="running",
                request={},
                projection={"pin": "fresh"},
            )
        )
        await setup.commit()

    authorization_committed = asyncio.Event()
    retraction_committed = asyncio.Event()
    async with session_factory() as authorization, session_factory() as retraction:
        run = await lock_scoped_workflow_run(
            authorization,
            workflow_id="workflow-1",
            studio_workflow_version_id="version-1",
            run_id="run-1",
        )
        assert run is not None
        assert run.projection == {"pin": "fresh"}

        async def retract_pin() -> None:
            locked = await lock_scoped_workflow_run(
                retraction,
                workflow_id="workflow-1",
                studio_workflow_version_id="version-1",
                run_id="run-1",
            )
            assert locked is not None
            assert authorization_committed.is_set()
            locked.projection = {"pin": "retracted"}
            await retraction.commit()
            retraction_committed.set()

        retraction_task = asyncio.create_task(retract_pin())
        await asyncio.sleep(0.05)
        assert not retraction_committed.is_set()
        authorization_committed.set()
        await authorization.commit()
        await retraction_task

    async with session_factory() as verify:
        persisted = await verify.get(WorkflowRun, "run-1")
        assert persisted is not None
        assert persisted.projection == {"pin": "retracted"}
    await engine.dispose()
