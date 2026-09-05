"""Network-level acceptance checks for the isolated OpenAlice workspace fixture."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from backend.models.agent_conversation import AgentConversationTurn
from tests.fixtures.openalice_workspace.seed import (
    ARTIFACT_ALPHA_ONE,
    ARTIFACT_BETA_ONE,
    CONVERSATION_ALPHA_ACTIVE,
    PROJECT_ALPHA,
    PROJECT_BETA,
    RUN_ALPHA_ONE,
    RUN_ALPHA_TWO,
    RUN_BETA_ONE,
    SESSION_ALPHA_ONE,
    WORKFLOW_ALPHA,
    WORKFLOW_BETA,
    WORKSPACE_ID,
)

TOKEN = "openalice-e2e-token"
ROOT = Path(__file__).resolve().parents[2]
BACKEND_RUNNER = ROOT / "frontend" / "e2e" / "openalice-workspace-support" / "run-backend.py"


@pytest.fixture
def isolated_backend(tmp_path: Path):
    db_path = tmp_path / "openalice-workspace.sqlite3"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")])),
    }
    process = subprocess.Popen(
        [sys.executable, str(BACKEND_RUNNER), "--db", str(db_path), "--port", "8048"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = "http://127.0.0.1:8048"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"isolated backend exited with code {process.returncode}")
            try:
                response = httpx.get(f"{base_url}/health", timeout=1)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise AssertionError("isolated backend did not become healthy on port 8048")
        yield base_url, db_path
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _get(client: httpx.Client, path: str) -> dict:
    response = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    return response.json()


def test_real_http_workspace_journey_persists_and_scopes(isolated_backend):
    base_url, db_path = isolated_backend
    with httpx.Client(base_url=base_url, timeout=10) as client:
        alpha_artifacts = _get(
            client,
            f"/api/v1/workspaces/{WORKSPACE_ID}/projects/{PROJECT_ALPHA}/artifacts"
            f"?workflow_id={WORKFLOW_ALPHA}&run_id={RUN_ALPHA_ONE}",
        )["data"]
        alpha_ids = {row["id"] for row in alpha_artifacts}
        assert f"{SESSION_ALPHA_ONE}:{ARTIFACT_ALPHA_ONE}" in alpha_ids
        assert "evidence-batch:batch-alpha-1" in alpha_ids
        assert all(row["project_id"] == PROJECT_ALPHA and row["run_id"] == RUN_ALPHA_ONE for row in alpha_artifacts)

        beta_artifacts = _get(
            client,
            f"/api/v1/workspaces/{WORKSPACE_ID}/projects/{PROJECT_BETA}/artifacts"
            f"?workflow_id={WORKFLOW_BETA}&run_id={RUN_BETA_ONE}",
        )["data"]
        assert {row["id"] for row in beta_artifacts} == {
            f"openalice-session-beta-1:{ARTIFACT_BETA_ONE}",
            "evidence-batch:batch-beta-1",
        }

        alpha_one_records = _get(
            client,
            f"/api/v1/records?project_id={PROJECT_ALPHA}&workflow_id={WORKFLOW_ALPHA}"
            f"&workflow_run_id={RUN_ALPHA_ONE}&limit=100",
        )["data"]
        alpha_two_records = _get(
            client,
            f"/api/v1/records?project_id={PROJECT_ALPHA}&workflow_id={WORKFLOW_ALPHA}"
            f"&workflow_run_id={RUN_ALPHA_TWO}&limit=100",
        )["data"]
        beta_records = _get(
            client,
            f"/api/v1/records?project_id={PROJECT_BETA}&workflow_id={WORKFLOW_BETA}"
            f"&workflow_run_id={RUN_BETA_ONE}&limit=100",
        )["data"]
        assert [row["id"] for row in alpha_one_records] == ["openalice-record-alpha-1"]
        assert [row["id"] for row in alpha_two_records] == ["openalice-record-alpha-2"]
        assert [row["id"] for row in beta_records] == ["openalice-record-beta-1"]

        detail = _get(
            client,
            f"/api/v1/workspaces/{WORKSPACE_ID}/projects/{PROJECT_ALPHA}/artifacts"
            f"/{SESSION_ALPHA_ONE}:{ARTIFACT_ALPHA_ONE}?workflow_id={WORKFLOW_ALPHA}&run_id={RUN_ALPHA_ONE}",
        )["data"]
        assert detail["conversation_id"] == CONVERSATION_ALPHA_ACTIVE
        assert "Alpha report body" in detail["content"]["content"]

        follow_up = client.post(
            f"/api/v1/chat/sessions/{CONVERSATION_ALPHA_ACTIVE}/messages",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "request_id": "openalice-http-follow-up",
                "content": "请补充 Alpha run one 的来源说明",
                "context": {
                    "project_id": PROJECT_ALPHA,
                    "workflow_id": WORKFLOW_ALPHA,
                    "run_id": RUN_ALPHA_ONE,
                    "surface": "project_artifact",
                },
            },
        )
        assert follow_up.status_code == 200, follow_up.text
        assert follow_up.json()["data"]["conversation_id"] == CONVERSATION_ALPHA_ACTIVE
        assert "Deterministic E2E reply" in follow_up.json()["data"]["turn"]["response"]["content"]

        proposal = _get(
            client,
            f"/api/v1/workspaces/{WORKSPACE_ID}/operations-inbox"
            "?type=change_proposal&status=open&limit=100",
        )["data"]
        assert len(proposal) == 1
        assert proposal[0]["evidence"]["run_id"] == RUN_ALPHA_ONE

        conversation = _get(client, f"/api/v1/chat/sessions/{CONVERSATION_ALPHA_ACTIVE}")["data"]
        assert conversation["turns"][-1]["user_content"] == "请补充 Alpha run one 的来源说明"

    async def read_persisted_turn() -> str:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        try:
            async with engine.connect() as connection:
                result = await connection.scalar(
                    select(AgentConversationTurn.user_content)
                    .where(AgentConversationTurn.conversation_id == CONVERSATION_ALPHA_ACTIVE)
                    .order_by(AgentConversationTurn.sequence.desc())
                    .limit(1)
                )
                return result
        finally:
            await engine.dispose()

    assert asyncio.run(read_persisted_turn()) == "请补充 Alpha run one 的来源说明"
