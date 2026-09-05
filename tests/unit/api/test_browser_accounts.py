from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.api.v1.browser_accounts import require_admin
from backend.models.browser import BrowserInstance
from backend.models.browser_account import BrowserAccount
from backend.schemas.browser import BrowserInstanceConfigUpdate
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services.browser_account_service import account_read
from backend.services.browser_service import update_browser_instance


@pytest.fixture
def admin(client, monkeypatch):
    from backend import browser_pool
    from backend.api.v1 import browser_accounts
    from backend.browser_pool import LocalBrowserPool
    from backend.main import app

    pool = LocalBrowserPool([])
    monkeypatch.setattr(browser_accounts, "get_pool", lambda: pool)
    monkeypatch.setattr(browser_pool, "get_pool", lambda: pool)
    from backend.security import url_guard

    monkeypatch.setattr(url_guard, "resolve_hostname", lambda _: ["93.184.215.14"])
    app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(
        subject="admin", is_platform_admin=True
    )
    yield
    app.dependency_overrides.pop(get_request_identity, None)


async def instance(db, name="one", **values):
    from backend.browser_pool import get_pool

    item = BrowserInstance(endpoint=f"http://agent-{name}:19222", profile_name=name, **values)
    db.add(item)
    await db.commit()
    get_pool().add_endpoint(item.endpoint)
    return item


@pytest.mark.asyncio
async def test_account_permissions():
    with pytest.raises(HTTPException) as error:
        await require_admin(RequestIdentity(subject="member"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_all_account_routes_reject_non_admin(client):
    from backend.main import app

    app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(subject="member")
    for method, path, body in [
        ("GET", "", None),
        ("POST", "", {"platform": "douyin", "label": "主号"}),
        ("PATCH", "/unknown", {"label": "新名称"}),
        ("POST", "/unknown/confirmation", {"status": "confirmed"}),
        ("DELETE", "/unknown", None),
        ("POST", "/unknown/restore", None),
        ("GET", "/websites", None),
        ("POST", "/unknown/login", None),
        ("GET", "/unknown/frame", None),
        ("POST", "/unknown/input", {"kind": "text", "text": "private"}),
    ]:
        result = await client.request(method, "/api/v1/browser-accounts" + path, json=body)
        assert result.status_code == 403


@pytest.mark.asyncio
async def test_multiple_accounts_same_platform_are_persistent_and_isolated(
    client, db_session, admin
):
    first = await instance(db_session, "1")
    second = await instance(db_session, "2")
    for item in (first, second):
        response = await client.post(
            "/api/v1/browser-accounts",
            json={
                "platform": "douyin",
                "label": "品牌号",
                "browser_instance_id": item.id,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["status"] == "unconfirmed"
    duplicate = await client.post(
        "/api/v1/browser-accounts",
        json={
            "platform": "youtube",
            "label": "不能共享",
            "browser_instance_id": first.id,
        },
    )
    assert duplicate.status_code == 409
    result = await client.get("/api/v1/browser-accounts")
    accounts = result.json()["data"]["accounts"]
    assert len(accounts) == 2
    assert {item["profile_name"] for item in accounts} == {"1", "2"}
    assert "cookie" not in result.text and "password" not in result.text


@pytest.mark.asyncio
async def test_confirmation_rename_and_record_removal_preserve_browser(client, db_session, admin):
    browser = await instance(db_session, "2")
    response = await client.post(
        "/api/v1/browser-accounts",
        json={
            "platform": "youtube",
            "label": "频道一",
            "browser_instance_id": browser.id,
        },
    )
    account_id = response.json()["data"]["id"]
    path = f"/api/v1/browser-accounts/{account_id}"
    confirmed = await client.post(path + "/confirmation", json={"status": "confirmed"})
    assert confirmed.json()["data"]["status"] == "confirmed"
    assert confirmed.json()["data"]["confirmation_source"] == "operator"
    renamed = await client.patch(path, json={"label": "频道二"})
    assert renamed.status_code == 200
    row = await db_session.get(BrowserAccount, account_id)
    assert row.label == "频道二"
    expired = await client.post(path + "/confirmation", json={"status": "needs_login"})
    assert expired.json()["data"]["confirmed_at"] is None
    assert (
        await client.request("DELETE", path, json={"clear_login_data": False})
    ).status_code == 200
    assert await db_session.get(BrowserInstance, browser.id) is not None
    assert (await client.get("/api/v1/browser-accounts")).json()["data"]["accounts"] == []
    archived = (await client.get("/api/v1/browser-accounts")).json()["data"]
    assert archived["archived_accounts"] == []
    assert await db_session.get(BrowserAccount, account_id) is None
    assert browser.login_reserved is True
    with pytest.raises(HTTPException) as protected:
        await update_browser_instance(
            db_session, browser, BrowserInstanceConfigUpdate(profile_name="other")
        )
    assert protected.value.status_code == 409
    assert not any(item["id"] == browser.id for item in archived["available_instances"])
    assert (
        await client.post(path + "/confirmation", json={"status": "confirmed"})
    ).status_code == 404
    assert (await client.post(path + "/restore")).status_code == 404


@pytest.mark.asyncio
async def test_anonymous_and_missing_instance_rejected(client, db_session, admin):
    browser = await instance(db_session, "2", profile_kind="anonymous")
    for browser_id, expected in [(browser.id, 409), ("missing", 404)]:
        result = await client.post(
            "/api/v1/browser-accounts",
            json={
                "platform": "xiaohongshu",
                "label": "账号",
                "browser_instance_id": browser_id,
            },
        )
        assert result.status_code == expected


@pytest.mark.asyncio
async def test_bound_profile_cannot_be_reconfigured_or_removed(
    client, db_session, admin, monkeypatch
):
    import base64

    from backend.api.v1 import browsers

    browser = await instance(db_session, "2")
    await client.post(
        "/api/v1/browser-accounts",
        json={
            "platform": "douyin",
            "label": "主号",
            "browser_instance_id": browser.id,
        },
    )
    for patch in [{"profile_name": "replacement"}, {"profile_kind": "anonymous"}]:
        with pytest.raises(HTTPException) as error:
            await update_browser_instance(db_session, browser, BrowserInstanceConfigUpdate(**patch))
        assert error.value.status_code == 409
    docker = Mock()
    monkeypatch.setattr(browsers, "docker_client", docker)
    result = await client.delete("/api/v1/browsers/chrome-instances/2")
    assert result.status_code == 409
    docker.assert_not_called()
    # Account protection also applies to the generic pool-removal route.
    from backend import browser_pool

    pool = SimpleNamespace(endpoints=[browser.endpoint])
    monkeypatch.setattr(browser_pool, "get_pool", lambda: pool)
    encoded = base64.urlsafe_b64encode(browser.endpoint.encode()).decode().rstrip("=")
    result = await client.delete(f"/api/v1/browsers/instances/{encoded}")
    assert result.status_code == 409


def test_old_confirmation_and_profile_drift_never_appear_current():
    account = SimpleNamespace(
        id="account",
        platform="douyin",
        label="主号",
        browser_instance_id="browser",
        profile_name="one",
        status="confirmed",
        confirmed_at=datetime.now(UTC) - timedelta(days=2),
        created_at=datetime.now(UTC),
    )
    browser = SimpleNamespace(
        endpoint="http://remote:9222", profile_name="one", profile_kind="authenticated"
    )
    pool = SimpleNamespace(
        endpoints=[browser.endpoint], is_ready=lambda _: True, available_for=lambda _: True
    )
    result = account_read(account, browser, pool)
    assert result["status"] == "unconfirmed"
    assert result["novnc_port"] is None
    browser.profile_name = "replacement"
    result = account_read(account, browser, pool)
    assert result["status"] == "profile_changed"
    assert result["browser_state"] == "unavailable"


@pytest.mark.asyncio
async def test_node_registration_and_deletion_cannot_override_account(
    client, db_session, admin, monkeypatch
):
    from backend.api.v1 import nodes
    from backend.models.edge_node import EdgeNode

    browser = await instance(db_session, "2")
    result = await client.post(
        "/api/v1/browser-accounts",
        json={
            "platform": "douyin",
            "label": "主号",
            "browser_instance_id": browser.id,
        },
    )
    assert result.status_code == 201
    node = EdgeNode(url=browser.endpoint)
    db_session.add(node)
    await db_session.commit()
    remove = Mock()
    add = Mock()
    monkeypatch.setattr(nodes, "_pool_remove", remove)
    monkeypatch.setattr(nodes, "_pool_add", add)
    result = await client.delete(f"/api/v1/nodes/{node.id}")
    assert result.status_code == 409
    remove.assert_not_called()
    result = await client.post(
        "/api/v1/nodes/register",
        json={
            "agent_url": browser.endpoint,
            "profile_kind": "anonymous",
        },
    )
    assert result.status_code == 409
    add.assert_not_called()
    assert browser.profile_kind == "authenticated"


@pytest.mark.asyncio
async def test_account_creation_rejects_pool_fallback(client, db_session, admin, monkeypatch):
    from contextlib import asynccontextmanager

    from backend.browser_pool import get_pool

    browser = await instance(db_session, "2")

    @asynccontextmanager
    async def fallback(_endpoint):
        yield "http://unrelated:19222"

    monkeypatch.setattr(get_pool(), "acquire", fallback)
    result = await client.post(
        "/api/v1/browser-accounts",
        json={
            "platform": "douyin",
            "label": "主号",
            "browser_instance_id": browser.id,
        },
    )
    assert result.status_code == 409
    assert not (await db_session.scalars(select(BrowserAccount))).all()


def test_account_migration_preserves_existing_browsers(monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration = importlib.import_module(
        "backend.migrations.versions.acc20260905a_add_browser_accounts"
    )
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE browser_instances (id VARCHAR(36) PRIMARY KEY, "
                "profile_name TEXT, profile_kind TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO browser_instances VALUES ('existing', 'existing', 'authenticated')")
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert "browser_accounts" in inspect(connection).get_table_names()
        assert inspect(connection).get_unique_constraints("browser_accounts")[0][
            "column_names"
        ] == ["browser_instance_id"]
        from sqlalchemy.exc import IntegrityError

        insert = """INSERT INTO browser_accounts
            (id, created_at, updated_at, platform, label, browser_instance_id, profile_name, status)
            VALUES ('account', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                'douyin', 'main', :instance, 'existing', 'unconfirmed')"""
        with pytest.raises(IntegrityError):
            connection.execute(text(insert), {"instance": "missing"})
        connection.execute(text(insert), {"instance": "existing"})
        for statement in [
            "DELETE FROM browser_instances WHERE id = 'existing'",
            "UPDATE browser_instances SET profile_name = 'other' WHERE id = 'existing'",
            "UPDATE browser_instances SET profile_kind = 'anonymous' WHERE id = 'existing'",
        ]:
            with pytest.raises(IntegrityError):
                connection.execute(text(statement))
        migration.downgrade()
        assert connection.scalar(text("SELECT id FROM browser_instances")) == "existing"
    engine.dispose()


@pytest.mark.asyncio
async def test_new_account_provisions_unique_profile_and_platform_startup(
    client, db_session, admin, monkeypatch
):
    from backend.api.v1 import browser_containers

    profiles = []

    async def provision(**kwargs):
        profiles.append(kwargs["profile_name"])
        assert kwargs["count"] == 1
        assert "https://www.xiaohongshu.com/" in kwargs["startup_pages"]
        await instance(db_session, kwargs["profile_name"])

    monkeypatch.setattr(browser_containers, "add_chrome_instance", provision)
    for _ in range(2):
        result = await client.post(
            "/api/v1/browser-accounts", json={"platform": "xiaohongshu", "label": "小红书"}
        )
        assert result.status_code == 201, result.text
    assert len(set(profiles)) == 2
    assert len((await db_session.scalars(select(BrowserAccount))).all()) == 2


@pytest.mark.asyncio
async def test_custom_website_needs_no_name_or_environment(client, db_session, admin, monkeypatch):
    from backend.api.v1 import browser_containers

    async def provision(**kwargs):
        assert kwargs["startup_pages"] == '["https://example.org/login"]'
        await instance(db_session, kwargs["profile_name"])

    monkeypatch.setattr(browser_containers, "add_chrome_instance", provision)
    response = await client.post("/api/v1/browser-accounts", json={"site_url": "example.org/login"})
    assert response.status_code == 201, response.text
    account = response.json()["data"]
    assert account["platform"] == "example.org"
    assert account["label"].startswith("example.org账号")
    assert account["login_url"] == "https://example.org/login"
    for url in ["http://127.0.0.1/admin", "file:///etc/passwd", "https://user:secret@example.org/"]:
        rejected = await client.post("/api/v1/browser-accounts", json={"site_url": url})
        assert rejected.status_code == 422
        assert "secret" not in rejected.text


@pytest.mark.asyncio
async def test_login_frames_are_private_and_inputs_validated(
    client, db_session, admin, monkeypatch
):
    from backend.services import browser_login_display

    browser = await instance(db_session, "2")
    result = await client.post(
        "/api/v1/browser-accounts",
        json={"site_url": "https://example.org", "browser_instance_id": browser.id},
    )
    path = "/api/v1/browser-accounts/" + result.json()["data"]["id"]
    driver = AsyncMock(
        side_effect=lambda *_: {
            "image": "pixels",
            "width": 1280,
            "height": 800,
            "title": "Login",
            "origin": "https://example.org",
            "suggested_name": "",
            "target_id": "target-one",
        }
    )
    monkeypatch.setattr(browser_login_display, "display", driver)
    for method, suffix, body in [
        ("POST", "/login", {}),
        ("GET", "/frame", None),
        ("POST", "/input", {"kind": "text", "text": "secret"}),
    ]:
        frame = await client.request(method, path + suffix, json=body)
        assert frame.status_code == 200, frame.text
        assert frame.headers["cache-control"] == "no-store, private"
        assert "target_id" not in frame.json()["data"]
        assert "secret" not in frame.text
    row = await db_session.get(BrowserAccount, result.json()["data"]["id"])
    assert row.login_target_id == "target-one"
    assert row.status == "unconfirmed"
    invalid = await client.post(path + "/input", json={"kind": "evaluate", "text": "secret"})
    assert invalid.status_code == 422
    assert "secret" not in invalid.text
    assert driver.await_count == 3


@pytest.mark.asyncio
async def test_delete_cleanup_failure_keeps_reservation_and_can_retry(
    client, db_session, admin, monkeypatch
):
    from backend.services import browser_login_display
    from backend.browser_pool import get_pool

    browser = await instance(db_session, "2")
    result = await client.post(
        "/api/v1/browser-accounts", json={"platform": "douyin", "browser_instance_id": browser.id}
    )
    account_id = result.json()["data"]["id"]
    path = f"/api/v1/browser-accounts/{account_id}"
    cleaner = Mock(side_effect=RuntimeError("sensitive internal failure"))
    monkeypatch.setattr(browser_login_display, "clear_managed_profile", cleaner)
    from backend.api.v1 import browser_containers

    env_write = Mock()
    monkeypatch.setattr(browser_containers, "update_env_file", env_write)
    assert (await client.delete(path)).status_code == 422
    cleaner.assert_not_called()
    result = await client.request("DELETE", path, json={"clear_login_data": True})
    assert result.status_code == 503
    assert "sensitive" not in result.text
    assert (await db_session.get(BrowserAccount, account_id)).status == "deleting"
    assert browser.login_reserved
    assert (
        await client.post(path + "/confirmation", json={"status": "confirmed"})
    ).status_code == 409
    assert (
        await client.request("DELETE", path, json={"clear_login_data": False})
    ).status_code == 409
    cleaner.side_effect = None
    assert (
        await client.request("DELETE", path, json={"clear_login_data": True})
    ).status_code == 200
    assert await db_session.get(BrowserAccount, account_id) is None
    assert await db_session.get(BrowserInstance, browser.id) is None
    assert browser.endpoint not in get_pool().endpoints
    env_write.assert_called_once_with("AGENT_POOL_ENDPOINTS", "")


@pytest.mark.asyncio
async def test_active_operation_blocks_delete_before_cleanup(
    client, db_session, admin, monkeypatch
):
    from backend.services import browser_login_display

    browser = await instance(db_session, "2")
    account = BrowserAccount(
        platform="example.org",
        label="one",
        browser_instance_id=browser.id,
        profile_name=browser.profile_name,
        operation_token="active",
        operation_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    db_session.add(account)
    await db_session.commit()
    cleanup = Mock()
    monkeypatch.setattr(browser_login_display, "clear_managed_profile", cleanup)
    result = await client.request(
        "DELETE", f"/api/v1/browser-accounts/{account.id}", json={"clear_login_data": True}
    )
    assert result.status_code == 409
    cleanup.assert_not_called()
    assert await db_session.get(BrowserAccount, account.id) is not None


@pytest.mark.asyncio
async def test_creator_retries_endpoint_conflict_before_docker_work(
    client, db_session, admin, monkeypatch
):
    from backend.api.v1 import browser_containers
    from sqlalchemy.exc import IntegrityError

    calls = []

    async def provision(**kwargs):
        calls.append(kwargs["profile_name"])
        if len(calls) == 1:
            raise IntegrityError("endpoint_unique", None, Exception())
        await instance(db_session, kwargs["profile_name"])

    monkeypatch.setattr(browser_containers, "add_chrome_instance", provision)
    result = await client.post("/api/v1/browser-accounts", json={"site_url": "example.org/login"})
    assert result.status_code == 201
    assert len(calls) == 2 and calls[0] == calls[1]
