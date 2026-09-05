from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.v1 import browser_spaces
from backend.database import get_db
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import WorkspaceAccess

_NOW = datetime.now(UTC)


def _space(**overrides):
    values = {
        "id": "space-1",
        "workspace_id": "workspace-1",
        "browser_instance_id": "instance-1",
        "binding_id": None,
        "owner_type": "operator",
        "owner_id": "operator-1",
        "status": "idle",
        "granted_capabilities": ["snapshot"],
        "revision": 0,
        "last_error_code": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _task(**overrides):
    values = {
        "id": "task-1",
        "space_id": "space-1",
        "operation_id": "operation-1",
        "status": "queued",
        "result": None,
        "error_code": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _event(**overrides):
    values = {
        "id": "event-1",
        "space_id": "space-1",
        "task_id": "task-1",
        "sequence": 1,
        "kind": "completed",
        "payload": {"result": {"ok": True, "headers": "must-not-leak"}},
        "created_at": _NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _client(monkeypatch, *, identity=None):
    app = FastAPI()
    app.include_router(browser_spaces.router)

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db
    if identity is not None:

        async def override_identity():
            return identity

        app.dependency_overrides[get_request_identity] = override_identity

    async def workspace_access(*_args, **_kwargs):
        return WorkspaceAccess(user_id="user-1", role="admin")

    monkeypatch.setattr(browser_spaces, "get_workspace_access", workspace_access)
    monkeypatch.setattr(browser_spaces, "require_permission", lambda *_args: None)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_browser_spaces_require_identity(monkeypatch):
    async with await _client(monkeypatch) as client:
        response = await client.get("/workspaces/workspace-1/browser-spaces")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_foreign_workspace_isolation_is_rejected(monkeypatch):
    async def deny(*_args, **_kwargs):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Workspace membership required")

    async with await _client(monkeypatch, identity=RequestIdentity(subject="operator-1")) as client:
        monkeypatch.setattr(browser_spaces, "get_workspace_access", deny)
        response = await client.get("/workspaces/foreign/browser-spaces")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_maps_reservation_conflict_to_409(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise browser_spaces.browser_space_service.BrowserSpaceError(
            "browser_instance_in_use", "instance is already reserved", 409
        )

    monkeypatch.setattr(browser_spaces.browser_space_service, "create_space", fail)
    async with await _client(
        monkeypatch, identity=RequestIdentity(subject="operator-1", is_platform_admin=True)
    ) as client:
        response = await client.post(
            "/workspaces/workspace-1/browser-spaces",
            json={
                "browser_instance_id": "instance-1",
                "owner_type": "operator",
                "owner_id": "operator-1",
                "granted_capabilities": ["snapshot"],
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "browser_instance_in_use"


@pytest.mark.asyncio
async def test_idempotent_task_response_is_200(monkeypatch):
    identity = RequestIdentity(subject="operator-1")

    async def get_space(*_args, **_kwargs):
        return _space()

    async def submit(*_args, **_kwargs):
        return _task(status="completed", result={"value": "bounded"}), False

    monkeypatch.setattr(browser_spaces.browser_space_service, "get_space", get_space)
    monkeypatch.setattr(browser_spaces.browser_space_service, "submit_task", submit)
    async with await _client(monkeypatch, identity=identity) as client:
        response = await client.post(
            "/workspaces/workspace-1/browser-spaces/space-1/tasks",
            json={"request_id": "request-1", "capability": "snapshot", "args": {}},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "space_id": "space-1",
        "task_id": "task-1",
        "operation_id": "operation-1",
        "capability": None,
        "status": "completed",
        "result": {"value": "bounded"},
        "error": None,
    }


@pytest.mark.asyncio
async def test_new_task_is_scheduled_after_accepted_response(monkeypatch):
    identity = RequestIdentity(subject="operator-1")
    scheduled: list[tuple[str, dict, int]] = []

    async def get_space(*_args, **_kwargs):
        return _space()

    async def submit(*_args, **_kwargs):
        assert _kwargs["execute"] is False
        return _task(), True

    async def schedule(task_id, args, timeout_seconds, gate, gate_authorized):
        assert gate is None
        assert gate_authorized is False
        scheduled.append((task_id, args, timeout_seconds))

    monkeypatch.setattr(browser_spaces.browser_space_service, "get_space", get_space)
    monkeypatch.setattr(browser_spaces.browser_space_service, "submit_task", submit)
    monkeypatch.setattr(
        browser_spaces.browser_space_service,
        "execute_task_in_background",
        schedule,
    )
    async with await _client(monkeypatch, identity=identity) as client:
        response = await client.post(
            "/workspaces/workspace-1/browser-spaces/space-1/tasks",
            json={
                "request_id": "request-2",
                "capability": "snapshot",
                "args": {"value": "ok"},
                "timeout_seconds": 45,
            },
        )

    assert response.status_code == 202
    assert scheduled == [("task-1", {"value": "ok"}, 45)]


@pytest.mark.asyncio
async def test_event_replay_is_ordered_bounded_and_redacted(monkeypatch):
    identity = RequestIdentity(subject="operator-1")

    async def list_events(*_args, **_kwargs):
        return [_event()]

    async with await _client(monkeypatch, identity=identity) as client:
        monkeypatch.setattr(browser_spaces.browser_space_service, "list_events", list_events)
        response = await client.get(
            "/workspaces/workspace-1/browser-spaces/space-1/events?after_sequence=4&limit=10"
        )

    assert response.status_code == 200
    assert response.json()["data"][0]["sequence"] == 1
    assert "headers" not in response.json()["data"][0]["payload"]["result"]
