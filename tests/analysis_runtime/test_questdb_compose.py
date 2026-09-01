import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_ENV = {
    **os.environ,
    "API_AUTH_TOKEN": "questdb-probe-api-token",
    "BOOTSTRAP_ADMIN_TOKEN": "questdb-probe-bootstrap-token",
    "COMPOSE_PROJECT_NAME": "opencli-questdb-rendered-contract",
    "QUESTDB_ANALYSIS_RUNTIME_ENABLED": "false",
    "QUESTDB_HEALTH_PORT": "9003",
    "QUESTDB_HTTP_PORT": "9000",
    "SECRET_KEY": "questdb-probe-secret-key-at-least-32-characters",
}


def _render_compose(*arguments: str) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required to verify the rendered contract")
    result = subprocess.run(
        ["docker", "compose", *arguments, "config", "--format", "json"],
        cwd=_REPOSITORY_ROOT,
        env=_COMPOSE_ENV,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_questdb_is_present_only_in_the_optional_analysis_runtime_profile() -> None:
    default_config = _render_compose()
    profile_config = _render_compose("--profile", "analysis-runtime")

    assert "questdb" not in default_config["services"]

    questdb = profile_config["services"]["questdb"]
    assert questdb["image"] == "questdb/questdb:10.0.1"
    assert questdb["profiles"] == ["analysis-runtime"]
    assert "container_name" not in questdb
    assert {
        (port["host_ip"], port["published"], port["target"])
        for port in questdb["ports"]
    } == {
        ("127.0.0.1", "9000", 9000),
        ("127.0.0.1", "9003", 9003),
    }
    assert "9003" in " ".join(questdb["healthcheck"]["test"])
    assert (
        default_config["services"]["api"]["environment"][
            "QUESTDB_ANALYSIS_RUNTIME_ENABLED"
        ]
        == "false"
    )
    assert "questdb" not in profile_config["services"]["api"].get("depends_on", {})
