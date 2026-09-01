"""Request identity, first-run admin setup, and local password auth."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.database import get_db
from backend.models.identity import (
    LocalCredential,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from backend.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    SetupRequest,
    SetupStatusRead,
    TokenRead,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity, issue_local_token
from backend.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=ApiResponse[dict])
async def read_identity(
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
) -> ApiResponse:
    return ApiResponse.ok(
        {
            "subject": identity.subject,
            "email": identity.email,
            "name": identity.name,
            "username": identity.username,
            "picture": identity.picture,
            "is_platform_admin": identity.is_platform_admin,
            "auth_method": identity.auth_method,
        }
    )


@router.get("/setup-status", response_model=ApiResponse[SetupStatusRead])
async def read_setup_status(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """setup_required is true iff local password auth has never been bootstrapped
    on this instance — independent of whether OIDC users already exist."""
    existing = await db.scalar(select(LocalCredential.id).limit(1))
    return ApiResponse.ok(SetupStatusRead(setup_required=existing is None))


@router.post("/setup", response_model=ApiResponse[TokenRead], status_code=status.HTTP_201_CREATED)
async def create_initial_admin(
    body: SetupRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Create the first local admin account. Deliberately unauthenticated —
    this IS the bootstrap path replacing "know the BOOTSTRAP_ADMIN_TOKEN, then
    call the platform API by hand" — but only while no local account exists
    yet. Re-checking immediately before the write narrows (does not fully
    eliminate) the race between two concurrent first-run setup calls; that's
    an acceptable tradeoff for a path meant to fire once, by one deployer.
    """
    if await db.scalar(select(LocalCredential.id).limit(1)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup already completed")

    email = body.email.strip().lower()
    local_email_taken = await db.scalar(
        select(User.id)
        .join(LocalCredential, LocalCredential.user_id == User.id)
        .where(User.email == email)
    )
    if local_email_taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")

    user = User(subject=f"local:{uuid4()}", email=email, display_name=body.display_name)
    db.add(user)
    await db.flush()

    workspace = await db.scalar(select(Workspace).order_by(Workspace.created_at).limit(1))
    if workspace is None:
        workspace = Workspace(name="Default", slug="default")
        db.add(workspace)
        await db.flush()

    db.add(
        WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.ADMIN)
    )
    db.add(LocalCredential(user_id=user.id, password_hash=hash_password(body.password)))
    await db.commit()

    token = issue_local_token(user, secret_key=get_settings().secret_key)
    return ApiResponse.ok(TokenRead(token=token))


@router.post("/login", response_model=ApiResponse[TokenRead])
async def login_with_password(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    email = body.email.strip().lower()
    result = (
        await db.execute(
            select(User, LocalCredential)
            .join(LocalCredential, LocalCredential.user_id == User.id)
            .where(User.email == email)
        )
    ).one_or_none()
    if (
        result is None
        or result.User.disabled
        or not verify_password(body.password, result.LocalCredential.password_hash)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    token = issue_local_token(result.User, secret_key=get_settings().secret_key)
    return ApiResponse.ok(TokenRead(token=token))


@router.post("/change-password", response_model=ApiResponse[None])
async def change_password(
    body: ChangePasswordRequest,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    if identity.auth_method != "local":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only local accounts have a password to change"
        )
    result = (
        await db.execute(
            select(User, LocalCredential)
            .join(LocalCredential, LocalCredential.user_id == User.id)
            .where(User.subject == identity.subject)
        )
    ).one_or_none()
    if result is None or not verify_password(
        body.current_password, result.LocalCredential.password_hash
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    result.LocalCredential.password_hash = hash_password(body.new_password)
    await db.commit()
    return ApiResponse.ok(None)
