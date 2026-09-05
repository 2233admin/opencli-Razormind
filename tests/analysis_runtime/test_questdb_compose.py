import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.questdb import probe as questdb_probe

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


def _render_compose(
    *arguments: str,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required to verify the rendered contract")
    result = subprocess.run(
        ["docker", "compose", *arguments, "config", "--format", "json"],
        cwd=_REPOSITORY_ROOT,
        env={**_COMPOSE_ENV, **(env_overrides or {})},
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
    assert set(questdb["networks"]) == {"questdb_runtime"}
    assert "default" not in questdb["networks"]
    assert profile_config["networks"]["questdb_runtime"]["internal"] is True
    assert {
        service_name
        for service_name, service in profile_config["services"].items()
        if "questdb_runtime" in service.get("networks", {})
    } == {"api", "questdb"}
    assert questdb["read_only"] is True
    assert questdb["environment"]["DO_CHOWN"] == "false"
    assert questdb["tmpfs"] == ["/tmp:size=64m,mode=1777"]
    assert questdb["cap_drop"] == ["ALL"]
    assert set(questdb["cap_add"]) == {"SETGID", "SETUID"}
    assert questdb["security_opt"] == ["no-new-privileges:true"]
    assert questdb["pids_limit"] == 512
    assert (
        default_config["services"]["api"]["environment"][
            "QUESTDB_ANALYSIS_RUNTIME_ENABLED"
        ]
        == "false"
    )
    assert "questdb" not in profile_config["services"]["api"].get("depends_on", {})


def test_questdb_profile_accepts_docker_assigned_loopback_ports() -> None:
    profile_config = _render_compose(
        "--profile",
        "analysis-runtime",
        env_overrides={
            "QUESTDB_HEALTH_PORT": "49152-65535",
            "QUESTDB_HTTP_PORT": "49152-65535",
        },
    )

    assert {
        (port["host_ip"], port["published"], port["target"])
        for port in profile_config["services"]["questdb"]["ports"]
    } == {
        ("127.0.0.1", "49152-65535", 9000),
        ("127.0.0.1", "49152-65535", 9003),
    }


def test_probe_accepts_engine_inspect_capability_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            json.dumps(
                [
                    {
                        "Config": {"Env": ["DO_CHOWN=false"]},
                        "HostConfig": {
                            "ReadonlyRootfs": True,
                            "CapDrop": ["ALL"],
                            "CapAdd": ["SETGID", "SETUID"],
                            "SecurityOpt": ["no-new-privileges:true"],
                            "PidsLimit": 512,
                            "Tmpfs": {"/tmp": "size=64m,mode=1777"},
                        },
                        "NetworkSettings": {
                            "Networks": {"probe_questdb_runtime": {}}
                        },
                    }
                ]
            ),
            json.dumps(
                [
                    {
                        "Labels": {
                            "com.docker.compose.project": "probe",
                            "com.docker.compose.network": "questdb_runtime",
                        },
                        "Internal": True,
                    }
                ]
            ),
        ]
    )

    def fake_run(*_args: Any, **_kwargs: Any) -> str:
        return next(responses)

    monkeypatch.setattr(questdb_probe, "_run", fake_run)

    evidence = questdb_probe._verify_container_isolation(
        repository_root=_REPOSITORY_ROOT,
        container_id="container-id",
        project_name="probe",
        env={},
    )

    assert evidence["minimal_startup_capabilities"] == "PASS"


def test_probe_wraps_malformed_inspect_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            json.dumps(
                [
                    {
                        "Config": {"Env": ["DO_CHOWN=false"]},
                        "HostConfig": None,
                        "NetworkSettings": {
                            "Networks": {"probe_questdb_runtime": {}}
                        },
                    }
                ]
            ),
            json.dumps(
                [
                    {
                        "Labels": {
                            "com.docker.compose.project": "probe",
                            "com.docker.compose.network": "questdb_runtime",
                        },
                        "Internal": True,
                    }
                ]
            ),
        ]
    )

    def fake_run(*_args: Any, **_kwargs: Any) -> str:
        return next(responses)

    monkeypatch.setattr(questdb_probe, "_run", fake_run)

    with pytest.raises(questdb_probe.ProbeError) as exc_info:
        questdb_probe._verify_container_isolation(
            repository_root=_REPOSITORY_ROOT,
            container_id="container-id",
            project_name="probe",
            env={},
        )

    assert exc_info.value.code == "hardening_inspection_failed"
