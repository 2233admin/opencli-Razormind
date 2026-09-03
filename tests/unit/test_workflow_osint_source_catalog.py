from copy import deepcopy

from backend.schemas.workflow import WorkflowProject
from backend.workflow.capability_projection import build_workflow_capabilities
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.runtime_registry import SOURCE_FETCH_BINDING_ID

SOURCE_CASES = (
    (
        "intelligence.source.searxng",
        {"endpoint": "https://search.example.test/search"},
        "source-searxng-http",
        "http",
    ),
    (
        "intelligence.source.rsshub",
        {"feedUrl": "https://rss.example.test/example/route"},
        "source-rsshub-feed",
        "rss",
    ),
    (
        "intelligence.source.doubao-research",
        {"question": "高吉星燕窝酸 DHA 藻油"},
        "source-doubao-research-capture",
        "doubao_research",
    ),
)


def _project_for_source(
    capability_id: str,
    required_params: dict[str, object],
) -> WorkflowProject:
    capability = next(
        item for item in build_workflow_capabilities().catalog if item.id == capability_id
    )
    manifest = capability.manifest
    adapter = deepcopy(manifest["nodeCatalog"]["adapter"])
    params = {
        parameter["name"]: deepcopy(parameter["default"])
        for parameter in manifest["presentation"]["parameters"]
        if "default" in parameter
    }
    params.update(required_params)
    return WorkflowProject.model_validate(
        {
            "id": f"{capability_id}-round-trip",
            "name": capability.label,
            "profile": "intelligence",
            "nodes": [
                {
                    "id": "source",
                    "kind": capability.kind,
                    "capability": capability.capability,
                    "adapter": adapter["id"],
                    "params": params,
                    "ui": {"catalogId": capability.id, "label": capability.label},
                }
            ],
            "edges": [],
            "adapters": [adapter],
            "agentPermissions": {
                "canFetchNetwork": True,
                "allowedDomains": ["search.example.test", "rss.example.test"],
            },
        }
    )


def test_osint_source_catalog_nodes_compile_to_existing_source_runtime():
    for capability_id, required_params, adapter_id, provider in SOURCE_CASES:
        project = _project_for_source(capability_id, required_params)

        compiled = compile_workflow_project(project)

        assert compiled.valid is True
        assert compiled.errors == []
        assert compiled.plan is not None
        node = compiled.plan.runtime.nodes[0]
        assert node.adapter is not None
        assert node.adapter.id == adapter_id
        assert node.adapter.provider == provider
        assert node.runtime["binding"]["binding_id"] == SOURCE_FETCH_BINDING_ID
        assert node.runtime.get("missing_runtime") is None


def test_osint_source_catalog_nodes_are_backend_authoritative_and_editable():
    catalog = {item.id: item for item in build_workflow_capabilities().catalog}

    for capability_id, required_params, adapter_id, provider in SOURCE_CASES:
        capability = catalog[capability_id]
        manifest = capability.manifest
        node_catalog = manifest["nodeCatalog"]
        parameter_names = {
            parameter["name"] for parameter in manifest["presentation"]["parameters"]
        }

        assert capability.status == "runnable"
        assert capability.backendAvailable is True
        assert manifest["canvas"]["node"] is True
        assert node_catalog["authority"] == "backend"
        assert node_catalog["category"] == "source"
        assert node_catalog["adapter"]["id"] == adapter_id
        assert node_catalog["adapter"]["provider"] == provider
        assert set(required_params).issubset(parameter_names)


def test_doubao_research_catalog_builds_with_trigger_input_contract():
    capabilities = [
        item
        for item in build_workflow_capabilities().catalog
        if item.id == "intelligence.source.doubao-research"
    ]

    assert len(capabilities) == 1
    capability = capabilities[0]

    assert capability.manifest["ports"]["inputs"] == [
        {"name": "in", "type": "trigger"}
    ]
