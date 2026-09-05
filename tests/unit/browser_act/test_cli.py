"""Unit tests for backend.browser_act.cli (Browser Act integration PR-B).

Mirrors tests/unit/channels/test_cli_channel.py conventions: patch
asyncio.create_subprocess_exec / asyncio.wait_for with AsyncMock, and use a
real sys.executable round trip to exercise the actual subprocess machinery
once.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.browser_act import cli


def _make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> AsyncMock:
    """Build a mock asyncio subprocess-transport object.

    ``kill`` is forced to a plain (sync) Mock — the real API's
    ``proc.kill()`` is synchronous — matching test_cli_channel.py's exact
    override of the same default.
    """
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = Mock()
    proc.wait = AsyncMock()
    return proc


# ── Binary resolution ─────────────────────────────────────────────────────


def test_resolve_bin_defaults_to_browser_act(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    assert cli._resolve_bin() == "browser-act"


def test_resolve_bin_reads_env_override(monkeypatch):
    monkeypatch.setenv("BROWSER_ACT_BIN", "/opt/tools/browser-act-custom")
    assert cli._resolve_bin() == "/opt/tools/browser-act-custom"


@pytest.mark.asyncio
async def test_browser_act_bin_env_override_used_in_subprocess_call(monkeypatch):
    """The overridden binary path is what create_subprocess_exec is called with."""
    monkeypatch.setenv("BROWSER_ACT_BIN", "/opt/tools/browser-act-custom")
    proc = _make_proc(stdout=b"ok")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        await cli.version()
    args, _kwargs = spawn.call_args
    assert args[0] == "/opt/tools/browser-act-custom"


# ── version() / get_skills() ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_version_returns_parsed_stdout(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"browser-act 2.0.2\n")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        result = await cli.version()
    assert result == "browser-act 2.0.2"
    spawn.assert_awaited_once_with(
        "browser-act",
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=None,
    )


@pytest.mark.asyncio
async def test_get_skills_argv_with_version(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"skills content")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        result = await cli.get_skills("core", "2.0.2")
    assert result == "skills content"
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "get-skills", "core", "--skill-version", "2.0.2")


@pytest.mark.asyncio
async def test_get_skills_argv_without_version(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"skills content")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        await cli.get_skills()
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "get-skills", "core")


# ── Session argv construction ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_navigate_argv(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"{}")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        async with cli.session("t") as sess:
            await sess.navigate("https://x.com")
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "navigate", "https://x.com")


@pytest.mark.asyncio
async def test_session_wait_argv(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"{}")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        async with cli.session("t") as sess:
            await sess.wait()
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "wait", "stable")


@pytest.mark.asyncio
async def test_session_eval_argv(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b'{"result": 1}')
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        async with cli.session("t") as sess:
            result = await sess.eval("document.title")
    assert result == '{"result": 1}'
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "eval", "document.title")


@pytest.mark.asyncio
async def test_session_state_argv(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"url=https://x.com")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        async with cli.session("t") as sess:
            await sess.state()
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "state")


@pytest.mark.asyncio
async def test_session_click_argv(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"{}")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        async with cli.session("t") as sess:
            await sess.click(4)
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "click", "4")


@pytest.mark.asyncio
async def test_session_input_argv(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"{}")
    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        async with cli.session("t") as sess:
            await sess.input(2, "hello@example.com")
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "input", "2", "hello@example.com")


# ── Shell-injection safety (architecture decision #6 — REQUIRED) ─────────


@pytest.mark.asyncio
async def test_eval_shell_metachars_stay_single_argv_element(monkeypatch):
    """A param containing shell metachars must never be split or interpolated
    into a shell string — create_subprocess_exec (not _shell) is used, and the
    dangerous string travels as ONE argv element, verbatim."""
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    dangerous = "foo(); rm -rf / #"
    proc = _make_proc(stdout=b"{}")
    with (
        patch("asyncio.create_subprocess_exec", return_value=proc) as spawn,
        patch("asyncio.create_subprocess_shell") as spawn_shell,
    ):
        async with cli.session("t") as sess:
            await sess.eval(dangerous)

    spawn_shell.assert_not_called()
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "eval", dangerous)
    # exactly one argv element carries the dangerous string, never split
    assert sum(1 for a in args if a == dangerous) == 1


@pytest.mark.asyncio
async def test_input_shell_metachars_stay_single_argv_element(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    dangerous = "$(whoami) && ls"
    proc = _make_proc(stdout=b"{}")
    with (
        patch("asyncio.create_subprocess_exec", return_value=proc) as spawn,
        patch("asyncio.create_subprocess_shell") as spawn_shell,
    ):
        async with cli.session("t") as sess:
            await sess.input(5, dangerous)

    spawn_shell.assert_not_called()
    args, _kwargs = spawn.call_args
    assert args == ("browser-act", "--session", "t", "input", "5", dangerous)
    assert sum(1 for a in args if a == dangerous) == 1


# ── Timeout -> kill (never orphan the child) ─────────────────────────────


@pytest.mark.asyncio
async def test_timeout_kills_process_and_raises(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = AsyncMock()
    proc.kill = Mock()
    proc.wait = AsyncMock()
    with (
        patch("asyncio.create_subprocess_exec", return_value=proc),
        patch("asyncio.wait_for", side_effect=TimeoutError()),
    ):
        with pytest.raises(cli.BrowserActError, match="timed out"):
            await cli.version()
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


# ── Non-zero exit -> BrowserActError, no secret leakage ──────────────────


@pytest.mark.asyncio
async def test_nonzero_exit_raises_with_stderr(monkeypatch):
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"", stderr=b"boom: element not found", returncode=1)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(cli.BrowserActError) as excinfo:
            async with cli.session("t") as sess:
                await sess.click(3)
    assert "boom: element not found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_error_message_never_contains_env_secrets(monkeypatch):
    """env carries the BrowserAct API key (PR-C); it must never leak into the
    BrowserActError message even though the call that failed used that env."""
    monkeypatch.delenv("BROWSER_ACT_BIN", raising=False)
    proc = _make_proc(stdout=b"", stderr=b"auth failed", returncode=1)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(cli.BrowserActError) as excinfo:
            async with cli.session("t", env={"BROWSER_ACT_API_KEY": "sekret"}) as sess:
                await sess.state()
    message = str(excinfo.value)
    assert "sekret" not in message
    assert "auth failed" in message


# ── Real exec-path round trip (mirrors test_cli_channel.py) ──────────────


@pytest.mark.asyncio
async def test_real_exec_path_round_trip(monkeypatch):
    """Exercise the actual subprocess machinery end to end via sys.executable
    standing in for the browser-act binary."""
    monkeypatch.setenv("BROWSER_ACT_BIN", sys.executable)
    result = await cli._run(["-c", "print('browser-act-cli-real-exec-ok')"])
    assert result.returncode == 0
    assert "browser-act-cli-real-exec-ok" in result.stdout


@pytest.mark.asyncio
async def test_real_exec_path_nonzero_exit_raises(monkeypatch):
    monkeypatch.setenv("BROWSER_ACT_BIN", sys.executable)
    with pytest.raises(cli.BrowserActError):
        await cli._run(["-c", "import sys; sys.exit(1)"])
