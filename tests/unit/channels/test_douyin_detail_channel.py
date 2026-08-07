import json

import pytest

from backend.channels.base import ChannelFetchError, ChannelResult, FetchContext
from backend.channels.douyin_detail_channel import DouyinDetailChannel, _aweme_id


def test_aweme_id_accepts_canonical_url_and_numeric_id():
    assert _aweme_id("https://www.douyin.com/video/7664819289043537167") == "7664819289043537167"
    assert _aweme_id("7664819289043537167") == "7664819289043537167"
    assert _aweme_id("https://example.com/video/7664819289043537167") is None


@pytest.mark.asyncio
async def test_collect_opens_page_then_returns_bounded_public_detail(monkeypatch):
    detail = {
        "aweme_id": "7664819289043537167",
        "desc": "麻将机改装后可以这么控制 #麻将机",
        "create_time": 1784858400,
        "author": {"nickname": "麻将机专卖店"},
        "statistics": {"digg_count": 12, "comment_count": 3, "share_count": 1},
        "video_tag": [{"tag_name": "棋牌"}],
        "video": {
            "duration": 37012,
            "play_addr": {"url_list": ["https://video.example/play.mp4"]},
            "cover": {"url_list": ["https://image.example/cover.jpg"]},
        },
    }
    calls: list[list[str]] = []

    async def fake_run(command):
        calls.append(command)
        if command[3] == "open":
            return 0, "{}", ""
        return 0, json.dumps({"aweme_detail": detail}), ""

    monkeypatch.setattr("backend.channels.douyin_detail_channel._run_douyin_command", fake_run)
    result = await DouyinDetailChannel().collect(
        {"url": "https://www.douyin.com/video/7664819289043537167"}, {}
    )

    assert result.success
    assert [command[3] for command in calls] == ["open", "eval"]
    assert "=>" not in calls[1][-1]
    item = result.items[0]
    assert item["url"] == "https://www.douyin.com/video/7664819289043537167"
    assert item["author"] == "麻将机专卖店"
    assert item["statistics"] == {"digg_count": 12, "comment_count": 3, "share_count": 1}
    assert item["media"]["play_url"] == "https://video.example/play.mp4"


@pytest.mark.asyncio
async def test_collect_rejects_non_douyin_url_without_spawning(monkeypatch):
    async def should_not_run(command):
        raise AssertionError("must not run")

    monkeypatch.setattr(
        "backend.channels.douyin_detail_channel._run_douyin_command", should_not_run
    )
    result = await DouyinDetailChannel().collect({"url": "https://example.com/video/123"}, {})

    assert not result.success
    assert "url" in (result.error or "")


# ── transient classification + thick fetch() (PR-thick-fetch) ─────────────


def _ctx(**config) -> FetchContext:
    cfg = {
        "url": "https://www.douyin.com/video/7664819289043537167",
        "max_retries": 3,
        "retry_base_delay": 0.001,
    }
    cfg.update(config)
    return FetchContext(config=cfg, params={})


@pytest.mark.asyncio
async def test_collect_classifies_cdp_transient_as_connection_error(monkeypatch):
    async def fake_run(command):
        return 1, "", "CDP connection is not open"

    monkeypatch.setattr("backend.channels.douyin_detail_channel._run_douyin_command", fake_run)
    result = await DouyinDetailChannel().collect(
        {"url": "https://www.douyin.com/video/7664819289043537167"}, {}
    )

    assert not result.success
    assert result.error_type == "ConnectionError"


@pytest.mark.asyncio
async def test_fetch_retries_transient_then_succeeds(monkeypatch):
    channel = DouyinDetailChannel()
    results = [
        ChannelResult.fail("CDP connection is not open", error_type="ConnectionError"),
        ChannelResult.ok([{"title": "ok", "aweme_id": "7664819289043537167"}]),
    ]
    calls: list[int] = []

    async def fake_collect(config, parameters):
        calls.append(1)
        return results.pop(0)

    monkeypatch.setattr(channel, "collect", fake_collect)
    out = await channel.fetch(_ctx())

    assert out.items[0]["aweme_id"] == "7664819289043537167"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_fetch_gives_up_after_max_retries(monkeypatch):
    channel = DouyinDetailChannel()
    calls: list[int] = []

    async def fake_collect(config, parameters):
        calls.append(1)
        return ChannelResult.fail("CDP connection is not open", error_type="ConnectionError")

    monkeypatch.setattr(channel, "collect", fake_collect)

    with pytest.raises(ChannelFetchError) as ei:
        await channel.fetch(_ctx(max_retries=2))

    assert ei.value.error_type == "ConnectionError"
    assert len(calls) == 3
