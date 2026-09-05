"""Unit tests for the OpenCLI channel."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.channels.base import (
    AbstractChannel,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
)
from backend.channels.opencli_channel import (
    OpenCLIChannel,
    _collect_via_agent,
    _get_named_options,
    _kill_subprocess,
    _parse_csv,
    _parse_json,
    _parse_markdown,
    _parse_table,
    _parse_yaml,
    _run_opencli,
)
from backend.pipeline.error_taxonomy import effective_error_type, is_retryable


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_kill_subprocess_terminates_windows_process_tree(monkeypatch):
    process = MagicMock(pid=4321, returncode=None)
    process.wait = AsyncMock()
    run = AsyncMock()
    monkeypatch.setattr("backend.channels.opencli_channel.asyncio.to_thread", run)

    await _kill_subprocess(process, platform="nt")

    assert run.await_args.args[1][:4] == ["taskkill", "/PID", "4321", "/T"]
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_subprocess_terminates_posix_process_group(monkeypatch):
    process = MagicMock(pid=4321, returncode=None)
    process.wait = AsyncMock()
    killpg = MagicMock()
    monkeypatch.setattr(
        "backend.channels.opencli_channel.os.killpg", killpg, raising=False
    )

    await _kill_subprocess(process, platform="posix")

    killpg.assert_called_once_with(4321, 9)
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_named_options_accept_opencli_underscore_flags():
    process = AsyncMock()
    process.communicate.return_value = (
        b"--url <value> --max_text_chars [value] --trace <mode>",
        b"",
    )
    with patch("asyncio.create_subprocess_exec", return_value=process):
        options = await _get_named_options(
            "opencli-underscore-test", "official-site", "observe"
        )

    assert options == frozenset({"url", "max_text_chars", "trace"})


@pytest.mark.asyncio
async def test_named_options_cancellation_kills_the_help_process():
    process = MagicMock()
    process.returncode = None
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    process.kill = MagicMock()
    process.wait = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch(
            "backend.channels.opencli_channel._kill_subprocess", new=AsyncMock()
        ) as kill_tree,
        pytest.raises(asyncio.CancelledError),
    ):
        await _get_named_options("cancel-opencli", "site", "command")

    kill_tree.assert_awaited_once_with(process)


# ── C17: --help/browser-requirement cache TTL + cache-only peek ─────────────


@pytest.mark.asyncio
async def test_named_options_cache_expires_after_ttl(monkeypatch):
    """A cached --help result older than the TTL is refetched (C17): without
    expiry, an opencli binary upgrade that adds/renames flags keeps being
    routed with the old flag set until the admin process restarts."""
    import backend.channels.opencli_channel as oc

    process = AsyncMock()
    process.communicate.return_value = (b"--alpha <value>", b"")
    with patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
        first = await _get_named_options("ttl-opencli", "site", "cmd")
    assert first == frozenset({"alpha"})
    assert spawn.call_count == 1

    # Age the cache entry past the TTL without a real wait.
    key = ("ttl-opencli", "site", "cmd")
    cached_at, value = oc._help_cache[key]
    oc._help_cache[key] = (cached_at - oc._CACHE_TTL_SECONDS - 1, value)

    process2 = AsyncMock()
    process2.communicate.return_value = (b"--beta <value>", b"")
    with patch("asyncio.create_subprocess_exec", return_value=process2) as spawn2:
        second = await _get_named_options("ttl-opencli", "site", "cmd")
    assert second == frozenset({"beta"})
    assert spawn2.call_count == 1  # refetched, not served the stale entry


@pytest.mark.asyncio
async def test_named_options_cache_hit_within_ttl_skips_subprocess():
    """A cache entry still within its TTL is served without refetching."""
    process = AsyncMock()
    process.communicate.return_value = (b"--gamma <value>", b"")
    with patch("asyncio.create_subprocess_exec", return_value=process):
        await _get_named_options("ttl-fresh-opencli", "site", "cmd")

    with patch("asyncio.create_subprocess_exec") as spawn:
        result = await _get_named_options("ttl-fresh-opencli", "site", "cmd")

    assert result == frozenset({"gamma"})
    spawn.assert_not_called()


def test_peek_named_options_cache_hit_no_subprocess():
    """Cache-only lookup (C17): a warm entry is returned synchronously with
    no subprocess spawn — used by pipeline.py's event-log display string,
    which must not pay a --help call on the collection hot path."""
    import time

    import backend.channels.opencli_channel as oc
    from backend.channels.opencli_channel import _peek_named_options

    oc._help_cache[("peek-bin", "site", "cmd")] = (time.monotonic(), frozenset({"url"}))
    assert _peek_named_options("peek-bin", "site", "cmd") == frozenset({"url"})


def test_peek_named_options_cache_miss_returns_none_no_subprocess():
    """A cold (or expired) cache entry returns None without ever spawning a
    subprocess (C17) — the caller falls back to treating all args as named,
    exactly like a failed --help fetch always did."""
    from backend.channels.opencli_channel import _peek_named_options

    with patch("asyncio.create_subprocess_exec") as spawn:
        result = _peek_named_options("never-cached-bin", "site", "cmd")
    assert result is None
    spawn.assert_not_called()


# ── C20: CDP tab-snapshot failure must not drive tab cleanup ────────────────


@pytest.mark.asyncio
async def test_snapshot_tab_ids_returns_none_on_failure():
    """A failed pre-collection tab snapshot returns None, not an empty set
    (C20): the two are not interchangeable. None means 'no trustworthy
    baseline' and must skip cleanup entirely; an empty set would be treated
    as 'genuinely zero tabs existed before', making every currently-open tab
    look new and get closed."""
    from backend.channels.opencli_channel import _snapshot_tab_ids

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await _snapshot_tab_ids("http://bad-endpoint:1")

    assert result is None


@pytest.mark.asyncio
async def test_snapshot_tab_ids_success_returns_set():
    from backend.channels.opencli_channel import _snapshot_tab_ids

    mock_response = MagicMock()
    mock_response.json = MagicMock(
        return_value=[{"id": "tab-1"}, {"id": "tab-2"}, {"no_id": True}]
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await _snapshot_tab_ids("http://good-endpoint:1")

    assert result == {"tab-1", "tab-2"}


@pytest.mark.asyncio
async def test_collect_cdp_skips_cleanup_when_snapshot_baseline_failed(channel):
    """If the pre-collection tab snapshot fails, cleanup must not run at all
    this pass (C20) — with no trustworthy baseline it can't tell tabs the
    user already had open from ones opencli just opened, so closing
    anything risks closing the user's own tabs."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"title": "test"}]', "")),
        ),
        patch(
            "backend.channels.opencli_channel._snapshot_tab_ids",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.channels.opencli_channel._cleanup_cdp_tabs", new=AsyncMock()
        ) as mock_cleanup,
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is True
    mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_collect_cdp_runs_cleanup_when_snapshot_baseline_ok(channel):
    """Sanity check for the above: when the snapshot succeeds, cleanup still
    runs exactly as before — C20 only changes the failure path."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"title": "test"}]', "")),
        ),
        patch(
            "backend.channels.opencli_channel._snapshot_tab_ids",
            new=AsyncMock(return_value={"pre-existing-tab"}),
        ),
        patch(
            "backend.channels.opencli_channel._cleanup_cdp_tabs", new=AsyncMock()
        ) as mock_cleanup,
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is True
    mock_cleanup.assert_awaited_once_with("http://chrome:9222", {"pre-existing-tab"})


def test_managed_profile_requirement_is_not_forwarded_as_a_cli_argument():
    from backend.channels.opencli_channel import _split_routing_parameters

    routing, cli = _split_routing_parameters(
        {
            "chrome_endpoint": "http://clean:9222",
            "required_profile_kind": "anonymous",
            "url": "https://example.com",
        }
    )

    assert routing == ("http://clean:9222", "anonymous")
    assert cli == {"url": "https://example.com"}


# ── Pure parser function tests ─────────────────────────────────────────────────

def test_parse_json_list():
    raw = '[{"id": 1}, {"id": 2}]'
    result = _parse_json(raw)
    assert result == [{"id": 1}, {"id": 2}]


def test_parse_json_single_object_wrapped():
    raw = '{"id": 1, "name": "test"}'
    result = _parse_json(raw)
    assert result == [{"id": 1, "name": "test"}]


def test_parse_json_with_preamble():
    raw = 'Some preamble text\n{"key": "value"}'
    result = _parse_json(raw)
    assert result == [{"key": "value"}]


def test_parse_json_no_json_raises():
    with pytest.raises(ValueError, match="No JSON found"):
        _parse_json("no json here at all")


def test_parse_yaml_list():
    raw = "- id: 1\n  name: alpha\n- id: 2\n  name: beta\n"
    result = _parse_yaml(raw)
    assert len(result) == 2
    assert result[0]["id"] == 1


def test_parse_yaml_dict_wrapped():
    raw = "id: 1\nname: single\n"
    result = _parse_yaml(raw)
    assert result == [{"id": 1, "name": "single"}]


def test_parse_yaml_scalar_wrapped():
    raw = "hello world"
    result = _parse_yaml(raw)
    assert result == [{"content": "hello world"}]


def test_parse_csv_basic():
    raw = "name,age\nAlice,30\nBob,25"
    result = _parse_csv(raw)
    assert len(result) == 2
    assert result[0]["name"] == "Alice"
    assert result[1]["age"] == "25"


def test_parse_csv_empty():
    raw = "name,age\n"
    result = _parse_csv(raw)
    assert result == []


def test_parse_table_with_box_drawing():
    raw = (
        "┌────────┬───────┐\n"
        "│ Name   │ Score │\n"
        "├────────┼───────┤\n"
        "│ Alice  │ 95    │\n"
        "│ Bob    │ 87    │\n"
        "└────────┴───────┘"
    )
    result = _parse_table(raw)
    assert len(result) == 2
    assert result[0]["Name"] == "Alice"
    assert result[1]["Score"] == "87"


def test_parse_table_no_data_lines():
    raw = "no table here"
    result = _parse_table(raw)
    assert result == [{"content": raw}]


def test_parse_table_header_only():
    raw = "│ Name │ Score │\n"
    result = _parse_table(raw)
    # Only a header row, no data rows — falls back to content
    assert result == [{"content": raw}]


def test_parse_markdown_basic():
    raw = (
        "| Name  | Value |\n"
        "| ----- | ----- |\n"
        "| alpha | 1     |\n"
        "| beta  | 2     |\n"
    )
    result = _parse_markdown(raw)
    assert len(result) == 2
    assert result[0]["Name"] == "alpha"
    assert result[1]["Value"] == "2"


def test_parse_markdown_no_table():
    raw = "just plain text"
    result = _parse_markdown(raw)
    assert result == [{"content": raw}]


def test_parse_markdown_only_header():
    raw = "| Name | Value |\n"
    result = _parse_markdown(raw)
    # Only one line — fewer than 2 pipe-starting lines → fallback
    assert result == [{"content": raw}]


# ── _collect_via_agent tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_via_agent_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "success": True, "items": [{"x": 1}],
        "metadata": {"trace_artifact": "trace://1", "runtime": {"opencli_version": "1.8.7"}},
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    settings = MagicMock(agent_http_timeout=130, api_auth_token="fleet-secret")
    with (
        patch("httpx.AsyncClient", return_value=mock_client_ctx),
        patch("backend.config.get_settings", return_value=settings),
    ):
        result = await _collect_via_agent(
            "http://agent:8000", "example.com", "list", {}, [], "json", "cdp", "exec-1"
        )

    assert result.success is True
    assert result.items == [{"x": 1}]
    assert result.metadata["trace_artifact"] == "trace://1"
    assert result.metadata["runtime"]["opencli_version"] == "1.8.7"
    assert mock_client.post.await_args.kwargs["headers"] == {
        "Authorization": "Bearer fleet-secret"
    }
    assert mock_client.post.await_args.kwargs["json"]["execution_id"] == "exec-1"


@pytest.mark.asyncio
async def test_collect_via_agent_timeout():
    import httpx

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await _collect_via_agent(
            "http://agent:8000", "site", "cmd", {}, [], "json", "cdp"
        )

    assert result.success is False
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_via_agent_http_error():

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await _collect_via_agent(
            "http://agent:8000", "site", "cmd", {}, [], "json", "cdp"
        )

    assert result.success is False
    assert "failed" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_via_agent_error_response():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"success": False, "error": "command not found"}
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await _collect_via_agent(
            "http://agent:8000", "site", "cmd", {}, [], "json", "cdp"
        )

    assert result.success is False
    assert "command not found" in result.error


# ── _run_opencli tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_opencli_success():
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b'[{"id":1}]', b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("asyncio.wait_for", return_value=(b'[{"id":1}]', b"")):
            returncode, stdout, stderr = await _run_opencli(
                ["/opt/opencli-cdp/bin/opencli", "example.com", "list"], {}
            )

    assert returncode == 0


@pytest.mark.asyncio
async def test_run_opencli_file_not_found():
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("no such file")):
        with pytest.raises(FileNotFoundError):
            await _run_opencli(["/nonexistent/opencli", "site", "cmd"], {})


@pytest.mark.asyncio
async def test_run_opencli_timeout():
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("asyncio.wait_for", side_effect=TimeoutError()):
            with pytest.raises(asyncio.TimeoutError):
                await _run_opencli(["/opt/opencli-cdp/bin/opencli", "site", "cmd"], {})


@pytest.mark.asyncio
async def test_run_opencli_cancellation_kills_the_child_process():
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        patch(
            "backend.channels.opencli_channel._kill_subprocess", new=AsyncMock()
        ) as kill_tree,
        pytest.raises(asyncio.CancelledError),
    ):
        await _run_opencli(["opencli", "site", "cmd"], {})

    kill_tree.assert_awaited_once_with(mock_proc)


# ── validate_config tests ──────────────────────────────────────────────────────

@pytest.fixture
def channel():
    return OpenCLIChannel()


@pytest.fixture(autouse=True)
def opencli_manifest_mocks(monkeypatch):
    """Keep unit tests offline: command manifest probing is tested by collect paths."""
    # Other pool test modules intentionally leave the module singleton in
    # different implementations.  Channel tests must not inherit a Redis pool
    # and block for its production BLPOP timeout when the full suite runs.
    from backend.browser_pool import init_pool

    init_pool(["http://chrome:9222"], use_redis=False)
    state = {"named_options": frozenset(), "requires_browser": True}

    async def fake_get_named_options(*_args, **_kwargs):
        return state["named_options"]

    async def fake_command_requires_browser(*_args, **_kwargs):
        return state["requires_browser"]

    monkeypatch.setattr(
        "backend.channels.opencli_channel._get_named_options",
        fake_get_named_options,
    )
    monkeypatch.setattr(
        "backend.channels.opencli_channel._command_requires_browser",
        fake_command_requires_browser,
    )
    yield state


@pytest.mark.asyncio
async def test_validate_config_missing_site(channel):
    errors = await channel.validate_config({"command": "list"})
    assert any("site" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_missing_command(channel):
    errors = await channel.validate_config({"site": "example.com"})
    assert any("command" in e for e in errors)


@pytest.mark.asyncio
async def test_validate_config_valid(channel):
    errors = await channel.validate_config({"site": "example.com", "command": "list"})
    assert errors == []


@pytest.mark.asyncio
async def test_validate_config_missing_both(channel):
    errors = await channel.validate_config({})
    assert len(errors) == 2


# ── health_check tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_binary_exists(channel):
    with patch("os.path.isfile", return_value=True):
        result = await channel.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_binary_missing(channel):
    with patch("os.path.isfile", return_value=False), \
         patch("shutil.which", return_value=None):
        result = await channel.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_agent_mode_skips_pool_probe(channel):
    """Agent mode has no local CDP endpoint — the binary check alone stands,
    since a remote agent's health is a separate registration concern."""
    with (
        patch("os.path.isfile", return_value=True),
        patch(
            "backend.config.get_settings",
            return_value=_make_mock_settings(collection_mode="agent"),
        ),
        patch("backend.browser_pool.get_pool") as mock_get_pool,
    ):
        result = await channel.health_check()
    assert result is True
    mock_get_pool.assert_not_called()


@pytest.mark.asyncio
async def test_health_check_bridge_mode_skips_cdp_probe(channel):
    """Bridge mode has no local /json/version to hit — reachability there is
    the daemon's concern, not this probe's."""
    mock_pool = _make_mock_pool(mode="bridge")
    with (
        patch("os.path.isfile", return_value=True),
        patch(
            "backend.config.get_settings",
            return_value=_make_mock_settings(collection_mode="local"),
        ),
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
    ):
        result = await channel.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_cdp_mode_reachable_returns_true(channel):
    mock_pool = _make_mock_pool(mode="cdp")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("os.path.isfile", return_value=True),
        patch(
            "backend.config.get_settings",
            return_value=_make_mock_settings(collection_mode="local"),
        ),
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("httpx.AsyncClient", return_value=mock_client_ctx),
    ):
        result = await channel.health_check()
    assert result is True
    mock_client.get.assert_called_once()
    assert "/json/version" in mock_client.get.call_args.args[0]


@pytest.mark.asyncio
async def test_health_check_cdp_mode_unreachable_returns_false(channel):
    mock_pool = _make_mock_pool(mode="cdp")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=OSError("connection refused"))
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("os.path.isfile", return_value=True),
        patch(
            "backend.config.get_settings",
            return_value=_make_mock_settings(collection_mode="local"),
        ),
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("httpx.AsyncClient", return_value=mock_client_ctx),
    ):
        result = await channel.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_routes_to_source_bound_endpoint(channel, db_engine):
    """A source with a browser binding for its site must probe that bound
    endpoint, not an arbitrary pool member — same lookup pipeline.py does
    before collect() so the probe reflects the endpoint this source actually
    uses."""
    from backend.services import browser_service

    sm = _sessionmaker(db_engine)
    async with sm() as session:
        await browser_service.create_binding(
            session, browser_endpoint="http://bound-chrome:9222", site="example.com"
        )
        await session.commit()

    mock_pool = _make_mock_pool(mode="cdp")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("os.path.isfile", return_value=True),
        patch(
            "backend.config.get_settings",
            return_value=_make_mock_settings(collection_mode="local"),
        ),
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.database.AsyncSessionLocal", sm),
        patch("httpx.AsyncClient", return_value=mock_client_ctx),
    ):
        result = await channel.health_check({"site": "example.com"})

    assert result is True
    mock_pool.acquire.assert_called_once_with(endpoint="http://bound-chrome:9222")


@pytest.mark.asyncio
async def test_health_check_no_binding_falls_back_to_default_pool_member(channel, db_engine):
    """No binding row for the site → acquire(endpoint=None), same as before
    this fix (a missing binding is not an error, matches pipeline.py's
    best-effort resolution)."""
    sm = _sessionmaker(db_engine)
    mock_pool = _make_mock_pool(mode="cdp")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("os.path.isfile", return_value=True),
        patch(
            "backend.config.get_settings",
            return_value=_make_mock_settings(collection_mode="local"),
        ),
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.database.AsyncSessionLocal", sm),
        patch("httpx.AsyncClient", return_value=mock_client_ctx),
    ):
        result = await channel.health_check({"site": "unbound-site.com"})

    assert result is True
    mock_pool.acquire.assert_called_once_with(endpoint=None)


# ── collect: agent mode tests ──────────────────────────────────────────────────

def _make_mock_pool(mode="cdp", agent_url="http://agent:8000", agent_protocol="http"):
    pool = MagicMock()
    pool.get_mode = MagicMock(return_value=mode)
    pool.get_agent_url = MagicMock(return_value=agent_url)
    pool.get_agent_protocol = MagicMock(return_value=agent_protocol)
    pool.endpoints = ["http://chrome:9222"]

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value="http://chrome:9222")
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _make_mock_settings(collection_mode="local", task_executor="local"):
    settings = MagicMock()
    settings.collection_mode = collection_mode
    settings.task_executor = task_executor
    return settings


@pytest.mark.asyncio
async def test_collect_agent_mode_http_success(channel):
    """Agent mode with http protocol dispatches to _collect_via_agent."""

    mock_pool = _make_mock_pool(
        mode="cdp",
        agent_url="http://agent:8000",
        agent_protocol="http",
    )
    mock_settings = _make_mock_settings(collection_mode="agent")

    agent_result = ChannelResult.ok([{"item": 1}], site="example.com", command="list")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._collect_via_agent",
            new=AsyncMock(return_value=agent_result),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is True
    assert result.items == [{"item": 1}]


@pytest.mark.asyncio
async def test_collect_agent_mode_ws_protocol_not_implemented(channel, db_engine):
    """Agent mode with ws protocol returns fail (not yet implemented)."""
    from unittest.mock import create_autospec

    from backend.browser_pool import LocalBrowserPool

    # Use create_autospec so isinstance(mock_pool, LocalBrowserPool) is True
    mock_pool = create_autospec(LocalBrowserPool, instance=True)
    mock_pool.get_mode.return_value = "cdp"
    mock_pool.get_agent_url.return_value = "http://agent:8000"
    mock_pool.get_agent_protocol.return_value = "ws"
    mock_pool.endpoints = ["http://chrome:9222"]
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value="http://chrome:9222")
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire.return_value = cm

    mock_settings = _make_mock_settings(collection_mode="agent")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list"}, {}
        )

    assert result.success is False
    assert "WS" in result.error or "ws" in result.error.lower() or "not yet" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_agent_mode_unknown_protocol(channel, db_engine):
    """Agent mode with unknown protocol returns fail."""
    from unittest.mock import create_autospec

    from backend.browser_pool import LocalBrowserPool

    mock_pool = create_autospec(LocalBrowserPool, instance=True)
    mock_pool.get_mode.return_value = "cdp"
    mock_pool.get_agent_url.return_value = "http://agent:8000"
    mock_pool.get_agent_protocol.return_value = "grpc"
    mock_pool.endpoints = ["http://chrome:9222"]
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value="http://chrome:9222")
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire.return_value = cm

    mock_settings = _make_mock_settings(collection_mode="agent")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list"}, {}
        )

    assert result.success is False
    assert "grpc" in result.error.lower() or "unknown" in result.error.lower()


# ── collect: local (subprocess) mode tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_local_nonbrowser_command_bypasses_browser_pool(
    channel, opencli_manifest_mocks
):
    """Static/news commands should collect directly without requiring Chrome."""
    opencli_manifest_mocks["named_options"] = frozenset({"limit"})
    opencli_manifest_mocks["requires_browser"] = False
    mock_settings = _make_mock_settings(collection_mode="local")
    captured: list[list[str]] = []

    async def fake_run_opencli(cmd, env):
        captured.append(cmd)
        return 0, '[{"title": "news"}]', ""

    with (
        patch("backend.browser_pool.get_pool") as mock_get_pool,
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(side_effect=fake_run_opencli),
        ),
    ):
        result = await channel.collect(
            {
                "site": "bbc",
                "command": "news",
                "format": "json",
                "args": {"limit": 1},
            },
            {},
        )

    assert result.success is True
    assert result.items == [{"title": "news"}]
    mock_get_pool.assert_not_called()
    assert captured[0][-4:] == ["--limit", "1", "-f", "json"]


@pytest.mark.asyncio
async def test_collect_local_cdp_success(channel):
    """Local CDP mode runs opencli subprocess and parses JSON output."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"title": "test"}]', "")),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is True
    assert result.items == [{"title": "test"}]


@pytest.mark.asyncio
async def test_collect_local_bridge_mode(channel):
    """Local bridge mode uses bridge binary and sets DAEMON env vars."""
    mock_pool = _make_mock_pool(mode="bridge")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"r": 1}]', "")),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is True


@pytest.mark.asyncio
async def test_collect_local_bridge_probe_failure_does_not_block_subprocess(channel):
    """The bridge readiness probe is advisory; the opencli command decides success."""
    mock_pool = _make_mock_pool(mode="bridge")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._check_bridge_ready",
            new=AsyncMock(return_value="bridge not connected"),
        ) as mock_bridge_probe,
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"r": 1}]', "")),
        ) as mock_run_opencli,
    ):
        result = await channel.collect(
            {"site": "twitter", "command": "search", "format": "json"}, {}
        )

    assert result.success is True
    mock_bridge_probe.assert_awaited_once()
    mock_run_opencli.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_local_nonzero_exit_code(channel):
    """Non-zero exit code returns failed ChannelResult."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(1, "", "command failed")),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list"}, {}
        )

    assert result.success is False
    assert "exited with code 1" in result.error


@pytest.mark.asyncio
async def test_collect_local_timeout(channel):
    """asyncio.TimeoutError in subprocess returns failed ChannelResult."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(side_effect=TimeoutError()),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list"}, {}
        )

    assert result.success is False
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_local_binary_not_found(channel):
    """FileNotFoundError in subprocess returns failed ChannelResult."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(side_effect=FileNotFoundError("binary not found")),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list"}, {}
        )

    assert result.success is False
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_local_parse_error(channel):
    """Invalid JSON output returns failed ChannelResult."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, "not valid json at all", "")),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is False
    assert "parse" in result.error.lower()


@pytest.mark.asyncio
async def test_collect_subprocess_exception(channel):
    """Generic exception from subprocess returns failed ChannelResult."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(side_effect=OSError("unexpected error")),
        ),
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list"}, {}
        )

    assert result.success is False
    assert "Failed to run" in result.error


# ── fetch(): thick-contract migration ───────────────────────────────────────
#
# OpenCLIChannel migrates onto fetch() via the same narrow-override pattern as
# BrowserActChannel (backend.channels.browser_act_channel): fetch() delegates
# straight to collect() — the internal transport (subprocess / LAN-agent HTTP /
# WS-agent dispatch) is unchanged and unmocked-transport-wise identical to the
# collect() tests above; only the entry point differs.


def test_opencli_channel_fetch_is_migrated():
    """channel_runner.run_channel's migration check
    (`type(chan).fetch is not AbstractChannel.fetch`) must recognize
    OpenCLIChannel as migrated now that fetch() is overridden."""
    assert OpenCLIChannel.fetch is not AbstractChannel.fetch


@pytest.mark.asyncio
async def test_fetch_returns_items_via_mocked_subprocess(channel):
    """fetch() — the entry point run_channel actually calls in production —
    returns items from opencli's real collection machinery, mocked at the same
    seam (_run_opencli) the collect() tests above use. Never a real subprocess."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"title": "test"}]', "")),
        ),
    ):
        ctx = FetchContext(
            config={"site": "example.com", "command": "list", "format": "json"},
            params={},
        )
        result = await channel.fetch(ctx)

    assert result.items == [{"title": "test"}]
    # Metadata passthrough matters for opencli specifically (node_url /
    # chrome_mode reach the runner through FetchResult.metadata).
    assert result.metadata.get("chrome_mode") == "cdp"


@pytest.mark.asyncio
async def test_fetch_transport_timeout_classifies_as_retryable(channel):
    """A subprocess timeout's error_type ("TimeoutError", set by
    _collect_with_opencli_subprocess) must reach error_taxonomy as retryable —
    proves fetch()'s ChannelFetchError(error_type=...) plumbing carries the
    classification through, not just that collect() sets it right internally."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(side_effect=TimeoutError()),
        ),
    ):
        ctx = FetchContext(config={"site": "example.com", "command": "list"}, params={})
        with pytest.raises(ChannelFetchError) as exc_info:
            await channel.fetch(ctx)

    assert exc_info.value.error_type == "TimeoutError"
    assert is_retryable(effective_error_type(exc_info.value)) is True


@pytest.mark.asyncio
async def test_fetch_binary_not_found_classifies_as_permanent(channel):
    """FileNotFoundError's error_type ("FileNotFoundError") must classify as
    permanent through the same fetch()->ChannelFetchError->error_taxonomy path —
    retrying a missing binary can't ever succeed."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(side_effect=FileNotFoundError("binary not found")),
        ),
    ):
        ctx = FetchContext(config={"site": "example.com", "command": "list"}, params={})
        with pytest.raises(ChannelFetchError) as exc_info:
            await channel.fetch(ctx)

    assert exc_info.value.error_type == "FileNotFoundError"
    assert is_retryable(effective_error_type(exc_info.value)) is False


@pytest.mark.asyncio
async def test_collect_does_not_route_through_fetch(channel):
    """Legacy path guard: collect() must stay the single source of truth for
    site-routing (unlike api_channel/crawl4ai_channel's inverted
    collect()-calls-fetch() pattern) — migrating fetch() must not silently
    become a rewrite of collect()'s dispatch logic. Same mocked seam and config
    as test_collect_local_cdp_success, plus a spy proving fetch() is never
    called from inside collect()."""
    mock_pool = _make_mock_pool(mode="cdp")
    mock_settings = _make_mock_settings(collection_mode="local")

    with (
        patch("backend.browser_pool.get_pool", return_value=mock_pool),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"title": "test"}]', "")),
        ),
        patch.object(
            channel,
            "fetch",
            new=AsyncMock(side_effect=AssertionError("collect() must not call fetch()")),
        ) as mock_fetch,
    ):
        result = await channel.collect(
            {"site": "example.com", "command": "list", "format": "json"}, {}
        )

    assert result.success is True
    assert result.items == [{"title": "test"}]
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_run_channel_builds_rate_limited_client_for_opencli(
    opencli_manifest_mocks,
):
    """End-to-end proof of the migration through the real runner entry point
    (mirrors test_channel_runner.py's
    test_migrated_channel_still_builds_rate_limited_client_when_none_injected,
    and test_rss_fetch.py's test_run_channel_drives_rss_and_persists_cursor, but
    with the real OpenCLIChannel): a migrated channel gets a RateLimitedClient
    built from its declared default_rate when the caller injects no http of its
    own — even though opencli's fetch() never reads ctx.http (documented
    accepted trade-off, same as BrowserActChannel)."""
    from types import SimpleNamespace

    from backend.pipeline.channel_runner import run_channel
    from backend.pipeline.cursor_store import InMemoryCursorStore

    opencli_manifest_mocks["requires_browser"] = False
    mock_settings = _make_mock_settings(collection_mode="local")
    source = SimpleNamespace(
        id="src-opencli-1",
        channel_type="opencli",
        channel_config={"site": "bbc", "command": "news", "format": "json"},
    )

    with (
        patch("backend.config.get_settings", return_value=mock_settings),
        patch(
            "backend.channels.opencli_channel._run_opencli",
            new=AsyncMock(return_value=(0, '[{"title": "news"}]', "")),
        ),
        patch("backend.pipeline.channel_runner.RateLimitedClient") as mock_rlc,
    ):
        mock_rlc.return_value.aclose = AsyncMock()
        result = await run_channel(
            source, {}, channel=OpenCLIChannel(), cursor_store=InMemoryCursorStore()
        )

    assert result.items == [{"title": "news"}]
    mock_rlc.assert_called_once()
    from backend.pipeline.http_client import TokenBucket, parse_rate

    bucket = mock_rlc.call_args.args[1]
    assert isinstance(bucket, TokenBucket)
    assert bucket.rate == parse_rate(OpenCLIChannel().capabilities.default_rate)
    assert bucket.rate == parse_rate("60/min")
