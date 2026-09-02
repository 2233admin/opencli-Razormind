#!/usr/bin/env python3
"""Run the QuestDB protocol contract against a disposable Compose project."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

IMAGE = "questdb/questdb:10.0.1"
PROFILE = "analysis-runtime"
SERVICE = "questdb"
SQL_PATH = "/api/v1/sql/execute"
PROJECT_PREFIX = "opencli-questdb-probe-"
STARTUP_TIMEOUT_SECONDS = 120.0
QUERY_TIMEOUT_SECONDS = 10.0
EPHEMERAL_PORT_RANGE = "49152-65535"
EPHEMERAL_PORT_MIN = 49152
EPHEMERAL_PORT_MAX = 65535


class ProbeError(RuntimeError):
    """A bounded probe failure that is safe to print without transport details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = STARTUP_TIMEOUT_SECONDS,
    failure_code: str,
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(failure_code) from exc
    if result.returncode != 0:
        raise ProbeError(failure_code)
    return result.stdout.strip()


def _compose_command(compose_file: Path, project_name: str, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        project_name,
        "--profile",
        PROFILE,
        *arguments,
    ]


def _wait_until_healthy(
    *,
    repository_root: Path,
    compose_file: Path,
    project_name: str,
    env: dict[str, str],
) -> str:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    container_id = ""
    while time.monotonic() < deadline:
        container_id = _run(
            _compose_command(compose_file, project_name, "ps", "-q", SERVICE),
            cwd=repository_root,
            env=env,
            timeout=10,
            failure_code="container_lookup_failed",
        )
        if container_id:
            health = _run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
                cwd=repository_root,
                env=env,
                timeout=10,
                failure_code="container_inspection_failed",
            )
            if health == "healthy":
                return container_id
            if health == "unhealthy":
                raise ProbeError("container_unhealthy")
        time.sleep(1)
    raise ProbeError("container_health_timeout")


def _published_loopback_port(
    *,
    repository_root: Path,
    compose_file: Path,
    project_name: str,
    env: dict[str, str],
    target_port: int,
) -> int:
    mapping = _run(
        _compose_command(
            compose_file,
            project_name,
            "port",
            SERVICE,
            str(target_port),
        ),
        cwd=repository_root,
        env=env,
        timeout=10,
        failure_code="published_port_discovery_failed",
    )
    mappings = [line.strip() for line in mapping.splitlines() if line.strip()]
    if len(mappings) != 1:
        raise ProbeError("published_port_discovery_failed")
    host, separator, port_text = mappings[0].rpartition(":")
    try:
        published_port = int(port_text)
    except ValueError as exc:
        raise ProbeError("published_port_discovery_failed") from exc
    if separator != ":" or host.strip("[]") != "127.0.0.1":
        raise ProbeError("published_port_not_loopback")
    if not EPHEMERAL_PORT_MIN <= published_port <= EPHEMERAL_PORT_MAX:
        raise ProbeError("published_port_discovery_failed")
    return published_port


def _normalized_capabilities(value: Any) -> set[str] | None:
    if not isinstance(value, list):
        return None
    capabilities: set[str] = set()
    for capability in value:
        if not isinstance(capability, str):
            return None
        capabilities.add(capability.removeprefix("CAP_"))
    return capabilities


def _verify_container_isolation(
    *,
    repository_root: Path,
    container_id: str,
    project_name: str,
    env: dict[str, str],
) -> dict[str, str]:
    raw_container = _run(
        ["docker", "inspect", container_id],
        cwd=repository_root,
        env=env,
        timeout=10,
        failure_code="hardening_inspection_failed",
    )
    try:
        container = json.loads(raw_container)[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProbeError("hardening_inspection_failed") from exc
    if not isinstance(container, dict):
        raise ProbeError("hardening_inspection_failed")
    host_config = container.get("HostConfig")
    container_config = container.get("Config")
    network_settings = container.get("NetworkSettings")
    networks = (
        network_settings.get("Networks")
        if isinstance(network_settings, dict)
        else None
    )
    if (
        not isinstance(host_config, dict)
        or not isinstance(container_config, dict)
        or not isinstance(networks, dict)
    ):
        raise ProbeError("hardening_inspection_failed")
    network_names = list(networks)

    if len(network_names) != 1:
        raise ProbeError("network_isolation_failed")
    raw_network = _run(
        ["docker", "network", "inspect", network_names[0]],
        cwd=repository_root,
        env=env,
        timeout=10,
        failure_code="network_inspection_failed",
    )
    try:
        network = json.loads(raw_network)[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProbeError("network_inspection_failed") from exc
    if not isinstance(network, dict):
        raise ProbeError("network_inspection_failed")
    labels = network.get("Labels")
    if not isinstance(labels, dict):
        raise ProbeError("network_inspection_failed")
    if (
        labels.get("com.docker.compose.project") != project_name
        or labels.get("com.docker.compose.network") != "questdb_runtime"
        or network.get("Internal") is not True
    ):
        raise ProbeError("network_isolation_failed")

    tmpfs = host_config.get("Tmpfs", {})
    tmpfs_options = tmpfs.get("/tmp", "") if isinstance(tmpfs, dict) else ""
    cap_drop = _normalized_capabilities(host_config.get("CapDrop"))
    cap_add = _normalized_capabilities(host_config.get("CapAdd"))
    container_environment = container_config.get("Env")
    hardening_matches = (
        host_config.get("ReadonlyRootfs") is True
        and cap_drop == {"ALL"}
        and cap_add == {"SETGID", "SETUID"}
        and host_config.get("SecurityOpt") == ["no-new-privileges:true"]
        and host_config.get("PidsLimit") == 512
        and "size=64m" in tmpfs_options
        and "mode=1777" in tmpfs_options
        and isinstance(container_environment, list)
        and "DO_CHOWN=false" in container_environment
    )
    if not hardening_matches:
        raise ProbeError("container_hardening_failed")
    return {
        "capabilities_dropped": "PASS",
        "entrypoint_chown_disabled": "PASS",
        "internal_network": "PASS",
        "minimal_startup_capabilities": "PASS",
        "no_new_privileges": "PASS",
        "pids_limit": "PASS",
        "read_only_root": "PASS",
        "writable_tmpfs": "PASS",
    }


def _execute_sql(client: httpx.Client, statement: str) -> dict[str, Any]:
    try:
        response = client.get(
            SQL_PATH,
            params={"query": statement},
            headers={"Statement-Timeout": "10000"},
        )
    except httpx.HTTPError as exc:
        raise ProbeError("query_transport_failed") from exc
    if response.status_code != httpx.codes.OK:
        raise ProbeError("query_rejected")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProbeError("query_response_invalid") from exc
    if not isinstance(payload, dict) or "error" in payload:
        raise ProbeError("query_response_invalid")
    return payload


def _wait_for_dataset(
    client: httpx.Client,
    statement: str,
    expected: list[list[Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = _execute_sql(client, statement)
        if payload.get("dataset") == expected:
            return payload
        time.sleep(0.5)
    raise ProbeError("wal_apply_timeout")


def _verify_protocol(query_port: int, health_port: int, table_name: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    table_created = False
    with httpx.Client(
        base_url=f"http://127.0.0.1:{query_port}",
        timeout=QUERY_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as query_client:
        try:
            health_response = httpx.get(
                f"http://127.0.0.1:{health_port}",
                timeout=QUERY_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise ProbeError("health_transport_failed") from exc
        if (
            health_response.status_code != httpx.codes.OK
            or health_response.text.strip() != "Status: Healthy"
        ):
            raise ProbeError("health_response_invalid")
        evidence["health"] = "PASS"

        readiness = _execute_sql(query_client, "select 1 as ready")
        if readiness.get("dataset") != [[1]]:
            raise ProbeError("readiness_response_invalid")
        evidence["readiness"] = "PASS"

        try:
            create = _execute_sql(
                query_client,
                f"CREATE TABLE {table_name} ("
                "probe_id STRING, source_key STRING, metric_kind SYMBOL, "
                "value DOUBLE, event_ts TIMESTAMP"
                ") TIMESTAMP(event_ts) PARTITION BY DAY WAL "
                "DEDUP UPSERT KEYS(event_ts, probe_id, source_key)",
            )
            if create.get("ddl") != "OK":
                raise ProbeError("explicit_schema_create_failed")
            table_created = True
            evidence["explicit_schema"] = "PASS"

            _execute_sql(
                query_client,
                f"INSERT INTO {table_name} VALUES "
                "('probe-1','event-1','trace',1.0,'2026-09-01T00:00:00.000000Z'),"
                "('probe-1','event-2','metric',7.0,'2026-09-01T00:01:00.000000Z'),"
                "('probe-1','event-3','trace',100.0,'2026-09-01T01:00:00.000000Z')",
            )
            _execute_sql(
                query_client,
                f"INSERT INTO {table_name} VALUES "
                "('probe-1','event-1','trace',5.0,'2026-09-01T00:00:00.000000Z')",
            )

            aggregate = _wait_for_dataset(
                query_client,
                f"SELECT count() row_count, sum(value) total_value FROM {table_name}",
                [[3, 112.0]],
            )
            replacement = _wait_for_dataset(
                query_client,
                f"SELECT value FROM {table_name} "
                "WHERE probe_id = 'probe-1' AND source_key = 'event-1' "
                "AND event_ts = '2026-09-01T00:00:00.000000Z'",
                [[5.0]],
            )
            evidence["deterministic_duplicate"] = {
                "status": "PASS",
                "aggregate": aggregate["dataset"][0],
                "replacement": replacement["dataset"][0][0],
            }

            bounded = _execute_sql(
                query_client,
                f"SELECT count() row_count, sum(value) total_value FROM {table_name} "
                "WHERE event_ts >= '2026-09-01T00:00:00.000000Z' "
                "AND event_ts < '2026-09-01T00:02:00.000000Z'",
            )
            if bounded.get("dataset") != [[2, 12.0]]:
                raise ProbeError("bounded_aggregation_failed")
            evidence["bounded_aggregation"] = {
                "status": "PASS",
                "result": bounded["dataset"][0],
            }

            schema = _execute_sql(query_client, f"SELECT * FROM {table_name} LIMIT 1")
            expected_columns = [
                {"name": "probe_id", "type": "STRING"},
                {"name": "source_key", "type": "STRING"},
                {"name": "metric_kind", "type": "SYMBOL"},
                {"name": "value", "type": "DOUBLE"},
                {"name": "event_ts", "type": "TIMESTAMP"},
            ]
            if schema.get("columns") != expected_columns or schema.get("timestamp") != 4:
                raise ProbeError("designated_timestamp_failed")
            evidence["designated_timestamp"] = "PASS"
        finally:
            if table_created:
                _execute_sql(query_client, f"DROP TABLE {table_name}")
                remaining = _execute_sql(
                    query_client,
                    f"SELECT count() row_count FROM tables() "
                    f"WHERE table_name = '{table_name}'",
                )
                if remaining.get("dataset") != [[0]]:
                    raise ProbeError("table_cleanup_failed")
                evidence["table_cleanup"] = "PASS"
    return evidence


def _cleanup_project(
    *,
    repository_root: Path,
    compose_file: Path,
    project_name: str,
    env: dict[str, str],
) -> dict[str, str]:
    cleanup = {"containers": "UNKNOWN", "volumes": "UNKNOWN"}
    try:
        _run(
            _compose_command(
                compose_file,
                project_name,
                "down",
                "--volumes",
                "--remove-orphans",
                "--timeout",
                "10",
            ),
            cwd=repository_root,
            env=env,
            failure_code="compose_cleanup_failed",
        )
        containers = _run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.ID}}",
            ],
            cwd=repository_root,
            env=env,
            timeout=10,
            failure_code="container_cleanup_check_failed",
        )
        volumes = _run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.Name}}",
            ],
            cwd=repository_root,
            env=env,
            timeout=10,
            failure_code="volume_cleanup_check_failed",
        )
        cleanup["containers"] = "PASS" if not containers else "FAIL"
        cleanup["volumes"] = "PASS" if not volumes else "FAIL"
    except ProbeError:
        cleanup["containers"] = "FAIL"
        cleanup["volumes"] = "FAIL"
    return cleanup


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    compose_file = repository_root / "docker-compose.yml"
    project_name = f"{PROJECT_PREFIX}{uuid4().hex[:12]}"
    table_name = f"opencli_probe_{uuid4().hex[:12]}"
    evidence: dict[str, Any] = {
        "project": project_name,
        "image": IMAGE,
        "probe_family": "5 - Container exec / internal-port HTTP",
        "cost_class": "needs-container",
    }

    if shutil.which("docker") is None:
        evidence.update(result="FAIL", failure_code="docker_cli_unavailable")
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 1

    env = {
        **os.environ,
        "API_AUTH_TOKEN": "questdb-disposable-probe-api-token",
        "BOOTSTRAP_ADMIN_TOKEN": "questdb-disposable-probe-bootstrap-token",
        "COMPOSE_PROJECT_NAME": project_name,
        "QUESTDB_ANALYSIS_RUNTIME_ENABLED": "true",
        "QUESTDB_ANALYSIS_RUNTIME_URL": "http://questdb:9000",
        "QUESTDB_ANALYSIS_RUNTIME_HEALTH_URL": "http://questdb:9003",
        "QUESTDB_HTTP_PORT": EPHEMERAL_PORT_RANGE,
        "QUESTDB_HEALTH_PORT": EPHEMERAL_PORT_RANGE,
        "SECRET_KEY": "questdb-disposable-probe-secret-at-least-32-characters",
    }

    failure_code: str | None = None
    try:
        _run(
            _compose_command(
                compose_file,
                project_name,
                "up",
                "-d",
                "--no-build",
                SERVICE,
            ),
            cwd=repository_root,
            env=env,
            failure_code="compose_start_failed",
        )
        container_id = _wait_until_healthy(
            repository_root=repository_root,
            compose_file=compose_file,
            project_name=project_name,
            env=env,
        )
        actual_image = _run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", container_id],
            cwd=repository_root,
            env=env,
            timeout=10,
            failure_code="image_inspection_failed",
        )
        if actual_image != IMAGE:
            raise ProbeError("image_pin_mismatch")
        evidence["image_pin"] = "PASS"
        evidence["isolation"] = _verify_container_isolation(
            repository_root=repository_root,
            container_id=container_id,
            project_name=project_name,
            env=env,
        )
        query_port = _published_loopback_port(
            repository_root=repository_root,
            compose_file=compose_file,
            project_name=project_name,
            env=env,
            target_port=9000,
        )
        health_port = _published_loopback_port(
            repository_root=repository_root,
            compose_file=compose_file,
            project_name=project_name,
            env=env,
            target_port=9003,
        )
        if query_port == health_port:
            raise ProbeError("published_port_collision")
        evidence["docker_assigned_loopback_ports"] = "PASS"
        evidence.update(_verify_protocol(query_port, health_port, table_name))
    except (ProbeError, KeyboardInterrupt) as exc:
        failure_code = exc.code if isinstance(exc, ProbeError) else "interrupted"
    finally:
        evidence["cleanup"] = _cleanup_project(
            repository_root=repository_root,
            compose_file=compose_file,
            project_name=project_name,
            env=env,
        )

    cleanup_passed = evidence["cleanup"] == {"containers": "PASS", "volumes": "PASS"}
    if failure_code is None and cleanup_passed:
        evidence["result"] = "PASS"
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0

    evidence["result"] = "FAIL"
    evidence["failure_code"] = failure_code or "cleanup_failed"
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 1


if __name__ == "__main__":
    sys.exit(main())
