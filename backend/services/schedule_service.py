import logging

from croniter import croniter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.schedule import CronSchedule
from backend.schemas.schedule import CronScheduleCreate, CronScheduleUpdate

logger = logging.getLogger(__name__)


def validate_cron_expression(expr: str) -> bool:
    return croniter.is_valid(expr)


async def list_schedules(
    session: AsyncSession,
    source_id: str | None = None,
    enabled: bool | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[CronSchedule], int]:
    query = select(CronSchedule).order_by(CronSchedule.created_at.desc())
    count_query = select(func.count()).select_from(CronSchedule)

    if source_id:
        query = query.where(CronSchedule.source_id == source_id)
        count_query = count_query.where(CronSchedule.source_id == source_id)
    if enabled is not None:
        query = query.where(CronSchedule.enabled == enabled)
        count_query = count_query.where(CronSchedule.enabled == enabled)
    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * limit
    result = await session.execute(query.offset(offset).limit(limit))
    return result.scalars().all(), total


async def get_schedule(session: AsyncSession, schedule_id: str) -> CronSchedule | None:
    result = await session.execute(
        select(CronSchedule).where(CronSchedule.id == schedule_id)
    )
    return result.scalar_one_or_none()


async def create_schedule(session: AsyncSession, data: CronScheduleCreate) -> CronSchedule:
    schedule = CronSchedule(**data.model_dump())
    session.add(schedule)
    await session.flush()
    await session.refresh(schedule)
    _sync_redbeat(schedule)
    return schedule


async def update_schedule(
    session: AsyncSession, schedule: CronSchedule, data: CronScheduleUpdate
) -> CronSchedule:
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(schedule, key, value)
    await session.flush()
    await session.refresh(schedule)
    _sync_redbeat(schedule)
    return schedule


async def delete_schedule(session: AsyncSession, schedule: CronSchedule) -> None:
    schedule_id = schedule.id
    await session.delete(schedule)
    await session.flush()
    _remove_redbeat(schedule_id)


# ── redbeat live-sync ─────────────────────────────────────────────────────────
# Without this, redbeat's redis-backed entries only reflect what existed when
# celery beat last started (same staleness problem as the old static
# beat_schedule dict) — a schedule created/edited/deleted through this API
# would need a beat restart to take effect. Only wired for task_executor ==
# "celery" (mirrors browser_pool's use_redis gate) so local/dev mode, which
# has no reason to run redis, never touches it. Called after flush() but
# before the request-level commit (backend.database.get_db commits after the
# router returns) — a request that flushes here then fails to commit for an
# unrelated reason would leave a redbeat entry pointing at a schedule that
# doesn't durably exist. Rare enough (flush already caught the common
# failures) not to warrant restructuring the session lifecycle for.
def _sync_redbeat(schedule: CronSchedule) -> None:
    from backend.config import get_settings

    if get_settings().task_executor != "celery":
        return
    from backend.worker import redbeat_sync

    try:
        redbeat_sync.sync_entry(schedule)
    except Exception as exc:
        logger.warning("redbeat sync failed for schedule %s: %s", schedule.id, exc)


def _remove_redbeat(schedule_id: str) -> None:
    from backend.config import get_settings

    if get_settings().task_executor != "celery":
        return
    from backend.worker import redbeat_sync

    try:
        redbeat_sync.remove_entry(schedule_id)
    except Exception as exc:
        logger.warning("redbeat remove failed for schedule %s: %s", schedule_id, exc)
