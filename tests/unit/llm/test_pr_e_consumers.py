"""model-provider runtime PR-E: consumer client-construction consolidation tests.

Covers chat.py / skill_channel.py / the openai+claude processors' switch from
each hand-rolling its own AsyncOpenAI/AsyncAnthropic + SSRF-guard wiring to
backend.llm.factory's build_openai_compat_adapter/build_anthropic_adapter +
OpenAICompatAdapter/AnthropicAdapter.get_client() — proving each consumer's
pre-PR-E behavior (env-var api_key fallback, SSRF guard, per-record error
handling) is unchanged now that construction is centralized instead of
duplicated four times over, and that leaving PR-D's resolver un-wired here is
safe (existing provider selection still works when model_defaults isn't
configured).

Every SDK client is mocked at the class level (openai.AsyncOpenAI /
anthropic.AsyncAnthropic) — no real network call, matching
tests/unit/llm/test_adapters.py's convention.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.llm.anthropic import AnthropicAdapter
from backend.llm.base import LlmAdapterError
from backend.llm.factory import (
    build_anthropic_adapter,
    build_openai_compat_adapter,
    litellm_prefix_for,
)
from backend.llm.openai_compat import OpenAICompatAdapter
from backend.llm.resolver import resolver
from backend.models.provider import ModelProvider


# ── factory helpers (backend.llm.factory) ───────────────────────────────────


def test_litellm_prefix_for_matches_pre_pr_e_mapping():
    """crawl4ai_channel's old inline dict, now centralized (decision #8) —
    values must match exactly, including the "openai" fallback default."""
    assert litellm_prefix_for("claude") == "anthropic"
    assert litellm_prefix_for("openai") == "openai"
    assert litellm_prefix_for("local") == "openai"
    assert litellm_prefix_for("some-unknown-type") == "openai"
    assert litellm_prefix_for(None) == "openai"


@pytest.mark.asyncio
async def test_build_openai_compat_adapter_builds_client_with_resolved_fields():
    mock_client = MagicMock()
    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        adapter = build_openai_compat_adapter(base_url=None, api_key="resolved-key")
        client = await adapter.get_client()
    assert client is mock_client
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "resolved-key"


@pytest.mark.asyncio
async def test_build_openai_compat_adapter_guards_private_base_url_by_default():
    """No provider_type passed -> allow_private False (the full guard) —
    matches every PR-E consumer, none of which carry a "local" distinction."""
    with patch("openai.AsyncOpenAI") as mock_cls:
        adapter = build_openai_compat_adapter(base_url="http://10.0.0.5/v1", api_key="k")
        with pytest.raises(LlmAdapterError, match="rejected"):
            await adapter.get_client()
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_build_anthropic_adapter_builds_client_with_resolved_fields():
    mock_client = MagicMock()
    with patch("anthropic.AsyncAnthropic", return_value=mock_client) as mock_cls:
        adapter = build_anthropic_adapter(api_key="claude-key")
        client = await adapter.get_client()
    assert client is mock_client
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "claude-key"


@pytest.mark.asyncio
async def test_openai_compat_public_get_client_matches_private_get_client():
    provider = ModelProvider(
        name="p", provider_type="openai", base_url=None, api_key="k",
        default_model="gpt-4o-mini", enabled=True,
    )
    mock_client = MagicMock()
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        adapter = OpenAICompatAdapter(provider)
        client = await adapter.get_client()
    assert client is mock_client


@pytest.mark.asyncio
async def test_anthropic_public_get_client_matches_private_get_client():
    provider = ModelProvider(
        name="p", provider_type="claude", base_url=None, api_key="k",
        default_model=None, enabled=True,
    )
    mock_client = MagicMock()
    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        adapter = AnthropicAdapter(provider)
        client = await adapter.get_client()
    assert client is mock_client


# ── chat.py: _build_client env fallback + guard preserved ───────────────────


@pytest.mark.asyncio
async def test_chat_build_client_uses_provider_api_key_when_set(monkeypatch):
    from backend.api.v1.chat import _build_client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = ModelProvider(
        name="p", provider_type="openai", base_url=None, api_key="provider-key",
        default_model="gpt-4o-mini", enabled=True,
    )
    mock_client = MagicMock()
    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        client = await _build_client(provider)
    assert client is mock_client
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "provider-key"


@pytest.mark.asyncio
async def test_chat_build_client_falls_back_to_openai_api_key_env(monkeypatch):
    """chat.py's pre-PR-E ``_build_client`` fell back to os.environ when the
    provider had no api_key configured — must still work through the
    consolidated adapter-based construction."""
    from backend.api.v1.chat import _build_client

    monkeypatch.setenv("OPENAI_API_KEY", "env-fallback-key")
    provider = ModelProvider(
        name="p", provider_type="openai", base_url=None, api_key=None,
        default_model="gpt-4o-mini", enabled=True,
    )
    mock_client = MagicMock()
    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        client = await _build_client(provider)
    assert client is mock_client
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "env-fallback-key"


@pytest.mark.asyncio
async def test_chat_build_client_rejects_private_base_url():
    """New in PR-E (decision #6): chat.py's _build_client had NO SSRF guard
    at all before — routing through OpenAICompatAdapter closes that gap. No
    existing test exercised the old unguarded path (see
    tests/integration/test_chat_api.py's module docstring), so this is a
    deliberate hardening, not a regression."""
    from backend.api.v1.chat import _build_client

    provider = ModelProvider(
        name="p", provider_type="openai", base_url="http://10.0.0.5/v1", api_key="k",
        default_model="gpt-4o-mini", enabled=True,
    )
    with patch("openai.AsyncOpenAI") as mock_cls:
        with pytest.raises(HTTPException) as exc_info:
            await _build_client(provider)
    assert exc_info.value.status_code == 502
    mock_cls.assert_not_called()


# ── skill_channel.py: _build_model_call preserves dict-config shape ─────────


@pytest.mark.asyncio
async def test_skill_channel_build_model_call_uses_dict_provider_fields():
    from backend.channels.skill_channel import _build_model_call

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MagicMock())
    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        model_call = await _build_model_call(
            {"api_key": "sk-skill", "base_url": None, "model": "qwen3:4b"}
        )
        await model_call([{"role": "user", "content": "hi"}], tools=None, model="qwen3:4b", xml=False)
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "sk-skill"


@pytest.mark.asyncio
async def test_skill_channel_build_model_call_rejects_private_base_url():
    """dict provider carries no provider_type -> allow_private stays False,
    matching pre-PR-E behavior exactly (skill_channel never allowed private
    addresses through its own inline avalidate_public_url_and_ip call
    either)."""
    from backend.channels.skill_channel import _build_model_call

    with patch("openai.AsyncOpenAI") as mock_cls:
        with pytest.raises(ValueError, match="rejected"):
            await _build_model_call({"api_key": "k", "base_url": "http://127.0.0.1:11434/v1"})
    mock_cls.assert_not_called()


# ── openai/claude processors: env fallback + guard + per-record errors ─────


@pytest.mark.asyncio
async def test_openai_processor_env_fallback_and_success(monkeypatch):
    from backend.processors.openai_processor import OpenAIProcessor

    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"summary": "ok"}'))]
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    record = MagicMock()
    record.normalized_data = {"content": "hello"}

    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        proc = OpenAIProcessor()
        result = await proc.process([record], "{{content}}", {})

    assert result.success is True
    assert result.enrichments == [{"summary": "ok"}]
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "env-openai-key"


@pytest.mark.asyncio
async def test_openai_processor_rejects_private_base_url():
    from backend.processors.openai_processor import OpenAIProcessor

    record = MagicMock()
    record.normalized_data = {}
    with patch("openai.AsyncOpenAI") as mock_cls:
        proc = OpenAIProcessor()
        result = await proc.process([record], "{{content}}", {"base_url": "http://10.0.0.5/v1"})
    assert result.success is False
    assert "rejected" in result.error
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_openai_processor_per_record_error_isolation():
    """One record's LLM call failing must not fail the whole batch — the
    existing per-record try/except is preserved through the consolidated
    client construction (client is built once, reused for every record)."""
    from backend.processors.openai_processor import OpenAIProcessor

    mock_client = MagicMock()
    ok_response = MagicMock()
    ok_response.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]
    ok_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    mock_client.chat.completions.create = AsyncMock(side_effect=[Exception("boom"), ok_response])

    records = [MagicMock(normalized_data={}), MagicMock(normalized_data={})]
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        proc = OpenAIProcessor()
        result = await proc.process(records, "{{content}}", {"api_key": "k"})

    assert result.success is True
    assert "error" in result.enrichments[0]
    assert result.enrichments[1] == {"ok": True}


@pytest.mark.asyncio
async def test_claude_processor_env_fallback_and_success(monkeypatch):
    from backend.processors.claude_processor import ClaudeProcessor

    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-claude-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"summary": "ok"}')]
    mock_response.usage = MagicMock(input_tokens=3, output_tokens=2)
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    record = MagicMock()
    record.normalized_data = {"content": "hi"}

    with patch("anthropic.AsyncAnthropic", return_value=mock_client) as mock_cls:
        proc = ClaudeProcessor()
        result = await proc.process([record], "{{content}}", {})

    assert result.success is True
    assert result.enrichments == [{"summary": "ok"}]
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "env-claude-key"


@pytest.mark.asyncio
async def test_claude_processor_per_record_error_isolation():
    from backend.processors.claude_processor import ClaudeProcessor

    mock_client = MagicMock()
    ok_response = MagicMock()
    ok_response.content = [MagicMock(text='{"ok": true}')]
    ok_response.usage = MagicMock(input_tokens=1, output_tokens=1)
    mock_client.messages.create = AsyncMock(side_effect=[Exception("boom"), ok_response])

    records = [MagicMock(normalized_data={}), MagicMock(normalized_data={})]
    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        proc = ClaudeProcessor()
        result = await proc.process(records, "{{content}}", {"api_key": "k"})

    assert result.success is True
    assert "error" in result.enrichments[0]
    assert result.enrichments[1] == {"ok": True}


# ── resolver fallback safety (decision #8): consumers unaffected when
# model_defaults is not configured ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolver_absent_model_defaults_does_not_affect_chat_provider_pick(db_session):
    """PR-E deliberately does NOT wire ProviderResolver into chat.py /
    skill_channel / the processors (see PR-E report — decision #8 only
    mandates factory adoption for these three, not resolver adoption). This
    proves that choice is safe: with zero ModelDefault rows, resolver.resolve
    reports "nothing configured" (None) while chat.py's own pre-existing
    _pick_provider selection keeps working, completely unaffected and never
    consulting model_defaults at all."""
    from backend.api.v1.chat import _pick_provider

    provider = ModelProvider(
        name="Existing Provider", provider_type="openai", base_url=None,
        api_key="k", default_model="gpt-4o-mini", enabled=True,
    )
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)

    resolved = await resolver.resolve(db_session, "chat")
    assert resolved is None  # no model_defaults row for role="chat"

    picked = await _pick_provider(db_session, None)
    assert picked.id == provider.id  # existing selection unaffected, no crash
