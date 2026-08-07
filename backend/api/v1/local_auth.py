"""First-run local administrator setup and password login."""

from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.database import get_db
from backend.models.identity import LocalAdmin
from backend.schemas.common import ApiResponse
from backend.security.local_auth import (
    hash_password,
    issue_session,
    login_attempt_limiter,
    verify_password,
)

router = APIRouter(prefix="/auth/local", tags=["auth"])


class PasswordInput(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class SetupInput(PasswordInput):
    bootstrap_token: str = Field(min_length=1, max_length=1024)


async def _admin(db: AsyncSession) -> LocalAdmin | None:
    return (await db.execute(select(LocalAdmin))).scalar_one_or_none()


@router.get("/status", response_model=ApiResponse[dict])
async def local_status(db: Annotated[AsyncSession, Depends(get_db)]) -> ApiResponse:
    return ApiResponse.ok({"configured": await _admin(db) is not None})


@router.post("/setup", response_model=ApiResponse[dict])
async def setup_local_admin(
    body: SetupInput,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse:
    if await _admin(db) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Local administrator is already configured")
    expected = get_settings().bootstrap_admin_token
    client_id = request.client.host if request.client else "unknown"
    login_attempt_limiter.check(client_id)
    if not expected or not compare_digest(body.bootstrap_token, expected):
        login_attempt_limiter.record_failure(client_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid recovery credential")
    db.add(LocalAdmin(id="local-admin", password_hash=hash_password(body.password)))
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Local administrator is already configured"
        ) from exc
    login_attempt_limiter.reset(client_id)
    return ApiResponse.ok({"access_token": issue_session()})


@router.post("/login", response_model=ApiResponse[dict])
async def local_login(
    body: PasswordInput,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse:
    client_id = request.client.host if request.client else "unknown"
    login_attempt_limiter.check(client_id)
    admin = await _admin(db)
    if admin is None or not verify_password(body.password, admin.password_hash):
        login_attempt_limiter.record_failure(client_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid administrator password")
    login_attempt_limiter.reset(client_id)
    return ApiResponse.ok({"access_token": issue_session()})
