"""Center-side manager for reverse WebSocket connections from edge agents.

When an edge agent cannot be reached by the center (NAT, firewall), it initiates
a persistent WebSocket connection to the center instead. The center dispatches
tasks by sending JSON messages down this connection and awaiting results.

Two independent request/response families share the same connection:

- ``collect`` / ``result`` — single-shot opencli collection tasks (unchanged).
- ``agent_task`` / ``agent_event`` (0..N) / ``agent_result`` — streaming
  agent-runtime task dispatch.

Wire protocol — every reverse-channel message type, one-line field shapes:

  register      agent→center  {"type": "register", "agent_url": str,
                                "mode": "bridge"|"cdp", "node_type"?: str,
                                "label"?: str, "runtimes"?: list[str]}
  registered    center→agent  {"type": "registered", "agent_url": str}
  collect       center→agent  {"type": "collect", "request_id": uuid,
                                "site": str, "command": str, "args": dict,
                                "positional_args": list, "format": str, "mode": str}
  result        agent→center  {"type": "result", "request_id": uuid,
                                "success": bool, "items": list, "error": str|None}
  ping          either→other  {"type": "ping"}
  pong          either→other  {"type": "pong"}
  agent_task    center→agent  {"type": "agent_task", "request_id": uuid,
                                "runtime": str, "workflow": str, "input": dict,
                                "config": dict, "session_id": str|None}
  agent_event   agent→center  {"type": "agent_event", "request_id": uuid,
                                "event": dict}
                                # one RuntimeEvent; 0..N per task
  agent_result  agent→center  {"type": "agent_result", "request_id": uuid,
                                "result": dict}
                                # terminal done/error RuntimeEvent; exactly 1
  cancel        center→agent  {"type": "cancel", "request_id": uuid}
                                # stops collect or agent_task with the same id

Protocol (collect/result path):
  1. Agent connects to  ws(s)://{center}/api/v1/browsers/agents/ws
  2. Agent → center:  {"type": "register", "agent_url": "...", "mode": "bridge", "label": "..."}
  3. Center → agent:  {"type": "registered", "agent_url": "..."}
  4. Center → agent:  {"type": "collect", "request_id": "<uuid>", "site": "...", ...}
  5. Agent → center:  {"type": "result", "request_id": "<uuid>", "success": true, "items": [...]}
  6. Either side:      {"type": "ping"} / {"type": "pong"}

Protocol (agent_task streaming path):
  1-3. Same registration handshake as above (registration may additionally
       carry ``runtimes`` — the agent-runtime types available on this node).
  4. Center → agent:  {"type": "agent_task", "request_id": "<uuid>", "runtime": "pi", ...}
  5. Agent → center:  0..N  {"type": "agent_event", "request_id": "<uuid>", "event": {...}}
  6. Agent → center:  exactly 1  {"type": "agent_result", "request_id": "<uuid>", "result": {...}}
"""

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# agent_url → active WebSocket connection
_connections: dict[str, WebSocket] = {}

# request_id → Future awaiting agent result (collect/result path)
_pending: dict[str, asyncio.Future] = {}
_collect_owners: dict[str, WebSocket] = {}
_task_owners: dict[str, WebSocket] = {}

# request_id → Future awaiting the terminal agent_result (agent_task path)
_pending_agent_tasks: dict[str, asyncio.Future] = {}

# request_id → (on_event callback, owning agent_url) for streaming agent_event dispatch
_agent_task_callbacks: dict[str, tuple[Callable[[dict[str, Any]], Any], str]] = {}


def register_connection(agent_url: str, ws: WebSocket) -> None:
    """Record a newly-established WS connection for agent_url."""
    previous = _connections.get(agent_url)
    if previous is not None and previous is not ws:
        unregister_connection(agent_url, previous)
        asyncio.get_running_loop().create_task(previous.close(code=1012))
    _connections[agent_url] = ws
    logger.info("WS agent connected: %s (total=%d)", agent_url, len(_connections))


def unregister_connection(agent_url: str, source_ws: WebSocket | None = None) -> bool:
    """Fail this transport's pending work without unregistering a replacement."""
    current = _connections.get(agent_url)
    if current is None or (source_ws is not None and current is not source_ws):
        return False
    for request_id, owner in list(_collect_owners.items()):
        if owner is current:
            future = _pending.get(request_id)
            if future is not None and not future.done():
                future.set_exception(RuntimeError("Agent disconnected before collection completed"))
    _connections.pop(agent_url, None)
    logger.info("WS agent disconnected: %s (remaining=%d)", agent_url, len(_connections))

    dead_request_ids = [
        request_id
        for request_id, (_, owner) in _agent_task_callbacks.items()
        if owner == agent_url
    ]
    for request_id in dead_request_ids:
        fut = _pending_agent_tasks.get(request_id)
        if fut is not None and not fut.done():
            fut.set_result({
                "type": "error",
                "task_id": request_id,
                "message": f"WS agent {agent_url!r} disconnected before task completed",
                "error_type": "AgentDisconnected",
            })
        _agent_task_callbacks.pop(request_id, None)
    return True


def is_connected(agent_url: str) -> bool:
    return agent_url in _connections


def list_connected() -> list[str]:
    return list(_connections.keys())


async def dispatch_collect(
    agent_url: str,
    site: str,
    command: str,
    args: dict[str, Any],
    positional_args: list[str],
    output_format: str,
    mode: str,
    timeout: float | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Send a collect task to a WS agent and await the result dict.

    Raises:
        RuntimeError: agent is not connected.
        TimeoutError: agent did not respond within *timeout* seconds.
    """
    if timeout is None:
        from backend.config import get_settings
        timeout = float(get_settings().agent_ws_timeout)

    ws = _connections.get(agent_url)
    if ws is None:
        raise RuntimeError(f"No active WS connection for agent: {agent_url}")

    request_id = request_id or str(uuid.uuid4())
    if request_id in _pending or request_id in _pending_agent_tasks:
        raise ValueError("request_id is already active")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict] = loop.create_future()
    _pending[request_id] = fut
    _collect_owners[request_id] = ws

    try:
        await ws.send_json({
            "type": "collect",
            "request_id": request_id,
            "site": site,
            "command": command,
            "args": args,
            "positional_args": positional_args,
            "format": output_format,
            "mode": mode,
        })
        logger.debug("WS dispatch | agent=%s request_id=%s site=%s cmd=%s",
                     agent_url, request_id, site, command)
        return await asyncio.wait_for(fut, timeout=timeout)
    except TimeoutError:
        raise TimeoutError(f"WS agent {agent_url!r} did not respond in {timeout}s")
    except asyncio.CancelledError:
        await ws.send_json({"type": "cancel", "request_id": request_id})
        raise
    finally:
        _pending.pop(request_id, None)
        _collect_owners.pop(request_id, None)


def resolve_response(
    request_id: str, result: dict[str, Any], source_ws: WebSocket | None = None
) -> None:
    """Called from the WS receive loop when an agent returns a 'result' message."""
    fut = _pending.get(request_id)
    if source_ws is not None and _collect_owners.get(request_id) is not source_ws:
        return
    if fut is None or fut.done():
        logger.warning("WS: unexpected result for request_id=%s (no waiting future)", request_id)
        return
    fut.set_result(result)


# ── Streaming agent-task dispatch ───────────────────────────────────────────
# Alongside the collect/result single-shot path above: agent_task/agent_event/
# agent_result support a long-running streaming task with N intermediate
# events before the terminal result.


async def send_agent_task(
    agent_url: str,
    task: dict[str, Any],
    on_event: Callable[[dict[str, Any]], Any],
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Send an agent_task to a WS agent, streaming events to *on_event* as
    they arrive, and return the terminal result dict once received.

    *on_event* is called once per ``agent_event`` frame with that frame's
    ``event`` payload. It may be a plain sync callable or an async callable
    (coroutine function) — both are supported, matching the flexibility the
    edge side (adapters) already assumes for callers.

    Raises:
        RuntimeError: agent is not connected.
        TimeoutError: agent did not respond within *timeout* seconds. Pending
            bookkeeping (the callback registration) is cleaned up either way.
    """
    ws = _connections.get(agent_url)
    if ws is None:
        raise RuntimeError(f"No active WS connection for agent: {agent_url}")

    request_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict] = loop.create_future()
    _pending_agent_tasks[request_id] = fut
    _task_owners[request_id] = ws
    _agent_task_callbacks[request_id] = (on_event, agent_url)

    try:
        await ws.send_json({"type": "agent_task", "request_id": request_id, **task})
        logger.debug("WS agent_task dispatch | agent=%s request_id=%s runtime=%s",
                     agent_url, request_id, task.get("runtime"))
        return await asyncio.wait_for(fut, timeout=timeout)
    except TimeoutError:
        await _cancel_agent_task(ws, request_id)
        raise TimeoutError(f"WS agent {agent_url!r} did not complete agent_task in {timeout}s")
    except asyncio.CancelledError:
        await _cancel_agent_task(ws, request_id)
        raise
    finally:
        _pending_agent_tasks.pop(request_id, None)
        _task_owners.pop(request_id, None)
        _agent_task_callbacks.pop(request_id, None)


async def _cancel_agent_task(ws: WebSocket, request_id: str) -> None:
    try:
        await ws.send_json({"type": "cancel", "request_id": request_id})
    except Exception:
        logger.warning("WS: failed to cancel agent_task request_id=%s", request_id, exc_info=True)


async def _invoke_on_event(
    on_event: Callable[[dict[str, Any]], Any],
    event: dict[str, Any],
) -> None:
    """Call *on_event*, awaiting it if it returned an awaitable (async callable)."""
    result = on_event(event)
    if inspect.isawaitable(result):
        await result


async def resolve_agent_event(
    request_id: str, msg: dict[str, Any], source_ws: WebSocket | None = None
) -> None:
    """Called from the WS receive loop when an agent sends an 'agent_event' frame."""
    entry = _agent_task_callbacks.get(request_id)
    if entry is None:
        logger.warning("WS: unexpected agent_event for request_id=%s (no waiting task)", request_id)
        return
    if source_ws is not None and _task_owners.get(request_id) is not source_ws:
        return
    on_event, _owner = entry
    event = msg.get("event", {})
    try:
        await _invoke_on_event(on_event, event)
    except Exception as exc:
        logger.exception("WS: on_event callback raised for request_id=%s", request_id)
        fut = _pending_agent_tasks.get(request_id)
        if fut is not None and not fut.done():
            fut.set_exception(exc)

def resolve_agent_result(
    request_id: str, msg: dict[str, Any], source_ws: WebSocket | None = None
) -> None:
    """Called from the WS receive loop when an agent sends the terminal 'agent_result' frame."""
    fut = _pending_agent_tasks.get(request_id)
    if source_ws is not None and _task_owners.get(request_id) is not source_ws:
        return
    if fut is None or fut.done():
        logger.warning(
            "WS: unexpected agent_result for request_id=%s (no waiting future)",
            request_id,
        )
        return
    fut.set_result(msg.get("result", {}))
