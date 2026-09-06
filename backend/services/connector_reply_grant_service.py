"""Explicit, revocable authorization for connector-backed Agent replies."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent_conversation import AgentConversation, AgentConversationStatus
from backend.models.connector_reply import (
    ConnectorInboundReceipt,
    ConnectorInstallation,
    ConnectorOutboundDelivery,
    ConnectorPrincipalBinding,
    ConnectorReplyGrant,
)
from backend.models.identity import User, Workspace, WorkspaceMembership
from backend.schemas.connector_reply import (
    ConnectorReplyGrantCreate,
    ConnectorReplyGrantCreated,
    ConnectorReplyGrantRead,
)
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
    role_allows,
)
from backend.services.agent_conversation_service import (
    AgentConversationError,
    validate_context_binding,
)
from backend.services.connector_installation_service import (
    ConnectorCredentialUnavailableError,
    read_installation_credentials,
)
from backend.services.connector_outbound_service import create_text_delivery, delivery_status

_SEAL = object()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _canonical_hash(workspace_id: str, project_id: str, body: ConnectorReplyGrantCreate) -> str:
    payload = {
        "binding_public_id": body.binding_public_id,
        "conversation_id": body.conversation_id,
        "expires_in_seconds": body.expires_in_seconds,
        "installation_public_id": body.installation_public_id,
        "project_id": project_id,
        "schema": "connector-reply-grant-create-v1",
        "workspace_id": workspace_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _context_hash(binding: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _active_slot(workspace_id: str, conversation_id: str, user_id: str) -> str:
    return hashlib.sha256(
        f"reply-slot-v1\0{workspace_id}\0{conversation_id}\0{user_id}".encode()
    ).hexdigest()


async def _require_ready() -> None:
    from backend.services.connector_reply_worker import get_connector_reply_worker_readiness

    if not get_connector_reply_worker_readiness().reply_execution_ready:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Connector replies are unavailable"
        )


async def _read(
    db: AsyncSession, row: ConnectorReplyGrant, *, created: bool | None = None
) -> ConnectorReplyGrantRead | ConnectorReplyGrantCreated:
    installation = await db.get(ConnectorInstallation, row.installation_id)
    binding = await db.get(ConnectorPrincipalBinding, row.binding_id)
    if installation is None or binding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector reply grant not found")
    values = dict(
        reply_grant_public_id=row.public_id,
        installation_public_id=installation.public_id,
        binding_public_id=binding.public_id,
        workspace_id=row.workspace_id,
        studio_workspace_id=row.studio_workspace_id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        run_id=row.run_id,
        conversation_id=row.conversation_id,
        status=row.status,
        version=row.version,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        activation_delivery_status=await delivery_status(
            db, reply_grant_id=row.id, purpose="activation"
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    if created is None:
        return ConnectorReplyGrantRead(**values)
    return ConnectorReplyGrantCreated(**values, created=created)


async def _owned_grant(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    public_id: str,
    identity: RequestIdentity,
    lock: bool = False,
) -> tuple[ConnectorReplyGrant, str]:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    query = select(ConnectorReplyGrant).where(
        ConnectorReplyGrant.public_id == public_id,
        ConnectorReplyGrant.workspace_id == workspace_id,
        ConnectorReplyGrant.project_id == project_id,
        ConnectorReplyGrant.created_by_user_id == access.user_id,
        ConnectorReplyGrant.bound_user_id == access.user_id,
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector reply grant not found")
    conversation = await db.get(AgentConversation, row.conversation_id)
    if conversation is None or conversation.created_by_user_id != access.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector reply grant not found")
    return row, access.user_id


async def create_reply_grant(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    identity: RequestIdentity,
    body: ConnectorReplyGrantCreate,
) -> ConnectorReplyGrantCreated:
    await _require_ready()
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    require_permission(access, WorkspacePermission.EXPORT_ANALYSIS)
    request_hash = _canonical_hash(workspace_id, project_id, body)
    existing = await db.scalar(
        select(ConnectorReplyGrant).where(
            ConnectorReplyGrant.workspace_id == workspace_id,
            ConnectorReplyGrant.project_id == project_id,
            ConnectorReplyGrant.created_by_user_id == access.user_id,
            ConnectorReplyGrant.create_request_id == body.request_id,
        )
    )
    if existing is not None:
        if existing.create_request_hash != request_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_reused")
        result = await _read(db, existing, created=False)
        assert isinstance(result, ConnectorReplyGrantCreated)
        return result

    installation = await db.scalar(
        select(ConnectorInstallation).where(
            ConnectorInstallation.public_id == body.installation_public_id,
            ConnectorInstallation.workspace_id == workspace_id,
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector installation not found")
    if installation.status != "active" or installation.revoked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connector installation is not active")
    try:
        read_installation_credentials(installation)
    except ConnectorCredentialUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Connector verification unavailable"
        ) from exc
    binding = await db.scalar(
        select(ConnectorPrincipalBinding).where(
            ConnectorPrincipalBinding.public_id == body.binding_public_id,
            ConnectorPrincipalBinding.installation_id == installation.id,
            ConnectorPrincipalBinding.workspace_id == workspace_id,
            ConnectorPrincipalBinding.user_id == access.user_id,
        )
    )
    if binding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector binding not found")
    if not binding.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connector binding is not active")
    conversation = await db.scalar(
        select(AgentConversation).where(
            AgentConversation.id == body.conversation_id,
            AgentConversation.workspace_id == workspace_id,
            AgentConversation.created_by_user_id == access.user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation not found")
    if conversation.status != AgentConversationStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent conversation is closed")
    raw_context = (
        conversation.context_binding if isinstance(conversation.context_binding, dict) else {}
    )
    studio_workspace_id = raw_context.get("studio_workspace_id")
    if not isinstance(studio_workspace_id, str) or not studio_workspace_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Studio conversation context required")
    try:
        normalized = await validate_context_binding(
            db,
            workspace_id,
            raw_context,
            studio_workspace_id=studio_workspace_id,
            allow_stored_studio_workspace=True,
        )
    except AgentConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if normalized != raw_context or normalized.get("project_id") != project_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversation context does not match project")

    now = _now()
    slot = _active_slot(workspace_id, conversation.id, access.user_id)
    active = await db.scalar(
        select(ConnectorReplyGrant).where(ConnectorReplyGrant.active_slot == slot).with_for_update()
    )
    if active is not None:
        if (
            active.created_by_user_id == access.user_id
            and active.create_request_id == body.request_id
        ):
            if active.create_request_hash != request_hash:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_reused")
            result = await _read(db, active, created=False)
            assert isinstance(result, ConnectorReplyGrantCreated)
            return result
        if _aware(active.expires_at) <= now:
            active.status = "expired"
            active.active_slot = None
            active.version += 1
        else:
            raise HTTPException(status.HTTP_409_CONFLICT, "active_reply_grant_exists")

    row = ConnectorReplyGrant(
        public_id=str(uuid.uuid4()),
        created_by_user_id=access.user_id,
        create_request_id=body.request_id,
        create_request_hash=request_hash,
        installation_id=installation.id,
        binding_id=binding.id,
        bound_user_id=access.user_id,
        workspace_id=workspace_id,
        studio_workspace_id=studio_workspace_id,
        project_id=project_id,
        workflow_id=normalized.get("workflow_id"),
        run_id=normalized.get("run_id"),
        conversation_id=conversation.id,
        conversation_context_hash=_context_hash(normalized),
        conversation_revision_cursor=conversation.revision,
        binding_revision=binding.revision,
        p2p_chat_id=binding.p2p_chat_id,
        status="active",
        version=1,
        expires_at=now + timedelta(seconds=body.expires_in_seconds),
        active_slot=slot,
        execution_lease_generation=0,
    )
    db.add(row)
    try:
        await db.flush()
        activation = create_text_delivery(
            db,
            installation_id=installation.id,
            reply_grant_id=row.id,
            purpose="activation",
            chat_id=binding.p2p_chat_id,
            text="此会话已启用外部回复。回复本消息可继续原 Agent 会话。",
            dedupe_owner_id=row.id,
        )
        await db.commit()
        from backend.services.connector_reply_worker import schedule_connector_outbound

        schedule_connector_outbound(activation.id)
        await db.refresh(row)
        result = await _read(db, row, created=True)
        assert isinstance(result, ConnectorReplyGrantCreated)
        return result
    except IntegrityError:
        await db.rollback()
        winner = await db.scalar(
            select(ConnectorReplyGrant).where(
                ConnectorReplyGrant.workspace_id == workspace_id,
                ConnectorReplyGrant.project_id == project_id,
                ConnectorReplyGrant.created_by_user_id == access.user_id,
                ConnectorReplyGrant.create_request_id == body.request_id,
            )
        )
        if winner is not None:
            if winner.create_request_hash != request_hash:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_reused")
            result = await _read(db, winner, created=False)
            assert isinstance(result, ConnectorReplyGrantCreated)
            return result
        if await db.scalar(
            select(ConnectorReplyGrant.id).where(ConnectorReplyGrant.active_slot == slot)
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "active_reply_grant_exists")
        raise HTTPException(status.HTTP_409_CONFLICT, "reply_grant_conflict")


async def get_reply_grant(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    public_id: str,
    identity: RequestIdentity,
) -> ConnectorReplyGrantRead:
    row, _ = await _owned_grant(
        db=db,
        workspace_id=workspace_id,
        project_id=project_id,
        public_id=public_id,
        identity=identity,
    )
    result = await _read(db, row)
    assert isinstance(result, ConnectorReplyGrantRead)
    return result


async def list_reply_grants(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    conversation_id: str,
    identity: RequestIdentity,
    *,
    limit: int = 20,
) -> list[ConnectorReplyGrantRead]:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    conversation = await db.scalar(
        select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.workspace_id == workspace_id,
            AgentConversation.created_by_user_id == access.user_id,
        )
    )
    if conversation is None or conversation.context_binding.get("project_id") != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation not found")
    rows = list(
        await db.scalars(
            select(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.workspace_id == workspace_id,
                ConnectorReplyGrant.project_id == project_id,
                ConnectorReplyGrant.conversation_id == conversation_id,
                ConnectorReplyGrant.created_by_user_id == access.user_id,
            )
            .order_by(ConnectorReplyGrant.created_at.desc(), ConnectorReplyGrant.id.desc())
            .limit(limit)
        )
    )
    return [await _read(db, row) for row in rows]  # type: ignore[misc]


async def revoke_reply_grant(
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    public_id: str,
    identity: RequestIdentity,
) -> ConnectorReplyGrantRead:
    row, user_id = await _owned_grant(
        db=db,
        workspace_id=workspace_id,
        project_id=project_id,
        public_id=public_id,
        identity=identity,
        lock=True,
    )
    if row.status == "active":
        row.status = "revoked"
        row.active_slot = None
        row.revoked_at = _now()
        row.revoked_by_user_id = user_id
        row.version += 1
        await db.commit()
        await db.refresh(row)
    result = await _read(db, row)
    assert isinstance(result, ConnectorReplyGrantRead)
    return result


@dataclass(frozen=True)
class ConnectorConversationAccess:
    receipt_id: str
    grant_id: str
    conversation_id: str
    actor_identity: RequestIdentity
    context_binding: dict[str, str]
    expected_revision: int
    lease_owner: str
    receipt_lease_generation: int
    grant_lease_generation: int
    _seal: object = field(repr=False, compare=False)

    def assert_sealed(self) -> None:
        if self._seal is not _SEAL:
            raise RuntimeError("invalid connector conversation capability")

    async def owns_execution_fence(self, db: AsyncSession) -> bool:
        """Lock and verify the receipt/grant generations for a short write."""

        self.assert_sealed()
        now = _now()
        receipt = await db.scalar(
            select(ConnectorInboundReceipt.id)
            .where(
                ConnectorInboundReceipt.id == self.receipt_id,
                ConnectorInboundReceipt.status == "processing",
                ConnectorInboundReceipt.lease_owner == self.lease_owner,
                ConnectorInboundReceipt.lease_generation == self.receipt_lease_generation,
                ConnectorInboundReceipt.grant_lease_generation == self.grant_lease_generation,
                ConnectorInboundReceipt.lease_expires_at > now,
            )
            .with_for_update()
        )
        grant = await db.scalar(
            select(ConnectorReplyGrant.id)
            .where(
                ConnectorReplyGrant.id == self.grant_id,
                ConnectorReplyGrant.execution_receipt_id == self.receipt_id,
                ConnectorReplyGrant.execution_lease_owner == self.lease_owner,
                ConnectorReplyGrant.execution_lease_generation == self.grant_lease_generation,
                ConnectorReplyGrant.execution_lease_expires_at > now,
            )
            .with_for_update()
        )
        return receipt is not None and grant is not None

    async def advance_revision_cursor(self, db: AsyncSession, *, next_revision: int) -> bool:
        self.assert_sealed()
        result = await db.execute(
            update(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.id == self.grant_id,
                ConnectorReplyGrant.conversation_revision_cursor == self.expected_revision,
                ConnectorReplyGrant.execution_receipt_id == self.receipt_id,
                ConnectorReplyGrant.execution_lease_owner == self.lease_owner,
                ConnectorReplyGrant.execution_lease_generation == self.grant_lease_generation,
            )
            .values(conversation_revision_cursor=next_revision)
        )
        return bool(result.rowcount)

    async def reauthorize_for_finalize(self, db: AsyncSession) -> None:
        self.assert_sealed()
        if not await self.owns_execution_fence(db):
            raise ValueError("connector_lease_lost")
        receipt = await db.scalar(
            select(ConnectorInboundReceipt)
            .where(ConnectorInboundReceipt.id == self.receipt_id)
            .execution_options(populate_existing=True)
        )
        grant = await db.scalar(
            select(ConnectorReplyGrant)
            .where(ConnectorReplyGrant.id == self.grant_id)
            .execution_options(populate_existing=True)
        )
        assert receipt is not None and grant is not None
        await _reauthorize(db, grant, receipt=receipt)


async def authorize_receipt_for_agent(
    db: AsyncSession,
    *,
    receipt_id: str,
    lease_owner: str,
    receipt_lease_generation: int,
    grant_lease_generation: int,
) -> ConnectorConversationAccess:
    receipt = await db.scalar(
        select(ConnectorInboundReceipt)
        .where(ConnectorInboundReceipt.id == receipt_id)
        .with_for_update()
    )
    if (
        receipt is None
        or receipt.status != "processing"
        or receipt.lease_owner != lease_owner
        or receipt.lease_generation != receipt_lease_generation
        or receipt.grant_lease_generation != grant_lease_generation
        or receipt.lease_expires_at is None
        or _aware(receipt.lease_expires_at) <= _now()
        or receipt.reply_grant_id is None
        or receipt.conversation_id is None
    ):
        raise ValueError("receipt_lease_invalid")
    grant = await db.get(ConnectorReplyGrant, receipt.reply_grant_id)
    if (
        grant is None
        or grant.execution_receipt_id != receipt.id
        or grant.execution_lease_owner != lease_owner
        or grant.execution_lease_generation != grant_lease_generation
        or grant.execution_lease_expires_at is None
        or _aware(grant.execution_lease_expires_at) <= _now()
    ):
        raise ValueError("reply_grant_unavailable")
    await _reauthorize(db, grant, receipt=receipt)
    user = await db.get(User, grant.bound_user_id)
    assert user is not None
    return ConnectorConversationAccess(
        receipt_id=receipt.id,
        grant_id=grant.id,
        conversation_id=grant.conversation_id,
        actor_identity=RequestIdentity(
            subject=user.subject,
            auth_method="connector",
            is_platform_admin=False,
            claims=None,
        ),
        context_binding=dict(
            (await db.get(AgentConversation, grant.conversation_id)).context_binding
        ),  # type: ignore[union-attr]
        expected_revision=grant.conversation_revision_cursor,
        lease_owner=lease_owner,
        receipt_lease_generation=receipt_lease_generation,
        grant_lease_generation=grant_lease_generation,
        _seal=_SEAL,
    )


async def _reauthorize(
    db: AsyncSession,
    grant: ConnectorReplyGrant,
    *,
    receipt: ConnectorInboundReceipt | None = None,
    lock_scope: bool = False,
) -> tuple[ConnectorInstallation, AgentConversation]:
    now = _now()
    if grant.status != "active" or _aware(grant.expires_at) <= now:
        raise ValueError("reply_grant_unavailable")

    def scoped(query):
        return query.with_for_update() if lock_scope else query

    installation = await db.scalar(
        scoped(
            select(ConnectorInstallation).where(ConnectorInstallation.id == grant.installation_id)
        )
    )
    binding = await db.scalar(
        scoped(
            select(ConnectorPrincipalBinding).where(
                ConnectorPrincipalBinding.id == grant.binding_id
            )
        )
    )
    user = await db.scalar(scoped(select(User).where(User.id == grant.bound_user_id)))
    workspace = await db.scalar(scoped(select(Workspace).where(Workspace.id == grant.workspace_id)))
    conversation = await db.scalar(
        scoped(select(AgentConversation).where(AgentConversation.id == grant.conversation_id))
    )
    membership = await db.scalar(
        scoped(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == grant.workspace_id,
                WorkspaceMembership.user_id == grant.bound_user_id,
            )
        )
    )
    if (
        installation is None
        or installation.status != "active"
        or installation.revoked_at is not None
        or binding is None
        or not binding.active
        or binding.revision != grant.binding_revision
        or binding.user_id != grant.bound_user_id
        or binding.p2p_chat_id != grant.p2p_chat_id
        or user is None
        or user.disabled
        or workspace is None
        or not workspace.active
        or membership is None
        or not role_allows(membership.role, WorkspacePermission.READ)
        or not role_allows(membership.role, WorkspacePermission.EXPORT_ANALYSIS)
        or conversation is None
        or conversation.status != AgentConversationStatus.ACTIVE.value
        or conversation.created_by_user_id != grant.bound_user_id
        or conversation.workspace_id != grant.workspace_id
        or _context_hash(dict(conversation.context_binding)) != grant.conversation_context_hash
        or conversation.revision != grant.conversation_revision_cursor
    ):
        raise ValueError("connector_authorization_revoked")
    read_installation_credentials(installation)
    if receipt is not None and (
        receipt.binding_id != binding.id
        or receipt.conversation_id != conversation.id
        or receipt.chat_id != grant.p2p_chat_id
        or receipt.sender_open_id != binding.open_id
        or receipt.tenant_key != binding.tenant_key
    ):
        raise ValueError("receipt_scope_mismatch")
    return installation, conversation


async def authorize_delivery(
    db: AsyncSession, *, delivery_id: str, lock_scope: bool = False
) -> tuple[ConnectorOutboundDelivery, ConnectorInstallation]:
    delivery_query = select(ConnectorOutboundDelivery).where(
        ConnectorOutboundDelivery.id == delivery_id
    )
    if lock_scope:
        delivery_query = delivery_query.with_for_update()
    row = await db.scalar(delivery_query)
    if row is None:
        raise ValueError("delivery_unavailable")
    grant_query = select(ConnectorReplyGrant).where(ConnectorReplyGrant.id == row.reply_grant_id)
    if lock_scope:
        grant_query = grant_query.with_for_update()
    grant = await db.scalar(grant_query)
    if grant is None or row.chat_id != grant.p2p_chat_id:
        raise ValueError("delivery_scope_mismatch")
    installation, _ = await _reauthorize(db, grant, lock_scope=lock_scope)
    return row, installation


async def reauthorize_reply_grant(db: AsyncSession, grant: ConnectorReplyGrant) -> None:
    """Revalidate a persisted reply capability without exposing its internals."""

    await _reauthorize(db, grant)


__all__ = [
    "ConnectorConversationAccess",
    "authorize_delivery",
    "authorize_receipt_for_agent",
    "create_reply_grant",
    "get_reply_grant",
    "list_reply_grants",
    "reauthorize_reply_grant",
    "revoke_reply_grant",
]
