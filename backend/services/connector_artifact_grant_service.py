"""Exact artifact authorization and opaque connector claims."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.crypto import CredentialCryptoError, decrypt, encrypt
from backend.models.agent_conversation import AgentConversation
from backend.models.connector_reply import (
    ConnectorArtifactGrant,
    ConnectorInboundReceipt,
    ConnectorOutboundDelivery,
    ConnectorPrincipalBinding,
    ConnectorReplyGrant,
)
from backend.schemas.connector_reply import (
    ConnectorArtifactGrantCreate,
    ConnectorArtifactGrantCreated,
    ConnectorArtifactGrantRead,
)
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services import project_artifact_service
from backend.services.connector_installation_service import (
    ConnectorCredentialUnavailableError,
)
from backend.services.connector_outbound_service import (
    create_artifact_offer_delivery,
    create_file_delivery,
    create_text_delivery,
    delivery_status,
)
from backend.services.connector_reply_grant_service import reauthorize_reply_grant
from backend.services.studio_agent_session_access import (
    resolve_stored_agent_session_workspace,
)

CLAIM_PLACEHOLDER = "[artifact claim]"
_CLAIM_RE = re.compile(r"^领取 ([A-Za-z0-9_-]{24,128})$")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def is_artifact_claim_intent(text: str) -> bool:
    return text.startswith("领取")


def _hash(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(workspace_id: str, project_id: str, body: ConnectorArtifactGrantCreate) -> str:
    payload = {
        "artifact_public_id": body.artifact_public_id,
        "expires_in_seconds": body.expires_in_seconds,
        "project_id": project_id,
        "run_id": body.run_id,
        "schema": "connector-artifact-grant-create-v1",
        "workflow_id": body.workflow_id,
        "workspace_id": workspace_id,
    }
    return _hash(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _active_slot(reply_grant_id: str, artifact_public_id: str, content_hash: str) -> str:
    return _hash(f"artifact-slot-v1\0{reply_grant_id}\0{artifact_public_id}\0{content_hash}")


async def _require_ready() -> None:
    from backend.services.connector_reply_worker import get_connector_reply_worker_readiness

    if not get_connector_reply_worker_readiness().artifact_delivery_ready:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Connector artifact delivery is unavailable"
        )


async def _owned_reply(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    reply_grant_public_id: str,
    identity: RequestIdentity,
    require_export: bool,
) -> tuple[ConnectorReplyGrant, str]:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    if require_export:
        require_permission(access, WorkspacePermission.EXPORT_ANALYSIS)
    reply = await db.scalar(
        select(ConnectorReplyGrant).where(
            ConnectorReplyGrant.public_id == reply_grant_public_id,
            ConnectorReplyGrant.workspace_id == workspace_id,
            ConnectorReplyGrant.project_id == project_id,
            ConnectorReplyGrant.created_by_user_id == access.user_id,
            ConnectorReplyGrant.bound_user_id == access.user_id,
        )
    )
    if reply is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector reply grant not found")
    conversation = await db.get(AgentConversation, reply.conversation_id)
    if conversation is None or conversation.created_by_user_id != access.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector reply grant not found")
    scope = await resolve_stored_agent_session_workspace(
        db,
        identity,
        workspace_id=conversation.workspace_id,
        context_binding=conversation.context_binding,
    )
    if scope.access.user_id != access.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector reply grant not found")
    return reply, access.user_id


async def _read(
    db: AsyncSession,
    row: ConnectorArtifactGrant,
    *,
    reply_public_id: str,
    created: bool | None = None,
    claim_text: str | None = None,
) -> ConnectorArtifactGrantRead | ConnectorArtifactGrantCreated:
    values = dict(
        artifact_grant_public_id=row.public_id,
        reply_grant_public_id=reply_public_id,
        artifact_public_id=row.artifact_public_id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        run_id=row.run_id,
        session_id=row.session_id,
        content_hash=row.content_hash,
        title=row.title,
        media_type=row.media_type,
        simulated=row.simulated,
        status=row.status,
        version=row.version,
        expires_at=row.expires_at,
        offer_delivery_status=await delivery_status(
            db,
            reply_grant_id=row.reply_grant_id,
            purpose="artifact_offer",
            artifact_grant_id=row.id,
        ),
        delivery_status=await delivery_status(
            db,
            reply_grant_id=row.reply_grant_id,
            purpose="artifact_delivery",
            artifact_grant_id=row.id,
        ),
        error_code=row.error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    if created is None:
        return ConnectorArtifactGrantRead(**values)
    return ConnectorArtifactGrantCreated(**values, created=created, claim_text=claim_text)


async def create_artifact_grant(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    reply_grant_public_id: str,
    identity: RequestIdentity,
    body: ConnectorArtifactGrantCreate,
) -> ConnectorArtifactGrantCreated:
    await _require_ready()
    reply, user_id = await _owned_reply(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        reply_grant_public_id=reply_grant_public_id,
        identity=identity,
        require_export=True,
    )
    if reply.status != "active" or _aware(reply.expires_at) <= _now():
        raise HTTPException(status.HTTP_409_CONFLICT, "Connector reply grant is not active")
    try:
        await reauthorize_reply_grant(db, reply)
    except (ConnectorCredentialUnavailableError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Connector reply grant is not active"
        ) from exc
    request_hash = _canonical_hash(workspace_id, project_id, body)
    existing = await db.scalar(
        select(ConnectorArtifactGrant).where(
            ConnectorArtifactGrant.workspace_id == workspace_id,
            ConnectorArtifactGrant.project_id == project_id,
            ConnectorArtifactGrant.created_by_user_id == user_id,
            ConnectorArtifactGrant.create_request_id == body.request_id,
        )
    )
    if existing is not None:
        if existing.create_request_hash != request_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_reused")
        result = await _read(db, existing, reply_public_id=reply.public_id, created=False)
        assert isinstance(result, ConnectorArtifactGrantCreated)
        return result
    try:
        scope = await project_artifact_service.resolve_scope(
            db,
            workspace_id=reply.studio_workspace_id,
            project_id=project_id,
            workflow_id=body.workflow_id,
            run_id=body.run_id,
        )
        detail = await project_artifact_service.get_project_artifact(
            db,
            scope=scope,
            artifact_id=body.artifact_public_id,
            conversation_workspace_id=reply.workspace_id,
            studio_workspace_id=reply.studio_workspace_id,
        )
    except project_artifact_service.ProjectArtifactError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.code.value) from exc
    if (
        detail.conversation_id != reply.conversation_id
        or detail.project_id != project_id
        or detail.workflow_id != body.workflow_id
        or detail.run_id != body.run_id
        or detail.session_id is None
        or not detail.content_hash
        or (reply.workflow_id is not None and reply.workflow_id != detail.workflow_id)
        or (reply.run_id is not None and reply.run_id != detail.run_id)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "artifact_conversation_provenance_unavailable"
        )

    slot = _active_slot(reply.id, detail.id, detail.content_hash)
    active = await db.scalar(
        select(ConnectorArtifactGrant)
        .where(ConnectorArtifactGrant.active_slot == slot)
        .with_for_update()
    )
    if active is not None:
        if active.created_by_user_id == user_id and active.create_request_id == body.request_id:
            if active.create_request_hash != request_hash:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_reused")
            result = await _read(db, active, reply_public_id=reply.public_id, created=False)
            assert isinstance(result, ConnectorArtifactGrantCreated)
            return result
        if _aware(active.expires_at) <= _now():
            active.status = "expired"
            active.active_slot = None
            active.version += 1
        else:
            raise HTTPException(status.HTTP_409_CONFLICT, "active_artifact_grant_exists")

    claim = secrets.token_urlsafe(32)
    row = ConnectorArtifactGrant(
        public_id=str(uuid.uuid4()),
        reply_grant_id=reply.id,
        conversation_id=reply.conversation_id,
        created_by_user_id=user_id,
        create_request_id=body.request_id,
        create_request_hash=request_hash,
        workspace_id=workspace_id,
        studio_workspace_id=reply.studio_workspace_id,
        project_id=project_id,
        workflow_id=detail.workflow_id,
        run_id=detail.run_id,
        artifact_public_id=detail.id,
        artifact_id=detail.artifact_id,
        session_id=detail.session_id,
        content_hash=detail.content_hash,
        title=detail.title[:255],
        media_type=detail.media_type[:255],
        simulated=detail.simulated,
        claim_digest=_hash(claim),
        claim_ciphertext=encrypt(claim),
        status="active",
        version=1,
        expires_at=_now() + timedelta(seconds=body.expires_in_seconds),
        active_slot=slot,
    )
    db.add(row)
    try:
        await db.flush()
        offer = create_artifact_offer_delivery(db, grant=row, reply_grant=reply)
        await db.commit()
        from backend.services.connector_reply_worker import schedule_connector_outbound

        schedule_connector_outbound(offer.id)
        await db.refresh(row)
        result = await _read(
            db,
            row,
            reply_public_id=reply.public_id,
            created=True,
            claim_text=f"领取 {claim}",
        )
        assert isinstance(result, ConnectorArtifactGrantCreated)
        return result
    except IntegrityError:
        await db.rollback()
        winner = await db.scalar(
            select(ConnectorArtifactGrant).where(
                ConnectorArtifactGrant.workspace_id == workspace_id,
                ConnectorArtifactGrant.project_id == project_id,
                ConnectorArtifactGrant.created_by_user_id == user_id,
                ConnectorArtifactGrant.create_request_id == body.request_id,
            )
        )
        if winner is not None:
            if winner.create_request_hash != request_hash:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_reused")
            result = await _read(db, winner, reply_public_id=reply.public_id, created=False)
            assert isinstance(result, ConnectorArtifactGrantCreated)
            return result
        if await db.scalar(
            select(ConnectorArtifactGrant.id).where(ConnectorArtifactGrant.active_slot == slot)
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "active_artifact_grant_exists")
        raise HTTPException(status.HTTP_409_CONFLICT, "artifact_grant_conflict")


async def _owned_artifact(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    reply_grant_public_id: str,
    artifact_grant_public_id: str,
    identity: RequestIdentity,
    lock: bool = False,
) -> tuple[ConnectorReplyGrant, ConnectorArtifactGrant]:
    reply, user_id = await _owned_reply(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        reply_grant_public_id=reply_grant_public_id,
        identity=identity,
        require_export=False,
    )
    query = select(ConnectorArtifactGrant).where(
        ConnectorArtifactGrant.public_id == artifact_grant_public_id,
        ConnectorArtifactGrant.reply_grant_id == reply.id,
        ConnectorArtifactGrant.created_by_user_id == user_id,
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector artifact grant not found")
    return reply, row


async def get_artifact_grant(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    reply_grant_public_id: str,
    artifact_grant_public_id: str,
    identity: RequestIdentity,
) -> ConnectorArtifactGrantRead:
    reply, row = await _owned_artifact(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        reply_grant_public_id=reply_grant_public_id,
        artifact_grant_public_id=artifact_grant_public_id,
        identity=identity,
    )
    result = await _read(db, row, reply_public_id=reply.public_id)
    assert isinstance(result, ConnectorArtifactGrantRead)
    return result


async def list_artifact_grants(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    reply_grant_public_id: str,
    identity: RequestIdentity,
    *,
    limit: int = 20,
) -> list[ConnectorArtifactGrantRead]:
    reply, user_id = await _owned_reply(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        reply_grant_public_id=reply_grant_public_id,
        identity=identity,
        require_export=False,
    )
    rows = list(
        await db.scalars(
            select(ConnectorArtifactGrant)
            .where(
                ConnectorArtifactGrant.reply_grant_id == reply.id,
                ConnectorArtifactGrant.created_by_user_id == user_id,
            )
            .order_by(ConnectorArtifactGrant.created_at.desc(), ConnectorArtifactGrant.id.desc())
            .limit(limit)
        )
    )
    return [
        await _read(db, row, reply_public_id=reply.public_id)  # type: ignore[misc]
        for row in rows
    ]


async def revoke_artifact_grant(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    reply_grant_public_id: str,
    artifact_grant_public_id: str,
    identity: RequestIdentity,
) -> ConnectorArtifactGrantRead:
    reply, row = await _owned_artifact(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        reply_grant_public_id=reply_grant_public_id,
        artifact_grant_public_id=artifact_grant_public_id,
        identity=identity,
        lock=True,
    )
    if row.status == "active":
        row.status = "revoked"
        row.active_slot = None
        row.revoked_at = _now()
        row.version += 1
        await db.commit()
        await db.refresh(row)
    result = await _read(db, row, reply_public_id=reply.public_id)
    assert isinstance(result, ConnectorArtifactGrantRead)
    return result


@dataclass(frozen=True)
class ArtifactClaimResolution:
    intent: str
    safe_content_text: str
    stable_error_code: str | None
    reply_grant_id: str | None = None
    conversation_id: str | None = None
    artifact_grant_id: str | None = None


async def resolve_artifact_claim(
    db: AsyncSession,
    *,
    text: str,
    binding: ConnectorPrincipalBinding,
) -> ArtifactClaimResolution:
    if not is_artifact_claim_intent(text):
        raise ValueError("not_artifact_claim")
    match = _CLAIM_RE.fullmatch(text)
    if match is None:
        return ArtifactClaimResolution(
            "artifact_claim", CLAIM_PLACEHOLDER, "artifact_claim_invalid"
        )
    row = await db.scalar(
        select(ConnectorArtifactGrant)
        .where(ConnectorArtifactGrant.claim_digest == _hash(match.group(1)))
        .with_for_update()
    )
    if row is None or row.status != "active" or _aware(row.expires_at) <= _now():
        return ArtifactClaimResolution(
            "artifact_claim", CLAIM_PLACEHOLDER, "artifact_claim_invalid"
        )
    reply = await db.get(ConnectorReplyGrant, row.reply_grant_id)
    if (
        reply is None
        or reply.status != "active"
        or reply.binding_id != binding.id
        or reply.bound_user_id != binding.user_id
        or reply.p2p_chat_id != binding.p2p_chat_id
        or not binding.active
        or reply.binding_revision != binding.revision
        or _aware(reply.expires_at) <= _now()
    ):
        return ArtifactClaimResolution(
            "artifact_claim", CLAIM_PLACEHOLDER, "artifact_claim_invalid"
        )
    return ArtifactClaimResolution(
        "artifact_claim",
        CLAIM_PLACEHOLDER,
        None,
        reply_grant_id=reply.id,
        conversation_id=reply.conversation_id,
        artifact_grant_id=row.id,
    )


async def create_artifact_delivery_for_receipt(
    db: AsyncSession,
    *,
    receipt: ConnectorInboundReceipt,
    grant: ConnectorArtifactGrant,
    reply: ConnectorReplyGrant,
) -> ConnectorOutboundDelivery:
    try:
        scope = await project_artifact_service.resolve_scope(
            db,
            workspace_id=grant.studio_workspace_id,
            project_id=grant.project_id,
            workflow_id=grant.workflow_id,
            run_id=grant.run_id,
        )
        detail = await project_artifact_service.get_project_artifact(
            db,
            scope=scope,
            artifact_id=grant.artifact_public_id,
            conversation_workspace_id=grant.workspace_id,
            studio_workspace_id=grant.studio_workspace_id,
        )
    except project_artifact_service.ProjectArtifactError as exc:
        raise ValueError(exc.code.value) from exc
    if (
        detail.id != grant.artifact_public_id
        or detail.artifact_id != grant.artifact_id
        or detail.session_id != grant.session_id
        or detail.project_id != grant.project_id
        or detail.workflow_id != grant.workflow_id
        or detail.run_id != grant.run_id
        or detail.conversation_id != grant.conversation_id
        or detail.content_hash != grant.content_hash
    ):
        raise ValueError("artifact_scope_or_hash_changed")
    body = None
    if isinstance(detail.content, dict):
        for field in ("body", "content"):
            candidate = detail.content.get(field)
            if isinstance(candidate, str):
                body = candidate
                break
    if grant.media_type in {"text/plain", "text/markdown", "text/html"} and isinstance(body, str):
        return create_text_delivery(
            db,
            installation_id=reply.installation_id,
            reply_grant_id=reply.id,
            purpose="artifact_delivery",
            chat_id=reply.p2p_chat_id,
            text=body,
            dedupe_owner_id=receipt.id,
            reply_to_message_id=receipt.provider_message_id,
            artifact_grant_id=grant.id,
        )
    if grant.media_type != "application/json" and not grant.media_type.endswith("+json"):
        raise ValueError("artifact_media_unsupported")
    payload = json.dumps(
        detail.content, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", grant.title).strip("-.") or "artifact"
    return create_file_delivery(
        db,
        installation_id=reply.installation_id,
        reply_grant_id=reply.id,
        artifact_grant_id=grant.id,
        chat_id=reply.p2p_chat_id,
        reply_to_message_id=receipt.provider_message_id,
        payload=payload,
        file_name=f"{safe_name[:240]}.json",
        media_type="application/json",
        dedupe_owner_id=receipt.id,
    )


def decrypt_claim_for_test(row: ConnectorArtifactGrant) -> str:
    """Narrow helper used by isolated persistence tests; never expose through API."""

    try:
        return decrypt(row.claim_ciphertext)
    except CredentialCryptoError as exc:
        raise ValueError("claim_decryption_failed") from exc


__all__ = [
    "ArtifactClaimResolution",
    "CLAIM_PLACEHOLDER",
    "create_artifact_delivery_for_receipt",
    "create_artifact_grant",
    "get_artifact_grant",
    "is_artifact_claim_intent",
    "list_artifact_grants",
    "resolve_artifact_claim",
    "revoke_artifact_grant",
]
