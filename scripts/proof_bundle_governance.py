#!/usr/bin/env python3
"""Fail-closed durable governance for redacted acceptance proof bundles.

This module is intentionally a release-harness service, not an Admin API.  It
keeps signing material outside the artifact store and exposes only public
fingerprints and signed redacted metadata through its HTTP adapter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from functools import wraps
from threading import RLock
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import Depends, FastAPI, HTTPException, Request

from scripts.non_bypass_failure_proof_contract import content_hash, validate

SCHEMA_VERSION = "ProofBundleGovernanceV1"
BUNDLE_SCHEMA = "ScenarioResultV1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?:bearer|token|secret|password|credential|private|transport|receiver.*key)",
    re.I,
)
_RAW_LOCATION = re.compile(
    r"^(?:https?|file)://|(?:^|[\\/])(?:tmp|var|proof-artifacts)(?:[\\/]|$)", re.I
)
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ENVELOPE_FIELDS = frozenset(
    {
        "governanceSchemaVersion",
        "artifactId",
        "scenarioId",
        "run",
        "contentHash",
        "sourceSchemaVersion",
        "createdAt",
        "expiresAt",
        "retentionClass",
        "retentionPolicyVersion",
        "scope",
        "redactionProfile",
        "signatureAlgorithm",
        "keyId",
    }
)
AUDIT_FIELDS = frozenset(
    {
        "auditSchemaVersion",
        "id",
        "at",
        "actor",
        "action",
        "artifactId",
        "contentHash",
        "scopeHash",
        "outcome",
        "reason",
        "previousHash",
        "keyId",
        "signature",
    }
)


class GovernanceDeniedError(RuntimeError):
    """An invalid governance operation which issued no certificate."""

    def __init__(self, reason: str, status_code: int = 403) -> None:
        super().__init__(reason)
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    scope: Mapping[str, str]


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    public_key: str
    not_before: int
    not_after: int
    revoked_at: int | None = None
    retired_at: int | None = None


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def fingerprint(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise GovernanceDeniedError("invalid encoded public material", 422) from exc


def _public_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def _assert_redacted(value: Any, path: str = "bundle") -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            if _SECRET.search(str(name)):
                raise GovernanceDeniedError(
                    f"secret-bearing field is forbidden at {path}.{name}", 422
                )
            _assert_redacted(child, f"{path}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_redacted(child, f"{path}[{index}]")
    elif isinstance(value, str) and (
        "-----BEGIN" in value or value.lower().startswith("bearer ") or _RAW_LOCATION.search(value)
    ):
        raise GovernanceDeniedError(f"private material is forbidden at {path}", 422)


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(self: "ProofBundleStore", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class ProofBundleStore:
    """Append-only audit and immutable bundle store, scoped per scenario."""

    def __init__(
        self,
        root: Path,
        *,
        key_root: Path | None = None,
        audit_private_key: Ed25519PrivateKey | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.key_root = key_root
        self._now = now or (lambda: int(time.time()))
        self._lock = RLock()
        self._key_private: dict[str, Ed25519PrivateKey] = {}
        self._keys: dict[str, SigningKey] = {}
        self._active_key: str | None = None
        if key_root is None:
            self._audit_private = audit_private_key or Ed25519PrivateKey.generate()
        else:
            self.key_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.key_root, 0o700)
            self._audit_private = self._load_audit_private(audit_private_key)
            self._load_lifecycle()
        self._audit_public = _public_bytes(self._audit_private)
        self._audit_key_id = f"audit-root-{fingerprint(self._audit_public)[:16]}"

    def _secret_path(self, name: str) -> Path:
        if self.key_root is None:
            raise RuntimeError("governance key namespace is not configured")
        return self.key_root / name

    def _write_secret(self, path: Path, value: bytes) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(value)
            temporary = Path(handle.name)
        os.replace(temporary, path)
        os.chmod(path, 0o600)

    def _load_audit_private(self, supplied: Ed25519PrivateKey | None) -> Ed25519PrivateKey:
        path = self._secret_path("audit-root.private")
        if path.exists():
            try:
                private = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
            except (OSError, ValueError) as exc:
                raise RuntimeError("governance audit root is unreadable") from exc
            if supplied is not None and _public_bytes(private) != _public_bytes(supplied):
                raise RuntimeError("governance audit root does not match supplied key")
            return private
        private = supplied or Ed25519PrivateKey.generate()
        self._write_secret(
            path,
            private.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
        )
        return private

    def _lifecycle_path(self) -> Path:
        return self._secret_path("lifecycle.json")

    def _bundle_key_path(self, key_id: str) -> Path:
        return self._secret_path("bundle-keys") / f"{key_id}.private"

    def _load_lifecycle(self) -> None:
        path = self._lifecycle_path()
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text("utf-8"))
            keys = state["keys"]
            active_key = state["activeKey"]
            if (
                set(state) != {"activeKey", "keys"}
                or not isinstance(keys, dict)
                or active_key is not None
                and not isinstance(active_key, str)
            ):
                raise ValueError("invalid lifecycle envelope")
            loaded: dict[str, SigningKey] = {}
            for key_id, value in keys.items():
                if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
                    raise ValueError("invalid lifecycle key identity")
                if not isinstance(value, dict) or set(value) != {
                    "key_id",
                    "public_key",
                    "not_before",
                    "not_after",
                    "revoked_at",
                    "retired_at",
                }:
                    raise ValueError("invalid lifecycle key")
                key = SigningKey(**value)
                if key.key_id != key_id:
                    raise ValueError("lifecycle key identity mismatch")
                loaded[key_id] = key
                if key.revoked_at is None:
                    private = Ed25519PrivateKey.from_private_bytes(
                        self._bundle_key_path(key_id).read_bytes()
                    )
                    if _b64(_public_bytes(private)) != key.public_key:
                        raise ValueError("lifecycle private key does not match public key")
                    self._key_private[key_id] = private
            if active_key is not None and active_key not in loaded:
                raise ValueError("lifecycle active key is unknown")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise RuntimeError("governance key lifecycle is unreadable") from exc
        self._keys, self._active_key = loaded, active_key

    def _persist_lifecycle(self) -> None:
        if self.key_root is None:
            return
        for key_id, key in self._keys.items():
            path = self._bundle_key_path(key_id)
            if key.revoked_at is not None:
                path.unlink(missing_ok=True)
                continue
            private = self._key_private.get(key_id)
            if private is None:
                raise RuntimeError("governance private key is unavailable")
            self._write_secret(
                path,
                private.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                ),
            )
        self._write_secret(
            self._lifecycle_path(),
            canonical(
                {
                    "activeKey": self._active_key,
                    "keys": {key_id: asdict(key) for key_id, key in self._keys.items()},
                }
            ),
        )

    @property
    def trust_root_fingerprint(self) -> str:
        return fingerprint(self._audit_public)

    def trust_root(self) -> dict[str, str]:
        return {
            "keyId": self._audit_key_id,
            "fingerprint": self.trust_root_fingerprint,
            "algorithm": "Ed25519",
            "publicKey": _b64(self._audit_public),
        }

    def _scope_hash(self, scope: Mapping[str, str]) -> str:
        # 128 bits still makes cross-scenario collisions infeasible while keeping
        # artifact paths below Windows' legacy path limit.
        return digest(dict(sorted(scope.items())))[:32]

    def _scope_dir(self, scope: Mapping[str, str]) -> Path:
        path = self.root / self._scope_hash(scope)
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _audit_path(self, scope: Mapping[str, str]) -> Path:
        return self._scope_dir(scope) / "audit.jsonl"

    def _records(self, scope: Mapping[str, str]) -> list[dict[str, Any]]:
        path = self._audit_path(scope)
        if not path.exists():
            return []
        try:
            return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
        except (OSError, json.JSONDecodeError) as exc:
            raise GovernanceDeniedError("audit continuity is unreadable", 409) from exc

    def _append_audit(
        self,
        principal: Principal,
        action: str,
        *,
        artifact_id: str | None = None,
        content_hash: str | None = None,
        outcome: str = "allowed",
        reason: str | None = None,
    ) -> dict[str, Any]:
        _assert_redacted(
            {
                "action": action,
                "artifactId": artifact_id,
                "contentHash": content_hash,
                "reason": reason,
            }
        )
        previous = self._records(principal.scope)
        predecessor = digest(previous[-1]) if previous else "0" * 64
        unsigned = {
            "auditSchemaVersion": SCHEMA_VERSION,
            "id": uuid.uuid4().hex,
            "at": self._now(),
            "actor": principal.subject,
            "action": action,
            "artifactId": artifact_id,
            "contentHash": content_hash,
            "scopeHash": self._scope_hash(principal.scope),
            "outcome": outcome,
            "reason": reason,
            "previousHash": predecessor,
            "keyId": self._audit_key_id,
        }
        record = {**unsigned, "signature": _b64(self._audit_private.sign(canonical(unsigned)))}
        path = self._audit_path(principal.scope)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        os.chmod(path, 0o600)
        return record

    def _deny(
        self,
        principal: Principal | None,
        action: str,
        reason: str,
        status: int = 403,
    ) -> None:
        if principal is not None:
            self._append_audit(principal, action, outcome="denied", reason=reason)
        raise GovernanceDeniedError(reason, status)

    def _require_role(self, principal: Principal, role: str, action: str) -> None:
        if principal.role != role:
            self._deny(principal, action, "role is not authorized")

    def _validate_key_id(self, principal: Principal, action: str, key_id: object) -> str:
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            self._deny(principal, action, "key identity is invalid", 422)
        return key_id

    def _validate_key_window(
        self,
        principal: Principal,
        action: str,
        key_id: object,
        not_before: object,
        not_after: object,
    ) -> tuple[str, int, int]:
        valid_key_id = self._validate_key_id(principal, action, key_id)
        if (
            isinstance(not_before, bool)
            or isinstance(not_after, bool)
            or not isinstance(not_before, int)
            or not isinstance(not_after, int)
            or not_before >= not_after
        ):
            self._deny(principal, action, "key window is invalid", 422)
        return valid_key_id, not_before, not_after

    def _bundle_dir(self, scope: Mapping[str, str], artifact_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", artifact_id):
            raise GovernanceDeniedError("artifact id is invalid", 422)
        return self._scope_dir(scope) / "bundles" / artifact_id

    def _key_for_signing(self) -> SigningKey:
        if self._active_key is None:
            raise GovernanceDeniedError("no active bundle key", 409)
        key = self._keys[self._active_key]
        now = self._now()
        if key.revoked_at is not None or not (key.not_before <= now < key.not_after):
            raise GovernanceDeniedError("active bundle key is unusable", 409)
        return key

    @_locked
    def bootstrap_active(
        self,
        principal: Principal,
        *,
        key_id: str,
        not_before: int,
        not_after: int,
    ) -> SigningKey:
        self._require_role(principal, "key-admin", "key.bootstrap-active")
        key_id, not_before, not_after = self._validate_key_window(
            principal, "key.bootstrap-active", key_id, not_before, not_after
        )
        if (
            self._active_key is not None
            or key_id in self._keys
            or not (not_before <= self._now() < not_after)
        ):
            self._deny(
                principal,
                "key.bootstrap-active",
                "invalid immutable active-key bootstrap",
                409,
            )
        private = Ed25519PrivateKey.generate()
        key = SigningKey(key_id, _b64(_public_bytes(private)), not_before, not_after)
        self._keys[key_id], self._key_private[key_id], self._active_key = key, private, key_id
        self._persist_lifecycle()
        self._append_audit(principal, "key.bootstrap-active")
        return key

    @_locked
    def stage_next(
        self,
        principal: Principal,
        *,
        key_id: str,
        not_before: int,
        not_after: int,
    ) -> SigningKey:
        self._require_role(principal, "key-admin", "key.stage-next")
        key_id, not_before, not_after = self._validate_key_window(
            principal, "key.stage-next", key_id, not_before, not_after
        )
        if key_id in self._keys or not_before >= not_after:
            self._deny(principal, "key.stage-next", "invalid staged key", 409)
        private = Ed25519PrivateKey.generate()
        key = SigningKey(key_id, _b64(_public_bytes(private)), not_before, not_after)
        self._keys[key_id], self._key_private[key_id] = key, private
        self._persist_lifecycle()
        self._append_audit(principal, "key.stage-next")
        return key

    @_locked
    def promote(self, principal: Principal, *, key_id: str) -> SigningKey:
        self._require_role(principal, "key-admin", "key.promote")
        key_id = self._validate_key_id(principal, "key.promote", key_id)
        candidate = self._keys.get(key_id)
        if (
            candidate is None
            or candidate.revoked_at is not None
            or not (candidate.not_before <= self._now() < candidate.not_after)
        ):
            self._deny(principal, "key.promote", "staged key is not active-eligible", 409)
        self._active_key = key_id
        self._persist_lifecycle()
        self._append_audit(principal, "key.promote")
        return candidate

    @_locked
    def retire(self, principal: Principal, *, key_id: str) -> SigningKey:
        self._require_role(principal, "key-admin", "key.retire")
        key_id = self._validate_key_id(principal, "key.retire", key_id)
        key = self._keys.get(key_id)
        if key is None or key.revoked_at is not None:
            self._deny(principal, "key.retire", "key cannot be retired", 409)
        retired = SigningKey(**{**asdict(key), "retired_at": self._now()})
        self._keys[key_id] = retired
        if self._active_key == key_id:
            self._active_key = None
        self._persist_lifecycle()
        self._append_audit(principal, "key.retire")
        return retired

    @_locked
    def revoke(self, principal: Principal, *, key_id: str) -> SigningKey:
        self._require_role(principal, "key-admin", "key.revoke")
        key_id = self._validate_key_id(principal, "key.revoke", key_id)
        key = self._keys.get(key_id)
        if key is None or key.revoked_at is not None:
            self._deny(principal, "key.revoke", "key cannot be revoked", 409)
        now = self._now()
        revoked = SigningKey(**{**asdict(key), "revoked_at": now, "retired_at": now})
        self._keys[key_id] = revoked
        self._key_private.pop(key_id, None)
        if self._active_key == key_id:
            self._active_key = None
        self._persist_lifecycle()
        self._append_audit(principal, "key.revoke")
        return revoked

    def _key_public(self, key_id: str) -> Ed25519PublicKey:
        key = self._keys.get(key_id)
        if key is None:
            raise GovernanceDeniedError("unknown bundle key", 409)
        return Ed25519PublicKey.from_public_bytes(_unb64(key.public_key))

    @_locked
    def create(
        self,
        principal: Principal,
        *,
        artifact_id: str,
        payload: dict[str, Any],
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_role(principal, "bundle-writer", "bundle.create")
        if not isinstance(payload, dict) or not isinstance(envelope, dict):
            self._deny(principal, "bundle.create", "bundle record is invalid", 422)
        try:
            _assert_redacted(payload)
            _assert_redacted(envelope)
        except GovernanceDeniedError as exc:
            self._deny(principal, "bundle.create", "bundle redaction is invalid", exc.status_code)
        if payload.get("schemaVersion") != BUNDLE_SCHEMA:
            self._deny(principal, "bundle.create", "only ScenarioResultV1 may be certified", 422)
        if set(envelope) != ENVELOPE_FIELDS:
            self._deny(principal, "bundle.create", "envelope is not the immutable allowlist", 422)
        content_hash, now = digest(payload), self._now()
        if (
            envelope["governanceSchemaVersion"] != SCHEMA_VERSION
            or envelope["artifactId"] != artifact_id
        ):
            self._deny(principal, "bundle.create", "envelope identity is invalid", 422)
        if (
            envelope["contentHash"] != content_hash
            or envelope["sourceSchemaVersion"] != BUNDLE_SCHEMA
        ):
            self._deny(principal, "bundle.create", "canonical content hash is invalid", 422)
        if envelope["scope"] != dict(principal.scope) or envelope["createdAt"] != now:
            self._deny(principal, "bundle.create", "scope or creation time is invalid", 422)
        if not isinstance(envelope["expiresAt"], int) or envelope["expiresAt"] <= now:
            self._deny(principal, "bundle.create", "certificate is already expired", 422)
        try:
            key = self._key_for_signing()
        except GovernanceDeniedError as exc:
            self._deny(principal, "bundle.create", "active key is unavailable", exc.status_code)
        if envelope["signatureAlgorithm"] != "Ed25519" or envelope["keyId"] != key.key_id:
            self._deny(principal, "bundle.create", "envelope key is not active", 422)
        if envelope["expiresAt"] > key.not_after:
            self._deny(
                principal,
                "bundle.create",
                "bundle validity exceeds signing-key validity",
                422,
            )
        try:
            target = self._bundle_dir(principal.scope, artifact_id)
        except GovernanceDeniedError as exc:
            self._deny(principal, "bundle.create", "artifact identity is invalid", exc.status_code)
        record = {"envelope": envelope, "payload": payload}
        if target.exists():
            try:
                existing = json.loads((target / "record.json").read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                self._deny(principal, "bundle.create", "immutable record is unreadable", 409)
            if canonical(existing["record"]) != canonical(record):
                self._deny(
                    principal,
                    "bundle.create",
                    "artifact id already has different bytes",
                    409,
                )
            self._append_audit(
                principal,
                "bundle.create",
                artifact_id=artifact_id,
                content_hash=content_hash,
            )
            return existing
        signature = _b64(self._key_private[key.key_id].sign(canonical(record)))
        target.mkdir(mode=0o700, parents=True)
        saved = {"record": record, "signature": signature}
        self._write_secret(target / "record.json", canonical(saved))
        self._append_audit(
            principal,
            "bundle.create",
            artifact_id=artifact_id,
            content_hash=content_hash,
        )
        return saved

    @_locked
    def bootstrap_for_scope(self, principal: Principal) -> SigningKey:
        """Create the service-selected initial key for one fresh scope."""
        now = self._now()
        return self.bootstrap_active(
            principal,
            key_id=f"bundle-{uuid.uuid4().hex}",
            not_before=now - 1,
            not_after=now + 86400,
        )

    @_locked
    def certify(self, principal: Principal, *, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate every certificate field inside the governance authority."""
        self._require_role(principal, "bundle-writer", "bundle.create")
        if not isinstance(payload, dict):
            self._deny(principal, "bundle.create", "bundle payload is invalid", 422)
        try:
            _assert_redacted(payload)
            result = json.loads(canonical(payload))
        except (GovernanceDeniedError, TypeError, ValueError) as exc:
            self._deny(principal, "bundle.create", "bundle payload is invalid", 422)
            raise AssertionError("unreachable") from exc
        if (
            result.get("schemaVersion") != BUNDLE_SCHEMA
            or result.get("run") != principal.scope["run"]
        ):
            self._deny(principal, "bundle.create", "bundle scope is invalid", 422)
        if not isinstance(result.get("scenario"), str) or not result["scenario"]:
            self._deny(principal, "bundle.create", "bundle payload is invalid", 422)
        try:
            key = self._key_for_signing()
        except GovernanceDeniedError as exc:
            self._deny(principal, "bundle.create", "active key is unavailable", exc.status_code)
        now = self._now()
        artifact_id = f"{result['scenario'][:8]}-{uuid.uuid4().hex[:12]}"
        unsigned = {name: value for name, value in result.items() if name != "governance"}
        result["governance"] = {
            "artifactId": artifact_id,
            "contentHash": content_hash(unsigned),
            "keyId": key.key_id,
            "trustRootFingerprint": self.trust_root_fingerprint,
        }
        try:
            validate(result, scenario=result["scenario"])
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self._deny(principal, "bundle.create", "bundle payload is invalid", 422)
            raise AssertionError("unreachable") from exc
        envelope = {
            "governanceSchemaVersion": SCHEMA_VERSION,
            "artifactId": artifact_id,
            "scenarioId": result["scenario"],
            "run": result["run"],
            "contentHash": digest(result),
            "sourceSchemaVersion": BUNDLE_SCHEMA,
            "createdAt": now,
            "expiresAt": min(now + 86400, key.not_after),
            "retentionClass": "release-proof",
            "retentionPolicyVersion": "v1",
            "scope": dict(principal.scope),
            "redactionProfile": result["redactionProfile"],
            "signatureAlgorithm": "Ed25519",
            "keyId": key.key_id,
        }
        return self.create(
            principal,
            artifact_id=artifact_id,
            payload=result,
            envelope=envelope,
        )

    def _expire(
        self,
        principal: Principal,
        target: Path,
        artifact_id: str,
        saved: dict[str, Any],
    ) -> None:
        metadata = saved["record"]["envelope"]
        tombstone = {
            key: metadata[key]
            for key in (
                "contentHash",
                "keyId",
                "retentionClass",
                "retentionPolicyVersion",
                "expiresAt",
            )
        }
        tombstone["artifactId"] = artifact_id
        self._write_secret(target / "tombstone.json", canonical(tombstone))
        (target / "record.json").unlink(missing_ok=True)
        self._append_audit(
            principal,
            "bundle.tombstone",
            artifact_id=artifact_id,
            content_hash=tombstone["contentHash"],
        )

    def _validate_saved(
        self, principal: Principal, artifact_id: str, saved: object
    ) -> tuple[dict[str, Any], dict[str, Any], SigningKey]:
        if not isinstance(saved, dict) or set(saved) != {"record", "signature"}:
            raise GovernanceDeniedError("bundle bytes are invalid", 409)
        record, signature = saved["record"], saved["signature"]
        if not isinstance(record, dict) or set(record) != {"envelope", "payload"}:
            raise GovernanceDeniedError("bundle bytes are invalid", 409)
        envelope, payload = record["envelope"], record["payload"]
        if (
            not isinstance(envelope, dict)
            or set(envelope) != ENVELOPE_FIELDS
            or not isinstance(payload, dict)
            or not isinstance(signature, str)
        ):
            raise GovernanceDeniedError("bundle bytes are invalid", 409)
        try:
            validate(payload, scenario=envelope["scenarioId"])
            key = self._keys[envelope["keyId"]]
            if (
                envelope["governanceSchemaVersion"] != SCHEMA_VERSION
                or envelope["sourceSchemaVersion"] != BUNDLE_SCHEMA
                or envelope["artifactId"] != artifact_id
                or envelope["scope"] != dict(principal.scope)
                or envelope["run"] != principal.scope["run"]
                or envelope["contentHash"] != digest(payload)
                or envelope["signatureAlgorithm"] != "Ed25519"
                or type(envelope["createdAt"]) is not int
                or type(envelope["expiresAt"]) is not int
                or key.revoked_at is not None
                or not (key.not_before <= envelope["createdAt"] < key.not_after)
                or envelope["expiresAt"] > key.not_after
            ):
                raise GovernanceDeniedError("bundle verification is invalid", 409)
            self._key_public(key.key_id).verify(_unb64(signature), canonical(record))
        except (
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            InvalidSignature,
            GovernanceDeniedError,
        ) as exc:
            raise GovernanceDeniedError("bundle verification is invalid", 409) from exc
        return record, envelope, key

    def _load(
        self, principal: Principal, artifact_id: str, *, action: str
    ) -> tuple[Path, dict[str, Any]]:
        try:
            target = self._bundle_dir(principal.scope, artifact_id)
        except GovernanceDeniedError as exc:
            self._deny(principal, action, "artifact identity is invalid", exc.status_code)
        if (target / "tombstone.json").exists():
            self._deny(principal, action, "bundle has expired", 410)
        try:
            saved = json.loads((target / "record.json").read_text("utf-8"))
        except FileNotFoundError:
            self._deny(principal, action, "bundle was not found", 404)
        except (OSError, json.JSONDecodeError):
            self._deny(principal, action, "bundle bytes are invalid", 409)
        try:
            _, envelope, _ = self._validate_saved(principal, artifact_id, saved)
        except GovernanceDeniedError as exc:
            self._deny(principal, action, "bundle verification is invalid", exc.status_code)
        if envelope["expiresAt"] <= self._now():
            self._expire(principal, target, artifact_id, saved)
            self._deny(principal, action, "bundle has expired", 410)
        return target, saved

    @_locked
    def read(self, principal: Principal, *, artifact_id: str) -> dict[str, Any]:
        _, saved = self._load(principal, artifact_id, action="bundle.read")
        self._append_audit(
            principal,
            "bundle.read",
            artifact_id=artifact_id,
            content_hash=saved["record"]["envelope"]["contentHash"],
        )
        return saved

    @_locked
    def verify(self, principal: Principal, *, artifact_id: str) -> dict[str, Any]:
        _, saved = self._load(principal, artifact_id, action="bundle.verify")
        _, envelope, key = self._validate_saved(principal, artifact_id, saved)
        self._append_audit(
            principal,
            "bundle.verify",
            artifact_id=artifact_id,
            content_hash=envelope["contentHash"],
        )
        return {
            "verified": True,
            "artifactId": artifact_id,
            "contentHash": envelope["contentHash"],
            "keyId": key.key_id,
        }

    def _verify_audit_chain(self, scope: Mapping[str, str]) -> list[dict[str, Any]]:
        records, predecessor = self._records(scope), "0" * 64
        for record in records:
            if (
                set(record) != AUDIT_FIELDS
                or record["previousHash"] != predecessor
                or record["keyId"] != self._audit_key_id
            ):
                raise GovernanceDeniedError("audit continuity is invalid", 409)
            unsigned = {key: value for key, value in record.items() if key != "signature"}
            try:
                self._audit_public_key().verify(_unb64(record["signature"]), canonical(unsigned))
            except (InvalidSignature, GovernanceDeniedError) as exc:
                raise GovernanceDeniedError("audit signature is invalid", 409) from exc
            predecessor = digest(record)
        return records

    def _audit_public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self._audit_public)

    @_locked
    def read_audit(self, principal: Principal) -> list[dict[str, Any]]:
        self._require_role(principal, "bundle-writer", "audit.read")
        try:
            self._verify_audit_chain(principal.scope)
        except GovernanceDeniedError as exc:
            self._deny(principal, "audit.read", str(exc), exc.status_code)
        self._append_audit(principal, "audit.read")
        return self._verify_audit_chain(principal.scope)

    @_locked
    def read_trust_root(self, principal: Principal) -> dict[str, str]:
        self._require_role(principal, "bundle-writer", "trust-root.read")
        self._append_audit(principal, "trust-root.read")
        return self.trust_root()

    @_locked
    def public_metadata(self, principal: Principal) -> dict[str, Any]:
        self._require_role(principal, "bundle-writer", "governance.public.read")
        self._append_audit(principal, "governance.public.read")
        active = self._keys.get(self._active_key) if self._active_key is not None else None
        return {
            "trustRoot": self.trust_root(),
            "activeKey": asdict(active) if active is not None else None,
            "keys": {key_id: asdict(key) for key_id, key in self._keys.items()},
        }


def verify_public_artifacts(saved: object, public: object, audit: object) -> None:
    """Verify a retained bundle without service-private material."""
    if not isinstance(public, dict) or set(public) != {"trustRoot", "activeKey", "keys"}:
        raise GovernanceDeniedError("public governance metadata is invalid", 409)
    trust, keys = public["trustRoot"], public["keys"]
    active_key = public["activeKey"]
    if active_key is not None and (
        not isinstance(active_key, dict)
        or set(active_key)
        != {
            "key_id",
            "public_key",
            "not_before",
            "not_after",
            "revoked_at",
            "retired_at",
        }
        or keys.get(active_key["key_id"]) != active_key
    ):
        raise GovernanceDeniedError("public governance metadata is invalid", 409)
    if not isinstance(trust, dict) or not isinstance(keys, dict):
        raise GovernanceDeniedError("public governance metadata is invalid", 409)
    try:
        audit_public = _unb64(trust["publicKey"])
        if (
            trust["algorithm"] != "Ed25519"
            or fingerprint(audit_public) != trust["fingerprint"]
            or trust["keyId"] != f"audit-root-{trust['fingerprint'][:16]}"
        ):
            raise ValueError("invalid trust root")
        if not isinstance(saved, dict) or set(saved) != {"record", "signature"}:
            raise ValueError("invalid saved bundle")
        record, signature = saved["record"], saved["signature"]
        if not isinstance(record, dict) or set(record) != {"envelope", "payload"}:
            raise ValueError("invalid bundle record")
        envelope, payload = record["envelope"], record["payload"]
        if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
            raise ValueError("invalid envelope")
        validate(payload, scenario=envelope["scenarioId"])
        key_data = keys[envelope["keyId"]]
        if not isinstance(key_data, dict) or set(key_data) != {
            "key_id",
            "public_key",
            "not_before",
            "not_after",
            "revoked_at",
            "retired_at",
        }:
            raise ValueError("invalid bundle key")
        key = SigningKey(**key_data)
        if (
            key.key_id != envelope["keyId"]
            or key.revoked_at is not None
            or envelope["contentHash"] != digest(payload)
            or not (key.not_before <= envelope["createdAt"] < key.not_after)
            or envelope["expiresAt"] > key.not_after
            or payload["governance"]["artifactId"] != envelope["artifactId"]
            or payload["governance"]["contentHash"]
            != content_hash(
                {name: value for name, value in payload.items() if name != "governance"}
            )
            or payload["governance"]["keyId"] != envelope["keyId"]
            or payload["governance"]["trustRootFingerprint"] != trust["fingerprint"]
        ):
            raise ValueError("bundle lifecycle is invalid")
        Ed25519PublicKey.from_public_bytes(_unb64(key.public_key)).verify(
            _unb64(signature), canonical(record)
        )
        if not isinstance(audit, list):
            raise ValueError("invalid audit")
        predecessor = "0" * 64
        for item in audit:
            if (
                not isinstance(item, dict)
                or set(item) != AUDIT_FIELDS
                or item["previousHash"] != predecessor
                or item["keyId"] != trust["keyId"]
            ):
                raise ValueError("audit continuity is invalid")
            unsigned = {name: value for name, value in item.items() if name != "signature"}
            Ed25519PublicKey.from_public_bytes(audit_public).verify(
                _unb64(item["signature"]), canonical(unsigned)
            )
            predecessor = digest(item)
        _assert_redacted({"saved": saved, "public": public, "audit": audit})
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidSignature,
        GovernanceDeniedError,
    ) as exc:
        raise GovernanceDeniedError("offline governance verification failed", 409) from exc


def create_app(store: ProofBundleStore, authenticate: Callable[[Request], Principal]) -> FastAPI:
    """Create a no-port HTTP surface with strict, role-separated DTOs."""
    app = FastAPI(title="proof-governance", docs_url=None, redoc_url=None, openapi_url=None)

    def principal(request: Request) -> Principal:
        try:
            return authenticate(request)
        except GovernanceDeniedError as exc:
            raise HTTPException(exc.status_code, detail="authentication denied") from exc

    def invoke(action: Callable[[], Any]) -> Any:
        try:
            return action()
        except GovernanceDeniedError as exc:
            raise HTTPException(exc.status_code, detail="request denied") from exc

    async def body(
        request: Request,
        actor: Principal,
        *,
        action: str,
        fields: frozenset[str],
    ) -> dict[str, Any]:
        try:
            value = await request.json()
        except (ValueError, UnicodeDecodeError):
            return invoke(lambda: store._deny(actor, action, "request is invalid", 422))
        if not isinstance(value, dict) or set(value) != fields:
            return invoke(lambda: store._deny(actor, action, "request is invalid", 422))
        return value

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/trust-root")
    def trust_root(actor: Principal = Depends(principal)) -> Any:
        return invoke(lambda: store.read_trust_root(actor))

    @app.get("/v1/public")
    def public_metadata(actor: Principal = Depends(principal)) -> Any:
        return invoke(lambda: store.public_metadata(actor))

    @app.post("/v1/bundles")
    async def create_bundle(request: Request, actor: Principal = Depends(principal)) -> Any:
        value = await body(request, actor, action="bundle.create", fields=frozenset({"payload"}))
        return invoke(lambda: store.certify(actor, payload=value["payload"]))

    @app.get("/v1/bundles/{artifact_id}")
    def read_bundle(artifact_id: str, actor: Principal = Depends(principal)) -> Any:
        return invoke(lambda: store.read(actor, artifact_id=artifact_id))

    @app.post("/v1/bundles/{artifact_id}/verify")
    async def verify_bundle(
        artifact_id: str, request: Request, actor: Principal = Depends(principal)
    ) -> Any:
        await body(request, actor, action="bundle.verify", fields=frozenset())
        return invoke(lambda: store.verify(actor, artifact_id=artifact_id))

    @app.get("/v1/audit")
    def audit(actor: Principal = Depends(principal)) -> Any:
        return invoke(lambda: store.read_audit(actor))

    @app.post("/v1/keys/bootstrap-active")
    async def bootstrap_key(request: Request, actor: Principal = Depends(principal)) -> Any:
        await body(request, actor, action="key.bootstrap-active", fields=frozenset())
        return invoke(lambda: asdict(store.bootstrap_for_scope(actor)))

    key_fields = {
        "stage-next": frozenset({"key_id", "not_before", "not_after"}),
        "promote": frozenset({"key_id"}),
        "retire": frozenset({"key_id"}),
        "revoke": frozenset({"key_id"}),
    }
    key_methods = {
        "stage-next": "stage_next",
        "promote": "promote",
        "retire": "retire",
        "revoke": "revoke",
    }

    def key_operation(route: str, fields: frozenset[str]) -> Callable[..., Any]:
        async def endpoint(request: Request, actor: Principal = Depends(principal)) -> Any:
            value = await body(request, actor, action=f"key.{route}", fields=fields)
            method = getattr(store, key_methods[route])
            return invoke(lambda: asdict(method(actor, **value)))

        return endpoint

    for route, fields in key_fields.items():
        app.post(f"/v1/keys/{route}")(key_operation(route, fields))
    return app


def temporary_store(*, now: Callable[[], int] | None = None) -> ProofBundleStore:
    """Test helper: production runners pass a 0700 scenario directory explicitly."""
    return ProofBundleStore(Path(tempfile.mkdtemp(prefix="proof-governance-")), now=now)
