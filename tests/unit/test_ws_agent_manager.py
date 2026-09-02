"""Unit tests for backend/ws_agent_manager.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.ws_agent_manager as mgr


@pytest.fixture(autouse=True)
def clear_state():
    """Ensure module-level dicts are clean before each test."""
    mgr._connections.clear()
    mgr._pending.clear()
    mgr._pending_agent_tasks.clear()
    mgr._agent_task_callbacks.clear()
    yield
    mgr._connections.clear()
    mgr._pending.clear()
    mgr._pending_agent_tasks.clear()
    mgr._agent_task_callbacks.clear()


# ── register / unregister / queries ───────────────────────────────────────────

def test_register_and_is_connected():
    ws = MagicMock()
    mgr.register_connection("http://agent:19823", ws)
    assert mgr.is_connected("http://agent:19823") is True


def test_unregister_removes_connection():
    ws = MagicMock()
    mgr.register_connection("http://agent:19823", ws)
    mgr.unregister_connection("http://agent:19823")
    assert mgr.is_connected("http://agent:19823") is False


def test_unregister_nonexistent_no_error():
    mgr.unregister_connection("http://nonexistent:19823")  # must not raise


def test_list_connected_returns_urls():
    mgr.register_connection("http://a:1", MagicMock())
    mgr.register_connection("http://b:2", MagicMock())
    connected = mgr.list_connected()
    assert "http://a:1" in connected
    assert "http://b:2" in connected
    assert len(connected) == 2


def test_is_connected_false_for_unknown():
    assert mgr.is_connected("http://nobody:19823") is False


# ── dispatch_collect: not connected ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_collect_raises_when_not_connected():
    with pytest.raises(RuntimeError, match="No active WS connection"):
        await mgr.dispatch_collect("http://missing:19823", "site", "cmd", {}, [], "json", "bridge")


# ── dispatch_collect: success ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_collect_success():
    """dispatch_collect sends JSON to WS and resolves the future when result arrives."""
    ws = AsyncMock()
    mgr.register_connection("http://agent:19823", ws)

    result_payload = {"success": True, "items": [{"id": 1}], "error": None}

    async def fake_send_json(payload):
        # Simulate agent responding right away
        request_id = payload["request_id"]
        asyncio.get_running_loop().call_soon(
            mgr.resolve_response, request_id, result_payload
        )

    ws.send_json = AsyncMock(side_effect=fake_send_json)

    result = await mgr.dispatch_collect(
        "http://agent:19823", "bilibili", "hot", {}, [], "json", "bridge", timeout=5.0
    )

    assert result["success"] is True
    assert result["items"] == [{"id": 1}]
    ws.send_json.assert_awaited_once()
    sent = ws.send_json.call_args[0][0]
    assert sent["type"] == "collect"
    assert sent["site"] == "bilibili"
    assert sent["command"] == "hot"


# ── dispatch_collect: timeout ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_collect_timeout():
    """dispatch_collect raises TimeoutError when agent does not respond."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()  # send succeeds but no resolve comes back
    mgr.register_connection("http://agent:19823", ws)

    with pytest.raises(TimeoutError, match="did not respond"):
        await mgr.dispatch_collect(
            "http://agent:19823", "site", "cmd", {}, [], "json", "bridge", timeout=0.05
        )


# ── dispatch_collect: pending cleaned up on timeout ──────────────────────────

@pytest.mark.asyncio
async def test_dispatch_collect_pending_cleaned_up_on_timeout():
    """After a timeout, the pending future is removed from _pending."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    mgr.register_connection("http://agent:19823", ws)

    with pytest.raises(TimeoutError):
        await mgr.dispatch_collect(
            "http://agent:19823", "s", "c", {}, [], "json", "bridge", timeout=0.05
        )

    assert len(mgr._pending) == 0


# ── resolve_response: unknown request_id ──────────────────────────────────────

def test_resolve_response_unknown_request_id_no_error():
    """resolve_response with unknown request_id must not raise."""
    mgr.resolve_response("nonexistent-id", {"success": True, "items": []})


# ── resolve_response: already-done future ─────────────────────────────────────

def test_resolve_response_already_done_future_no_error():
    """resolve_response on a future that's already resolved must not raise."""
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    fut.set_result({"done": True})
    mgr._pending["req-done"] = fut
    # Should log a warning but not raise
    mgr.resolve_response("req-done", {"success": True})
    loop.close()


# ── send_agent_task: not connected ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_agent_task_raises_when_not_connected():
    with pytest.raises(RuntimeError, match="No active WS connection"):
        await mgr.send_agent_task("http://missing:19823", {"runtime": "pi"}, lambda e: None)


# ── send_agent_task: happy path, N events then result (sync on_event) ──────

@pytest.mark.asyncio
async def test_send_agent_task_happy_path_sync_on_event_order_preserved():
    ws = AsyncMock()
    mgr.register_connection("http://agent:19823", ws)

    received_events = []
    sent_frames = []

    async def fake_send(payload):
        sent_frames.append(payload)
        if payload["type"] != "agent_task":
            return
        request_id = payload["request_id"]

        async def drive_events():
            await mgr.resolve_agent_event(
                request_id, {"event": {"type": "started", "task_id": request_id}}
            )
            await mgr.resolve_agent_event(
                request_id, {"event": {"type": "text", "task_id": request_id, "text": "hi"}}
            )
            mgr.resolve_agent_result(
                request_id, {"result": {"type": "done", "task_id": request_id, "result": {}}}
            )

        asyncio.ensure_future(drive_events())

    ws.send_json = AsyncMock(side_effect=fake_send)

    def on_event(event):
        received_events.append(event)

    result = await mgr.send_agent_task(
        "http://agent:19823", {"runtime": "pi", "workflow": "w"}, on_event, timeout=5.0
    )

    assert result["type"] == "done"
    assert [e["type"] for e in received_events] == ["started", "text"]
    assert sent_frames[0]["type"] == "agent_task"
    assert sent_frames[0]["runtime"] == "pi"
    assert "request_id" in sent_frames[0]


# ── send_agent_task: happy path with async on_event ─────────────────────────

@pytest.mark.asyncio
async def test_send_agent_task_supports_async_on_event():
    ws = AsyncMock()
    mgr.register_connection("http://agent:19823", ws)

    received_events = []

    async def on_event(event):
        received_events.append(event)

    async def fake_send(payload):
        if payload["type"] == "agent_task":
            request_id = payload["request_id"]
            await mgr.resolve_agent_event(request_id, {"event": {"type": "started", "task_id": request_id}})
            mgr.resolve_agent_result(
                request_id, {"result": {"type": "done", "task_id": request_id, "result": {"ok": True}}}
            )

    ws.send_json = AsyncMock(side_effect=fake_send)

    result = await mgr.send_agent_task(
        "http://agent:19823", {"runtime": "pi"}, on_event, timeout=5.0
    )

    assert result["result"] == {"ok": True}
    assert len(received_events) == 1
    assert received_events[0]["type"] == "started"


# ── send_agent_task: timeout ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_agent_task_timeout():
    ws = AsyncMock()
    ws.send_json = AsyncMock()  # send succeeds, but no agent_result ever arrives
    mgr.register_connection("http://agent:19823", ws)

    with pytest.raises(TimeoutError, match="did not complete agent_task"):
        await mgr.send_agent_task("http://agent:19823", {"runtime": "pi"}, lambda e: None, timeout=0.05)

    sent_frames = [call.args[0] for call in ws.send_json.await_args_list]
    assert [frame["type"] for frame in sent_frames] == ["agent_task", "cancel"]
    assert sent_frames[1]["request_id"] == sent_frames[0]["request_id"]
    # Bookkeeping cleaned up after timeout.
    assert len(mgr._pending_agent_tasks) == 0
    assert len(mgr._agent_task_callbacks) == 0


# ── send_agent_task: disconnect fails pending fast ──────────────────────────

@pytest.mark.asyncio
async def test_unregister_connection_fails_pending_agent_tasks_fast():
    ws = AsyncMock()
    ws.send_json = AsyncMock()  # send succeeds, agent then disconnects with no result
    mgr.register_connection("http://agent:19823", ws)

    async def disconnect_soon():
        await asyncio.sleep(0.01)
        mgr.unregister_connection("http://agent:19823")

    disconnect_task = asyncio.ensure_future(disconnect_soon())
    result = await mgr.send_agent_task(
        "http://agent:19823", {"runtime": "pi"}, lambda e: None, timeout=5.0
    )
    await disconnect_task

    assert result["type"] == "error"
    assert result["error_type"] == "AgentDisconnected"


@pytest.mark.asyncio
async def test_unregister_connection_leaves_other_agents_pending_tasks_alone():
    ws_a = AsyncMock()
    ws_b = AsyncMock()
    mgr.register_connection("http://agent-a:1", ws_a)
    mgr.register_connection("http://agent-b:2", ws_b)

    ws_b.send_json = AsyncMock()  # never resolves — held pending deliberately
    task_b = asyncio.ensure_future(
        mgr.send_agent_task("http://agent-b:2", {"runtime": "pi"}, lambda e: None, timeout=5.0)
    )
    await asyncio.sleep(0.01)  # let task_b register its pending future

    mgr.unregister_connection("http://agent-a:1")

    assert not task_b.done()
    task_b.cancel()
    try:
        await task_b
    except asyncio.CancelledError:
        pass


def test_unregister_connection_no_pending_agent_tasks_no_error():
    mgr.unregister_connection("http://nonexistent:19823")  # must not raise


# ── resolve_agent_event / resolve_agent_result: unknown request_id ─────────

@pytest.mark.asyncio
async def test_resolve_agent_event_unknown_request_id_no_error():
    await mgr.resolve_agent_event("nonexistent-id", {"event": {"type": "text"}})


def test_resolve_agent_result_unknown_request_id_no_error():
    mgr.resolve_agent_result("nonexistent-id", {"result": {"type": "done"}})


def test_resolve_agent_result_already_done_future_no_error():
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    fut.set_result({"type": "done"})
    mgr._pending_agent_tasks["req-done"] = fut
    mgr.resolve_agent_result("req-done", {"result": {"type": "done"}})
    loop.close()


@pytest.mark.asyncio
async def test_resolve_agent_event_callback_exception_fails_pending_task():
    def bad_on_event(event):
        raise ValueError("boom")

    fut = asyncio.get_running_loop().create_future()
    mgr._pending_agent_tasks["req-1"] = fut
    mgr._agent_task_callbacks["req-1"] = (bad_on_event, "http://agent:1")
    await mgr.resolve_agent_event("req-1", {"event": {"type": "text"}})

    with pytest.raises(ValueError, match="boom"):
        await fut
