"""Unit tests for backend.llm's runtime adapters (model-provider runtime PR-B).

Every SDK client (openai.AsyncOpenAI / anthropic.AsyncAnthropic) is mocked at
the class level — these tests never make a real network call. The two
security-critical properties get dedicated coverage:

  * the url_guard rejection path: a blocked base_url must raise *before* the
    key-bearing SDK client is ever constructed (proven by asserting the
    mocked SDK class was never called), and
  * api_key never appears in any error string these adapters raise/return.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.llm.anthropic import AnthropicAdapter
from backend.llm.base import LlmAdapterError
from backend.llm.catalog import anthropic_catalog
from backend.llm.factory import get_adapter
from backend.llm.openai_compat import OpenAICompatAdapter
from backend.models.provider import ModelProvider

SECRET_KEY = "sk-test-super-secret-do-not-leak-12345"


def _provider(**overrides) -> ModelProvider:
    defaults = dict(
        name="test-provider",
        provider_type="openai",
        base_url="https://api.example.com/v1",
        api_key=SECRET_KEY,
        default_model="gpt-4o-mini",
        enabled=True,
    )
    defaults.update(overrides)
    return ModelProvider(**defaults)


def _models_page(ids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(data=[SimpleNamespace(id=i) for i in ids])


def _chat_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


# ── OpenAICompatAdapter: chat / list_models / test_connection ──────────────


@pytest.mark.asyncio
async def test_openai_compat_chat_returns_text():
    provider = _provider(base_url=None)  # skip guard/DNS — not under test here
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_chat_response("hi there"))
    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        adapter = OpenAICompatAdapter(provider)
        result = await adapter.chat([{"role": "user", "content": "hello"}])
    assert result == "hi there"
    mock_cls.assert_called_once()
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o-mini"  # provider.default_model


@pytest.mark.asyncio
async def test_openai_compat_chat_model_override():
    provider = _provider(base_url=None)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_chat_response("ok"))
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        adapter = OpenAICompatAdapter(provider)
        await adapter.chat([{"role": "user", "content": "hello"}], model="gpt-4o")
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_openai_compat_list_models_returns_ids():
    provider = _provider(base_url=None)
    mock_client = MagicMock()
    mock_client.models.list = AsyncMock(return_value=_models_page(["gpt-4o-mini", "gpt-4o"]))
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        adapter = OpenAICompatAdapter(provider)
        models = await adapter.list_models()
    assert models == ["gpt-4o-mini", "gpt-4o"]


@pytest.mark.asyncio
async def test_openai_compat_test_connection_success():
    provider = _provider(base_url=None)
    mock_client = MagicMock()
    mock_client.models.list = AsyncMock(return_value=_models_page(["gpt-4o-mini"]))
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        adapter = OpenAICompatAdapter(provider)
        result = await adapter.test_connection()
    assert result["ok"] is True
    assert isinstance(result["latency_ms"], float)
    assert result["models_sample"] == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_openai_compat_test_connection_failure_sanitized():
    provider = _provider(base_url=None)
    mock_client = MagicMock()
    mock_client.models.list = AsyncMock(
        side_effect=Exception(f"401 unauthorized: bad key {SECRET_KEY}")
    )
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        adapter = OpenAICompatAdapter(provider)
        result = await adapter.test_connection()
    assert result["ok"] is False
    assert SECRET_KEY not in result["error"]
    assert "REDACTED" in result["error"]


@pytest.mark.asyncio
async def test_openai_compat_chat_failure_raises_llm_adapter_error_without_key():
    provider = _provider(base_url=None)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=Exception(f"upstream rejected key={SECRET_KEY}")
    )
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        adapter = OpenAICompatAdapter(provider)
        with pytest.raises(LlmAdapterError) as exc_info:
            await adapter.chat([{"role": "user", "content": "hi"}])
    assert SECRET_KEY not in str(exc_info.value)


# ── url_guard rejection: guard runs BEFORE a key-bearing client is built ───


@pytest.mark.asyncio
async def test_openai_compat_rejects_private_base_url_before_building_client():
    """A blocked base_url must raise before openai.AsyncOpenAI is ever
    constructed — proving the api_key never gets attached to a client aimed
    at a non-public host."""
    provider = _provider(provider_type="openai", base_url="http://10.0.0.5/v1")
    with patch("openai.AsyncOpenAI") as mock_cls:
        adapter = OpenAICompatAdapter(provider)
        with pytest.raises(LlmAdapterError, match="rejected"):
            await adapter.list_models()
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_openai_compat_rejects_private_base_url_error_has_no_key():
    provider = _provider(provider_type="openai", base_url="http://127.0.0.1:8080/v1")
    with patch("openai.AsyncOpenAI"):
        adapter = OpenAICompatAdapter(provider)
        with pytest.raises(LlmAdapterError) as exc_info:
            await adapter.chat([{"role": "user", "content": "hi"}])
    assert SECRET_KEY not in str(exc_info.value)


# ── local-address exemption (decision #6) ───────────────────────────────────


@pytest.mark.asyncio
async def test_openai_compat_local_type_allows_loopback_base_url():
    """provider_type == "local" is exempted from the private/loopback block
    (ollama on 127.0.0.1) — the guard must not reject building the client."""
    provider = _provider(provider_type="local", base_url="http://127.0.0.1:11434/v1")
    mock_client = MagicMock()
    mock_client.models.list = AsyncMock(return_value=_models_page(["qwen3:4b"]))
    with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
        adapter = OpenAICompatAdapter(provider)
        models = await adapter.list_models()
    mock_cls.assert_called_once()
    assert models == ["qwen3:4b"]


@pytest.mark.asyncio
async def test_openai_compat_openai_type_rejects_same_loopback_url():
    """The exact same loopback URL that is fine for provider_type=="local"
    must still be rejected for provider_type=="openai" — the exemption is
    scoped strictly by type, not by address."""
    provider = _provider(provider_type="openai", base_url="http://127.0.0.1:11434/v1")
    with patch("openai.AsyncOpenAI") as mock_cls:
        adapter = OpenAICompatAdapter(provider)
        with pytest.raises(LlmAdapterError, match="rejected"):
            await adapter.list_models()
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_openai_compat_local_type_still_pins_transport():
    """Even with the private-IP allowance, the client is still built with a
    pinned http_client (DNS-rebind protection is unaffected by the
    local-address exemption — allow_private only changes which addresses
    pass the block-list check)."""
    provider = _provider(provider_type="local", base_url="http://127.0.0.1:11434/v1")
    with patch("openai.AsyncOpenAI") as mock_cls:
        adapter = OpenAICompatAdapter(provider)
        await adapter._get_client()
    _, kwargs = mock_cls.call_args
    assert kwargs["http_client"] is not None
    await adapter.aclose()


# ── AnthropicAdapter: chat / list_models / test_connection ──────────────────


def _anthropic_provider(**overrides) -> ModelProvider:
    defaults = dict(
        name="claude-provider",
        provider_type="claude",
        base_url=None,
        api_key=SECRET_KEY,
        default_model=None,
        enabled=True,
    )
    defaults.update(overrides)
    return ModelProvider(**defaults)


def _anthropic_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


@pytest.mark.asyncio
async def test_anthropic_chat_returns_text():
    provider = _anthropic_provider()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_anthropic_response("claude says hi"))
    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        adapter = AnthropicAdapter(provider)
        result = await adapter.chat([{"role": "user", "content": "hello"}])
    assert result == "claude says hi"


@pytest.mark.asyncio
async def test_anthropic_list_models_returns_catalog():
    provider = _anthropic_provider()
    adapter = AnthropicAdapter(provider)
    models = await adapter.list_models()
    assert models == [entry["model_id"] for entry in anthropic_catalog()]


@pytest.mark.asyncio
async def test_anthropic_test_connection_success():
    provider = _anthropic_provider()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_anthropic_response("pong"))
    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        adapter = AnthropicAdapter(provider)
        result = await adapter.test_connection()
    assert result["ok"] is True
    assert isinstance(result["latency_ms"], float)
    assert result["models_sample"]


@pytest.mark.asyncio
async def test_anthropic_test_connection_failure_sanitized():
    provider = _anthropic_provider()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        side_effect=Exception(f"invalid x-api-key {SECRET_KEY}")
    )
    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        adapter = AnthropicAdapter(provider)
        result = await adapter.test_connection()
    assert result["ok"] is False
    assert SECRET_KEY not in result["error"]
    assert "REDACTED" in result["error"]


@pytest.mark.asyncio
async def test_anthropic_chat_failure_raises_without_key():
    provider = _anthropic_provider()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        side_effect=Exception(f"bad key {SECRET_KEY}")
    )
    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        adapter = AnthropicAdapter(provider)
        with pytest.raises(LlmAdapterError) as exc_info:
            await adapter.chat([{"role": "user", "content": "hi"}])
    assert SECRET_KEY not in str(exc_info.value)


@pytest.mark.asyncio
async def test_anthropic_rejects_private_base_url_before_building_client():
    provider = _anthropic_provider(base_url="http://10.0.0.5:9999")
    with patch("anthropic.AsyncAnthropic") as mock_cls:
        adapter = AnthropicAdapter(provider)
        with pytest.raises(LlmAdapterError, match="rejected"):
            await adapter.chat([{"role": "user", "content": "hi"}])
    mock_cls.assert_not_called()


# ── factory dispatch ─────────────────────────────────────────────────────────


def test_factory_dispatches_openai():
    assert isinstance(get_adapter(_provider(provider_type="openai")), OpenAICompatAdapter)


def test_factory_dispatches_local():
    assert isinstance(get_adapter(_provider(provider_type="local")), OpenAICompatAdapter)


def test_factory_dispatches_claude():
    assert isinstance(get_adapter(_anthropic_provider(provider_type="claude")), AnthropicAdapter)


def test_factory_unknown_provider_type_raises():
    with pytest.raises(LlmAdapterError, match="no adapter registered"):
        get_adapter(_provider(provider_type="carrier-pigeon"))
