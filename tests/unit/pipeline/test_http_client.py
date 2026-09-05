"""Phase 1 HTTP infra tests: parse_rate, the token bucket, and the retry/backoff
client (429/5xx retry, Retry-After honored, give-up after max_retries)."""

from typing import Any

import httpx
import pytest

from backend.pipeline.http_client import RateLimitedClient, TokenBucket, parse_rate


class FakeClient:
    """Returns queued responses in order; counts requests."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls += 1
        return self._responses.pop(0)

    async def aclose(self) -> None:
        pass


# ── parse_rate ───────────────────────────────────────────────────────────────

def test_parse_rate():
    assert parse_rate("60/min") == 1.0
    assert parse_rate("10/sec") == 10.0
    assert parse_rate("120/hour") == pytest.approx(120 / 3600)
    assert parse_rate("garbage") == 1.0  # unparseable → safe default


# ── token bucket ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_token_bucket_bursts_then_blocks(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("backend.pipeline.http_client.asyncio.sleep", fake_sleep)

    bucket = TokenBucket(rate=10, capacity=5)
    for _ in range(5):
        await bucket.acquire()  # full bucket → no wait
    assert slept == []
    await bucket.acquire()      # empty → must wait
    assert len(slept) == 1 and slept[0] > 0


# ── retry / backoff ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(
        "backend.pipeline.http_client.asyncio.sleep", lambda d: slept.append(d) or _noop()
    )

    client = FakeClient([httpx.Response(429), httpx.Response(200)])
    rl = RateLimitedClient(client, TokenBucket(1000), log=None)
    resp = await rl.get("http://x")

    assert resp.status_code == 200
    assert client.calls == 2
    assert len(slept) == 1  # one backoff sleep (bucket full → no rate wait)


@pytest.mark.asyncio
async def test_honors_retry_after_header(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(
        "backend.pipeline.http_client.asyncio.sleep", lambda d: slept.append(d) or _noop()
    )

    client = FakeClient([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    rl = RateLimitedClient(client, TokenBucket(1000))
    resp = await rl.get("http://x")

    assert resp.status_code == 200
    assert 7.0 in slept  # exact Retry-After used, not exponential backoff


@pytest.mark.asyncio
async def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("backend.pipeline.http_client.asyncio.sleep", lambda d: _noop())

    client = FakeClient([httpx.Response(503) for _ in range(10)])
    rl = RateLimitedClient(client, TokenBucket(1000), max_retries=3)
    resp = await rl.get("http://x")

    assert resp.status_code == 503
    assert client.calls == 4  # initial + 3 retries


# ── AUDIT C13: gateway statuses (504 + Cloudflare 520/522/524) retry too ────

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [504, 520, 522, 524])
async def test_retries_on_gateway_statuses_then_succeeds(monkeypatch, status):
    """504 (gateway timeout) and Cloudflare's 520/522/524 are transient
    upstream/proxy conditions same as 502/503 — RETRY_STATUS previously
    stopped at 503, so these leaked straight to the caller with zero retry."""
    monkeypatch.setattr("backend.pipeline.http_client.asyncio.sleep", lambda d: _noop())

    client = FakeClient([httpx.Response(status), httpx.Response(200)])
    rl = RateLimitedClient(client, TokenBucket(1000), log=None)
    resp = await rl.get("http://x")

    assert resp.status_code == 200
    assert client.calls == 2


async def _noop() -> None:
    return None
