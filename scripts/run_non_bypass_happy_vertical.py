#!/usr/bin/env python3
"""Run and certify one disposable, non-bypass III happy vertical.

The runner deliberately has no simulation mode: it signs only evidence emitted by
an in-network driver after the real Compose topology is admitted.  It persists a
redacted bundle and verification key, while every credential and transport key
lives only in the 0700 per-run scratch directory removed during cleanup.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jose import jwt

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.non-bypass-acceptance.yml"
FIXTURE = ROOT / "tests/acceptance/fixtures/opencli-proof"
FIXTURE_DIGEST = ROOT / "tests/acceptance/fixtures/opencli-proof.sha256"
try:
    from scripts.non_bypass_proof_contract import (
        PINNED_III,
        ProofRejected,
        assert_substitutions_rejected,
        delivery_payload_hash,
        validate_evidence,
    )
except ModuleNotFoundError:  # Direct script execution resolves its own directory first.
    from non_bypass_proof_contract import (  # type: ignore[no-redef]
        PINNED_III,
        ProofRejected,
        assert_substitutions_rejected,
        delivery_payload_hash,
        validate_evidence,
    )


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = True,
    timeout: float = 300,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=capture,
            check=False,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout}s: {' '.join(command)}") from exc
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RunLedger:
    run: str
    project: str
    scratch: Path
    artifact_dir: Path


def _write_private(path: Path, value: str | bytes) -> None:
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(0o600)


def _write_public(path: Path, value: str | bytes) -> None:
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(0o644)


def _make_secrets(ledger: RunLedger) -> dict[str, str]:
    ledger.scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ledger.scratch, 0o700)
    values = {
        name: secrets.token_urlsafe(36)
        for name in (
            "API_AUTH_TOKEN",
            "III_LIFECYCLE_TOKEN",
            "III_INGRESS_RECEIPT_SECRET",
            "ODP_QUERY_ADMIN_CREDENTIAL",
            "ODP_QUERY_CURSOR_SECRET",
        )
    }
    request_key = secrets.token_urlsafe(36)
    receipt_key = secrets.token_urlsafe(36)
    values.update(
        {
            "CONTROLLED_RECEIVER_REGISTRY_JSON": _canonical(
                {
                    "receiver-channel-proof": {
                        "url": "https://8.8.8.8:8000/api/v1/controlled-receiver/v2/deliver",
                        "receiverIdentity": "controlled-receiver-proof",
                        "credentialReference": "credential-reference-proof",
                        "requestKeyId": "request-key-proof",
                        "receiptKeyId": "receipt-key-proof",
                        "allowedNetworks": ["8.8.8.0/24"],
                        "durableStatus": "accepted",
                    }
                }
            ).decode(),
            "CONTROLLED_RECEIVER_CREDENTIALS_JSON": _canonical(
                {"credential-reference-proof": request_key}
            ).decode(),
            "CONTROLLED_RECEIVER_INBOUND_KEYS_JSON": _canonical(
                {"request-key-proof": request_key}
            ).decode(),
            "CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON": _canonical(
                {"receipt-key-proof": receipt_key}
            ).decode(),
        }
    )
    for name, value in values.items():
        _write_private(ledger.scratch / name.lower(), value)
    _run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=proof-ca",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            str(ledger.scratch / "ca-key.pem"),
            "-out",
            str(ledger.scratch / "ca.pem"),
        ]
    )
    _run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=proof-controlled-receiver",
            "-keyout",
            str(ledger.scratch / "receiver-key.pem"),
            "-out",
            str(ledger.scratch / "receiver.csr"),
        ]
    )
    _write_private(
        ledger.scratch / "receiver.ext",
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:proof-controlled-receiver,IP:8.8.8.8\n",
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
        ]
    )
    return values


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _make_identities(
    ledger: RunLedger,
    values: dict[str, str],
    *,
    governance_scope: Mapping[str, str] | None = None,
) -> None:
    """Create proof-admin identities and optional failure-governance identities."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "proof-jwks-v1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }
    _write_public(ledger.scratch / "jwks.json", _canonical({"keys": [jwk]}))
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    now = int(time.time())
    for role in ("PROPOSER", "REVIEWER"):
        subject = f"proof-{role.lower()}"
        values[f"PROOF_{role}_JWT"] = jwt.encode(
            {
                "sub": subject,
                "email": f"{subject}@proof.invalid",
                "name": subject,
                "preferred_username": subject,
                "iss": "http://proof-oidc",
                "aud": "proof-admin",
                "iat": now,
                "exp": now + 600,
            },
            private,
            algorithm="RS256",
            headers={"kid": jwk["kid"]},
        )
    values["BOOTSTRAP_ADMIN_TOKEN"] = secrets.token_urlsafe(36)
    if governance_scope is not None:
        if set(governance_scope) != {"workspace", "project", "workflow", "run"} or any(
            not isinstance(value, str) or not value for value in governance_scope.values()
        ):
            raise ValueError("governance scope must use the exact proof scope")
        scope = dict(governance_scope)
        for environment, role in (
            ("PROOF_BUNDLE_WRITER_JWT", "bundle-writer"),
            ("PROOF_KEY_ADMIN_JWT", "key-admin"),
        ):
            subject = f"proof-{role}"
            values[environment] = jwt.encode(
                {
                    "sub": subject,
                    "iss": "http://proof-oidc",
                    "aud": "proof-governance",
                    "iat": now,
                    "exp": now + 600,
                    "role": role,
                    "proof_scope": scope,
                },
                private,
                algorithm="RS256",
                headers={"kid": jwk["kid"]},
            )


def _compose(ledger: RunLedger, env: dict[str, str], *arguments: str) -> str:
    return _run(
        [
            "docker",
            "compose",
            "--parallel",
            "1",
            "-p",
            ledger.project,
            "-f",
            str(COMPOSE_FILE),
            *arguments,
        ],
        env=env,
    )


def _build_images(ledger: RunLedger, env: dict[str, str]) -> None:
    """Build each context serially before Compose starts it.

    Docker Desktop currently mishandles Compose's shared BuildKit session for
    this Windows checkout path; explicit, identically tagged builds preserve
    the real topology without relying on a host-port or mock fallback.
    """
    admin = ["docker", "build", "--target", "non-bypass-acceptance", "-t"]
    for service in ("proof-admin", "proof-driver", "proof-controlled-receiver"):
        _run([*admin, f"{ledger.project}-{service}", "."], env=env)
    for service, dockerfile in (
        ("proof-odp-ingest", "odp-rs/Dockerfile.ingest"),
        ("proof-odp-store", "odp-rs/Dockerfile.store"),
        ("proof-odp-query", "odp-rs/Dockerfile.query"),
    ):
        _run(
            ["docker", "build", "-t", f"{ledger.project}-{service}", "-f", dockerfile, "odp-rs"],
            env=env,
        )
    for service, dockerfile in (
        ("proof-collector", "iii/workers/collector-opencli/Dockerfile"),
        ("proof-bridge", "iii/workers/odp-ingest-bridge/Dockerfile"),
    ):
        _run(
            ["docker", "build", "-t", f"{ledger.project}-{service}", "-f", dockerfile, "iii"],
            env=env,
        )


def _migrate_fresh_admin(ledger: RunLedger, env: dict[str, str]) -> None:
    """Prove fresh and server-truncated PostgreSQL migration paths converge."""

    _compose(
        ledger,
        env,
        "up",
        "-d",
        "--wait",
        "proof-admin-postgres",
        "proof-receiver-postgres",
    )
    _compose(
        ledger,
        env,
        "run",
        "--rm",
        "--no-deps",
        "proof-admin",
        "/bin/sh",
        "-c",
        "PYTHONUNBUFFERED=1 alembic upgrade g5b6c7d8e9f0",
    )
    _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin-postgres",
        "psql",
        "-U",
        "proof",
        "-d",
        "proof_admin",
        "-c",
        "ALTER INDEX ix_evidence_batch_materialization_status "
        "RENAME TO ix_evidence_batch_materialization_manifests_materialization_sta",
    )
    _compose(
        ledger,
        env,
        "run",
        "--rm",
        "--no-deps",
        "proof-admin",
        "/bin/sh",
        "-c",
        "PYTHONUNBUFFERED=1 alembic upgrade head",
    )
    canonical = _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin-postgres",
        "psql",
        "-U",
        "proof",
        "-d",
        "proof_admin",
        "-At",
        "-c",
        "SELECT to_regclass('ix_evidence_batch_materialization_status')",
    ).strip()
    if canonical != "ix_evidence_batch_materialization_status":
        raise ProofRejected("PostgreSQL materialization index did not converge")
    _compose(
        ledger,
        env,
        "run",
        "--rm",
        "--no-deps",
        "proof-controlled-receiver",
        "/bin/sh",
        "-c",
        "PYTHONUNBUFFERED=1 alembic upgrade head",
    )


def _admit(ledger: RunLedger, env: dict[str, str], fixture_digest: str) -> None:
    _compose(ledger, env, "config", "--quiet")
    rendered = _compose(ledger, env, "config")
    if "ports:" in rendered or PINNED_III not in rendered or "8.8.8.0/24" not in rendered:
        raise ProofRejected("topology is not isolated, pinned, and port-free")
    _build_images(ledger, env)
    _migrate_fresh_admin(ledger, env)
    try:
        _compose(ledger, env, "up", "--no-build", "--wait")
    except RuntimeError as exc:
        try:
            logs = _compose(ledger, env, "logs", "--no-color")
        except RuntimeError:
            logs = "Compose logs unavailable"
        raise RuntimeError(f"{exc}\n{logs}") from exc
    cli = _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin",
        "/bin/sh",
        "-c",
        'test "$III_CLI_PATH" = /opt/iii/iii '
        '&& test "$III_URL" = ws://proof-iii:49134 '
        "&& /opt/iii/iii --version",
    ).strip()
    jwks = _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin",
        "curl",
        "-fsS",
        "http://proof-oidc/jwks.json",
    )
    if '"kid":"proof-jwks-v1"' not in jwks:
        raise ProofRejected("per-run OIDC JWKS is not reachable from Admin")
    if cli != "0.19.4":
        raise ProofRejected("copied III CLI is not exactly 0.19.4")
    actual_fixture = _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-collector",
        "sha256sum",
        "/proof/opencli-proof",
    ).split()[0]
    if actual_fixture != fixture_digest:
        raise ProofRejected("collector fixture digest mismatch")
    _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin",
        "/opt/iii/iii",
        "trigger",
        "--address",
        "proof-iii",
        "--port",
        "49134",
        "opencli::status",
    )


def _verify_receiver_tls(ledger: RunLedger, env: dict[str, str]) -> None:
    """Prove the pinned client uses this run's CA and rejects the system trust store."""

    _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin",
        "/bin/sh",
        "-ec",
        'test "$SSL_CERT_FILE" = /run/proof/ca.pem; '
        'curl -fsS --cacert "$SSL_CERT_FILE" https://8.8.8.8:8000/health; '
        "! curl -fsS --cacert /etc/ssl/certs/ca-certificates.crt https://8.8.8.8:8000/health",
    )


def _driver_evidence(ledger: RunLedger, env: dict[str, str]) -> dict[str, Any]:
    _verify_receiver_tls(ledger, env)
    try:
        output = _compose(
            ledger,
            env,
            "exec",
            "-T",
            "proof-driver",
            "python",
            "/app/tests/acceptance/non_bypass_vertical.py",
            "--run",
            ledger.run,
        )
    except RuntimeError as exc:
        try:
            logs = _compose(
                ledger,
                env,
                "logs",
                "--no-color",
                "proof-admin",
                "proof-controlled-receiver",
                "proof-collector",
                "proof-bridge",
                "proof-relay",
            )
        except RuntimeError:
            logs = "Compose logs unavailable"
        raise RuntimeError(f"{exc}\n{logs}") from exc
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ProofRejected("in-network driver did not produce JSON evidence") from exc


def _delivery_body(evidence: dict[str, Any]) -> bytes:
    decision = evidence["decision"]
    claims = decision["claims"]
    manifests = decision["manifests"]
    if decision["payloadHash"] != delivery_payload_hash(claims, manifests):
        raise ProofRejected("delivery payload hash does not match the driver decision DTO")
    payload = {
        "schemaVersion": "delivery-claim-manifest-v1",
        "claims": claims,
        "manifestHashes": [manifest["manifestHash"] for manifest in manifests],
    }
    return _canonical(
        {
            "version": "v2",
            "receiverIdentity": "controlled-receiver-proof",
            "operationId": decision["operationId"],
            "decisionHash": decision["decisionHash"],
            "payloadHash": decision["payloadHash"],
            "payload": payload,
        }
    )


def _attach_status_receipt(
    ledger: RunLedger, env: dict[str, str], evidence: dict[str, Any]
) -> None:
    body = _delivery_body(evidence)
    output = _compose(
        ledger,
        env,
        "exec",
        "-T",
        "proof-admin",
        "python",
        "/app/tests/acceptance/non_bypass_vertical.py",
        "receipt-status",
        "--body-base64",
        base64.b64encode(body).decode("ascii"),
    )
    try:
        signed_receipt = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ProofRejected("controlled receiver status did not return JSON receipt") from exc
    if not isinstance(signed_receipt, dict):
        raise ProofRejected("controlled receiver status did not return a receipt object")
    current = evidence["receiverReceipt"]
    receipt_hash = hashlib.sha256(_canonical(signed_receipt)).hexdigest()
    if (
        signed_receipt.get("receiptId") != current["receiptId"]
        or receipt_hash != current["receiptHash"]
    ):
        raise ProofRejected("controlled receiver status receipt conflicts with execution evidence")
    preimage = {
        key: signed_receipt[key]
        for key in (
            "version",
            "receiverIdentity",
            "operationId",
            "decisionHash",
            "payloadHash",
            "durableStatus",
            "receiptId",
            "timestamp",
        )
    }
    current.update(
        receiverIdentity=signed_receipt.get("receiverIdentity"),
        signedReceipt=signed_receipt,
        receiptPreimage=_canonical(preimage).decode("utf-8"),
    )


def _sign(ledger: RunLedger, evidence: dict[str, Any]) -> None:
    staging = ledger.artifact_dir.with_name(f".{ledger.artifact_dir.name}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700, parents=True)
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    payload = _canonical(evidence)
    signature = private.sign(payload)
    public.verify(signature, payload)
    (staging / "proof.json").write_bytes(payload + b"\n")
    (staging / "proof.json.sig").write_text(
        base64.b64encode(signature).decode() + "\n",
        encoding="ascii",
    )
    public_bytes = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    (staging / "proof.pub").write_text(
        base64.b64encode(public_bytes).decode() + "\n",
        encoding="ascii",
    )
    if ledger.artifact_dir.exists():
        raise ProofRejected("run-specific artifact directory already exists")
    staging.replace(ledger.artifact_dir)


def _cleanup(ledger: RunLedger, env: dict[str, str]) -> list[str]:
    """Attempt every cleanup independently and return diagnostics without raising."""

    diagnostics: list[str] = []

    def attempt(label: str, command: list[str]) -> str:
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            diagnostics.append(f"{label}: {exc}")
            return ""
        if completed.returncode:
            diagnostics.append(
                f"{label}: exit {completed.returncode}: "
                f"{(completed.stderr or completed.stdout).strip()[:500]}"
            )
        return completed.stdout

    compose = [
        "docker",
        "compose",
        "--parallel",
        "1",
        "-p",
        ledger.project,
        "-f",
        str(COMPOSE_FILE),
    ]
    attempt("compose down", [*compose, "down", "--volumes", "--remove-orphans"])
    resources = (
        ("containers", ["docker", "ps", "-aq"]),
        ("networks", ["docker", "network", "ls", "-q"]),
        ("volumes", ["docker", "volume", "ls", "-q"]),
    )
    label = f"com.docker.compose.project={ledger.project}"
    for kind, list_command in resources:
        identifiers = attempt(
            f"inspect labeled {kind}",
            [*list_command, "--filter", f"label={label}"],
        ).split()
        if identifiers:
            remove = {
                "containers": ["docker", "rm", "-f"],
                "networks": ["docker", "network", "rm"],
                "volumes": ["docker", "volume", "rm", "-f"],
            }[kind]
            attempt(f"remove labeled {kind}", [*remove, *identifiers])
        remaining = attempt(
            f"verify labeled {kind}",
            [*list_command, "--filter", f"label={label}"],
        ).split()
        if remaining:
            diagnostics.append(f"ledger-labeled {kind} remain: {', '.join(remaining)}")
    for service in (
        "proof-admin",
        "proof-driver",
        "proof-controlled-receiver",
        "proof-odp-ingest",
        "proof-odp-store",
        "proof-odp-query",
        "proof-collector",
        "proof-bridge",
    ):
        attempt(
            f"remove image {service}",
            ["docker", "image", "rm", "-f", f"{ledger.project}-{service}"],
        )
    shutil.rmtree(ledger.scratch, ignore_errors=True)
    if ledger.scratch.exists():
        diagnostics.append("per-run scratch directory remains")
    staging = ledger.artifact_dir.with_name(f".{ledger.artifact_dir.name}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    if staging.exists():
        diagnostics.append("partial artifact directory remains")
    return diagnostics


def run(artifact_dir: Path) -> Path:
    run_id = f"nbv-{uuid.uuid4().hex}"
    scratch = Path(tempfile.mkdtemp(prefix=f"{run_id}-"))
    ledger = RunLedger(
        run=run_id,
        project=run_id,
        scratch=scratch,
        artifact_dir=artifact_dir / run_id,
    )
    env = {
        **os.environ,
        "PROOF_SECRETS_DIR": str(scratch),
        "PROOF_RUN_ID": run_id,
        "COMPOSE_PARALLEL_LIMIT": "1",
    }
    failure: BaseException | None = None
    try:
        os.chmod(scratch, 0o700)
        artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.rmtree(ledger.artifact_dir, ignore_errors=True)
        shutil.rmtree(
            ledger.artifact_dir.with_name(f".{ledger.artifact_dir.name}.partial"),
            ignore_errors=True,
        )
        fixture_digest = FIXTURE_DIGEST.read_text(encoding="ascii").split()[0]
        if _sha256_file(FIXTURE) != fixture_digest:
            raise ProofRejected("committed fixture digest mismatch")
        values = _make_secrets(ledger)
        _make_identities(ledger, values)
        env.update(values)
        env["PROOF_FIXTURE_DIGEST"] = fixture_digest
        _admit(ledger, env, fixture_digest)
        evidence = _driver_evidence(ledger, env)
        _attach_status_receipt(ledger, env, evidence)
        validate_evidence(
            evidence,
            fixture_digest=fixture_digest,
            run=run_id,
            strict_receipt=True,
            receipt_keys_json=env["CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON"],
        )
        assert_substitutions_rejected(evidence, fixture_digest=fixture_digest, run=run_id)
        _sign(ledger, evidence)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_diagnostics = _cleanup(ledger, env)
        if cleanup_diagnostics:
            shutil.rmtree(ledger.artifact_dir, ignore_errors=True)
            message = "cleanup incomplete: " + " | ".join(cleanup_diagnostics)
            if failure is not None:
                failure.add_note(message)
            else:
                raise RuntimeError(message)
    return ledger.artifact_dir


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(run(args.artifact_dir))
    except Exception as exc:  # Do not write a signature on any admission or flow failure.
        print(f"non-bypass proof rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
