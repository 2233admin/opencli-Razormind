from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.record import CollectedRecord
from backend.models.studio import StudioWorkflow


async def list_records(
    session: AsyncSession,
    source_id: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    limit: int = 20,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> tuple[list[CollectedRecord], int]:
    sort_column = {
        "created_at": CollectedRecord.created_at,
        "updated_at": CollectedRecord.updated_at,
        "status": CollectedRecord.status,
        "source_id": CollectedRecord.source_id,
        "workflow_id": CollectedRecord.workflow_id,
        "workflow_run_id": CollectedRecord.workflow_run_id,
    }.get(sort_by, CollectedRecord.created_at)
    query = select(CollectedRecord).order_by(
        sort_column.asc() if sort_order == "asc" else sort_column.desc()
    )
    count_query = select(func.count()).select_from(CollectedRecord)

    filters = []
    if source_id:
        filters.append(CollectedRecord.source_id == source_id)
    if task_id:
        filters.append(CollectedRecord.task_id == task_id)
    if project_id:
        project_workflows = select(StudioWorkflow.id).where(
            StudioWorkflow.project_id == project_id,
            StudioWorkflow.archived.is_(False),
        )
        filters.append(CollectedRecord.workflow_id.in_(project_workflows))
    if status:
        filters.append(CollectedRecord.status == status)
    if search:
        term = search.lower()
        filters.append(
            or_(
                func.lower(func.cast(CollectedRecord.normalized_data, String)).contains(term),
                func.lower(func.cast(CollectedRecord.raw_data, String)).contains(term),
                func.lower(func.cast(CollectedRecord.ai_enrichment, String)).contains(term),
            )
        )

    if filters:
        for f in filters:
            query = query.where(f)
            count_query = count_query.where(f)

    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * limit
    result = await session.execute(query.offset(offset).limit(limit))
    return result.scalars().all(), total


async def get_record(
    session: AsyncSession, record_id: str
) -> CollectedRecord | None:
    result = await session.execute(
        select(CollectedRecord).where(CollectedRecord.id == record_id)
    )
    return result.scalar_one_or_none()


async def delete_records(
    session: AsyncSession,
    record_ids: list[str],
) -> int:
    """Delete records by IDs. Returns deleted count."""
    result = await session.execute(
        delete(CollectedRecord).where(CollectedRecord.id.in_(record_ids))
    )
    return result.rowcount


async def delete_all_records(
    session: AsyncSession,
    source_id: str | None = None,
) -> int:
    """Delete all records, optionally filtered by source. Returns deleted count."""
    stmt = delete(CollectedRecord)
    if source_id:
        stmt = stmt.where(CollectedRecord.source_id == source_id)
    result = await session.execute(stmt)
    return result.rowcount
