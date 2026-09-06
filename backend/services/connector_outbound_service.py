"""Immutable, fenced outbound deliveries for external connector replies."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.auth.crypto import CredentialCryptoError, decrypt
from backend.models.connector_reply import (
    ConnectorArtifactGrant,
    ConnectorOutboundDelivery,
    ConnectorReplyGrant,
)
from backend.services.connector_installation_service import (
    ConnectorCredentialUnavailableError,
    read_installation_credentials,
)

logger = logging.getLogger(__name__)
LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 10
CONNECT_TIMEOUT = 15.0
SEND_TIMEOUT = 20.0
DISCONNECT_TIMEOUT = 5.0
MAX_TEXT_BYTES = 150 * 1024


def _hash(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _uuid_for(operation_id: str) -> str:
    # Official create/reply APIs accept at most 50 characters.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli:{operation_id}"))


def _operation(kind: str, owner_id: str) -> str:
    return _hash(f"connector-delivery-v1\0{kind}\0{owner_id}")


def create_text_delivery(
    db: AsyncSession,
    *,
    installation_id: str,
    reply_grant_id: str,
    purpose: str,
    chat_id: str,
    text: str,
    dedupe_owner_id: str,
    reply_to_message_id: str | None = None,
    artifact_grant_id: str | None = None,
) -> ConnectorOutboundDelivery:
    encoded = text.encode("utf-8")
    if not encoded or len(encoded) > MAX_TEXT_BYTES:
        raise ValueError("connector outbound text size is invalid")
    operation_id = _operation(purpose, dedupe_owner_id)
    row = ConnectorOutboundDelivery(
        public_id=str(uuid.uuid4()),
        operation_id=operation_id,
        sdk_uuid=_uuid_for(operation_id),
        dedupe_slot=_hash(f"delivery-slot-v1\0{purpose}\0{dedupe_owner_id}"),
        installation_id=installation_id,
        reply_grant_id=reply_grant_id,
        artifact_grant_id=artifact_grant_id,
        purpose=purpose,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        payload_kind="text",
        safe_text=text,
        payload_hash=_hash(encoded),
        request_hash=_hash(
            json.dumps(
                {
                    "chat_id": chat_id,
                    "purpose": purpose,
                    "reply_to": reply_to_message_id,
                    "text": text,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        status="pending",
        attempt_count=0,
        lease_generation=0,
    )
    db.add(row)
    return row


def create_artifact_offer_delivery(
    db: AsyncSession,
    *,
    grant: ConnectorArtifactGrant,
    reply_grant: ConnectorReplyGrant,
) -> ConnectorOutboundDelivery:
    operation_id = _operation("artifact_offer", grant.id)
    row = ConnectorOutboundDelivery(
        public_id=str(uuid.uuid4()),
        operation_id=operation_id,
        sdk_uuid=_uuid_for(operation_id),
        dedupe_slot=_hash(f"delivery-slot-v1\0artifact_offer\0{grant.id}"),
        installation_id=reply_grant.installation_id,
        reply_grant_id=reply_grant.id,
        artifact_grant_id=grant.id,
        purpose="artifact_offer",
        chat_id=reply_grant.p2p_chat_id,
        payload_kind="artifact_offer",
        payload_hash=_hash(grant.claim_ciphertext),
        request_hash=_hash(
            json.dumps(
                {
                    "artifact": grant.artifact_public_id,
                    "claim_hash": grant.claim_digest,
                    "purpose": "artifact_offer",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        template_version="artifact-offer-v1",
        status="pending",
        attempt_count=0,
        lease_generation=0,
    )
    db.add(row)
    return row


def create_file_delivery(
    db: AsyncSession,
    *,
    installation_id: str,
    reply_grant_id: str,
    artifact_grant_id: str,
    chat_id: str,
    reply_to_message_id: str,
    payload: bytes,
    file_name: str,
    media_type: str,
    dedupe_owner_id: str,
) -> ConnectorOutboundDelivery:
    if not payload or len(payload) > 1_048_576:
        raise ValueError("connector outbound file size is invalid")
    operation_id = _operation("artifact_delivery", dedupe_owner_id)
    row = ConnectorOutboundDelivery(
        public_id=str(uuid.uuid4()),
        operation_id=operation_id,
        sdk_uuid=_uuid_for(operation_id),
        dedupe_slot=_hash(f"delivery-slot-v1\0artifact_delivery\0{dedupe_owner_id}"),
        installation_id=installation_id,
        reply_grant_id=reply_grant_id,
        artifact_grant_id=artifact_grant_id,
        purpose="artifact_delivery",
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        payload_kind="file",
        payload_bytes=payload,
        file_name=file_name[:255],
        media_type=media_type[:255],
        payload_hash=_hash(payload),
        request_hash=_hash(
            json.dumps(
                {
                    "artifact_grant_id": artifact_grant_id,
                    "chat_id": chat_id,
                    "file_name": file_name[:255],
                    "payload_hash": _hash(payload),
                    "reply_to": reply_to_message_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        status="pending",
        attempt_count=0,
        lease_generation=0,
    )
    db.add(row)
    return row


async def delivery_status(
    db: AsyncSession, *, reply_grant_id: str, purpose: str, artifact_grant_id: str | None = None
) -> str | None:
    query = select(ConnectorOutboundDelivery.status).where(
        ConnectorOutboundDelivery.reply_grant_id == reply_grant_id,
        ConnectorOutboundDelivery.purpose == purpose,
    )
    if artifact_grant_id is not None:
        query = query.where(ConnectorOutboundDelivery.artifact_grant_id == artifact_grant_id)
    return await db.scalar(query)


async def renew_delivery_lease(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    delivery_id: str,
    owner: str,
    generation: int,
) -> bool:
    async with session_factory() as db:
        result = await db.execute(
            update(ConnectorOutboundDelivery)
            .where(
                ConnectorOutboundDelivery.id == delivery_id,
                ConnectorOutboundDelivery.lease_owner == owner,
                ConnectorOutboundDelivery.lease_generation == generation,
                ConnectorOutboundDelivery.status.in_(("connecting", "sending")),
            )
            .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS))
        )
        await db.commit()
        return bool(result.rowcount)


async def _outbound_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    delivery_id: str,
    owner: str,
    generation: int,
    lost: asyncio.Event,
) -> None:
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not await renew_delivery_lease(
                session_factory,
                delivery_id=delivery_id,
                owner=owner,
                generation=generation,
            ):
                lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        lost.set()


async def _claim_delivery(
    session_factory: async_sessionmaker[AsyncSession], delivery_id: str
) -> tuple[str, int] | None:
    owner = uuid.uuid4().hex
    now = datetime.now(UTC)
    async with session_factory() as db:
        row = await db.scalar(
            select(ConnectorOutboundDelivery)
            .where(ConnectorOutboundDelivery.id == delivery_id)
            .with_for_update()
        )
        if row is None or row.status not in {"pending", "retryable_failed"}:
            return None
        row.status = "connecting"
        row.lease_owner = owner
        row.lease_generation += 1
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.attempt_count += 1
        generation = row.lease_generation
        await db.commit()
    return owner, generation


async def _set_fenced_state(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    delivery_id: str,
    owner: str,
    generation: int,
    expected: tuple[str, ...],
    state: str,
    error_code: str | None = None,
    provider_message_id: str | None = None,
) -> bool:
    values: dict[str, Any] = {"status": state, "error_code": error_code}
    if provider_message_id is not None:
        values["provider_message_id"] = provider_message_id
        values["sent_at"] = datetime.now(UTC)
    if state not in {"connecting", "sending"}:
        values.update(lease_owner=None, lease_expires_at=None)
    async with session_factory() as db:
        result = await db.execute(
            update(ConnectorOutboundDelivery)
            .where(
                ConnectorOutboundDelivery.id == delivery_id,
                ConnectorOutboundDelivery.lease_owner == owner,
                ConnectorOutboundDelivery.lease_generation == generation,
                ConnectorOutboundDelivery.status.in_(expected),
            )
            .values(**values)
        )
        if result.rowcount and state == "sent":
            delivery = await db.get(ConnectorOutboundDelivery, delivery_id)
            if (
                delivery is not None
                and delivery.purpose == "artifact_delivery"
                and delivery.artifact_grant_id is not None
            ):
                artifact = await db.get(ConnectorArtifactGrant, delivery.artifact_grant_id)
                if artifact is not None and artifact.status == "active":
                    artifact.status = "redeemed"
                    artifact.redeemed_at = datetime.now(UTC)
                    artifact.active_slot = None
                    artifact.version += 1
        if result.rowcount and state == "failed" and error_code == "claim_decryption_failed":
            delivery = await db.get(ConnectorOutboundDelivery, delivery_id)
            if delivery is not None and delivery.artifact_grant_id is not None:
                artifact = await db.get(ConnectorArtifactGrant, delivery.artifact_grant_id)
                if artifact is not None and artifact.status == "active":
                    artifact.status = "failed"
                    artifact.error_code = "claim_decryption_failed"
                    artifact.active_slot = None
                    artifact.version += 1
        await db.commit()
        return bool(result.rowcount)


async def _load_send_snapshot(
    session_factory: async_sessionmaker[AsyncSession], delivery_id: str
) -> tuple[ConnectorOutboundDelivery, Any, str | bytes]:
    from backend.services.connector_reply_grant_service import authorize_delivery

    async with session_factory() as db:
        row, installation = await authorize_delivery(db, delivery_id=delivery_id)
        credentials = read_installation_credentials(installation)
        if row.payload_kind == "artifact_offer":
            grant = await db.get(ConnectorArtifactGrant, row.artifact_grant_id)
            if grant is None or grant.status != "active":
                raise ValueError("artifact_grant_unavailable")
            try:
                claim = decrypt(grant.claim_ciphertext)
            except CredentialCryptoError as exc:
                raise ValueError("claim_decryption_failed") from exc
            payload: str | bytes = f"产物已授权：{grant.title}\n领取 {claim}"
        elif row.payload_kind == "text" and row.safe_text is not None:
            payload = row.safe_text
        elif row.payload_kind == "file" and row.payload_bytes is not None:
            payload = row.payload_bytes
        else:
            raise ValueError("outbound_payload_invalid")
        db.expunge(row)
        return row, credentials, payload


async def deliver_outbound(
    session_factory: async_sessionmaker[AsyncSession],
    delivery_id: str,
    *,
    channel_factory: Callable[..., Any] | None = None,
) -> str:
    """Attempt one delivery; send-stage uncertainty is permanently indeterminate."""

    claim = await _claim_delivery(session_factory, delivery_id)
    if claim is None:
        return "not_claimed"
    owner, generation = claim
    try:
        row, credentials, payload = await _load_send_snapshot(session_factory, delivery_id)
    except (ConnectorCredentialUnavailableError, ValueError) as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else "authorization_failed"
        await _set_fenced_state(
            session_factory,
            delivery_id=delivery_id,
            owner=owner,
            generation=generation,
            expected=("connecting",),
            state="failed",
            error_code=code,
        )
        return "failed"

    if channel_factory is None:
        from lark_channel import FeishuChannel

        channel_factory = FeishuChannel
    channel = channel_factory(
        app_id=(await _installation_app_id(session_factory, row.installation_id)),
        app_secret=credentials.app_secret,
        encrypt_key=credentials.encrypt_key,
        verification_token=credentials.verification_token,
        transport="webhook",
    )
    lost = asyncio.Event()
    heartbeat = asyncio.create_task(
        _outbound_heartbeat(
            session_factory,
            delivery_id=delivery_id,
            owner=owner,
            generation=generation,
            lost=lost,
        ),
        name=f"connector-outbound-heartbeat:{delivery_id}",
    )
    try:
        try:
            await asyncio.wait_for(
                channel.connect_until_ready(timeout=CONNECT_TIMEOUT), timeout=CONNECT_TIMEOUT
            )
        except Exception:
            await _set_fenced_state(
                session_factory,
                delivery_id=delivery_id,
                owner=owner,
                generation=generation,
                expected=("connecting",),
                state="retryable_failed",
                error_code="connect_failed",
            )
            return "retryable_failed"
        if lost.is_set():
            return "fence_lost"
        try:
            fresh_row, _, fresh_payload = await _load_send_snapshot(session_factory, delivery_id)
            immutable_snapshot = (
                fresh_row.request_hash,
                fresh_row.payload_hash,
                fresh_row.sdk_uuid,
                fresh_row.chat_id,
                fresh_row.reply_to_message_id,
                fresh_row.purpose,
            )
            if immutable_snapshot != (
                row.request_hash,
                row.payload_hash,
                row.sdk_uuid,
                row.chat_id,
                row.reply_to_message_id,
                row.purpose,
            ):
                raise ValueError("outbound_snapshot_changed")
            row = fresh_row
            payload = fresh_payload
        except (ConnectorCredentialUnavailableError, ValueError) as exc:
            code = (
                exc.args[0]
                if exc.args and isinstance(exc.args[0], str)
                else "authorization_changed"
            )
            await _set_fenced_state(
                session_factory,
                delivery_id=delivery_id,
                owner=owner,
                generation=generation,
                expected=("connecting",),
                state="failed",
                error_code=code,
            )
            return "failed"
        marked = await _set_fenced_state(
            session_factory,
            delivery_id=delivery_id,
            owner=owner,
            generation=generation,
            expected=("connecting",),
            state="sending",
        )
        if not marked:
            return "fence_lost"
        try:
            message: Any = payload
            if row.payload_kind == "file":
                from lark_channel import MediaSource, OutboundFile

                message = OutboundFile(
                    source=MediaSource(kind="buffer", buffer=payload),
                    file_name=row.file_name or "artifact.json",
                )
            else:
                from lark_channel import OutboundText

                message = OutboundText(text=str(payload))
            result = await asyncio.wait_for(
                channel.send(
                    row.chat_id,
                    message,
                    {
                        "receive_id_type": "chat_id",
                        "reply_to": row.reply_to_message_id,
                        "reply_target_gone": "fail",
                        "uuid": row.sdk_uuid,
                    },
                ),
                timeout=SEND_TIMEOUT,
            )
        except Exception:
            await _set_fenced_state(
                session_factory,
                delivery_id=delivery_id,
                owner=owner,
                generation=generation,
                expected=("sending",),
                state="indeterminate",
                error_code="send_result_unknown",
            )
            return "indeterminate"
        if lost.is_set():
            return "fence_lost"
        if bool(getattr(result, "success", False)) and getattr(result, "message_id", None):
            saved = await _set_fenced_state(
                session_factory,
                delivery_id=delivery_id,
                owner=owner,
                generation=generation,
                expected=("sending",),
                state="sent",
                provider_message_id=str(result.message_id),
            )
            return "sent" if saved else "fence_lost"
        result_code_value = getattr(getattr(result, "error", None), "code", None)
        result_code = getattr(result_code_value, "value", result_code_value)
        definitive = str(result_code) in {
            "target_revoked",
            "permission_denied",
            "format_error",
            "not_supported",
            "ssrf_blocked",
        }
        await _set_fenced_state(
            session_factory,
            delivery_id=delivery_id,
            owner=owner,
            generation=generation,
            expected=("sending",),
            state="failed" if definitive else "indeterminate",
            error_code=str(result_code) if definitive else "send_result_unknown",
        )
        return "failed" if definitive else "indeterminate"
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await asyncio.wait_for(channel.disconnect(), timeout=DISCONNECT_TIMEOUT)
        except Exception:
            logger.warning(
                "connector outbound disconnect failed", extra={"delivery_id": row.public_id}
            )


async def _installation_app_id(
    session_factory: async_sessionmaker[AsyncSession], installation_id: str
) -> str:
    from backend.models.connector_reply import ConnectorInstallation

    async with session_factory() as db:
        row = await db.get(ConnectorInstallation, installation_id)
        if row is None:
            raise ValueError("installation_unavailable")
        return row.app_id


__all__ = [
    "create_artifact_offer_delivery",
    "create_file_delivery",
    "create_text_delivery",
    "deliver_outbound",
    "delivery_status",
    "renew_delivery_lease",
]
