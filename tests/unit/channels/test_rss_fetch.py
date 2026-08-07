"""RSS thick-contract fetch(): conditional GET (etag/304), identity(), and the
runner driving it end to end (the thin-channel / thick-runner vertical slice)."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.channels.base import ChannelFetchError, FetchContext
from backend.channels.rss_channel import RSSChannel


@pytest.fixture(autouse=True)
def _fake_dns():
    """fetch()/collect() now run feed_url through backend.security.url_guard
    (SSRF guard — AUDIT item B3), which resolves the host via
    socket.getaddrinfo. These tests use an unresolvable placeholder host
    ("x") with an injected fake http client, so fake a public-IP resolution
    for every hostname — keeps them decoupled from live DNS entirely."""
    with patch(
        "socket.getaddrinfo", return_value=[(None, None, None, "", ("93.184.216.34", 0))]
    ):
        yield

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>A</title><link>https://x/a</link><guid>id-a</guid></item>
<item><title>B</title><link>https://x/b</link><guid>id-b</guid></item>
</channel></rss>"""


class _Resp:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Http:
    """Minimal stand-in for the runner's rate-limited client (get())."""

    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp


def test_rss_declares_incremental():
    assert RSSChannel().capabilities.incremental is True


def test_rss_identity_uses_entry_id():
    assert RSSChannel().identity({"id": "id-a", "link": "l"}) == "id-a"
    assert RSSChannel().identity({"id": "", "link": ""}) is None


@pytest.mark.asyncio
async def test_fetch_200_returns_items_and_advances_cursor():
    headers = {
        "ETag": 'W/"v2"',
        "Last-Modified": "Wed, 01 Jul 2026 00:00:00 GMT",
    }
    http = _Http(_Resp(200, text=_RSS, headers=headers))
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=None, http=http)

    result = await RSSChannel().fetch(ctx)

    assert [i["id"] for i in result.items] == ["id-a", "id-b"]
    assert result.next_cursor == {
        "etag": 'W/"v2"',
        "last_modified": "Wed, 01 Jul 2026 00:00:00 GMT",
    }
    assert result.has_more is False


@pytest.mark.asyncio
async def test_fetch_parses_feed_off_event_loop_thread():
    """AUDIT C22: feedparser.parse() must run via asyncio.to_thread on the
    fetch() path too — a multi-MB feed would otherwise freeze every other
    request/task on this process for the duration of the parse."""
    import feedparser

    http = _Http(_Resp(200, text=_RSS, headers={}))
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=None, http=http)

    seen_threads: list[int] = []
    real_parse = feedparser.parse

    def spy_parse(content):
        seen_threads.append(threading.get_ident())
        return real_parse(content)

    with patch("feedparser.parse", side_effect=spy_parse):
        result = await RSSChannel().fetch(ctx)

    assert [i["id"] for i in result.items] == ["id-a", "id-b"]
    assert len(seen_threads) == 1
    assert seen_threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_fetch_304_no_new_items_keeps_cursor_and_sends_conditional():
    http = _Http(_Resp(304))
    cursor = {"etag": 'W/"v1"'}
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=cursor, http=http)

    result = await RSSChannel().fetch(ctx)

    assert result.items == []
    assert result.next_cursor == cursor
    # The conditional request carried the cursor's etag.
    assert http.calls[0][1]["headers"]["If-None-Match"] == 'W/"v1"'


@pytest.mark.asyncio
async def test_fetch_bozo_feed_raises_error_type_mapped_to_schema_drift():
    """WIRING_GAP_LEDGER W1: fetch()'s bozo branch must carry error_type (from
    feedparser's real bozo_exception class) so error_kinds.map_error_type
    resolves it to SCHEMA_DRIFT -- previously this raise passed no error_type,
    so control.recorder's `elif error_type is not None` guard silently
    dropped it and the SCHEMA_DRIFT chain never fired."""
    from xml.sax import SAXParseException

    from backend.control.error_kinds import ErrorKind, map_error_type

    http = _Http(_Resp(200, text="NOT VALID XML AT ALL !!!", headers={}))
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=None, http=http)

    fake_parsed = MagicMock()
    fake_parsed.bozo = True
    fake_parsed.entries = []
    fake_parsed.bozo_exception = SAXParseException("syntax error", None, MagicMock())

    with patch("feedparser.parse", return_value=fake_parsed):
        with pytest.raises(ChannelFetchError) as exc_info:
            await RSSChannel().fetch(ctx)

    assert exc_info.value.error_type == "SAXParseException"
    assert map_error_type(exc_info.value.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_fetch_html_response_raises_error_type_mapped_to_schema_drift():
    """Issue #36: an HTML error page parses as bozo=False with entries=[] on
    the fetch() path too — must raise ChannelFetchError(ParseError), mapped
    to SCHEMA_DRIFT, instead of returning a fake empty feed."""
    from backend.control.error_kinds import ErrorKind, map_error_type

    http = _Http(_Resp(200, text="<html><body>Error 502</body></html>", headers={}))
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=None, http=http)

    with pytest.raises(ChannelFetchError) as exc_info:
        await RSSChannel().fetch(ctx)

    assert exc_info.value.error_type == "ParseError"
    assert map_error_type(exc_info.value.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_fetch_empty_body_raises_error_type_mapped_to_schema_drift():
    """Issue #36: an empty HTTP 200 body on the fetch() path must raise
    ParseError (SCHEMA_DRIFT), not return a fake empty feed."""
    from backend.control.error_kinds import ErrorKind, map_error_type

    http = _Http(_Resp(200, text="", headers={}))
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=None, http=http)

    with pytest.raises(ChannelFetchError) as exc_info:
        await RSSChannel().fetch(ctx)

    assert exc_info.value.error_type == "ParseError"
    assert map_error_type(exc_info.value.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_fetch_valid_empty_feed_succeeds():
    """A legitimate zero-entry feed (version detected) must NOT be flagged as
    a non-feed response on the fetch() path."""
    empty_rss = '<rss version="2.0"><channel><title>Empty</title></channel></rss>'
    http = _Http(_Resp(200, text=empty_rss, headers={}))
    ctx = FetchContext(config={"feed_url": "https://x/feed"}, params={}, cursor=None, http=http)

    result = await RSSChannel().fetch(ctx)

    assert result.items == []
    assert result.has_more is False


@pytest.mark.asyncio
async def test_run_channel_drives_rss_and_persists_cursor():
    from backend.pipeline.channel_runner import run_channel
    from backend.pipeline.cursor_store import InMemoryCursorStore

    source = SimpleNamespace(
        id="src-1", channel_type="rss", channel_config={"feed_url": "https://x/feed"}
    )
    http = _Http(_Resp(200, text=_RSS, headers={"ETag": 'W/"v2"'}))
    store = InMemoryCursorStore()

    result = await run_channel(
        source,
        {},
        cursor_store=store,
        channel=RSSChannel(),
        http=http,
    )
    items = result.items

    assert [i["id"] for i in items] == ["id-a", "id-b"]
    # Incremental channel: the runner persisted the advanced cursor.
    assert await store.load("src-1") == {"etag": 'W/"v2"'}
