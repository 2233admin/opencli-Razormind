"""HTTP and executor contract tests using persisted Spaces and the real capability service."""

import asyncio
import json
import os

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import database
from backend.api.v1.browser_spaces import router
from backend.models.browser import (
    BrowserBinding,
    BrowserInstance,
    BrowserRuntimeBundle,
    BrowserRuntimeDeployment,
)
from backend.models.browser_space import BrowserSpace, BrowserSpaceTask
from backend.models.identity import User, Workspace, WorkspaceMembership
from backend.security.identity import RequestIdentity, get_request_identity, is_platform_admin
from backend.services import browser_capability_service as capabilities
from backend.services import browser_space_service as spaces
from tests.browser_space_fixtures import configure_space_runtime

_REAL_DISPATCH = capabilities._dispatch_capability


@pytest_asyncio.fixture
async def contract(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'spaces.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    async with factory() as db:
        workspace = Workspace(name="Contract", slug="contract")
        other = Workspace(name="Other", slug="other")
        owner = User(subject="owner")
        stranger = User(subject="stranger")
        instance = BrowserInstance(endpoint="http://slot:9222", profile_name="contract-slot")
        db.add_all([workspace, other, owner, stranger, instance])
        await db.flush()
        db.add_all(
            [
                WorkspaceMembership(workspace_id=workspace.id, user_id=owner.id, role="admin"),
                WorkspaceMembership(
                    workspace_id=workspace.id, user_id=stranger.id, role="operator"
                ),
            ]
        )
        await db.commit()
        await configure_space_runtime(db, instance)
        workspace_id, other_id, instance_id = workspace.id, other.id, instance.id

    app = FastAPI()
    app.include_router(router)
    identity = {"subject": "owner", "platform_admin": True}

    async def get_identity():
        return RequestIdentity(
            subject=identity["subject"], is_platform_admin=identity.get("platform_admin", False)
        )

    async def get_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_request_identity] = get_identity
    app.dependency_overrides[database.get_db] = get_db
    calls = []

    async def dispatch(instance, capability, args):
        calls.append((instance.id, capability.name, args))
        return {
            "title": "safe snapshot",
            "cookies": "private-cookie",
            "message": "Authorization: Bearer private-token",
            "location": "ws://slot:9222/private",
            "markup": "<html>private page</html>",
        }

    monkeypatch.setattr(capabilities, "_dispatch_capability", dispatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "factory": factory,
            "identity": identity,
            "calls": calls,
            "base": f"/workspaces/{workspace_id}/browser-spaces",
            "instance": instance_id,
            "workspace": workspace_id,
            "other": other_id,
        }
    await engine.dispose()


async def create(contract, **overrides):
    response = await contract["client"].post(
        contract["base"],
        json={
            "browser_instance_id": contract["instance"],
            "owner_type": "operator",
            "owner_id": "owner",
            "granted_capabilities": ["snapshot"],
            **overrides,
        },
    )
    return response


@pytest.mark.asyncio
async def test_http_lifecycle_real_capability_dispatch_and_terminal_replay(contract):
    client, base = contract["client"], contract["base"]
    available = await client.get(f"{base}/instances")
    assert available.json()["data"] == [{"id": contract["instance"], "capabilities": ["snapshot"]}]
    created = await create(contract)
    assert created.status_code == 201
    path = f"{base}/{created.json()['data']['id']}"
    assert (await client.get(f"{base}/instances")).json()["data"] == []
    body = {"request_id": "snapshot-once", "capability": "snapshot", "args": {}}
    submitted = await client.post(f"{path}/tasks", json=body)
    assert submitted.status_code == 202
    assert submitted.json()["data"]["status"] == "queued"
    detail = (await client.get(path)).json()["data"]
    assert detail["active_task"] is None
    assert detail["latest_task"]["status"] == "completed"
    assert detail["latest_task"]["result"]["title"] == "safe snapshot"
    repeated = await client.post(f"{path}/tasks", json=body)
    assert repeated.status_code == 200
    assert repeated.json()["data"]["task_id"] == detail["latest_task"]["task_id"]
    assert len(contract["calls"]) == 1
    cancelled = await client.post(f"{path}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "completed"
    events = (await client.get(f"{path}/events")).json()["data"]
    assert [event["kind"] for event in events] == ["queued", "started", "completed"]
    page = (await client.get(f"{path}/events?after_sequence=1&limit=1")).json()["data"]
    assert [event["sequence"] for event in page] == [2]
    assert (await client.post(f"{path}/close")).json()["data"]["status"] == "closed"
    assert (
        await client.post(f"{path}/tasks", json={**body, "request_id": "after-close"})
    ).status_code == 409
    assert (await create(contract)).status_code == 201
    encoded = json.dumps([detail, events])
    for secret in ["private-cookie", "private-token", "slot:9222", "private page"]:
        assert secret not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [
        ({"capability": "other"}, "capability_not_granted"),
        ({"args": {"value": 42}}, "invalid_capability_args"),
        ({"args": {"javascript": "alert(1)"}}, "invalid_capability_args"),
        ({"timeout_seconds": 601}, None),
        ({"args": {"value": "x" * 65537}}, None),
    ],
)
async def test_invalid_tasks_never_persist_or_dispatch(contract, body, code):
    space_id = (await create(contract)).json()["data"]["id"]
    response = await contract["client"].post(
        f"{contract['base']}/{space_id}/tasks",
        json={
            "request_id": "invalid",
            "capability": "snapshot",
            "args": {},
            **body,
        },
    )
    assert response.status_code == 422
    if code:
        assert response.json()["detail"] == code
    async with contract["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(BrowserSpaceTask)) == 0
    assert contract["calls"] == []


@pytest.mark.asyncio
async def test_unknown_granted_capability_is_rejected(contract):
    created = await create(contract, granted_capabilities=["unknown"])
    space_id = created.json()["data"]["id"]
    response = await contract["client"].post(
        f"{contract['base']}/{space_id}/tasks",
        json={
            "request_id": "unknown",
            "capability": "unknown",
            "args": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "unknown_capability"
    assert contract["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,components,code",
    [
        ("DEGRADED", [{"id": "page", "healthy": True}], "slot_not_ready"),
        ("READY", [], "capability_component_unavailable"),
    ],
)
async def test_runtime_readiness_errors_persist_failed_task(contract, state, components, code):
    space_id = (await create(contract)).json()["data"]["id"]
    async with contract["factory"]() as db:
        deployment = await db.scalar(select(BrowserRuntimeDeployment))
        deployment.state, deployment.loaded_components = state, components
        await db.commit()
    path = f"{contract['base']}/{space_id}"
    response = await contract["client"].post(
        f"{path}/tasks",
        json={
            "request_id": "not-ready",
            "capability": "snapshot",
            "args": {},
        },
    )
    assert response.status_code == 202
    detail = (await contract["client"].get(path)).json()["data"]
    assert detail["latest_task"]["status"] == "failed"
    assert detail["latest_task"]["error"] == code
    assert detail["status"] == "error"
    assert contract["calls"] == []


@pytest.mark.asyncio
async def test_foreign_owner_and_workspace_cannot_access_lifecycle(contract):
    space_id = (await create(contract)).json()["data"]["id"]
    contract["identity"]["subject"] = "stranger"
    client, base = contract["client"], contract["base"]
    assert (await client.get(base)).json()["data"] == []
    for method, suffix in [
        ("GET", ""),
        ("GET", "/events"),
        ("POST", "/cancel"),
        ("POST", "/close"),
    ]:
        assert (await client.request(method, f"{base}/{space_id}{suffix}")).status_code == 403
    assert (
        await client.post(
            f"{base}/{space_id}/tasks",
            json={
                "request_id": "foreign",
                "capability": "snapshot",
                "args": {},
            },
        )
    ).status_code == 403
    assert (await create(contract)).status_code == 403
    assert (await client.get(f"/workspaces/{contract['other']}/browser-spaces")).status_code == 403
    assert (await client.get(f"{base}/instances")).status_code == 403


@pytest.mark.asyncio
async def test_concurrent_reservations_have_one_winner(contract):
    results = await asyncio.gather(create(contract), create(contract))
    assert sorted(response.status_code for response in results) == [201, 409]
    assert (
        next(r for r in results if r.status_code == 409).json()["detail"]
        == "browser_instance_in_use"
    )
    async with contract["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(BrowserSpace)) == 1


@pytest.mark.asyncio
async def test_unmanaged_slot_and_mismatched_binding_create_no_space(contract):
    async with contract["factory"]() as db:
        instance = BrowserInstance(endpoint="http://personal:9222", profile_name="personal")
        binding = BrowserBinding(browser_endpoint="http://foreign:9222", site="foreign")
        db.add_all([instance, binding])
        await db.commit()
        instance_id, binding_id = instance.id, binding.id
    assert (await create(contract, browser_instance_id=instance_id)).status_code == 409
    assert (await create(contract, binding_id=binding_id)).status_code == 404
    assert (await create(contract, browser_instance_id="unknown")).status_code == 404
    async with contract["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(BrowserSpace)) == 0


@pytest.mark.asyncio
async def test_runtime_dispatch_holds_no_database_transaction(contract, monkeypatch):
    seen = []
    original = capabilities.invoke_capability

    async def invoke(session, *args, **kwargs):
        async def dispatch(*_):
            seen.append(session.in_transaction())
            return {"ok": True}

        monkeypatch.setattr(capabilities, "_dispatch_capability", dispatch)
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(capabilities, "invoke_capability", invoke)
    space_id = (await create(contract)).json()["data"]["id"]
    await contract["client"].post(
        f"{contract['base']}/{space_id}/tasks",
        json={
            "request_id": "transaction",
            "capability": "snapshot",
            "args": {},
        },
    )
    assert seen == [False]


@pytest.mark.asyncio
async def test_repeated_cancel_is_idempotent_before_and_after_cleanup(contract):
    space_id = (await create(contract)).json()["data"]["id"]
    async with contract["factory"]() as db:
        task, _ = await spaces.submit_task(
            db,
            contract["workspace"],
            space_id,
            {
                "request_id": "cancel",
                "capability": "snapshot",
                "args": {},
            },
            execute=False,
        )
        first = await spaces.cancel_task(db, contract["workspace"], space_id)
        second = await spaces.cancel_task(db, contract["workspace"], space_id)
        assert first.id == second.id
        assert second.status == "queued"
        await spaces.execute_task(db, task, {})
        terminal = await spaces.cancel_task(db, contract["workspace"], space_id)
        assert terminal.status == "cancelled"
        events = await spaces.list_events(db, contract["workspace"], space_id)
        assert [event.kind for event in events] == ["queued", "cancel_requested", "cancelled"]
    assert contract["calls"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "x" * 65537},
        {"items": ["x" * 1000] * 100},
    ],
)
def test_oversize_payload_replaced_as_a_whole(payload):
    assert spaces._safe_result(payload) == {"truncated": True, "reason": "result_too_large"}


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("BROWSER_SPACE_TEST_CHROMIUM"),
    reason="set BROWSER_SPACE_TEST_CHROMIUM to a disposable Chromium executable",
)
@pytest.mark.asyncio
async def test_real_browser_create_snapshot_close(contract, monkeypatch):
    """A fresh Chromium process, production Space API and capability adapter.

    The test Agent exposes only the named snapshot action over an in-process
    HTTP transport; it never uses an operator browser or publishes CDP.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.environ.get("BROWSER_SPACE_TEST_CHROMIUM"),
            headless=True,
        )
        try:
            page = await browser.new_page()
            await page.set_content("<title>Space snapshot proof</title><h1>Dedicated browser</h1>")
            agent = FastAPI()

            @agent.post("/runtime/invoke")
            async def snapshot(body: dict):
                assert body["workflow"] == "snapshot"
                assert body["input"] == {}
                return {
                    "title": await page.title(),
                    "heading": await page.locator("h1").inner_text(),
                }

            original_client = AsyncClient
            monkeypatch.setattr(
                capabilities.httpx,
                "AsyncClient",
                lambda **kwargs: original_client(transport=ASGITransport(app=agent), **kwargs),
            )
            monkeypatch.setattr(capabilities, "_dispatch_capability", _REAL_DISPATCH)
            created = await create(contract)
            assert created.status_code == 201
            path = f"{contract['base']}/{created.json()['data']['id']}"
            response = await contract["client"].post(
                f"{path}/tasks",
                json={
                    "request_id": "real-browser",
                    "capability": "snapshot",
                    "args": {},
                },
            )
            assert response.status_code == 202
            detail = (await contract["client"].get(path)).json()["data"]
            assert detail["latest_task"]["status"] == "completed"
            assert detail["latest_task"]["result"] == {
                "title": "Space snapshot proof",
                "heading": "Dedicated browser",
            }
            assert (await contract["client"].post(f"{path}/close")).json()["data"][
                "status"
            ] == "closed"
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [False, True])
async def test_gate_authorization_reuses_existing_platform_identity(contract, authorized):
    async with contract["factory"]() as db:
        bundle = await db.scalar(select(BrowserRuntimeBundle))
        manifest = json.loads(json.dumps(bundle.manifest))
        manifest["capabilities"][0]["required_gate"] = "operator-confirmed"
        bundle.manifest = manifest
        await db.commit()
    space_id = (await create(contract)).json()["data"]["id"]
    contract["identity"]["platform_admin"] = authorized
    path = f"{contract['base']}/{space_id}"
    response = await contract["client"].post(
        f"{path}/tasks",
        json={
            "request_id": "gate",
            "capability": "snapshot",
            "args": {},
            "gate": "operator-confirmed",
        },
    )
    assert response.status_code == 202
    detail = (await contract["client"].get(path)).json()["data"]
    assert detail["latest_task"]["status"] == ("completed" if authorized else "failed")
    assert detail["latest_task"]["error"] == (None if authorized else "gate_not_satisfied")
    assert len(contract["calls"]) == int(authorized)


@pytest.mark.asyncio
async def test_rejected_request_never_echoes_secret_input(contract):
    space_id = (await create(contract)).json()["data"]["id"]
    response = await contract["client"].post(
        f"{contract['base']}/{space_id}/tasks",
        json={
            "request_id": "bad",
            "capability": "snapshot",
            "args": "Bearer private-token",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


@pytest.mark.asyncio
async def test_concurrent_idempotent_submissions_commit_one_task(contract):
    space_id = (await create(contract)).json()["data"]["id"]

    async def submit():
        async with contract["factory"]() as db:
            return await spaces.submit_task(
                db,
                contract["workspace"],
                space_id,
                {
                    "request_id": "same-request",
                    "capability": "snapshot",
                    "args": {},
                },
                execute=False,
            )

    first, second = await asyncio.gather(submit(), submit())
    assert first[0].id == second[0].id
    assert sorted([first[1], second[1]]) == [False, True]
    async with contract["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(BrowserSpaceTask)) == 1


@pytest.mark.asyncio
async def test_workspace_admin_cannot_allocate_global_slots_without_platform_permission(contract):
    contract["identity"]["platform_admin"] = False
    assert (await contract["client"].get(f"{contract['base']}/instances")).status_code == 403
    response = await create(contract)
    assert response.status_code == 403
    assert response.json()["detail"] == "platform_admin_required"


@pytest.mark.parametrize("roles", ["platform-admin", {"platform-admin": False}, None, 123])
def test_malformed_role_claims_never_authorize_gate_or_allocation(roles):
    assert not is_platform_admin(RequestIdentity(subject="owner", claims={"roles": roles}))


@pytest.mark.parametrize(
    "text",
    [
        "Cookie: session=private; other=private",
        "profile_path: C:/Users/private/profile",
        "ws://private/runtime",
        "<html>private</html>",
        "Authorization: Bearer private",
    ],
)
def test_sensitive_strings_and_keys_are_redacted(text):
    encoded = json.dumps(spaces._safe_result({"message": text, text: "private"}))
    assert "private" not in encoded


def test_non_finite_runtime_output_is_safe_json():
    assert spaces._safe_result({"number": float("nan")}) == {"number": None}


@pytest.mark.asyncio
async def test_remote_cancel_keeps_lease_until_original_runtime_returns(contract, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    async def dispatch(*_):
        started.set()
        await finish.wait()
        return {"ok": True}

    monkeypatch.setattr(capabilities, "_dispatch_capability", dispatch)
    space_id = (await create(contract)).json()["data"]["id"]
    async with contract["factory"]() as db:
        task, _ = await spaces.submit_task(
            db,
            contract["workspace"],
            space_id,
            {
                "request_id": "remote-cancel",
                "capability": "snapshot",
                "args": {},
            },
            execute=False,
        )
        running = asyncio.create_task(spaces.execute_task(db, task, {}))
        try:
            await asyncio.wait_for(started.wait(), 5)
            path = f"{contract['base']}/{space_id}"
            for _ in range(2):
                response = await contract["client"].post(f"{path}/cancel")
                assert response.json()["data"]["status"] == "running"
            blocked = await contract["client"].post(
                f"{path}/tasks",
                json={
                    "request_id": "too-soon",
                    "capability": "snapshot",
                    "args": {},
                },
            )
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == "space_task_in_progress"
            assert (await contract["client"].post(f"{path}/close")).status_code == 409
        finally:
            finish.set()
            terminal = await running
        assert terminal.status == "cancelled"
        assert (await contract["client"].post(f"{path}/close")).status_code == 200


@pytest.mark.asyncio
async def test_transport_timeout_never_releases_unacknowledged_remote_work(contract, monkeypatch):
    finish = asyncio.Event()
    remote_work = None

    async def dispatch(*_):
        nonlocal remote_work
        remote_work = asyncio.create_task(finish.wait())
        # A remote HTTP server keeps working even when its client disconnects.
        await asyncio.shield(remote_work)
        return {"ok": True}

    monkeypatch.setattr(capabilities, "_dispatch_capability", dispatch)
    space_id = (await create(contract)).json()["data"]["id"]
    try:
        async with contract["factory"]() as db:
            task, _ = await spaces.submit_task(
                db,
                contract["workspace"],
                space_id,
                {
                    "request_id": "remote-timeout",
                    "capability": "snapshot",
                    "args": {},
                },
                execute=False,
            )
            terminal = await spaces.execute_task(db, task, {}, timeout_seconds=0.1)
            assert terminal.status == "failed"
            assert terminal.error_code == "timeout"
        assert remote_work is not None and not remote_work.done()
        path = f"{contract['base']}/{space_id}"
        for suffix, body in [
            ("close", None),
            (
                "tasks",
                {
                    "request_id": "too-soon",
                    "capability": "snapshot",
                    "args": {},
                },
            ),
        ]:
            response = await contract["client"].post(f"{path}/{suffix}", json=body)
            assert response.status_code == 409
            assert response.json()["detail"] == "runtime_cleanup_unconfirmed"
        assert (await contract["client"].get(f"{contract['base']}/instances")).json()["data"] == []
    finally:
        finish.set()
        if remote_work:
            await remote_work
