"""Local async scheduler — replaces Celery Beat when TASK_EXECUTOR=local."""

import asyncio
import logging
from datetime import UTC, datetime

from croniter import croniter

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None

# AUDIT C2: (schedule_id, cron_expression) pairs we've already warned about,
# so a permanently-malformed cron doesn't spam a warning every tick. Keyed on
# the expression too, so editing the schedule to a new (still-bad) value
# warns again instead of staying silent forever. Process-lifetime cache —
# intentionally not cleared on scheduler stop/start within the same process.
_warned_bad_cron: set[tuple[str, str]] = set()


async def _get_enabled_schedules() -> list[dict]:
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.schedule import CronSchedule
    from backend.models.source import DataSource

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CronSchedule, DataSource)
            .join(DataSource, CronSchedule.source_id == DataSource.id)
            .where(CronSchedule.enabled.is_(True), DataSource.enabled.is_(True))
            # A source flagged review_required (e.g. by a captcha pause — see
            # backend.pipeline.pipeline's captcha branch) must not be
            # dispatched until a human clears the flag; the control loop
            # writes the state, the scheduler honors it.
            .where(DataSource.review_required.is_(False))
        )
        return [
            {
                "schedule_id": sched.id,
                "source_id": sched.source_id,
                "cron_expression": sched.cron_expression,
                "parameters": sched.parameters,
                "name": sched.name,
            }
            for sched, _ in result.all()
        ]


def _now() -> datetime:
    """Thin seam over datetime.now so tests can drive the clock without sleeping."""
    return datetime.now(UTC)


def _fires_in_window(
    cron_expression: str,
    schedule_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    name: str | None = None,
) -> int:
    """Count cron fire times in the half-open interval (window_start, window_end].

    AUDIT C2: a cron_expression croniter can't parse used to be swallowed by
    a bare `except Exception: return False` — the schedule went permanently
    silent with zero log trace. Now it warns once per (schedule_id,
    cron_expression) pair and is treated as "not due" (0 fires) so one bad
    schedule can't crash the loop or take down the others.
    """
    try:
        cron = croniter(cron_expression, window_start)
    except Exception as exc:
        warn_key = (schedule_id, cron_expression)
        if warn_key not in _warned_bad_cron:
            _warned_bad_cron.add(warn_key)
            logger.warning(
                "schedule %s (%s) has an unparseable cron_expression %r; "
                "skipping until fixed: %s",
                schedule_id, name or "?", cron_expression, exc,
            )
        return 0

    count = 0
    while True:
        next_fire = cron.get_next(datetime)
        if next_fire > window_end:
            break
        count += 1
    return count


async def _scheduler_loop() -> None:
    logger.info("Local scheduler started")
    # AUDIT C4: the previous "due within the last 61s" check was a fixed
    # window decoupled from actual tick cadence (sleep(60) + loop body
    # time) — drift near the boundary could get one fire dispatched by two
    # consecutive ticks, and a slow loop body (>1s) could silently miss a
    # fire. A process-local watermark instead makes consecutive ticks cover
    # disjoint, gapless (last_tick, now] windows: no fire time can ever fall
    # in two windows, and a slow tick just widens its own window (catching
    # up) instead of losing anything.
    last_tick: datetime | None = None
    while True:
        try:
            await asyncio.sleep(60)
            now = _now()

            if last_tick is None:
                # First tick after process start: establish the watermark
                # without dispatching, so a restart never replays everything
                # that fired while the process was down.
                last_tick = now
                continue

            schedules = await _get_enabled_schedules()
            from backend.executor import get_executor
            executor = get_executor()
            for sched in schedules:
                fire_count = _fires_in_window(
                    sched["cron_expression"],
                    sched["schedule_id"],
                    last_tick,
                    now,
                    name=sched["name"],
                )
                if fire_count == 0:
                    continue
                if fire_count > 1:
                    logger.debug(
                        "schedule %s coalesced %d fire times into one dispatch",
                        sched["schedule_id"], fire_count,
                    )
                logger.info("Firing schedule %s", sched["schedule_id"])
                try:
                    await executor.dispatch_scheduled_collection(
                        sched["schedule_id"],
                        sched["source_id"],
                        sched["parameters"],
                    )
                except Exception as exc:
                    # AUDIT C4: one schedule's dispatch raising must not stop
                    # the rest of this tick's schedules from being evaluated,
                    # and must not stall last_tick below — otherwise the next
                    # tick's re-widened window would re-dispatch schedules
                    # that already fired successfully earlier in this same
                    # tick, reopening the double-dispatch bug this fix closes.
                    logger.warning(
                        "schedule %s dispatch failed: %s", sched["schedule_id"], exc,
                    )

            try:
                from backend.services.automation_schedule_service import (
                    dispatch_due_automations,
                )

                automation_runs = await dispatch_due_automations(last_tick, now)
                if automation_runs:
                    logger.info(
                        "Dispatched %d scheduled Automation occurrence(s): %s",
                        len(automation_runs),
                        [run.id for run in automation_runs],
                    )
            except Exception as exc:
                logger.warning("Automation scheduler tick failed: %s", exc)

            try:
                from backend.services.scheduled_run_recovery import (
                    recover_queued_scheduled_runs_local,
                )

                queued_run_ids = await recover_queued_scheduled_runs_local()
                if queued_run_ids:
                    logger.info(
                        "Recovered %d queued scheduled Operations Agent run(s): %s",
                        len(queued_run_ids),
                        queued_run_ids,
                    )
            except Exception as exc:
                logger.warning("Scheduled Operations Agent recovery failed: %s", exc)

            last_tick = now
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Scheduler loop error: %s", exc)

    logger.info("Local scheduler stopped")


def start_scheduler() -> None:
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop())


def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
