"""HTTP-seam tests for AI-authored WorkflowProject patch preview."""

import pytest

from tests.integration.test_workflow_compile_api import _valid_workflow_project


def _fixture_opencli_adapter_catalog() -> tuple[dict, ...]:
    return (
        {
            "site": "bbc",
            "name": "news",
            "description": "BBC news",
            "access": "read",
            "browser": False,
            "strategy": "public",
            "args": [{"name": "limit", "type": "int", "required": False}],
            "columns": ["title", "url"],
        },
        {
            "site": "twitter",
            "name": "search",
            "description": "X search",
            "access": "read",
            "browser": True,
            "strategy": "cookie",
            "args": [
                {
                    "name": "query",
                    "type": "str",
                    "required": True,
                    "positional": True,
                },
                {"name": "limit", "type": "int", "required": False},
            ],
            "columns": ["id", "text"],
        },
        {
            "site": "twitter",
            "name": "post",
            "description": "Post to X",
            "access": "write",
            "browser": True,
            "strategy": "cookie",
            "args": [{"name": "text", "type": "str", "required": True}],
            "columns": ["id", "url"],
        },
    )


def _fixture_opencli_required_arg_catalog() -> tuple[dict, ...]:
    return (
        {
            "site": "example",
            "name": "search",
            "description": "Example search",
            "access": "read",
            "browser": False,
            "strategy": "public",
            "args": [
                {
                    "name": "query",
                    "type": "str",
                    "required": True,
                    "positional": True,
                },
                {"name": "limit", "type": "int", "required": False},
            ],
            "columns": ["id", "text"],
        },
    )


@pytest.mark.asyncio
async def test_patch_adds_existing_node_updates_params_connects_and_compiles(client):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:2]
    project["edges"] = project["edges"][:1]

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "store-inbox",
                        "kind": "inbox",
                        "capability": "store",
                        "params": {"queue": "triage"},
                        "ui": {"catalogId": "intelligence.output.inbox"},
                    },
                },
                {
                    "op": "update_parameters",
                    "nodeId": "normalize-items",
                    "params": {"language": "en-US", "preserveSourceRefs": True},
                },
                {
                    "op": "connect_nodes",
                    "edge": {
                        "id": "e-normalize-store",
                        "source": "normalize-items",
                        "target": "store-inbox",
                    },
                },
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["errors"] == []
    assert data["missing_capabilities"] == []
    assert [operation["op"] for operation in data["patch"]["operations"]] == [
        "add_node",
        "update_parameters",
        "connect_nodes",
    ]
    patched = data["project"]
    assert [node["id"] for node in patched["nodes"]] == [
        "source-jin10",
        "normalize-items",
        "store-inbox",
    ]
    normalize = next(node for node in patched["nodes"] if node["id"] == "normalize-items")
    assert normalize["params"]["language"] == "en-US"
    assert data["compile"]["valid"] is True
    assert data["compile"]["plan"]["runtime"]["node_ids"] == [
        "source-jin10",
        "normalize-items",
        "store-inbox",
    ]


@pytest.mark.asyncio
async def test_patch_rejects_hand_rolled_primitive_and_executor(client):
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "custom-ai-node",
                        "kind": "agent",
                        "capability": "summarize",
                        "params": {"rawOpencliCommand": "opencli collect custom"},
                        "ui": {
                            "primitiveId": "primitive.generated.custom",
                            "executor": {"type": "python"},
                            "missingCapability": "custom.ai.summary",
                        },
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is False
    assert data["project"] is None
    assert data["compile"] is None
    assert {error["code"] for error in data["errors"]} == {
        "forbidden_node_definition",
        "unknown_node_library_binding",
    }


@pytest.mark.asyncio
async def test_patch_rejects_unknown_adapter_after_apply(client):
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "notify-slack",
                        "kind": "notify",
                        "capability": "send",
                        "adapter": "unknown-slack",
                        "params": {"target": "ops"},
                        "ui": {"catalogId": "intelligence.output.webhook"},
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is False
    assert data["project"] is None
    assert data["compile"]["valid"] is False
    errors = [error for error in data["errors"] if error["code"] == "missing_adapter_binding"]
    assert errors
    assert errors[0]["node_id"] == "notify-slack"


@pytest.mark.asyncio
async def test_patch_packages_selected_nodes_into_hda_and_compiles(client):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:2]
    project["edges"] = project["edges"][:1]

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "package_nodes",
                    "internalNodeIds": ["source-jin10", "normalize-items"],
                    "packageNode": {
                        "id": "collection-hda",
                        "kind": "source",
                        "capability": "fetch",
                        "adapter": "jin10-kuaixun",
                        "params": {"template": "collection-pipeline"},
                        "topicCollapse": {
                            "groupId": "collection-hda",
                            "nodeCount": 2,
                            "mode": "locked",
                            "packageInternal": True,
                        },
                        "ui": {"catalogId": "package.collection.pipeline"},
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["errors"] == []
    patched = data["project"]
    assert [node["id"] for node in patched["nodes"]] == ["collection-hda"]
    package_node = patched["nodes"][0]
    assert [node["id"] for node in package_node["internals"]["nodes"]] == [
        "source-jin10",
        "normalize-items",
    ]
    assert [edge["id"] for edge in package_node["internals"]["edges"]] == ["e-source-normalize"]
    assert data["compile"]["plan"]["runtime"]["node_ids"] == [
        "collection-hda",
        "collection-hda::source-jin10",
        "collection-hda::normalize-items",
    ]


@pytest.mark.asyncio
async def test_patch_reports_missing_capability_without_inventing_node(client):
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "request_missing_capability",
                    "capability": "browser.profile.smart-rotate",
                    "reason": "Need cross-machine browser profile fanout.",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert [node["id"] for node in data["project"]["nodes"]] == [
        node["id"] for node in project["nodes"]
    ]
    assert [edge["id"] for edge in data["project"]["edges"]] == [
        edge["id"] for edge in project["edges"]
    ]
    assert data["missing_capabilities"] == [
        {
            "capability": "browser.profile.smart-rotate",
            "reason": "Need cross-machine browser profile fanout.",
            "n8n_search_hint": "browser.profile.smart-rotate",
        }
    ]
    assert data["compile"]["valid"] is True


@pytest.mark.asyncio
async def test_patch_materializes_opencli_read_adapter_node(client, monkeypatch):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_adapter_catalog,
    )
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "materialize_opencli_adapter",
                    "adapterNodeId": "opencli.adapter.bbc.news",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["errors"] == []

    patched = data["project"]
    nodes = {node["id"]: node for node in patched["nodes"]}
    node = nodes["source-bbc-news"]
    assert node["kind"] == "source"
    assert node["capability"] == "fetch"
    assert node["adapter"] == "opencli-bbc"
    assert node["ui"]["catalogId"] == "intelligence.source.opencli-slot"
    assert node["ui"]["adapterNodeId"] == "opencli.adapter.bbc.news"
    assert node["params"]["site"] == "bbc"
    assert node["params"]["command"] == "news"
    assert node["params"]["opencliAdapterNodeId"] == "opencli.adapter.bbc.news"

    adapters = {adapter["id"]: adapter for adapter in patched["adapters"]}
    assert adapters["opencli-bbc"] == {
        "id": "opencli-bbc",
        "type": "source",
        "provider": "opencli",
        "mode": "live",
        "config": {"channel": "opencli"},
    }

    runtime_nodes = {node["id"]: node for node in data["compile"]["plan"]["runtime"]["nodes"]}
    assert runtime_nodes["source-bbc-news"]["runtime"]["binding"]["channel"] == ("opencli")
    assert runtime_nodes["source-bbc-news"]["runtime"]["binding"]["input"] == {
        "site": "bbc",
        "command": "news",
    }


@pytest.mark.asyncio
async def test_patch_materializes_opencli_required_arg_adapter_with_params(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_required_arg_catalog,
    )
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "materialize_opencli_adapter",
                    "adapterNodeId": "opencli.adapter.example.search",
                    "nodeId": "source-x-openai",
                    "params": {"query": "openai", "limit": 1},
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    node = nodes["source-x-openai"]
    assert node["adapter"] == "opencli-example"
    assert node["params"]["positional_args"] == ["openai"]
    assert node["params"]["args"] == {"limit": 1}

    runtime_nodes = {node["id"]: node for node in data["compile"]["plan"]["runtime"]["nodes"]}
    assert runtime_nodes["source-x-openai"]["runtime"]["binding"]["channel"] == ("opencli")


@pytest.mark.asyncio
async def test_patch_materialize_opencli_required_arg_reports_missing_params(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_required_arg_catalog,
    )
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "materialize_opencli_adapter",
                    "adapterNodeId": "opencli.adapter.example.search",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is False
    assert data["project"] is None
    assert data["compile"] is None
    assert {error["code"] for error in data["errors"]} == {"missing_opencli_adapter_params"}
    assert data["missing_capabilities"] == [
        {
            "capability": "opencli.adapter.params",
            "reason": "Missing OpenCLI adapter params: query",
            "n8n_search_hint": "opencli.adapter.example.search",
        }
    ]


@pytest.mark.asyncio
async def test_patch_materializes_opencli_write_adapter_as_guarded_opencli_action(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_adapter_catalog,
    )
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": project,
            "operations": [
                {
                    "op": "materialize_opencli_adapter",
                    "adapterNodeId": "opencli.adapter.twitter.post",
                    "params": {"text": "hello"},
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["errors"] == []

    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    node = nodes["tool-twitter-post"]
    assert node["kind"] == "action"
    assert node["capability"] == "store"
    assert node["adapter"] == "opencli-twitter"
    assert node["proposalState"] == "proposed"
    assert node["ui"]["catalogId"] == "external.tool.capability"
    assert node["params"]["site"] == "twitter"
    assert node["params"]["command"] == "post"
    assert node["params"]["opencliAccess"] == "write"
    assert node["params"]["opencliAdapterNodeId"] == "opencli.adapter.twitter.post"
    assert node["params"]["args"] == {"text": "hello"}

    runtime_nodes = {node["id"]: node for node in data["compile"]["plan"]["runtime"]["nodes"]}
    tool_runtime = runtime_nodes["tool-twitter-post"]["runtime"]
    assert tool_runtime["binding"]["binding_id"] == "iii.collector-opencli.snapshot"
    assert tool_runtime["binding"]["channel"] == "opencli"
    assert tool_runtime["binding"]["input"] == {
        "site": "twitter",
        "command": "post",
        "access": "write",
    }
    assert tool_runtime["binding"]["contract"]["permissionGate"]["required"] == [
        "canFetchNetwork",
        "write:proposalState=accepted",
        "write:canMutateExternalSites",
    ]
    assert "write:tool_call_started" in tool_runtime["binding"]["contract"][
        "eventShape"
    ]["events"]
    assert tool_runtime["resource_requirement"]["mutationMode"] == "write"
    assert "missing_runtime" not in tool_runtime


@pytest.mark.asyncio
async def test_patch_rejects_opencli_write_adapter_with_missing_required_args(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.workflow.opencli_adapter_nodes._load_opencli_catalog",
        _fixture_opencli_adapter_catalog,
    )

    response = await client.post(
        "/api/v1/workflows/patch",
        json={
            "project": _valid_workflow_project(),
            "operations": [
                {
                    "op": "materialize_opencli_adapter",
                    "adapterNodeId": "opencli.adapter.twitter.post",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is False
    assert data["project"] is None
    assert data["compile"] is None
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == "missing_opencli_adapter_params"
    assert data["errors"][0]["message"] == (
        'OpenCLI adapter node "opencli.adapter.twitter.post" requires params: text'
    )
    assert data["errors"][0]["path"] == ["operations", "0", "adapterNodeId"]


@pytest.mark.asyncio
async def test_demand_draft_assembles_xiaohongshu_need_into_native_nodes(client):
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/demand-draft",
        json={
            "project": project,
            "text": "抓小红书热帖",
            "locale": "zh-CN",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["missing_capabilities"] == []
    assert [operation["op"] for operation in data["patch"]["operations"]] == [
        "add_adapter",
        "add_node",
        "add_node",
        "connect_nodes",
        "add_node",
        "add_node",
        "add_node",
        "connect_nodes",
        "connect_nodes",
        "connect_nodes",
    ]

    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    assert nodes["source-xiaohongshu"]["ui"]["catalogId"] == ("intelligence.source.opencli-slot")
    assert nodes["source-xiaohongshu"]["params"]["demand"]["text"] == "抓小红书热帖"
    assert nodes["source-xiaohongshu"]["params"]["args"] == {"keyword": "热门"}
    assert nodes["normalize-xiaohongshu"]["ui"]["catalogId"] == (
        "intelligence.processing.normalize"
    )
    assert nodes["merge-candidates"]["ui"]["catalogId"] == "intelligence.flow.merge"
    assert nodes["accept-records"]["ui"]["catalogId"] == ("intelligence.control.record-acceptance")
    assert nodes["record-sink"]["ui"]["catalogId"] == "intelligence.sink.records"

    runtime_nodes = {node["id"]: node for node in data["compile"]["plan"]["runtime"]["nodes"]}
    assert runtime_nodes["source-xiaohongshu"]["runtime"]["binding"]["channel"] == ("opencli")
    assert runtime_nodes["normalize-xiaohongshu"]["runtime"]["binding"]["binding_id"] == (
        "workflow.transform.normalize"
    )
    assert runtime_nodes["merge-candidates"]["runtime"]["binding"]["binding_id"] == (
        "workflow.flow.merge"
    )
    assert runtime_nodes["accept-records"]["runtime"]["binding"]["binding_id"] == (
        "workflow.gate.record-acceptance"
    )
    assert runtime_nodes["record-sink"]["runtime"]["binding"]["binding_id"] == (
        "workflow.record-sink.records"
    )


@pytest.mark.asyncio
async def test_demand_draft_assembles_multi_source_need_through_merge(client):
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/demand-draft",
        json={
            "project": project,
            "text": "抓小红书和B站AI热帖",
            "locale": "zh-CN",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    assert "source-xiaohongshu" in nodes
    assert "source-bilibili" in nodes
    assert nodes["source-xiaohongshu"]["params"]["args"] == {"keyword": "AI"}
    assert nodes["source-bilibili"]["params"]["args"] == {"keyword": "AI"}
    assert data["missing_capabilities"] == [
        {
            "capability": "collection.rank.popularity",
            "reason": (
                "The matched OpenCLI search adapters accept a query and limit but expose no "
                "popularity sort or threshold. Keep the topic search runnable, but require a "
                "governed popularity-ranking capability before claiming hot-post fidelity."
            ),
            "n8n_search_hint": "collection.rank.popularity",
        }
    ]
    edges = {
        (edge["source"], edge["target"], edge.get("targetPort"))
        for edge in data["project"]["edges"]
    }
    assert ("normalize-xiaohongshu", "merge-candidates", "in1") in edges
    assert ("normalize-bilibili", "merge-candidates", "in2") in edges

    runtime_nodes = {node["id"]: node for node in data["compile"]["plan"]["runtime"]["nodes"]}
    assert (
        runtime_nodes["merge-candidates"]["runtime"]["binding"]["input"]["preserveLineage"] is True
    )


@pytest.mark.asyncio
async def test_demand_draft_reports_missing_capability_for_unknown_source(client):
    project = _valid_workflow_project()

    response = await client.post(
        "/api/v1/workflows/demand-draft",
        json={
            "project": project,
            "text": "抓未知平台热帖",
            "locale": "zh-CN",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["patch"]["operations"][0]["op"] == "request_missing_capability"
    assert data["missing_capabilities"] == [
        {
            "capability": "collection.source.intent_mapping",
            "reason": (
                "No existing Canvas source capability matched this collection need. "
                "Add a real source/channel mapping before assembling runnable nodes."
            ),
            "n8n_search_hint": "collection.source.intent_mapping",
        }
    ]
    assert [node["id"] for node in data["project"]["nodes"]] == [
        node["id"] for node in project["nodes"]
    ]


@pytest.mark.asyncio
async def test_langgraph_import_preserves_graph_as_opencli_capability_nodes(client):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:1]
    project["edges"] = []

    response = await client.post(
        "/api/v1/workflows/import/external-runtime",
        json={
            "project": project,
            "runtime": "langgraph",
            "name": "research-agent",
            "graph": {
                "nodes": [
                    {"id": "planner", "type": "RunnableLambda", "label": "Planner"},
                    {"id": "search_tool", "type": "ToolNode", "name": "Search Tool"},
                    {"id": "join", "type": "merge", "label": "Join"},
                ],
                "edges": [
                    {"id": "planner-search", "source": "planner", "target": "search_tool"},
                    {"id": "search-join", "source": "search_tool", "target": "join"},
                ],
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["missing_capabilities"] == []
    assert [operation["op"] for operation in data["patch"]["operations"]] == [
        "add_node",
        "add_node",
        "add_node",
        "connect_nodes",
        "connect_nodes",
    ]

    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    assert nodes["planner"]["ui"]["catalogId"] == "external.tool.capability"
    assert nodes["search-tool"]["ui"]["catalogId"] == "external.tool.capability"
    assert nodes["join"]["ui"]["catalogId"] == "intelligence.flow.merge"
    assert nodes["search-tool"]["kind"] == "action"
    assert nodes["search-tool"]["capability"] == "store"
    assert nodes["search-tool"]["ui"]["externalWorkflow"] == {
        "runtime": "langgraph",
        "graphName": "research-agent",
        "nodeId": "search_tool",
        "nodeType": "ToolNode",
        "raw": {"id": "search_tool", "name": "Search Tool", "type": "ToolNode"},
    }
    assert "executor" not in nodes["search-tool"]["ui"]
    assert "rawExecutor" not in nodes["search-tool"]["params"]

    imported_edges = [
        edge for edge in data["project"]["edges"] if edge["source"] in {"planner", "search-tool"}
    ]
    assert [(edge["source"], edge["target"]) for edge in imported_edges] == [
        ("planner", "search-tool"),
        ("search-tool", "join"),
    ]
    assert imported_edges[0]["sourcePort"] == "out"
    assert imported_edges[0]["targetPort"] == "in"
    assert imported_edges[1]["targetPort"] == "in1"

    runtime_nodes = {node["id"]: node for node in data["compile"]["plan"]["runtime"]["nodes"]}
    assert runtime_nodes["search-tool"]["runtime"]["origin"]["catalog_id"] == (
        "external.tool.capability"
    )
    assert runtime_nodes["search-tool"]["runtime"]["missing_runtime"]["code"] == (
        "missing_tool_capability_binding"
    )
    assert runtime_nodes["join"]["runtime"]["binding"]["binding_id"] == ("workflow.flow.merge")


@pytest.mark.asyncio
async def test_langchain_import_accepts_dict_nodes_and_edge_only_nodes(client):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:1]
    project["edges"] = []

    response = await client.post(
        "/api/v1/workflows/import/external-runtime",
        json={
            "project": project,
            "runtime": "langchain",
            "graph": {
                "nodes": {
                    "prompt": {"type": "PromptTemplate"},
                    "parser": {"type": "OutputParser"},
                },
                "edges": [
                    {"source": "prompt", "target": "parser"},
                    {"source": "parser", "target": "unlisted_tool"},
                ],
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    assert nodes["prompt"]["ui"]["catalogId"] == "external.tool.capability"
    assert nodes["parser"]["ui"]["catalogId"] == "intelligence.processing.normalize"
    assert nodes["unlisted-tool"]["ui"]["externalWorkflow"]["runtime"] == "langchain"
    imported_edges = [
        edge for edge in data["project"]["edges"] if edge["source"] in {"prompt", "parser"}
    ]
    assert [(edge["source"], edge["target"]) for edge in imported_edges] == [
        ("prompt", "parser"),
        ("parser", "unlisted-tool"),
    ]


@pytest.mark.asyncio
async def test_dataflow_import_maps_sha_locked_aliases_to_native_data_nodes(client):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:1]
    project["edges"] = []
    source_sha = "f62aa1349e0ff14cb737a4cbda1945d04fde85bb"
    source_id = (
        f"dataflow@{source_sha}::dataflow.operators.general_text.refine."
        "remove_emoji_refiner.RemoveEmojiRefiner"
    )

    response = await client.post(
        "/api/v1/workflows/import/external-runtime",
        json={
            "project": project,
            "runtime": "dataflow",
            "name": "pinned-cleaning",
            "graph": {
                "sourceSha": source_sha,
                "nodes": [
                    {
                        "id": "lowercase",
                        "module": "general_text.refine.lowercase_refiner",
                        "class": "LowercaseRefiner",
                        "runConfig": {"input_key": "content"},
                    },
                    {
                        "id": "emoji",
                        "sourceId": source_id,
                        "runConfig": {"input_key": "content"},
                    },
                ],
                "edges": [{"source": "lowercase", "target": "emoji"}],
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    nodes = {node["id"]: node for node in data["project"]["nodes"]}
    for node_id, operation in (
        ("lowercase", "lowercase"),
        ("emoji", "removeEmoji"),
    ):
        node = nodes[node_id]
        assert node["kind"] == "agent"
        assert node["capability"] == "normalize"
        assert node["ui"]["catalogId"] == "intelligence.data.refine"
        assert set(node["params"]) == {
            "operatorId",
            "packVersion",
            "config",
        }
        assert node["params"]["operatorId"] == "text.clean"
        assert node["params"]["packVersion"] == "1.1.0"
        assert node["params"]["config"] == {
            "fields": ["content"],
            "operations": [operation],
        }
        assert node["ui"]["externalWorkflow"]["sourceSha"] == source_sha
        assert node["ui"]["externalWorkflow"]["runtime"] == "dataflow"
        assert "toolCapability" not in node["params"]
        assert node["ui"]["catalogId"] != "external.tool.capability"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_sha", "node", "detail"),
    [
        (
            "deadbeef",
            {
                "module": "general_text.refine.lowercase_refiner",
                "class": "LowercaseRefiner",
                "runConfig": {"input_key": "content"},
            },
            "DataFlow graph.sourceSha must equal",
        ),
        (
            "f62aa1349e0ff14cb737a4cbda1945d04fde85bb",
            {
                "module": "general_text.refine.semantic_refiner",
                "class": "SemanticRefiner",
                "runConfig": {"input_key": "content"},
            },
            "dataflow_operator_unsupported",
        ),
    ],
)
async def test_dataflow_import_fails_closed_for_unpinned_or_unsupported_nodes(
    client,
    source_sha,
    node,
    detail,
):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:1]
    project["edges"] = []
    node["id"] = "unsupported"

    response = await client.post(
        "/api/v1/workflows/import/external-runtime",
        json={
            "project": project,
            "runtime": "dataflow",
            "graph": {"sourceSha": source_sha, "nodes": [node]},
        },
    )

    assert response.status_code == 400
    assert detail in response.json()["detail"]


@pytest.mark.asyncio
async def test_imported_external_tool_runs_through_opencli_tool_capability_binding(client):
    project = {
        "id": "wf-external-tool-runtime",
        "name": "External tool runtime",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "manual",
                "kind": "schedule",
                "capability": "trigger",
                "params": {"mode": "manual"},
                "ui": {"catalogId": "intelligence.schedule.cron"},
            }
        ],
        "edges": [],
        "adapters": [],
        "agentPermissions": {
            "canFetchNetwork": False,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
    }

    imported = await client.post(
        "/api/v1/workflows/import/external-runtime",
        json={
            "project": project,
            "runtime": "langgraph",
            "name": "fixture-tool-graph",
            "graph": {
                "nodes": [
                    {
                        "id": "search_tool",
                        "type": "ToolNode",
                        "toolCapabilityId": "tool.search.fixture",
                        "executor": {
                            "mode": "fixture",
                            "outputs": [
                                {
                                    "title": "Fixture tool result",
                                    "url": "https://example.com/tool-result",
                                    "content": "Tool result emitted through OpenCLI Admin.",
                                }
                            ],
                        },
                    },
                    {"id": "parser", "type": "OutputParser"},
                ],
                "edges": [{"source": "search_tool", "target": "parser"}],
            },
        },
    )
    assert imported.status_code == 200
    imported_data = imported.json()["data"]
    assert imported_data["valid"] is True
    nodes = {node["id"]: node for node in imported_data["project"]["nodes"]}
    assert nodes["search-tool"]["params"]["toolCapability"]["id"] == "tool.search.fixture"
    assert nodes["search-tool"]["params"]["toolCapability"]["versionPin"] == {
        "package": "opencli-admin",
        "packageVersion": "0.1.0",
        "capabilityVersion": "1.0.0",
        "provenance": "built-in",
    }
    runtime_nodes = {
        node["id"]: node for node in imported_data["compile"]["plan"]["runtime"]["nodes"]
    }
    assert runtime_nodes["search-tool"]["runtime"]["binding"]["binding_id"] == (
        "workflow.external-tool.capability"
    )
    assert (
        runtime_nodes["search-tool"]["runtime"]["binding"]["input"]["toolCapabilityId"]
        == "tool.search.fixture"
    )

    run = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": imported_data["project"],
            "runId": "run-external-tool-fixture",
            "traceId": "trace-external-tool-fixture",
        },
    )
    assert run.status_code == 202
    run_data = run.json()["data"]
    assert run_data["status"] == "completed"
    states = {state["nodeId"]: state for state in run_data["nodeStates"]}
    assert states["search-tool"]["status"] == "completed"
    assert states["parser"]["status"] == "completed"

    events = (await client.get("/api/v1/workflows/runs/run-external-tool-fixture/events")).json()[
        "data"
    ]
    by_node = {}
    for event in events:
        by_node.setdefault(event["nodeId"], []).append(event)
    tool_partial = by_node["search-tool"][3]
    assert [event["eventType"] for event in by_node["search-tool"]] == [
        "queued",
        "started",
        "tool_call_started",
        "partial",
        "tool_call_completed",
        "completed",
    ]
    assert by_node["search-tool"][2]["details"]["toolCapabilityId"] == "tool.search.fixture"
    assert tool_partial["message"] == "OpenCLI Tool Capability emitted output"
    assert tool_partial["details"]["bindingId"] == "workflow.external-tool.capability"
    assert tool_partial["details"]["toolCapabilityId"] == "tool.search.fixture"
    assert tool_partial["details"]["outputItemCount"] == 1
    assert by_node["parser"][2]["details"]["recordCandidateCount"] == 1


@pytest.mark.asyncio
async def test_external_realtime_stream_tool_runs_okx_snapshot_executor(
    client,
    monkeypatch,
):
    def fake_okx_snapshot(params):
        assert params["instId"] == "ETH-USDT-SWAP"
        assert params["proxyUrl"] == "http://127.0.0.1:7897"
        return {
            "schema": "event.market.ticker.v1",
            "source": "okx",
            "channel": "tickers",
            "instId": "ETH-USDT-SWAP",
            "eventType": "market.ticker",
            "eventTime": "2026-07-05T09:10:26.659000+00:00",
            "latencyMs": 234,
            "market": {
                "last": "1766.01",
                "bidPx": "1766.01",
                "askPx": "1766.02",
            },
            "raw": {"instId": "ETH-USDT-SWAP", "last": "1766.01"},
        }

    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer.execute_okx_market_ticker_snapshot",
        fake_okx_snapshot,
    )
    project = {
        "id": "wf-okx-realtime-tool",
        "name": "OKX realtime tool",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "okx-stream",
                "kind": "action",
                "capability": "store",
                "params": {
                    "toolCapability": {
                        "id": "tool.realtime.stream.subscribe",
                        "versionPin": {
                            "package": "opencli-admin",
                            "packageVersion": "0.1.0",
                            "capabilityVersion": "1.0.0",
                            "provenance": "built-in",
                        },
                        "executor": {
                            "mode": "okx_market_ticker_snapshot",
                            "params": {"instId": "ETH-USDT-SWAP"},
                        },
                    },
                    "toolParams": {"proxyUrl": "http://127.0.0.1:7897"},
                },
                "ui": {"catalogId": "external.tool.capability"},
            }
        ],
        "edges": [],
        "adapters": [],
        "agentPermissions": {
            "canFetchNetwork": True,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
    }

    run = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-okx-realtime-tool",
            "traceId": "trace-okx-realtime-tool",
        },
    )

    assert run.status_code == 202
    data = run.json()["data"]
    assert data["status"] == "completed"

    events = (await client.get("/api/v1/workflows/runs/run-okx-realtime-tool/events")).json()[
        "data"
    ]
    by_node = {}
    for event in events:
        by_node.setdefault(event["nodeId"], []).append(event)

    okx_events = by_node["okx-stream"]
    assert [event["eventType"] for event in okx_events] == [
        "queued",
        "started",
        "tool_call_started",
        "partial",
        "tool_call_completed",
        "completed",
    ]
    assert okx_events[2]["details"]["executorMode"] == "okx_market_ticker_snapshot"
    partial = okx_events[3]
    assert partial["details"]["toolCapabilityId"] == "tool.realtime.stream.subscribe"
    assert partial["details"]["outputItemCount"] == 1
    assert partial["details"]["sampleOutputs"][0]["source"] == "okx"
    assert partial["details"]["sampleOutputs"][0]["instId"] == "ETH-USDT-SWAP"
    assert partial["details"]["sampleOutputs"][0]["market"]["last"] == "1766.01"


@pytest.mark.asyncio
async def test_imported_external_tool_rejects_unregistered_tool_capability(client):
    project = _valid_workflow_project()
    project["nodes"] = project["nodes"][:1]
    project["edges"] = []

    response = await client.post(
        "/api/v1/workflows/import/external-runtime",
        json={
            "project": project,
            "runtime": "langgraph",
            "graph": {
                "nodes": [
                    {
                        "id": "unknown_tool",
                        "type": "ToolNode",
                        "toolCapabilityId": "tool.missing.fixture",
                        "executor": {
                            "mode": "fixture",
                            "output": {"title": "Should not bind"},
                        },
                    }
                ],
                "edges": [],
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    runtime_node = next(
        node for node in data["compile"]["plan"]["runtime"]["nodes"] if node["id"] == "unknown-tool"
    )
    assert "binding" not in runtime_node["runtime"]
    assert runtime_node["runtime"]["missing_runtime"]["code"] == "unknown_tool_capability"
    assert runtime_node["runtime"]["external_tool"]["toolCapabilityId"] == ("tool.missing.fixture")
