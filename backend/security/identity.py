"""OIDC request identity verification and emergency bootstrap authentication."""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from backend.security.local_auth import is_local_session


@dataclass(frozen=True)
class IdentitySettings:
    issuer: str
    audience: str
    jwks_url: str = ""
    bootstrap_admin_token: str = ""

    @classmethod
    def from_env(cls) -> IdentitySettings:
        return cls(
            issuer=os.getenv("OIDC_ISSUER", "").rstrip("/"),
            audience=os.getenv("OIDC_AUDIENCE", ""),
            jwks_url=os.getenv("OIDC_JWKS_URL", ""),
            bootstrap_admin_token=os.getenv("BOOTSTRAP_ADMIN_TOKEN", ""),
        )


@dataclass(frozen=True)
class RequestIdentity:
    subject: str
    email: str | None = None
    name: str | None = None
    username: str | None = None
    picture: str | None = None
    is_platform_admin: bool = False
    auth_method: str = "oidc"
    claims: Mapping[str, Any] | None = None


class OIDCVerifier:
    def __init__(
        self,
        settings: IdentitySettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._jwks: dict[str, Any] | None = None

    async def verify(self, token: str) -> RequestIdentity:
        if not self.settings.issuer or not self.settings.audience:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC is not configured")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise JWTError("Unsupported signing algorithm")
            keys = (await self._get_jwks()).get("keys", [])
            key = next(item for item in keys if item.get("kid") == header.get("kid"))
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
            )
        except (JWTError, StopIteration, KeyError, TypeError, httpx.HTTPError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has no subject")
        username = _string_claim(claims, "preferred_username")
        return RequestIdentity(
            subject=subject,
            email=_string_claim(claims, "email"),
            name=_string_claim(claims, "name") or username,
            username=username,
            picture=_string_claim(claims, "picture"),
            claims=claims,
        )

    async def _get_jwks(self) -> dict[str, Any]:
        if self._jwks is None:
            if self._client is not None:
                client = self._client
            else:
                client = httpx.AsyncClient(timeout=5.0)
            try:
                url = self.settings.jwks_url
                if not url:
                    discovery = await client.get(
                        f"{self.settings.issuer}/.well-known/openid-configuration"
                    )
                    discovery.raise_for_status()
                    url = discovery.json().get("jwks_uri", "")
                    if not isinstance(url, str) or not url:
                        raise httpx.HTTPError("OIDC discovery has no jwks_uri")
                response = await client.get(url)
                response.raise_for_status()
                self._jwks = response.json()
            finally:
                if self._client is None:
                    await client.aclose()
        return self._jwks


def identity_dependency(
    settings: IdentitySettings | None = None,
    verifier: OIDCVerifier | None = None,
):
    """Build a FastAPI dependency, injectable for tests and application wiring."""
    resolved = settings or IdentitySettings.from_env()
    oidc = verifier or OIDCVerifier(resolved)

    async def get_request_identity(request: Request) -> RequestIdentity:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if resolved.bootstrap_admin_token and hmac.compare_digest(
            token, resolved.bootstrap_admin_token
        ):
            return RequestIdentity(
                subject="bootstrap-admin",
                name="Bootstrap Admin",
                is_platform_admin=True,
                auth_method="bootstrap",
            )
        if is_local_session(token):
            return RequestIdentity(
                subject="local-admin",
                name="Local Administrator",
                is_platform_admin=True,
                auth_method="local",
            )
        return await oidc.verify(token)

    return get_request_identity


get_request_identity = identity_dependency()


def _string_claim(claims: Mapping[str, Any], key: str) -> str | None:
    value = claims.get(key)
    return value if isinstance(value, str) and value else None
