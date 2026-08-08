"""Production call-chain tests for issue #55: the agent dock ``POST
/api/v1/chat`` endpoint routed through ``ProviderResolver.resolve_with_fallback``
for the ``chat`` role.

The only mocked seam is the network boundary — ``backend.api.v1.chat._build_client``
is replaced with fake OpenAI-compatible clients whose ``chat.completions.create``
can be scripted to raise SDK exceptions. Everything else (HTTP endpoint,
``model_defaults`` candidates, resolver failover/cooldown, provider rows,
legacy fallback) runs for real, so these prove the wiring the resolver unit
tests cannot: that a configured failover order actually drives the production
chat path, and that installs without candidates keep the pre-failover
behavior.
"""

import httpx
import openai
import pytest

from backend.llm.resolver import ProviderResolver


# ── fake OpenAI-compatible client (network seam) ────────────────────────────
class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = []


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, exc: Exception | None, content: str) -> None:
        self._exc = exc
        self._content = content

    async def create(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, exc: Exception | None, content: str) -> None:
        self.completions = _FakeCompletions(exc, content)


class _FakeClient:
    def __init__(self, exc: Exception | None, content: str) -> None:
        self.chat = _FakeChat(exc, content)


def _connection_error() -> openai.APIConnectionError:
    request = httpx.Request("POST", "http://fake-provider/chat/completions")
    return openai.APIConnectionError(request=request)


def _auth_error() -> openai.AuthenticationError:
    request = httpx.Request("POST", "http://fake-provider/chat/completions")
    response = httpx.Response(401, request=request)
    return openai.AuthenticationError("401 bad key", response=response, body=None)


# ── setup helpers (real API path) ───────────────────────────────────────────
async def _create_provider(client, *, name: str, enabled: bool = True) -> str:
    resp = await client.post(
        "/api/v1/providers",
        json={
            "name": name,
            "provider_type": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test-key",
            "default_model": "gpt-4o-mini",
            "enabled": enabled,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _register_model(client, provider_id: str, model_id: str) -> None:
    resp = await client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": model_id})
    assert resp.status_code == 201, resp.text


async def _put_chat_defaults(client, candidates: list[dict]) -> None:
    resp = await client.put("/api/v1/model-defaults/chat", json={"candidates": candidates})
    assert resp.status_code == 200, resp.text


def _patch_network(monkeypatch, behavior: dict[str, Exception | str]):
    """behavior: provider_id -> str (success content) or Exception (raised)."""
    built: list[str] = []

    async def fake_build(provider):
        built.append(provider.id)
        value = behavior[provider.id]
        if isinstance(value, Exception):
            return _FakeClient(exc=value, content="")
        return _FakeClient(exc=None, content=value)

    monkeypatch.setattr("backend.api.v1.chat._build_client", fake_build)
    # isolate cooldown state per test (the module singleton would persist it)
    monkeypatch.setattr("backend.api.v1.chat.resolver", ProviderResolver())
    return built


async def _chat(client, message: str = "hi") -> httpx.Response:
    payload = {"messages": [{"role": "user", "content": message}]}
    return await client.post("/api/v1/chat", json=payload)


# ── legacy path: no model_defaults configured ───────────────────────────────
def _patch_network_with_default(monkeypatch, behavior: dict[str, Exception | str], default: str):
    """Like _patch_network, but providers absent from ``behavior`` succeed with ``default``."""
    built: list[str] = []

    async def fake_build(provider):
        built.append(provider.id)
        value = behavior.get(provider.id, default)
        if isinstance(value, Exception):
            return _FakeClient(exc=value, content="")
        return _FakeClient(exc=None, content=value)

    monkeypatch.setattr("backend.api.v1.chat._build_client", fake_build)
    monkeypatch.setattr("backend.api.v1.chat.resolver", ProviderResolver())
    return built


@pytest.mark.asyncio
async def test_legacy_path_without_model_defaults_uses_first_enabled_provider(
    client, db_session, monkeypatch
):
    await _create_provider(client, name="Legacy Only")
    built = _patch_network_with_default(monkeypatch, {}, default="legacy reply")

    resp = await _chat(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["type"] == "message"
    assert resp.json()["data"]["content"] == "legacy reply"
    assert len(built) == 1


@pytest.mark.asyncio
async def test_legacy_path_without_enabled_provider_returns_400(client, db_session, monkeypatch):
    await _create_provider(client, name="Disabled Only", enabled=False)
    _patch_network_with_default(monkeypatch, {}, default="unused")

    resp = await _chat(client)

    assert resp.status_code == 400
    assert "没有可用的模型 provider" in resp.json()["detail"]


# ── failover path: candidates configured ────────────────────────────────────
@pytest.mark.asyncio
async def test_retryable_primary_failure_fails_over_to_secondary(
    client, db_session, monkeypatch
):
    primary = await _create_provider(client, name="Primary")
    secondary = await _create_provider(client, name="Secondary")
    await _register_model(client, primary, "model-a")
    await _register_model(client, secondary, "model-b")
    await _put_chat_defaults(
        client,
        [
            {"provider_id": primary, "model_id": "model-a"},
            {"provider_id": secondary, "model_id": "model-b"},
        ],
    )
    built = _patch_network(monkeypatch, {primary: _connection_error(), secondary: "reply from b"})

    resp = await _chat(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["content"] == "reply from b"
    assert built == [primary, secondary]  # tried in candidate order


@pytest.mark.asyncio
async def test_business_error_does_not_fail_over(client, db_session, monkeypatch):
    primary = await _create_provider(client, name="Primary")
    secondary = await _create_provider(client, name="Secondary")
    await _register_model(client, primary, "model-a")
    await _register_model(client, secondary, "model-b")
    await _put_chat_defaults(
        client,
        [
            {"provider_id": primary, "model_id": "model-a"},
            {"provider_id": secondary, "model_id": "model-b"},
        ],
    )
    built = _patch_network(monkeypatch, {primary: _auth_error(), secondary: "unused"})

    resp = await _chat(client)

    # 4xx = configuration problem → re-raised immediately (decision #7), no failover
    assert resp.status_code == 502
    assert "模型调用失败" in resp.json()["detail"]
    assert built == [primary]  # secondary never built


@pytest.mark.asyncio
async def test_all_candidates_unavailable_returns_502(client, db_session, monkeypatch):
    primary = await _create_provider(client, name="Primary")
    secondary = await _create_provider(client, name="Secondary")
    await _register_model(client, primary, "model-a")
    await _register_model(client, secondary, "model-b")
    await _put_chat_defaults(
        client,
        [
            {"provider_id": primary, "model_id": "model-a"},
            {"provider_id": secondary, "model_id": "model-b"},
        ],
    )
    built = _patch_network(
        monkeypatch, {primary: _connection_error(), secondary: _connection_error()}
    )

    resp = await _chat(client)

    assert resp.status_code == 502
    assert "no live provider candidate" in resp.json()["detail"]
    assert built == [primary, secondary]


@pytest.mark.asyncio
async def test_disabled_candidate_is_skipped(client, db_session, monkeypatch):
    primary = await _create_provider(client, name="Primary Disabled", enabled=False)
    secondary = await _create_provider(client, name="Secondary")
    await _register_model(client, primary, "model-a")
    await _register_model(client, secondary, "model-b")
    await _put_chat_defaults(
        client,
        [
            {"provider_id": primary, "model_id": "model-a"},
            {"provider_id": secondary, "model_id": "model-b"},
        ],
    )
    built = _patch_network(monkeypatch, {secondary: "reply from b"})

    resp = await _chat(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["content"] == "reply from b"
    assert built == [secondary]  # disabled primary never built


@pytest.mark.asyncio
async def test_explicit_provider_id_bypasses_failover(client, db_session, monkeypatch):
    primary = await _create_provider(client, name="Primary")
    secondary = await _create_provider(client, name="Secondary")
    await _register_model(client, primary, "model-a")
    await _register_model(client, secondary, "model-b")
    await _put_chat_defaults(
        client,
        [
            {"provider_id": primary, "model_id": "model-a"},
            {"provider_id": secondary, "model_id": "model-b"},
        ],
    )
    built = _patch_network(monkeypatch, {primary: "explicit reply", secondary: "unused"})

    resp = await client.post(
        "/api/v1/chat",
        json={
            "provider_id": primary,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["content"] == "explicit reply"
    assert built == [primary]
