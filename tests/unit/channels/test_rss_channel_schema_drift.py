"""RSS parser behaviour and SCHEMA_DRIFT wiring tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from xml.sax import SAXParseException

import pytest

from backend.channels.rss_channel import RSSChannel
from backend.control.error_kinds import ErrorKind, map_error_type


@pytest.fixture
def channel():
    return RSSChannel()


class _HttpClient:
    def __init__(self):
        self.response = MagicMock(text="NOT VALID XML AT ALL !!!")
        self.response.raise_for_status = MagicMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, *_args, **_kwargs):
        return self.response


class _BodyClient:
    """Collect-side client serving a fixed body (Issue #36 cases)."""

    def __init__(self, text: str):
        self.response = MagicMock(text=text)
        self.response.raise_for_status = MagicMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, *_args, **_kwargs):
        return self.response


async def _collect_with_parsed(channel, parsed):
    with (
        patch("httpx.AsyncClient", return_value=_HttpClient()),
        patch("feedparser.parse", return_value=parsed),
    ):
        return await channel.collect({"feed_url": "https://example.com/rss"}, {})


@pytest.mark.asyncio
async def test_collect_bozo_feed_error_type_maps_to_schema_drift(channel):
    """A real bozo exception type reaches the SCHEMA_DRIFT mapper."""
    parsed = MagicMock(bozo=True, entries=[])
    parsed.bozo_exception = SAXParseException("syntax error", None, MagicMock())

    result = await _collect_with_parsed(channel, parsed)

    assert result.success is False
    assert result.error_type == "SAXParseException"
    assert map_error_type(result.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_collect_bozo_feed_without_bozo_exception_falls_back_to_parse_error(
    channel,
):
    """Missing bozo_exception still produces a mapped ParseError."""
    parsed = SimpleNamespace(bozo=True, entries=[])

    result = await _collect_with_parsed(channel, parsed)

    assert result.success is False
    assert result.error_type == "ParseError"
    assert map_error_type(result.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_collect_bozo_feed_with_entries_succeeds(channel):
    """A bozo feed that still has entries succeeds as a partial parse."""
    entry = MagicMock()
    entry.get = lambda key, default="": {
        "title": "Partial Item",
        "link": "https://ex.com/p",
        "summary": "",
        "author": "",
        "published": "",
        "id": "pid1",
        "tags": [],
    }.get(key, default)
    parsed = MagicMock(bozo=True, entries=[entry])
    parsed.feed = MagicMock()
    parsed.feed.get = lambda key, default="": default

    result = await _collect_with_parsed(channel, parsed)

    assert result.success is True
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_collect_html_error_page_fails_with_parse_error(channel):
    """Issue #36: an HTML error page parses as bozo=False with entries=[] —
    previously returned OK as a fake empty feed; now fails with a structured
    ParseError that maps to SCHEMA_DRIFT."""
    html = (
        "<!DOCTYPE html><html><head><title>Error 502</title></head>"
        "<body><h1>Bad Gateway</h1><p>nginx</p></body></html>"
    )
    with patch("httpx.AsyncClient", return_value=_BodyClient(html)):
        result = await channel.collect({"feed_url": "https://example.com/rss"}, {})

    assert result.success is False
    assert result.error_type == "ParseError"
    assert map_error_type(result.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_collect_empty_body_fails_with_parse_error(channel):
    """Issue #36: an empty HTTP 200 body parses as bozo=False with entries=[]
    — must fail as ParseError (SCHEMA_DRIFT), not look like a healthy feed."""
    with patch("httpx.AsyncClient", return_value=_BodyClient("")):
        result = await channel.collect({"feed_url": "https://example.com/rss"}, {})

    assert result.success is False
    assert result.error_type == "ParseError"
    assert map_error_type(result.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_collect_plain_xml_not_a_feed_fails_with_parse_error(channel):
    """Issue #36: well-formed XML that is not a feed (no recognized version)
    must not be reported as a successful empty feed."""
    with patch("httpx.AsyncClient", return_value=_BodyClient("<foo><bar>1</bar></foo>")):
        result = await channel.collect({"feed_url": "https://example.com/rss"}, {})

    assert result.success is False
    assert result.error_type == "ParseError"
    assert map_error_type(result.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_collect_valid_empty_feed_succeeds(channel):
    """A legitimate zero-entry feed (feedparser detects its version) must NOT
    be flagged as a non-feed response."""
    empty_rss = '<rss version="2.0"><channel><title>Empty</title></channel></rss>'
    with patch("httpx.AsyncClient", return_value=_BodyClient(empty_rss)):
        result = await channel.collect({"feed_url": "https://example.com/rss"}, {})

    assert result.success is True
    assert result.items == []
    assert result.metadata.get("total_entries") == 0
