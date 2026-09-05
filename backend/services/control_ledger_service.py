"""Read-only listing over the control_actions Evidence Ledger (issue 07).

Distinct from backend/api/v1/control.py's GET /control/advisory-report (a
folded aggregate report): this is the raw row-level listing an operator
audits — "what did the controller suggest/do for source X, and what
happened" — filterable and paginated like every other list endpoint
(backend/services/record_service.py is the sibling pattern).

Pure read: nothing here ever writes to control_actions or any other table.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.control_action import ControlActionRecord


async def list_control_actions(
    session: AsyncSession,
    source_id: str | None = None,
    mode: str | None = None,
    outcome: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[ControlActionRecord], int]:
    """List Evidence Ledger rows, newest first, with optional equality
    filters on source_id/mode/outcome.

    ``outcome`` also accepts the sentinel "pending" to mean "not yet judged"
    (``evaluated_at IS NULL``) — pending rows have no ``outcome`` value in the
    DB (see backend/models/control_action.py), so a literal-equality filter
    on the column can never select them; this is the same pending-vs-verdict
    distinction the advisory-report tally already draws (backend/api/v1/
    control.py's ``_tally``).
    """
    query = select(ControlActionRecord).order_by(ControlActionRecord.created_at.desc())
    count_query = select(func.count()).select_from(ControlActionRecord)

    filters = []
    if source_id:
        filters.append(ControlActionRecord.source_id == source_id)
    if mode:
        filters.append(ControlActionRecord.mode == mode)
    if outcome:
        if outcome == "pending":
            filters.append(ControlActionRecord.evaluated_at.is_(None))
        else:
            filters.append(ControlActionRecord.outcome == outcome)

    for f in filters:
        query = query.where(f)
        count_query = count_query.where(f)

    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * limit
    result = await session.execute(query.offset(offset).limit(limit))
    return result.scalars().all(), total
