from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.v1.connector_replies import router
from backend.database import Base, get_db
from backend.models.connector_reply import ConnectorInstallation, ConnectorPrincipalBinding
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.schemas.connector_reply import ConnectorInstallationCreate, ConnectorInstallationUpdate
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import connector_installation_service as service


@pytest.fixture
async def database(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _member(factory, *, subject="owner", role=WorkspaceRole.ADMIN, workspace_id="ws-1"):
    async with factory() as db:
        user = User(id=f"user-{subject}", subject=subject, disabled=False)
        workspace = await db.get(Workspace, workspace_id)
        if workspace is None:
            workspace = Workspace(
                id=workspace_id, name=workspace_id, slug=workspace_id, active=True
            )
            db.add(workspace)
        db.add(user)
        db.add(WorkspaceMembership(workspace_id=workspace_id, user_id=user.id, role=role))
        await db.commit()
    return RequestIdentity(subject=subject), user.id


def _create(name="Feishu"):
    return ConnectorInstallationCreate(
        name=name,
        app_id=f"cli-{name}",
        tenant_key=f"tenant-{name}",
        app_secret="app-secret",
        encrypt_key="encrypt-key",
        verification_token="verify-token",
    )


@pytest.mark.asyncio
async def test_installation_crud_health_is_workspace_scoped_and_secrets_are_write_only(database):
    identity, _ = await _member(database)
    foreign, _ = await _member(database, subject="foreign", workspace_id="ws-2")
    async with database() as db:
        created = await service.create_installation(db, "ws-1", identity, _create())
        await db.commit()
        assert created.has_app_secret and created.has_encrypt_key and created.has_verification_token
        assert "secret" not in created.model_dump()
        public_id = created.installation_public_id
    async with database() as db:
        health = await service.installation_health(db, "ws-1", public_id, identity)
        assert health.callback_ready is True
        assert health.reply_execution_ready is False
        updated = await service.update_installation(
            db, "ws-1", public_id, identity, ConnectorInstallationUpdate(enabled=False)
        )
        await db.commit()
        assert updated.status == "disabled" and updated.config_revision == 2
    async with database() as db:
        with pytest.raises(HTTPException) as error:
            await service.installation_health(db, "ws-2", public_id, foreign)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_health_is_blocked_when_encrypted_credentials_are_unreadable(database):
    identity, _ = await _member(database)
    async with database() as db:
        created = await service.create_installation(db, "ws-1", identity, _create())
        row = await db.scalar(
            select(ConnectorInstallation).where(
                ConnectorInstallation.public_id == created.installation_public_id
            )
        )
        row._encrypt_key_encrypted = "corrupt"
        await db.commit()
    async with database() as db:
        health = await service.installation_health(
            db, "ws-1", created.installation_public_id, identity
        )
        assert health.status == "blocked"
        assert health.callback_ready is False
        assert health.last_error_code == "credential_unavailable"


@pytest.mark.asyncio
async def test_viewer_can_create_self_challenge_but_cannot_configure(database):
    admin, _ = await _member(database)
    viewer, _ = await _member(database, subject="viewer", role=WorkspaceRole.VIEWER)
    async with database() as db:
        installation = await service.create_installation(db, "ws-1", admin, _create())
        await db.commit()
    async with database() as db:
        challenge = await service.create_binding_challenge(
            db, "ws-1", installation.installation_public_id, viewer
        )
        await db.commit()
        assert challenge.command_text.startswith("绑定 ")
        with pytest.raises(HTTPException) as error:
            await service.update_installation(
                db,
                "ws-1",
                installation.installation_public_id,
                viewer,
                ConnectorInstallationUpdate(name="x"),
            )
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_binding_revoke_rechecks_self_or_configuration_permission(database):
    admin, _ = await _member(database)
    viewer, viewer_id = await _member(database, subject="viewer", role=WorkspaceRole.VIEWER)
    other, _ = await _member(database, subject="other", role=WorkspaceRole.VIEWER)
    async with database() as db:
        installation_read = await service.create_installation(db, "ws-1", admin, _create())
        installation = await db.scalar(
            select(ConnectorInstallation).where(
                ConnectorInstallation.public_id == installation_read.installation_public_id
            )
        )
        binding = ConnectorPrincipalBinding(
            public_id="binding-1",
            installation_id=installation.id,
            workspace_id="ws-1",
            user_id=viewer_id,
            tenant_key=installation.tenant_key,
            open_id="ou-viewer",
            p2p_chat_id="oc-viewer",
            active=True,
            revision=1,
        )
        db.add(binding)
        await db.commit()
    async with database() as db:
        with pytest.raises(HTTPException) as error:
            await service.revoke_binding(db, "ws-1", "binding-1", other)
        assert error.value.status_code == 403
    async with database() as db:
        result = await service.revoke_binding(db, "ws-1", "binding-1", viewer)
        await db.commit()
        assert result.active is False


@pytest.mark.asyncio
async def test_typed_http_contract_uses_public_ids_and_never_returns_secrets(database):
    identity, _ = await _member(database)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        async with database() as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/workspaces/ws-1/connector-installations",
            json=_create().model_dump(),
        )
        assert response.status_code == 201
        payload = response.json()["data"]
        assert payload["installation_public_id"]
        assert payload["has_app_secret"] is True
        assert "app_secret" not in payload and "encrypt_key" not in payload
        listed = await client.get("/api/v1/workspaces/ws-1/connector-installations")
        assert (
            listed.json()["data"][0]["installation_public_id"] == payload["installation_public_id"]
        )


@pytest.mark.asyncio
async def test_callback_route_rejects_oversize_body_before_dispatch(database):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        async with database() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/connectors/feishu/installations/unknown/events",
            content=b"x" * (256 * 1024 + 1),
        )
        assert response.status_code == 413
