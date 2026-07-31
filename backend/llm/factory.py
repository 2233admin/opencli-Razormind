"""Adapter factory (model-provider runtime PR-B, decision #6): dispatch a
:class:`~backend.models.provider.ModelProvider` row to its
:class:`~backend.llm.base.ProviderAdapter` by ``provider_type``.

This is the single place PR-C's API routes / PR-D's resolver / PR-E's
consumption points should call to turn a stored provider row into something
that can ``chat``/``list_models``/``test_connection`` — the SSRF guard
(decision #6) lives inside the concrete adapters' client-building step
(``OpenAICompatAdapter._get_client`` / ``AnthropicAdapter._get_client``), not
here; this function only dispatches on ``provider_type``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.llm.anthropic import AnthropicAdapter
from backend.llm.base import LlmAdapterError, ProviderAdapter
from backend.llm.openai_compat import OpenAICompatAdapter

#: provider_type -> adapter class (decision #2: provider_type stays the
#: existing openai|claude|local enum; it now also selects the adapter
#: *family* — openai/local share OpenAICompatAdapter, claude gets its own).
_ADAPTERS: dict[str, type[ProviderAdapter]] = {
    "openai": OpenAICompatAdapter,
    "local": OpenAICompatAdapter,
    "claude": AnthropicAdapter,
}

#: provider_type -> litellm provider-name prefix (crawl4ai_channel's
#: LLMConfig, model-provider runtime PR-E decision #8's crawl4ai exception). Mirrors
#: _ADAPTERS' family grouping 1:1 (openai/local share the openai wire
#: protocol, claude is Anthropic's own) so the two mappings can't quietly
#: drift apart by being hand-maintained in two files.
_LITELLM_PREFIX: dict[str, str] = {
    "openai": "openai",
    "local": "openai",
    "claude": "anthropic",
}


def get_adapter(provider: Any) -> ProviderAdapter:
    """Return the :class:`ProviderAdapter` for ``provider.provider_type``.

    Raises :class:`LlmAdapterError` for an unrecognized ``provider_type``
    rather than silently defaulting — a typo'd/legacy provider_type should
    fail loudly here instead of quietly getting the wrong adapter.
    """
    provider_type = getattr(provider, "provider_type", None)
    adapter_cls = _ADAPTERS.get(provider_type)
    if adapter_cls is None:
        raise LlmAdapterError(
            f"no adapter registered for provider_type={provider_type!r} "
            f"(expected one of {sorted(_ADAPTERS)})"
        )
    return adapter_cls(provider)


def litellm_prefix_for(provider_type: str | None) -> str:
    """Map a ``ModelProvider.provider_type`` to the litellm provider-name
    prefix ``crawl4ai_channel``'s ``LLMConfig`` needs (model-provider runtime PR-E, decision
    #8's crawl4ai exception): the litellm client/call itself stays untouched
    there, but *which* prefix a given ``provider_type`` maps to is now
    decided here — the same place :func:`get_adapter` dispatches from —
    instead of an independently hand-maintained dict inside the channel that
    could silently drift out of sync with ``_ADAPTERS``.

    Falls back to ``"openai"`` for an unrecognized ``provider_type``,
    matching crawl4ai_channel's pre-PR-E behavior exactly.
    """
    return _LITELLM_PREFIX.get(provider_type or "", "openai")


def _provider_view(
    *,
    provider_type: str | None,
    base_url: str | None,
    api_key: str | None,
    default_model: str | None = None,
) -> Any:
    """Build a minimal read-only stand-in for a
    :class:`~backend.models.provider.ModelProvider` row from already-resolved
    field values (model-provider runtime PR-E).

    ``OpenAICompatAdapter``/``AnthropicAdapter`` only ever read
    ``provider.provider_type`` / ``.base_url`` / ``.api_key`` /
    ``.default_model`` off whatever object they're constructed with — they
    don't require a real ORM instance. This lets ``build_openai_compat_adapter``/
    ``build_anthropic_adapter`` below hand them a disposable view instead of
    the caller's actual ``provider``, which matters for two reasons:

    * chat.py's ``provider`` is a live ORM instance still attached to a DB
      session — writing an env-var API-key fallback onto it directly
      (``provider.api_key = ...``) would mark it dirty and risk persisting
      the env-derived key back into ``model_providers.api_key`` on the next
      commit/autoflush. A throwaway view sidesteps that entirely.
    * skill_channel's / the processors' ``provider``/``config`` is a plain
      ``dict`` (``.get(...)`` access), not an object with attributes at all —
      an adapter constructed directly from it would find every attribute
      lookup falling through to nothing.

    Each PR-E call site keeps resolving its OWN fields first (attribute vs
    dict-get, its own env-var fallback name, its own default) exactly as it
    did before model-provider runtime — this only removes the duplicated *client
    construction* step, not each caller's field-resolution rules.
    """
    return SimpleNamespace(
        provider_type=provider_type,
        base_url=base_url,
        api_key=api_key or "",
        default_model=default_model,
    )


def build_openai_compat_adapter(
    *,
    base_url: str | None,
    api_key: str | None,
    default_model: str | None = None,
    provider_type: str | None = None,
) -> OpenAICompatAdapter:
    """Build an :class:`OpenAICompatAdapter` from already-resolved field
    values (model-provider runtime PR-E) — for ``chat.py``/``skill_channel``/the ``openai``
    processor, which need the guarded ``AsyncOpenAI`` client construction
    this adapter implements, but whose ``provider`` is either a live ORM row
    (chat.py) or a plain config ``dict`` (skill_channel, the processors), not
    something this helper should re-derive each caller's own field-resolution
    rules for (see :func:`_provider_view`).

    ``provider_type`` defaults to ``None`` (→ ``allow_private=False``, the
    full SSRF guard — see ``OpenAICompatAdapter``'s docstring / decision #6):
    only ``ModelProvider.provider_type == "local"`` should ever get the
    private-address exemption, and none of chat.py's provider selection,
    skill_channel's dict config, or the openai processor's config carry that
    distinction today — passing ``None`` here keeps every one of them fully
    guarded, exactly as they were (chat.py: unguarded before, now fully
    guarded — a deliberate SSRF-hole closure, decision #6 — see PR-E report;
    skill_channel/openai processor: already fully guarded, unchanged).
    """
    return OpenAICompatAdapter(
        _provider_view(
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
        )
    )


def build_anthropic_adapter(
    *,
    api_key: str | None,
    base_url: str | None = None,
    default_model: str | None = None,
) -> AnthropicAdapter:
    """Build an :class:`AnthropicAdapter` from already-resolved field values
    (model-provider runtime PR-E) — for the ``claude`` processor. See
    :func:`build_openai_compat_adapter` / :func:`_provider_view` for why this
    takes field values rather than a real provider object.
    """
    return AnthropicAdapter(
        _provider_view(
            provider_type="claude",
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
        )
    )
