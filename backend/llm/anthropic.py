"""Anthropic adapter (model-provider runtime PR-B) — ``provider_type == "claude"`` (decision
#2)."""

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
from backend.llm.catalog import anthropic_catalog
from backend.security.url_guard import (
    PinnedAsyncHTTPTransport,
    SSRFValidationError,
    avalidate_public_url_and_ip,
)

#: Fallback model when neither an explicit ``model=`` kwarg nor
#: ``provider.default_model`` is set — mirrors
#: ``backend.processors.claude_processor.ClaudeProcessor``'s own default so
#: behaviour is unchanged for callers that relied on that default.
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicAdapter(ProviderAdapter):
    """Adapter for ``provider_type == "claude"``.

    Uses ``anthropic.AsyncAnthropic`` (mirrors
    ``backend.processors.claude_processor.ClaudeProcessor``'s SDK usage).
    Unlike ``OpenAICompatAdapter``, there is no ``provider_type == "local"``
    case here — Anthropic's endpoint is effectively fixed
    (``https://api.anthropic.com`` by SDK default), so this adapter never
    passes ``allow_private=True`` to the guard: a ``base_url`` override (rare
    — e.g. a proxy in front of the real API) is validated with the full,
    unmodified SSRF guard, exactly like ``openai``-type providers.

    ``list_models()`` returns the hardcoded
    :func:`backend.llm.catalog.anthropic_catalog` model ids rather than
    hitting the network — decision #5: Anthropic has no ``GET /v1/models``-
    style discovery endpoint.
    """

    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        self._client: Any = None
        self._pinned_http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import anthropic

        api_key = self.provider.api_key or ""
        base_url = getattr(self.provider, "base_url", None) or None
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            # Same SSRF-guard + DNS-rebind-pinning pattern as
            # OpenAICompatAdapter/skill_channel/openai_processor —
            # allow_private is always False here (see class docstring).
            try:
                base_url, ips = await avalidate_public_url_and_ip(base_url)
            except SSRFValidationError as exc:
                raise LlmAdapterError(
                    self._sanitize(f"provider base_url rejected: {exc}")
                ) from exc
            hostname = urlparse(base_url).hostname or ""
            self._pinned_http_client = httpx.AsyncClient(
                transport=PinnedAsyncHTTPTransport(hostname, ips)
            )
            client_kwargs["base_url"] = base_url
            client_kwargs["http_client"] = self._pinned_http_client
        self._client = anthropic.AsyncAnthropic(**client_kwargs)
        return self._client

    async def aclose(self) -> None:
        """Close the pinned ``http_client`` this adapter opened, if any."""
        if self._pinned_http_client is not None:
            await self._pinned_http_client.aclose()
            self._pinned_http_client = None

    async def get_client(self) -> Any:
        """Public accessor for the guarded ``AsyncAnthropic`` client (model-provider runtime
        PR-E) — mirrors :meth:`OpenAICompatAdapter.get_client`. Used by
        ``claude_processor`` to consolidate client construction while keeping
        its own per-record loop + usage-token logging (which needs the raw
        SDK response object, not ``chat()``'s plain-text return).
        """
        return await self._get_client()

    def _resolve_model(self, model: str | None) -> str:
        return model or self.provider.default_model or _DEFAULT_MODEL

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
        max_tokens = kwargs.pop("max_tokens", 1024)
        try:
            response = await client.messages.create(
                model=self._resolve_model(model),
                max_tokens=max_tokens,
                messages=messages,
                **kwargs,
            )
        except LlmAdapterError:
            raise
        except Exception as exc:
            raise LlmAdapterError(
                self._sanitize(f"chat completion failed: {exc}"),
                retryable=classify_retryable(exc),
            ) from exc
        return response.content[0].text if response.content else ""

    async def list_models(self) -> list[str]:
        return [entry["model_id"] for entry in anthropic_catalog()]

    async def test_connection(self) -> ConnectionTestResult:
        started = time.monotonic()
        try:
            client = await self._get_client()
        except LlmAdapterError as exc:
            return {"ok": False, "error": str(exc)}
        model = self.provider.default_model or _DEFAULT_MODEL
        try:
            await client.messages.create(
                model=model, max_tokens=1, messages=[{"role": "user", "content": "ping"}]
            )
        except Exception as exc:
            return {"ok": False, "error": self._sanitize(str(exc))}
        latency_ms = (time.monotonic() - started) * 1000
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "models_sample": (await self.list_models())[:10],
        }
