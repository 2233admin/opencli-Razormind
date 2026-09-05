#!/usr/bin/env python3
"""Run failure scenarios only when their real public-boundary facts are present.

The runner is deliberately unable to mint a result from Compose state, actuator
responses, or gate state. A scenario is signed only after the in-network driver
has written an authenticated scoped-public-API fact document.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from scripts.non_bypass_failure_proof_contract import canonical
from scripts.proof_bundle_governance import (
    GovernanceDeniedError,
    verify_public_artifacts,
)

from scripts.run_non_bypass_happy_vertical import (
    RunLedger as HappyRunLedger,
)
from scripts.run_non_bypass_happy_vertical import (
    _make_identities,
    _make_secrets,
)
from tests.acceptance.non_bypass_failure_matrix import (
    SCENARIO_ORDER,
    normalize_public_facts,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/acceptance/fixtures/opencli-failure-proof"
FIXTURE_DIGEST = ROOT / "tests/acceptance/fixtures/opencli-failure-proof.sha256"
PINNED_III = (
    "iiidev/iii:0.19.4@sha256:14ed48b463d8a2e0d3583512acf106b3514f406c5e9965a5854710ff936e1e86"
)


class FailureRunRejectedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScenarioLedger:
    scenario: str
    project: str
    scratch: Path
    artifact: Path


RECEIVER_PROFILE_SCENARIOS = frozenset({"receiver-recovery", "cancel-in-flight"})


def _scenario_compose_profiles(scenario: str) -> str:
    return "receiver" if scenario in RECEIVER_PROFILE_SCENARIOS else ""


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    input: str | None = None,
    timeout: int = 360,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FailureRunRejectedError(f"command timed out: {' '.join(command)}") from exc
    if completed.returncode:
        raise FailureRunRejectedError(
            completed.stderr.strip() or completed.stdout.strip() or "command failed"
        )
    return completed.stdout


def _fixture_digest() -> str:
    expected = FIXTURE_DIGEST.read_text("utf-8").split()[0]
    actual = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if actual != expected:
        raise FailureRunRejectedError("failure fixture digest is not pinned")
    return actual


CATALOG_NAMES = ("root", "collector", "bridge", "ingest", "store", "query")


def _catalog_digest(base: Path, overlay: Path) -> str:
    """Bind catalog names to every source file copied into any catalog image."""
    inputs = (
        ROOT / "Dockerfile",
        ROOT / ".dockerignore",
        ROOT / "pyproject.toml",
        ROOT / "backend",
        ROOT / "alembic.ini",
        ROOT / "entrypoint.sh",
        ROOT / "scripts/patch-opencli.js",
        ROOT / "scripts/install-agent.sh",
        ROOT / "scripts/proof_bundle_governance.py",
        ROOT / "scripts/proof_bundle_governance_http.py",
        ROOT / "scripts/non_bypass_failure_proof_contract.py",
        ROOT / "tests/acceptance/non_bypass_vertical.py",
        ROOT / "tests/acceptance/non_bypass_failure_matrix.py",
        ROOT / "tests/acceptance/non_bypass_failure_driver.py",
        ROOT / "tests/acceptance/fault_tools",
        FIXTURE,
        FIXTURE_DIGEST,
        ROOT / "iii/workers/collector-opencli/Dockerfile",
        ROOT / "iii/workers/collector-opencli/pyproject.toml",
        ROOT / "iii/workers/collector-opencli/src",
        ROOT / "iii/workers/odp-ingest-bridge/Dockerfile",
        ROOT / "iii/workers/odp-ingest-bridge/pyproject.toml",
        ROOT / "iii/workers/odp-ingest-bridge/src",
        ROOT / "iii/lib",
        ROOT / "odp-rs/Dockerfile.ingest",
        ROOT / "odp-rs/Dockerfile.store",
        ROOT / "odp-rs/Dockerfile.query",
        ROOT / "odp-rs/Cargo.toml",
        ROOT / "odp-rs/Cargo.lock",
        ROOT / "odp-rs/crates",
        base,
        overlay,
    )
    digest = hashlib.sha256()
    for input_path in inputs:
        files = (
            (input_path,)
            if input_path.is_file()
            else sorted(
                path
                for path in input_path.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
            )
        )
        for path in files:
            digest.update(path.resolve().relative_to(ROOT).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _catalog_images(digest: str) -> dict[str, str]:
    return {name: f"opencli-proof-{name}:{digest}" for name in CATALOG_NAMES}


def _catalog_build_commands(
    images: dict[str, str],
    *,
    root_cache_bust: str | None = None,
) -> tuple[tuple[str, list[str]], ...]:
    def build(image: str, dockerfile: str, context: str, *extra: str) -> list[str]:
        return [
            "docker",
            "buildx",
            "build",
            "--load",
            "--tag",
            image,
            *extra,
            "-f",
            dockerfile,
            context,
        ]

    root_extra = ["--target", "non-bypass-acceptance"]
    if root_cache_bust is not None:
        root_extra.extend(["--build-arg", f"PROOF_CATALOG_DIGEST={root_cache_bust}"])
    return (
        ("root", build(images["root"], "Dockerfile", ".", *root_extra)),
        (
            "collector",
            build(images["collector"], "iii/workers/collector-opencli/Dockerfile", "iii"),
        ),
        ("bridge", build(images["bridge"], "iii/workers/odp-ingest-bridge/Dockerfile", "iii")),
        ("ingest", build(images["ingest"], "odp-rs/Dockerfile.ingest", "odp-rs")),
        ("store", build(images["store"], "odp-rs/Dockerfile.store", "odp-rs")),
        ("query", build(images["query"], "odp-rs/Dockerfile.query", "odp-rs")),
    )


def _remaining(deadline: float) -> int:
    remaining = int(deadline - time.monotonic())
    if remaining <= 0:
        raise FailureRunRejectedError("catalog admission exceeded 120 seconds")
    return remaining


def _inspect_images(images: list[str], env: dict[str, str], *, timeout: int) -> dict[str, str]:
    result = {
        image: _run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image], env=env, timeout=timeout
        ).strip()
        for image in images
    }
    if any(not image_id.startswith("sha256:") for image_id in result.values()):
        raise FailureRunRejectedError("catalog image identity is not immutable")
    return result


def _build_catalog(env: dict[str, str], base: Path, overlay: Path) -> dict[str, Any]:
    """Build six content-addressed images once; scenarios never call build."""
    digest = _catalog_digest(base, overlay)
    images = _catalog_images(digest)
    deadline = time.monotonic() + 120
    # `docker build` uses Docker Desktop's broken session metadata path here.
    # Buildx with the desktop builder and `--load` is the verified replacement:
    # it produces daemon-inspectable IDs while retaining the shared deadline.
    builder_env = {**env, "PROOF_CATALOG_DIGEST": digest}
    for _name, command in _catalog_build_commands(images, root_cache_bust=digest):
        _run(command, env=builder_env, timeout=_remaining(deadline))
    catalog_ledger = ScenarioLedger("catalog", f"nbf-catalog-{digest[:12]}", ROOT, ROOT)
    catalog_env = {
        **builder_env,
        **_receiver_address_values(catalog_ledger.project, builder_env),
        "COMPOSE_PROFILES": "receiver",
    }
    configured = _compose(
        catalog_ledger,
        catalog_env,
        base,
        overlay,
        "config",
        "--images",
        timeout=_remaining(deadline),
    ).splitlines()
    if PINNED_III not in configured:
        raise FailureRunRejectedError("pinned III catalog image is absent")
    if not set(images.values()) <= set(configured):
        raise FailureRunRejectedError("overlay does not resolve to the complete catalog")
    image_ids = _inspect_images(configured, builder_env, timeout=_remaining(deadline))
    return {
        "digest": digest,
        "images": images,
        "imageIds": image_ids,
        "fixtureDigest": _fixture_digest(),
    }


def _compose(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
    *args: str,
    input: str | None = None,
    timeout: int = 360,
) -> str:
    return _run(
        ["docker", "compose", "-p", ledger.project, "-f", str(base), "-f", str(overlay), *args],
        env=env,
        input=input,
        timeout=timeout,
    )


def _receiver_address_values(
    project: str, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Derive the isolated public receiver /24 for one fresh Compose project.

    Docker assigns the first dynamic endpoint to .1; reserve it and pin the
    receiver proxy at .2 so Admin and control Admin can join this same network.
    """
    digest = hashlib.sha256(project.encode("utf-8")).digest()
    defaults = {
        "PROOF_RECEIVER_SUBNET": f"11.{digest[0]}.{digest[1]}.0/24",
        "PROOF_RECEIVER_IP": f"11.{digest[0]}.{digest[1]}.2",
        "PROOF_RECEIVER_GATEWAY": f"11.{digest[0]}.{digest[1]}.254",
    }
    overrides = os.environ if environment is None else environment
    values = {name: overrides.get(name, default) for name, default in defaults.items()}
    _validate_receiver_address_values(values)
    return values


def _validate_receiver_address_values(values: Mapping[str, str]) -> None:
    try:
        subnet = ipaddress.ip_network(values["PROOF_RECEIVER_SUBNET"], strict=True)
        receiver_ip = ipaddress.ip_address(values["PROOF_RECEIVER_IP"])
        gateway = ipaddress.ip_address(values["PROOF_RECEIVER_GATEWAY"])
    except (KeyError, ValueError) as exc:
        raise FailureRunRejectedError("receiver address tuple is invalid") from exc
    if (
        not isinstance(subnet, ipaddress.IPv4Network)
        or not isinstance(receiver_ip, ipaddress.IPv4Address)
        or not isinstance(gateway, ipaddress.IPv4Address)
        or subnet.prefixlen != 24
        or not receiver_ip.is_global
        or receiver_ip not in subnet
        or receiver_ip
        in {subnet.network_address, subnet.network_address + 1, subnet.broadcast_address}
        or gateway not in subnet
        or gateway in {subnet.network_address, subnet.broadcast_address}
        or gateway == receiver_ip
    ):
        raise FailureRunRejectedError(
            "receiver address tuple must use distinct global /24 host and gateway"
        )


def _configure_failure_receiver(ledger: ScenarioLedger, values: dict[str, str]) -> None:
    """Bind the scenario's isolated public-class receiver before Compose config."""
    _validate_receiver_address_values(values)
    ip = values["PROOF_RECEIVER_IP"]
    subnet = values["PROOF_RECEIVER_SUBNET"]
    credentials = json.loads(values["CONTROLLED_RECEIVER_CREDENTIALS_JSON"])
    inbound = json.loads(values["CONTROLLED_RECEIVER_INBOUND_KEYS_JSON"])
    receipts = json.loads(values["CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON"])
    credential_reference, request_key = next(iter(credentials.items()))
    request_key_id = next(iter(inbound))
    receipt_key_id = next(iter(receipts))
    values["CONTROLLED_RECEIVER_REGISTRY_JSON"] = json.dumps(
        {
            "receiver-channel-proof": {
                "url": f"https://{ip}:8000/api/v1/controlled-receiver/v2/deliver",
                "receiverIdentity": "controlled-receiver-proof",
                "credentialReference": credential_reference,
                "requestKeyId": request_key_id,
                "receiptKeyId": receipt_key_id,
                "allowedNetworks": [subnet],
                "durableStatus": "accepted",
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    (ledger.scratch / "controlled_receiver_registry_json").write_text(
        values["CONTROLLED_RECEIVER_REGISTRY_JSON"],
        encoding="utf-8",
    )
    os.chmod(ledger.scratch / "controlled_receiver_registry_json", 0o600)
    (ledger.scratch / "receiver.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName=DNS:proof-controlled-receiver,DNS:proof-delivery-proxy,IP:{ip}\n",
        encoding="utf-8",
    )
    _run(
        [
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(ledger.scratch / "receiver.csr"),
            "-CA",
            str(ledger.scratch / "ca.pem"),
            "-CAkey",
            str(ledger.scratch / "ca-key.pem"),
            "-CAcreateserial",
            "-extfile",
            str(ledger.scratch / "receiver.ext"),
            "-out",
            str(ledger.scratch / "receiver-cert.pem"),
        ],
        env={**os.environ},
    )


def _admit(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Verify a fresh tuple against the prebuilt immutable catalog."""
    _compose(ledger, env, base, overlay, "config", "--quiet", timeout=120)
    images = _compose(ledger, env, base, overlay, "config", "--images", timeout=120).splitlines()
    if PINNED_III not in images:
        raise FailureRunRejectedError("pinned III catalog image is absent")
    image_ids = _inspect_images(images, env, timeout=120)
    expected = catalog["imageIds"]
    if image_ids != expected:
        raise FailureRunRejectedError("scenario image catalog diverged from preflight")
    if _fixture_digest() != catalog["fixtureDigest"]:
        raise FailureRunRejectedError("scenario fixture digest diverged from preflight")
    report = {
        "scenario": ledger.scenario,
        "fixtureDigest": catalog["fixtureDigest"],
        "catalogDigest": catalog["digest"],
        "catalogImageIds": image_ids,
        "project": ledger.project,
    }
    # Admission report is intentionally unsigned and never becomes a bundle fact.
    ledger.artifact.mkdir(parents=True, exist_ok=True)
    (ledger.artifact / "admission.json").write_bytes(canonical(report))
    return report


def _facts_from_crash_after_ingest(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "-p",
        ledger.project,
        "-f",
        str(base),
        "-f",
        str(overlay),
        "exec",
        "-T",
        "proof-driver",
        "python",
        "tests/acceptance/non_bypass_failure_driver.py",
        "--scenario",
        ledger.scenario,
        "--run",
        ledger.project,
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def wait_for(name: str) -> None:
        path = ledger.artifact / "coordination" / f"{ledger.project}.{name}"
        deadline = time.monotonic() + 90
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        if not path.exists():
            process.kill()
            out, err = process.communicate(timeout=30)
            raise FailureRunRejectedError(
                err.strip() or out.strip() or f"crash driver missed {name}"
            )

    wait_for("arm-report-hold")
    _compose(
        ledger,
        env,
        base,
        overlay,
        "exec",
        "-T",
        "proof-driver",
        "curl",
        "-fsS",
        "-X",
        "POST",
        "-H",
        f"X-API-Token: {env['API_AUTH_TOKEN']}",
        "-H",
        "Content-Type: application/json",
        "-d",
        '{"mode":"hold"}',
        "http://proof-relay:8080/_gate/report",
        timeout=30,
    )
    (ledger.artifact / "coordination" / f"{ledger.project}.report-hold-armed").write_text(
        "armed", encoding="utf-8"
    )
    wait_for("ingress-observed")
    _compose(ledger, env, base, overlay, "stop", "proof-collector", timeout=30)
    (ledger.artifact / "coordination" / f"{ledger.project}.collector-stopped").write_text(
        "stopped", encoding="utf-8"
    )
    stdout, stderr = process.communicate(timeout=120)
    if process.returncode:
        raise FailureRunRejectedError(
            stderr.strip() or stdout.strip() or "crash-after-ingest driver failed"
        )
    return normalize_public_facts(json.loads(stdout))


def _facts_from_receiver_recovery(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
) -> dict[str, Any]:
    """Restart only the delivery boundary after all faulted sends are durable."""
    command = [
        "docker",
        "compose",
        "-p",
        ledger.project,
        "-f",
        str(base),
        "-f",
        str(overlay),
        "exec",
        "-T",
        "proof-driver",
        "python",
        "tests/acceptance/non_bypass_failure_driver.py",
        "--scenario",
        ledger.scenario,
        "--run",
        ledger.project,
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    signal = ledger.artifact / "coordination" / f"{ledger.project}.receiver-restart-ready"
    deadline = time.monotonic() + 180
    while not signal.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not signal.exists():
        process.kill()
        stdout, stderr = process.communicate(timeout=30)
        raise FailureRunRejectedError(
            stderr.strip() or stdout.strip() or "receiver recovery driver missed restart signal"
        )
    _compose(
        ledger,
        env,
        base,
        overlay,
        "restart",
        "proof-delivery-proxy",
        "proof-controlled-receiver",
        timeout=60,
    )
    readiness_deadline = time.monotonic() + 30
    while True:
        try:
            _compose(
                ledger,
                env,
                base,
                overlay,
                "exec",
                "-T",
                "proof-driver",
                "curl",
                "-fsS",
                "--cacert",
                "/run/proof/ca.pem",
                "https://proof-delivery-proxy:8000/health",
                timeout=10,
            )
            break
        except FailureRunRejectedError:
            if time.monotonic() >= readiness_deadline:
                process.kill()
                stdout, stderr = process.communicate(timeout=30)
                raise FailureRunRejectedError(
                    stderr.strip() or stdout.strip() or "delivery proxy did not recover"
                )
            time.sleep(0.2)
    (ledger.artifact / "coordination" / f"{ledger.project}.receiver-restarted").write_text(
        "restarted", encoding="utf-8"
    )
    stdout, stderr = process.communicate(timeout=180)
    if process.returncode:
        raise FailureRunRejectedError(
            stderr.strip() or stdout.strip() or "receiver recovery driver failed"
        )
    return normalize_public_facts(json.loads(stdout))


def _facts_from_driver(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
) -> dict[str, Any]:
    supported = {
        "admin-crash",
        "iii-unreachable",
        "no-report",
        "signed-zero",
        "ingest-redis-store-loss",
        "duplicate-dlq",
        "query-page-race",
        "graph-stale-auth-cas-retract",
        "amendment-decision-conflict",
        "receiver-recovery",
        "cancel-before-dispatch",
        "cancel-in-flight",
    }
    if ledger.scenario not in supported:
        raise FailureRunRejectedError(f"in-network driver is not implemented for {ledger.scenario}")
    command = [
        "docker",
        "compose",
        "-p",
        ledger.project,
        "-f",
        str(base),
        "-f",
        str(overlay),
        "exec",
        "-T",
        "proof-driver",
        "python",
        "tests/acceptance/non_bypass_failure_driver.py",
        "--scenario",
        ledger.scenario,
        "--run",
        ledger.project,
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ledger.scenario in {
        "ingest-redis-store-loss",
        "duplicate-dlq",
        "query-page-race",
        "graph-stale-auth-cas-retract",
        "amendment-decision-conflict",
        "cancel-before-dispatch",
        "cancel-in-flight",
    }:
        stdout, stderr = process.communicate(timeout=360)
        if process.returncode:
            raise FailureRunRejectedError(
                stderr.strip() or stdout.strip() or f"{ledger.scenario} driver failed"
            )
        return normalize_public_facts(json.loads(stdout))
    if ledger.scenario == "signed-zero":
        stdout, stderr = process.communicate(timeout=120)
        if process.returncode:
            raise FailureRunRejectedError(
                stderr.strip() or stdout.strip() or "signed-zero driver failed"
            )
        return normalize_public_facts(json.loads(stdout))
    signal_name = "iii-ready" if ledger.scenario == "iii-unreachable" else "submitted"
    signal = ledger.artifact / "coordination" / f"{ledger.project}.{signal_name}"
    deadline = time.monotonic() + 90
    while not signal.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not signal.exists():
        process.kill()
        _stdout, stderr = process.communicate(timeout=30)
        raise FailureRunRejectedError(
            f"{ledger.scenario} driver did not reach its public gate boundary: "
            + (stderr.strip() or _stdout.strip() or "no driver output")
        )
    if ledger.scenario == "admin-crash":
        _compose(ledger, env, base, overlay, "kill", "proof-admin", timeout=30)
        _compose(
            ledger,
            env,
            base,
            overlay,
            "exec",
            "-T",
            "proof-driver",
            "curl",
            "-fsS",
            "-X",
            "POST",
            "-H",
            f"X-API-Token: {env['API_AUTH_TOKEN']}",
            "-H",
            "Content-Type: application/json",
            "-d",
            '{"upstream":"control"}',
            "http://proof-relay:8080/_gate/callback-upstream",
            timeout=30,
        )
        release_name = "resume"
    elif ledger.scenario == "no-report":
        _compose(
            ledger,
            env,
            base,
            overlay,
            "exec",
            "-T",
            "proof-driver",
            "curl",
            "-fsS",
            "-X",
            "POST",
            "-H",
            f"X-API-Token: {env['API_AUTH_TOKEN']}",
            "-H",
            "Content-Type: application/json",
            "-d",
            '{"mode":"drop"}',
            "http://proof-relay:8080/_gate/report",
            timeout=30,
        )
        release_name = "resume"
    else:
        _run(
            [
                "docker",
                "network",
                "disconnect",
                f"{ledger.project}_proof-iii-admin",
                f"{ledger.project}-proof-admin-1",
            ],
            env=env,
            timeout=30,
        )
        release_name = "iii-release"
    (ledger.artifact / "coordination" / f"{ledger.project}.{release_name}").write_text(
        "released", encoding="utf-8"
    )
    stdout, stderr = process.communicate(timeout=120)
    if process.returncode:
        raise FailureRunRejectedError(
            stderr.strip() or stdout.strip() or "in-network admin-crash driver failed"
        )
    try:
        return normalize_public_facts(json.loads(stdout))
    except (json.JSONDecodeError, RuntimeError) as exc:
        raise FailureRunRejectedError(
            f"in-network driver facts failed normalization: {exc}"
        ) from exc


def _govern(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
    result: dict[str, Any],
    *,
    deadline: float,
) -> tuple[dict[str, Any], bytes, dict[str, Any], list[dict[str, Any]]]:
    """Delegate all key, envelope, signature, and audit authority in-network."""
    client = (
        "import json,os,sys,httpx\n"
        "base='http://proof-governance:8000/v1'\n"
        "payload=json.load(sys.stdin)\n"
        "writer={'Authorization':'Bearer '+os.environ['PROOF_BUNDLE_WRITER_JWT']}\n"
        "admin={'Authorization':'Bearer '+os.environ['PROOF_KEY_ADMIN_JWT']}\n"
        "with httpx.Client(timeout=20, trust_env=False) as http:\n"
        "  def call(method,path,headers,body=None):\n"
        "    response=http.request(method,base+path,headers=headers,json=body)\n"
        "    response.raise_for_status()\n"
        "    return response.json()\n"
        "  call('POST','/keys/bootstrap-active',admin,{})\n"
        "  public=call('GET','/public',writer)\n"
        "  created=call('POST','/bundles',writer,{'payload':payload})\n"
        "  artifact=created['record']['envelope']['artifactId']\n"
        "  if call('GET','/bundles/'+artifact,writer) != created:\n"
        "    raise RuntimeError('governance read did not preserve the record')\n"
        "  if call('POST','/bundles/'+artifact+'/verify',writer,{}).get('verified') is not True:\n"
        "    raise RuntimeError('governance verification failed')\n"
        "  audit=call('GET','/audit',writer)\n"
        "  if audit[-1].get('action') != 'audit.read':\n"
        "    raise RuntimeError('governance audit read failed')\n"
        "print(json.dumps({'saved':created,'public':public,'audit':audit},sort_keys=True,separators=(',',':')))\n"
    )
    if deadline <= time.monotonic():
        raise FailureRunRejectedError("scenario deadline expired before governance")
    stdout = _compose(
        ledger,
        env,
        base,
        overlay,
        "exec",
        "-T",
        "proof-driver",
        "python",
        "-c",
        client,
        input=canonical(result).decode("utf-8"),
        timeout=max(1, min(60, int(deadline - time.monotonic()))),
    )
    try:
        archived = json.loads(stdout)
        saved, public, audit = archived["saved"], archived["public"], archived["audit"]
        envelope = saved["record"]["envelope"]
        if (
            saved["record"]["payload"]["scenario"] != ledger.scenario
            or envelope["run"] != result["run"]
            or not isinstance(saved["signature"], str)
            or not isinstance(public, dict)
            or not isinstance(audit, list)
        ):
            raise ValueError("governance service response is incomplete")
        return saved, canonical(saved), public, audit
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FailureRunRejectedError("in-network governance response is invalid") from exc


def _cleanup_command(
    command: list[str], env: Mapping[str, str], *, deadline: float
) -> subprocess.CompletedProcess[str] | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _remove_unused_project_networks(
    ledger: ScenarioLedger, env: Mapping[str, str], *, deadline: float
) -> list[str]:
    """Delete only zero-endpoint networks owned by this failure-row project."""
    if not ledger.project.startswith("nbf-"):
        return []
    project_label = f"com.docker.compose.project={ledger.project}"
    listed = _cleanup_command(
        ["docker", "network", "ls", "--quiet", "--filter", f"label={project_label}"],
        env,
        deadline=deadline,
    )
    if listed is None or listed.returncode:
        return []
    removed: list[str] = []
    for network_id in listed.stdout.splitlines():
        network_id = network_id.strip()
        if not network_id:
            continue
        inspected = _cleanup_command(
            ["docker", "network", "inspect", network_id, "--format", "{{len .Containers}}"],
            env,
            deadline=deadline,
        )
        if inspected is None or inspected.returncode or inspected.stdout.strip() != "0":
            continue
        deleted = _cleanup_command(["docker", "network", "rm", network_id], env, deadline=deadline)
        if deleted is not None and not deleted.returncode:
            removed.append(network_id)
    return removed


def _cleanup(
    ledger: ScenarioLedger,
    env: dict[str, str],
    base: Path,
    overlay: Path,
) -> None:
    deadline = time.monotonic() + 60
    try:
        try:
            _compose(
                ledger,
                env,
                base,
                overlay,
                "down",
                "--volumes",
                "--remove-orphans",
                timeout=min(45, max(1, deadline - time.monotonic())),
            )
        except FailureRunRejectedError:
            pass
        _remove_unused_project_networks(ledger, env, deadline=deadline)
    finally:
        shutil.rmtree(ledger.scratch, ignore_errors=True)


def run_matrix(
    artifact_dir: Path,
    *,
    compose_file: Path,
    overlay_file: Path,
    scenario: str | None = None,
) -> list[Path]:
    matrix_started = time.monotonic()
    catalog = _build_catalog({**os.environ}, compose_file, overlay_file)
    results: list[Path] = []
    selected = (scenario,) if scenario else SCENARIO_ORDER
    for scenario in selected:
        if time.monotonic() - matrix_started >= 7200:
            raise FailureRunRejectedError("full matrix exceeded the 7200 second bound")
        run_id = f"nbf-{scenario}-{uuid.uuid4().hex[:12]}"
        ledger = ScenarioLedger(
            scenario,
            run_id,
            Path(tempfile.mkdtemp(prefix=f"{run_id}-")),
            artifact_dir / scenario / run_id,
        )
        os.chmod(ledger.scratch, 0o700)
        ledger.artifact.mkdir(mode=0o700, parents=True, exist_ok=False)
        base_ledger = HappyRunLedger(run_id, ledger.project, ledger.scratch, ledger.artifact)
        values = _make_secrets(base_ledger)
        values.update(_receiver_address_values(ledger.project))
        _configure_failure_receiver(ledger, values)
        _make_identities(
            base_ledger,
            values,
            governance_scope={
                "workspace": "failure",
                "project": ledger.project,
                "workflow": "failure-matrix",
                "run": ledger.project,
            },
        )
        env = {
            **os.environ,
            **values,
            "PROOF_SCENARIO": scenario,
            "PROOF_SECRETS_DIR": str(ledger.scratch),
            "PROOF_ARTIFACT_DIR": str(ledger.artifact),
            "PROOF_FIXTURE_DIGEST": _fixture_digest(),
            "COMPOSE_PARALLEL_LIMIT": "1",
            "COMPOSE_PROFILES": _scenario_compose_profiles(scenario),
            "PROOF_CATALOG_DIGEST": catalog["digest"],
        }
        started, deadline = time.monotonic(), time.monotonic() + 360
        saved: dict[str, Any] | None = None
        public: dict[str, Any] | None = None
        audit: list[dict[str, Any]] | None = None
        path = ledger.artifact / "proof.json"
        try:
            _admit(ledger, env, compose_file, overlay_file, catalog)
            if time.monotonic() >= deadline:
                raise FailureRunRejectedError("scenario deadline expired during admission")
            _compose(
                ledger,
                env,
                compose_file,
                overlay_file,
                "up",
                "--detach",
                "--wait",
                "--no-build",
                timeout=max(1, min(120, int(deadline - time.monotonic()))),
            )
            if scenario == "crash-after-ingest":
                gather = _facts_from_crash_after_ingest
            elif scenario == "receiver-recovery":
                gather = _facts_from_receiver_recovery
            else:
                gather = _facts_from_driver
            result = gather(ledger, env, compose_file, overlay_file)
            if time.monotonic() >= deadline:
                raise FailureRunRejectedError("scenario deadline expired before governance")
            saved, saved_bytes, public, audit = _govern(
                ledger, env, compose_file, overlay_file, result, deadline=deadline
            )
            if time.monotonic() >= deadline:
                raise FailureRunRejectedError("scenario deadline expired during governance")
            path.write_bytes(saved_bytes)
            (ledger.artifact / "governance-public.json").write_bytes(canonical(public))
            (ledger.artifact / "audit.json").write_bytes(canonical(audit))
        finally:
            _cleanup(ledger, env, compose_file, overlay_file)
        if saved is None or public is None or audit is None:
            raise FailureRunRejectedError("scenario issued no governed bundle")
        try:
            verify_public_artifacts(saved, public, audit)
        except GovernanceDeniedError as exc:
            raise FailureRunRejectedError("offline governance verification failed") from exc
        if time.monotonic() >= deadline:
            raise FailureRunRejectedError("scenario deadline expired before retention")
        results.append(path)
    if time.monotonic() - matrix_started >= 7200:
        raise FailureRunRejectedError("full matrix exceeded the 7200 second bound")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=ROOT / "docker-compose.non-bypass-acceptance.yml",
    )
    parser.add_argument(
        "--overlay-file",
        type=Path,
        default=ROOT / "docker-compose.non-bypass-failure.yml",
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIO_ORDER)
    args = parser.parse_args(argv)
    try:
        run_matrix(
            args.artifact_dir,
            compose_file=args.compose_file,
            overlay_file=args.overlay_file,
            scenario=args.scenario,
        )
    except FailureRunRejectedError as exc:
        print(f"failure matrix rejected: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
