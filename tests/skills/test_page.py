"""Browser-free tests for CDP page and Task Space session lifecycle."""

import asyncio

import pytest
import pytest_asyncio

import backend.skills.page as page_module
from backend.skills.page import (
    SkillPage,
    browser_session,
    close_all_task_spaces,
    close_task_space,
    normalize_task_space,
    normalize_task_space_ttl,
    open_skill_page,
)


class _FakePage:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


class _FakeDriver:
    def __init__(self) -> None:
        self.close_count = 0
        self.stop_count = 0
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.close_count += 1
        self.connected = False

    async def stop(self) -> None:
        self.stop_count += 1


@pytest_asyncio.fixture(autouse=True)
async def clean_task_spaces():
    await close_all_task_spaces()
    yield
    await close_all_task_spaces()


@pytest.fixture
def fake_connector(monkeypatch):
    drivers: list[_FakeDriver] = []

    async def connect(
        endpoint: str,
        *,
        task_space: str | None = None,
        task_space_ttl_seconds: float = 600,
    ) -> SkillPage:
        driver = _FakeDriver()
        drivers.append(driver)
        return SkillPage(
            driver,
            driver,
            _FakePage(),
            endpoint=endpoint,
            task_space=task_space,
        )

    monkeypatch.setattr(page_module, "_connect_skill_page", connect)
    return drivers
async def test_task_space_reuses_connection_until_explicit_close(fake_connector):
    first = await open_skill_page("cdp://one", task_space="research")
    await first.aclose()

    second = await open_skill_page("cdp://one", task_space="research")

    assert second is not first
    assert second.page is first.page
    assert second.endpoint == "cdp://one"
    assert second.task_space == "research"
    assert len(fake_connector) == 1
    assert fake_connector[0].close_count == 0

    await second.aclose()
    assert await close_task_space("cdp://one", "research") is True
    assert fake_connector[0].close_count == 1
    assert fake_connector[0].stop_count == 1
    assert await close_task_space("cdp://one", "research") is False


async def test_stale_task_space_lease_cannot_drive_or_release_new_lease(fake_connector):
    first = await open_skill_page("cdp://one", task_space="isolated")
    await first.aclose()
    second = await open_skill_page("cdp://one", task_space="isolated")

    with pytest.raises(RuntimeError, match="lease is closed"):
        await first.inner_text()
    await first.aclose()
    await second.aclose()


async def test_task_space_serializes_concurrent_leases(fake_connector):
    first = await open_skill_page("cdp://one", task_space="shared")
    pending = asyncio.create_task(open_skill_page("cdp://one", task_space="shared"))
    await asyncio.sleep(0)

    assert not pending.done()
    await first.aclose()
    second = await pending

    assert second.page is first.page
    assert second is not first
    assert len(fake_connector) == 1
    await second.aclose()


async def test_task_space_reconnects_after_browser_or_tab_loss(fake_connector):
    first = await open_skill_page("cdp://one", task_space="recover")
    await first.aclose()
    fake_connector[0].connected = False
    first.page.closed = True

    second = await open_skill_page("cdp://one", task_space="recover")

    assert second is not first
    assert len(fake_connector) == 2
    assert fake_connector[0].close_count == 1
    await second.aclose()


async def test_bridge_task_space_uses_active_page_identity(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def run_bridge(*args: str, **_kwargs):
        calls.append(args)
        if args[-2:] == ("tab", "list"):
            return []
        return {"page": "target-after-restart"}

    monkeypatch.setattr(page_module, "_run_bridge_command", run_bridge)

    assert await page_module._bridge_task_space_target(
        "research",
        "http://cdp",
        600,
    ) == "target-after-restart"
    assert calls == [
        ("browser", "research", "tab", "list"),
        ("browser", "research", "tab", "new"),
    ]
def test_bridge_binary_requires_dedicated_configuration(monkeypatch):
    monkeypatch.delenv("OPENCLI_BRIDGE_BIN", raising=False)
    monkeypatch.setattr(page_module.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(page_module.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="OPENCLI_BRIDGE_BIN"):
        page_module._resolve_bridge_bin()


def test_bridge_binary_prefers_dedicated_configuration(monkeypatch):
    configured = r"C:\opencli-bridge\opencli.cmd"
    monkeypatch.setenv("OPENCLI_BRIDGE_BIN", configured)
    monkeypatch.setenv("OPENCLI_BIN", r"C:\opencli-proof\opencli.cmd")
    monkeypatch.setattr(page_module.shutil, "which", lambda _name: None)

    assert page_module._resolve_bridge_bin() == configured
def test_bridge_binary_uses_installed_opencli_when_unconfigured(monkeypatch):
    monkeypatch.delenv("OPENCLI_BRIDGE_BIN", raising=False)
    monkeypatch.setattr(page_module.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(
        page_module.shutil,
        "which",
        lambda name: r"C:\npm\opencli.cmd" if name == "opencli" else None,
    )

    assert page_module._resolve_bridge_bin() == r"C:\npm\opencli.cmd"



async def test_task_space_heartbeat_refreshes_bridge_lease(monkeypatch, fake_connector):
    calls: list[tuple[str, ...]] = []

    async def run_bridge(*args: str, **_kwargs):
        calls.append(args)
        return [{"page": "target", "active": True}]

    monkeypatch.setattr(page_module, "_run_bridge_command", run_bridge)
    page = await open_skill_page(
        "cdp://one",
        task_space="heartbeat",
        task_space_ttl_seconds=0.3,
    )
    await asyncio.sleep(0.3)

    assert ("browser", "heartbeat", "tab", "list") in calls
    await page.aclose()





class _FakeCdpSession:
    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    async def send(self, method: str):
        assert method == "Target.getTargetInfo"
        return {"targetInfo": {"targetId": self.target_id}}

    async def detach(self) -> None:
        pass


class _TargetContext:
    def __init__(self, pages: list[object]) -> None:
        self.pages = pages

    async def new_cdp_session(self, page: object) -> _FakeCdpSession:
        return _FakeCdpSession(page.target_id)


class _TargetPage:
    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    def is_closed(self) -> bool:
        return False


async def test_select_bridge_page_does_not_guess_from_page_order():
    first = _TargetPage("user-tab")
    automation = _TargetPage("automation-tab")
    context = _TargetContext([first, automation])
    selected = await page_module._select_bridge_page(context, "automation-tab")

    assert selected is automation


async def test_expired_idle_task_space_is_replaced(fake_connector):
    first = await open_skill_page(
        "cdp://one", task_space="short", task_space_ttl_seconds=1
    )
    await first.aclose()
    session = page_module._TASK_SPACE_SESSIONS._sessions[("cdp://one", "short")]
    session._last_used -= 2

    second = await open_skill_page(
        "cdp://one", task_space="short", task_space_ttl_seconds=1
    )

    assert second is not first
    assert len(fake_connector) == 2
    assert fake_connector[0].close_count == 1
    await second.aclose()


async def test_idle_task_space_closes_after_ttl_without_reopen(fake_connector):
    page = await open_skill_page(
        "cdp://one", task_space="expiring", task_space_ttl_seconds=0.01
    )
    await page.aclose()

    await asyncio.sleep(0.05)

    assert fake_connector[0].close_count == 1
    assert fake_connector[0].stop_count == 1


async def test_close_all_defers_busy_task_space_until_lease_release(fake_connector):
    page = await open_skill_page("cdp://one", task_space="busy")

    await close_all_task_spaces()

    assert fake_connector[0].close_count == 0
    await page.aclose()
    assert fake_connector[0].close_count == 1


async def test_pending_task_space_lease_blocks_explicit_close(fake_connector):
    first = await open_skill_page("cdp://one", task_space="queued")
    pending = asyncio.create_task(open_skill_page("cdp://one", task_space="queued"))
    await asyncio.sleep(0)

    assert await close_task_space("cdp://one", "queued") is False
    await asyncio.wait_for(first.aclose(), timeout=1)
    second = await asyncio.wait_for(pending, timeout=1)
    assert second is not first
    await second.aclose()


async def test_browser_session_context_releases_task_space(fake_connector):
    async with browser_session("cdp://one", task_space=7) as page:
        assert page.task_space == "7"
        assert fake_connector[0].close_count == 0

    assert fake_connector[0].close_count == 0
    assert await close_task_space("cdp://one", 7) is True
    assert fake_connector[0].close_count == 1


@pytest.mark.parametrize("value", [None, "", "  ", False, 0, -1, [], object()])
def test_normalize_task_space_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="task_space"):
        normalize_task_space(value)

@pytest.mark.parametrize("value", [None, True, 0, -1, "10", float("inf"), object()])
def test_normalize_task_space_ttl_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="task_space_ttl_seconds"):
        normalize_task_space_ttl(value)


def test_normalize_task_space_accepts_strings_and_positive_integers():
    assert normalize_task_space("  work  ") == "work"
    assert normalize_task_space(3) == "3"
    assert normalize_task_space_ttl(3) == 3.0
