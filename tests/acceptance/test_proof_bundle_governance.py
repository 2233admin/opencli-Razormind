from __future__ import annotations

import base64
import contextlib
import hashlib
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts/proof_bundle_governance.py"
spec = importlib.util.spec_from_file_location("proof_bundle_governance", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _base64url_integer(value: int) -> str:
    return (
        base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))
        .rstrip(b"=")
        .decode("ascii")
    )


@contextlib.contextmanager
def _authenticated_governance_client(tmp_path: Path, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test-jwks-key",
                "n": _base64url_integer(public.n),
                "e": _base64url_integer(public.e),
                "alg": "RS256",
                "use": "sig",
            }
        ]
    }

    class JWKSHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/jwks.json":
                self.send_error(404)
                return
            encoded = json.dumps(jwks).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), JWKSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "PROOF_GOVERNANCE_JWKS_URL",
        f"http://127.0.0.1:{server.server_address[1]}/jwks.json",
    )
    monkeypatch.setenv("PROOF_GOVERNANCE_ISSUER", "http://proof-oidc")
    monkeypatch.setenv("PROOF_GOVERNANCE_AUDIENCE", "proof-governance")
    http_spec = importlib.util.spec_from_file_location(
        "proof_bundle_governance_http_test",
        ROOT / "scripts/proof_bundle_governance_http.py",
    )
    assert http_spec and http_spec.loader
    http_module = importlib.util.module_from_spec(http_spec)
    sys.modules[http_spec.name] = http_module
    http_spec.loader.exec_module(http_module)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    def token(
        role: str,
        scope: dict[str, str],
        *,
        audience: str = "proof-governance",
        subject: str | None = None,
    ) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": subject or f"proof-{role}",
                "iss": "http://proof-oidc",
                "aud": audience,
                "iat": now,
                "exp": now + 300,
                "role": role,
                "proof_scope": scope,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-jwks-key"},
        )

    try:
        yield TestClient(http_module.build_app(tmp_path)), token
    finally:
        server.shutdown()
        thread.join()


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _result() -> dict:
    return {
        "schemaVersion": "ScenarioResultV1",
        "scenario": "iii-unreachable",
        "run": "run-1",
        "fault": "disconnect",
        "actuator": {
            "name": "proof-public-driver",
            "invocationHash": _hash("actuator"),
        },
        "correlation": {
            "commandId": "command-1",
            "attemptId": "attempt-1",
            "workflowRunId": "workflow-run-1",
            "hashes": {"command": _hash("command")},
        },
        "collection": {
            "blockingStage": "bridge_unavailable",
            "recoveryAction": "retry",
            "sideEffectUncertainty": True,
        },
        "materialization": {
            "status": "unknown",
            "blocker": "none",
            "recoveryAction": "none",
            "manifestHash": None,
            "reconciliationRevision": None,
            "pageSnapshotAsOf": None,
        },
        "graph": {
            "pin": None,
            "sequence": None,
            "readBlocker": "none",
            "mutationStatus": "none",
        },
        "delivery": {
            "state": "none",
            "outcome": "none",
            "attemptCount": 0,
            "receiptHash": None,
            "reconciliation": "none",
        },
        "forbiddenFacts": {
            "adminCreatedFallback": False,
            "lateEffectAbsenceClaim": False,
            "containerAuthority": False,
            "pageFinality": False,
        },
        "redactionProfile": "failure-v1",
        "timing": {
            "startedAt": 100,
            "completedAt": 101,
            "deadlineSeconds": 360,
        },
    }


def _store(tmp_path: Path, clock: list[int]):
    store = module.ProofBundleStore(tmp_path, now=lambda: clock[0])
    scope = {"workspace": "w", "project": "p", "workflow": "f", "run": "r"}
    admin = module.Principal("admin", "key-admin", scope)
    writer = module.Principal("writer", "bundle-writer", scope)
    store.bootstrap_active(admin, key_id="k1", not_before=1, not_after=1000)
    return store, admin, writer


def _envelope(store, writer, result, now: int, expires: int = 200) -> dict:
    artifact_id = "artifact-1"
    result["run"] = writer.scope["run"]
    unsigned = {name: value for name, value in result.items() if name != "governance"}
    result["governance"] = {
        "artifactId": artifact_id,
        "contentHash": module.content_hash(unsigned),
        "keyId": "k1",
        "trustRootFingerprint": store.trust_root_fingerprint,
    }
    return {
        "governanceSchemaVersion": module.SCHEMA_VERSION,
        "artifactId": artifact_id,
        "scenarioId": result["scenario"],
        "run": result["run"],
        "contentHash": module.digest(result),
        "sourceSchemaVersion": "ScenarioResultV1",
        "createdAt": now,
        "expiresAt": expires,
        "retentionClass": "failure",
        "retentionPolicyVersion": "v1",
        "scope": dict(writer.scope),
        "redactionProfile": "failure-v1",
        "signatureAlgorithm": "Ed25519",
        "keyId": "k1",
    }


def test_signed_immutable_bundle_and_audit_chain(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    result = _result()
    saved = store.create(
        writer,
        artifact_id="artifact-1",
        payload=result,
        envelope=_envelope(store, writer, result, 100),
    )
    assert saved["record"]["envelope"]["contentHash"] == module.digest(result)
    assert store.verify(writer, artifact_id="artifact-1")["verified"] is True
    assert store.read(writer, artifact_id="artifact-1") == saved
    records = store.read_audit(writer)
    assert {record["action"] for record in records} >= {
        "bundle.create",
        "bundle.verify",
        "bundle.read",
        "audit.read",
    }
    changed = _result()
    changed["fault"] = "changed"
    with pytest.raises(module.GovernanceDeniedError):
        store.create(
            writer,
            artifact_id="artifact-1",
            payload=changed,
            envelope=_envelope(store, writer, changed, 100),
        )


def test_bootstrap_requires_a_currently_valid_active_key(tmp_path: Path):
    clock = [100]
    scope = {"workspace": "w", "project": "p", "workflow": "f", "run": "r"}
    store = module.ProofBundleStore(tmp_path, now=lambda: clock[0])
    admin = module.Principal("admin", "key-admin", scope)
    with pytest.raises(module.GovernanceDeniedError):
        store.bootstrap_active(admin, key_id="future", not_before=101, not_after=1000)
    with pytest.raises(module.GovernanceDeniedError):
        store.bootstrap_active(admin, key_id="expired", not_before=1, not_after=100)


def test_public_artifacts_verify_offline_after_service_state_is_removed(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    result = _result()
    result["run"] = "r"
    saved = store.create(
        writer,
        artifact_id="artifact-1",
        payload=result,
        envelope=_envelope(store, writer, result, 100),
    )
    public = store.public_metadata(writer)
    audit = store.read_audit(writer)
    module.verify_public_artifacts(saved, public, audit)
    assert b"BEGIN PRIVATE KEY" not in json.dumps(public).encode()


def test_writer_cannot_administer_keys_and_denial_is_audited(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    with pytest.raises(module.GovernanceDeniedError):
        store.stage_next(writer, key_id="k2", not_before=101, not_after=1000)
    assert store.read_audit(writer)[-2]["outcome"] == "denied"


def test_tamper_and_expiry_fail_closed_without_certificate(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    result = _result()
    store.create(
        writer,
        artifact_id="artifact-1",
        payload=result,
        envelope=_envelope(store, writer, result, 100, 101),
    )
    clock[0] = 102
    for operation in (
        lambda: store.verify(writer, artifact_id="artifact-1"),
        lambda: store.read(writer, artifact_id="artifact-1"),
    ):
        with pytest.raises(module.GovernanceDeniedError) as expired:
            operation()
        assert expired.value.status_code == 410
    tombstone = json.loads(next(tmp_path.rglob("tombstone.json")).read_text())
    assert set(tombstone) == {
        "artifactId",
        "contentHash",
        "keyId",
        "retentionClass",
        "retentionPolicyVersion",
        "expiresAt",
    }
    assert not list(tmp_path.rglob("record.json"))
    assert "signature" not in json.dumps(tombstone)


def test_key_namespace_survives_restart_without_private_artifact_bytes(tmp_path: Path):
    clock = [100]
    artifact_root, key_root = tmp_path / "artifacts", tmp_path / "keys"
    scope = {"workspace": "w", "project": "p", "workflow": "f", "run": "r"}
    admin = module.Principal("admin", "key-admin", scope)
    writer = module.Principal("writer", "bundle-writer", scope)
    store = module.ProofBundleStore(artifact_root, key_root=key_root, now=lambda: clock[0])
    first = store.bootstrap_active(admin, key_id="k1", not_before=1, not_after=1000)
    result = _result()
    result["run"] = "r"
    store.create(
        writer,
        artifact_id="artifact-1",
        payload=result,
        envelope=_envelope(store, writer, result, 100),
    )
    store.stage_next(admin, key_id="k2", not_before=100, not_after=1000)
    store.promote(admin, key_id="k2")
    store.retire(admin, key_id="k1")
    store.revoke(admin, key_id="k2")

    restarted = module.ProofBundleStore(artifact_root, key_root=key_root, now=lambda: clock[0])
    assert restarted.trust_root_fingerprint == store.trust_root_fingerprint
    assert restarted.verify(writer, artifact_id="artifact-1")["keyId"] == first.key_id
    assert restarted._active_key is None
    assert restarted._keys["k1"].retired_at == 100
    assert restarted._keys["k2"].revoked_at == 100
    assert not list(artifact_root.rglob("*.private"))
    assert not any(
        b"BEGIN PRIVATE KEY" in path.read_bytes()
        for path in artifact_root.rglob("*")
        if path.is_file()
    )
    if os.name != "nt":
        assert (key_root / "audit-root.private").stat().st_mode & 0o777 == 0o600
        assert (key_root / "lifecycle.json").stat().st_mode & 0o777 == 0o600
        assert (key_root / "bundle-keys" / "k1.private").stat().st_mode & 0o777 == 0o600
    assert not (key_root / "bundle-keys" / "k2.private").exists()
    assert restarted.read_audit(writer)[-1]["action"] == "audit.read"


def test_concurrent_http_creates_preserve_a_continuous_audit_chain(tmp_path: Path, monkeypatch):
    scope = {"workspace": "w", "project": "p", "workflow": "f", "run": "run-1"}
    with _authenticated_governance_client(tmp_path, monkeypatch) as (client, token):
        writer = {"Authorization": f"Bearer {token('bundle-writer', scope)}"}
        admin = {"Authorization": f"Bearer {token('key-admin', scope)}"}
        assert client.post("/v1/keys/bootstrap-active", headers=admin, json={}).status_code == 200

        def create(index: int) -> int:
            payload = _result()
            payload["correlation"]["commandId"] = f"command-{index}"
            return client.post("/v1/bundles", headers=writer, json={"payload": payload}).status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(create, range(16))) == [200] * 16
        audit = client.get("/v1/audit", headers=writer)
        assert audit.status_code == 200
        previous = "0" * 64
        for item in audit.json():
            assert item["previousHash"] == previous
            previous = module.digest(item)


def test_audit_tampering_is_detected_before_audit_read_is_recorded(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    audit = next(tmp_path.rglob("audit.jsonl"))
    audit.write_text(
        audit.read_text().replace(
            '"action":"key.bootstrap-active"',
            '"action":"key.bootstrap-tampered"',
        )
    )
    with pytest.raises(module.GovernanceDeniedError):
        store.read_audit(writer)
    appended = [json.loads(line) for line in audit.read_text().splitlines()]
    assert appended[-1]["action"] == "audit.read"
    assert appended[-1]["outcome"] == "denied"


def test_secret_bearing_input_is_rejected_and_never_written(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    result = _result()
    result["privateKey"] = "not allowed"
    with pytest.raises(module.GovernanceDeniedError):
        store.create(
            writer,
            artifact_id="artifact-1",
            payload=result,
            envelope=_envelope(store, writer, result, 100),
        )
    assert not list(tmp_path.rglob("record.json"))
    persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert b"privateKey" not in persisted
    assert b"not allowed" not in persisted


def test_no_port_http_surface_exposes_only_authenticated_governance(tmp_path: Path):
    clock = [100]
    store, _admin, writer = _store(tmp_path, clock)
    app = module.create_app(store, lambda _request: writer)
    client = TestClient(app)
    assert client.get("/v1/trust-root").status_code == 200
    result = _result()
    result["run"] = "r"
    response = client.post("/v1/bundles", json={"payload": result})
    assert response.status_code == 200
    artifact_id = response.json()["record"]["envelope"]["artifactId"]
    assert client.post(f"/v1/bundles/{artifact_id}/verify", json={}).json()["verified"] is True


def test_http_jwks_authentication_lifecycle_and_redacted_denials(tmp_path: Path, monkeypatch):
    scope = {"workspace": "w", "project": "p", "workflow": "f", "run": "run-1"}
    with _authenticated_governance_client(tmp_path, monkeypatch) as (client, token):
        writer = {"Authorization": f"Bearer {token('bundle-writer', scope)}"}
        admin = {"Authorization": f"Bearer {token('key-admin', scope)}"}
        wrong_subject = client.get(
            "/v1/trust-root",
            headers={
                "Authorization": (
                    f"Bearer {token('bundle-writer', scope, subject='proof-key-admin')}"
                )
            },
        )
        assert wrong_subject.status_code == 401

        role_denial = client.post("/v1/keys/bootstrap-active", headers=writer, json={})
        assert role_denial.status_code == 403
        assert role_denial.json() == {"detail": "request denied"}
        assert client.post("/v1/keys/bootstrap-active", headers=admin, json={}).status_code == 200
        trust_root = client.get("/v1/trust-root", headers=writer)
        assert trust_root.status_code == 200

        created = client.post("/v1/bundles", headers=writer, json={"payload": _result()})
        assert created.status_code == 200
        saved = created.json()
        artifact_id = saved["record"]["envelope"]["artifactId"]
        assert saved["record"]["envelope"]["createdAt"] <= int(time.time())
        assert saved["record"]["envelope"]["keyId"].startswith("bundle-")
        assert (
            saved["record"]["payload"]["governance"]["trustRootFingerprint"]
            == trust_root.json()["fingerprint"]
        )
        assert client.get(f"/v1/bundles/{artifact_id}", headers=writer).json() == saved
        assert (
            client.post(f"/v1/bundles/{artifact_id}/verify", headers=writer, json={}).json()[
                "verified"
            ]
            is True
        )
        audit = client.get("/v1/audit", headers=writer)
        assert audit.status_code == 200
        allowed_creates = [
            item
            for item in audit.json()
            if item["action"] == "bundle.create" and item["outcome"] == "allowed"
        ]
        assert len(allowed_creates) == 1
        denied_bootstraps = [
            item
            for item in audit.json()
            if item["action"] == "key.bootstrap-active" and item["outcome"] == "denied"
        ]
        assert len(denied_bootstraps) == 1

        now = int(time.time())
        staged = client.post(
            "/v1/keys/stage-next",
            headers=admin,
            json={"key_id": "next-key", "not_before": now - 1, "not_after": now + 300},
        )
        assert staged.status_code == 200
        assert (
            client.post("/v1/keys/promote", headers=admin, json={"key_id": "next-key"}).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/keys/retire",
                headers=admin,
                json={"key_id": saved["record"]["envelope"]["keyId"]},
            ).status_code
            == 200
        )
        assert (
            client.post("/v1/keys/revoke", headers=admin, json={"key_id": "next-key"}).status_code
            == 200
        )

        mismatched_scope = dict(scope, run="different-run")
        scope_denial = client.post(
            "/v1/bundles",
            headers={"Authorization": f"Bearer {token('bundle-writer', mismatched_scope)}"},
            json={"payload": _result()},
        )
        assert scope_denial.status_code == 422
        assert scope_denial.json() == {"detail": "request denied"}

        record_path = next(tmp_path.rglob("record.json"))
        tampered = json.loads(record_path.read_text())
        tampered["record"]["payload"]["fault"] = "tampered"
        record_path.write_text(json.dumps(tampered))
        tamper = client.post(f"/v1/bundles/{artifact_id}/verify", headers=writer, json={})
        assert tamper.status_code == 409
        assert tamper.json() == {"detail": "request denied"}
        assert client.get(f"/v1/bundles/{artifact_id}", headers=writer).status_code == 409

        before_invalid = b"".join(path.read_bytes() for path in tmp_path.rglob("audit.jsonl"))
        invalid = client.get("/v1/audit", headers={"Authorization": "Bearer definitely-not-a-jwt"})
        assert invalid.status_code == 401
        assert invalid.json() == {"detail": "authentication denied"}
        after_invalid = b"".join(path.read_bytes() for path in tmp_path.rglob("audit.jsonl"))
        assert after_invalid == before_invalid
        persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
        assert b"BEGIN PRIVATE KEY" not in persisted
        assert b"definitely-not-a-jwt" not in persisted
