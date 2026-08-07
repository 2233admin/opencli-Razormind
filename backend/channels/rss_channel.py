"""RSS channel using feedparser."""

import asyncio
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx

from backend.channels.base import (
    AbstractChannel,
    Capabilities,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel
from backend.security.url_guard import (
    SSRFValidationError,
    avalidate_public_url,
    guarded_async_client,
)


def _bozo_error_type(parsed: Any) -> str:
    """error_type for a bozo (unparseable) feed — WIRING_GAP_LEDGER W1.

    feedparser sets ``bozo_exception`` to the underlying parse failure (e.g.
    ``xml.sax._exceptions.SAXParseException`` for malformed markup, truncated
    declarations, or an encoding mismatch — verified empirically across all
    of those shapes; see backend/control/error_kinds.py's SCHEMA_DRIFT set).
    Its class name is a real structured ``error_type`` like every other
    channel already produces, so it flows through ChannelResult.fail() /
    ChannelFetchError into control.recorder instead of being dropped by the
    ``elif error_type is not None`` guard there. Falls back to a generic
    "ParseError" (also mapped to SCHEMA_DRIFT) on the defensive case where
    feedparser didn't attach one.
    """
    bozo_exception = getattr(parsed, "bozo_exception", None)
    if bozo_exception is not None:
        return type(bozo_exception).__name__
    return "ParseError"


def _parsed_version(parsed: Any) -> str:
    """Detected feed format (``'rss20'``, ``'atom10'``, ...), or ``''`` when
    feedparser recognized no feed format in the body. Works for both
    feedparser's dict-like result and bare namespaces used in tests."""
    getter = getattr(parsed, "get", None)
    if callable(getter):
        return str(getter("version") or "")
    return str(getattr(parsed, "version", "") or "")


def _non_feed_response(parsed: Any) -> bool:
    """True when feedparser accepted the body but recognized no feed format.

    Issue #36: an HTTP 200 with an empty body or an HTML error page parses as
    ``bozo=False`` with ``entries=[]`` — visually identical to a legitimate
    empty feed, so no structured error reaches control. The distinguishing
    signal is the detected feed version: a real (even empty) RSS/Atom feed
    parses with ``version`` set (``'rss20'``/``'atom10'``/...), while
    empty/HTML/non-feed bodies leave it falsy (``''``/``None``). Conservative
    by design: this only fires when zero entries were parsed, so a partial
    parse that yielded items still succeeds. ``ParseError`` maps to
    SCHEMA_DRIFT in backend/control/error_kinds.py, so these responses now
    reach the control recorder instead of looking like a healthy empty feed.
    """
    return (
        not getattr(parsed, "bozo", False)
        and not getattr(parsed, "entries", None)
        and not _parsed_version(parsed)
    )


@register_channel
class RSSChannel(AbstractChannel):
    """Collect entries from RSS/Atom feeds."""

    channel_type = "rss"
    capabilities = Capabilities(
        incremental=True, paginated=False, auth_kind="none", default_rate="60/min"
    )

    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        feed_url: str = config.get("feed_url", "")
        max_entries: int = config.get("max_entries", 50)
        timeout: int = config.get("timeout", 30)
        allow_private_network = bool(config.get("allow_private_network"))
        allowed_domains = [
            str(domain).strip().lower().lstrip(".")
            for domain in config.get("allowed_domains", [])
            if str(domain).strip()
        ]

        try:
            # guarded_async_client validates feed_url AND pins the connection
            # to the IP(s) that validation resolved (DNS-rebinding TOCTOU
            # closure — AUDIT B3 follow-up; see backend.security.url_guard's
            # module docstring). TLS/SNI/cert verification are unaffected.
            if allow_private_network:
                host = (urlsplit(feed_url).hostname or "").lower()
                if not host or not any(
                    host == domain or host.endswith(f".{domain}")
                    for domain in allowed_domains
                ):
                    return ChannelResult.fail(
                        f"RSS feed host {host!r} is not allowed for private-network access",
                        error_type="SSRFValidationError",
                    )
                client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
            else:
                client, feed_url = await guarded_async_client(
                    feed_url, timeout=timeout, follow_redirects=False
                )
        except SSRFValidationError as exc:
            return ChannelResult.fail(
                f"RSS feed URL rejected: {exc}", error_type="SSRFValidationError"
            )

        try:
            # follow_redirects=False: a validated public URL could otherwise
            # 30x-redirect to a private/loopback/fleet address, bypassing the
            # check above (SSRF via redirect). RSS feeds don't legitimately
            # need cross-host redirect chains for this use case.
            async with client as opened_client:
                response = await opened_client.get(
                    feed_url,
                    headers={"User-Agent": "opencli-admin/1.0 (+https://github.com)"},
                )
                response.raise_for_status()
                content = response.text
        except httpx.TimeoutException as exc:
            return ChannelResult.fail(
                f"RSS feed request timed out: {_safe_url(feed_url)}", error_type=type(exc).__name__
            )
        except httpx.HTTPStatusError as exc:
            return ChannelResult.fail(
                f"HTTP {exc.response.status_code} fetching feed", error_type=type(exc).__name__
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            return ChannelResult.fail(
                f"Failed to fetch RSS feed: {detail}", error_type=type(exc).__name__
            )

        # AUDIT C22: feedparser.parse() is a synchronous, potentially
        # multi-second call for a large feed — running it inline would freeze
        # the whole event loop (every other request/task on this process)
        # for the duration. asyncio.to_thread runs it in the default executor
        # instead; the parsed result is used identically either way.
        parsed = await asyncio.to_thread(feedparser.parse, content)
        if parsed.bozo and not parsed.entries:
            return ChannelResult.fail(
                f"Failed to parse feed: {getattr(parsed, 'bozo_exception', 'unknown error')}",
                error_type=_bozo_error_type(parsed),
            )
        # Issue #36: feedparser treats an empty body or an HTML error page as a
        # successful non-bozo parse with entries=[] — indistinguishable from a
        # legitimate empty feed by bozo alone. Fail explicitly (ParseError maps
        # to SCHEMA_DRIFT) when no feed format was recognized.
        if _non_feed_response(parsed):
            return ChannelResult.fail(
                "RSS feed returned no recognized feed content "
                "(empty body or HTML/non-feed response)",
                error_type="ParseError",
            )

        entries = parsed.entries[:max_entries]
        items = [self._entry_to_dict(entry) for entry in entries]

        return ChannelResult.ok(
            items,
            feed_title=parsed.feed.get("title", ""),
            total_entries=len(parsed.entries),
        )

    def _entry_to_dict(self, entry: Any) -> dict[str, Any]:
        return {
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", ""),
            "author": entry.get("author", ""),
            "published": entry.get("published", ""),
            "tags": [t.get("term", "") for t in entry.get("tags", [])],
            "id": entry.get("id", entry.get("link", "")),
        }

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        """Incremental RSS fetch: a conditional GET keyed on the cursor's etag /
        last_modified. A 304 means nothing new — return no items and keep the
        cursor unchanged. A 200 reparses and advances the cursor to the response's
        ETag / Last-Modified. RSS isn't paginated, so ``has_more`` is always False.

        Uses the runner-provided rate-limited client (``ctx.http``) when present,
        falling back to a one-shot client so ``fetch()`` also works standalone.
        """
        config = ctx.config
        feed_url: str = config.get("feed_url", "")
        max_entries: int = config.get("max_entries", 50)
        timeout: int = config.get("timeout", 30)
        cursor = ctx.cursor or {}

        headers = {"User-Agent": "opencli-admin/1.0 (+https://github.com)"}
        if cursor.get("etag"):
            headers["If-None-Match"] = cursor["etag"]
        if cursor.get("last_modified"):
            headers["If-Modified-Since"] = cursor["last_modified"]

        # follow_redirects=False on the one-shot path — see collect()'s comment;
        # ctx.http is the runner-shared client (its redirect policy/connection
        # pinning is the runner's concern, out of this file's boundary — it
        # only gets the plain call-time validate_public_url here).
        if ctx.http is not None:
            try:
                feed_url = await avalidate_public_url(feed_url)
            except SSRFValidationError as exc:
                raise ChannelFetchError(
                    f"RSS feed URL rejected: {exc}", error_type="SSRFValidationError"
                ) from exc
            response = await ctx.http.get(feed_url, headers=headers, timeout=timeout)
        else:
            try:
                # guarded_async_client pins the connection to the validated
                # IP(s) — DNS-rebinding TOCTOU closure (AUDIT B3 follow-up).
                client, feed_url = await guarded_async_client(
                    feed_url, timeout=timeout, follow_redirects=False
                )
            except SSRFValidationError as exc:
                raise ChannelFetchError(
                    f"RSS feed URL rejected: {exc}", error_type="SSRFValidationError"
                ) from exc
            async with client as opened_client:
                response = await opened_client.get(feed_url, headers=headers)

        if response.status_code == 304:
            # Not Modified — no new entries; preserve the cursor as-is.
            return FetchResult(items=[], next_cursor=(cursor or None), has_more=False)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # AUDIT C13: classify by status so a gateway blip (502/503/504,
            # Cloudflare's 520/522/524, ...) reaches the celery retry boundary
            # instead of defaulting to permanent alongside a genuine 4xx —
            # this call used to be bare, so ANY status error (not just
            # gateway ones) propagated as a raw HTTPStatusError with no
            # retry-classification hint at all.
            from backend.pipeline.error_taxonomy import is_retryable_http_status

            status = exc.response.status_code
            error_type = (
                "RetryableHTTPStatus" if is_retryable_http_status(status) else "PermanentHTTPStatus"
            )
            raise ChannelFetchError(f"HTTP {status} fetching feed", error_type=error_type) from exc

        # AUDIT C22: see collect()'s twin comment — off-load the synchronous
        # parse instead of blocking the event loop.
        parsed = await asyncio.to_thread(feedparser.parse, response.text)
        if parsed.bozo and not parsed.entries:
            raise ChannelFetchError(
                f"Failed to parse feed: {getattr(parsed, 'bozo_exception', 'unknown error')}",
                error_type=_bozo_error_type(parsed),
            )
        # Issue #36: see collect()'s twin comment — an empty/HTML response
        # parses as bozo=False with entries=[] and must not look like a
        # healthy empty feed.
        if _non_feed_response(parsed):
            raise ChannelFetchError(
                "RSS feed returned no recognized feed content "
                "(empty body or HTML/non-feed response)",
                error_type="ParseError",
            )
        items = [self._entry_to_dict(entry) for entry in parsed.entries[:max_entries]]

        next_cursor = dict(cursor)
        if etag := response.headers.get("ETag"):
            next_cursor["etag"] = etag
        if last_modified := response.headers.get("Last-Modified"):
            next_cursor["last_modified"] = last_modified

        return FetchResult(items=items, next_cursor=(next_cursor or None), has_more=False)

    def identity(self, item: dict[str, Any]) -> str | None:
        # RSS entry id (falls back to link in _entry_to_dict): a stable dedup key,
        # so editing two chars of a title is the same item, not a new one.
        return item.get("id") or None

    async def health_check(
        self, config: dict[str, Any] | None = None, source_id: str | None = None
    ) -> bool:
        probe_config = dict(config or {})
        probe_config["max_entries"] = 1
        result = await self.collect(probe_config, {})
        return result.success

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("feed_url"):
            errors.append("'feed_url' is required for rss channel")
        return errors


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
