from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.v1 import chat
from backend.api.v1.connector_replies import router as connector_replies_router
from backend.auth.crypto import encrypt
from backend.database import Base, get_db
from backend.models.agent_conversation import AgentConversation, AgentConversationTurn
from backend.models.connector_reply import (
    ConnectorArtifactGrant,
    ConnectorInboundReceipt,
    ConnectorInstallation,
    ConnectorOutboundDelivery,
    ConnectorPrincipalBinding,
    ConnectorReplyGrant,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.studio import StudioProject, StudioWorkflow, StudioWorkspace
from backend.schemas.connector_reply import ConnectorArtifactGrantCreate, ConnectorReplyGrantCreate
from backend.schemas.project_artifact import ProjectArtifactDetail
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import agent_conversation_service as conversations
from backend.services import connector_artifact_grant_service as artifacts
from backend.services import connector_outbound_service as outbound
from backend.services import connector_reply_grant_service as grants
from backend.services import connector_reply_worker as worker
from backend.services.connector_outbound_service import (
    create_artifact_offer_delivery,
    create_file_delivery,
    create_text_delivery,
    deliver_outbound,
)
from backend.services.connector_receipt_service import (
    VerifiedFeishuMessage,
    persist_verified_message,
)


@pytest.fixture
async def p2_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'connector-p2.db'}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(id="p2-user", subject="p2-subject", disabled=False)
        workspace = Workspace(id="p2-governed", name="Governed", slug="p2-governed")
        studio = StudioWorkspace(id="p2-studio", name="Studio", slug="p2-studio")
        project = StudioProject(
            id="p2-project",
            workspace_id=studio.id,
            name="Project",
            slug="p2-project",
            created_by_user_id=user.id,
        )
        workflow = StudioWorkflow(id="p2-workflow", project_id=project.id, name="Workflow")
        conversation = AgentConversation(
            id="p2-conversation",
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            context_binding={
                "studio_workspace_id": studio.id,
                "project_id": project.id,
                "workflow_id": workflow.id,
            },
            revision=0,
        )
        installation = ConnectorInstallation(
            id="p2-installation-id",
            public_id="p2-installation",
            workspace_id=workspace.id,
            provider="feishu",
            name="Feishu",
            app_id="app",
            tenant_key="tenant",
            status="active",
        )
        installation.app_secret = "secret"
        installation.encrypt_key = "encrypt"
        installation.verification_token = "verify"
        binding = ConnectorPrincipalBinding(
            id="p2-binding-id",
            public_id="p2-binding",
            installation_id=installation.id,
            workspace_id=workspace.id,
            user_id=user.id,
            tenant_key="tenant",
            open_id="ou-bound",
            p2p_chat_id="oc-bound",
            active=True,
            revision=1,
        )
        db.add_all(
            [
                user,
                workspace,
                WorkspaceMembership(
                    workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OPERATOR
                ),
                studio,
                project,
                workflow,
                conversation,
                installation,
                binding,
            ]
        )
        await db.commit()
    monkeypatch.setattr(worker, "schedule_connector_outbound", lambda _delivery_id: None)
    await worker.start_connector_reply_worker(factory, reply_enabled=True, artifact_enabled=True)
    yield factory
    await worker.stop_connector_reply_worker()
    await engine.dispose()


def _identity() -> RequestIdentity:
    return RequestIdentity(subject="p2-subject", auth_method="oidc")


def _create(request_id: str = "grant-1") -> ConnectorReplyGrantCreate:
    return ConnectorReplyGrantCreate(
        request_id=request_id,
        installation_public_id="p2-installation",
        binding_public_id="p2-binding",
        conversation_id="p2-conversation",
    )


def _message(
    text: str,
    *,
    message_id: str,
    reply_to: str | None = None,
) -> VerifiedFeishuMessage:
    return VerifiedFeishuMessage(
        provider_message_id=message_id,
        provider_event_id=f"event-{message_id}",
        app_id="app",
        tenant_key="tenant",
        sender_open_id="ou-bound",
        chat_id="oc-bound",
        chat_type="p2p",
        message_type="text",
        reply_to_message_id=reply_to,
        safe_content_text=text,
    )


def _artifact_detail(
    *,
    conversation_id: str | None = "p2-conversation",
    media_type: str = "text/plain",
    content: dict | None = None,
) -> ProjectArtifactDetail:
    now = datetime.now(UTC)
    return ProjectArtifactDetail(
        id="session:artifact",
        artifact_id="artifact",
        title="Connector report",
        media_type=media_type,
        kind="report",
        content_hash="artifact-content-hash",
        workspace_id="p2-studio",
        project_id="p2-project",
        workflow_id="p2-workflow",
        run_id="p2-run",
        session_id="session",
        conversation_id=conversation_id,
        source="native",
        simulated=False,
        created_at=now,
        updated_at=now,
        schema_version="1",
        content=content if content is not None else {"body": "authorized artifact body"},
        payload={},
        provenance={"conversation_id": conversation_id},
        grounding_artifact_ids=[],
    )


def _install_artifact_detail(monkeypatch, detail: ProjectArtifactDetail) -> None:
    async def resolve_scope(*_args, **_kwargs):
        return object()

    async def get_artifact(*_args, **_kwargs):
        return detail

    monkeypatch.setattr(artifacts.project_artifact_service, "resolve_scope", resolve_scope)
    monkeypatch.setattr(artifacts.project_artifact_service, "get_project_artifact", get_artifact)


async def _grant(factory) -> tuple[ConnectorReplyGrant, ConnectorOutboundDelivery]:
    async with factory() as db:
        created = await grants.create_reply_grant(
            db, "p2-governed", "p2-project", _identity(), _create()
        )
        assert created.created is True
    async with factory() as db:
        grant = await db.scalar(select(ConnectorReplyGrant))
        activation = await db.scalar(
            select(ConnectorOutboundDelivery).where(
                ConnectorOutboundDelivery.purpose == "activation"
            )
        )
        assert grant is not None and activation is not None
        return grant, activation


@pytest.mark.asyncio
async def test_reply_grant_idempotency_active_slot_and_rebuild(p2_scope):
    factory = p2_scope
    first, _ = await _grant(factory)
    async with factory() as db:
        replay = await grants.create_reply_grant(
            db, "p2-governed", "p2-project", _identity(), _create()
        )
        assert replay.created is False
        assert replay.reply_grant_public_id == first.public_id
        assert await db.scalar(select(func.count()).select_from(ConnectorReplyGrant)) == 1
        assert await db.scalar(select(func.count()).select_from(ConnectorOutboundDelivery)) == 1
    async with factory() as db:
        with pytest.raises(HTTPException, match="active_reply_grant_exists"):
            await grants.create_reply_grant(
                db, "p2-governed", "p2-project", _identity(), _create("different-key")
            )
        await db.rollback()
        await grants.revoke_reply_grant(
            db, "p2-governed", "p2-project", first.public_id, _identity()
        )
        rebuilt = await grants.create_reply_grant(
            db, "p2-governed", "p2-project", _identity(), _create("different-key")
        )
        assert rebuilt.created is True
    async with factory() as db:
        rows = list(await db.scalars(select(ConnectorReplyGrant)))
        assert sorted(row.status for row in rows) == ["active", "revoked"]


@pytest.mark.asyncio
async def test_concurrent_same_request_has_one_grant_and_activation(p2_scope):
    factory = p2_scope

    async def create():
        async with factory() as db:
            return await grants.create_reply_grant(
                db, "p2-governed", "p2-project", _identity(), _create("concurrent-key")
            )

    first, second = await asyncio.gather(create(), create())
    assert sorted((first.created, second.created)) == [False, True]
    assert first.reply_grant_public_id == second.reply_grant_public_id
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorReplyGrant)) == 1
        assert await db.scalar(select(func.count()).select_from(ConnectorOutboundDelivery)) == 1


@pytest.mark.asyncio
async def test_claim_prefix_is_always_redacted_and_only_valid_claim_binds_artifact(p2_scope):
    factory = p2_scope
    reply, _ = await _grant(factory)
    claim = "opaque_claim_material_1234567890"
    async with factory() as db:
        artifact = ConnectorArtifactGrant(
            id="artifact-grant-id",
            public_id="artifact-grant",
            reply_grant_id=reply.id,
            conversation_id=reply.conversation_id,
            created_by_user_id="p2-user",
            create_request_id="artifact-request",
            create_request_hash="1" * 64,
            workspace_id="p2-governed",
            studio_workspace_id="p2-studio",
            project_id="p2-project",
            workflow_id="p2-workflow",
            run_id="run",
            artifact_public_id="session:artifact",
            artifact_id="artifact",
            session_id="session",
            content_hash="2" * 64,
            title="Report",
            media_type="application/json",
            simulated=True,
            claim_digest=__import__("hashlib").sha256(claim.encode()).hexdigest(),
            claim_ciphertext=encrypt(claim),
            status="active",
            version=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            active_slot="3" * 64,
        )
        db.add(artifact)
        await db.commit()
    async with factory() as db:
        invalid = await persist_verified_message(
            db,
            "p2-installation-id",
            _message("领取 malformed", message_id="claim-invalid"),
        )
        await db.commit()
        assert invalid.safe_content_text == "[artifact claim]"
        assert invalid.artifact_grant_id is None
        assert invalid.status == "rejected"
    async with factory() as db:
        valid = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(f"领取 {claim}", message_id="claim-valid"),
        )
        await db.commit()
        assert valid.safe_content_text == "[artifact claim]"
        assert valid.artifact_grant_id == "artifact-grant-id"
        assert valid.reply_grant_id == reply.id
        assert valid.status == "received"
    async with factory() as db:
        rows = list(await db.scalars(select(ConnectorInboundReceipt)))
        for row in rows:
            assert claim not in row.safe_content_text


@pytest.mark.asyncio
async def test_revoked_artifact_after_callback_fails_receipt_and_releases_grant(p2_scope):
    factory = p2_scope
    reply, _ = await _grant(factory)
    claim = "revoked_claim_material_1234567890"
    async with factory() as db:
        artifact = ConnectorArtifactGrant(
            id="revoked-artifact-grant-id",
            public_id="revoked-artifact-grant",
            reply_grant_id=reply.id,
            conversation_id=reply.conversation_id,
            created_by_user_id="p2-user",
            create_request_id="revoked-artifact-request",
            create_request_hash="8" * 64,
            workspace_id="p2-governed",
            studio_workspace_id="p2-studio",
            project_id="p2-project",
            workflow_id="p2-workflow",
            run_id="run",
            artifact_public_id="session:revoked-artifact",
            artifact_id="revoked-artifact",
            session_id="session",
            content_hash="9" * 64,
            title="Revoked report",
            media_type="application/json",
            simulated=True,
            claim_digest=__import__("hashlib").sha256(claim.encode()).hexdigest(),
            claim_ciphertext=encrypt(claim),
            status="active",
            version=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            active_slot="a" * 64,
        )
        db.add(artifact)
        await db.commit()
    async with factory() as db:
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(f"领取 {claim}", message_id="claim-revoked-after-ack"),
        )
        await db.commit()
        assert receipt.status == "received"
        receipt_id = receipt.id
    async with factory() as db:
        artifact = await db.get(ConnectorArtifactGrant, "revoked-artifact-grant-id")
        assert artifact is not None
        artifact.status = "revoked"
        artifact.active_slot = None
        artifact.revoked_at = datetime.now(UTC)
        await db.commit()
    await worker.process_connector_receipt(factory, receipt_id)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        reply = await db.get(ConnectorReplyGrant, reply.id)
        assert receipt is not None and receipt.status == "permanent_failed"
        assert receipt.stable_error_code == "artifact_grant_unavailable"
        assert receipt.lease_owner is None and receipt.lease_expires_at is None
        assert reply is not None and reply.execution_receipt_id is None
        assert reply.execution_lease_owner is None
        assert reply.execution_lease_expires_at is None


@pytest.mark.asyncio
async def test_artifact_grant_replay_claim_and_delivery_redeems_atomically(p2_scope, monkeypatch):
    factory = p2_scope
    reply, _ = await _grant(factory)
    _install_artifact_detail(monkeypatch, _artifact_detail())
    body = ConnectorArtifactGrantCreate(
        request_id="artifact-create",
        artifact_public_id="session:artifact",
        workflow_id="p2-workflow",
        run_id="p2-run",
    )
    async with factory() as db:
        created = await artifacts.create_artifact_grant(
            db,
            "p2-governed",
            "p2-project",
            reply.public_id,
            _identity(),
            body,
        )
        assert created.created is True
        assert created.claim_text is not None
        claim_text = created.claim_text
        replay = await artifacts.create_artifact_grant(
            db,
            "p2-governed",
            "p2-project",
            reply.public_id,
            _identity(),
            body,
        )
        assert replay.created is False
        assert replay.claim_text is None
        grant = await db.scalar(select(ConnectorArtifactGrant))
        offer = await db.scalar(
            select(ConnectorOutboundDelivery).where(
                ConnectorOutboundDelivery.purpose == "artifact_offer"
            )
        )
        assert grant is not None and offer is not None
        assert claim_text not in grant.claim_ciphertext
        assert offer.safe_text is None and offer.payload_bytes is None

        offer.status = "sent"
        offer.provider_message_id = "artifact-offer-message"
        await db.commit()

    async with factory() as db:
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(claim_text, message_id="artifact-claim"),
        )
        await db.commit()
        assert receipt.status == "received"
        assert receipt.safe_content_text == "[artifact claim]"
        receipt_id = receipt.id
    await worker.process_connector_receipt(factory, receipt_id)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        assert receipt is not None and receipt.outbound_delivery_id is not None
        delivery = await db.get(ConnectorOutboundDelivery, receipt.outbound_delivery_id)
        assert delivery is not None
        assert delivery.purpose == "artifact_delivery"
        assert delivery.artifact_grant_id == grant.id
        assert delivery.safe_text == "authorized artifact body"
        delivery_id = delivery.id

    class Channel:
        def __init__(self, **_kwargs):
            pass

        async def connect_until_ready(self, *, timeout):
            assert timeout > 0

        async def send(self, _chat_id, message, opts):
            assert type(message).__name__ == "OutboundText"
            assert message.text == "authorized artifact body"
            assert opts["reply_target_gone"] == "fail"
            return type("Result", (), {"success": True, "message_id": "artifact-sent"})()

        async def disconnect(self):
            pass

    assert await deliver_outbound(factory, delivery_id, channel_factory=Channel) == "sent"
    async with factory() as db:
        grant = await db.get(ConnectorArtifactGrant, grant.id)
        delivery = await db.get(ConnectorOutboundDelivery, delivery_id)
        assert grant is not None and grant.status == "redeemed"
        assert grant.active_slot is None and grant.redeemed_at is not None
        assert delivery is not None and delivery.status == "sent"


@pytest.mark.asyncio
async def test_native_markdown_content_field_creates_text_delivery(p2_scope, monkeypatch):
    factory = p2_scope
    reply, _ = await _grant(factory)
    _install_artifact_detail(
        monkeypatch,
        _artifact_detail(
            media_type="text/markdown",
            content={"content": "# Native report\n\nVerified body"},
        ),
    )
    async with factory() as db:
        created = await artifacts.create_artifact_grant(
            db,
            "p2-governed",
            "p2-project",
            reply.public_id,
            _identity(),
            ConnectorArtifactGrantCreate(
                request_id="native-markdown",
                artifact_public_id="session:artifact",
                workflow_id="p2-workflow",
                run_id="p2-run",
            ),
        )
        assert created.claim_text is not None
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(created.claim_text, message_id="native-markdown-claim"),
        )
        await db.commit()
        receipt_id = receipt.id
    await worker.process_connector_receipt(factory, receipt_id)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        assert receipt is not None and receipt.status == "completed"
        assert receipt.outbound_delivery_id is not None
        delivery = await db.get(ConnectorOutboundDelivery, receipt.outbound_delivery_id)
        assert delivery is not None
        assert delivery.payload_kind == "text"
        assert delivery.safe_text == "# Native report\n\nVerified body"


@pytest.mark.asyncio
async def test_json_artifact_uses_canonical_in_memory_payload(p2_scope, monkeypatch):
    factory = p2_scope
    reply, _ = await _grant(factory)
    content = {
        "url": "https://example.invalid/must-not-be-fetched",
        "path": "C:/must-not-be-read.json",
        "body": {"verified": True},
        "label": "界面预览",
    }
    _install_artifact_detail(
        monkeypatch,
        _artifact_detail(media_type="application/json", content=content),
    )
    async with factory() as db:
        created = await artifacts.create_artifact_grant(
            db,
            "p2-governed",
            "p2-project",
            reply.public_id,
            _identity(),
            ConnectorArtifactGrantCreate(
                request_id="native-json",
                artifact_public_id="session:artifact",
                workflow_id="p2-workflow",
                run_id="p2-run",
            ),
        )
        assert created.claim_text is not None
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(created.claim_text, message_id="native-json-claim"),
        )
        await db.commit()
        receipt_id = receipt.id
    await worker.process_connector_receipt(factory, receipt_id)
    expected = json.dumps(
        content, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        assert receipt is not None and receipt.status == "completed"
        delivery = await db.get(ConnectorOutboundDelivery, receipt.outbound_delivery_id)
        assert delivery is not None and delivery.payload_kind == "file"
        assert delivery.payload_bytes == expected
        assert delivery.media_type == "application/json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "content"),
    (
        ("image/png", {"body": "not an allowed file type"}),
        ("text/markdown", {"content": {"nested": "not text"}}),
    ),
)
async def test_artifact_rejects_unknown_media_and_non_string_text(
    p2_scope, monkeypatch, media_type, content
):
    factory = p2_scope
    reply, _ = await _grant(factory)
    _install_artifact_detail(
        monkeypatch,
        _artifact_detail(media_type=media_type, content=content),
    )
    async with factory() as db:
        created = await artifacts.create_artifact_grant(
            db,
            "p2-governed",
            "p2-project",
            reply.public_id,
            _identity(),
            ConnectorArtifactGrantCreate(
                request_id="unsupported-media",
                artifact_public_id="session:artifact",
                workflow_id="p2-workflow",
                run_id="p2-run",
            ),
        )
        assert created.claim_text is not None
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(created.claim_text, message_id="unsupported-media-claim"),
        )
        await db.commit()
        receipt_id = receipt.id
    await worker.process_connector_receipt(factory, receipt_id)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        assert receipt is not None and receipt.status == "permanent_failed"
        assert receipt.stable_error_code == "artifact_media_unsupported"
        assert receipt.outbound_delivery_id is None


@pytest.mark.asyncio
async def test_outbound_payload_size_limits_count_encoded_bytes(p2_scope):
    factory = p2_scope
    reply, _ = await _grant(factory)
    async with factory() as db:
        exact_file = create_file_delivery(
            db,
            installation_id="p2-installation-id",
            reply_grant_id=reply.id,
            artifact_grant_id="artifact-size-boundary",
            chat_id="oc-bound",
            reply_to_message_id="size-request",
            payload=b"x" * 1_048_576,
            file_name="boundary.json",
            media_type="application/json",
            dedupe_owner_id="file-size-boundary",
        )
        assert len(exact_file.payload_bytes or b"") == 1_048_576
        with pytest.raises(ValueError, match="file size is invalid"):
            create_file_delivery(
                db,
                installation_id="p2-installation-id",
                reply_grant_id=reply.id,
                artifact_grant_id="artifact-too-large",
                chat_id="oc-bound",
                reply_to_message_id="size-request",
                payload=b"x" * 1_048_577,
                file_name="too-large.json",
                media_type="application/json",
                dedupe_owner_id="file-too-large",
            )
        exact_text = create_text_delivery(
            db,
            installation_id="p2-installation-id",
            reply_grant_id=reply.id,
            purpose="agent_reply",
            chat_id="oc-bound",
            text="x" * outbound.MAX_TEXT_BYTES,
            dedupe_owner_id="text-size-boundary",
        )
        assert len((exact_text.safe_text or "").encode("utf-8")) == outbound.MAX_TEXT_BYTES
        with pytest.raises(ValueError, match="text size is invalid"):
            create_text_delivery(
                db,
                installation_id="p2-installation-id",
                reply_grant_id=reply.id,
                purpose="agent_reply",
                chat_id="oc-bound",
                text=("x" * (outbound.MAX_TEXT_BYTES - 2)) + "界",
                dedupe_owner_id="utf8-text-too-large",
            )


@pytest.mark.asyncio
async def test_artifact_grant_requires_trusted_conversation_origin(p2_scope, monkeypatch):
    factory = p2_scope
    reply, _ = await _grant(factory)
    _install_artifact_detail(monkeypatch, _artifact_detail(conversation_id=None))
    async with factory() as db:
        with pytest.raises(HTTPException, match="artifact_conversation_provenance_unavailable"):
            await artifacts.create_artifact_grant(
                db,
                "p2-governed",
                "p2-project",
                reply.public_id,
                _identity(),
                ConnectorArtifactGrantCreate(
                    request_id="originless",
                    artifact_public_id="session:artifact",
                    workflow_id="p2-workflow",
                    run_id="p2-run",
                ),
            )
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(ConnectorArtifactGrant)) == 0


@pytest.mark.asyncio
async def test_concurrent_artifact_request_returns_one_claim_and_offer(p2_scope, monkeypatch):
    factory = p2_scope
    reply, _ = await _grant(factory)
    _install_artifact_detail(monkeypatch, _artifact_detail())
    body = ConnectorArtifactGrantCreate(
        request_id="artifact-concurrent",
        artifact_public_id="session:artifact",
        workflow_id="p2-workflow",
        run_id="p2-run",
    )

    async def create():
        async with factory() as db:
            return await artifacts.create_artifact_grant(
                db,
                "p2-governed",
                "p2-project",
                reply.public_id,
                _identity(),
                body,
            )

    first, second = await asyncio.gather(create(), create())
    assert sorted((first.created, second.created)) == [False, True]
    claims = [item.claim_text for item in (first, second) if item.claim_text is not None]
    assert len(claims) == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorArtifactGrant)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ConnectorOutboundDelivery)
                .where(ConnectorOutboundDelivery.purpose == "artifact_offer")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_two_connector_turns_advance_revision_cursor_and_reuse_history(p2_scope):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        await db.commit()

    seen_messages: list[list[str]] = []

    async def fake_runner(_db, body, identity, **_kwargs):
        assert identity.subject == "p2-subject"
        assert identity.auth_method == "connector"
        assert identity.is_platform_admin is False
        seen_messages.append([message.content for message in body.messages])
        return chat.ChatReply(type="message", content=f"answer-{len(seen_messages)}")

    for index in (1, 2):
        async with factory() as db:
            receipt = await persist_verified_message(
                db,
                "p2-installation-id",
                _message(
                    f"question-{index}",
                    message_id=f"inbound-{index}",
                    reply_to="activation-message",
                ),
            )
            await db.commit()
            assert receipt.status == "received"
            receipt_id = receipt.id
        await worker.process_connector_receipt(factory, receipt_id, chat_runner=fake_runner)

    async with factory() as db:
        conversation = await db.get(AgentConversation, "p2-conversation")
        current_grant = await db.get(ConnectorReplyGrant, reply.id)
        turns = list(
            await db.scalars(select(AgentConversationTurn).order_by(AgentConversationTurn.sequence))
        )
        assert conversation is not None and conversation.revision == 2
        assert current_grant is not None and current_grant.conversation_revision_cursor == 2
        assert [turn.status for turn in turns] == ["completed", "completed"]
        assert len(seen_messages[1]) == 3
        assert seen_messages[1][-1] == "question-2"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ConnectorOutboundDelivery)
                .where(ConnectorOutboundDelivery.purpose == "agent_reply")
            )
            == 2
        )


@pytest.mark.asyncio
async def test_completed_turn_recovery_does_not_call_model_twice(p2_scope):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "recover this",
                message_id="completed-before-receipt-finalize",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id
    claim = await worker._claim_receipt(factory, receipt_id)
    assert claim is not None
    owner, receipt_generation, grant_generation = claim
    calls = 0

    async def first_runner(_db, _body, _identity, **_kwargs):
        nonlocal calls
        calls += 1
        return chat.ChatReply(type="message", content="durably completed")

    async with factory() as db:
        access = await grants.authorize_receipt_for_agent(
            db,
            receipt_id=receipt_id,
            lease_owner=owner,
            receipt_lease_generation=receipt_generation,
            grant_lease_generation=grant_generation,
        )
        await conversations.send_connector_message(
            db,
            access,
            request_id=worker._turn_request_id(
                "p2-installation-id", "completed-before-receipt-finalize"
            ),
            content="recover this",
            chat_runner=first_runner,
        )
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        current_grant = await db.get(ConnectorReplyGrant, reply.id)
        assert receipt is not None and current_grant is not None
        receipt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        current_grant.execution_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    async def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("completed deterministic turn must not call the model again")

    await worker.process_connector_receipt(factory, receipt_id, chat_runner=forbidden_runner)
    assert calls == 1
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        assert receipt is not None and receipt.status == "completed"
        assert receipt.outbound_delivery_id is not None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AgentConversationTurn)
                .where(AgentConversationTurn.conversation_id == "p2-conversation")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_failed_turn_is_terminal_and_recovery_does_not_call_model_twice(p2_scope):
    factory = p2_scope
    _, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "fail once",
                message_id="failed-turn-is-terminal",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id
    calls = 0

    async def failing_runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("model failed with a private diagnostic")

    await worker.process_connector_receipt(factory, receipt_id, chat_runner=failing_runner)
    await worker.recover_connector_receipts()
    await worker.process_connector_receipt(factory, receipt_id, chat_runner=failing_runner)
    assert calls == 1
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        turn = await db.scalar(
            select(AgentConversationTurn).where(
                AgentConversationTurn.request_id
                == worker._turn_request_id("p2-installation-id", "failed-turn-is-terminal")
            )
        )
        assert receipt is not None and receipt.status == "permanent_failed"
        assert receipt.stable_error_code == "connector_processing_failed"
        assert receipt.outbound_delivery_id is None
        assert turn is not None and turn.status == "failed"


@pytest.mark.asyncio
async def test_connector_proposal_uses_existing_turn_and_notice_path(p2_scope):
    factory = p2_scope
    _, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "prepare change",
                message_id="proposal-inbound",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id

    async def proposal_runner(_db, _body, _identity, **kwargs):
        provenance = kwargs["proposal_provenance"]
        assert provenance.conversation_id == "p2-conversation"
        return chat.ChatReply(
            type="proposal",
            proposal=chat.Proposal(
                tool="toggle_source",
                args={"source_id": "source", "enabled": True},
                summary="Enable source",
                diff="enabled: false -> true",
                work_item_id="work-item",
                workspace_id="p2-governed",
                proposal_version="v1",
            ),
        )

    await worker.process_connector_receipt(factory, receipt_id, chat_runner=proposal_runner)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        turn = await db.scalar(select(AgentConversationTurn))
        delivery = await db.scalar(
            select(ConnectorOutboundDelivery).where(
                ConnectorOutboundDelivery.purpose == "proposal_notice"
            )
        )
        assert receipt is not None and receipt.status == "proposal"
        assert turn is not None and turn.status == "proposal"
        assert delivery is not None and delivery.safe_text == "Enable source"


@pytest.mark.asyncio
async def test_proposal_recovery_reuses_committed_turn_without_model_rerun(p2_scope):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "prepare recoverable change",
                message_id="proposal-before-receipt-finalize",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id
    claim = await worker._claim_receipt(factory, receipt_id)
    assert claim is not None
    owner, receipt_generation, grant_generation = claim
    calls = 0

    async def proposal_runner(_db, _body, _identity, **_kwargs):
        nonlocal calls
        calls += 1
        return chat.ChatReply(
            type="proposal",
            proposal=chat.Proposal(
                tool="toggle_source",
                args={"source_id": "source", "enabled": True},
                summary="Recover proposal",
                diff="enabled: false -> true",
                work_item_id="recover-work-item",
                workspace_id="p2-governed",
                proposal_version="v1",
            ),
        )

    async with factory() as db:
        access = await grants.authorize_receipt_for_agent(
            db,
            receipt_id=receipt_id,
            lease_owner=owner,
            receipt_lease_generation=receipt_generation,
            grant_lease_generation=grant_generation,
        )
        _, turn = await conversations.send_connector_message(
            db,
            access,
            request_id=worker._turn_request_id(
                "p2-installation-id", "proposal-before-receipt-finalize"
            ),
            content="prepare recoverable change",
            chat_runner=proposal_runner,
        )
        assert turn.status == "proposal"
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        current_grant = await db.get(ConnectorReplyGrant, reply.id)
        assert receipt is not None and current_grant is not None
        receipt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        current_grant.execution_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    async def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("committed proposal must not call the model again")

    await worker.process_connector_receipt(factory, receipt_id, chat_runner=forbidden_runner)
    assert calls == 1
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        assert receipt is not None and receipt.status == "proposal"
        assert receipt.outbound_delivery_id is not None
        delivery = await db.get(ConnectorOutboundDelivery, receipt.outbound_delivery_id)
        assert delivery is not None and delivery.purpose == "proposal_notice"
        assert delivery.safe_text == "Recover proposal"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AgentConversationTurn)
                .where(AgentConversationTurn.conversation_id == "p2-conversation")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_stale_receipt_and_grant_generations_cannot_renew(p2_scope):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message("question", message_id="lease-inbound", reply_to="activation-message"),
        )
        await db.commit()
        receipt_id = receipt.id
    claim = await worker._claim_receipt(factory, receipt_id)
    assert claim is not None
    owner, receipt_generation, grant_generation = claim
    async with factory() as db:
        row = await db.get(ConnectorReplyGrant, reply.id)
        assert row is not None
        row.execution_lease_generation += 1
        await db.commit()
    assert not await worker.renew_receipt_lease(
        factory,
        receipt_id=receipt_id,
        owner=owner,
        receipt_generation=receipt_generation,
        grant_generation=grant_generation,
    )


@pytest.mark.asyncio
async def test_two_sqlite_workers_cannot_claim_same_receipt_or_grant(p2_scope):
    factory = p2_scope
    _, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "concurrent claim",
                message_id="concurrent-receipt-claim",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id
    results = await asyncio.gather(
        worker._claim_receipt(factory, receipt_id),
        worker._claim_receipt(factory, receipt_id),
    )
    assert sum(result is not None for result in results) == 1
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        grant = await db.scalar(select(ConnectorReplyGrant))
        assert receipt is not None and receipt.lease_generation == 1
        assert grant is not None and grant.execution_lease_generation == 1


@pytest.mark.asyncio
async def test_recovery_cursor_prevents_retryable_head_starvation(p2_scope, monkeypatch):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        for index in range(6):
            await persist_verified_message(
                db,
                "p2-installation-id",
                _message(
                    f"queued-{index}",
                    message_id=f"queued-receipt-{index}",
                    reply_to="activation-message",
                ),
            )
        for index in range(6):
            create_text_delivery(
                db,
                installation_id="p2-installation-id",
                reply_grant_id=reply.id,
                purpose="agent_reply",
                chat_id="oc-bound",
                text=f"retryable-{index}",
                dedupe_owner_id=f"retryable-{index}",
            ).status = "retryable_failed"
        await db.commit()
        receipt_ids = list(
            await db.scalars(
                select(ConnectorInboundReceipt.id).order_by(
                    ConnectorInboundReceipt.created_at, ConnectorInboundReceipt.id
                )
            )
        )
        delivery_ids = list(
            await db.scalars(
                select(ConnectorOutboundDelivery.id)
                .where(ConnectorOutboundDelivery.status == "retryable_failed")
                .order_by(ConnectorOutboundDelivery.created_at, ConnectorOutboundDelivery.id)
            )
        )
    scheduled_receipts: list[str] = []
    scheduled_deliveries: list[str] = []
    monkeypatch.setattr(worker, "schedule_connector_receipt", scheduled_receipts.append)
    monkeypatch.setattr(worker, "schedule_connector_outbound", scheduled_deliveries.append)
    await worker.recover_connector_receipts(limit=5)
    await worker.recover_connector_receipts(limit=5)
    assert receipt_ids[-1] in scheduled_receipts
    assert delivery_ids[-1] in scheduled_deliveries


@pytest.mark.asyncio
async def test_stale_failure_writer_cannot_mark_takeover_turn_failed(p2_scope):
    factory = p2_scope
    _, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "question",
                message_id="takeover-before-turn",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id
    first_claim = await worker._claim_receipt(factory, receipt_id)
    assert first_claim is not None
    first_owner, first_receipt_generation, first_grant_generation = first_claim
    async with factory() as db:
        stale_access = await grants.authorize_receipt_for_agent(
            db,
            receipt_id=receipt_id,
            lease_owner=first_owner,
            receipt_lease_generation=first_receipt_generation,
            grant_lease_generation=first_grant_generation,
        )
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        grant = await db.scalar(select(ConnectorReplyGrant))
        assert receipt is not None and grant is not None
        receipt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        grant.execution_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    second_claim = await worker._claim_receipt(factory, receipt_id)
    assert second_claim is not None
    second_owner, second_receipt_generation, second_grant_generation = second_claim
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking_runner(_db, _body, _identity, **_kwargs):
        started.set()
        await finish.wait()
        return chat.ChatReply(type="message", content="takeover answer")

    async with factory() as takeover_db:
        takeover_access = await grants.authorize_receipt_for_agent(
            takeover_db,
            receipt_id=receipt_id,
            lease_owner=second_owner,
            receipt_lease_generation=second_receipt_generation,
            grant_lease_generation=second_grant_generation,
        )
        request_id = worker._turn_request_id("p2-installation-id", "takeover-before-turn")
        takeover_task = asyncio.create_task(
            conversations.send_connector_message(
                takeover_db,
                takeover_access,
                request_id=request_id,
                content="question",
                chat_runner=blocking_runner,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        async with factory() as stale_db:
            turn = await stale_db.scalar(
                select(AgentConversationTurn).where(AgentConversationTurn.request_id == request_id)
            )
            assert turn is not None and turn.status == "running"
            await conversations._mark_connector_turn_failed(
                stale_db,
                stale_access,
                turn.id,
                code="stale_failure",
                message="stale worker",
            )
        async with factory() as check_db:
            turn = await check_db.scalar(
                select(AgentConversationTurn).where(AgentConversationTurn.request_id == request_id)
            )
            assert turn is not None and turn.status == "running"
        finish.set()
        _, completed = await takeover_task
        assert completed.status == "completed"


@pytest.mark.asyncio
async def test_send_timeout_is_indeterminate_and_disconnect_is_awaited(p2_scope):
    factory = p2_scope
    _, delivery = await _grant(factory)

    class Channel:
        disconnected = False

        def __init__(self, **_kwargs):
            pass

        async def connect_until_ready(self, *, timeout):
            assert timeout > 0

        async def send(self, *_args, **_kwargs):
            raise TimeoutError

        async def disconnect(self):
            type(self).disconnected = True

    result = await deliver_outbound(factory, delivery.id, channel_factory=Channel)
    assert result == "indeterminate"
    assert Channel.disconnected is True
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None
        assert row.status == "indeterminate"
        assert row.error_code == "send_result_unknown"
    assert await worker.recover_connector_receipts() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("success", "message_id", "error_code", "expected"),
    (
        (True, None, None, "indeterminate"),
        (False, None, "permission_denied", "failed"),
        (False, None, "rate_limited", "indeterminate"),
    ),
)
async def test_outbound_result_classification(p2_scope, success, message_id, error_code, expected):
    factory = p2_scope
    _, delivery = await _grant(factory)

    class Channel:
        def __init__(self, **_kwargs):
            pass

        async def connect_until_ready(self, *, timeout):
            assert timeout > 0

        async def send(self, _chat_id, _message, opts):
            assert len(opts["uuid"]) <= 50
            assert opts["reply_target_gone"] == "fail"
            error = None
            if error_code is not None:
                error = type("Error", (), {"code": type("Code", (), {"value": error_code})()})()
            return type(
                "Result",
                (),
                {"success": success, "message_id": message_id, "error": error},
            )()

        async def disconnect(self):
            pass

    assert await deliver_outbound(factory, delivery.id, channel_factory=Channel) == expected
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None and row.status == expected
        if expected == "indeterminate":
            assert row.error_code == "send_result_unknown"
        else:
            assert row.error_code == error_code


@pytest.mark.asyncio
async def test_outbound_sdk_failures_do_not_log_sensitive_values(p2_scope, caplog):
    factory = p2_scope
    _, delivery = await _grant(factory)
    sensitive = {
        "safe_text": "sensitive-agent-response",
        "open_id": "ou-bound",
        "chat_id": "oc-bound",
        "app_secret": "secret",
        "encrypt_key": "encrypt",
        "verification_token": "verify",
        "provider_error": "provider-private-diagnostic",
    }
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None
        row.safe_text = sensitive["safe_text"]
        await db.commit()

    class Channel:
        def __init__(self, **kwargs):
            assert kwargs["app_secret"] == sensitive["app_secret"]
            assert kwargs["encrypt_key"] == sensitive["encrypt_key"]
            assert kwargs["verification_token"] == sensitive["verification_token"]

        async def connect_until_ready(self, *, timeout):
            assert timeout > 0

        async def send(self, chat_id, message, _opts):
            assert chat_id == sensitive["chat_id"]
            assert message.text == sensitive["safe_text"]
            raise RuntimeError(
                " ".join(
                    (
                        sensitive["provider_error"],
                        sensitive["safe_text"],
                        sensitive["open_id"],
                        sensitive["chat_id"],
                    )
                )
            )

        async def disconnect(self):
            raise RuntimeError(sensitive["provider_error"])

    caplog.set_level("WARNING")
    assert await deliver_outbound(factory, delivery.id, channel_factory=Channel) == "indeterminate"
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "connector outbound disconnect failed" in rendered
    for value in sensitive.values():
        assert value not in rendered


@pytest.mark.asyncio
async def test_connector_http_reads_and_errors_do_not_expose_sensitive_values(
    p2_scope, monkeypatch
):
    factory = p2_scope
    _install_artifact_detail(monkeypatch, _artifact_detail())
    sensitive = {
        "app_secret": "http-app-secret-marker",
        "encrypt_key": "http-encrypt-key-marker",
        "verification_token": "http-verification-token-marker",
        "open_id": "ou-bound",
        "chat_id": "oc-bound",
    }
    async with factory() as db:
        installation = await db.get(ConnectorInstallation, "p2-installation-id")
        assert installation is not None
        installation.app_secret = sensitive["app_secret"]
        installation.encrypt_key = sensitive["encrypt_key"]
        installation.verification_token = sensitive["verification_token"]
        await db.commit()

    app = FastAPI()
    app.include_router(connector_replies_router)

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = _identity
    reply_path = "/workspaces/p2-governed/projects/p2-project/connector-reply-grants"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reply_created = await client.post(reply_path, json=_create("http-reply").model_dump())
        assert reply_created.status_code == 201
        reply_public_id = reply_created.json()["data"]["reply_grant_public_id"]
        artifact_path = f"{reply_path}/{reply_public_id}/artifact-grants"
        artifact_body = ConnectorArtifactGrantCreate(
            request_id="http-artifact",
            artifact_public_id="session:artifact",
            workflow_id="p2-workflow",
            run_id="p2-run",
        ).model_dump()
        artifact_created = await client.post(artifact_path, json=artifact_body)
        assert artifact_created.status_code == 201
        created_data = artifact_created.json()["data"]
        claim_text = created_data["claim_text"]
        assert isinstance(claim_text, str) and claim_text
        artifact_public_id = created_data["artifact_grant_public_id"]

        replay = await client.post(artifact_path, json=artifact_body)
        reply_list = await client.get(reply_path, params={"conversation_id": "p2-conversation"})
        reply_detail = await client.get(f"{reply_path}/{reply_public_id}")
        artifact_list = await client.get(artifact_path)
        artifact_detail = await client.get(f"{artifact_path}/{artifact_public_id}")
        missing = await client.get(f"{artifact_path}/missing-artifact-grant")
    assert replay.status_code == 201 and replay.json()["data"]["claim_text"] is None
    assert all(
        response.status_code < 400
        for response in (reply_list, reply_detail, artifact_list, artifact_detail)
    )
    assert missing.status_code == 404
    safe_http = "\n".join(
        response.text
        for response in (
            reply_created,
            replay,
            reply_list,
            reply_detail,
            artifact_list,
            artifact_detail,
            missing,
        )
    )
    async with factory() as db:
        artifact = await db.scalar(select(ConnectorArtifactGrant))
        assert artifact is not None
        internal_values = (artifact.claim_ciphertext, artifact.claim_digest)
    assert claim_text not in safe_http
    for value in (*sensitive.values(), *internal_values):
        assert value not in safe_http


@pytest.mark.asyncio
async def test_connect_failure_is_retryable_and_stop_before_start_is_idempotent(p2_scope):
    factory = p2_scope
    _, delivery = await _grant(factory)

    class Channel:
        disconnected = False

        def __init__(self, **_kwargs):
            pass

        async def connect_until_ready(self, *, timeout):
            raise ConnectionError

        async def send(self, *_args, **_kwargs):
            raise AssertionError("send must not run")

        async def disconnect(self):
            type(self).disconnected = True

    result = await deliver_outbound(factory, delivery.id, channel_factory=Channel)
    assert result == "retryable_failed"
    assert Channel.disconnected is True
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None and row.status == "retryable_failed"
    await worker.stop_connector_reply_worker()
    await worker.stop_connector_reply_worker()
    assert worker.get_connector_reply_worker_readiness().started is False


@pytest.mark.asyncio
async def test_recovery_fences_expired_outbound_owner_and_is_concurrency_safe(
    p2_scope, monkeypatch
):
    factory = p2_scope
    _, delivery = await _grant(factory)
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None
        row.status = "sending"
        row.lease_owner = "late-owner"
        row.lease_generation = 4
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    scheduled: list[str] = []
    monkeypatch.setattr(worker, "schedule_connector_outbound", scheduled.append)
    await asyncio.gather(worker.recover_connector_receipts(), worker.recover_connector_receipts())
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None
        assert row.status == "indeterminate"
        assert row.lease_generation == 5
    assert not await outbound._set_fenced_state(
        factory,
        delivery_id=delivery.id,
        owner="late-owner",
        generation=4,
        expected=("sending",),
        state="sent",
        provider_message_id="late-message",
    )


@pytest.mark.asyncio
async def test_recovery_supervisor_reschedules_retryable_connect_failure(p2_scope, monkeypatch):
    factory = p2_scope
    _, delivery = await _grant(factory)
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None
        row.status = "retryable_failed"
        row.error_code = "connect_failed"
        await db.commit()
    scheduled: list[str] = []
    await worker.stop_connector_reply_worker()
    monkeypatch.setattr(worker, "RECOVERY_SECONDS", 0.01)
    monkeypatch.setattr(worker, "schedule_connector_outbound", scheduled.append)
    await worker.start_connector_reply_worker(factory, reply_enabled=True, artifact_enabled=True)
    for _ in range(20):
        if delivery.id in scheduled:
            break
        await asyncio.sleep(0.01)
    assert delivery.id in scheduled
    await worker.stop_connector_reply_worker()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revocation",
    ("grant", "binding", "user", "membership", "conversation"),
)
async def test_connect_completion_reauthorizes_before_send(p2_scope, revocation):
    factory = p2_scope
    _, delivery = await _grant(factory)

    class Channel:
        disconnected = False

        def __init__(self, **_kwargs):
            pass

        async def connect_until_ready(self, *, timeout):
            assert timeout > 0
            async with factory() as db:
                if revocation == "grant":
                    row = await db.scalar(select(ConnectorReplyGrant))
                    assert row is not None
                    row.status = "revoked"
                    row.active_slot = None
                    row.revoked_at = datetime.now(UTC)
                elif revocation == "binding":
                    row = await db.get(ConnectorPrincipalBinding, "p2-binding-id")
                    assert row is not None
                    row.active = False
                    row.revision += 1
                elif revocation == "user":
                    row = await db.get(User, "p2-user")
                    assert row is not None
                    row.disabled = True
                elif revocation == "membership":
                    await db.execute(
                        delete(WorkspaceMembership).where(
                            WorkspaceMembership.workspace_id == "p2-governed",
                            WorkspaceMembership.user_id == "p2-user",
                        )
                    )
                else:
                    row = await db.get(AgentConversation, "p2-conversation")
                    assert row is not None
                    row.revision += 1
                await db.commit()

        async def send(self, *_args, **_kwargs):
            raise AssertionError("send must not run after authorization changes")

        async def disconnect(self):
            type(self).disconnected = True

    result = await deliver_outbound(factory, delivery.id, channel_factory=Channel)
    assert result == "failed"
    assert Channel.disconnected is True
    async with factory() as db:
        row = await db.get(ConnectorOutboundDelivery, delivery.id)
        assert row is not None
        assert row.status == "failed"
        assert row.provider_message_id is None


@pytest.mark.asyncio
async def test_model_completion_after_binding_revocation_fails_closed(p2_scope):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "question",
                message_id="revoked-during-model",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id

    async def revoking_runner(_db, _body, _identity, **_kwargs):
        async with factory() as mutation_db:
            binding = await mutation_db.get(ConnectorPrincipalBinding, "p2-binding-id")
            assert binding is not None
            binding.active = False
            binding.revision += 1
            await mutation_db.commit()
        return chat.ChatReply(type="message", content="must not be delivered")

    await worker.process_connector_receipt(factory, receipt_id, chat_runner=revoking_runner)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        conversation = await db.get(AgentConversation, "p2-conversation")
        current_grant = await db.get(ConnectorReplyGrant, reply.id)
        turn = await db.scalar(
            select(AgentConversationTurn).where(AgentConversationTurn.request_id.like("feishu:%"))
        )
        assert receipt is not None and receipt.status == "permanent_failed"
        assert receipt.outbound_delivery_id is None
        assert conversation is not None and conversation.revision == 0
        assert current_grant is not None and current_grant.conversation_revision_cursor == 0
        assert turn is not None and turn.status == "failed"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ConnectorOutboundDelivery)
                .where(ConnectorOutboundDelivery.purpose == "agent_reply")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_lost_receipt_heartbeat_discards_model_result(p2_scope, monkeypatch):
    factory = p2_scope
    reply, activation = await _grant(factory)
    async with factory() as db:
        activation.status = "sent"
        activation.provider_message_id = "activation-message"
        await db.merge(activation)
        receipt = await persist_verified_message(
            db,
            "p2-installation-id",
            _message(
                "question",
                message_id="lost-heartbeat",
                reply_to="activation-message",
            ),
        )
        await db.commit()
        receipt_id = receipt.id

    async def lose_fence(_factory, _receipt_id, _owner, _receipt_gen, _grant_gen, lost):
        lost.set()
        await asyncio.Event().wait()

    async def fake_runner(_db, _body, _identity, **_kwargs):
        await asyncio.sleep(0)
        return chat.ChatReply(type="message", content="must be discarded")

    monkeypatch.setattr(worker, "_heartbeat", lose_fence)
    await worker.process_connector_receipt(factory, receipt_id, chat_runner=fake_runner)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        conversation = await db.get(AgentConversation, "p2-conversation")
        current_grant = await db.get(ConnectorReplyGrant, reply.id)
        turn = await db.scalar(
            select(AgentConversationTurn).where(AgentConversationTurn.request_id.like("feishu:%"))
        )
        assert receipt is not None and receipt.status == "processing"
        assert receipt.outbound_delivery_id is None
        assert conversation is not None and conversation.revision == 0
        assert current_grant is not None and current_grant.conversation_revision_cursor == 0
        assert turn is not None and turn.status == "running"
        receipt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        current_grant.execution_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    await worker.process_connector_receipt(factory, receipt_id, chat_runner=fake_runner)
    async with factory() as db:
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        turn = await db.scalar(
            select(AgentConversationTurn).where(AgentConversationTurn.request_id.like("feishu:%"))
        )
        assert receipt is not None and receipt.status == "permanent_failed"
        assert receipt.stable_error_code == "agent_execution_indeterminate"
        assert turn is not None and turn.status == "failed"


@pytest.mark.asyncio
async def test_corrupt_claim_fails_grant_and_delivery_without_sdk(p2_scope):
    factory = p2_scope
    reply, _ = await _grant(factory)
    async with factory() as db:
        artifact = ConnectorArtifactGrant(
            public_id="bad-claim-grant",
            reply_grant_id=reply.id,
            conversation_id=reply.conversation_id,
            created_by_user_id="p2-user",
            create_request_id="bad-claim-request",
            create_request_hash="4" * 64,
            workspace_id="p2-governed",
            studio_workspace_id="p2-studio",
            project_id="p2-project",
            workflow_id="p2-workflow",
            run_id="run",
            artifact_public_id="session:artifact",
            artifact_id="artifact",
            session_id="session",
            content_hash="5" * 64,
            title="Report",
            media_type="application/json",
            simulated=True,
            claim_digest="6" * 64,
            claim_ciphertext="not-fernet",
            status="active",
            version=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            active_slot="7" * 64,
        )
        db.add(artifact)
        await db.flush()
        offer = create_artifact_offer_delivery(db, grant=artifact, reply_grant=reply)
        await db.commit()
        offer_id = offer.id

    def channel_factory(**_kwargs):
        raise AssertionError("SDK must not be constructed when claim decryption fails")

    result = await deliver_outbound(factory, offer_id, channel_factory=channel_factory)
    assert result == "failed"
    async with factory() as db:
        offer = await db.get(ConnectorOutboundDelivery, offer_id)
        artifact = await db.scalar(
            select(ConnectorArtifactGrant).where(
                ConnectorArtifactGrant.public_id == "bad-claim-grant"
            )
        )
        assert offer is not None and offer.error_code == "claim_decryption_failed"
        assert artifact is not None and artifact.status == "failed"
        assert artifact.active_slot is None


@pytest.mark.asyncio
async def test_wrong_shared_key_fails_claim_permanently_and_restore_does_not_retry(
    p2_scope, monkeypatch
):
    factory = p2_scope
    reply, _ = await _grant(factory)
    claim = "shared-key-claim-material"
    async with factory() as db:
        artifact = ConnectorArtifactGrant(
            public_id="wrong-key-grant",
            reply_grant_id=reply.id,
            conversation_id=reply.conversation_id,
            created_by_user_id="p2-user",
            create_request_id="wrong-key-request",
            create_request_hash="b" * 64,
            workspace_id="p2-governed",
            studio_workspace_id="p2-studio",
            project_id="p2-project",
            workflow_id="p2-workflow",
            run_id="run",
            artifact_public_id="session:artifact",
            artifact_id="artifact",
            session_id="session",
            content_hash="c" * 64,
            title="Report",
            media_type="application/json",
            simulated=True,
            claim_digest=__import__("hashlib").sha256(claim.encode()).hexdigest(),
            claim_ciphertext=encrypt(claim),
            status="active",
            version=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            active_slot="d" * 64,
        )
        db.add(artifact)
        await db.flush()
        offer = create_artifact_offer_delivery(db, grant=artifact, reply_grant=reply)
        await db.commit()
        offer_id = offer.id
    correct_key = __import__("os").environ["CREDENTIAL_ENCRYPTION_KEY"]
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    def channel_factory(**_kwargs):
        raise AssertionError("SDK must not be constructed when the shared key changed")

    assert await deliver_outbound(factory, offer_id, channel_factory=channel_factory) == "failed"
    async with factory() as db:
        offer = await db.get(ConnectorOutboundDelivery, offer_id)
        artifact = await db.scalar(
            select(ConnectorArtifactGrant).where(
                ConnectorArtifactGrant.public_id == "wrong-key-grant"
            )
        )
        assert offer is not None and offer.error_code == "claim_decryption_failed"
        assert artifact is not None and artifact.status == "failed"
        assert artifact.error_code == "claim_decryption_failed"
        assert artifact.active_slot is None
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", correct_key)
    assert (
        await deliver_outbound(factory, offer_id, channel_factory=channel_factory) == "not_claimed"
    )
