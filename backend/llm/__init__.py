"""backend.llm — self-built model-provider runtime (model-provider runtime decision #1).

No litellm: this package owns provider-agnostic building blocks for
chat/list_models/test_connection dispatch across ``model_providers`` rows.
PR-A shipped the closed-set vocabulary shared by the data layer
(``backend.models.provider_model``, ``backend.models.model_default``) and
their Pydantic schemas, plus the Anthropic model catalog
(``backend.llm.catalog``). PR-B added the runtime itself: ``ProviderAdapter`` (``backend.llm.base``),
``OpenAICompatAdapter`` (``backend.llm.openai_compat``, ``provider_type in
{"openai", "local"}``), ``AnthropicAdapter`` (``backend.llm.anthropic``,
``provider_type == "claude"``), and ``factory.get_adapter()``
(``backend.llm.factory``) to dispatch a
:class:`~backend.models.provider.ModelProvider` row to the right one. PR-D
(this state) adds the failover resolver: ``ProviderResolver``/
``ResolverError``/the module-level ``resolver`` singleton
(``backend.llm.resolver``) — pure logic only, not yet wired into any
consumption point (that is PR-E's job).
"""

from typing import Any

from backend.llm.anthropic import AnthropicAdapter
from backend.llm.base import ConnectionTestResult, LlmAdapterError, ProviderAdapter
from backend.llm.factory import get_adapter
from backend.llm.openai_compat import OpenAICompatAdapter
from backend.llm.resolver import ProviderResolver, ResolverError, resolver

#: provider_models.model_type — v1 ships only "llm"; the column itself stays
#: a plain string so future embedding/rerank rows don't need a migration.
VALID_MODEL_TYPES = frozenset({"llm"})

#: model_defaults.role — the three consumption points model-provider runtime collapses onto
#: ModelProvider (decision #4): agent dock chat, skill_channel's cheap
#: executor model, pipeline enrichment fallback.
VALID_ROLES = frozenset({"chat", "executor", "enrichment"})

#: provider_models.source — "discovered" rows come from sync (OpenAI-compat
#: /v1/models or the Anthropic catalog), "manual" rows are hand-entered and
#: must never be overwritten/deleted by a sync (decision #3).
VALID_MODEL_SOURCES = frozenset({"discovered", "manual"})


def is_valid_model_type(value: Any) -> bool:
    return value in VALID_MODEL_TYPES


def is_valid_role(value: Any) -> bool:
    return value in VALID_ROLES


def is_valid_model_source(value: Any) -> bool:
    return value in VALID_MODEL_SOURCES


__all__ = [
    "VALID_MODEL_TYPES",
    "VALID_ROLES",
    "VALID_MODEL_SOURCES",
    "is_valid_model_type",
    "is_valid_role",
    "is_valid_model_source",
    "ProviderAdapter",
    "LlmAdapterError",
    "ConnectionTestResult",
    "OpenAICompatAdapter",
    "AnthropicAdapter",
    "get_adapter",
    "ProviderResolver",
    "ResolverError",
    "resolver",
]
