from unittest.mock import AsyncMock

import pytest
from mcp import Client

from backend import mcp_server


@pytest.mark.asyncio
async def test_modern_protocol_discovers_structured_project_tools():
    async with Client(mcp_server.mcp, mode=mcp_server.MCP_PROTOCOL_VERSION) as client:
        protocol_version = client.protocol_version
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert protocol_version == "2026-07-28"
    assert result.result_type == "complete"
    assert result.cache_scope == "private"
    assert {
        "list_workflow_node_capabilities",
        "draft_workflow_from_intent",
        "preview_workflow_node_patch",
        "compile_workflow_draft",
        "list_project_workflows",
        "run_published_workflow",
        "get_project_runtime_summary",
        "list_project_runtime_logs",
        "get_project_runtime_trace",
    } <= tools.keys()
    assert tools["run_published_workflow"].output_schema["type"] == "object"
    assert tools["run_published_workflow"].annotations.idempotent_hint is True
    assert tools["draft_workflow_from_intent"].annotations.read_only_hint is True
    assert tools["compile_workflow_draft"].annotations.read_only_hint is True
    assert tools["list_project_runtime_logs"].annotations.read_only_hint is True


@pytest.mark.asyncio
async def test_published_workflow_tool_reuses_real_project_run_endpoint(monkeypatch):
    request = AsyncMock(return_value={"success": True, "data": {"runId": "run-1"}})
    monkeypatch.setattr(mcp_server, "_request", request)

    result = await mcp_server.run_published_workflow(
        "workspace-1",
        "project-1",
        "workflow-1",
        {"topic": "MCP"},
        "retry-1",
        "agent-1",
    )

    assert result["data"]["runId"] == "run-1"
    request.assert_awaited_once_with(
        "POST",
        "/api/v1/workspaces/workspace-1/projects/project-1/workflows/workflow-1/runs",
        headers={"Idempotency-Key": "retry-1"},
        json={
            "inputs": {"topic": "MCP"},
            "response_mode": "async",
            "user": "agent-1",
        },
    )


@pytest.mark.asyncio
async def test_agent_workflow_tools_reuse_stateless_rest_contracts(monkeypatch):
    request = AsyncMock(return_value={"success": True, "data": {"valid": True}})
    monkeypatch.setattr(mcp_server, "_request", request)

    await mcp_server.list_workflow_node_capabilities()
    request.assert_awaited_with("GET", "/api/v1/workflows/capabilities")

    request.reset_mock()
    drafted = await mcp_server.draft_workflow_from_intent("抓小红书热帖")
    assert drafted["data"]["valid"] is True
    _, kwargs = request.await_args
    assert request.await_args.args[:2] == ("POST", "/api/v1/workflows/demand-draft")
    assert kwargs["json"]["text"] == "抓小红书热帖"
    seed = kwargs["json"]["project"]
    assert seed["nodes"][0]["ui"]["catalogId"] == "intelligence.input.collection-need"
    assert seed["agentPermissions"]["canMutateExternalSites"] is False

    request.reset_mock()
    await mcp_server.preview_workflow_node_patch(seed, [{"op": "add_node"}])
    request.assert_awaited_once_with(
        "POST",
        "/api/v1/workflows/patch",
        json={"project": seed, "operations": [{"op": "add_node"}]},
    )

    request.reset_mock()
    await mcp_server.compile_workflow_draft(seed)
    request.assert_awaited_once_with(
        "POST",
        "/api/v1/workflows/compile",
        json={"project": seed},
    )
