"""Container entry point for the internal proof-governance HTTP service."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Request
from jose import jwt

from scripts.proof_bundle_governance import (
    GovernanceDeniedError,
    Principal,
    ProofBundleStore,
    create_app,
)


def _jwks() -> dict[str, Any]:
    try:
        response = httpx.get(
            os.environ.get("PROOF_GOVERNANCE_JWKS_URL", "http://proof-oidc/jwks.json"),
            timeout=2,
            trust_env=False,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
            raise ValueError("JWKS is invalid")
        return value
    except Exception as exc:
        raise GovernanceDeniedError("credential is invalid", 401) from exc


def _authenticate(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise GovernanceDeniedError("credential is missing", 401)
    try:
        token = authorization[7:]
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("invalid token algorithm")
        jwks = _jwks()
        key = next(item for item in jwks["keys"] if item.get("kid") == header.get("kid"))
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=os.environ.get("PROOF_GOVERNANCE_AUDIENCE", "proof-governance"),
            issuer=os.environ.get("PROOF_GOVERNANCE_ISSUER", "http://proof-oidc"),
        )
        role, scope, expires = (
            claims.get("role"),
            claims.get("proof_scope"),
            claims.get("exp"),
        )
        expected_subject = {
            "bundle-writer": "proof-bundle-writer",
            "key-admin": "proof-key-admin",
        }.get(role)
        if (
            expected_subject is None
            or not isinstance(scope, dict)
            or type(expires) is not int
            or expires <= int(time.time())
            or claims.get("sub") != expected_subject
            or set(scope) != {"workspace", "project", "workflow", "run"}
            or any(
                not isinstance(value, str) or not value or len(value) > 256
                for value in scope.values()
            )
        ):
            raise ValueError("invalid authorization claims")
        return Principal(expected_subject, role, dict(scope))
    except Exception as exc:
        raise GovernanceDeniedError("credential is invalid", 401) from exc


def build_app(root: Path, *, key_root: Path | None = None) -> Any:
    return create_app(ProofBundleStore(root, key_root=key_root), _authenticate)


store = ProofBundleStore(
    Path(os.environ.get("PROOF_GOVERNANCE_ROOT", "/tmp/proof-governance")),
    key_root=(
        Path(os.environ["PROOF_GOVERNANCE_KEY_ROOT"])
        if "PROOF_GOVERNANCE_KEY_ROOT" in os.environ
        else None
    ),
)
app = create_app(store, _authenticate)
