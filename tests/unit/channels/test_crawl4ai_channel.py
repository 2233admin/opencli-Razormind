"""Unit tests for the Crawl4AI channel — mocks crawl4ai.AsyncWebCrawler (the
package's browser lifecycle is intentionally NOT under this repo's
browser_pool, per ADR: Crawl4AI manages its own anti-detection browser)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.channels.base import ChannelFetchError, FetchContext
from backend.channels.crawl4ai_channel import Crawl4AIChannel
from backend.control.error_kinds import ErrorKind, map_error_type


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def channel():
    return Crawl4AIChannel()


@pytest.fixture(autouse=True)
def _public_dns():
    """Let mocked crawler calls pass the real SSRF validation deterministically."""
    with patch("socket.getaddrinfo", return_value=[(None, None, None, "", ("93.184.216.34", 0))]):
        yield


def _make_crawler_ctx(result):
    crawler = AsyncMock()
    crawler.arun = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=crawler)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, crawler


def _make_result(success=True, extracted_content=None, error_message=None, status_code=200):
    result = MagicMock()
    result.success = success
    result.extracted_content = extracted_content
    result.error_message = error_message
    result.status_code = status_code
    return result


# ── validate_config ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_validate_config_missing_url(channel):
    errors = await channel.validate_config({"selectors": {"title": "h1"}})
    assert "'url' is required for crawl4ai channel" in errors


@pytest.mark.asyncio
async def test_validate_config_missing_selectors_and_instruction(channel):
    errors = await channel.validate_config({"url": "https://example.com"})
    assert any("selectors" in e and "instruction" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_valid(channel):
    errors = await channel.validate_config(
        {"url": "https://example.com", "selectors": {"title": "h1"}}
    )
    assert errors == []


@pytest.mark.asyncio
async def test_validate_config_instruction_only_is_valid(channel):
    errors = await channel.validate_config(
        {"url": "https://example.com", "instruction": "extract the article title and author"}
    )
    assert errors == []


# ── fetch() ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_success_parses_extracted_items(channel):
    import json

    result = _make_result(extracted_content=json.dumps([{"title": "Alpha"}, {"title": "Beta"}]))
    ctx_mgr, crawler = _make_crawler_ctx(result)

    ctx = FetchContext(
        config={
            "url": "https://example.com",
            "list_selector": ".item",
            "selectors": {"title": "h2"},
        },
        params={},
    )
    with patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr):
        fetch_result = await channel.fetch(ctx)

    assert fetch_result.items == [{"title": "Alpha"}, {"title": "Beta"}]
    assert fetch_result.metadata == {"url": "https://example.com", "status_code": 200}
    crawler.arun.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_no_url_raises(channel):
    ctx = FetchContext(config={"selectors": {"title": "h1"}}, params={})
    with pytest.raises(ChannelFetchError, match="'url' is required"):
        await channel.fetch(ctx)


@pytest.mark.asyncio
async def test_fetch_crawl_failure_raises_with_error_message(channel):
    result = _make_result(success=False, error_message="blocked by anti-bot")
    ctx_mgr, _crawler = _make_crawler_ctx(result)

    ctx = FetchContext(
        config={"url": "https://example.com", "selectors": {"title": "h1"}}, params={}
    )
    with patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr):
        with pytest.raises(ChannelFetchError, match="blocked by anti-bot"):
            await channel.fetch(ctx)


@pytest.mark.asyncio
async def test_fetch_malformed_extracted_content_raises(channel):
    result = _make_result(extracted_content="not-json{{{")
    ctx_mgr, _crawler = _make_crawler_ctx(result)

    ctx = FetchContext(
        config={"url": "https://example.com", "selectors": {"title": "h1"}}, params={}
    )
    with patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr):
        with pytest.raises(ChannelFetchError) as exc_info:
            await channel.fetch(ctx)

    # WIRING_GAP_LEDGER W1: parse failures must carry error_type so the
    # SCHEMA_DRIFT chain (error_kinds -> control.recorder) fires instead of
    # being dropped by recorder's `elif error_type is not None` guard.
    assert exc_info.value.error_type == "JSONDecodeError"
    assert map_error_type(exc_info.value.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_fetch_cookie_auth_passes_resolved_cookies_to_browser_config(channel):
    import json

    result = _make_result(extracted_content=json.dumps([{"title": "Alpha"}]))
    ctx_mgr, _crawler = _make_crawler_ctx(result)

    ctx = FetchContext(
        config={
            "url": "https://example.com/page",
            "selectors": {"title": "h1"},
            "auth": {"type": "cookie"},
        },
        params={},
    )
    fake_cookies = [{"name": "session_id", "domain": "example.com", "value": "abc"}]
    with (
        patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr) as crawler_ctor,
        patch(
            "backend.auth.manager.AuthManager.resolve_cookies", AsyncMock(return_value=fake_cookies)
        ) as resolve_cookies,
        patch("crawl4ai.BrowserConfig") as browser_config_ctor,
    ):
        await channel.fetch(ctx)

    resolve_cookies.assert_awaited_once_with("example.com")
    assert browser_config_ctor.call_args.kwargs["cookies"] == fake_cookies
    crawler_ctor.assert_called_once()


# ── LLM extraction fallback (no 'selectors' configured) ─────────────────────
@pytest.mark.asyncio
async def test_fetch_llm_fallback_no_instruction_raises(channel):
    ctx = FetchContext(config={"url": "https://example.com"}, params={})
    with pytest.raises(ChannelFetchError, match="provide 'instruction'"):
        await channel.fetch(ctx)


@pytest.mark.asyncio
async def test_fetch_llm_fallback_builds_llm_strategy_when_no_selectors(channel):
    import json

    result = _make_result(extracted_content=json.dumps([{"title": "Alpha"}]))
    ctx_mgr, crawler = _make_crawler_ctx(result)

    ctx = FetchContext(
        config={"url": "https://example.com", "instruction": "extract the article title"},
        params={},
    )
    fake_llm_config = MagicMock()
    with (
        patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr),
        patch(
            "backend.channels.crawl4ai_channel.Crawl4AIChannel._resolve_llm_config",
            AsyncMock(return_value=fake_llm_config),
        ) as resolve_llm,
    ):
        fetch_result = await channel.fetch(ctx)

    assert fetch_result.items == [{"title": "Alpha"}]
    resolve_llm.assert_awaited_once_with(None)
    run_config = crawler.arun.call_args.kwargs["config"]
    assert run_config.extraction_strategy.instruction == "extract the article title"
    assert run_config.extraction_strategy.llm_config is fake_llm_config
    assert run_config.extraction_strategy.extract_type == "block"


@pytest.mark.asyncio
async def test_fetch_llm_fallback_with_schema_uses_schema_extraction_type(channel):
    import json

    result = _make_result(extracted_content=json.dumps({"title": "Alpha"}))
    ctx_mgr, crawler = _make_crawler_ctx(result)

    ctx = FetchContext(
        config={
            "url": "https://example.com",
            "instruction": "extract structured fields",
            "extraction_schema": {"title": "string"},
            "provider_id": "provider-123",
        },
        params={},
    )
    fake_llm_config = MagicMock()
    with (
        patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr),
        patch(
            "backend.channels.crawl4ai_channel.Crawl4AIChannel._resolve_llm_config",
            AsyncMock(return_value=fake_llm_config),
        ) as resolve_llm,
    ):
        fetch_result = await channel.fetch(ctx)

    assert fetch_result.items == [{"title": "Alpha"}]
    resolve_llm.assert_awaited_once_with("provider-123")
    run_config = crawler.arun.call_args.kwargs["config"]
    assert run_config.extraction_strategy.extract_type == "schema"
    assert run_config.extraction_strategy.schema == {"title": "string"}


@pytest.mark.asyncio
async def test_resolve_llm_config_no_enabled_provider_raises(channel, db_engine):
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        with pytest.raises(ChannelFetchError, match="no enabled model provider"):
            await channel._resolve_llm_config(None)


@pytest.mark.asyncio
async def test_resolve_llm_config_maps_claude_provider_to_anthropic_prefix(channel, db_engine):
    from backend.models.provider import ModelProvider

    sm = _sessionmaker(db_engine)
    async with sm() as session:
        session.add(
            ModelProvider(
                name="my-claude",
                provider_type="claude",
                api_key="sk-test",
                default_model="claude-haiku-4-5-20251001",
            )
        )
        await session.commit()

    with patch("backend.database.AsyncSessionLocal", sm):
        llm_config = await channel._resolve_llm_config(None)

    assert llm_config.provider == "anthropic/claude-haiku-4-5-20251001"
    assert llm_config.api_token == "sk-test"


@pytest.mark.asyncio
async def test_resolve_llm_config_defaults_unknown_provider_type_to_openai_prefix(
    channel, db_engine
):
    from backend.models.provider import ModelProvider

    sm = _sessionmaker(db_engine)
    async with sm() as session:
        session.add(
            ModelProvider(
                name="local-gateway", provider_type="local", api_key="k", default_model="llama3"
            )
        )
        await session.commit()

    with patch("backend.database.AsyncSessionLocal", sm):
        llm_config = await channel._resolve_llm_config(None)

    assert llm_config.provider == "openai/llama3"


# ── health_check ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_health_check_no_config_returns_true(channel):
    assert await channel.health_check(None) is True


@pytest.mark.asyncio
async def test_health_check_no_url_returns_false(channel):
    assert await channel.health_check({}) is False


@pytest.mark.asyncio
async def test_health_check_package_unavailable_returns_false(channel):
    with patch.dict("sys.modules", {"crawl4ai": None}):
        assert await channel.health_check({"url": "https://example.com"}) is False


@pytest.mark.asyncio
async def test_health_check_success(channel):
    result = _make_result(success=True)
    ctx_mgr, _crawler = _make_crawler_ctx(result)
    with patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr):
        assert await channel.health_check({"url": "https://example.com"}) is True


@pytest.mark.asyncio
async def test_health_check_crawl_exception_returns_false(channel):
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(side_effect=RuntimeError("browser launch failed"))
    with patch("crawl4ai.AsyncWebCrawler", return_value=ctx_mgr):
        assert await channel.health_check({"url": "https://example.com"}) is False


# ── registry ─────────────────────────────────────────────────────────────────
def test_crawl4ai_registered():
    from backend.channels.registry import list_channel_types

    assert "crawl4ai" in list_channel_types()
