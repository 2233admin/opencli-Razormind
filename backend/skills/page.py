"""CDP page wrapper — drive a browser_pool Chrome over Playwright (ADR-0003 D1).

Connects **over CDP** (``chromium.connect_over_cdp(cdp_endpoint)``) to an
**already-running** Chrome supplied by ``backend.browser_pool`` — the same
substrate the opencli channel relies on. ``connect_over_cdp`` *attaches* to the
existing browser context, so a logged-in page (site cookies already present in
that Chrome) is reused; it does **not** launch a new browser. Local + LAN
endpoints only (ADR-0003 D1); driving NAT edge nodes via ``agent_server`` is v2.

``SkillPage`` exposes only the raw page ops the fixed verb set (#02) calls —
``goto / click / type / select / scroll / inner_text / extract`` — all
``ref``-addressed (a ``ref`` is the ``N`` that ``perception.snapshot()`` wrote
as ``data-skill-ref="N"``). It makes **no** risk decisions and exposes **no**
model-facing ``evaluate(js)`` escape hatch (ADR-0003 D2/D3); the single internal
``evaluate`` (for ``scroll``) stays server-side and is never surfaced to the
model.

The caller owns the pool-slot lifetime: pass in the endpoint string that
``browser_pool.get_pool().acquire(endpoint=...)`` yields; do **not** acquire the
slot inside ``SkillPage``. On close we drop the **CDP connection** (and stop the
Playwright driver) — we never close the underlying Chrome owned by the pool.
"""

import asyncio
import json
import logging
import math
import os
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_DEFAULT_TASK_SPACE_TTL_SECONDS = 600.0
_BRIDGE_COMMAND_TIMEOUT_SECONDS = 15.0


def _ref_selector(ref: str | int) -> str:
    """CSS selector resolving an element strictly by its data-skill-ref.

    A ``ref`` is the ``N`` ``perception.snapshot()`` assigned as
    ``data-skill-ref="N"``. Resolving strictly by that attribute means a stale
    ref fails loudly (no element) rather than silently clicking the wrong one.
    """
    return f'[data-skill-ref="{ref}"]'




class SkillPage:
    """Thin async wrapper around a CDP-attached Playwright page.

    Holds the Playwright handle, the connected browser, and the active page.
    Build it via :func:`open_skill_page`; use it as an async context manager so
    the loop (#03) can ``async with open_skill_page(ep) as sp:``.
    """

    def __init__(
        self,
        pw: Any,
        browser: Any,
        page: Any,
        *,
        endpoint: str | None = None,
        task_space: str | None = None,
        context: Any = None,
    ) -> None:
        self._pw = pw
        self._browser = browser
        self.page = page
        self.endpoint = endpoint
        self.task_space = task_space
        self._closed = False
        self._context = context

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def __aenter__(self) -> "SkillPage":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close this direct CDP connection without touching Chrome."""
        await self._close_connection()

    async def _close_connection(self) -> None:
        """Close the owned Playwright connection without touching Chrome."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception as exc:  # pragma: no cover - teardown best-effort
            logger.debug("SkillPage: browser connection close failed: %s", exc)
        finally:
            self._browser = None
            try:
                if self._pw is not None:
                    await self._pw.stop()
            except Exception as exc:  # pragma: no cover - teardown best-effort
                logger.debug("SkillPage: playwright stop failed: %s", exc)
            finally:
                self._pw = None

    # ── raw page ops (the verb set #02 dispatches to) ─────────────────────
    async def goto(self, url: str) -> None:
        """Navigate to ``url`` and return when navigation settles."""
        await self.page.goto(url)

    async def click(self, ref: str | int) -> None:
        """Click the element tagged ``data-skill-ref="<ref>"``."""
        await self.page.locator(_ref_selector(ref)).click()

    async def type(self, ref: str | int, text: str, submit: bool = False) -> None:
        """Fill the ``ref`` element with ``text``; optionally press Enter."""
        locator = self.page.locator(_ref_selector(ref))
        await locator.fill(text)
        if submit:
            await locator.press("Enter")

    async def select(self, ref: str | int, value: str) -> None:
        """Select ``value`` in the ``ref`` ``<select>`` element."""
        await self.page.locator(_ref_selector(ref)).select_option(value)

    async def scroll(self, direction: str) -> None:
        """Scroll one viewport up/down.

        The only internal ``page.evaluate`` use besides perception; it is **not**
        exposed to the model (ADR-0003 D3 forbids a model-facing ``evaluate``).
        """
        sign = -1 if str(direction).lower() in ("up", "top", "-1") else 1
        await self.page.evaluate(
            "(s) => window.scrollBy(0, s * window.innerHeight)", sign
        )

    async def inner_text(self) -> str:
        """Return the page's visible text (for the ``extract`` verb). Text, not HTML."""
        return await self.page.inner_text("body")

    async def extract(self) -> str:
        """Alias of :meth:`inner_text` — the ``extract`` verb's text payload."""
        return await self.inner_text()


def _resolve_bridge_bin() -> str:
    """Resolve the dedicated Browser Bridge CLI."""
    configured = os.environ.get("OPENCLI_BRIDGE_BIN")
    if configured:
        return shutil.which(configured) or configured
    default = "/opt/opencli-bridge/bin/opencli"
    if os.path.isfile(default):
        return default
    discovered = shutil.which("opencli-bridge")
    if discovered:
        return discovered
    discovered = shutil.which("opencli")
    if discovered:
        return discovered
    raise RuntimeError(
        "Browser Bridge CLI is unavailable; set OPENCLI_BRIDGE_BIN "
        "to the dedicated bridge executable"
    )


class SkillPageLease:
    """Lease-scoped view that cannot outlive or release another lease."""

    def __init__(self, page: SkillPage, release: Callable[[], Awaitable[None]]) -> None:
        self._page = page
        self._release = release
        self._closed = False
        self.endpoint = page.endpoint
        self.task_space = page.task_space
    @property
    def page(self) -> Any:
        return self._page.page

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("browser session lease is closed")

    async def __aenter__(self) -> "SkillPageLease":
        self._ensure_open()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._release()

    async def goto(self, url: str) -> None:
        self._ensure_open()
        await self._page.goto(url)

    async def click(self, ref: str | int) -> None:
        self._ensure_open()
        await self._page.click(ref)

    async def type(self, ref: str | int, text: str, submit: bool = False) -> None:
        self._ensure_open()
        await self._page.type(ref, text, submit)

    async def select(self, ref: str | int, value: str) -> None:
        self._ensure_open()
        await self._page.select(ref, value)

    async def scroll(self, direction: str) -> None:
        self._ensure_open()
        await self._page.scroll(direction)

    async def inner_text(self) -> str:
        self._ensure_open()
        return await self._page.inner_text()

    async def extract(self) -> str:
        self._ensure_open()
        return await self._page.extract()


async def _run_bridge_command(
    *args: str,
    cdp_endpoint: str | None = None,
    idle_timeout_seconds: float | None = None,
) -> Any:
    """Run one structured Browser Bridge command with a bounded timeout."""
    env = os.environ.copy()
    if cdp_endpoint:
        env["OPENCLI_DAEMON_HOST"] = urlparse(cdp_endpoint).hostname or "localhost"
        env.setdefault("OPENCLI_DAEMON_PORT", "19825")
    if idle_timeout_seconds is not None:
        env["OPENCLI_BROWSER_IDLE_TIMEOUT"] = str(
            max(1, math.ceil(float(idle_timeout_seconds)))
        )
    proc = await asyncio.create_subprocess_exec(
        _resolve_bridge_bin(),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=_BRIDGE_COMMAND_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"Browser Bridge command timed out after {_BRIDGE_COMMAND_TIMEOUT_SECONDS:g}s"
        ) from None
    except BaseException:
        proc.kill()
        with suppress(ProcessLookupError):
            await proc.wait()
        raise
    if proc.returncode:
        message = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Browser Bridge command failed with exit {proc.returncode}: {message}"
        )
    try:
        return json.loads(stdout.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Browser Bridge returned invalid JSON") from exc


def _bridge_target_from_result(result: Any, task_space: str) -> str:
    if not isinstance(result, list):
        raise RuntimeError("Browser Bridge Task Space returned a non-list tab result")
    active_targets = [
        item["page"]
        for item in result
        if isinstance(item, dict)
        and isinstance(item.get("page"), str)
        and item.get("active") is True
    ]
    all_targets = [
        item["page"]
        for item in result
        if isinstance(item, dict) and isinstance(item.get("page"), str)
    ]
    if len(active_targets) == 1:
        return active_targets[0]
    if len(active_targets) == 0 and len(all_targets) == 1:
        return all_targets[0]
    raise RuntimeError(
        f"Browser Bridge Task Space {task_space!r} has no unambiguous active tab"
    )


async def _bridge_task_space_target(
    task_space: str,
    cdp_endpoint: str,
    ttl_seconds: float,
) -> str:
    """Return the active Bridge target for a named Task Space."""
    result = await _run_bridge_command(
        "browser",
        task_space,
        "tab",
        "list",
        cdp_endpoint=cdp_endpoint,
        idle_timeout_seconds=ttl_seconds,
    )
    if result == []:
        result = await _run_bridge_command(
            "browser",
            task_space,
            "tab",
            "new",
            cdp_endpoint=cdp_endpoint,
            idle_timeout_seconds=ttl_seconds,
        )
        result = [result]
    return _bridge_target_from_result(result, task_space)


async def _page_target_id(context: Any, page: Any) -> str | None:
    """Read Playwright's exact CDP target identity for a page."""
    if getattr(page, "is_closed", lambda: True)():
        return None
    session = await context.new_cdp_session(page)
    try:
        result = await session.send("Target.getTargetInfo")
    finally:
        await session.detach()
    target_info = result.get("targetInfo", {}) if isinstance(result, dict) else {}
    target_id = target_info.get("targetId")
    return target_id if isinstance(target_id, str) else None


async def _select_bridge_page(context: Any, target_id: str) -> Any:
    """Select only the active tab explicitly returned by Browser Bridge."""
    for page in context.pages:
        if await _page_target_id(context, page) == target_id:
            return page
    raise RuntimeError("Browser Bridge Task Space target is not attached to the CDP endpoint")


def _connection_is_alive(page: SkillPage) -> bool:
    browser = page._browser
    if browser is None:
        return False
    is_connected = getattr(browser, "is_connected", None)
    if callable(is_connected) and not is_connected():
        return False
    is_closed = getattr(page.page, "is_closed", None)
    return not is_closed() if callable(is_closed) else True


class BrowserSession:
    """A reusable, serialized browser session for one endpoint and Task Space.

    The session owns only the Playwright connection. It never owns or closes the
    underlying Chrome process. A Task Space lease is released by
    ``SkillPage.aclose()``; the connection remains warm until its idle TTL
    expires or ``close_task_space()`` is called.
    """

    def __init__(
        self,
        endpoint: str,
        task_space: str,
        page: SkillPage,
        *,
        ttl_seconds: float,
    ) -> None:
        self.endpoint = endpoint
        self.task_space = task_space
        self.page = page
        self._ttl_seconds = ttl_seconds
        self._last_used = time.monotonic()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._pending_acquires = 0
        self._close_requested = False
        self._closed = False
        self._expiry_task = asyncio.create_task(self._expire_when_idle())
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def expired(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return (
            not self.busy
            and self._pending_acquires == 0
            and current - self._last_used >= self._ttl_seconds
        )

    async def _expire_when_idle(self) -> None:
        while not self._closed:
            if self.busy or self._pending_acquires:
                self._wake.clear()
                await self._wake.wait()
                continue
            delay = max(
                0.0,
                self._last_used + self._ttl_seconds - time.monotonic(),
            )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                if self.expired():
                    await self.close()
                    return

    async def _bridge_heartbeat(self) -> None:
        interval = min(30.0, max(0.25, self._ttl_seconds / 3))
        while not self._closed and self.busy:
            await asyncio.sleep(interval)
            if self._closed or not self.busy:
                return
            try:
                result = await _run_bridge_command(
                    "browser",
                    self.task_space,
                    "tab",
                    "list",
                    cdp_endpoint=self.endpoint,
                    idle_timeout_seconds=self._ttl_seconds,
                )
                target_id = _bridge_target_from_result(result, self.task_space)
                if self.page._context is not None:
                    current_id = await _page_target_id(
                        self.page._context,
                        self.page.page,
                    )
                    if current_id != target_id:
                        self.page.page = await _select_bridge_page(
                            self.page._context,
                            target_id,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Task Space heartbeat failed for %s/%s: %s",
                    self.endpoint,
                    self.task_space,
                    exc,
                )
    async def _stop_bridge_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _reconnect_if_stale(self) -> None:
        if _connection_is_alive(self.page):
            return
        self.page._closed = False
        await self.page._close_connection()
        self.page = await _connect_skill_page(
            self.endpoint,
            task_space=self.task_space,
            task_space_ttl_seconds=self._ttl_seconds,
        )

    async def acquire(self) -> SkillPageLease:
        if self._closed:
            raise RuntimeError("browser session is closed")
        await self._lock.acquire()
        if self._closed:
            self._lock.release()
            raise RuntimeError("browser session is closed")
        try:
            await self._reconnect_if_stale()
        except BaseException:
            self._lock.release()
            self._wake.set()
            raise
        self._last_used = time.monotonic()
        self._heartbeat_task = asyncio.create_task(self._bridge_heartbeat())
        return SkillPageLease(self.page, self.release)

    async def release(self) -> None:
        await self._stop_bridge_heartbeat()
        self._last_used = time.monotonic()
        if self._lock.locked():
            self._lock.release()
        self._wake.set()
        if self._close_requested:
            await self.close()

    async def close(self) -> None:
        if self.busy or self._pending_acquires:
            return
        await self._stop_bridge_heartbeat()
        self._closed = True
        self._wake.set()
        expiry_task = self._expiry_task
        if expiry_task is not None and expiry_task is not asyncio.current_task():
            expiry_task.cancel()
            with suppress(asyncio.CancelledError):
                await expiry_task
        self.page._closed = False
        await self.page._close_connection()


class _BrowserSessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def open(
        self,
        endpoint: str,
        task_space: str,
        *,
        ttl_seconds: float,
    ) -> SkillPageLease:
        key = (endpoint, task_space)
        async with self._lock:
            await self._evict_expired()
            session = self._sessions.get(key)
            if session is None or session._closed:
                page = await _connect_skill_page(
                    endpoint,
                    task_space=task_space,
                    task_space_ttl_seconds=ttl_seconds,
                )
                session = BrowserSession(
                    endpoint,
                    task_space,
                    page,
                    ttl_seconds=ttl_seconds,
                )
                self._sessions[key] = session
            else:
                session._ttl_seconds = ttl_seconds
            session._pending_acquires += 1
        try:
            return await session.acquire()
        finally:
            async with self._lock:
                session._pending_acquires -= 1
                should_close = (
                    session._close_requested
                    and not session.busy
                    and session._pending_acquires == 0
                )
                session._wake.set()
            if should_close:
                await session.close()

    async def close(self, endpoint: str, task_space: str) -> bool:
        key = (endpoint, task_space)
        async with self._lock:
            session = self._sessions.get(key)
            if (
                session is None
                or session.busy
                or session._pending_acquires
            ):
                return False
            self._sessions.pop(key, None)
        await session.close()
        return True

    async def _evict_expired(self) -> None:
        expired = [
            (key, session)
            for key, session in self._sessions.items()
            if session.expired()
        ]
        for key, session in expired:
            self._sessions.pop(key, None)
            await session.close()

    async def close_all(self) -> None:
        async with self._lock:
            idle: list[BrowserSession] = []
            for key, session in list(self._sessions.items()):
                if session.busy or session._pending_acquires:
                    session._close_requested = True
                else:
                    self._sessions.pop(key, None)
                    idle.append(session)
        for session in idle:
            await session.close()


_TASK_SPACE_SESSIONS = _BrowserSessionRegistry()


def normalize_task_space(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    raise ValueError("task_space must be a non-empty string or positive integer")


def normalize_task_space_ttl(value: object) -> float:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
        and math.isfinite(float(value))
    ):
        return float(value)
    raise ValueError("task_space_ttl_seconds must be a positive number")


async def _connect_skill_page(
    cdp_endpoint: str,
    *,
    task_space: str | None = None,
    task_space_ttl_seconds: float = _DEFAULT_TASK_SPACE_TTL_SECONDS,
) -> SkillPage:
    from playwright.async_api import async_playwright

    target_id = (
        await _bridge_task_space_target(
            task_space,
            cdp_endpoint,
            task_space_ttl_seconds,
        )
        if task_space is not None
        else None
    )
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        if target_id is None:
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            page = await _select_bridge_page(context, target_id)
    except Exception:
        await pw.stop()
        raise

    logger.info(
        "SkillPage: connected over CDP to %s%s",
        cdp_endpoint,
        f" (task_space={task_space})" if task_space else "",
    )
    return SkillPage(
        pw,
        browser,
        page,
        endpoint=cdp_endpoint,
        task_space=task_space,
        context=context,
    )


@asynccontextmanager
async def browser_session(
    cdp_endpoint: str,
    *,
    task_space: str | int | None = None,
    task_space_ttl_seconds: float = _DEFAULT_TASK_SPACE_TTL_SECONDS,
) -> AsyncIterator[SkillPage]:
    """Lease a direct or reusable Task Space browser session."""
    if task_space is None:
        page = await _connect_skill_page(cdp_endpoint)
    else:
        page = await open_skill_page(
            cdp_endpoint,
            task_space=task_space,
            task_space_ttl_seconds=task_space_ttl_seconds,
        )
    try:
        yield page
    finally:
        await page.aclose()

async def close_task_space(cdp_endpoint: str, task_space: str | int) -> bool:
    """Close an idle Task Space connection and return whether it existed."""
    return await _TASK_SPACE_SESSIONS.close(
        cdp_endpoint,
        normalize_task_space(task_space),
    )


async def close_all_task_spaces() -> None:
    """Close every cached Task Space connection during process shutdown."""
    await _TASK_SPACE_SESSIONS.close_all()


async def open_skill_page(
    cdp_endpoint: str,
    *,
    task_space: str | int | None = None,
    task_space_ttl_seconds: float = _DEFAULT_TASK_SPACE_TTL_SECONDS,
) -> SkillPage:
    """Connect to an existing Chrome page, optionally reusing a Task Space.

    ``task_space`` is a logical session name scoped to ``cdp_endpoint``. With
    no Task Space, this preserves the original one-shot connection behaviour.
    """
    if task_space is None:
        return await _connect_skill_page(cdp_endpoint)
    normalized_task_space = normalize_task_space(task_space)
    ttl_seconds = normalize_task_space_ttl(task_space_ttl_seconds)
    return await _TASK_SPACE_SESSIONS.open(
        cdp_endpoint,
        normalized_task_space,
        ttl_seconds=ttl_seconds,
    )
