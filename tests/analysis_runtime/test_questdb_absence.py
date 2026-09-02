from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_studio_lifecycle_api import _create_studio_workflow

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_STARTUP_CHECK = """
import asyncio

from httpx import ASGITransport, AsyncClient

from backend.main import app


async def verify_startup() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/health")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "ok"


asyncio.run(verify_startup())
print("QUESTDB_ABSENT_SAFE_STARTUP=PASS")
"""
_RUNTIME_CONFIGURATIONS = [
    pytest.param(
        {
            "QUESTDB_ANALYSIS_RUNTIME_ENABLED": "false",
            "QUESTDB_ANALYSIS_RUNTIME_URL": "http://127.0.0.1:1",
            "QUESTDB_ANALYSIS_RUNTIME_HEALTH_URL": "http://127.0.0.1:1",
        },
        id="disabled",
    ),
    pytest.param(
        {
            "QUESTDB_ANALYSIS_RUNTIME_ENABLED": "true",
            "QUESTDB_ANALYSIS_RUNTIME_URL": "http://127.0.0.1:1",
            "QUESTDB_ANALYSIS_RUNTIME_HEALTH_URL": "http://127.0.0.1:1",
            "QUESTDB_ANALYSIS_RUNTIME_TIMEOUT_SECONDS": "0.1",
        },
        id="unreachable",
    ),
]


@pytest.mark.parametrize("runtime_environment", _RUNTIME_CONFIGURATIONS)
def test_application_starts_without_a_reachable_questdb(
    tmp_path: Path,
    runtime_environment: dict[str, str],
) -> None:
    database_path = (tmp_path / "opencli-absence.db").as_posix()
    environment = {
        **os.environ,
        "AGENT_API_TOKEN": "",
        "API_AUTH_TOKEN": "",
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
        "LOCAL_AUTH_STATE_PATH": str(tmp_path / "local-auth.hash"),
        "SECRET_KEY": "questdb-absence-test-secret-key-at-least-32-characters",
        **runtime_environment,
    }

    result = subprocess.run(
        [sys.executable, "-c", _STARTUP_CHECK],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "QUESTDB_ABSENT_SAFE_STARTUP=PASS" in result.stdout


@pytest.mark.parametrize("runtime_environment", _RUNTIME_CONFIGURATIONS)
async def test_existing_workflow_validation_completes_without_a_reachable_questdb(
    client,
    monkeypatch: pytest.MonkeyPatch,
    runtime_environment: dict[str, str],
) -> None:
    for name, value in runtime_environment.items():
        monkeypatch.setenv(name, value)

    created = await _create_studio_workflow(client)
    response = await client.post(
        f"{created['base_url']}/draft/validation-runs",
        json={},
    )

    assert response.status_code == 201, response.text
    run = response.json()["data"]
    assert run["status"] == "completed"
    assert run["valid"] is True
    assert run["errors"] == []
