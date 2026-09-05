"""Unit tests for backend/scheduler.py."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scheduler import (
    _fires_in_window,
    _get_enabled_schedules,
    start_scheduler,
    stop_scheduler,
)


@pytest.fixture(autouse=True)
def _reset_warned_bad_cron(monkeypatch):
    """AUDIT C2's warn-once cache is a process-lifetime module-level set by
    design — reset it around every test so test execution order can't leak
    warn state between cases (see triage doc P3-6 on shared process state)."""
    import backend.scheduler as sched_module

    sched_module._warned_bad_cron.clear()
    monkeypatch.setattr(
        "backend.services.automation_schedule_service.dispatch_due_automations",
        AsyncMock(return_value=[]),
    )
    yield
    sched_module._warned_bad_cron.clear()


# ── _fires_in_window tests ──────────────────────────────────────────────────────

def test_fires_in_window_fires_within_window_counts_once():
    """A fire time landing inside (window_start, window_end] counts."""
    last_tick = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    now = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)
    # "* * * * *" fires every minute; next fire after 12:00:00 is 12:01:00
    assert _fires_in_window("* * * * *", "sched-1", last_tick, now) == 1


def test_fires_in_window_open_left_edge_excludes_prior_fire():
    """A fire time exactly at window_start (the exclusive edge) is not recounted."""
    last_tick = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)  # itself a fire instant
    now = datetime(2024, 6, 1, 12, 2, 0, tzinfo=UTC)
    assert _fires_in_window("* * * * *", "sched-1", last_tick, now) == 1  # only 12:02:00


def test_fires_in_window_consecutive_windows_never_double_count():
    """Two back-to-back (last_tick, now] windows sharing a boundary each see
    the boundary fire exactly once between them — this is the C4 guarantee."""
    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)  # exact fire instant
    t2 = datetime(2024, 6, 1, 12, 2, 0, tzinfo=UTC)

    window1 = _fires_in_window("* * * * *", "sched-1", t0, t1)
    window2 = _fires_in_window("* * * * *", "sched-1", t1, t2)

    assert window1 == 1  # counts the 12:01:00 fire
    assert window2 == 1  # counts only 12:02:00 — 12:01:00 is not double-counted


def test_fires_in_window_no_fire_returns_zero():
    """No fire time in a short window returns 0."""
    last_tick = datetime(2024, 6, 1, 12, 0, 10, tzinfo=UTC)
    now = datetime(2024, 6, 1, 12, 0, 40, tzinfo=UTC)
    assert _fires_in_window("0 * * * *", "sched-1", last_tick, now) == 0


def test_fires_in_window_future_cron_not_due():
    """Cron for a later time has no fire in a window that ends before it."""
    last_tick = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    now = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)
    assert _fires_in_window("0 13 * * *", "sched-1", last_tick, now) == 0


def test_fires_in_window_slow_tick_coalesces_missed_fires():
    """A window spanning 3 missed fire times reports count=3 so the caller
    can dispatch once instead of once per missed fire."""
    last_tick = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    now = datetime(2024, 6, 1, 12, 3, 0, tzinfo=UTC)
    assert _fires_in_window("* * * * *", "sched-1", last_tick, now) == 3


def test_fires_in_window_malformed_cron_returns_zero_and_warns_once():
    """An unparseable cron_expression returns 0 (treated as not-due) and logs
    exactly one warning across repeated calls with the same
    (schedule_id, cron_expression) pair."""
    now = datetime.now(UTC)
    last_tick = now - timedelta(seconds=60)

    with patch("backend.scheduler.logger") as mock_logger:
        first = _fires_in_window("not-a-cron", "sched-bad", last_tick, now, name="Bad Sched")
        second = _fires_in_window("not-a-cron", "sched-bad", last_tick, now, name="Bad Sched")

    assert first == 0
    assert second == 0
    mock_logger.warning.assert_called_once()
    args = mock_logger.warning.call_args.args
    assert args[1] == "sched-bad"
    assert args[3] == "not-a-cron"


def test_fires_in_window_rewarns_when_expression_changes():
    """Editing a bad schedule to a different (still-bad) expression re-warns,
    while repeating the same bad expression does not spam."""
    now = datetime.now(UTC)
    last_tick = now - timedelta(seconds=60)

    with patch("backend.scheduler.logger") as mock_logger:
        _fires_in_window("bad-one", "sched-bad", last_tick, now)
        _fires_in_window("bad-one", "sched-bad", last_tick, now)  # same key: no rewarn
        _fires_in_window("bad-two", "sched-bad", last_tick, now)  # edited: rewarn

    assert mock_logger.warning.call_count == 2


def test_fires_in_window_independent_schedules_warn_independently():
    """Two different schedules sharing the same broken expression each get
    their own warning (the cache key includes schedule_id)."""
    now = datetime.now(UTC)
    last_tick = now - timedelta(seconds=60)

    with patch("backend.scheduler.logger") as mock_logger:
        _fires_in_window("not-a-cron", "sched-a", last_tick, now)
        _fires_in_window("not-a-cron", "sched-b", last_tick, now)

    assert mock_logger.warning.call_count == 2


# ── start_scheduler / stop_scheduler tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_start_scheduler_creates_task():
    """start_scheduler should create an asyncio Task."""
    import backend.scheduler as sched_module

    # Patch the loop function so it never actually sleeps
    async def mock_loop():
        await asyncio.sleep(3600)

    with patch("backend.scheduler._scheduler_loop", mock_loop):
        start_scheduler()
        try:
            assert sched_module._scheduler_task is not None
            assert isinstance(sched_module._scheduler_task, asyncio.Task)
        finally:
            stop_scheduler()
            # Allow cancellation to propagate
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stop_scheduler_cancels_task():
    """stop_scheduler should cancel the running task and set it to None."""
    import backend.scheduler as sched_module

    async def mock_loop():
        await asyncio.sleep(3600)

    with patch("backend.scheduler._scheduler_loop", mock_loop):
        start_scheduler()
        task = sched_module._scheduler_task
        assert task is not None

        stop_scheduler()
        await asyncio.sleep(0)

        assert sched_module._scheduler_task is None
        assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_stop_scheduler_when_no_task():
    """stop_scheduler should not raise when no task is running."""
    import backend.scheduler as sched_module
    sched_module._scheduler_task = None
    # Must not raise
    stop_scheduler()


# ── _get_enabled_schedules tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_enabled_schedules_empty():
    """Returns empty list when DB has no enabled schedules."""
    from backend.scheduler import _get_enabled_schedules

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.database.AsyncSessionLocal", return_value=cm):
        result = await _get_enabled_schedules()

    assert result == []


@pytest.mark.asyncio
async def test_get_enabled_schedules_returns_list():
    """Returns correctly shaped dicts for each enabled schedule."""
    from backend.scheduler import _get_enabled_schedules

    mock_sched = MagicMock()
    mock_sched.id = "sched-1"
    mock_sched.source_id = "src-1"
    mock_sched.cron_expression = "*/5 * * * *"
    mock_sched.parameters = {"limit": 10}
    mock_sched.name = "Test Schedule"

    mock_source = MagicMock()

    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_sched, mock_source)]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.database.AsyncSessionLocal", return_value=cm):
        result = await _get_enabled_schedules()

    assert len(result) == 1
    assert result[0]["schedule_id"] == "sched-1"
    assert result[0]["source_id"] == "src-1"
    assert result[0]["cron_expression"] == "*/5 * * * *"
    assert result[0]["parameters"] == {"limit": 10}
    assert result[0]["name"] == "Test Schedule"


# ── _scheduler_loop tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_loop_first_tick_establishes_watermark_no_dispatch():
    """First tick after process start only establishes last_tick; it must not
    fetch schedules or dispatch anything (no catch-up storm on restart)."""
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            raise asyncio.CancelledError()

    mock_get_schedules = AsyncMock(return_value=[])

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0]),
        patch("backend.scheduler._get_enabled_schedules", mock_get_schedules),
        patch("backend.executor.get_executor", return_value=AsyncMock()),
    ):
        await _scheduler_loop()

    mock_get_schedules.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_loop_dispatches_once_when_fire_in_window():
    """Once last_tick is established, a schedule with a fire time in
    (last_tick, now] is dispatched exactly once."""
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)

    due_schedule = {
        "schedule_id": "sched-fire",
        "source_id": "src-1",
        "cron_expression": "* * * * *",
        "parameters": {},
        "name": "every minute",
    }

    mock_executor = AsyncMock()
    mock_executor.dispatch_scheduled_collection = AsyncMock()

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1]),
        patch(
            "backend.scheduler._get_enabled_schedules",
            new=AsyncMock(return_value=[due_schedule]),
        ),
        patch("backend.scheduler._fires_in_window", return_value=1) as mock_fires,
        patch("backend.executor.get_executor", return_value=mock_executor),
    ):
        await _scheduler_loop()

    mock_executor.dispatch_scheduled_collection.assert_called_once_with(
        "sched-fire", "src-1", {}
    )
    mock_fires.assert_called_once_with(
        "* * * * *", "sched-fire", t0, t1, name="every minute"
    )


@pytest.mark.asyncio
async def test_scheduler_loop_recovers_queued_scheduled_agent_runs():
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)
    iteration = 0

    async def mock_sleep(_seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            raise asyncio.CancelledError()

    recover = AsyncMock(return_value=["scheduled-run-1"])
    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1]),
        patch("backend.scheduler._get_enabled_schedules", new=AsyncMock(return_value=[])),
        patch("backend.executor.get_executor", return_value=AsyncMock()),
        patch(
            "backend.services.scheduled_run_recovery.recover_queued_scheduled_runs_local",
            recover,
        ),
    ):
        await _scheduler_loop()

    recover.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_loop_skips_when_no_fire_in_window():
    """_scheduler_loop does not dispatch schedules with zero fires in the window."""
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)

    non_due_schedule = {
        "schedule_id": "sched-skip",
        "source_id": "src-1",
        "cron_expression": "0 0 1 1 *",
        "parameters": {},
        "name": "yearly",
    }

    mock_executor = AsyncMock()
    mock_executor.dispatch_scheduled_collection = AsyncMock()

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1]),
        patch(
            "backend.scheduler._get_enabled_schedules",
            new=AsyncMock(return_value=[non_due_schedule]),
        ),
        patch("backend.scheduler._fires_in_window", return_value=0),
        patch("backend.executor.get_executor", return_value=mock_executor),
    ):
        await _scheduler_loop()

    mock_executor.dispatch_scheduled_collection.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_loop_consecutive_ticks_use_disjoint_windows():
    """last_tick advances to the previous tick's `now`, so tick N+1's window
    starts exactly where tick N's window ended — proving two consecutive
    ticks can never both dispatch for the same fire instant (AUDIT C4)."""
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)
    t2 = datetime(2024, 6, 1, 12, 2, 0, tzinfo=UTC)

    sched = {
        "schedule_id": "sched-1",
        "source_id": "src-1",
        "cron_expression": "* * * * *",
        "parameters": {},
        "name": "n",
    }

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 4:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1, t2]),
        patch("backend.scheduler._get_enabled_schedules", new=AsyncMock(return_value=[sched])),
        patch("backend.scheduler._fires_in_window", return_value=0) as mock_fires,
        patch("backend.executor.get_executor", return_value=AsyncMock()),
    ):
        await _scheduler_loop()

    assert mock_fires.call_args_list[0].args[2:4] == (t0, t1)
    assert mock_fires.call_args_list[1].args[2:4] == (t1, t2)


@pytest.mark.asyncio
async def test_scheduler_loop_coalesces_multiple_fires_into_single_dispatch():
    """A window spanning several missed fire times (fire_count > 1) still
    dispatches exactly once, and logs the coalesced count at debug level."""
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 3, 0, tzinfo=UTC)

    sched = {
        "schedule_id": "sched-1",
        "source_id": "src-1",
        "cron_expression": "* * * * *",
        "parameters": {},
        "name": "n",
    }

    mock_executor = AsyncMock()
    mock_executor.dispatch_scheduled_collection = AsyncMock()

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1]),
        patch("backend.scheduler._get_enabled_schedules", new=AsyncMock(return_value=[sched])),
        patch("backend.scheduler._fires_in_window", return_value=3),
        patch("backend.executor.get_executor", return_value=mock_executor),
        patch("backend.scheduler.logger") as mock_logger,
    ):
        await _scheduler_loop()

    mock_executor.dispatch_scheduled_collection.assert_called_once()
    mock_logger.debug.assert_called_once()
    assert mock_logger.debug.call_args.args[1:] == ("sched-1", 3)


@pytest.mark.asyncio
async def test_scheduler_loop_handles_exception_and_continues():
    """_scheduler_loop catches non-cancel exceptions and keeps running,
    leaving last_tick untouched from before the failed iteration so the next
    successful tick's window naturally widens to catch up."""
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 5, 0, tzinfo=UTC)

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration == 2:
            raise RuntimeError("transient error")
        if iteration >= 4:
            raise asyncio.CancelledError()

    mock_get_schedules = AsyncMock(return_value=[])

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1]),
        patch("backend.scheduler._get_enabled_schedules", mock_get_schedules),
        patch("backend.executor.get_executor", return_value=AsyncMock()),
    ):
        await _scheduler_loop()

    # 1: first tick (sets last_tick=t0) 2: RuntimeError (last_tick untouched)
    # 3: recovers with now=t1, window (t0, t1] 4: cancels
    assert iteration == 4
    mock_get_schedules.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_loop_dispatch_failure_does_not_stall_watermark():
    """If one schedule's dispatch raises, (1) the other due schedule in the
    same tick is still dispatched, and (2) last_tick still advances — so the
    next tick's window does not re-widen and re-dispatch a schedule that
    already fired successfully earlier in this same tick.

    Uses the real _fires_in_window (not mocked): "1 12 * * *" fires exactly
    once, at 12:01:00. If last_tick incorrectly stalled at t0=12:00:00 after
    the failure, tick 3's window would be the stale, wider (12:00:00, 12:02:00]
    instead of the correct (12:01:00, 12:02:00] — and would wrongly re-count
    the 12:01:00 fire, re-dispatching sched-ok a second time.
    """
    from backend.scheduler import _scheduler_loop

    t0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC)  # "1 12 * * *" fires here
    t2 = datetime(2024, 6, 1, 12, 2, 0, tzinfo=UTC)

    sched_ok = {
        "schedule_id": "sched-ok",
        "source_id": "src-ok",
        "cron_expression": "1 12 * * *",
        "parameters": {},
        "name": "ok",
    }
    sched_fail = {
        "schedule_id": "sched-fail",
        "source_id": "src-fail",
        "cron_expression": "1 12 * * *",
        "parameters": {},
        "name": "fail",
    }

    async def dispatch_side_effect(schedule_id, source_id, parameters):
        if schedule_id == "sched-fail":
            raise RuntimeError("boom")

    mock_executor = AsyncMock()
    mock_executor.dispatch_scheduled_collection = AsyncMock(side_effect=dispatch_side_effect)

    iteration = 0

    async def mock_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 4:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("backend.scheduler._now", side_effect=[t0, t1, t2]),
        patch(
            "backend.scheduler._get_enabled_schedules",
            new=AsyncMock(return_value=[sched_ok, sched_fail]),
        ),
        patch("backend.executor.get_executor", return_value=mock_executor),
    ):
        await _scheduler_loop()

    calls = mock_executor.dispatch_scheduled_collection.call_args_list
    ok_calls = [c for c in calls if c.args[0] == "sched-ok"]
    fail_calls = [c for c in calls if c.args[0] == "sched-fail"]

    assert len(ok_calls) == 1, "sched-ok must not be re-dispatched after sched-fail's failure"
    assert len(fail_calls) == 1


# ── review_required gating (PR-captcha-governance) ────────────────────────


@pytest.mark.asyncio
async def test_get_enabled_schedules_skips_review_required_sources(db_session):
    """A source flagged review_required (e.g. by a captcha pause) must not be
    dispatched by the scheduler until a human clears the flag — the control
    loop writes the state, the scheduler must honor it."""
    from backend.models.schedule import CronSchedule
    from backend.models.source import DataSource

    good = DataSource(
        name="good", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/good.xml"},
        enabled=True, review_required=False,
    )
    flagged = DataSource(
        name="flagged", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/flagged.xml"},
        enabled=True, review_required=True,
    )
    db_session.add_all([good, flagged])
    await db_session.flush()

    db_session.add_all([
        CronSchedule(
            source_id=good.id, name="s-good", cron_expression="* * * * *", enabled=True
        ),
        CronSchedule(
            source_id=flagged.id,
            name="s-flagged",
            cron_expression="* * * * *",
            enabled=True,
        ),
    ])
    await db_session.flush()

    with patch("backend.database.AsyncSessionLocal", return_value=db_session):
        schedules = await _get_enabled_schedules()

    source_ids = {s["source_id"] for s in schedules}
    assert good.id in source_ids
    assert flagged.id not in source_ids


@pytest.mark.asyncio
async def test_get_enabled_schedules_still_skips_disabled_sources(db_session):
    """The existing enabled gating keeps working alongside the review gate."""
    from backend.models.schedule import CronSchedule
    from backend.models.source import DataSource

    disabled = DataSource(
        name="disabled", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/d.xml"},
        enabled=False, review_required=False,
    )
    db_session.add(disabled)
    await db_session.flush()
    db_session.add(
        CronSchedule(source_id=disabled.id, name="s-d", cron_expression="* * * * *", enabled=True)
    )
    await db_session.flush()

    with patch("backend.database.AsyncSessionLocal", return_value=db_session):
        schedules = await _get_enabled_schedules()

    assert schedules == []
