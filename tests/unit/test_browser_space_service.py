from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from backend.models.browser import BrowserInstance
from backend.models.browser_space import (
    BrowserSpaceEventKind,
    BrowserSpaceStatus,
    BrowserSpaceTaskStatus,
)
from backend.models.identity import Workspace
from backend.services.browser_space_service import (
    BrowserSpaceError,
    cancel_task,
    close_space,
    create_space,
    execute_task,
    get_latest_task,
    list_events,
    submit_task,
)
from tests.browser_space_fixtures import configure_space_runtime


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.release_calls: defaultdict[str, int] = defaultdict(int)
        self.started: dict[str, asyncio.Event] = {}
        self.resume: dict[str, asyncio.Event] = {}
        self.cancelled: set[str] = set()

    async def execute(self, *, instance, capability, args, timeout_seconds, task_id):
        self.calls.append(task_id)
        started = self.started.setdefault(task_id, asyncio.Event())
        resume = self.resume.setdefault(task_id, asyncio.Event())
        started.set()
        await resume.wait()
        if task_id in self.cancelled:
            raise asyncio.CancelledError
        return {
            "value": args.get("value", "ok"),
            "url": "https://secret.invalid",
            "message": "token=topsecret Authorization: Bearer othersecret",
        }

    async def cancel(self, task_id: str) -> bool:
        self.cancelled.add(task_id)
        self.resume.setdefault(task_id, asyncio.Event()).set()
        return True

    async def release(self, space_id: str) -> None:
        self.release_calls[space_id] += 1


async def _space(db_session, workspace_id: str, instance_id: str, owner: str = "user-1"):
    instance = await db_session.get(BrowserInstance, instance_id)
    if not instance.runtime_bundle_id:
        await configure_space_runtime(db_session, instance)
    return await create_space(
        db_session,
        workspace_id,
        {
            "browser_instance_id": instance_id,
            "owner_type": "operator",
            "owner_id": owner,
            "granted_capabilities": ["snapshot"],
        },
    )


@pytest.mark.asyncio
async def test_same_space_tasks_are_serialized(db_session):
    workspace = Workspace(name="W", slug="w")
    instance = BrowserInstance(endpoint="http://browser-1")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    executor = FakeExecutor()

    first = asyncio.create_task(
        submit_task(
            db_session,
            workspace.id,
            space.id,
            {"request_id": "r1", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    )
    while not executor.calls:
        await asyncio.sleep(0)
    with pytest.raises(BrowserSpaceError) as exc:
        await submit_task(
            db_session,
            workspace.id,
            space.id,
            {"request_id": "r2", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    assert exc.value.code == "space_task_in_progress"
    executor.resume[executor.calls[0]].set()
    await first


@pytest.mark.asyncio
async def test_idempotency_calls_executor_once(db_session):
    workspace = Workspace(name="W2", slug="w2")
    instance = BrowserInstance(endpoint="http://browser-2")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    executor = FakeExecutor()
    task1 = asyncio.create_task(
        submit_task(
            db_session,
            workspace.id,
            space.id,
            {"request_id": "same", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    )
    while not executor.calls:
        await asyncio.sleep(0)
    executor.resume[executor.calls[0]].set()
    first, _ = await task1
    second, _ = await submit_task(
        db_session,
        workspace.id,
        space.id,
        {"request_id": "same", "capability": "snapshot", "args": {}},
        executor=executor,
    )
    assert first.id == second.id
    assert executor.calls == [first.id]


@pytest.mark.asyncio
async def test_deferred_submission_commits_without_runtime_call(db_session):
    workspace = Workspace(name="W2-deferred", slug="w2-deferred")
    instance = BrowserInstance(endpoint="http://browser-2-deferred")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    executor = FakeExecutor()

    task, created = await submit_task(
        db_session,
        workspace.id,
        space.id,
        {"request_id": "deferred", "capability": "snapshot", "args": {}},
        executor=executor,
        execute=False,
    )

    assert created is True
    assert task.status == BrowserSpaceTaskStatus.QUEUED
    assert executor.calls == []


@pytest.mark.asyncio
async def test_latest_task_is_scoped_to_requested_space(db_session):
    workspace = Workspace(name="W2-latest", slug="w2-latest")
    first_instance = BrowserInstance(endpoint="http://browser-2-latest-a", profile_name="latest-a")
    second_instance = BrowserInstance(endpoint="http://browser-2-latest-b", profile_name="latest-b")
    db_session.add_all([workspace, first_instance, second_instance])
    await db_session.commit()
    first_space = await _space(db_session, workspace.id, first_instance.id, "u1")
    second_space = await _space(db_session, workspace.id, second_instance.id, "u2")

    first_task, _ = await submit_task(
        db_session,
        workspace.id,
        first_space.id,
        {"request_id": "first", "capability": "snapshot", "args": {}},
        execute=False,
    )
    second_task, _ = await submit_task(
        db_session,
        workspace.id,
        second_space.id,
        {"request_id": "second", "capability": "snapshot", "args": {}},
        execute=False,
    )

    latest = await get_latest_task(db_session, first_space.id)

    assert latest is not None
    assert latest.id == first_task.id
    assert latest.id != second_task.id


@pytest.mark.asyncio
async def test_cross_process_cancellation_waits_for_runtime_cleanup(db_session):
    workspace = Workspace(name="W2-cancel", slug="w2-cancel")
    instance = BrowserInstance(endpoint="http://browser-2-cancel")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    task, _ = await submit_task(
        db_session,
        workspace.id,
        space.id,
        {"request_id": "cross-process-cancel", "capability": "snapshot", "args": {}},
        execute=False,
    )

    pending = await cancel_task(db_session, workspace.id, space.id)

    assert pending.status == BrowserSpaceTaskStatus.QUEUED
    assert pending.cancel_requested is True
    finished = await execute_task(db_session, task, {}, executor=FakeExecutor())
    assert finished.status == BrowserSpaceTaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_close_waits_for_cross_process_cancellation_acknowledgement(db_session):
    workspace = Workspace(name="W2-close", slug="w2-close")
    instance = BrowserInstance(endpoint="http://browser-2-close")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    task, _ = await submit_task(
        db_session,
        workspace.id,
        space.id,
        {"request_id": "close-running", "capability": "snapshot", "args": {}},
        execute=False,
    )
    task.status = BrowserSpaceTaskStatus.RUNNING.value
    space.status = BrowserSpaceStatus.RUNNING.value
    await db_session.commit()

    with pytest.raises(BrowserSpaceError) as exc:
        await close_space(db_session, workspace.id, space.id)

    assert exc.value.code == "cancellation_pending"
    await db_session.refresh(space)
    assert space.status == BrowserSpaceStatus.RUNNING.value


@pytest.mark.asyncio
async def test_cancellation_acknowledges_and_releases_once(db_session):
    workspace = Workspace(name="W3", slug="w3")
    instance = BrowserInstance(endpoint="http://browser-3")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    executor = FakeExecutor()
    pending = asyncio.create_task(
        submit_task(
            db_session,
            workspace.id,
            space.id,
            {"request_id": "cancel", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    )
    while not executor.calls:
        await asyncio.sleep(0)
    cancelled = await cancel_task(db_session, workspace.id, space.id, executor=executor)
    result, _ = await pending
    assert cancelled.status in {
        BrowserSpaceTaskStatus.RUNNING,
        BrowserSpaceTaskStatus.CANCELLED,
    }
    assert result.status == BrowserSpaceTaskStatus.CANCELLED
    events = await list_events(db_session, workspace.id, space.id)
    assert [event.kind for event in events][-2:] == [
        BrowserSpaceEventKind.CANCEL_REQUESTED,
        BrowserSpaceEventKind.CANCELLED,
    ]


@pytest.mark.asyncio
async def test_result_and_events_are_bounded_and_ordered(db_session):
    workspace = Workspace(name="W4", slug="w4")
    instance = BrowserInstance(endpoint="http://browser-4")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    executor = FakeExecutor()
    task = asyncio.create_task(
        submit_task(
            db_session,
            workspace.id,
            space.id,
            {"request_id": "bounded", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    )
    while not executor.calls:
        await asyncio.sleep(0)
    executor.resume[executor.calls[0]].set()
    row, _ = await task
    assert row.result == {"value": "ok", "message": "[REDACTED] [REDACTED]"}
    events = await list_events(db_session, workspace.id, space.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert all(event.payload is not None for event in events)


@pytest.mark.asyncio
async def test_runtime_timeout_finishes_task_as_failed(db_session):
    workspace = Workspace(name="W4-timeout", slug="w4-timeout")
    instance = BrowserInstance(endpoint="http://browser-4-timeout")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    task, created = await submit_task(
        db_session,
        workspace.id,
        space.id,
        {"request_id": "timeout", "capability": "snapshot", "args": {}},
        execute=False,
    )

    assert created is True
    finished = await execute_task(
        db_session,
        task,
        {},
        timeout_seconds=0.01,
        executor=FakeExecutor(),
    )

    assert finished.status == BrowserSpaceTaskStatus.FAILED
    assert finished.error_code == "timeout"


@pytest.mark.asyncio
async def test_distinct_spaces_can_execute_concurrently(db_session):
    workspace = Workspace(name="W5", slug="w5")
    one = BrowserInstance(endpoint="http://browser-5a", profile_name="concurrent-a")
    two = BrowserInstance(endpoint="http://browser-5b", profile_name="concurrent-b")
    db_session.add_all([workspace, one, two])
    await db_session.commit()
    first_space = await _space(db_session, workspace.id, one.id, "u1")
    second_space = await _space(db_session, workspace.id, two.id, "u2")
    executor = FakeExecutor()
    first = asyncio.create_task(
        submit_task(
            db_session,
            workspace.id,
            first_space.id,
            {"request_id": "a", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    )
    second = asyncio.create_task(
        submit_task(
            db_session,
            workspace.id,
            second_space.id,
            {"request_id": "b", "capability": "snapshot", "args": {}},
            executor=executor,
        )
    )
    while len(executor.calls) < 2:
        await asyncio.sleep(0)
    for task_id in executor.calls:
        executor.resume[task_id].set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_close_space_releases_browser_instance_once(db_session):
    workspace = Workspace(name="W6", slug="w6")
    instance = BrowserInstance(endpoint="http://browser-6")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    executor = FakeExecutor()
    closed = await close_space(db_session, workspace.id, space.id, executor=executor)
    again = await close_space(db_session, workspace.id, space.id, executor=executor)
    assert closed.status == BrowserSpaceStatus.CLOSED
    assert again.id == closed.id
    assert executor.release_calls[space.id] == 1


@pytest.mark.asyncio
async def test_active_instance_reservation_is_unique(db_session):
    workspace = Workspace(name="W7", slug="w7")
    instance = BrowserInstance(endpoint="http://browser-7")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    await _space(db_session, workspace.id, instance.id)
    with pytest.raises(BrowserSpaceError) as exc:
        await _space(db_session, workspace.id, instance.id, "other")
    assert exc.value.code == "browser_instance_in_use"


@pytest.mark.asyncio
async def test_closed_space_rejects_new_task(db_session):
    workspace = Workspace(name="W8", slug="w8")
    instance = BrowserInstance(endpoint="http://browser-8")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)
    await close_space(db_session, workspace.id, space.id)
    with pytest.raises(BrowserSpaceError) as exc:
        await submit_task(
            db_session,
            workspace.id,
            space.id,
            {"request_id": "closed", "capability": "snapshot", "args": {}},
            executor=FakeExecutor(),
        )
    assert exc.value.code == "closed_space"


@pytest.mark.asyncio
async def test_cancel_without_active_task_returns_stable_conflict(db_session):
    workspace = Workspace(name="W9", slug="w9")
    instance = BrowserInstance(endpoint="http://browser-9")
    db_session.add_all([workspace, instance])
    await db_session.commit()
    space = await _space(db_session, workspace.id, instance.id)

    with pytest.raises(BrowserSpaceError) as exc:
        await cancel_task(db_session, workspace.id, space.id)

    assert exc.value.code == "no_active_task"
