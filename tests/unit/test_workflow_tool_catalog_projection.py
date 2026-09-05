from copy import deepcopy

from backend.schemas.workflow import WorkflowProject, WorkflowProjectNode
from backend.workflow.capability_projection import build_workflow_capabilities
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.runtime_registry import (
    EXTERNAL_TOOL_BINDING_ID,
    resolve_runtime_metadata,
)
from backend.workflow.tool_capabilities import list_workflow_tool_capabilities


def test_tool_capability_catalog_projection_is_explicit_and_executable(monkeypatch):
    registry = list_workflow_tool_capabilities()
    fixture = next(tool for tool in registry.tools if tool.id == "tool.search.fixture")
    projected = fixture.model_copy(
        update={
            "id": "tool.osint.metasearch",
            "label": "OSINT Metasearch",
            "manifest": {
                **fixture.manifest,
                "canvas": {"node": True},
                "nodeCatalog": {
                    "id": "tool.osint.metasearch",
                    "authority": "backend",
                    "origin": "tool-capability",
                    "category": "processing",
                    "kind": "action",
                    "capability": "store",
                },
                "presentation": {"icon": "Search"},
            },
        },
        deep=True,
    )
    hidden = fixture.model_copy(update={"id": "tool.osint.hidden"}, deep=True)
    response = registry.model_copy(update={"tools": [projected, hidden]})
    monkeypatch.setattr(
        "backend.workflow.capability_projection.list_workflow_tool_capabilities",
        lambda: response,
    )

    capabilities = build_workflow_capabilities()
    catalog = {item.id: item for item in capabilities.catalog}
    resources = {item.id: item for item in capabilities.resources}

    assert "tool.osint.metasearch" in catalog
    assert "tool.osint.hidden" not in catalog
    item = catalog["tool.osint.metasearch"]
    assert item.status == "runnable"
    assert item.backendAvailable is True
    assert item.kind == "action"
    assert item.capability == "store"
    assert item.runtimeBinding == "workflow.external-tool.capability"
    assert item.manifest["toolCapability"]["id"] == "tool.osint.metasearch"
    assert item.manifest["toolCapability"]["executor"]["mode"] == "fixture"
    assert [field["name"] for field in item.manifest["presentation"]["parameters"][:2]] == [
        "toolCapability",
        "toolParams",
    ]
    assert item.manifest["presentation"]["parameters"][0]["default"]["versionPin"] == {
        "package": "opencli-admin",
        "packageVersion": "0.1.0",
        "capabilityVersion": "1.0.0",
        "provenance": "built-in",
    }
    assert "resource.tool-capability.tool.osint.metasearch" in resources
    assert "resource.tool-capability.tool.osint.hidden" in resources


TOOL_ID = "tool.intelligence.situation-awareness"


def _projected_tool_node() -> WorkflowProjectNode:
    capability = next(
        item for item in build_workflow_capabilities().catalog if item.id == TOOL_ID
    )
    parameters = capability.manifest["presentation"]["parameters"]
    params = {
        parameter["name"]: deepcopy(parameter["default"])
        for parameter in parameters
        if "default" in parameter
    }
    return WorkflowProjectNode.model_validate(
        {
            "id": "projected-tool",
            "kind": capability.kind,
            "capability": capability.capability,
            "params": params,
            "ui": {"catalogId": capability.id},
        }
    )


def _project(node: WorkflowProjectNode) -> WorkflowProject:
    return WorkflowProject.model_validate(
        {
            "id": "tool-catalog-round-trip",
            "name": "Tool catalog round trip",
            "profile": "intelligence",
            "nodes": [node.model_dump(mode="json")],
            "edges": [],
            "adapters": [],
        }
    )


def test_projected_tool_node_compiles_and_resolves_runtime_with_correct_pin():
    node = _projected_tool_node()

    compiled = compile_workflow_project(_project(node))
    assert compiled.valid is True
    assert compiled.errors == []

    metadata = resolve_runtime_metadata(node, None)
    assert metadata.get("missing_runtime") is None
    assert metadata["binding"]["binding_id"] == EXTERNAL_TOOL_BINDING_ID
    assert metadata["binding"]["input"]["toolCapabilityId"] == TOOL_ID
    assert (
        metadata["binding"]["input"]["toolCapabilityVersionPin"]
        == node.params["toolCapability"]["versionPin"]
    )


def test_projected_tool_node_rejects_stale_version_pin():
    node = _projected_tool_node()
    node.params["toolCapability"]["versionPin"]["capabilityVersion"] = "999.0.0"

    compiled = compile_workflow_project(_project(node))

    assert compiled.valid is False
    assert "tool_capability_version_pin_mismatch" in {
        error.code for error in compiled.errors
    }
