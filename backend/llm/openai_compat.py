"""OpenAI-compatible adapter (model-provider runtime PR-B) — ``provider_type in {"openai",
"local"}`` (decision #2: both are the same wire protocol, just different
trust levels for the target address).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.llm.base import (
    ConnectionTestResult,
    LlmAdapterError,
    ProviderAdapter,
    classify_retryable,
    redact_secret,
)
from backend.security.url_guard import (
    PinnedAsyncHTTPTransport,
    SSRFValidationError,
    avalidate_public_url_and_ip,
)


class OpenAICompatAdapter(ProviderAdapter):
    """Adapter for ``provider_type in {"openai", "local"}``.

    Builds an ``openai.AsyncOpenAI`` client pointed at ``provider.base_url``,
    reusing the exact SSRF-guard + DNS-rebind-pinning pattern
    ``backend.channels.skill_channel._build_model_call`` and
    ``backend.processors.openai_processor.OpenAIProcessor`` already use for
    this same SDK: validate ``base_url`` with
    ``avalidate_public_url_and_ip`` and hand ``AsyncOpenAI`` an
    ``http_client`` whose transport is a ``PinnedAsyncHTTPTransport`` bound
    to the validated IP(s) — see :mod:`backend.security.url_guard`'s module
    docstring for the full DNS-rebind-closure mechanism. When ``base_url``
    is unset the SDK's own default endpoint is used, unvalidated and
    unpinned, exactly as the existing call sites already do.

    **Local-address exemption (decision #6 — flag for reviewer
    confirmation)**: ``backend.security.url_guard`` had *no* existing
    localhost/private-IP allowlist before this PR (confirmed by reading the
    whole module + its test file — every IP-space check was unconditional).
    Yet ``provider_type == "local"`` exists specifically for self-hosted
    providers that live at exactly the addresses the guard blocks: ollama on
    loopback (``http://localhost:11434``), model-hotel on the NetBird
    fleet-mesh CGNAT range (``100.64.0.0/10``, e.g. ``100.80.x.x``). Rather
    than leave "local" providers permanently unreachable, this adapter adds
    a narrow ``allow_private=True`` opt-in (see
    ``backend.security.url_guard.is_ip_blocked``) that is threaded through
    to both the initial validation call and the pinned transport's
    connect-time re-check, and is used ONLY when
    ``self.provider.provider_type == "local"`` — an ``openai`` provider's
    ``base_url`` is always validated with ``allow_private=False`` (the full,
    unmodified guard). The connection is still IP-pinned in both cases:
    ``allow_private`` only changes which addresses pass the block-list
    check, not whether DNS-rebind pinning applies.
    """

    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        self._allow_private = getattr(provider, "provider_type", None) == "local"
        self._client: Any = None
        self._pinned_http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        api_key = self.provider.api_key or ""
        base_url = self.provider.base_url or None
        if base_url:
            try:
                base_url, ips = await avalidate_public_url_and_ip(
                    base_url, allow_private=self._allow_private
                )
            except SSRFValidationError as exc:
                raise LlmAdapterError(
                    self._sanitize(f"provider base_url rejected: {exc}")
                ) from exc
            hostname = urlparse(base_url).hostname or ""
            self._pinned_http_client = httpx.AsyncClient(
                transport=PinnedAsyncHTTPTransport(
                    hostname, ips, allow_private=self._allow_private
                )
            )
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, http_client=self._pinned_http_client
        )
        return self._client

    async def aclose(self) -> None:
        """Close the pinned ``http_client`` this adapter opened, if any.

        ``AsyncOpenAI`` does not close an externally-supplied ``http_client``
        (mirrors ``OpenAIProcessor``'s own cleanup) — callers that create an
        adapter directly (rather than through a request-scoped helper) should
        call this when done with it.
        """
        if self._pinned_http_client is not None:
            await self._pinned_http_client.aclose()
            self._pinned_http_client = None

    async def get_client(self) -> Any:
        """Public accessor for the guarded ``AsyncOpenAI`` client (model-provider runtime
        PR-E).

        ``chat.py``'s agent-dock tool-calling loop and ``skill_channel``'s
        cheap-executor loop both need the *raw* SDK client (not
        :meth:`chat`'s plain-text return) because they drive their own
        multi-step ``tools=``/``tool_choice=`` loop that this adapter's thin
        ``chat()`` doesn't support. This just exposes the same
        SSRF-validated + DNS-rebind-pinned construction :meth:`chat`/
        :meth:`list_models` already use internally, so those two call sites
        stop duplicating the ``AsyncOpenAI`` + ``PinnedAsyncHTTPTransport``
        wiring themselves.
        """
        return await self._get_client()

    def _resolve_model(self, model: str | None) -> str:
        return model or self.provider.default_model or "gpt-4o-mini"

    def _sanitize(self, message: str) -> str:
        return redact_secret(message, self.provider.api_key)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        client = await self._get_client()
        try:
            response = await client.chat.completions.create(
                model=self._resolve_model(model), messages=messages, **kwargs
            )
        except LlmAdapterError:
            raise
        except Exception as exc:
            raise LlmAdapterError(
                self._sanitize(f"chat completion failed: {exc}"),
                retryable=classify_retryable(exc),
            ) from exc
        return response.choices[0].message.content or ""

    async def list_models(self) -> list[str]:
        client = await self._get_client()
        try:
            page = await client.models.list()
        except LlmAdapterError:
            raise
        except Exception as exc:
            raise LlmAdapterError(
                self._sanitize(f"model discovery failed: {exc}"),
                retryable=classify_retryable(exc),
            ) from exc
        return [model.id for model in (page.data or [])]

    async def test_connection(self) -> ConnectionTestResult:
        started = time.monotonic()
        try:
            models = await self.list_models()
        except LlmAdapterError as exc:
            return {"ok": False, "error": str(exc)}
        latency_ms = (time.monotonic() - started) * 1000
        return {"ok": True, "latency_ms": latency_ms, "models_sample": models[:10]}
