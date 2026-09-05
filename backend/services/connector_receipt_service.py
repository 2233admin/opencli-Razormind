"""Atomic, restart-safe persistence for verified connector messages."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.connector_reply import (
    ConnectorBindingChallenge,
    ConnectorInboundReceipt,
    ConnectorInstallation,
    ConnectorPrincipalBinding,
)
from backend.models.identity import User, Workspace, WorkspaceMembership


class ReceiptConflictError(RuntimeError):
    pass


class ReceiptRejectedError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedFeishuMessage:
    provider_message_id: str
    provider_event_id: str | None
    app_id: str
    tenant_key: str
    sender_open_id: str
    chat_id: str
    chat_type: str
    message_type: str
    reply_to_message_id: str | None
    safe_content_text: str


def canonical_request_hash(
    installation_id: str, message: VerifiedFeishuMessage, intent: str
) -> str:
    payload = {
        "app_id": message.app_id,
        "chat_id": message.chat_id,
        "installation_id": installation_id,
        "intent": intent,
        "message_id": message.provider_message_id,
        "open_id": message.sender_open_id,
        "reply_to": message.reply_to_message_id,
        "tenant_key": message.tenant_key,
        "text": message.safe_content_text,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _eligible_user_for_challenge(
    db: AsyncSession, challenge_digest: str, installation: ConnectorInstallation
) -> ConnectorBindingChallenge | None:
    return await db.scalar(
        select(ConnectorBindingChallenge)
        .join(User, User.id == ConnectorBindingChallenge.user_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.user_id == User.id)
            & (WorkspaceMembership.workspace_id == ConnectorBindingChallenge.workspace_id),
        )
        .join(Workspace, Workspace.id == ConnectorBindingChallenge.workspace_id)
        .where(
            ConnectorBindingChallenge.challenge_digest == challenge_digest,
            ConnectorBindingChallenge.installation_id == installation.id,
            ConnectorBindingChallenge.workspace_id == installation.workspace_id,
            ConnectorBindingChallenge.consumed_at.is_(None),
            User.disabled.is_(False),
            Workspace.active.is_(True),
        )
        .with_for_update()
    )


async def _eligible_binding(
    db: AsyncSession, installation: ConnectorInstallation, message: VerifiedFeishuMessage
) -> ConnectorPrincipalBinding | None:
    return await db.scalar(
        select(ConnectorPrincipalBinding)
        .join(User, User.id == ConnectorPrincipalBinding.user_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.user_id == User.id)
            & (WorkspaceMembership.workspace_id == ConnectorPrincipalBinding.workspace_id),
        )
        .join(Workspace, Workspace.id == ConnectorPrincipalBinding.workspace_id)
        .where(
            ConnectorPrincipalBinding.installation_id == installation.id,
            ConnectorPrincipalBinding.workspace_id == installation.workspace_id,
            ConnectorPrincipalBinding.tenant_key == message.tenant_key,
            ConnectorPrincipalBinding.open_id == message.sender_open_id,
            ConnectorPrincipalBinding.p2p_chat_id == message.chat_id,
            ConnectorPrincipalBinding.active.is_(True),
            User.disabled.is_(False),
            Workspace.active.is_(True),
        )
        .with_for_update()
    )


async def persist_verified_message(
    db: AsyncSession, installation_id: str, message: VerifiedFeishuMessage
) -> ConnectorInboundReceipt:
    installation = await db.scalar(
        select(ConnectorInstallation)
        .where(ConnectorInstallation.id == installation_id)
        .with_for_update()
    )
    if installation is None:
        raise ReceiptRejectedError("installation_not_active")
    if message.app_id != installation.app_id or message.tenant_key != installation.tenant_key:
        raise ReceiptRejectedError("installation_identity_mismatch")
    if message.chat_type != "p2p" or message.message_type != "text":
        raise ReceiptRejectedError("unsupported_message_shape")

    prefix = "绑定 "
    intent = "binding" if message.safe_content_text.startswith(prefix) else "reply"
    digest = canonical_request_hash(installation.id, message, intent)
    existing = await db.scalar(
        select(ConnectorInboundReceipt)
        .where(
            ConnectorInboundReceipt.installation_id == installation.id,
            ConnectorInboundReceipt.provider_message_id == message.provider_message_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise ReceiptConflictError("provider_message_id_payload_conflict")
        return existing

    now = datetime.now(UTC)
    binding = None
    error_code = None
    receipt_status = "rejected"
    installation_error = (
        "installation_revoked"
        if installation.revoked_at is not None
        else ("installation_disabled" if installation.status != "active" else None)
    )
    if installation_error is not None:
        error_code = installation_error
    elif intent == "binding":
        code = message.safe_content_text.removeprefix(prefix)
        challenge = await _eligible_user_for_challenge(
            db, hashlib.sha256(code.encode()).hexdigest(), installation
        )
        expires_at = (
            challenge.expires_at.replace(tzinfo=UTC)
            if challenge and challenge.expires_at.tzinfo is None
            else (challenge.expires_at if challenge else None)
        )
        if challenge is None or expires_at is None or expires_at <= now:
            error_code = "binding_challenge_invalid"
        else:
            challenge.attempts += 1
            local_binding = await db.scalar(
                select(ConnectorPrincipalBinding).where(
                    ConnectorPrincipalBinding.installation_id == installation.id,
                    ConnectorPrincipalBinding.user_id == challenge.user_id,
                )
            )
            remote_binding = await db.scalar(
                select(ConnectorPrincipalBinding).where(
                    ConnectorPrincipalBinding.installation_id == installation.id,
                    ConnectorPrincipalBinding.tenant_key == message.tenant_key,
                    ConnectorPrincipalBinding.open_id == message.sender_open_id,
                )
            )
            if (
                local_binding is not None
                and remote_binding is not None
                and local_binding.id != remote_binding.id
            ):
                error_code = "binding_principal_conflict"
            elif (local_binding or remote_binding) is not None:
                candidate = local_binding or remote_binding
                assert candidate is not None
                if candidate.active:
                    error_code = "binding_principal_conflict"
                else:
                    candidate.user_id = challenge.user_id
                    candidate.tenant_key = message.tenant_key
                    candidate.open_id = message.sender_open_id
                    candidate.p2p_chat_id = message.chat_id
                    candidate.active = True
                    candidate.revision += 1
                    candidate.revoked_at = None
                    candidate.revoked_by_user_id = None
                    binding = candidate
                    challenge.consumed_at = now
                    receipt_status = "completed"
            else:
                binding = ConnectorPrincipalBinding(
                    public_id=str(uuid.uuid4()),
                    installation_id=installation.id,
                    workspace_id=installation.workspace_id,
                    user_id=challenge.user_id,
                    tenant_key=message.tenant_key,
                    open_id=message.sender_open_id,
                    p2p_chat_id=message.chat_id,
                    active=True,
                    revision=1,
                )
                db.add(binding)
                await db.flush()
                challenge.consumed_at = now
                receipt_status = "completed"
    else:
        binding = await _eligible_binding(db, installation, message)
        error_code = "reply_grant_unavailable" if binding else "principal_not_bound"

    receipt = ConnectorInboundReceipt(
        installation_id=installation.id,
        provider_message_id=message.provider_message_id,
        provider_event_id=message.provider_event_id,
        request_hash=digest,
        tenant_key=message.tenant_key,
        sender_open_id=message.sender_open_id,
        chat_id=message.chat_id,
        reply_to_message_id=message.reply_to_message_id,
        safe_content_text=message.safe_content_text,
        intent=intent,
        status=receipt_status,
        stable_error_code=error_code,
        binding_id=binding.id if binding else None,
        attempt_count=1,
        processed_at=now,
    )
    db.add(receipt)
    try:
        await db.flush()
    except IntegrityError as exc:
        # The transaction is no longer usable here; the independent caller rolls it
        # back and resolves the winning row in a fresh retry session.
        raise ReceiptConflictError("concurrent_receipt_insert") from exc
    installation.last_ready_at = now
    installation.last_error_code = installation_error
    return receipt
