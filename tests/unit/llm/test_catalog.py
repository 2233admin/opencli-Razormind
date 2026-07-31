"""Unit tests for backend.llm (model-provider runtime PR-A): closed-set vocabulary shared by
the model-provider data layer, plus the hardcoded Anthropic model catalog
(decision #5 — Anthropic has no /v1/models discovery endpoint)."""

from backend.llm import (
    VALID_MODEL_SOURCES,
    VALID_MODEL_TYPES,
    VALID_ROLES,
    is_valid_model_source,
    is_valid_model_type,
    is_valid_role,
)
from backend.llm.catalog import ANTHROPIC_CATALOG, anthropic_catalog


def test_valid_model_types_v1_is_llm_only():
    assert VALID_MODEL_TYPES == frozenset({"llm"})
    assert is_valid_model_type("llm") is True
    assert is_valid_model_type("embedding") is False
    assert is_valid_model_type("rerank") is False
    assert is_valid_model_type("") is False
    assert is_valid_model_type(None) is False


def test_valid_roles_closed_set():
    assert VALID_ROLES == frozenset({"chat", "executor", "enrichment"})
    for role in ("chat", "executor", "enrichment"):
        assert is_valid_role(role) is True
    assert is_valid_role("summarizer") is False
    assert is_valid_role("Chat") is False  # case-sensitive, no normalization


def test_valid_model_sources_closed_set():
    assert VALID_MODEL_SOURCES == frozenset({"discovered", "manual"})
    assert is_valid_model_source("discovered") is True
    assert is_valid_model_source("manual") is True
    assert is_valid_model_source("synced") is False


def test_anthropic_catalog_non_empty():
    assert len(ANTHROPIC_CATALOG) >= 3


def test_anthropic_catalog_entries_have_required_fields():
    required = {
        "model_id",
        "display_name",
        "context_window",
        "supports_tools",
        "supports_vision",
    }
    for entry in ANTHROPIC_CATALOG:
        assert required <= set(entry.keys())
        assert isinstance(entry["model_id"], str) and entry["model_id"]
        assert isinstance(entry["display_name"], str) and entry["display_name"]
        assert isinstance(entry["context_window"], int) and entry["context_window"] > 0
        assert isinstance(entry["supports_tools"], bool)
        assert isinstance(entry["supports_vision"], bool)


def test_anthropic_catalog_model_ids_unique():
    ids = [entry["model_id"] for entry in ANTHROPIC_CATALOG]
    assert len(ids) == len(set(ids))


def test_anthropic_catalog_seeds_current_model_ids():
    ids = {entry["model_id"] for entry in ANTHROPIC_CATALOG}
    assert ids == {"claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"}


def test_anthropic_catalog_helper_returns_defensive_copy():
    result = anthropic_catalog()
    assert result == ANTHROPIC_CATALOG
    result.append({"model_id": "mutated", "display_name": "x", "context_window": 1,
                    "supports_tools": False, "supports_vision": False})
    result[0]["model_id"] = "mutated-in-place"
    # The module-level constant must be untouched by mutating the returned list.
    assert len(ANTHROPIC_CATALOG) == 3
    assert ANTHROPIC_CATALOG[0]["model_id"] != "mutated-in-place"
