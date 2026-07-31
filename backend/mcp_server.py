"""First-party MCP surface for OpenCLI Admin.

The same server is available over stdio (``opencli-mcp``) and as the built-in
``/mcp`` Streamable HTTP endpoint mounted by :mod:`backend.main`.  The official
MCP v2 SDK supplies the 2026-07-28 stateless protocol, ``server/discover``,
cache metadata, and legacy protocol translation.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from backend.config import get_settings

API_BASE_URL = os.environ.get("OPENCLI_ADMIN_API_URL", "http://localhost:8031").rstrip("/")
MCP_PROTOCOL_VERSION = "2026-07-28"

READ_ONLY_TOOL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
WRITE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
IDEMPOTENT_WRITE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def _auth_headers() -> dict[str, str]:
    token = get_settings().api_auth_token
    return {"Authorization": f"Bearer {token}"} if token else {}


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


def _transport_security() -> TransportSecuritySettings:
    """Keep DNS-rebinding protection on while allowing the configured public URL."""

    allowed_hosts = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "api:*",
    ]
    allowed_origins = [
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://localhost",
        "http://localhost:*",
        "http://[::1]",
        "http://[::1]:*",
    ]
    public_url = get_settings().public_url.strip()
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.netloc:
            allowed_hosts.append(parsed.netloc)
            allowed_origins.append(f"{parsed.scheme}://{parsed.netloc}")
    allowed_hosts.extend(_csv_env("OPENCLI_MCP_ALLOWED_HOSTS"))
    allowed_origins.extend(_csv_env("OPENCLI_MCP_ALLOWED_ORIGINS"))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )


mcp = MCPServer(
    "opencli-admin",
    version="0.4.0",
    instructions=(
        "Use project tools for immutable published workflow runs and their durable traces. "
        "Use source tools for collection administration."
    ),
)


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Call the REST API and normalize HTTP/network failures for tool callers."""

    try:
        async with httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=30.0,
            headers=_auth_headers(),
        ) as client:
            response = await client.request(method, path, **kwargs)
            try:
                body = response.json()
            except ValueError:
                if response.status_code >= 400:
                    return {"success": False, "error": response.text}
                response.raise_for_status()
                raise
            if response.status_code >= 400:
                error = body.get("detail") or body.get("error") or response.text
                return {"success": False, "error": error}
            return body
    except httpx.HTTPError as exc:
        return {
            "success": False,
            "error": f"request to {API_BASE_URL}{path} failed: {exc}",
        }


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def list_sources(
    enabled: bool | None = None,
    channel_type: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """List configured data sources, optionally filtered by state or channel type."""

    params: dict[str, Any] = {"page": page, "limit": limit}
    if enabled is not None:
        params["enabled"] = enabled
    if channel_type is not None:
        params["channel_type"] = channel_type
    return await _request("GET", "/api/v1/sources", params=params)


@mcp.tool(annotations=WRITE_TOOL, structured_output=True)
async def create_source(
    name: str,
    channel_type: str,
    channel_config: dict[str, Any],
    description: str | None = None,
    enabled: bool = True,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create an OpenCLI data source."""

    return await _request(
        "POST",
        "/api/v1/sources",
        json={
            "name": name,
            "channel_type": channel_type,
            "channel_config": channel_config,
            "description": description,
            "enabled": enabled,
            "tags": tags or [],
        },
    )


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def test_source(source_id: str) -> dict[str, Any]:
    """Dry-run source connectivity without storing collected records."""

    return await _request("POST", f"/api/v1/sources/{source_id}/test")


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def discover_feed(url: str) -> dict[str, Any]:
    """Find RSS/Atom feed candidates for a website."""

    return await _request("POST", "/api/v1/sources/discover-feed", json={"url": url})


@mcp.tool(annotations=WRITE_TOOL, structured_output=True)
async def trigger_task(
    source_id: str,
    parameters: dict[str, Any] | None = None,
    priority: int = 5,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch a collection run and return its task identifier."""

    return await _request(
        "POST",
        "/api/v1/tasks/trigger",
        json={
            "source_id": source_id,
            "parameters": parameters or {},
            "priority": priority,
            "agent_id": agent_id,
        },
    )


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def get_task(task_id: str) -> dict[str, Any]:
    """Read a collection task's durable status."""

    return await _request("GET", f"/api/v1/tasks/{task_id}")


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def list_records(
    source_id: str | None = None,
    task_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """Query collected records."""

    params: dict[str, Any] = {"page": page, "limit": limit}
    if source_id is not None:
        params["source_id"] = source_id
    if task_id is not None:
        params["task_id"] = task_id
    if status is not None:
        params["status"] = status
    if search is not None:
        params["search"] = search
    return await _request("GET", "/api/v1/records", params=params)


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def list_project_workflows(workspace_id: str, project_id: str) -> dict[str, Any]:
    """List a project's workflows and current published versions."""

    return await _request(
        "GET",
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows",
    )


@mcp.tool(annotations=IDEMPOTENT_WRITE_TOOL, structured_output=True)
async def run_published_workflow(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    inputs: dict[str, Any],
    idempotency_key: str,
    user: str = "mcp-client",
) -> dict[str, Any]:
    """Run the current immutable published version with an explicit retry key."""

    return await _request(
        "POST",
        (
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/workflows/{workflow_id}/runs"
        ),
        headers={"Idempotency-Key": idempotency_key},
        json={"inputs": inputs, "response_mode": "async", "user": user},
    )


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def get_project_runtime_summary(workspace_id: str, project_id: str) -> dict[str, Any]:
    """Read project-level run counts and recent activity."""

    return await _request(
        "GET",
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/runtime-summary",
    )


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def list_project_runtime_logs(
    workspace_id: str,
    project_id: str,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """List durable project workflow runs."""

    params: dict[str, Any] = {"page": page, "limit": limit}
    if status is not None:
        params["status"] = status
    if search is not None:
        params["search"] = search
    return await _request(
        "GET",
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/runtime-logs",
        params=params,
    )


@mcp.tool(annotations=READ_ONLY_TOOL, structured_output=True)
async def get_project_runtime_trace(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    after_sequence: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Read one project-owned run's projection, checkpoint, and events."""

    params: dict[str, Any] = {}
    if after_sequence is not None:
        params["afterSequence"] = after_sequence
    if limit is not None:
        params["limit"] = limit
    return await _request(
        "GET",
        (
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/workflows/{workflow_id}/runs/{run_id}/trace"
        ),
        params=params,
    )


mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security(),
)


def main() -> None:
    transport = os.environ.get("OPENCLI_MCP_TRANSPORT", "stdio")
    try:
        if transport == "streamable-http":
            mcp.run(
                transport="streamable-http",
                host=os.environ.get("OPENCLI_MCP_HOST", "127.0.0.1"),
                port=int(os.environ.get("OPENCLI_MCP_PORT", "8765")),
                streamable_http_path="/mcp",
                json_response=True,
                stateless_http=True,
                transport_security=_transport_security(),
            )
            return
        if transport == "stdio":
            mcp.run(transport="stdio")
            return
        raise ValueError("OPENCLI_MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
