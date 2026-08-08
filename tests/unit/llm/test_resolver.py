"""Unit tests for model-provider runtime PR-D: ``backend.llm.resolver`` (failover) and
``backend.llm.base.classify_retryable`` (decision #7's connection-vs-business
error split).

``get_adapter`` is patched at ``backend.llm.resolver.get_adapter`` so tests
can assert exactly which candidates had an adapter built (cooled candidates
must never reach it) without any real SDK/network involvement; ``operation``
(the callable ``resolve_with_fallback`` invokes per live candidate) is always
a test double, never a real adapter method. A fake injectable clock replaces
``time.monotonic`` so cooldown-window assertions never depend on real sleeps.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import openai
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.llm.base import LlmAdapterError, classify_retryable
from backend.llm.resolver import ProviderResolver, ResolverError
from backend.models.model_default import ModelDefault
from backend.models.provider import ModelProvider

SECRET_KEY = "sk-resolver-test-secret-do-not-leak"


class FakeClock:
    """Injectable monotonic clock (model-provider runtime PR-D): starts at 0.0, only moves
    when the test calls :meth:`advance` — no real sleeps anywhere here."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


async def _make_provider(db: AsyncSession, name: str) -> ModelProvider:
    provider = ModelProvider(
        name=name,
        provider_type="openai",
        base_url=None,
        api_key=SECRET_KEY,
        default_model="gpt-4o-mini",
        enabled=True,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


async def _make_default(db: AsyncSession, role: str, candidates: list[dict]) -> ModelDefault:
    row = ModelDefault(role=role, candidates=candidates)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    return httpx.Response(status_code, request=request)


# ---------------------------------------------------------------------------
# resolve() — primary candidate, no failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_returns_first_candidate(db_session: AsyncSession):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    resolver = ProviderResolver(now=FakeClock())
    resolved = await resolver.resolve(db_session, "chat")

    assert resolved is not None
    assert resolved.provider.id == provider_a.id
    assert resolved.model_id == "model-a"
    assert resolved.adapter is not None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_default_row(db_session: AsyncSession):
    resolver = ProviderResolver(now=FakeClock())
    assert await resolver.resolve(db_session, "chat") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_candidates_empty(db_session: AsyncSession):
    await _make_default(db_session, "executor", [])
    resolver = ProviderResolver(now=FakeClock())
    assert await resolver.resolve(db_session, "executor") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_primary_provider_disabled(db_session: AsyncSession):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_a.enabled = False
    await db_session.commit()
    await _make_default(
        db_session,
        "chat",
        [{"provider_id": provider_a.id, "model_id": "model-a"}],
    )
    resolver = ProviderResolver(now=FakeClock())
    assert await resolver.resolve(db_session, "chat") is None


@pytest.mark.asyncio
async def test_has_candidates_true_only_for_nonempty_role(db_session: AsyncSession):
    resolver = ProviderResolver(now=FakeClock())
    assert await resolver.has_candidates(db_session, "chat") is False
    await _make_default(
        db_session,
        "chat",
        [{"provider_id": "any-provider-id", "model_id": "model-a"}],
    )
    assert await resolver.has_candidates(db_session, "chat") is True
    await _make_default(db_session, "executor", [])
    assert await resolver.has_candidates(db_session, "executor") is False


# ---------------------------------------------------------------------------
# resolve_with_fallback() — sequential failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_failover_a_retryable_fails_b_succeeds(db_session: AsyncSession):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    clock = FakeClock()
    resolver = ProviderResolver(now=clock)

    operation = AsyncMock(
        side_effect=[LlmAdapterError("connection reset", retryable=True), "b-result"]
    )

    with patch("backend.llm.resolver.get_adapter", side_effect=lambda p: object()) as mock_get_adapter:
        result = await resolver.resolve_with_fallback(db_session, "chat", operation)

    assert result == "b-result"
    assert operation.call_count == 2
    assert mock_get_adapter.call_count == 2
    # provider-a called with model-a, provider-b called with model-b
    assert operation.call_args_list[0].args[1] == "model-a"
    assert operation.call_args_list[1].args[1] == "model-b"
    # provider-a is now cooled down (it retryable-failed)
    assert resolver._is_cooled(provider_a.id) is True
    assert resolver._is_cooled(provider_b.id) is False


@pytest.mark.asyncio
async def test_no_default_raises_resolver_error(db_session: AsyncSession):
    resolver = ProviderResolver(now=FakeClock())
    operation = AsyncMock()
    with pytest.raises(ResolverError):
        await resolver.resolve_with_fallback(db_session, "chat", operation)
    operation.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_provider_skipped_without_cooldown_uses_next(
    db_session: AsyncSession,
):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    provider_a.enabled = False
    await db_session.commit()
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    resolver = ProviderResolver(now=FakeClock())
    operation = AsyncMock(side_effect=["b-result"])

    patched_adapter = patch(
        "backend.llm.resolver.get_adapter", side_effect=lambda p: object()
    )
    with patched_adapter as mock_get_adapter:
        result = await resolver.resolve_with_fallback(db_session, "chat", operation)

    assert result == "b-result"
    # the disabled candidate never reached adapter construction or the operation
    operation.assert_called_once()
    assert operation.call_args.args[1] == "model-b"
    assert mock_get_adapter.call_count == 1
    # disabling is an operator choice, not a liveness failure → no cooldown
    assert resolver._is_cooled(provider_a.id) is False
    assert resolver._is_cooled(provider_b.id) is False


@pytest.mark.asyncio
async def test_all_candidates_disabled_raises_resolver_error(db_session: AsyncSession):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    provider_a.enabled = False
    provider_b.enabled = False
    await db_session.commit()
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    resolver = ProviderResolver(now=FakeClock())
    operation = AsyncMock()
    with pytest.raises(ResolverError, match="tried=0"):
        await resolver.resolve_with_fallback(db_session, "chat", operation)
    operation.assert_not_called()


# ---------------------------------------------------------------------------
# 4xx business error — NO failover (decision #7, required coverage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_4xx_business_error_does_not_fail_over(db_session: AsyncSession):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    clock = FakeClock()
    resolver = ProviderResolver(now=clock)
    operation = AsyncMock(side_effect=LlmAdapterError("401 bad api key", retryable=False))

    with patch("backend.llm.resolver.get_adapter", side_effect=lambda p: object()):
        with pytest.raises(LlmAdapterError, match="401 bad api key"):
            await resolver.resolve_with_fallback(db_session, "chat", operation)

    # B was NEVER tried.
    assert operation.call_count == 1
    assert operation.call_args_list[0].args[1] == "model-a"
    # A got NO cooldown entry — a 4xx is a config error, not a liveness one.
    assert resolver._is_cooled(provider_a.id) is False


# ---------------------------------------------------------------------------
# cooldown skip + window expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_skip_within_window_then_retry_after_expiry(db_session: AsyncSession):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    clock = FakeClock()
    resolver = ProviderResolver(cooldown_seconds=60.0, now=clock)

    async def operation(adapter, model_id):
        if model_id == "model-a":
            raise LlmAdapterError("boom", retryable=True)
        return f"result-{model_id}"

    with patch("backend.llm.resolver.get_adapter", side_effect=lambda p: object()) as mock_get_adapter:
        # First call: A fails (retryable), cooldown set, B succeeds.
        result1 = await resolver.resolve_with_fallback(db_session, "chat", operation)
        assert result1 == "result-model-b"
        assert mock_get_adapter.call_count == 2  # A (failed) + B (succeeded)

        # Second call, still inside the cooldown window: A must be skipped
        # WITHOUT building its adapter at all.
        mock_get_adapter.reset_mock()
        result2 = await resolver.resolve_with_fallback(db_session, "chat", operation)
        assert result2 == "result-model-b"
        assert mock_get_adapter.call_count == 1  # only B — A skipped pre-adapter-build
        built_for = [call.args[0].id for call in mock_get_adapter.call_args_list]
        assert built_for == [provider_b.id]

        # Advance the clock past the cooldown window: A is tried again.
        clock.advance(60.1)
        mock_get_adapter.reset_mock()
        result3 = await resolver.resolve_with_fallback(db_session, "chat", operation)
        assert result3 == "result-model-b"  # A still fails when retried...
        assert mock_get_adapter.call_count == 2  # ...but it WAS retried this time
        assert mock_get_adapter.call_args_list[0].args[0].id == provider_a.id


# ---------------------------------------------------------------------------
# all candidates exhausted -> clear ResolverError, no key leak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_candidates_exhausted_raises_resolver_error_without_key(
    db_session: AsyncSession,
):
    provider_a = await _make_provider(db_session, "provider-a")
    provider_b = await _make_provider(db_session, "provider-b")
    await _make_default(
        db_session,
        "chat",
        [
            {"provider_id": provider_a.id, "model_id": "model-a"},
            {"provider_id": provider_b.id, "model_id": "model-b"},
        ],
    )

    resolver = ProviderResolver(now=FakeClock())
    operation = AsyncMock(
        side_effect=LlmAdapterError(f"connection refused near {SECRET_KEY}", retryable=True)
    )

    with patch("backend.llm.resolver.get_adapter", side_effect=lambda p: object()):
        with pytest.raises(ResolverError) as exc_info:
            await resolver.resolve_with_fallback(db_session, "chat", operation)

    message = str(exc_info.value)
    assert "chat" in message
    assert SECRET_KEY not in message
    assert operation.call_count == 2
    assert resolver._is_cooled(provider_a.id) is True
    assert resolver._is_cooled(provider_b.id) is True


# ---------------------------------------------------------------------------
# concurrency: cooldown dict must not corrupt under concurrent callers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_resolve_with_fallback_is_consistent(db_engine):
    """N concurrent resolve_with_fallback calls, sharing one resolver
    instance, where the first candidate always retryable-fails: no crash,
    every call still lands on the working second candidate, and the shared
    cooldown dict ends up in a sane state (provider-a cooled)."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as setup_session:
        provider_a = await _make_provider(setup_session, "provider-a")
        provider_b = await _make_provider(setup_session, "provider-b")
        await _make_default(
            setup_session,
            "chat",
            [
                {"provider_id": provider_a.id, "model_id": "model-a"},
                {"provider_id": provider_b.id, "model_id": "model-b"},
            ],
        )

    clock = FakeClock()
    resolver = ProviderResolver(now=clock)

    async def operation(adapter, model_id):
        if model_id == "model-a":
            raise LlmAdapterError("boom", retryable=True)
        return f"result-{model_id}"

    async def run_one():
        async with session_factory() as session:
            return await resolver.resolve_with_fallback(session, "chat", operation)

    # The get_adapter patch must wrap the whole gather, NOT each task: a
    # `with patch(...)` entered per-task around an `await` makes the
    # enter/exit attribute-restore race under concurrent scheduling and can
    # leak the mock onto the module attribute after the test (breaking any
    # later test that resolves a real adapter).
    with patch("backend.llm.resolver.get_adapter", side_effect=lambda p: object()):
        results = await asyncio.gather(*(run_one() for _ in range(20)))

    assert results == ["result-model-b"] * 20
    assert resolver._is_cooled(provider_a.id) is True
    assert resolver._is_cooled(provider_b.id) is False


# ---------------------------------------------------------------------------
# classify_retryable — connection-level vs business (4xx) exceptions
# ---------------------------------------------------------------------------


def test_classify_retryable_openai_connection_error():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    assert classify_retryable(exc) is True


def test_classify_retryable_openai_timeout_error():
    exc = openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
    assert classify_retryable(exc) is True


def test_classify_retryable_openai_internal_server_error():
    exc = openai.InternalServerError("500 boom", response=_response(500), body=None)
    assert classify_retryable(exc) is True


def test_classify_retryable_openai_bad_request_error():
    exc = openai.BadRequestError("400 malformed", response=_response(400), body=None)
    assert classify_retryable(exc) is False


def test_classify_retryable_openai_authentication_error():
    exc = openai.AuthenticationError("401 bad key", response=_response(401), body=None)
    assert classify_retryable(exc) is False


def test_classify_retryable_anthropic_connection_error():
    exc = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    assert classify_retryable(exc) is True


def test_classify_retryable_anthropic_internal_server_error():
    exc = anthropic.InternalServerError("500 boom", response=_response(500), body=None)
    assert classify_retryable(exc) is True


def test_classify_retryable_anthropic_bad_request_error():
    exc = anthropic.BadRequestError("400 malformed", response=_response(400), body=None)
    assert classify_retryable(exc) is False


def test_classify_retryable_anthropic_authentication_error():
    exc = anthropic.AuthenticationError("401 bad key", response=_response(401), body=None)
    assert classify_retryable(exc) is False


def test_classify_retryable_httpx_connect_error():
    assert classify_retryable(httpx.ConnectError("connection refused")) is True


def test_classify_retryable_httpx_timeout_exception():
    assert classify_retryable(httpx.TimeoutException("timed out")) is True


def test_classify_retryable_asyncio_timeout_error():
    assert classify_retryable(asyncio.TimeoutError()) is True


def test_classify_retryable_generic_status_code_5xx_fallback():
    class _FakeStatusError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__("fake upstream error")
            self.status_code = status_code

    assert classify_retryable(_FakeStatusError(503)) is True


def test_classify_retryable_generic_status_code_4xx_fallback():
    class _FakeStatusError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__("fake upstream error")
            self.status_code = status_code

    assert classify_retryable(_FakeStatusError(404)) is False


def test_classify_retryable_unrecognized_exception_defaults_false():
    assert classify_retryable(ValueError("something unrelated")) is False
