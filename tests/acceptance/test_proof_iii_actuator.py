from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _actuator_module():
    path = ROOT / "tests/acceptance/fault_tools/proof_iii_actuator.py"
    spec = importlib.util.spec_from_file_location("proof_iii_actuator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_actor_triggers_pinned_iii_batch_with_correlated_record(monkeypatch):
    actuator = _actuator_module()
    monkeypatch.setenv("III_CLI_PATH", "/opt/iii/iii")
    request = actuator.IngressRequest(
        phase="pre_snapshot_101",
        workspace_id="workspace-1",
        project_id="project-1",
        workflow_id="workflow-1",
        studio_workflow_version_id="version-1",
        run_id="run-1",
        node_id="opencli-source",
        command_id="command-1",
        attempt_id="attempt-1",
        attempt_number=1,
        task_id="task-1",
        trace_id="trace-1",
        source_id="00000000-0000-0000-0000-000000000101",
        payload_sha256="a" * 64,
        event_id="actor-101",
    )

    command = actuator._trigger_command("ws://proof-iii:49134", request)

    assert command[:7] == [
        "/opt/iii/iii",
        "trigger",
        "--address",
        "proof-iii",
        "--port",
        "49134",
        "odp.ingest::batch",
    ]
    payload = json.loads(command[-1])
    assert payload["task_id"] == "task-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["admin_collection"] == {
        "version": "v1",
        "workspace_id": "workspace-1",
        "project_id": "project-1",
        "workflow_id": "workflow-1",
        "studio_workflow_version_id": "version-1",
        "run_id": "run-1",
        "node_id": "opencli-source",
        "command_id": "command-1",
        "attempt_id": "attempt-1",
        "attempt_number": 1,
        "task_id": "task-1",
        "trace_id": "trace-1",
        "source_id": "00000000-0000-0000-0000-000000000101",
        "source_binding_id": None,
        "source_binding_revision_id": None,
        "source_binding_revision_number": None,
        "payload_sha256": "a" * 64,
        "expected_key_set_sha256": actuator._expected_key_set_sha256(payload["events"][0]),
    }
    assert payload["events"] == [
        {
            "schema_version": 1,
            "provider": "opencli/proof-iii-actuator",
            "source_id": "00000000-0000-0000-0000-000000000101",
            "event_id": "actor-101",
            "ingest_mode": "snapshot",
            "source_ts": payload["events"][0]["source_ts"],
            "payload": payload["events"][0]["payload"],
            "raw_data": payload["events"][0]["raw_data"],
            "task_id": "task-1",
            "trace_id": "trace-1",
        }
    ]
    assert payload["events"][0]["payload"]["phase"] == "pre_snapshot_101"


def test_actor_accepts_the_amendment_duplicate_phase():
    actuator = _actuator_module()

    request = actuator.IngressRequest(
        phase="amendment_duplicate",
        workspace_id="workspace-1",
        project_id="project-1",
        workflow_id="workflow-1",
        studio_workflow_version_id="version-1",
        run_id="run-1",
        node_id="opencli-source",
        command_id="command-1",
        attempt_id="attempt-1",
        attempt_number=1,
        task_id="task-1",
        trace_id="trace-1",
        source_id="00000000-0000-0000-0000-000000000001",
        payload_sha256="a" * 64,
        event_id="original-event",
    )

    assert request.phase == "amendment_duplicate"


def test_actor_rejects_an_unauthenticated_request(monkeypatch):
    actuator = _actuator_module()
    monkeypatch.setenv("API_AUTH_TOKEN", "actor-token")
    request = {
        "phase": "late_102",
        "workspace_id": "workspace-1",
        "project_id": "project-1",
        "workflow_id": "workflow-1",
        "studio_workflow_version_id": "version-1",
        "run_id": "run-1",
        "node_id": "opencli-source",
        "command_id": "command-1",
        "attempt_id": "attempt-1",
        "attempt_number": 1,
        "task_id": "task-1",
        "trace_id": "trace-1",
        "source_id": "00000000-0000-0000-0000-000000000102",
        "payload_sha256": "a" * 64,
        "event_id": "actor-102",
    }

    with TestClient(actuator.app) as client:
        assert client.post("/actuate/ingress", json=request).status_code == 401
