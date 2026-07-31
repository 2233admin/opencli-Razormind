"""browser-act CLI subprocess wrapper.

Distinct from both ``backend/cli.py`` (the opencli-skill HTTP client) and
``backend/browser_act_packs/`` (PR-A's vendored SKILL.md packs). This module
only knows how to invoke the external ``browser-act`` binary safely.

Subprocess safety mirrors ``backend/channels/cli_channel.py`` exactly:
``asyncio.create_subprocess_exec`` + ``asyncio.wait_for(timeout)`` +
``TimeoutError`` -> ``proc.kill()`` + ``await proc.wait()`` so a timed-out
child is never orphaned.

Both hops (this CLI, and the scripts/*.py -> eval-js hop the future channel
drives) are argv-only. Never
``asyncio.create_subprocess_shell``, never ``shell=True``, never a
string-joined command. User-controlled values (URLs, JS, input text) always
travel as individual argv elements so they can never be interpreted by a
shell or interpolated into one.

Binary resolution mirrors ``backend/channels/opencli_channel.py``'s
``OPENCLI_BIN`` approach: a plain ``os.environ.get`` lookup, read at call
time (not cached at import time) via ``_resolve_bin()`` so tests can
monkeypatch the env var freely.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


def _resolve_bin() -> str:
    """Return the configured browser-act binary, read fresh on every call."""
    return os.environ.get("BROWSER_ACT_BIN") or "browser-act"


class BrowserActError(Exception):
    """Raised on a non-zero browser-act exit or a timed-out call.

    The message carries the subcommand + argv + captured stderr so failures
    are diagnosable, but it deliberately NEVER includes the env dict passed
    to the subprocess: PR-C injects secrets (e.g. the BrowserAct API key)
    via env, never argv, specifically so they never end up in argv-based
    logging — this exception must uphold the same guarantee for error text.
    """


@dataclass
class BrowserActResult:
    """Raw result of one browser-act invocation."""

    returncode: int
    stdout: str
    stderr: str


async def _run(
    args: list[str],
    *,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> BrowserActResult:
    """Run ``browser-act <args>`` as a plain argv subprocess.

    Never shell=True, never create_subprocess_shell, never a joined command
    string (architecture decision #6) — ``args`` is passed straight through
    to ``create_subprocess_exec`` as separate argv elements.

    ``env``, if given, is merged over a copy of ``os.environ`` (so the child
    still gets PATH etc.); if omitted, the child simply inherits the parent
    environment (``env=None``).

    ``timeout`` defaults to ``get_settings().browser_act_timeout`` when not
    given explicitly.
    """
    bin_path = _resolve_bin()

    if timeout is None:
        from backend.config import get_settings

        timeout = get_settings().browser_act_timeout

    merged_env: dict[str, str] | None = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)

    proc = await asyncio.create_subprocess_exec(
        bin_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        # wait_for only cancels communicate() — the child itself keeps
        # running until explicitly killed. Mirrors cli_channel.py exactly so
        # a timed-out browser-act call never leaves an orphan process.
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise BrowserActError(
            f"browser-act {' '.join(args)} timed out after {timeout}s"
        ) from exc

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise BrowserActError(
            f"browser-act {' '.join(args)} exited with code {proc.returncode}: "
            f"{stderr_text}"
        )

    return BrowserActResult(returncode=proc.returncode, stdout=stdout_text, stderr=stderr_text)


async def version() -> str:
    """Return ``browser-act --version`` output, stripped."""
    result = await _run(["--version"])
    return result.stdout.strip()


async def get_skills(
    kind: str = "core",
    skill_version: str | None = None,
    *,
    timeout: float | None = None,
) -> str:
    """Run ``browser-act get-skills <kind> [--skill-version <v>]``."""
    args = ["get-skills", kind]
    if skill_version:
        args.extend(["--skill-version", skill_version])
    result = await _run(args, timeout=timeout)
    return result.stdout


@dataclass
class BrowserActSession:
    """A named, stateful browser-act session.

    Every subcommand is prefixed with ``--session <name>`` (the CLI's global
    option, placed BEFORE the subcommand per the upstream docs, e.g.
    ``browser-act --session my-task navigate <url>``) and shares this
    session's ``env`` across calls.
    """

    name: str
    env: dict[str, str] | None = None

    async def run(self, args: list[str], *, timeout: float | None = None) -> str:
        """Escape hatch: run an arbitrary subcommand under this session."""
        result = await _run(
            ["--session", self.name, *args], timeout=timeout, env=self.env
        )
        return result.stdout

    async def navigate(self, url: str, *, timeout: float | None = None) -> str:
        return await self.run(["navigate", url], timeout=timeout)

    async def wait(self, mode: str = "stable", *, timeout: float | None = None) -> str:
        return await self.run(["wait", mode], timeout=timeout)

    async def eval(self, js: str, *, timeout: float | None = None) -> str:
        return await self.run(["eval", js], timeout=timeout)

    async def state(self, *, timeout: float | None = None) -> str:
        return await self.run(["state"], timeout=timeout)

    async def click(self, index: int, *, timeout: float | None = None) -> str:
        return await self.run(["click", str(index)], timeout=timeout)

    async def input(
        self, index: int, value: str, *, timeout: float | None = None
    ) -> str:
        return await self.run(["input", str(index), value], timeout=timeout)


@asynccontextmanager
async def session(
    name: str, *, env: dict[str, str] | None = None
) -> AsyncIterator[BrowserActSession]:
    """Scope a browser-act session name/env for a block of calls.

    Deliberately MINIMAL lifecycle (Browser Act integration architecture decision #10): this
    does NOT call ``browser open`` on enter nor ``session close`` on exit.
    Browser creation/deletion carries BrowserAct's own confirmation-gate
    concern, and owning that gate is PR-C's job (the manifest-interpreter
    channel), not this low-level CLI wrapper's. A non-interactive
    ``browser-act session close <name>`` command does exist upstream, but
    since this context manager never opens the browser either, closing it
    here would be asymmetric and could tear down a session a PR-C caller
    still needs — so cleanup here is intentionally a no-op.
    """
    yield BrowserActSession(name=name, env=env)
