"""``ProviderAdapter`` ABC (model-provider runtime PR-B).

Every concrete adapter (:class:`~backend.llm.openai_compat.OpenAICompatAdapter`,
:class:`~backend.llm.anthropic.AnthropicAdapter`) implements the same three
async methods against a :class:`~backend.models.provider.ModelProvider` row,
so :func:`backend.llm.factory.get_adapter` can hand callers (PR-C's API
routes, PR-D's resolver, PR-E's consumption points) a uniform surface
regardless of which vendor SDK sits underneath.

Kept deliberately thin: ``chat()`` returns plain assistant text (not the raw
SDK response object) because PR-B has no consumer yet that needs anything
richer — a later PR can widen the return type without touching this ABC's
shape if a real need shows up. ``test_connection()`` returns a dict rather
than a dataclass so it serializes straight into an API response body (PR-C)
with no extra marshalling step.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, TypedDict

import anthropic
import httpx
import openai


def redact_secret(message: str, secret: str | None) -> str:
    """Strip a literal ``secret`` (the provider's plaintext ``api_key``) out
    of an error ``message`` before it is ever raised/returned.

    Every concrete adapter routes SDK exception text through this before
    wrapping it in :class:`LlmAdapterError` or a
    :class:`~backend.llm.base.ConnectionTestResult`'s ``error`` field — SDK
    exceptions can echo back request details (e.g. a vendor's HTTP client
    repr'ing its own auth header) and this is the one seam that guarantees
    the key never reaches a log line, an API response, or a raised message.
    A missing/empty ``secret`` is a no-op (nothing to redact).
    """
    if not secret:
        return message
    return message.replace(secret, "***REDACTED***")


class LlmAdapterError(Exception):
    """Raised for any adapter-level failure (bad provider_type, SDK error,
    connection failure, etc).

    Mirrors :class:`backend.browser_act.cli.BrowserActError`'s guarantee: the
    message is built to be diagnosable but must NEVER include the provider's
    ``api_key`` (or any other credential/env value) — every concrete adapter
    sanitizes SDK exception text before wrapping it here (see each adapter's
    ``_sanitize_error`` helper). Treat a message containing a raw key as a
    bug in the adapter that raised it, not something this class can enforce
    on its own.

    ``retryable`` (model-provider runtime PR-D, decision #7) tells
    :class:`~backend.llm.resolver.ProviderResolver` whether this failure is
    connection-level (connect error / timeout / 5xx — the provider is
    unreachable or broken right now, worth failing over to the next
    candidate) or a business error (4xx — bad key, malformed request; a
    *configuration* problem no other candidate would fix, so failing over
    would just mask it). Defaults to ``False`` — an adapter that raises this
    directly (e.g. :func:`~backend.llm.factory.get_adapter`'s unknown
    ``provider_type``) is a config error, not a transient one. Concrete
    adapters set it explicitly via :func:`classify_retryable` when wrapping a
    caught SDK exception.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def classify_retryable(exc: BaseException) -> bool:
    """Classify a caught SDK/transport exception as connection-level
    (``True``) vs business-level (``False``) for model-provider runtime decision #7.

    Connection-level (worth a failover): stdlib/httpx transport timeouts and
    connection failures, and each SDK's own connection/timeout/5xx exception
    types (``openai.APIConnectionError``/``APITimeoutError``/
    ``InternalServerError``, the ``anthropic`` equivalents).

    Business-level (never fails over — decision #7: a 4xx is a config
    error, not a liveness problem): auth/permission/bad-request/not-found/
    rate-limit/conflict — checked explicitly, and, as a catch-all for any
    other ``APIStatusError`` subclass this list doesn't name, any exception
    carrying a ``status_code`` is classified by that code (``>= 500`` ->
    retryable, else not) before falling back to ``False`` for anything
    unrecognized.
    """
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            httpx.TimeoutException,
            httpx.NetworkError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.InternalServerError,
        ),
    ):
        return True

    if isinstance(
        exc,
        (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.BadRequestError,
            openai.NotFoundError,
            openai.UnprocessableEntityError,
            openai.RateLimitError,
            openai.ConflictError,
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.BadRequestError,
            anthropic.NotFoundError,
            anthropic.UnprocessableEntityError,
            anthropic.RateLimitError,
            anthropic.ConflictError,
        ),
    ):
        return False

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code >= 500

    return False


class ConnectionTestResult(TypedDict, total=False):
    """Shape returned by :meth:`ProviderAdapter.test_connection`.

    ``total=False`` — a failed test omits ``latency_ms``/``models_sample``
    rather than filling them with ``None`` noise; callers should use
    ``.get(...)``.
    """

    ok: bool
    latency_ms: float | None
    error: str | None
    models_sample: list[str] | None


class ProviderAdapter(ABC):
    """Uniform async runtime surface over one :class:`ModelProvider` row.

    Constructed directly from the ORM instance (not its individual fields)
    so an adapter can read whichever columns it needs (``base_url``,
    ``api_key`` — via the model's decrypting property, ``default_model``)
    without the factory having to know each adapter's field list.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Send ``messages`` (OpenAI chat-message shape: ``{"role", "content"}``
        dicts) and return the assistant's reply text.

        ``model`` defaults to ``self.provider.default_model`` when omitted.
        Raises :class:`LlmAdapterError` on failure (never leaks ``api_key``).
        """

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Return the model ids this provider exposes.

        OpenAI-compat: discovered via ``GET {base_url}/v1/models`` (decision
        #5). Anthropic: returned from the hardcoded
        :func:`backend.llm.catalog.anthropic_catalog` (no discovery endpoint
        exists). Raises :class:`LlmAdapterError` on failure — callers that
        want a non-raising "discovery failed, here's why" surface should use
        :meth:`test_connection` instead (decision #5: discovery failure must
        not crash the caller).
        """

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Probe the provider cheaply and report ``{ok, latency_ms, error,
        models_sample}``.

        Never raises for an ordinary connection/auth failure — those come
        back as ``{"ok": False, "error": <sanitized message>}`` so PR-C's
        ``POST /providers/{id}/test`` endpoint can return this dict straight
        through as the response body. ``error`` never contains ``api_key``.
        """
