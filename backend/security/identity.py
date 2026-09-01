"""OIDC, local-password, and emergency bootstrap request identity verification."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.database import get_db
from backend.models.identity import User

# Local accounts (backend/api/v1/identity.py's /auth/setup and /auth/login)
# are signed HS256 with this secret; OIDC tokens are always RS256 (enforced
# below in OIDCVerifier.verify). The alg header alone discriminates which
# verification path a bearer token needs — no separate token "type" claim
# or extra network round-trip required to tell them apart.
LOCAL_TOKEN_ALG = "HS256"
LOCAL_TOKEN_TTL = timedelta(days=30)


@dataclass(frozen=True)
class IdentitySettings:
    issuer: str
    audience: str
    jwks_url: str = ""
    bootstrap_admin_token: str = ""
    local_token_secret: str = ""

    @classmethod
    def from_env(cls) -> IdentitySettings:
        # Sourced from Settings (backend/config.py), not raw os.getenv(): the
        # process environment alone is not a reliable source for these — under
        # plain `uv run uvicorn ...` uv does not inject .env into os.environ,
        # while Settings parses .env directly via pydantic-settings regardless
        # of what the launching process actually exported.
        settings = get_settings()
        return cls(
            issuer=settings.oidc_issuer.rstrip("/"),
            audience=settings.oidc_audience,
            jwks_url=settings.oidc_jwks_url,
            bootstrap_admin_token=settings.bootstrap_admin_token,
            local_token_secret=settings.secret_key,
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


def issue_local_token(user: User, *, secret_key: str) -> str:
    """Mint a bearer token for a local (password-auth) User.

    Held and resent by the frontend exactly like an OIDC id_token or the
    bootstrap token (see frontend/lib/auth/session.ts) — this is not a
    server-side session, there's nothing to look up on refresh besides the
    token itself.
    """
    now = datetime.now(UTC)
    claims = {"sub": user.subject, "iat": now, "exp": now + LOCAL_TOKEN_TTL}
    return jwt.encode(claims, secret_key, algorithm=LOCAL_TOKEN_ALG)


async def _verify_local_token(token: str, *, secret_key: str, db: AsyncSession) -> RequestIdentity:
    try:
        claims = jwt.decode(token, secret_key, algorithms=[LOCAL_TOKEN_ALG])
    except JWTError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    subject = claims.get("sub")
    user = await db.scalar(select(User).where(User.subject == subject)) if subject else None
    if user is None or user.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled or not found")
    return RequestIdentity(
        subject=user.subject,
        email=user.email,
        name=user.display_name,
        auth_method="local",
    )


def identity_dependency(
    settings: IdentitySettings | None = None,
    verifier: OIDCVerifier | None = None,
):
    """Build a FastAPI dependency, injectable for tests and application wiring."""
    resolved = settings or IdentitySettings.from_env()
    oidc = verifier or OIDCVerifier(resolved)

    async def get_request_identity(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> RequestIdentity:
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
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        if header.get("alg") == LOCAL_TOKEN_ALG:
            return await _verify_local_token(token, secret_key=resolved.local_token_secret, db=db)
        return await oidc.verify(token)

    return get_request_identity


get_request_identity = identity_dependency()


def _string_claim(claims: Mapping[str, Any], key: str) -> str | None:
    value = claims.get(key)
    return value if isinstance(value, str) and value else None
