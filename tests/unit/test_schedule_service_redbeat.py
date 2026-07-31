"""Unit tests for schedule_service's redbeat sync gating (runtime-hardening phase PR-C).

Existing tests in test_schedules_crud.py run under the default
task_executor="local" and never touch redbeat at all (the gate short-circuits)
— these specifically exercise the task_executor="celery" branch via mocking,
since there's no local redis in this environment.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.models.source import DataSource
from backend.schemas.schedule import CronScheduleCreate, CronScheduleUpdate
from backend.services import schedule_service


@pytest.fixture
async def source(db_session):
    s = DataSource(
        name="Redbeat Sched Src", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed"},
    )
    db_session.add(s)
    await db_session.flush()
    return s


def _celery_settings():
    settings = MagicMock()
    settings.task_executor = "celery"
    return settings


@pytest.mark.asyncio
async def test_create_schedule_local_executor_never_touches_redbeat(db_session, source):
    """Default task_executor="local": redbeat_sync module is never imported."""
    data = CronScheduleCreate(
        source_id=source.id, name="Local", cron_expression="0 * * * *"
    )
    with patch("backend.worker.redbeat_sync.sync_entry") as mock_sync:
        await schedule_service.create_schedule(db_session, data)
    mock_sync.assert_not_called()


@pytest.mark.asyncio
async def test_create_schedule_celery_executor_syncs_redbeat(db_session, source):
    data = CronScheduleCreate(
        source_id=source.id, name="Celery", cron_expression="0 * * * *"
    )
    with (
        patch("backend.config.get_settings", return_value=_celery_settings()),
        patch("backend.worker.redbeat_sync.sync_entry") as mock_sync,
    ):
        sched = await schedule_service.create_schedule(db_session, data)
    mock_sync.assert_called_once_with(sched)


@pytest.mark.asyncio
async def test_update_schedule_celery_executor_syncs_redbeat(db_session, source):
    data = CronScheduleCreate(
        source_id=source.id, name="Celery", cron_expression="0 * * * *"
    )
    sched = await schedule_service.create_schedule(db_session, data)

    with (
        patch("backend.config.get_settings", return_value=_celery_settings()),
        patch("backend.worker.redbeat_sync.sync_entry") as mock_sync,
    ):
        await schedule_service.update_schedule(
            db_session, sched, CronScheduleUpdate(cron_expression="*/10 * * * *")
        )
    mock_sync.assert_called_once()


@pytest.mark.asyncio
async def test_delete_schedule_celery_executor_removes_redbeat_entry(db_session, source):
    data = CronScheduleCreate(
        source_id=source.id, name="Celery", cron_expression="0 * * * *"
    )
    sched = await schedule_service.create_schedule(db_session, data)
    sched_id = sched.id

    with (
        patch("backend.config.get_settings", return_value=_celery_settings()),
        patch("backend.worker.redbeat_sync.remove_entry") as mock_remove,
    ):
        await schedule_service.delete_schedule(db_session, sched)
    mock_remove.assert_called_once_with(sched_id)


@pytest.mark.asyncio
async def test_sync_failure_does_not_fail_the_request(db_session, source):
    """A redis hiccup must not turn into a 500 on the schedule endpoint — the
    DB write is the source of truth, redbeat sync is best-effort."""
    data = CronScheduleCreate(
        source_id=source.id, name="Flaky", cron_expression="0 * * * *"
    )
    with (
        patch("backend.config.get_settings", return_value=_celery_settings()),
        patch("backend.worker.redbeat_sync.sync_entry", side_effect=ConnectionError("redis down")),
    ):
        sched = await schedule_service.create_schedule(db_session, data)  # must not raise

    assert sched.id is not None
