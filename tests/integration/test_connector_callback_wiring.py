"""Verify the mounted provider boundary while fleet authentication is enabled."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import get_settings
from backend.database import Base, get_db
from backend.main import create_app
from backend.models.connector_reply import ConnectorInboundReceipt
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.security.identity import RequestIdentity, get_request_identity
from tests.unit.test_feishu_connector_runtime import _event, _request

FLEET_TOKEN = "connector-wiring-test-token"


@pytest.fixture
async def mounted_connector(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_auth_token", FLEET_TOKEN)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mounted-connectors.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id="connector-owner", subject="connector-owner", disabled=False))
        db.add(Workspace(id="connector-ws", slug="connector-ws", name="Test", active=True))
        db.add(WorkspaceMembership(
            workspace_id="connector-ws", user_id="connector-owner", role=WorkspaceRole.ADMIN,
        ))
        await db.commit()

    async def isolated_db():
        async with factory() as db:
            yield db
            await db.commit()

    app = create_app()
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(
        subject="connector-owner",
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        await engine.dispose()


async def test_mounted_signed_callback_commits_without_fleet_token(mounted_connector):
    client, factory = mounted_connector
    installation_path = "/api/v1/workspaces/connector-ws/connector-installations"
    schema = (await client.get("/openapi.json")).json()
    callback_template = "/api/v1/connectors/feishu/installations/{installation_public_id}/events"
    assert schema["paths"][callback_template]["post"]["security"] == []
    management_template = "/api/v1/workspaces/{workspace_id}/connector-installations"
    assert schema["paths"][management_template]["post"]["security"] == [{"BearerAuth": []}]
    assert (await client.get(installation_path)).status_code == 401
    created = await client.post(
        installation_path,
        headers={"Authorization": f"Bearer {FLEET_TOKEN}"},
        json={
            "name": "Isolated Feishu", "app_id": "cli", "tenant_key": "tenant",
            "app_secret": "test-secret", "encrypt_key": "encrypt-key",
            "verification_token": "verify",
        },
    )
    assert created.status_code == 201, created.text
    installation = created.json()["data"]["installation_public_id"]
    callback = f"/api/v1/connectors/feishu/installations/{installation}/events"
    body, headers = _request("encrypt-key", _event())
    assert (await client.post(callback, content=body, headers=headers)).status_code == 200
    assert (await client.post(callback, content=body, headers=headers)).status_code == 200
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 1
        receipt = await db.scalar(select(ConnectorInboundReceipt))
        assert receipt.safe_content_text == "hello"
        assert receipt.status == "rejected"
        assert receipt.stable_error_code == "principal_not_bound"

    invalid_body, invalid_headers = _request(
        "encrypt-key", _event(message_id="forged"), signature_valid=False,
    )
    invalid = await client.post(callback, content=invalid_body, headers=invalid_headers)
    assert invalid.status_code == 500
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 1

    challenge_body, challenge_headers = _request("encrypt-key", {
        "type": "url_verification", "token": "verify", "challenge": "local-challenge",
    })
    challenge = await client.post(callback, content=challenge_body, headers=challenge_headers)
    assert challenge.status_code == 200
    assert challenge.json()["challenge"] == "local-challenge"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 1


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/connectors/feishu/installations/test/events"),
    ("PUT", "/api/v1/connectors/feishu/installations/test/events"),
    ("POST", "/api/v1/connectors/feishu/installations/test/events/extra"),
    ("POST", "/api/v1/connectors/feishu/installations/test/events/"),
    ("POST", "/api/v1/connectors/feishu/installations/test%2Fextra/events"),
    ("POST", "/api/v1/connectors/feishu/installations//events"),
    ("POST", "/api/v1/connectors/other/installations/test/events"),
    ("POST", "/api/v1/workspaces/connector-ws/connector-installations"),
    ("POST", "/api/v1/workspaces/connector-ws/connector-installations/test/binding-challenges"),
])
async def test_callback_exemption_does_not_open_other_paths(mounted_connector, method, path):
    client, _ = mounted_connector
    assert (await client.request(method, path, json={})).status_code == 401
