from backend.schemas.workflow_runtime import WorkflowNodeRunEvent, WorkflowRunNodeState


def _event(**overrides) -> WorkflowNodeRunEvent:
    values = {
        "id": "run-1:0001:started:operator::package::group::implementation",
        "sequence": 1,
        "workflowId": "workflow-1",
        "workflowRunId": "run-1",
        "traceId": "trace-1",
        "nodeId": "operator::package::group::implementation",
        "eventType": "started",
        "createdAt": "2026-07-14T00:00:00Z",
    }
    values.update(overrides)
    return WorkflowNodeRunEvent.model_validate(values)


def test_four_level_event_exposes_canonical_path_and_legacy_location_fields():
    event = _event(
        nodePath=["operator", "package", "group", "implementation"],
    )

    assert event.nodePath == ["operator", "package", "group", "implementation"]
    assert event.packageNodeId == "operator::package::group"
    assert event.internalNodeId == "implementation"

    state = WorkflowRunNodeState.model_validate(
        {
            "nodeId": event.nodeId,
            "nodePath": event.nodePath,
            "status": "running",
        }
    )
    assert state.nodePath == event.nodePath
    assert state.packageNodeId == "operator::package::group"
    assert state.internalNodeId == "implementation"


def test_legacy_two_segment_event_is_upgraded_to_canonical_node_path():
    event = _event(
        id="run-1:0001:started:package::implementation",
        nodeId="package::implementation",
        packageNodeId="package",
        internalNodeId="implementation",
    )

    assert event.nodePath == ["package", "implementation"]
    assert event.packageNodeId == "package"
    assert event.internalNodeId == "implementation"
    assert event.model_dump(mode="json")["nodePath"] == ["package", "implementation"]
