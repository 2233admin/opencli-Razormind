"""A reverse-channel peer may answer only work dispatched to its own socket."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend import ws_agent_manager as manager


@pytest.fixture(autouse=True)
def isolated_connections():
    registries = (
        manager._connections,
        manager._pending,
        manager._collect_owners,
        manager._pending_agent_tasks,
        manager._task_owners,
        manager._agent_task_callbacks,
    )
    for registry in registries:
        registry.clear()
    yield
    for registry in registries:
        registry.clear()


async def test_foreign_collection_result_cannot_complete_owned_work():
    owner, foreign = AsyncMock(), AsyncMock()
    sent = asyncio.Event()
    manager.register_connection("agent", owner)

    async def dispatch(payload):
        manager.resolve_response(payload["request_id"], {"identity": "foreign"}, foreign)
        sent.set()

    owner.send_json.side_effect = dispatch
    work = asyncio.create_task(
        manager.dispatch_collect(
            "agent", "site", "read", {}, [], "json", "bridge", request_id="owned"
        )
    )
    await asyncio.wait_for(sent.wait(), 1)
    assert not work.done()
    manager.resolve_response("owned", {"identity": "owner"}, owner)
    assert await work == {"identity": "owner"}


async def test_foreign_streaming_events_and_terminal_results_are_ignored():
    owner, foreign, event_handler = AsyncMock(), AsyncMock(), AsyncMock()
    sent = asyncio.Event()
    request_ids = []
    manager.register_connection("agent", owner)

    async def dispatch(payload):
        request_ids.append(payload["request_id"])
        await manager.resolve_agent_event(
            payload["request_id"], {"event": {"text": "foreign"}}, foreign
        )
        manager.resolve_agent_result(
            payload["request_id"], {"result": {"identity": "foreign"}}, foreign
        )
        sent.set()

    owner.send_json.side_effect = dispatch
    work = asyncio.create_task(manager.send_agent_task("agent", {"runtime": "pi"}, event_handler))
    await asyncio.wait_for(sent.wait(), 1)
    assert not work.done()
    event_handler.assert_not_awaited()
    await manager.resolve_agent_event(request_ids[0], {"event": {"text": "owner"}}, owner)
    manager.resolve_agent_result(request_ids[0], {"result": {"identity": "owner"}}, owner)
    assert await work == {"identity": "owner"}
    event_handler.assert_awaited_once_with({"text": "owner"})


async def test_disconnect_fails_pending_collect_without_waiting_for_timeout():
    owner = AsyncMock()
    sent = asyncio.Event()
    owner.send_json.side_effect = lambda payload: sent.set()
    manager.register_connection("agent", owner)
    work = asyncio.create_task(
        manager.dispatch_collect("agent", "site", "read", {}, [], "json", "bridge", timeout=60)
    )
    await asyncio.wait_for(sent.wait(), 1)
    assert manager.unregister_connection("agent", owner)
    with pytest.raises(RuntimeError, match="disconnected"):
        await asyncio.wait_for(work, 1)
    assert not manager._collect_owners


async def test_replaced_socket_cannot_unregister_or_answer_replacement_work():
    old, replacement = AsyncMock(), AsyncMock()
    manager.register_connection("agent", old)
    manager.register_connection("agent", replacement)
    assert not manager.unregister_connection("agent", old)
    assert manager.is_connected("agent")

    async def dispatch(payload):
        manager.resolve_response(payload["request_id"], {"identity": "old"}, old)
        manager.resolve_response(payload["request_id"], {"identity": "replacement"}, replacement)

    replacement.send_json.side_effect = dispatch
    result = await manager.dispatch_collect("agent", "site", "read", {}, [], "json", "bridge")
    assert result == {"identity": "replacement"}
    await asyncio.sleep(0)
    old.close.assert_awaited_once_with(code=1012)


async def test_duplicate_request_does_not_replace_the_original_waiter():
    owner = AsyncMock()
    sent = asyncio.Event()
    owner.send_json.side_effect = lambda payload: sent.set()
    manager.register_connection("agent", owner)
    work = asyncio.create_task(
        manager.dispatch_collect(
            "agent", "site", "read", {}, [], "json", "bridge", request_id="same"
        )
    )
    await asyncio.wait_for(sent.wait(), 1)
    with pytest.raises(ValueError, match="already active"):
        await manager.dispatch_collect(
            "agent", "site", "read", {}, [], "json", "bridge", request_id="same"
        )
    manager.resolve_response("same", {"identity": "original"}, owner)
    assert await work == {"identity": "original"}


@pytest.mark.parametrize("entrypoint", ["nodes", "browsers"])
async def test_websocket_entrypoints_reject_foreign_frames(entrypoint, monkeypatch):
    from unittest.mock import MagicMock

    from starlette.websockets import WebSocketDisconnect

    from backend import browser_pool, database
    from backend.api.v1 import browsers, nodes

    # Database availability is unrelated to message ownership. Exercise the real
    # receive loops through their supported non-fatal DB-upsert failure path.
    def unavailable_database():
        raise RuntimeError("Controlled unavailable metadata store")

    monkeypatch.setattr(database, "AsyncSessionLocal", unavailable_database)
    monkeypatch.setattr(browser_pool, "get_pool", MagicMock(return_value=object()))
    owner, peer = AsyncMock(), AsyncMock()
    manager.register_connection("http://owner", owner)
    collect = asyncio.get_running_loop().create_future()
    terminal = asyncio.get_running_loop().create_future()
    event_handler = AsyncMock()
    manager._pending["collect"] = collect
    manager._collect_owners["collect"] = owner
    manager._pending_agent_tasks["stream"] = terminal
    manager._task_owners["stream"] = owner
    manager._agent_task_callbacks["stream"] = (event_handler, "http://owner")
    peer.receive_json.side_effect = [
        {"type": "register", "agent_url": "http://peer", "mode": "bridge", "node_type": "shell"},
        {"type": "result", "request_id": "collect", "success": True},
        {"type": "agent_event", "request_id": "stream", "event": {"text": "forged"}},
        {"type": "agent_result", "request_id": "stream", "result": {"text": "forged"}},
        WebSocketDisconnect(),
    ]
    handler = nodes.node_ws_endpoint if entrypoint == "nodes" else browsers.agent_ws_endpoint
    await handler(peer)
    assert peer.receive_json.await_count == 5
    assert not collect.done()
    assert not terminal.done()
    event_handler.assert_not_awaited()
    assert manager.is_connected("http://owner")
