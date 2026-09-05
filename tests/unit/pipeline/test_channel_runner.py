"""Phase 1 runner tests: run_channel owns the cross-cutting concerns (pagination,
cursor load/save) so channels stay thin and only implement fetch()."""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.channels.base import (
    AbstractChannel,
    Capabilities,
    ChannelResult,
    FetchResult,
)
from backend.pipeline.channel_runner import MAX_PAGES, run_channel
from backend.pipeline.cursor_store import InMemoryCursorStore


def _source(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"id": "s1", "channel_type": "fake", "channel_config": {}}
    base.update(over)
    return SimpleNamespace(**base)


class PagedChannel(AbstractChannel):
    """Incremental + paginated channel: cursor is {"page": n}; emits 2 items/page."""

    channel_type = "paged"
    capabilities = Capabilities(incremental=True, paginated=True)

    def __init__(self, total_pages: int = 3) -> None:
        self.total = total_pages
        self.cursors_seen: list[dict | None] = []

    async def fetch(self, ctx):
        self.cursors_seen.append(ctx.cursor)
        idx = (ctx.cursor or {}).get("page", 0)
        items = [{"id": f"{idx}-{i}"} for i in range(2)]
        nxt = idx + 1
        return FetchResult(items=items, next_cursor={"page": nxt}, has_more=nxt < self.total)

    async def collect(self, config, parameters):  # pragma: no cover - must not run
        raise AssertionError("runner must call fetch(), not collect()")

    async def validate_config(self, config):
        return []


class CollectOnlyChannel(AbstractChannel):
    """Default caps: fetch() bridges to collect(), one shot, no cursor."""

    channel_type = "co"

    async def collect(self, config, parameters):
        return ChannelResult.ok([{"id": "x"}])

    async def validate_config(self, config):
        return []


class NonIncrementalPagedChannel(AbstractChannel):
    """Paginated channel whose cursor is only meaningful during one run."""

    channel_type = "paged-no-resume"
    capabilities = Capabilities(incremental=False, paginated=True)

    def __init__(self) -> None:
        self.cursors_seen: list[dict | None] = []

    async def fetch(self, ctx):
        self.cursors_seen.append(ctx.cursor)
        offset = (ctx.cursor or {}).get("offset", 0)
        if offset == 0:
            return FetchResult(
                items=[{"id": "first-page"}],
                next_cursor={"offset": 1},
                has_more=True,
            )
        return FetchResult(items=[{"id": "second-page"}], has_more=False)

    async def collect(self, config, parameters):  # pragma: no cover - unused
        raise AssertionError("runner must call fetch(), not collect()")

    async def validate_config(self, config):
        return []


class InfiniteChannel(AbstractChannel):
    """Always has_more → exercises the MAX_PAGES guard. 1 item/page."""

    channel_type = "inf"
    capabilities = Capabilities(incremental=True, paginated=True)

    async def fetch(self, ctx):
        idx = (ctx.cursor or {}).get("page", 0)
        return FetchResult(items=[{"id": idx}], next_cursor={"page": idx + 1}, has_more=True)

    async def collect(self, config, parameters):  # pragma: no cover
        raise AssertionError("unused")

    async def validate_config(self, config):
        return []


@pytest.mark.asyncio
async def test_runner_drives_pagination_and_saves_cursor_each_page():
    chan = PagedChannel(total_pages=3)
    store = InMemoryCursorStore()
    items = (
        await run_channel(
            _source(),
            {},
            channel=chan,
            cursor_store=store,
            http=object(),
        )
    ).items

    assert len(items) == 6  # 3 pages x 2 items
    assert [i["id"] for i in items] == ["0-0", "0-1", "1-0", "1-1", "2-0", "2-1"]
    # First fetch sees no cursor, then each saved cursor in turn.
    assert chan.cursors_seen == [None, {"page": 1}, {"page": 2}]
    # Cursor persisted after the last page → next run resumes from page 3.
    assert await store.load("s1") == {"page": 3}


@pytest.mark.asyncio
async def test_runner_resumes_from_stored_cursor():
    chan = PagedChannel(total_pages=3)
    store = InMemoryCursorStore()
    await store.save("s1", {"page": 2})  # pretend a prior run got to page 2
    items = (
        await run_channel(
            _source(),
            {},
            channel=chan,
            cursor_store=store,
            http=object(),
        )
    ).items

    assert chan.cursors_seen[0] == {"page": 2}  # started where it left off
    assert [i["id"] for i in items] == ["2-0", "2-1"]  # only the remaining page


@pytest.mark.asyncio
async def test_collect_only_channel_runs_once_no_cursor():
    chan = CollectOnlyChannel()
    store = InMemoryCursorStore()
    result = await run_channel(_source(), {}, channel=chan, cursor_store=store, http=object())

    assert result.items == [{"id": "x"}]
    assert await store.load("s1") is None  # not incremental → nothing saved


@pytest.mark.asyncio
async def test_non_incremental_pagination_advances_cursor_within_run():
    """A non-incremental paginated source must not fetch page zero repeatedly."""
    chan = NonIncrementalPagedChannel()
    store = InMemoryCursorStore()

    result = await run_channel(_source(), {}, channel=chan, cursor_store=store, http=object())

    assert result.items == [{"id": "first-page"}, {"id": "second-page"}]
    assert chan.cursors_seen == [None, {"offset": 1}]
    assert await store.load("s1") is None


@pytest.mark.asyncio
async def test_max_pages_guard_stops_infinite_pagination():
    items = (
        await run_channel(
            _source(),
            {},
            channel=InfiniteChannel(),
            cursor_store=InMemoryCursorStore(),
            http=object(),
        )
    ).items
    assert len(items) == MAX_PAGES


class MetadataChannel(AbstractChannel):
    """collect()-only channel whose ChannelResult carries non-item metadata (the
    opencli/skill shape: node_url, awaiting_confirm, ...)."""

    channel_type = "md"

    async def collect(self, config, parameters):
        return ChannelResult.ok([{"id": "x"}], node_url="http://node:1", awaiting_confirm=True)

    async def validate_config(self, config):
        return []


class FailsOnSecondPageChannel(AbstractChannel):
    """Incremental + paginated: succeeds on page 0, raises on page 1 — exercises
    a mid-pagination failure after the cursor has already advanced once."""

    channel_type = "fail2"
    capabilities = Capabilities(incremental=True, paginated=True)

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, ctx):
        self.calls += 1
        if self.calls == 1:
            return FetchResult(items=[{"id": "p0"}], next_cursor={"page": 1}, has_more=True)
        raise RuntimeError("boom")

    async def collect(self, config, parameters):  # pragma: no cover - unused
        raise AssertionError("must not be called")

    async def validate_config(self, config):
        return []


@pytest.mark.asyncio
async def test_pagination_failure_after_first_page_logs_discarded_items(caplog):
    """A mid-pagination failure must not silently discard the items already
    fetched this call — at minimum, log what's being lost and why."""
    chan = FailsOnSecondPageChannel()
    store = InMemoryCursorStore()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="boom"):
            await run_channel(_source(), {}, channel=chan, cursor_store=store, http=object())

    assert any("already-fetched" in r.getMessage() for r in caplog.records)
    # Exception propagation and cursor-commit timing are unchanged — page 0's
    # cursor is still persisted even though the call as a whole raises.
    assert await store.load("s1") == {"page": 1}


@pytest.mark.asyncio
async def test_unmigrated_channel_builds_no_rate_limited_client():
    """CollectOnlyChannel never overrides fetch() — the default adapter bridges
    straight to collect() and never reads ctx.http, so building a real
    RateLimitedClient/httpx.AsyncClient for it (and holding it open for the
    channel's whole run) is pure waste."""
    with patch("backend.pipeline.channel_runner.RateLimitedClient") as mock_rlc:
        result = await run_channel(
            _source(), {}, channel=CollectOnlyChannel(), cursor_store=InMemoryCursorStore()
        )
    mock_rlc.assert_not_called()
    assert result.items == [{"id": "x"}]


@pytest.mark.asyncio
async def test_migrated_channel_still_builds_rate_limited_client_when_none_injected():
    """A channel that DOES override fetch() (e.g. RSS/PagedChannel) still needs
    the shared client when the caller doesn't inject its own."""
    chan = PagedChannel(total_pages=1)
    with patch("backend.pipeline.channel_runner.RateLimitedClient") as mock_rlc:
        mock_rlc.return_value.aclose = AsyncMock()
        await run_channel(_source(), {}, channel=chan, cursor_store=InMemoryCursorStore())
    mock_rlc.assert_called_once()


@pytest.mark.asyncio
async def test_default_fetch_adapter_forwards_collect_metadata():
    """The default fetch() adapter must not drop collect()'s metadata — opencli's
    node_url and skill's awaiting_confirm depend on it reaching PipelineResult."""
    result = await run_channel(
        _source(), {}, channel=MetadataChannel(), cursor_store=InMemoryCursorStore(), http=object()
    )
    assert result.metadata == {"node_url": "http://node:1", "awaiting_confirm": True}
