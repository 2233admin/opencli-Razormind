import pytest

from backend.schemas.workflow import WorkflowProject
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.hda_templates import materialize_hda_templates
from backend.workflow.situation_awareness import SITUATION_AWARENESS_TOOL_CAPABILITY_ID
from backend.workflow.swarm_simulation import SWARM_SIMULATION_TOOL_CAPABILITY_ID
from backend.workflow.tool_capabilities import resolve_workflow_tool_capability


def test_two_independent_packages_materialize_to_distinct_tool_capabilities():
    project = WorkflowProject.model_validate(
        {
            "id": "wf-independent-intelligence-tools",
            "name": "Independent intelligence tools",
            "profile": "intelligence",
            "version": 1,
            "nodes": [
                {
                    "id": "situation",
                    "kind": "agent",
                    "capability": "normalize",
                    "params": {
                        "template": "situation-awareness",
                        "query": "人工智能",
                        "windowDays": 30,
                    },
                    "ui": {"catalogId": "package.intelligence.situation-awareness"},
                },
                {
                    "id": "swarm",
                    "kind": "agent",
                    "capability": "normalize",
                    "params": {
                        "template": "swarm-forecast",
                        "provider": "local",
                        "maxRounds": 3,
                    },
                    "ui": {"catalogId": "package.simulation.swarm-forecast"},
                },
            ],
            "edges": [
                {
                    "id": "situation-swarm",
                    "source": "situation",
                    "target": "swarm",
                    "sourcePort": "out",
                    "targetPort": "in",
                }
            ],
            "adapters": [],
        }
    )

    materialized = materialize_hda_templates(project)
    situation, swarm = materialized.nodes

    assert situation.internals is not None
    assert swarm.internals is not None
    assert situation.internals.nodes[0].params["toolCapability"]["id"] == (
        SITUATION_AWARENESS_TOOL_CAPABILITY_ID
    )
    assert situation.internals.nodes[0].params["toolParams"]["query"] == "人工智能"
    assert swarm.internals.nodes[0].params["toolCapability"]["id"] == (
        SWARM_SIMULATION_TOOL_CAPABILITY_ID
    )
    assert swarm.internals.nodes[0].params["toolParams"]["maxRounds"] == 3
    compiled = compile_workflow_project(materialized)
    assert compiled.valid is True
    assert compiled.plan is not None
    assert "situation::tool" in compiled.plan.runtime.node_ids
    assert "swarm::tool" in compiled.plan.runtime.node_ids


def test_native_lifecycle_package_parameters_materialize_into_actions():
    project = WorkflowProject.model_validate(
        {
            "id": "wf-native-lifecycle-parameters",
            "name": "Native lifecycle parameters",
            "profile": "intelligence",
            "version": 1,
            "nodes": [
                {
                    "id": "native",
                    "kind": "agent",
                    "capability": "normalize",
                    "params": {
                        "template": "native-intelligence-lifecycle",
                        "seed": 17,
                        "personaCount": 8,
                        "maxRounds": 4,
                        "agentCount": 6,
                        "requirement": "Track the likely outcome.",
                        "platforms": ["reddit"],
                        "question": "What changed?",
                    },
                    "ui": {"catalogId": "package.intelligence.native-lifecycle"},
                }
            ],
            "edges": [],
            "adapters": [],
        }
    )

    native = materialize_hda_templates(project).nodes[0]
    assert native.internals is not None
    actions = {
        node.id: node.params["toolParams"]
        for node in native.internals.nodes
        if node.kind == "action"
    }
    assert actions["research"]["seed"] == 17
    assert actions["personas"]["personaCount"] == 8
    assert actions["simulation-start"] == {
        "seed": 17,
        "maxRounds": 4,
        "agentCount": 6,
        "platforms": ["reddit"],
        "requirement": "Track the likely outcome.",
    }
    assert actions["report-ask"]["question"] == "What changed?"


def test_two_independent_tool_capabilities_are_registered():
    situation = resolve_workflow_tool_capability(SITUATION_AWARENESS_TOOL_CAPABILITY_ID)
    swarm = resolve_workflow_tool_capability(SWARM_SIMULATION_TOOL_CAPABILITY_ID)

    assert situation is not None
    assert situation.executor.mode == "situation_awareness"
    assert swarm is not None
    assert swarm.executor.mode == "swarm_simulation"


@pytest.mark.asyncio
async def test_two_package_catalog_capabilities_are_projected_runnable(client):
    response = await client.get("/api/v1/workflows/capabilities")
    assert response.status_code == 200
    catalog = {row["id"]: row for row in response.json()["data"]["catalog"]}

    assert catalog["package.intelligence.situation-awareness"]["status"] == "runnable"
    assert catalog["package.intelligence.situation-awareness"]["backendAvailable"] is True
    assert catalog["package.simulation.swarm-forecast"]["status"] == "runnable"
    assert catalog["package.simulation.swarm-forecast"]["backendAvailable"] is True
    native_parameters = catalog["package.intelligence.native-lifecycle"]["manifest"][
        "presentation"
    ]["parameters"]
    assert [parameter["name"] for parameter in native_parameters] == [
        "seed",
        "personaCount",
        "maxRounds",
        "agentCount",
        "requirement",
        "platforms",
        "question",
    ]


@pytest.mark.asyncio
async def test_collection_output_flows_through_situation_into_swarm(client):
    project = {
        "id": "wf-situation-to-swarm",
        "name": "Situation to swarm",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "source-douyin",
                "kind": "source",
                "capability": "fetch",
                "adapter": "opencli-douyin",
                "params": {
                    "site": "douyin",
                    "command": "search",
                    "fixtureItems": [
                        {
                            "title": "人工智能新模型 #人工智能",
                            "url": "https://www.douyin.com/video/1?share_source=copy",
                            "create_time": 1784512800,
                            "digg_count": "2万",
                        }
                    ],
                },
                "ui": {"catalogId": "intelligence.source.opencli-slot"},
            },
            {
                "id": "normalize",
                "kind": "agent",
                "capability": "normalize",
                "params": {"preserveSourceRefs": True},
                "ui": {"catalogId": "intelligence.processing.normalize"},
            },
            {
                "id": "collection-output",
                "kind": "inbox",
                "capability": "store",
                "params": {"queue": "opencli-hda-output", "archive": False},
                "ui": {"catalogId": "intelligence.output.collection-result"},
            },
            {
                "id": "situation",
                "kind": "action",
                "capability": "store",
                "params": {
                    "toolCapability": {
                        "id": SITUATION_AWARENESS_TOOL_CAPABILITY_ID,
                        "executor": {"mode": "situation_awareness", "params": {}},
                    },
                    "toolParams": {
                        "query": "人工智能",
                        "now": "2026-07-21T00:00:00Z",
                        "windowDays": 30,
                    },
                },
                "ui": {"catalogId": "external.tool.capability"},
            },
            {
                "id": "swarm",
                "kind": "action",
                "capability": "store",
                "params": {
                    "toolCapability": {
                        "id": SWARM_SIMULATION_TOOL_CAPABILITY_ID,
                        "executor": {"mode": "swarm_simulation", "params": {}},
                    },
                    "toolParams": {
                        "provider": "local",
                        "requirement": "推演人工智能讨论走势",
                        "agentCount": 6,
                        "maxRounds": 3,
                        "now": "2026-07-21T00:00:00Z",
                    },
                },
                "ui": {"catalogId": "external.tool.capability"},
            },
        ],
        "edges": [
            {
                "id": "source-normalize",
                "source": "source-douyin",
                "target": "normalize",
                "sourcePort": "out",
                "targetPort": "in",
            },
            {
                "id": "normalize-output",
                "source": "normalize",
                "target": "collection-output",
                "sourcePort": "out",
                "targetPort": "in",
            },
            {
                "id": "output-situation",
                "source": "collection-output",
                "target": "situation",
                "sourcePort": "out",
                "targetPort": "in",
            },
            {
                "id": "situation-swarm",
                "source": "situation",
                "target": "swarm",
                "sourcePort": "out",
                "targetPort": "in",
            },
        ],
        "adapters": [
            {
                "id": "opencli-douyin",
                "type": "source",
                "provider": "opencli",
                "mode": "live",
                "config": {"channel": "opencli"},
            }
        ],
        "agentPermissions": {
            "canFetchNetwork": True,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
    }

    response = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-situation-to-swarm",
            "traceId": "trace-situation-to-swarm",
        },
    )

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "completed"
    events = (
        await client.get("/api/v1/workflows/runs/run-situation-to-swarm/events")
    ).json()["data"]
    by_node = {}
    for event in events:
        by_node.setdefault(event["nodeId"], []).append(event)

    collection_partial = next(
        event for event in by_node["collection-output"] if event["eventType"] == "partial"
    )
    assert collection_partial["details"]["outputItemCount"] == 1

    situation_partial = next(
        event for event in by_node["situation"] if event["eventType"] == "partial"
    )
    assert situation_partial["details"]["executorMode"] == "situation_awareness"
    situation_sample = situation_partial["details"]["sampleOutputs"][0]
    assert situation_sample["schema"] == "situation.report.v1"
    assert situation_sample["counts"]["includedAfterDedupe"] == 1

    swarm_partial = next(
        event for event in by_node["swarm"] if event["eventType"] == "partial"
    )
    assert swarm_partial["details"]["executorMode"] == "swarm_simulation"
    swarm_sample = swarm_partial["details"]["sampleOutputs"][0]
    assert swarm_sample["schema"] == "swarm.forecast.v1"
    assert swarm_sample["simulated"] is True
    assert swarm_sample["run"]["roundsCompleted"] == 3
