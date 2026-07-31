"""Anthropic model catalog (model-provider runtime decision #5).

Anthropic has no ``GET /v1/models``-style discovery endpoint the way
OpenAI-compatible providers do (PR-B's ``OpenAICompatAdapter`` hits
``{base_url}/v1/models`` directly, which works against ollama/model-hotel/
deepseek/etc.) — so for ``provider_type="claude"``, model discovery falls
back to this hardcoded, maintained constant instead of a network call.

This list is a maintained constant, not a derived value: update it by hand
as Anthropic ships new models. There is no other source of truth for it in
this codebase.
"""

from typing import TypedDict


class AnthropicModel(TypedDict):
    model_id: str
    display_name: str
    context_window: int
    supports_tools: bool
    supports_vision: bool


ANTHROPIC_CATALOG: list[AnthropicModel] = [
    {
        "model_id": "claude-opus-4-8",
        "display_name": "Claude Opus 4.8",
        "context_window": 200000,
        "supports_tools": True,
        "supports_vision": True,
    },
    {
        "model_id": "claude-sonnet-5",
        "display_name": "Claude Sonnet 5",
        "context_window": 200000,
        "supports_tools": True,
        "supports_vision": True,
    },
    {
        "model_id": "claude-haiku-4-5-20251001",
        "display_name": "Claude Haiku 4.5",
        "context_window": 200000,
        "supports_tools": True,
        "supports_vision": False,
    },
]


def anthropic_catalog() -> list[AnthropicModel]:
    """Return the maintained Anthropic model catalog.

    Returns a defensive shallow copy (new list of new dicts) so a caller
    mutating the result can't corrupt the module-level constant.
    """
    return [dict(entry) for entry in ANTHROPIC_CATALOG]
