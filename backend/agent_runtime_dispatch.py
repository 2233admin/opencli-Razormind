"""Browser helpers and structured runtime dispatch for the edge agent."""

import asyncio
import csv
import io
import json
import logging
from typing import Any

import httpx
import yaml
from fastapi import HTTPException
from pydantic import BaseModel

from backend.agent_runtimes.base import AgentTask, RuntimeInvocationError
from backend.agent_runtimes.registry import get_runtime

logger = logging.getLogger(__name__)


class RuntimeInvokeRequest(BaseModel):
    """Structured runtime action forwarded only from an allowlisted bundle."""

    runtime: str
    workflow: str
    instructions: str
    input: dict[str, Any] = {}
    config: dict[str, Any] = {}


async def snapshot_tab_ids(cdp_endpoint: str) -> set[str]:
    """Return the set of tab IDs currently open in Chrome."""

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{cdp_endpoint}/json/list")
            return {target["id"] for target in resp.json() if "id" in target}
    except Exception:
        return set()


async def cleanup_cdp_tabs(cdp_endpoint: str, pre_existing_ids: set[str]) -> None:
    """Close tabs opened during collection while preserving existing tabs."""

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{cdp_endpoint}/json/list")
            tabs = resp.json()
            remaining_pages = sum(1 for target in tabs if target.get("type") == "page")
            for tab in tabs:
                tab_id = tab.get("id", "")
                if tab.get("type") == "page" and tab_id not in pre_existing_ids:
                    try:
                        await client.get(f"{cdp_endpoint}/json/close/{tab_id}")
                        logger.info(
                            "cleanup: closed new tab %s url=%s",
                            tab_id,
                            tab.get("url", "")[:80],
                        )
                        remaining_pages -= 1
                    except Exception:
                        pass
            if remaining_pages == 0:
                try:
                    await client.put(f"{cdp_endpoint}/json/new")
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("cleanup: could not close CDP tabs at %s: %s", cdp_endpoint, exc)


def parse_output(raw: str, fmt: str) -> list[dict]:
    if fmt == "json":
        start = next((index for index, char in enumerate(raw) if char in "{["), None)
        if start is None:
            raise ValueError(f"No JSON found: {raw[:200]!r}")
        data = json.loads(raw[start:])
        return data if isinstance(data, list) else [data]
    if fmt == "yaml":
        data = yaml.safe_load(raw)
        if isinstance(data, list):
            return data
        return [data] if isinstance(data, dict) else [{"content": str(data)}]
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(raw.strip())))
    return [{"content": raw}]


async def _evaluate_cdp_target(websocket_url: str, expression: str) -> Any:
    """Evaluate one expression in an existing CDP target and return its value."""
    import websockets

    async with websockets.connect(websocket_url, open_timeout=5) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=30))
            if message.get("id") != 1:
                continue
            if "error" in message or message.get("result", {}).get("exceptionDetails"):
                raise RuntimeError(
                    message.get("error")
                    or message["result"]["exceptionDetails"].get("text", "CDP evaluation failed")
                )
            return message.get("result", {}).get("result", {}).get("value")


def _script_host_action(req: RuntimeInvokeRequest) -> tuple[str, str]:
    pack = req.config.get("pack")
    action = req.config.get("action") or req.workflow
    if not isinstance(pack, str) or not pack or not isinstance(action, str) or not action:
        raise HTTPException(
            status_code=400,
            detail="script-host capabilities require config.pack and an action",
        )
    return pack, action


async def invoke_script_host(req: RuntimeInvokeRequest, *, cdp_endpoint: str) -> dict:
    """Invoke only an action present in the packaged Script Host registry."""

    pack, action = _script_host_action(req)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{cdp_endpoint.rstrip('/')}/json/list")
            response.raise_for_status()
            targets = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Chrome CDP unavailable: {exc}") from exc

    for target in targets:
        websocket_url = target.get("webSocketDebuggerUrl")
        target_url = target.get("url", "")
        is_script_host_target = target.get("type") == "service_worker" or (
            target.get("type") == "page" and target_url.endswith("/host.html")
        )
        if (
            not is_script_host_target
            or not isinstance(websocket_url, str)
            or not target_url.startswith("chrome-extension://")
        ):
            continue
        try:
            manifest_name = await _evaluate_cdp_target(
                websocket_url, "chrome.runtime.getManifest().name"
            )
        except Exception:
            continue
        if manifest_name != "OpenCLI Script Host":
            continue
        invocation = {
            "pack": pack,
            "action": action,
            "args": req.input,
            "tabId": req.config.get("tab_id"),
        }
        expression = (
            f"globalThis.opencliScriptHost.invoke({json.dumps(invocation, separators=(',', ':'))})"
        )
        try:
            result = await _evaluate_cdp_target(websocket_url, expression)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Script Host invocation failed: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise HTTPException(status_code=502, detail="Script Host returned a non-object result")
        return result
    raise HTTPException(status_code=503, detail="OpenCLI Script Host target is unavailable")


async def invoke_runtime(
    request_id: str,
    req: RuntimeInvokeRequest,
    *,
    cdp_endpoint: str,
) -> dict:
    if req.runtime == "codex":
        raise HTTPException(
            status_code=403,
            detail="Codex runtime is only available through controller WS dispatch",
        )
    if req.runtime == "script-host":
        return await invoke_script_host(req, cdp_endpoint=cdp_endpoint)
    try:
        adapter = get_runtime(req.runtime)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unsupported runtime: {req.runtime}") from exc
    task = AgentTask(
        task_id=request_id,
        workflow=req.workflow,
        instructions=req.instructions,
        input=req.input,
        config=req.config,
    )
    config_errors = adapter.validate_config(task.config)
    if config_errors:
        raise HTTPException(status_code=400, detail="; ".join(config_errors))
    terminal_event: dict | None = None
    async for event in adapter.invoke(task):
        if not isinstance(event, dict):
            raise HTTPException(status_code=502, detail="runtime produced an invalid event")
        if terminal_event is not None:
            raise HTTPException(
                status_code=502,
                detail="runtime produced events after its terminal event",
            )
        if event.get("type") in {"done", "error"}:
            terminal_event = event
    if terminal_event is None:
        raise HTTPException(
            status_code=502,
            detail=f"runtime {req.runtime!r} produced no terminal event",
        )
    return terminal_event
