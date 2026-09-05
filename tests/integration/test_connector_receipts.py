from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models.connector_reply import (
    ConnectorBindingChallenge,
    ConnectorInboundReceipt,
    ConnectorInstallation,
    ConnectorPrincipalBinding,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.services.connector_receipt_service import (
    ReceiptConflictError,
    VerifiedFeishuMessage,
    persist_verified_message,
)


@pytest.fixture
async def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'receipts.db'}", connect_args={"timeout": 10}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        workspace = Workspace(id="ws", name="Workspace", slug="workspace", active=True)
        user = User(id="user", subject="subject", disabled=False)
        db.add_all(
            [
                workspace,
                user,
                WorkspaceMembership(workspace_id="ws", user_id="user", role=WorkspaceRole.VIEWER),
            ]
        )
        installation = ConnectorInstallation(
            public_id="installation",
            workspace_id="ws",
            provider="feishu",
            name="Feishu",
            app_id="cli",
            tenant_key="tenant",
            status="active",
        )
        installation.app_secret = "secret"
        installation.encrypt_key = "encrypt"
        installation.verification_token = "verify"
        db.add(installation)
        await db.commit()
        installation_id = installation.id
    yield factory, installation_id
    await engine.dispose()


def _message(text: str, *, message_id="m1", open_id="ou-1", chat_id="oc-1"):
    return VerifiedFeishuMessage(
        message_id, "e1", "cli", "tenant", open_id, chat_id, "p2p", "text", None, text
    )


@pytest.mark.asyncio
async def test_binding_consumption_and_receipt_are_atomic_and_replay_safe(seeded):
    factory, installation_id = seeded
    code = "one-time"
    async with factory() as db:
        db.add(
            ConnectorBindingChallenge(
                public_id="challenge",
                installation_id=installation_id,
                workspace_id="ws",
                user_id="user",
                challenge_digest=hashlib.sha256(code.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                attempts=0,
            )
        )
        await db.commit()
    async with factory() as db:
        first = await persist_verified_message(db, installation_id, _message(f"绑定 {code}"))
        await db.commit()
        first_id = first.id
    async with factory() as db:
        replay = await persist_verified_message(db, installation_id, _message(f"绑定 {code}"))
        await db.commit()
        assert replay.id == first_id and replay.status == "completed"
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 1
    async with factory() as db:
        with pytest.raises(ReceiptConflictError):
            await persist_verified_message(db, installation_id, _message("changed"))


@pytest.mark.asyncio
async def test_reply_rechecks_binding_membership_and_records_stable_rejection(seeded):
    factory, installation_id = seeded
    async with factory() as db:
        receipt = await persist_verified_message(db, installation_id, _message("continue"))
        await db.commit()
        assert receipt.status == "rejected"
        assert receipt.stable_error_code == "principal_not_bound"
        assert receipt.safe_content_text == "continue"


@pytest.mark.asyncio
async def test_cross_installation_and_identity_mismatch_fail_before_receipt(seeded):
    factory, installation_id = seeded
    bad = VerifiedFeishuMessage(
        "m2", "e2", "other-app", "tenant", "ou", "oc", "p2p", "text", None, "x"
    )
    async with factory() as db:
        with pytest.raises(Exception, match="installation_identity_mismatch"):
            await persist_verified_message(db, installation_id, bad)
        await db.rollback()
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
async def test_revoked_or_disabled_local_identity_cannot_authorize_reply(seeded):
    factory, installation_id = seeded
    async with factory() as db:
        db.add(
            ConnectorPrincipalBinding(
                public_id="bound",
                installation_id=installation_id,
                workspace_id="ws",
                user_id="user",
                tenant_key="tenant",
                open_id="ou-1",
                p2p_chat_id="oc-1",
                active=False,
                revision=2,
                revoked_at=datetime.now(UTC),
            )
        )
        await db.commit()
    async with factory() as db:
        revoked = await persist_verified_message(
            db, installation_id, _message("reply", message_id="revoked")
        )
        await db.commit()
        assert revoked.stable_error_code == "principal_not_bound"
    async with factory() as db:
        binding = await db.scalar(select(ConnectorPrincipalBinding))
        binding.active = True
        user = await db.get(User, "user")
        user.disabled = True
        await db.commit()
    async with factory() as db:
        disabled = await persist_verified_message(
            db, installation_id, _message("reply", message_id="disabled")
        )
        await db.commit()
        assert disabled.stable_error_code == "principal_not_bound"
