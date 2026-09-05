"""Executor-facing Browser Space lifecycle coverage."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models.browser import BrowserInstance
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.services import browser_space_service as service
from tests.browser_space_fixtures import configure_space_runtime


async def _seed_space(db_session, suffix: str):
    user = User(subject=f"integration-owner-{suffix}")
    workspace = Workspace(name=f"Integration {suffix}", slug=f"integration-{suffix}")
    instance = BrowserInstance(endpoint=f"http://integration-{suffix}:9222")
    db_session.add_all((user, workspace, instance))
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OPERATOR,
        )
    )
    await db_session.commit()
    await configure_space_runtime(db_session, instance)
    return await service.create_space(
        db_session,
        workspace_id=workspace.id,
        browser_instance_id=instance.id,
        owner_type="operator",
        owner_id=user.subject,
        granted_capabilities=["snapshot"],
    ), workspace


@pytest.mark.asyncio
async def test_runtime_failure_is_ordered_bounded_and_stable(db_session):
    space, workspace = await _seed_space(db_session, "failure")

    class RuntimeTestError(Exception):
        code = "runtime_not_ready"

    async def failing_executor(*_):
        raise RuntimeTestError("authorization: bearer private-token")

    task = await service.submit_task(
        db_session,
        workspace_id=workspace.id,
        space_id=space.id,
        request_id="runtime-failure",
        capability="snapshot",
        args={},
        executor=failing_executor,
    )
    events = await service.list_events(db_session, workspace_id=workspace.id, space_id=space.id)

    assert task.status == "failed"
    assert task.error_code == "runtime_not_ready"
    assert "private-token" not in (task.error_message or "")
    assert [event.kind for event in events] == ["queued", "started", "failed"]


@pytest.mark.asyncio
async def test_distinct_spaces_can_execute_with_independent_fake_executors(db_session, db_engine):
    first, first_workspace = await _seed_space(db_session, "first")
    second, second_workspace = await _seed_space(db_session, "second")
    entered = 0
    release = asyncio.Event()

    async def executor(_, __, ___):
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await release.wait()
        return {"ok": True}

    first_task = await service.submit_task(
        db_session,
        workspace_id=first_workspace.id,
        space_id=first.id,
        request_id="one",
        capability="snapshot",
        args={},
    )
    second_task = await service.submit_task(
        db_session,
        workspace_id=second_workspace.id,
        space_id=second.id,
        request_id="two",
        capability="snapshot",
        args={},
    )
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as first_session, session_factory() as second_session:
        one, two = await asyncio.gather(
            service.execute_task(first_session, task_id=first_task.id, executor=executor),
            service.execute_task(second_session, task_id=second_task.id, executor=executor),
        )
    assert entered == 2
    assert one.status == two.status == "completed"
