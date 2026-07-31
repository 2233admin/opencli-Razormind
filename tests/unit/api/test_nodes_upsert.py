"""Unit tests for backend/api/v1/nodes.py's `_upsert_node` runtimes handling.

Uses the shared async `db_session` fixture (tests/conftest.py) directly —
no WS/HTTP layer involved — so persistence of the new nullable
EdgeNode.runtimes column is verified without the cross-event-loop fragility
of driving a sync TestClient websocket against an async DB fixture (see the
integration test file's note for why that path was not used there).
"""

import pytest
from sqlalchemy import select

from backend.api.v1.nodes import _upsert_node
from backend.models.edge_node import EdgeNode


@pytest.mark.asyncio
async def test_upsert_node_persists_runtimes_on_create(db_session):
    node = await _upsert_node(
        db_session, "http://agent:1", runtimes=["pi", "stub"]
    )
    await db_session.commit()

    result = await db_session.execute(select(EdgeNode).where(EdgeNode.url == "http://agent:1"))
    fetched = result.scalar_one()
    assert fetched.runtimes == ["pi", "stub"]
    assert node.runtimes == ["pi", "stub"]


@pytest.mark.asyncio
async def test_upsert_node_without_runtimes_leaves_column_null(db_session):
    node = await _upsert_node(db_session, "http://agent:2")
    await db_session.commit()

    result = await db_session.execute(select(EdgeNode).where(EdgeNode.url == "http://agent:2"))
    fetched = result.scalar_one()
    assert fetched.runtimes is None
    assert node.runtimes is None


@pytest.mark.asyncio
async def test_upsert_node_updates_runtimes_on_reregister(db_session):
    await _upsert_node(db_session, "http://agent:3", runtimes=["pi"])
    await db_session.commit()

    updated = await _upsert_node(db_session, "http://agent:3", runtimes=["pi", "langgraph"])
    await db_session.commit()

    assert updated.runtimes == ["pi", "langgraph"]


@pytest.mark.asyncio
async def test_upsert_node_reregister_without_runtimes_preserves_existing(db_session):
    """A re-register handshake that omits `runtimes` (e.g. an older agent
    build, or the HTTP registration path which never sends it) must not wipe
    out a previously-advertised runtimes list."""
    await _upsert_node(db_session, "http://agent:4", runtimes=["pi"])
    await db_session.commit()

    updated = await _upsert_node(db_session, "http://agent:4", runtimes=None)
    await db_session.commit()

    assert updated.runtimes == ["pi"]
