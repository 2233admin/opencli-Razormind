"""HTTP adapter for OpenTabs' localhost MCP server.

OpenTabs exposes several interfaces; this adapter intentionally targets the
stable, low-context REST surface used by its own CLI:

* ``GET /tools`` lists plugin/browser/platform tools, optionally filtered by
  ``?plugin=...``.
* ``POST /tools/{name}/call`` invokes a tool with ``{"arguments": {...}}``.
* ``GET /health`` checks the server and extension/plugin status.

The MCP streamable HTTP and gateway endpoints remain compatible at the product
boundary, but this runtime adapter keeps our edge-agent contract simple: one
``agent_task`` maps to one OpenTabs tool-list or tool-call request.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from inspect import isawaitable
from typing import Any

import httpx

from backend.agent_runtimes.base import (
    AgentTask,
    RuntimeAdapter,
    RuntimeCapabilities,
    RuntimeInvocationError,
    event_done,
    event_error,
    event_started,
    event_state,
    event_tool_call,
    event_tool_result,
)
from backend.agent_runtimes.registry import register_runtime
from backend.security.url_guard import guarded_async_client

_DEFAULT_BASE_URL = "http://127.0.0.1:9515"
_DEFAULT_TIMEOUT_SECONDS = 60.0

_LIST_WORKFLOWS = {"tool.list", "tools.list", "list_tools", "opentabs_list_tools"}
_CALL_WORKFLOWS = {"tool.call", "tools.call", "call_tool", "opentabs_call"}
_HEALTH_WORKFLOWS = {"health", "server.health", "opentabs_health"}


@register_runtime
class OpenTabsRuntimeAdapter(RuntimeAdapter):
    """Adapter for OpenTabs' authenticated local HTTP server."""

    runtime_type = "opentabs"
    capabilities = RuntimeCapabilities(
        transport="http",
        streaming=False,
        resume_by_id=False,
        checkpoint="none",
        concurrent_sessions=True,
        features=frozenset({"browser", "tool_events"}),
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if "base_url" in config and config["base_url"] is not None:
            base_url = config["base_url"]
            if not isinstance(base_url, str) or not base_url.strip():
                errors.append("'base_url' must be a non-empty string when provided")
        if "secret" in config and config["secret"] is not None:
            secret = config["secret"]
            if not isinstance(secret, str) or not secret:
                errors.append("'secret' must be a non-empty string when provided")
        if "timeout_seconds" in config and config["timeout_seconds"] is not None:
            timeout = config["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                errors.append("'timeout_seconds' must be a positive number when provided")
        return errors

    async def health(self) -> bool:
        config: dict[str, Any] = {}
        try:
            client = await self._resolved_client(config)
            async with client:
                response = await client.get("/health", headers=self._headers(config))
            return response.status_code == 200 and _read_json(response).get("status") == "ok"
        except Exception:
            return False

    @classmethod
    def is_available(cls, binary: str = "opentabs") -> bool:
        return bool(os.environ.get("OPENTABS_BASE_URL")) or shutil.which(binary) is not None

    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        errors = self.validate_config(task.config or {})
        if errors:
            yield event_error(task.task_id, "; ".join(errors), error_type="ValueError")
            return

        yield event_started(task.task_id)

        try:
            if task.workflow in _LIST_WORKFLOWS:
                async for event in self._invoke_list_tools(task):
                    yield event
                return
            if task.workflow in _HEALTH_WORKFLOWS:
                async for event in self._invoke_health(task):
                    yield event
                return

            async for event in self._invoke_call_tool(task):
                yield event
        except RuntimeInvocationError as exc:
            yield event_error(
                task.task_id,
                str(exc),
                error_type=exc.error_type or type(exc).__name__,
            )
        except httpx.TimeoutException as exc:
            yield event_error(
                task.task_id, f"OpenTabs request timed out: {exc}", type(exc).__name__
            )
        except httpx.HTTPError as exc:
            yield event_error(task.task_id, f"OpenTabs request failed: {exc}", type(exc).__name__)
        except Exception as exc:
            yield event_error(task.task_id, f"OpenTabs adapter failed: {exc}", type(exc).__name__)

    async def _invoke_list_tools(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        plugin = _read_optional_string(task.input.get("plugin")) or _read_optional_string(
            task.config.get("plugin")
        )
        args: dict[str, Any] = {}
        if plugin:
            args["plugin"] = plugin

        yield event_tool_call(task.task_id, "opentabs_list_tools", args=args)
        client = await self._resolved_client(task.config)
        async with client:
            response = await client.get(
                "/tools",
                params=({"plugin": plugin} if plugin else None),
                headers=self._headers(task.config),
            )
        tools = _checked_json_response(response, "/tools")
        if not isinstance(tools, list):
            raise RuntimeInvocationError("OpenTabs /tools response was not a list", "ValueError")

        result = {"tools": tools, "plugin": plugin}
        yield event_tool_result(task.task_id, "opentabs_list_tools", result=result)
        yield event_done(task.task_id, result=result)

    async def _invoke_health(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        yield event_tool_call(task.task_id, "opentabs_health", args={})
        client = await self._resolved_client(task.config)
        async with client:
            response = await client.get("/health", headers=self._headers(task.config))
        payload = _checked_json_response(response, "/health")
        if not isinstance(payload, dict):
            raise RuntimeInvocationError(
                "OpenTabs /health response was not an object", "ValueError"
            )
        yield event_state(task.task_id, {"opentabs": payload})
        yield event_done(task.task_id, result={"health": payload})

    async def _invoke_call_tool(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        tool_name, arguments = _tool_call_request(task)
        if not tool_name:
            yield event_error(
                task.task_id,
                'OpenTabs tool call requires input.tool/name or workflow="<tool_name>"',
                error_type="ValueError",
            )
            return

        yield event_tool_call(task.task_id, tool_name, args=arguments)
        client = await self._resolved_client(task.config)
        async with client:
            response = await client.post(
                f"/tools/{tool_name}/call",
                json={"arguments": arguments},
                headers=self._headers(task.config),
            )
        result = _checked_json_response(response, f"/tools/{tool_name}/call")
        if not isinstance(result, dict):
            raise RuntimeInvocationError(
                f"OpenTabs tool {tool_name!r} response was not an object",
                "ValueError",
            )

        is_error = bool(result.get("isError"))
        yield event_tool_result(task.task_id, tool_name, result=result, is_error=is_error)
        if is_error:
            yield event_error(
                task.task_id,
                _tool_error_message(result) or f"OpenTabs tool {tool_name!r} returned isError",
                error_type="OpenTabsToolError",
            )
            return
        yield event_done(task.task_id, result={"tool": tool_name, "result": result})

    async def _client(self, config: dict[str, Any]) -> tuple[httpx.AsyncClient, str]:
        """Build an SSRF-guarded client pinned to OpenTabs' ``base_url``.

        ``base_url`` may come from user/DB-supplied ``config`` (see
        :func:`_base_url`) — a plain ``httpx.AsyncClient(base_url=...)`` here
        would let a hostile ``base_url`` make this adapter attach the
        ``OPENTABS_SECRET``/``config["secret"]`` Bearer header (see
        :meth:`_headers`) to a request against an internal/metadata host.
        ``guarded_async_client`` validates the URL (rejecting private/
        loopback/link-local/metadata targets) and pins the connection to the
        validated IP(s) (closing the DNS-rebinding TOCTOU window) before any
        caller can build headers or send a request — so the secret is never
        sent to an unvalidated host.
        """
        base_url = _base_url(config)
        client, validated_base_url = await guarded_async_client(
            base_url,
            base_url=base_url,
            timeout=_timeout_seconds(config),
            trust_env=False,
        )
        return client, validated_base_url

    async def _resolved_client(self, config: dict[str, Any]) -> httpx.AsyncClient:
        result = self._client(config)
        if isawaitable(result):
            result = await result
        return result[0] if isinstance(result, tuple) else result

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        secret = (
            _read_optional_string(config.get("secret"))
            or os.environ.get("OPENTABS_SECRET", "")
        )
        return {"Authorization": f"Bearer {secret}"} if secret else {}


def _base_url(config: dict[str, Any]) -> str:
    configured = _read_optional_string(config.get("base_url")) or os.environ.get(
        "OPENTABS_BASE_URL"
    )
    return (configured or _DEFAULT_BASE_URL).rstrip("/")


def _timeout_seconds(config: dict[str, Any]) -> float:
    raw = config.get("timeout_seconds")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return _DEFAULT_TIMEOUT_SECONDS


def _checked_json_response(response: httpx.Response, endpoint: str) -> Any:
    payload = _read_json(response)
    if response.status_code >= 400 and payload == {}:
        text = response.text[:500]
        raise RuntimeInvocationError(
            f"OpenTabs {endpoint} failed ({response.status_code}): {text}",
            "HTTPStatusError",
        )
    if response.status_code >= 400 and isinstance(payload, dict) and not payload.get("isError"):
        message = payload.get("error") or payload.get("message") or response.text[:500]
        raise RuntimeInvocationError(
            f"OpenTabs {endpoint} failed ({response.status_code}): {message}",
            "HTTPStatusError",
        )
    return payload


def _read_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _tool_call_request(task: AgentTask) -> tuple[str, dict[str, Any]]:
    payload = task.input if isinstance(task.input, dict) else {}
    workflow = _read_optional_string(task.workflow) or ""
    tool_name = (
        _read_optional_string(payload.get("tool"))
        or _read_optional_string(payload.get("name"))
        or ("" if workflow in _CALL_WORKFLOWS else workflow)
    )
    args = payload.get("arguments", payload.get("args"))
    if isinstance(args, dict):
        return tool_name, dict(args)
    return tool_name, {
        str(key): value
        for key, value in payload.items()
        if key not in {"tool", "name", "arguments", "args"}
    }


def _tool_error_message(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts = [
            str(item.get("text"))
            for item in content
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text") is not None
            )
        ]
        return "".join(parts)
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")
    return str(error) if error else ""


def _read_optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
