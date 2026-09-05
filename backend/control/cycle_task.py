"""cycle_task: the thin asyncio background-task wrapper around
``backend.control.cycle.run_control_cycle_once`` (issue 03 / PR-Control-4,
ADR-0007).

Started/stopped in the app lifespan (``backend.main.lifespan``) — NOT hung on
the collection scheduler (ADR-0007: the controller and the plant it
supervises must not share a scheduling domain). Deliberately minimal: all
actual decision/gate/actuator logic lives in ``backend.control.cycle``, which
is directly testable with an injected session and ``now`` without touching
this wrapper at all. This module only owns the asyncio loop, session
lifecycle per tick, and start/stop bookkeeping.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from backend.config import get_settings
from backend.control.cycle import run_control_cycle_once

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _tick() -> None:
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            result = await run_control_cycle_once(session, now=datetime.now(UTC))
            await session.commit()
            logger.info(
                "control cycle tick: sources=%d suggestions=%d executed=%d blocked=%d "
                "auto_resumed=%d outcomes=%s",
                result.sources_decided,
                result.suggestions_seen,
                len(result.executions),
                len(result.blocked),
                len(result.auto_resumed),
                result.outcome_counts,
            )
        except Exception:
            await session.rollback()
            logger.exception("control cycle tick failed")


async def _loop(period_seconds: float) -> None:
    logger.info("Control Cycle started (period=%ss)", period_seconds)
    try:
        while True:
            await _tick()
            await asyncio.sleep(period_seconds)
    except asyncio.CancelledError:
        logger.info("Control Cycle stopped")
        raise


def start() -> None:
    """Start the background task. No-op if already running (mirrors
    ``backend.scheduler.start_scheduler``'s idempotent-start convention)."""
    global _task
    if _task is not None and not _task.done():
        return
    period = get_settings().control_cycle_period_seconds
    _task = asyncio.create_task(_loop(period))


async def stop() -> None:
    """Cancel and await the background task's clean shutdown. No-op if not
    running."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


def is_running() -> bool:
    return _task is not None and not _task.done()
