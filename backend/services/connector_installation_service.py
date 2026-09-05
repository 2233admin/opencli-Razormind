"""Workspace-authorized connector installation and principal binding administration."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.crypto import CredentialCryptoError
from backend.models.connector_reply import (
    ConnectorBindingChallenge,
    ConnectorInstallation,
    ConnectorPrincipalBinding,
)
from backend.schemas.connector_reply import (
    ConnectorBindingChallengeRead,
    ConnectorBindingRead,
    ConnectorInstallationCreate,
    ConnectorInstallationHealth,
    ConnectorInstallationRead,
    ConnectorInstallationUpdate,
)
from backend.security.identity import RequestIdentity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)


class ConnectorCredentialUnavailableError(RuntimeError):
    """Stored connector verification material cannot be used safely."""


@dataclass(frozen=True)
class ConnectorInstallationCredentials:
    app_secret: str
    encrypt_key: str
    verification_token: str


def read_installation_credentials(
    row: ConnectorInstallation,
) -> ConnectorInstallationCredentials:
    """Decrypt all required credentials as one fail-closed boundary."""

    if not all(
        (row._app_secret_encrypted, row._encrypt_key_encrypted, row._verification_token_encrypted)
    ):
        raise ConnectorCredentialUnavailableError("credential_unavailable")
    try:
        credentials = ConnectorInstallationCredentials(
            app_secret=row.app_secret,
            encrypt_key=row.encrypt_key,
            verification_token=row.verification_token,
        )
    except (CredentialCryptoError, UnicodeError) as exc:
        raise ConnectorCredentialUnavailableError("credential_unavailable") from exc
    if not all(
        value.strip()
        for value in (
            credentials.app_secret,
            credentials.encrypt_key,
            credentials.verification_token,
        )
    ):
        raise ConnectorCredentialUnavailableError("credential_unavailable")
    return credentials


def read_installation(row: ConnectorInstallation) -> ConnectorInstallationRead:
    return ConnectorInstallationRead(
        installation_public_id=row.public_id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        name=row.name,
        app_id=row.app_id,
        tenant_key=row.tenant_key,
        status=row.status,
        config_revision=row.config_revision,
        has_app_secret=bool(row._app_secret_encrypted),
        has_encrypt_key=bool(row._encrypt_key_encrypted),
        has_verification_token=bool(row._verification_token_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _installation(
    db: AsyncSession, workspace_id: str, public_id: str, *, lock: bool = False
) -> ConnectorInstallation:
    query = select(ConnectorInstallation).where(
        ConnectorInstallation.workspace_id == workspace_id,
        ConnectorInstallation.public_id == public_id,
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector installation not found")
    return row


async def list_installations(
    db: AsyncSession, workspace_id: str, identity: RequestIdentity
) -> list[ConnectorInstallationRead]:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    rows = (
        await db.scalars(
            select(ConnectorInstallation)
            .where(ConnectorInstallation.workspace_id == workspace_id)
            .order_by(ConnectorInstallation.created_at)
        )
    ).all()
    return [read_installation(row) for row in rows]


async def create_installation(
    db: AsyncSession,
    workspace_id: str,
    identity: RequestIdentity,
    body: ConnectorInstallationCreate,
) -> ConnectorInstallationRead:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    row = ConnectorInstallation(
        public_id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        provider="feishu",
        name=body.name,
        app_id=body.app_id,
        tenant_key=body.tenant_key,
        status="active",
    )
    row.app_secret = body.app_secret
    row.encrypt_key = body.encrypt_key
    row.verification_token = body.verification_token
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Connector installation already exists"
        ) from exc
    return read_installation(row)


async def update_installation(
    db: AsyncSession,
    workspace_id: str,
    public_id: str,
    identity: RequestIdentity,
    body: ConnectorInstallationUpdate,
) -> ConnectorInstallationRead:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    row = await _installation(db, workspace_id, public_id, lock=True)
    if row.revoked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Revoked installation cannot be changed")
    if body.name is not None:
        row.name = body.name
    if body.app_secret is not None:
        row.app_secret = body.app_secret
    if body.encrypt_key is not None:
        row.encrypt_key = body.encrypt_key
    if body.verification_token is not None:
        row.verification_token = body.verification_token
    if body.enabled is not None:
        row.status = "active" if body.enabled else "disabled"
    row.config_revision += 1
    row.last_error_code = None
    await db.flush()
    return read_installation(row)


async def installation_health(
    db: AsyncSession, workspace_id: str, public_id: str, identity: RequestIdentity
) -> ConnectorInstallationHealth:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    row = await _installation(db, workspace_id, public_id)
    try:
        read_installation_credentials(row)
        credentials_ready = True
    except ConnectorCredentialUnavailableError:
        credentials_ready = False
    callback_ready = row.status == "active" and credentials_ready
    return ConnectorInstallationHealth(
        installation_public_id=row.public_id,
        status=row.status if credentials_ready else "blocked",
        callback_ready=callback_ready,
        binding_ready=callback_ready,
        last_ready_at=row.last_ready_at,
        last_error_code=(row.last_error_code if credentials_ready else "credential_unavailable"),
    )


async def get_my_binding(
    db: AsyncSession,
    workspace_id: str,
    installation_public_id: str,
    identity: RequestIdentity,
) -> ConnectorBindingRead | None:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    installation = await _installation(db, workspace_id, installation_public_id)
    row = await db.scalar(
        select(ConnectorPrincipalBinding).where(
            ConnectorPrincipalBinding.installation_id == installation.id,
            ConnectorPrincipalBinding.workspace_id == workspace_id,
            ConnectorPrincipalBinding.user_id == access.user_id,
        )
    )
    if row is None:
        return None
    return ConnectorBindingRead(
        binding_public_id=row.public_id,
        installation_public_id=installation.public_id,
        user_id=row.user_id,
        active=row.active,
        revoked_at=row.revoked_at,
        updated_at=row.updated_at,
    )


async def create_binding_challenge(
    db: AsyncSession, workspace_id: str, public_id: str, identity: RequestIdentity
) -> ConnectorBindingChallengeRead:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    installation = await _installation(db, workspace_id, public_id)
    if installation.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Connector installation is not active")
    code = secrets.token_urlsafe(12)
    row = ConnectorBindingChallenge(
        public_id=str(uuid.uuid4()),
        installation_id=installation.id,
        workspace_id=workspace_id,
        user_id=access.user_id,
        challenge_digest=hashlib.sha256(code.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        attempts=0,
    )
    db.add(row)
    await db.flush()
    return ConnectorBindingChallengeRead(
        challenge_public_id=row.public_id,
        installation_public_id=public_id,
        command_text=f"绑定 {code}",
        expires_at=row.expires_at,
    )


async def revoke_binding(
    db: AsyncSession, workspace_id: str, binding_public_id: str, identity: RequestIdentity
) -> ConnectorBindingRead:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    row = await db.scalar(
        select(ConnectorPrincipalBinding)
        .where(
            ConnectorPrincipalBinding.workspace_id == workspace_id,
            ConnectorPrincipalBinding.public_id == binding_public_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector binding not found")
    if row.user_id != access.user_id:
        require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    if row.active:
        row.active = False
        row.revision += 1
        row.revoked_at = datetime.now(UTC)
        row.revoked_by_user_id = access.user_id
        await db.flush()
    installation = await db.get(ConnectorInstallation, row.installation_id)
    assert installation is not None
    return ConnectorBindingRead(
        binding_public_id=row.public_id,
        installation_public_id=installation.public_id,
        user_id=row.user_id,
        active=row.active,
        revoked_at=row.revoked_at,
        updated_at=row.updated_at,
    )
