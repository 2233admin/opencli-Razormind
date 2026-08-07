"""Unit tests for the API channel."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.channels.api_channel import ApiChannel, _resolve_dict_secrets, _resolve_secrets
from backend.channels.base import ChannelFetchError, FetchContext
from backend.control.error_kinds import ErrorKind, map_error_type


@pytest.fixture(autouse=True)
def _fake_dns():
    """This channel now runs every base_url+endpoint through
    backend.security.url_guard (SSRF guard — AUDIT item B3), which resolves
    the hostname via socket.getaddrinfo. The fixture URLs here
    (api.example.com etc.) are illustrative and don't actually resolve, so
    fake a public-IP resolution for every hostname — keeps these tests
    decoupled from live DNS/network access entirely."""
    with patch(
        "socket.getaddrinfo", return_value=[(None, None, None, "", ("93.184.216.34", 0))]
    ):
        yield


# ── _resolve_secrets ───────────────────────────────────────────────────────────

def test_resolve_secrets_with_env(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret_value")
    result = _resolve_secrets("Bearer {{secret:MY_TOKEN}}")
    assert result == "Bearer secret_value"


def test_resolve_secrets_missing_env(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    result = _resolve_secrets("{{secret:MISSING_VAR}}")
    assert result == ""


def test_resolve_secrets_no_template():
    result = _resolve_secrets("plain string")
    assert result == "plain string"


def test_resolve_secrets_multiple_placeholders(monkeypatch):
    monkeypatch.setenv("A", "hello")
    monkeypatch.setenv("B", "world")
    result = _resolve_secrets("{{secret:A}} {{secret:B}}")
    assert result == "hello world"


def test_resolve_dict_secrets_replaces_string_values(monkeypatch):
    monkeypatch.setenv("MY_KEY", "resolved")
    d = {"auth": "{{secret:MY_KEY}}", "count": 42}
    result = _resolve_dict_secrets(d)
    assert result["auth"] == "resolved"
    assert result["count"] == 42


def test_resolve_dict_secrets_non_string_passthrough():
    d = {"num": 99, "flag": True, "lst": [1, 2]}
    result = _resolve_dict_secrets(d)
    assert result == {"num": 99, "flag": True, "lst": [1, 2]}


# ── validate_config ────────────────────────────────────────────────────────────

@pytest.fixture
def channel():
    return ApiChannel()


@pytest.mark.asyncio
async def test_validate_config_missing_base_url(channel):
    errors = await channel.validate_config({"endpoint": "/test"})
    assert any("base_url" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_missing_endpoint(channel):
    errors = await channel.validate_config({"base_url": "https://api.example.com"})
    assert any("endpoint" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_valid(channel):
    errors = await channel.validate_config({
        "base_url": "https://api.example.com",
        "endpoint": "/data",
    })
    assert errors == []


@pytest.mark.asyncio
async def test_validate_config_missing_both(channel):
    errors = await channel.validate_config({})
    assert len(errors) == 2


# ── _build_auth_headers ────────────────────────────────────────────────────────

def test_build_auth_headers_bearer(channel, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "mytoken")
    headers = channel._build_auth_headers({"type": "bearer", "token_env": "API_TOKEN"})
    assert headers == {"Authorization": "Bearer mytoken"}


def test_build_auth_headers_bearer_inline_token(channel):
    headers = channel._build_auth_headers({"type": "bearer", "token": "directtoken"})
    assert headers == {"Authorization": "Bearer directtoken"}


def test_build_auth_headers_basic(channel):
    headers = channel._build_auth_headers({
        "type": "basic",
        "username": "user",
        "password": "pass",
    })
    expected = base64.b64encode(b"user:pass").decode()
    assert headers == {"Authorization": f"Basic {expected}"}


def test_build_auth_headers_api_key_default_header(channel, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "k123")
    headers = channel._build_auth_headers({"type": "api_key", "key_env": "MY_API_KEY"})
    assert headers == {"X-API-Key": "k123"}


def test_build_auth_headers_api_key_custom_header(channel, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "k456")
    headers = channel._build_auth_headers({
        "type": "api_key",
        "key_env": "MY_API_KEY",
        "header": "X-Custom-Auth",
    })
    assert headers == {"X-Custom-Auth": "k456"}


def test_build_auth_headers_no_auth(channel):
    headers = channel._build_auth_headers({})
    assert headers == {}


def test_build_auth_headers_unknown_type(channel):
    headers = channel._build_auth_headers({"type": "unknown"})
    assert headers == {}


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_mock_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if json_data is not None:
        response.json = MagicMock(return_value=json_data)
    else:
        response.json = MagicMock(side_effect=ValueError("not json"))
    return response


def _make_mock_client(response):
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_client_ctx, mock_client


# ── collect: success ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_get_success(channel):
    """Successful GET returns ChannelResult with items list."""
    response = _make_mock_response(json_data=[{"id": 1}, {"id": 2}])
    mock_client_ctx, _ = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/items"}, {}
        )

    assert result.success is True
    assert len(result.items) == 2
    assert result.items[0]["id"] == 1


@pytest.mark.asyncio
async def test_collect_result_path_navigation(channel):
    """result_path 'data.items' navigates two levels of nested dict."""
    json_data = {"data": {"items": [{"x": 1}, {"x": 2}]}}
    response = _make_mock_response(json_data=json_data)
    mock_client_ctx, _ = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/data",
                "result_path": "data.items",
            },
            {},
        )

    assert result.success is True
    assert len(result.items) == 2
    assert result.items[0]["x"] == 1


@pytest.mark.asyncio
async def test_collect_result_path_single_level(channel):
    """result_path with single key extracts nested list."""
    json_data = {"results": [{"name": "a"}, {"name": "b"}]}
    response = _make_mock_response(json_data=json_data)
    mock_client_ctx, _ = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/data",
                "result_path": "results",
            },
            {},
        )

    assert result.success is True
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_collect_non_list_response_wrapped(channel):
    """A single object response is wrapped in a list."""
    json_data = {"id": 42, "name": "single"}
    response = _make_mock_response(json_data=json_data)
    mock_client_ctx, _ = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/item/42"}, {}
        )

    assert result.success is True
    assert len(result.items) == 1
    assert result.items[0]["id"] == 42


@pytest.mark.asyncio
async def test_collect_post_method(channel):
    """POST request passes body as JSON and no query params."""
    json_data = [{"created": True}]
    response = _make_mock_response(json_data=json_data)
    mock_client_ctx, mock_client = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        await channel.collect(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/create",
                "method": "POST",
                "body": {"key": "value"},
            },
            {},
        )

    call_kwargs = mock_client.request.call_args
    assert call_kwargs.kwargs.get("json") == {"key": "value"}
    assert call_kwargs.kwargs.get("params") is None


@pytest.mark.asyncio
async def test_collect_bearer_auth_header_sent(channel, monkeypatch):
    """Bearer auth config results in Authorization header being sent."""
    monkeypatch.setenv("MY_TOKEN", "test_token_xyz")
    json_data = [{"ok": True}]
    response = _make_mock_response(json_data=json_data)
    mock_client_ctx, mock_client = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/secure",
                "auth": {"type": "bearer", "token_env": "MY_TOKEN"},
            },
            {},
        )

    assert result.success is True
    call_kwargs = mock_client.request.call_args
    headers_arg = call_kwargs.kwargs.get("headers", {})
    assert headers_arg.get("Authorization") == "Bearer test_token_xyz"


# ── collect: error cases ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_timeout_returns_fail(channel):
    """TimeoutException yields a failed ChannelResult."""
    import httpx

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/slow"}, {}
        )

    assert result.success is False
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_http_error_returns_fail(channel):
    """HTTP 500 error yields a failed ChannelResult."""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            message="Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500, text="Internal Server Error"),
        )
    )
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/err"}, {}
        )

    assert result.success is False
    assert "500" in result.error


@pytest.mark.asyncio
async def test_collect_non_json_response_returns_fail(channel):
    """Non-JSON response body yields a failed ChannelResult."""
    response = _make_mock_response(json_data=None)
    mock_client_ctx, _ = _make_mock_client(response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/html"}, {}
        )

    assert result.success is False
    assert "JSON" in result.error


@pytest.mark.asyncio
async def test_collect_generic_exception_returns_fail(channel):
    """Connection errors yield a failed ChannelResult."""
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/data"}, {}
        )

    assert result.success is False
    assert "failed" in result.error.lower()


# ── fetch(): thick-contract path ─────────────────────────────────────────────

class _FetchHttp:
    """Minimal stand-in for the runner's rate-limited client (request())."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._response


@pytest.mark.asyncio
async def test_fetch_prefers_stored_credential_over_inline_config(channel, monkeypatch):
    """A source migrated to the encrypted store resolves its bearer token from
    AuthManager, ignoring the (now-stale) inline token_env in channel_config."""
    monkeypatch.setenv("SHOULD_NOT_BE_USED", "inline-value")
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/secure",
            "auth": {"type": "bearer", "token_env": "SHOULD_NOT_BE_USED"},
        },
        params={},
        source_id="src-1",
        http=http,
    )

    with patch(
        "backend.auth.manager.AuthManager.resolve",
        AsyncMock(return_value={"token": "stored-secret"}),
    ):
        result = await channel.fetch(ctx)

    assert result.items == [{"ok": True}]
    headers = http.calls[0][2]["headers"]
    assert headers["Authorization"] == "Bearer stored-secret"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_inline_config_when_no_stored_credential(channel, monkeypatch):
    """A source that hasn't migrated to the encrypted store keeps working exactly
    as before — env-indirected token, zero behaviour change."""
    monkeypatch.setenv("MY_TOKEN", "env-secret")
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/secure",
            "auth": {"type": "bearer", "token_env": "MY_TOKEN"},
        },
        params={},
        source_id="src-2",
        http=http,
    )

    with patch(
        "backend.auth.manager.AuthManager.resolve",
        AsyncMock(return_value={}),  # nothing stored for this source
    ):
        result = await channel.fetch(ctx)

    assert result.items == [{"ok": True}]
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer env-secret"


@pytest.mark.asyncio
async def test_fetch_basic_auth_stored_creds_used(channel):
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/secure",
            "auth": {"type": "basic", "username": "should-not-be-used", "password": "x"},
        },
        params={},
        source_id="src-basic",
        http=http,
    )

    with patch(
        "backend.auth.manager.AuthManager.resolve",
        AsyncMock(return_value={"username": "stored-user", "password": "stored-pass"}),
    ):
        result = await channel.fetch(ctx)

    import base64

    expected = base64.b64encode(b"stored-user:stored-pass").decode()
    assert result.items == [{"ok": True}]
    assert http.calls[0][2]["headers"]["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_fetch_basic_auth_empty_stored_creds_falls_back_to_legacy(channel, monkeypatch):
    """Neither username nor password stored via AuthManager — must fall back to
    legacy inline config rather than sending an empty 'Basic <base64 of \":\">'
    header (this is the divergence that used to exist between AuthManager.
    resolve_context() and ApiChannel._resolve_auth_headers(); both now delegate
    to the same build_auth_header() and agree)."""
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/secure",
            "auth": {"type": "basic", "username": "legacy-user", "password": "legacy-pass"},
        },
        params={},
        source_id="src-basic-empty",
        http=http,
    )

    with patch(
        "backend.auth.manager.AuthManager.resolve",
        AsyncMock(return_value={}),  # nothing stored
    ):
        result = await channel.fetch(ctx)

    import base64

    expected = base64.b64encode(b"legacy-user:legacy-pass").decode()
    assert result.items == [{"ok": True}]
    assert http.calls[0][2]["headers"]["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_fetch_no_source_id_skips_credential_store(channel, monkeypatch):
    """fetch() called standalone (no source_id, e.g. a config test/preview) never
    hits the DB — falls straight to the legacy inline/env resolution."""
    monkeypatch.setenv("MY_TOKEN", "env-secret")
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/secure",
            "auth": {"type": "bearer", "token_env": "MY_TOKEN"},
        },
        params={},
        source_id=None,
        http=http,
    )

    with patch("backend.auth.manager.AuthManager.resolve") as mock_resolve:
        result = await channel.fetch(ctx)

    mock_resolve.assert_not_called()
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer env-secret"


@pytest.mark.asyncio
async def test_fetch_forwards_configured_timeout_to_shared_client(channel):
    """ctx.http is the runner's shared client (fixed client-level timeout) — the
    per-source configured timeout must still be applied per-request, or a slow
    API silently gets the runner's default instead of what the user configured."""
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/slow", "timeout": 120},
        params={},
        http=http,
    )

    await channel.fetch(ctx)

    assert http.calls[0][2]["timeout"] == 120


@pytest.mark.asyncio
async def test_fetch_returns_url_and_status_code_metadata(channel):
    response = _make_mock_response(json_data=[{"id": 1}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/items"},
        params={},
        http=http,
    )

    result = await channel.fetch(ctx)

    assert result.metadata == {"url": "https://api.example.com/items", "status_code": 200}


@pytest.mark.asyncio
async def test_fetch_result_path_navigation(channel):
    response = _make_mock_response(json_data={"data": {"items": [{"x": 1}, {"x": 2}]}})
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/data",
            "result_path": "data.items",
        },
        params={},
        http=http,
    )

    result = await channel.fetch(ctx)

    assert len(result.items) == 2
    assert result.items[0]["x"] == 1


@pytest.mark.asyncio
async def test_fetch_timeout_raises_channel_fetch_error(channel):
    import httpx

    http = AsyncMock()
    http.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/slow"},
        params={},
        http=http,
    )

    with pytest.raises(ChannelFetchError, match="timed out"):
        await channel.fetch(ctx)


@pytest.mark.asyncio
async def test_fetch_generic_exception_raises_channel_fetch_error(channel):
    """collect() has a generic except-Exception fallback (connection refused, DNS
    failure, ...); fetch() must have the same, or those errors bypass
    ChannelFetchError and the runner's retry/backoff contract entirely."""
    http = AsyncMock()
    http.request = AsyncMock(side_effect=OSError("connection refused"))
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/data"},
        params={},
        http=http,
    )

    with pytest.raises(ChannelFetchError, match="connection refused"):
        await channel.fetch(ctx)


@pytest.mark.asyncio
async def test_fetch_http_error_raises_channel_fetch_error(channel):
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            message="Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500, text="Internal Server Error"),
        )
    )
    http = AsyncMock()
    http.request = AsyncMock(return_value=mock_response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/err"},
        params={},
        http=http,
    )

    with pytest.raises(ChannelFetchError, match="500"):
        await channel.fetch(ctx)


# ── WIRING_GAP_LEDGER W1: JSON parse failures must carry error_type so the
# SCHEMA_DRIFT chain (error_kinds -> control.recorder) fires instead of being
# dropped by recorder's `elif error_type is not None` guard. ────────────────

@pytest.mark.asyncio
async def test_fetch_non_json_response_classified_schema_drift(channel):
    """fetch() JSON parse failure carries error_type mapping to SCHEMA_DRIFT."""
    import json

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        side_effect=json.JSONDecodeError("Expecting value", "doc", 0)
    )
    http = AsyncMock()
    http.request = AsyncMock(return_value=mock_response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/data"},
        params={},
        http=http,
    )

    with pytest.raises(ChannelFetchError) as exc_info:
        await channel.fetch(ctx)

    assert exc_info.value.error_type == "JSONDecodeError"
    assert map_error_type(exc_info.value.error_type) is ErrorKind.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_collect_non_json_response_classified_schema_drift(channel):
    """collect() propagates fetch()'s error_type through ChannelResult.fail."""
    import json

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        side_effect=json.JSONDecodeError("Expecting value", "doc", 0)
    )
    mock_client_ctx, _ = _make_mock_client(mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.collect(
            {"base_url": "https://api.example.com", "endpoint": "/data"}, {}
        )

    assert result.success is False
    assert result.error_type == "JSONDecodeError"
    assert map_error_type(result.error_type) is ErrorKind.SCHEMA_DRIFT


# ── AUDIT C13: gateway statuses classify retryable, other 4xx stay permanent ──

def _mock_status_error(status: int):
    import httpx

    return httpx.HTTPStatusError(
        message=f"HTTP {status}",
        request=MagicMock(),
        response=MagicMock(status_code=status, text=f"error {status}"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504, 520, 522, 524])
async def test_fetch_gateway_status_classified_retryable(channel, status):
    """504/520/522/524 (and 502/503) must set error_type="RetryableHTTPStatus"
    on the raised ChannelFetchError so pipeline.py's retry taxonomy sees them
    as transient, not a permanent failure — previously these all leaked
    through as a bare wrapper with no retry-classification hint."""
    mock_response = MagicMock()
    mock_response.status_code = status
    mock_response.raise_for_status = MagicMock(side_effect=_mock_status_error(status))
    http = AsyncMock()
    http.request = AsyncMock(return_value=mock_response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/err"},
        params={},
        http=http,
    )

    with pytest.raises(ChannelFetchError) as exc_info:
        await channel.fetch(ctx)

    assert exc_info.value.error_type == "RetryableHTTPStatus"


@pytest.mark.asyncio
async def test_fetch_client_404_classified_permanent(channel):
    """A genuine 4xx (not 408/429) stays permanent — retrying an unchanged
    malformed/missing-resource request can't succeed."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(side_effect=_mock_status_error(404))
    http = AsyncMock()
    http.request = AsyncMock(return_value=mock_response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/missing"},
        params={},
        http=http,
    )

    with pytest.raises(ChannelFetchError) as exc_info:
        await channel.fetch(ctx)

    assert exc_info.value.error_type == "PermanentHTTPStatus"


# ── health_check (runtime-hardening phase PR-E: real per-source probe) ───────────────────────────

@pytest.mark.asyncio
async def test_health_check_no_config_is_liveness_only(channel):
    """No config (called standalone) → can't probe anything, assume healthy."""
    assert await channel.health_check() is True


@pytest.mark.asyncio
async def test_health_check_missing_base_url_is_unhealthy(channel):
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
        result = await channel.health_check({"base_url": "https://api.example.com", "endpoint": "/ping"})

    assert result is True
    mock_client.head.assert_called_once()


@pytest.mark.asyncio
async def test_health_check_falls_back_to_get_when_head_not_allowed(channel):
    head_response = MagicMock()
    head_response.status_code = 405
    get_response = MagicMock()
    get_response.status_code = 200
    get_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=head_response)
    mock_client.get = AsyncMock(return_value=get_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.health_check({"base_url": "https://api.example.com"})

    assert result is True
    mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_health_check_unreachable_returns_false(channel):
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await channel.health_check({"base_url": "https://api.example.com"})

    assert result is False


@pytest.mark.asyncio
async def test_health_check_sends_real_auth_headers(channel):
    """The probe carries real auth (not a bare unauthenticated ping) —
    otherwise a 401-gated API always reports unhealthy even when it's fine."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        await channel.health_check(
            {"base_url": "https://api.example.com", "auth": {"type": "bearer", "token": "tok123"}}
        )

    sent_headers = mock_client.head.call_args.kwargs["headers"]
    assert sent_headers["Authorization"] == "Bearer tok123"


# ── auth.type == "cookie" (CookieCloud-synced session) ──────────────────────────
@pytest.mark.asyncio
async def test_fetch_cookie_auth_sends_synced_cookies(channel):
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={
            "base_url": "https://api.example.com",
            "endpoint": "/secure",
            "auth": {"type": "cookie"},
        },
        params={},
        http=http,
    )

    with patch(
        "backend.auth.manager.AuthManager.resolve_cookies",
        AsyncMock(return_value=[{"name": "session_id", "value": "abc"}, {"name": "csrf", "value": "xyz"}]),
    ) as resolve_cookies:
        result = await channel.fetch(ctx)

    resolve_cookies.assert_awaited_once_with("api.example.com")
    assert result.items == [{"ok": True}]
    headers = http.calls[0][2]["headers"]
    assert headers["Cookie"] == "session_id=abc; csrf=xyz"


@pytest.mark.asyncio
async def test_fetch_cookie_auth_no_synced_cookies_sends_no_header(channel):
    response = _make_mock_response(json_data=[{"ok": True}])
    http = _FetchHttp(response)
    ctx = FetchContext(
        config={"base_url": "https://api.example.com", "endpoint": "/secure", "auth": {"type": "cookie"}},
        params={},
        http=http,
    )

    with patch("backend.auth.manager.AuthManager.resolve_cookies", AsyncMock(return_value=[])):
        await channel.fetch(ctx)

    headers = http.calls[0][2]["headers"]
    assert "Cookie" not in headers
