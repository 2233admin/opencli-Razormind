"""API channel: direct REST/GraphQL API calls."""

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.channels.base import (
    AbstractChannel,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel
from backend.security.url_guard import (
    SSRFValidationError,
    avalidate_public_url,
    guarded_async_client,
)

logger = logging.getLogger(__name__)

_SECRET_RE = re.compile(r"\{\{secret:([A-Z_][A-Z0-9_]*)\}\}")


def _resolve_secrets(value: str) -> str:
    """Replace {{secret:ENV_VAR}} placeholders with env var values."""

    def _replace(match: re.Match) -> str:
        env_var = match.group(1)
        return os.environ.get(env_var, "")

    return _SECRET_RE.sub(_replace, value)


def _resolve_dict_secrets(d: dict) -> dict:
    return {k: _resolve_secrets(v) if isinstance(v, str) else v for k, v in d.items()}


@register_channel
class ApiChannel(AbstractChannel):
    """Collect data from REST/GraphQL APIs."""

    channel_type = "api"

    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        """Thin wrapper around ``fetch()``: a bare ``FetchContext`` (no
        ``ctx.http``, so ``fetch()`` opens its own one-shot client; no
        ``ctx.source_id``, so auth always resolves via the legacy inline/env
        path — this method's signature has no source id to resolve the
        encrypted store against) reproduces this method's original behaviour
        exactly. Converts ``fetch()``'s raise-based failure contract back into
        this method's return-based one so existing callers keep working."""
        ctx = FetchContext(config=config, params=parameters)
        try:
            result = await self.fetch(ctx)
        except ChannelFetchError as exc:
            cause = exc.__cause__
            return ChannelResult.fail(str(exc), error_type=type(cause).__name__ if cause else None)
        return ChannelResult.ok(result.items, **result.metadata)

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        """Thick-contract fetch: auth prefers the encrypted credential store
        (``backend.auth.AuthManager``) over plaintext ``channel_config.auth``
        when a source has migrated, and requests go through the runner's
        rate-limited/retrying client (``ctx.http``) when present, falling back
        to a one-shot client otherwise. Raises ``ChannelFetchError`` on failure
        so the runner's retry/backoff policy applies instead of a swallowed
        ``ChannelResult.fail``.
        """
        config = ctx.config
        base_url: str = config.get("base_url", "")
        endpoint: str = config.get("endpoint", "")
        method: str = config.get("method", "GET").upper()
        auth_config: dict = config.get("auth", {})
        query_params: dict = {**config.get("params", {}), **ctx.params}
        request_body: dict = config.get("body", {})
        extra_headers: dict = _resolve_dict_secrets(config.get("headers", {}))
        timeout: int = config.get("timeout", 30)
        result_path: str = config.get("result_path", "")

        url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        try:
            url = await avalidate_public_url(url)
        except SSRFValidationError as exc:
            raise ChannelFetchError(
                f"api channel URL rejected: {exc}", error_type="SSRFValidationError"
            ) from exc

        headers = await self._resolve_auth_headers(auth_config, ctx.source_id, base_url)
        headers.update(extra_headers)

        # follow_redirects defaults to False on httpx.AsyncClient — a validated
        # URL must not be allowed to 30x-redirect to a private/loopback/fleet
        # address (SSRF via redirect), so this is left unset deliberately.
        # ctx.http is the runner-shared client (connection pinning there is
        # out of this file's boundary); the one-shot path is pinned via
        # guarded_async_client (DNS-rebinding TOCTOU closure, AUDIT B3).
        if ctx.http is not None:
            response = await self._send(ctx.http, method, url, query_params, request_body, headers, timeout)
        else:
            try:
                client, url = await guarded_async_client(url, timeout=timeout)
            except SSRFValidationError as exc:
                raise ChannelFetchError(
                    f"api channel URL rejected: {exc}", error_type="SSRFValidationError"
                ) from exc
            async with client as opened_client:
                response = await self._send(
                    opened_client, method, url, query_params, request_body, headers, timeout
                )

        try:
            data = response.json()
        except Exception as exc:
            # WIRING_GAP_LEDGER W1: error_type must be set so the SCHEMA_DRIFT
            # chain (error_kinds -> control.recorder) fires instead of being
            # dropped by recorder's `elif error_type is not None` guard.
            raise ChannelFetchError(
                "Failed to parse API response as JSON", error_type=type(exc).__name__
            ) from exc

        if result_path:
            for key in result_path.split("."):
                if isinstance(data, dict):
                    data = data.get(key, [])
                else:
                    break

        items = data if isinstance(data, list) else [data]
        return FetchResult(items=items, metadata={"url": url, "status_code": response.status_code})

    @staticmethod
    async def _send(
        client: Any,
        method: str,
        url: str,
        query_params: dict,
        request_body: dict,
        headers: dict,
        timeout: int,
    ) -> httpx.Response:
        """One request on ``client``, wrapped into ``ChannelFetchError``."""
        try:
            response = await client.request(
                method,
                url,
                params=query_params if method == "GET" else None,
                json=request_body if method != "GET" else None,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise ChannelFetchError(f"API request to {url} timed out") from exc
        except httpx.HTTPStatusError as exc:
            # AUDIT C13: classify by status so a gateway blip (502/503/504,
            # Cloudflare's 520/522/524, ...) reaches the celery retry boundary
            # instead of defaulting to permanent alongside a genuine 4xx.
            from backend.pipeline.error_taxonomy import is_retryable_http_status

            status = exc.response.status_code
            error_type = (
                "RetryableHTTPStatus" if is_retryable_http_status(status) else "PermanentHTTPStatus"
            )
            raise ChannelFetchError(
                f"HTTP {status}: {exc.response.text[:200]}", error_type=error_type
            ) from exc
        except Exception as exc:
            raise ChannelFetchError(f"API request failed: {exc}") from exc

    async def _resolve_auth_headers(
        self, auth: dict, source_id: str | None, base_url: str = ""
    ) -> dict[str, str]:
        """Prefer decrypted creds from AuthManager's encrypted store when the
        source has migrated; fall back to the legacy inline/env config unchanged
        (``_build_auth_headers``) so unmigrated sources keep working as-is."""
        auth_type = auth.get("type", "")
        if auth_type == "cookie":
            return await self._resolve_cookie_header(base_url)
        if source_id and auth_type in ("bearer", "api_key", "basic"):
            from backend.auth.header_builder import build_auth_header
            from backend.auth.manager import AuthManager

            creds = await AuthManager().resolve(source_id)
            headers = build_auth_header(auth_type, creds, header_name=auth.get("header", "X-API-Key"))
            if headers:
                return headers
        return self._build_auth_headers(auth)

    @staticmethod
    async def _resolve_cookie_header(base_url: str) -> dict[str, str]:
        """auth.type == "cookie": borrow a real login session synced from
        CookieCloud (``backend.auth.manager.AuthManager.resolve_cookies``),
        keyed by ``base_url``'s domain. Empty dict (no header) when nothing is
        synced for that domain yet — never a hard failure."""
        from backend.auth.manager import AuthManager

        domain = urlparse(base_url).hostname or ""
        if not domain:
            return {}
        cookies = await AuthManager().resolve_cookies(domain)
        if not cookies:
            return {}
        return {"Cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies)}

    def _build_auth_headers(self, auth: dict) -> dict[str, str]:
        auth_type = auth.get("type", "")
        if auth_type == "bearer":
            token_env = auth.get("token_env", "")
            token = os.environ.get(token_env, auth.get("token", ""))
            self._warn_inline(auth, "token", token_env)
            return {"Authorization": f"Bearer {token}"}
        if auth_type == "basic":
            import base64
            raw_pw = auth.get("password", "")
            user = _resolve_secrets(auth.get("username", ""))
            pw = _resolve_secrets(raw_pw)
            if raw_pw and "{{secret:" not in raw_pw:
                self._warn_inline(auth, "password", "")
            encoded = base64.b64encode(f"{user}:{pw}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        if auth_type == "api_key":
            header_name = auth.get("header", "X-API-Key")
            key_env = auth.get("key_env", "")
            key = os.environ.get(key_env, auth.get("key", ""))
            self._warn_inline(auth, "key", key_env)
            return {header_name: key}
        return {}

    @staticmethod
    def _warn_inline(auth: dict, field: str, env_key: str) -> None:
        """Deprecation: an inline plaintext secret in ``channel_config.auth``.
        Prefer env indirection (``<field>_env`` / ``{{secret:ENV}}``) or the
        encrypted credential store (``backend.auth.AuthManager``)."""
        if auth.get(field) and not (env_key and env_key in os.environ):
            logger.warning(
                "api channel: inline plaintext '%s' in channel_config.auth is "
                "deprecated; use %s_env or the encrypted credential store",
                field,
                field,
            )

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("base_url"):
            errors.append("'base_url' is required for api channel")
        if not config.get("endpoint"):
            errors.append("'endpoint' is required for api channel")
        return errors

    async def health_check(
        self, config: dict[str, Any] | None = None, source_id: str | None = None
    ) -> bool:
        """Deep readiness: a real HEAD (falling back to GET when HEAD isn't
        supported) against base_url+endpoint, with real auth headers — not
        just "is the config well-formed" (that's validate_config's job).
        Short timeout: this is a liveness probe, not a full request."""
        if config is None:
            return True  # no source context to probe (e.g. called standalone)
        base_url: str = config.get("base_url", "")
        if not base_url:
            return False
        endpoint: str = config.get("endpoint", "")
        url = base_url.rstrip("/") + "/" + endpoint.lstrip("/") if endpoint else base_url

        try:
            client, url = await guarded_async_client(url, timeout=5)
        except SSRFValidationError as exc:
            logger.warning("api channel health_check: URL rejected: %s", exc)
            return False

        headers = await self._resolve_auth_headers(config.get("auth", {}), source_id, base_url)

        try:
            async with client as opened_client:
                response = await opened_client.head(url, headers=headers)
                if response.status_code in (404, 405):
                    response = await opened_client.get(url, headers=headers)
                response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("api channel health_check: %s unreachable: %s", url, exc)
            return False
