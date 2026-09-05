"""Live proof: cookie/login state survives across separate ``connect_over_cdp``
attach -> detach -> reattach cycles against the **same** already-running browser.

Why this exists: the whole skill/opencli execute story relies on attaching to an
already-running, human-logged-in Chrome/Edge (``backend.skills.page``,
``backend.browser_pool``) rather than launching a fresh disposable browser context,
specifically so a skill run can reuse a real login session (cookies) instead of
needing its own credential handling. That's an architectural claim; this test
proves it empirically against a real browser rather than trusting the reasoning.

Gated behind the existing ``live`` marker — same convention as
``test_execute_loop_live.py`` (see ``TESTING.md``). Reads
``SKILL_LIVE_CDP_ENDPOINT`` and skips with an actionable message if unset.
"""

import os

import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.live

_COOKIE_NAME = "opencli_live_persistence_probe"


@pytest.fixture(scope="module")
def cdp_endpoint():
    ep = os.environ.get("SKILL_LIVE_CDP_ENDPOINT") or os.environ.get("OPENCLI_CDP_ENDPOINT")
    if not ep:
        pytest.skip(
            "set SKILL_LIVE_CDP_ENDPOINT to a running Chrome/Edge --remote-debugging-port "
            "endpoint (e.g. http://127.0.0.1:9222) to run the live cookie-persistence proof"
        )
    return ep


async def test_cookie_survives_detach_and_reattach(cdp_endpoint):
    """Set a cookie, detach (not close) the real browser, reattach fresh, read it back.

    ``browser.close()`` on a CDP-*attached* browser detaches the Playwright client
    without killing the underlying browser process (same behavior
    ``test_execute_loop_live.py::_read_dom_flag`` relies on) — this is exactly what
    happens between two separate task runs against the same ``browser_pool`` endpoint
    in production, so this is the realistic reuse pattern, not a synthetic one.
    """
    probe_value = "still-here"

    # ── attach #1: set the cookie, then detach ──────────────────────────────────
    pw1 = await async_playwright().start()
    browser1 = await pw1.chromium.connect_over_cdp(cdp_endpoint)
    try:
        context1 = browser1.contexts[0] if browser1.contexts else await browser1.new_context()
        await context1.add_cookies(
            [
                {
                    "name": _COOKIE_NAME,
                    "value": probe_value,
                    "domain": "127.0.0.1",
                    "path": "/",
                }
            ]
        )
        cookies_immediately_after = await context1.cookies("http://127.0.0.1/")
        assert any(
            c["name"] == _COOKIE_NAME and c["value"] == probe_value
            for c in cookies_immediately_after
        )
    finally:
        await browser1.close()  # detach only — real browser keeps running
        await pw1.stop()

    # ── attach #2: brand-new Playwright client + connection, same browser ──────
    pw2 = await async_playwright().start()
    browser2 = await pw2.chromium.connect_over_cdp(cdp_endpoint)
    try:
        context2 = browser2.contexts[0] if browser2.contexts else await browser2.new_context()
        cookies_after_reattach = await context2.cookies("http://127.0.0.1/")
        match = next((c for c in cookies_after_reattach if c["name"] == _COOKIE_NAME), None)
        assert match is not None, (
            "cookie set on attach #1 is gone on a fresh connect_over_cdp — session "
            "state does NOT survive detach/reattach against this browser"
        )
        assert match["value"] == probe_value
    finally:
        await browser2.close()
        await pw2.stop()
