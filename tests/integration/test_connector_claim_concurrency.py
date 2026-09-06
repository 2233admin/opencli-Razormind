"""Independent checks of connector claims on the supported SQLite backend."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.connector_reply import ConnectorOutboundDelivery
from backend.services import connector_outbound_service as outbound
from tests.integration.test_connector_reply_phase_two import (
    _grant,
    p2_scope,  # noqa: F401 - reuse the real isolated SQLite fixture
)


async def test_two_sqlite_workers_cannot_claim_the_same_delivery(p2_scope, monkeypatch):  # noqa: F811
    _, delivery = await _grant(p2_scope)
    original_scalar = AsyncSession.scalar
    readers = 0
    both_read = asyncio.Event()

    async def simultaneous_read(session, statement, *args, **kwargs):
        nonlocal readers
        result = await original_scalar(session, statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", [])
        if (
            descriptions
            and descriptions[0].get("entity") is ConnectorOutboundDelivery
            and result is not None
            and result.id == delivery.id
        ):
            readers += 1
            if readers == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=5)
        return result

    monkeypatch.setattr(AsyncSession, "scalar", simultaneous_read)
    results = await asyncio.gather(
        outbound._claim_delivery(p2_scope, delivery.id),
        outbound._claim_delivery(p2_scope, delivery.id),
    )
    assert sum(result is not None for result in results) == 1, results
