"""Unit tests for the web scraper channel."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from backend.channels.base import ChannelFetchError, FetchContext
from backend.channels.web_scraper_channel import WebScraperChannel


@pytest.fixture
def channel():
    return WebScraperChannel()


@pytest.fixture(autouse=True)
def _public_dns():
    """Let mocked HTTP calls pass the real SSRF validation deterministically."""
    with patch("socket.getaddrinfo", return_value=[(None, None, None, "", ("93.184.216.34", 0))]):
        yield


SAMPLE_HTML = """
<html>
<body>
  <ul>
    <li class="item">
      <h2 class="title">Item Alpha</h2>
      <span class="price">$10</span>
    </li>
    <li class="item">
      <h2 class="title">Item Beta</h2>
      <span class="price">$20</span>
    </li>
  </ul>
  <h1 class="page-title">Products</h1>
</body>
</html>
"""


def _make_mock_response(status_code=200, text=SAMPLE_HTML):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def _make_mock_client(response):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_client_ctx


# ── validate_config ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_config_missing_url(channel):
    errors = await channel.validate_config({"selectors": {"title": "h1"}})
    assert any("url" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_missing_selectors(channel):
    errors = await channel.validate_config({"url": "https://example.com"})
    assert any("selectors" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_valid(channel):
    errors = await channel.validate_config(
        {
            "url": "https://example.com",
            "selectors": {"title": "h1"},
        }
    )
    assert errors == []


@pytest.mark.asyncio
async def test_validate_config_missing_both(channel):
    errors = await channel.validate_config({})
    assert len(errors) == 2


# ── collect: with list_selector ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_with_list_selector(channel):
    """list_selector finds multiple containers and extracts fields from each."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "list_selector": "li.item",
                "selectors": {"title": "h2.title", "price": "span.price"},
            },
            {},
        )

    assert result.success is True
    assert len(result.items) == 2
    assert result.items[0]["title"] == "Item Alpha"
    assert result.items[0]["price"] == "$10"
    assert result.items[1]["title"] == "Item Beta"
    assert result.items[1]["price"] == "$20"


@pytest.mark.asyncio
async def test_collect_list_selector_no_matches(channel):
    """list_selector with no matches returns empty items list."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "list_selector": "div.nonexistent",
                "selectors": {"title": "h2"},
            },
            {},
        )

    assert result.success is True
    assert len(result.items) == 0


# ── collect: without list_selector ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_without_list_selector(channel):
    """Without list_selector, extracts a single item from whole page."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "selectors": {"page_title": "h1.page-title"},
            },
            {},
        )

    assert result.success is True
    assert len(result.items) == 1
    assert result.items[0]["page_title"] == "Products"


@pytest.mark.asyncio
async def test_collect_metadata_includes_url_and_status(channel):
    """ChannelResult metadata contains url and status_code."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "selectors": {"title": "h1"},
            },
            {},
        )

    assert result.success is True
    assert result.metadata.get("url") == "https://example.com"
    assert result.metadata.get("status_code") == 200


# ── AUDIT C22: BeautifulSoup parse off-loaded via asyncio.to_thread ────────────

@pytest.mark.asyncio
async def test_collect_parses_html_off_event_loop_thread(channel):
    """BeautifulSoup(...) must run via asyncio.to_thread, not inline on the
    event loop — a large scraped page would otherwise freeze every other
    request/task on this process for the duration of the parse."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    seen_threads: list[int] = []
    real_soup = BeautifulSoup

    def spy_soup(markup, parser):
        seen_threads.append(threading.get_ident())
        return real_soup(markup, parser)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx), patch(
        "backend.channels.web_scraper_channel.BeautifulSoup", side_effect=spy_soup
    ):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "selectors": {"page_title": "h1.page-title"},
            },
            {},
        )

    assert result.success is True
    assert result.items[0]["page_title"] == "Products"
    assert len(seen_threads) == 1
    assert seen_threads[0] != threading.get_ident()


# ── collect: error cases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_timeout_returns_fail(channel):
    """TimeoutException produces a failed ChannelResult."""
    import httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"url": "https://example.com", "selectors": {"title": "h1"}}, {}
        )

    assert result.success is False
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_http_error_returns_fail(channel):
    """HTTP 403 status produces a failed ChannelResult."""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            message="Forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        )
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"url": "https://example.com", "selectors": {"title": "h1"}}, {}
        )

    assert result.success is False
    assert "403" in result.error


@pytest.mark.asyncio
async def test_collect_generic_exception_returns_fail(channel):
    """Connection errors produce a failed ChannelResult."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"url": "https://example.com", "selectors": {"title": "h1"}}, {}
        )

    assert result.success is False
    assert "Request failed" in result.error


# ── _extract_fields ────────────────────────────────────────────────────────────


def test_extract_fields(channel):
    html = "<html><body><h1>Hello World</h1><p class='desc'>Description</p></body></html>"
    soup = BeautifulSoup(html, "lxml")
    selectors = {"title": "h1", "description": "p.desc"}
    result = channel._extract_fields(soup, selectors)
    assert result["title"] == "Hello World"
    assert result["description"] == "Description"


def test_extract_fields_missing_selector(channel):
    html = "<html><body><h1>Hello</h1></body></html>"
    soup = BeautifulSoup(html, "lxml")
    result = channel._extract_fields(soup, {"title": "h1", "missing": ".no-exist"})
    assert result["title"] == "Hello"
    assert "missing" not in result


def test_extract_fields_empty_selectors(channel):
    html = "<html><body><h1>Hello</h1></body></html>"
    soup = BeautifulSoup(html, "lxml")
    result = channel._extract_fields(soup, {})
    assert result == {}


def test_extract_fields_strips_whitespace(channel):
    html = "<html><body><p class='p'>  spaced text  </p></body></html>"
    soup = BeautifulSoup(html, "lxml")
    result = channel._extract_fields(soup, {"text": "p.p"})
    assert result["text"] == "spaced text"


@pytest.mark.asyncio
async def test_collect_custom_headers_sent(channel):
    """Custom headers from config are merged with default User-Agent."""
    response = _make_mock_response()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    captured_headers = {}

    def fake_client_constructor(**kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return mock_client_ctx

    with patch("httpx.AsyncClient", side_effect=fake_client_constructor):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "selectors": {"title": "h1"},
                "headers": {"X-My-Header": "custom-value"},
            },
            {},
        )

    assert result.success is True
    assert captured_headers.get("X-My-Header") == "custom-value"
    assert "User-Agent" in captured_headers


# ── runtime-hardening phase PR-D: fetch() thick contract (ctx.http path — rate limit/retry) ──────


@pytest.mark.asyncio
async def test_fetch_uses_shared_client_with_per_request_headers(channel):
    """When ctx.http is present (the runner's RateLimitedClient), headers
    can't be baked into a shared client's constructor — they must go on the
    per-request .get() call instead."""
    response = _make_mock_response()
    shared_client = AsyncMock()
    shared_client.get = AsyncMock(return_value=response)

    ctx = FetchContext(
        config={
            "url": "https://example.com",
            "selectors": {"page_title": "h1.page-title"},
            "headers": {"X-My-Header": "v"},
        },
        params={},
        http=shared_client,
    )
    result = await channel.fetch(ctx)

    assert result.items[0]["page_title"] == "Products"
    call_kwargs = shared_client.get.call_args.kwargs
    assert call_kwargs["headers"]["X-My-Header"] == "v"
    assert "User-Agent" in call_kwargs["headers"]


@pytest.mark.asyncio
async def test_fetch_raises_channel_fetch_error_on_timeout():
    """fetch()'s raise-based contract (not collect()'s return-based one)."""
    import httpx

    channel = WebScraperChannel()
    shared_client = AsyncMock()
    shared_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    ctx = FetchContext(
        config={"url": "https://example.com", "selectors": {"title": "h1"}},
        params={},
        http=shared_client,
    )
    with pytest.raises(ChannelFetchError, match="timed out"):
        await channel.fetch(ctx)


@pytest.mark.asyncio
async def test_collect_still_delegates_to_fetch_without_ctx_http(channel):
    """collect()'s thin-wrapper contract: no ctx.http, own client, same
    behaviour as before the migration (already covered above by the full
    old test suite passing unchanged — this just pins the delegation)."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        with patch.object(channel, "fetch", wraps=channel.fetch) as spy:
            result = await channel.collect(
                {"url": "https://example.com", "selectors": {"page_title": "h1.page-title"}}, {}
            )

    spy.assert_called_once()
    assert result.success is True


# ── health_check (runtime-hardening phase PR-E: real per-source probe) ───────────────────────────


@pytest.mark.asyncio
async def test_health_check_no_config_is_parser_liveness_only(channel):
    assert await channel.health_check() is True


@pytest.mark.asyncio
async def test_health_check_missing_url_is_unhealthy(channel):
    assert await channel.health_check({}) is False


@pytest.mark.asyncio
async def test_health_check_reachable_returns_true(channel):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.health_check({"url": "https://example.com"})

    assert result is True


@pytest.mark.asyncio
async def test_health_check_unreachable_returns_false(channel):
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.health_check({"url": "https://example.com"})

    assert result is False


@pytest.mark.asyncio
async def test_health_check_lxml_unavailable_returns_false(channel):
    """The "driver" (parser backend) tier — a broken lxml install must fail
    the check before any network probe is even attempted."""
    with patch(
        "backend.channels.web_scraper_channel.BeautifulSoup",
        side_effect=Exception("lxml not installed"),
    ):
        result = await channel.health_check({"url": "https://example.com"})

    assert result is False


# ── auth.type == "cookie" (CookieCloud-synced session) ──────────────────────────
@pytest.mark.asyncio
async def test_collect_cookie_auth_sends_synced_cookies(channel):
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with (
        patch("httpx.AsyncClient", return_value=mock_client_ctx) as ctor,
        patch(
            "backend.auth.manager.AuthManager.resolve_cookies",
            AsyncMock(return_value=[{"name": "session_id", "value": "abc"}]),
        ) as resolve_cookies,
    ):
        result = await channel.collect(
            {
                "url": "https://example.com",
                "selectors": {"title": "h1"},
                "auth": {"type": "cookie"},
            },
            {},
        )

    resolve_cookies.assert_awaited_once_with("example.com")
    assert result.success is True
    assert ctor.call_args.kwargs["headers"]["Cookie"] == "session_id=abc"


@pytest.mark.asyncio
async def test_collect_no_cookie_auth_type_never_calls_resolve_cookies(channel):
    """No auth.type == "cookie" configured — resolve_cookies must never be hit
    (avoid a DB round trip on every plain scrape)."""
    response = _make_mock_response()
    mock_client_ctx = _make_mock_client(response)

    with (
        patch("httpx.AsyncClient", return_value=mock_client_ctx),
        patch("backend.auth.manager.AuthManager.resolve_cookies", AsyncMock()) as resolve_cookies,
    ):
        await channel.collect({"url": "https://example.com", "selectors": {"title": "h1"}}, {})

    resolve_cookies.assert_not_awaited()
