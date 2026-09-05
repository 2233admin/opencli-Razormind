from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend import browser_pool
from backend.browser_pool import LocalBrowserPool, NoReadyBrowserSlotError, RedisBrowserPool


@pytest.mark.asyncio
async def test_unrouted_tasks_never_use_account_profile(monkeypatch):
    pool = LocalBrowserPool(["account", "public"])
    monkeypatch.setattr(browser_pool, "_account_reservations", AsyncMock(return_value={"account"}))
    async with pool.acquire() as endpoint:
        assert endpoint == "public"
    with pytest.raises(NoReadyBrowserSlotError):
        async with pool.acquire("account"):
            pytest.fail("a task received a login Profile")


@pytest.mark.asyncio
async def test_waiting_task_rechecks_ownership_and_releases_slot(monkeypatch):
    pool = LocalBrowserPool(["account"])
    monkeypatch.setattr(
        browser_pool, "_account_reservations", AsyncMock(side_effect=[set(), {"account"}])
    )
    with pytest.raises(NoReadyBrowserSlotError):
        async with pool.acquire("account"):
            pytest.fail("ownership changed while acquiring")
    assert pool.available_for("account")


@pytest.mark.asyncio
async def test_redis_account_reservations_fail_before_leasing(monkeypatch):
    pool = RedisBrowserPool(["account"], "redis://unused")
    monkeypatch.setattr(browser_pool, "_account_reservations", AsyncMock(return_value={"account"}))
    for endpoint in (None, "account"):
        with pytest.raises(NoReadyBrowserSlotError):
            async with pool.acquire(endpoint):
                pytest.fail("reserved endpoint was leased")


@pytest.mark.asyncio
async def test_reservations_are_read_from_durable_database(db_session, monkeypatch):
    from backend import database
    from backend.models.browser import BrowserInstance
    from backend.models.browser_account import BrowserAccount

    instance = BrowserInstance(endpoint="account", profile_name="account")
    db_session.add(instance)
    await db_session.flush()
    account = BrowserAccount(
        browser_instance_id=instance.id, platform="douyin", label="one", profile_name="account"
    )
    db_session.add(account)
    await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(database, "AsyncSessionLocal", session)
    pool = LocalBrowserPool(["account"])
    pool.enforce_account_reservations = True
    assert await browser_pool._account_reservations(pool) == {"account"}
    account.status = "archived"
    await db_session.commit()
    assert await browser_pool._account_reservations(pool) == {"account"}
    await db_session.delete(account)
    await db_session.commit()
    assert await browser_pool._account_reservations(pool) == set()
    instance.login_reserved = True
    await db_session.commit()
    assert await browser_pool._account_reservations(pool) == {"account"}
