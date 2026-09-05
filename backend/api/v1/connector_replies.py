"""Connector installation administration and strict public callback endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.database import get_db
from backend.models.connector_reply import ConnectorInstallation
from backend.schemas.common import ApiResponse
from backend.schemas.connector_reply import (
    ConnectorBindingChallengeRead,
    ConnectorBindingRead,
    ConnectorInstallationCreate,
    ConnectorInstallationHealth,
    ConnectorInstallationRead,
    ConnectorInstallationUpdate,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import connector_installation_service as installations
from backend.services.feishu_connector_runtime import dispatch_feishu_callback

router = APIRouter(tags=["connector-replies"])
MAX_CALLBACK_BODY_BYTES = 256 * 1024


@router.get(
    "/workspaces/{workspace_id}/connector-installations",
    response_model=ApiResponse[list[ConnectorInstallationRead]],
)
async def list_connector_installations(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse.ok(await installations.list_installations(db, workspace_id, identity))


@router.post(
    "/workspaces/{workspace_id}/connector-installations",
    response_model=ApiResponse[ConnectorInstallationRead],
    status_code=201,
)
async def create_connector_installation(
    workspace_id: str,
    body: ConnectorInstallationCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse.ok(await installations.create_installation(db, workspace_id, identity, body))


@router.patch(
    "/workspaces/{workspace_id}/connector-installations/{installation_public_id}",
    response_model=ApiResponse[ConnectorInstallationRead],
)
async def update_connector_installation(
    workspace_id: str,
    installation_public_id: str,
    body: ConnectorInstallationUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse.ok(
        await installations.update_installation(
            db, workspace_id, installation_public_id, identity, body
        )
    )


@router.get(
    "/workspaces/{workspace_id}/connector-installations/{installation_public_id}/health",
    response_model=ApiResponse[ConnectorInstallationHealth],
)
async def get_connector_installation_health(
    workspace_id: str,
    installation_public_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse.ok(
        await installations.installation_health(db, workspace_id, installation_public_id, identity)
    )


@router.post(
    "/workspaces/{workspace_id}/connector-installations/{installation_public_id}/binding-challenges",
    response_model=ApiResponse[ConnectorBindingChallengeRead],
    status_code=201,
)
async def create_connector_binding_challenge(
    workspace_id: str,
    installation_public_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse.ok(
        await installations.create_binding_challenge(
            db, workspace_id, installation_public_id, identity
        )
    )


@router.delete(
    "/workspaces/{workspace_id}/connector-bindings/{binding_public_id}",
    response_model=ApiResponse[ConnectorBindingRead],
)
async def revoke_connector_binding(
    workspace_id: str,
    binding_public_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse.ok(
        await installations.revoke_binding(db, workspace_id, binding_public_id, identity)
    )


@router.post("/connectors/feishu/installations/{installation_public_id}/events")
async def receive_feishu_event(
    installation_public_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_CALLBACK_BODY_BYTES:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE, "Connector callback body too large"
                )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Content-Length") from exc
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_CALLBACK_BODY_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, "Connector callback body too large"
            )
        chunks.append(chunk)
    body = b"".join(chunks)
    installation = await db.scalar(
        select(ConnectorInstallation).where(
            ConnectorInstallation.public_id == installation_public_id,
            ConnectorInstallation.provider == "feishu",
            ConnectorInstallation.status == "active",
            ConnectorInstallation.revoked_at.is_(None),
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector installation not found")
    if db.bind is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Connector persistence unavailable"
        )
    session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    result = await dispatch_feishu_callback(
        installation=installation,
        session_factory=session_factory,
        uri=str(request.url.path),
        headers={key.lower(): value for key, value in request.headers.items()},
        body=body,
    )
    return Response(content=result.content, status_code=result.status_code, headers=result.headers)


__all__ = ["router"]
