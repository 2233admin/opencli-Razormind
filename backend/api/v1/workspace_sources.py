from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.source_binding import Source, SourceRevision
from backend.schemas.common import ApiResponse
from backend.schemas.source_binding import (
    SourceCreate,
    SourceRead,
    SourceRevisionCreate,
    SourceRevisionRead,
    SourceUpdate,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import WorkspacePermission, get_workspace_access, require_permission

router = APIRouter(prefix="/workspaces/{workspace_id}/sources", tags=["sources"])


async def _get_source(db: AsyncSession, workspace_id: str, source_id: str, for_update: bool = False) -> Source:
    query = select(Source).where(Source.workspace_id == workspace_id, Source.id == source_id)
    if for_update:
        query = query.with_for_update()
    source = await db.scalar(query)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return source


@router.get("", response_model=ApiResponse[list[SourceRead]])
async def list_sources(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    rows = (await db.execute(
        select(Source).where(Source.workspace_id == workspace_id).order_by(Source.created_at)
    )).scalars().all()
    return ApiResponse.ok([SourceRead.model_validate(row) for row in rows])


@router.post("", response_model=ApiResponse[SourceRead], status_code=201)
async def create_source(
    workspace_id: str,
    body: SourceCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    source = Source(
        workspace_id=workspace_id,
        name=body.name,
        slug=body.slug,
        adapter_type=body.adapter_type,
        description=body.description,
        current_revision_number=1,
        created_by_user_id=access.user_id,
    )
    db.add(source)
    await db.flush()
    db.add(
        SourceRevision(
            source_id=source.id,
            revision_number=1,
            adapter_config=body.adapter_config,
            created_by_user_id=access.user_id,
        )
    )
    await db.flush()
    return ApiResponse.ok(SourceRead.model_validate(source))


@router.get("/{source_id}", response_model=ApiResponse[SourceRead])
async def get_source(
    workspace_id: str,
    source_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    source = await _get_source(db, workspace_id, source_id)
    return ApiResponse.ok(SourceRead.model_validate(source))


@router.patch("/{source_id}", response_model=ApiResponse[SourceRead])
async def update_source(
    workspace_id: str,
    source_id: str,
    body: SourceUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    source = await _get_source(db, workspace_id, source_id, for_update=True)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    await db.flush()
    return ApiResponse.ok(SourceRead.model_validate(source))


@router.get("/{source_id}/revisions", response_model=ApiResponse[list[SourceRevisionRead]])
async def list_source_revisions(
    workspace_id: str,
    source_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    await _get_source(db, workspace_id, source_id)
    rows = (await db.execute(
        select(SourceRevision)
        .where(SourceRevision.source_id == source_id)
        .order_by(SourceRevision.revision_number)
    )).scalars().all()
    return ApiResponse.ok([SourceRevisionRead.model_validate(row) for row in rows])


@router.post(
    "/{source_id}/revisions", response_model=ApiResponse[SourceRevisionRead], status_code=201
)
async def create_source_revision(
    workspace_id: str,
    source_id: str,
    body: SourceRevisionCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    source = await _get_source(db, workspace_id, source_id, for_update=True)
    next_revision = source.current_revision_number + 1
    revision = SourceRevision(
        source_id=source.id,
        revision_number=next_revision,
        adapter_config=body.adapter_config,
        created_by_user_id=access.user_id,
    )
    db.add(revision)
    source.current_revision_number = next_revision
    await db.flush()
    return ApiResponse.ok(SourceRevisionRead.model_validate(revision))
