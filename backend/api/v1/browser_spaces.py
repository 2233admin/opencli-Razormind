"""Workspace-scoped Browser Space lifecycle and task routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.browser_space import (
    BrowserSpaceCreate,
    BrowserSpaceEventRead,
    BrowserSpaceRead,
    BrowserSpaceTaskCreate,
    BrowserSpaceTaskRead,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity, is_platform_admin
from backend.security.workspace_rbac import (
    WorkspaceAccess,
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services import browser_space_service


class BrowserSpaceRoute(APIRoute):
    """Validation errors must not echo rejected secret-bearing request inputs."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_validation(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_request"
                ) from exc

        return bounded_validation


router = APIRouter(
    prefix="/workspaces/{workspace_id}/browser-spaces",
    tags=["browser-spaces"],
    route_class=BrowserSpaceRoute,
)


def _safe_payload(value: Any) -> Any:
    """Return a bounded, secret-free representation suitable for an API response."""
    return browser_space_service._safe_result(value)


def _space_read(space: Any, latest_task: Any = None) -> BrowserSpaceRead:
    result = BrowserSpaceRead.model_validate(space)
    if latest_task is not None:
        result.latest_task = _task_read(latest_task)
        if latest_task.status in {"queued", "running"}:
            result.active_task = result.latest_task
    return result


def _task_read(task: Any) -> BrowserSpaceTaskRead:
    return BrowserSpaceTaskRead(
        space_id=str(task.space_id),
        task_id=str(task.id),
        operation_id=str(task.operation_id),
        capability=getattr(task, "capability", None),
        status=task.status,
        result=_safe_payload(task.result) if task.result is not None else None,
        error=getattr(task, "error_code", None),
    )


def _event_read(event: Any) -> BrowserSpaceEventRead:
    return BrowserSpaceEventRead(
        id=str(event.id),
        space_id=str(event.space_id),
        task_id=str(event.task_id) if event.task_id is not None else None,
        sequence=event.sequence,
        kind=event.kind,
        payload=_safe_payload(event.payload) or {},
        created_at=event.created_at,
    )


def _service_error(exc: browser_space_service.BrowserSpaceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def _require_owner_or_manager(
    space: Any, identity: RequestIdentity, access: WorkspaceAccess
) -> None:
    """Allow the owner or a Workspace manager to mutate a Space."""
    role = getattr(access.role, "value", access.role)
    if role in {"admin", "maintainer"}:
        return
    if space.owner_id in {identity.subject, access.user_id}:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Browser Space owner permission required")


def _space_identity(identity: RequestIdentity, access: WorkspaceAccess) -> RequestIdentity | None:
    role = getattr(access.role, "value", access.role)
    return None if role in {"admin", "maintainer"} else identity


@router.get("", response_model=ApiResponse[list[BrowserSpaceRead]])
async def list_browser_spaces(
    workspace_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    try:
        spaces = await browser_space_service.list_spaces(db, workspace_id, identity, limit=limit)
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    return ApiResponse.ok([_space_read(space) for space in spaces])


@router.get("/instances", response_model=ApiResponse[list[dict[str, Any]]])
async def list_available_browser_instances(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    if not is_platform_admin(identity):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_admin_required")
    return ApiResponse.ok(await browser_space_service.available_instances(db))


@router.post("", response_model=ApiResponse[BrowserSpaceRead], status_code=status.HTTP_201_CREATED)
async def create_browser_space(
    workspace_id: str,
    body: BrowserSpaceCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    if not is_platform_admin(identity):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_admin_required")
    if body.owner_type == "operator" and body.owner_id != identity.subject:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Browser Space owner permission required")
    try:
        space = await browser_space_service.create_space(db, workspace_id, body, identity)
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    return ApiResponse.ok(_space_read(space))


@router.get("/{space_id}", response_model=ApiResponse[BrowserSpaceRead])
async def get_browser_space(
    workspace_id: str,
    space_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    try:
        space = await browser_space_service.get_space(
            db, workspace_id, space_id, _space_identity(identity, access)
        )
        active_task = await browser_space_service.get_latest_task(db, space.id, active_only=False)
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    return ApiResponse.ok(_space_read(space, active_task))


@router.post("/{space_id}/tasks", response_model=ApiResponse[BrowserSpaceTaskRead])
async def submit_browser_space_task(
    workspace_id: str,
    space_id: str,
    body: BrowserSpaceTaskCreate,
    response: Response,
    background_tasks: BackgroundTasks,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.RUN_OPERATIONS_AGENTS)
    try:
        space = await browser_space_service.get_space(
            db, workspace_id, space_id, _space_identity(identity, access)
        )
        _require_owner_or_manager(space, identity, access)
        task, created = await browser_space_service.submit_task(
            db,
            workspace_id,
            space_id,
            body,
            _space_identity(identity, access),
            execute=False,
        )
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    if created:
        gate_authorized = is_platform_admin(identity)
        background_tasks.add_task(
            browser_space_service.execute_task_in_background,
            str(task.id),
            body.args,
            body.timeout_seconds,
            body.gate,
            gate_authorized,
        )
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return ApiResponse.ok(_task_read(task))


@router.post("/{space_id}/cancel", response_model=ApiResponse[BrowserSpaceTaskRead])
async def cancel_browser_space(
    workspace_id: str,
    space_id: str,
    response: Response,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.RUN_OPERATIONS_AGENTS)
    try:
        space = await browser_space_service.get_space(
            db, workspace_id, space_id, _space_identity(identity, access)
        )
        _require_owner_or_manager(space, identity, access)
        task = await browser_space_service.cancel_space(
            db, workspace_id, space_id, _space_identity(identity, access)
        )
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    response.status_code = status.HTTP_200_OK
    return ApiResponse.ok(_task_read(task))


@router.post("/{space_id}/close", response_model=ApiResponse[BrowserSpaceRead])
async def close_browser_space(
    workspace_id: str,
    space_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.MANAGE_CONFIGURATION)
    try:
        space = await browser_space_service.get_space(
            db, workspace_id, space_id, _space_identity(identity, access)
        )
        _require_owner_or_manager(space, identity, access)
        closed = await browser_space_service.close_space(
            db, workspace_id, space_id, _space_identity(identity, access)
        )
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    return ApiResponse.ok(_space_read(closed))


@router.get("/{space_id}/events", response_model=ApiResponse[list[BrowserSpaceEventRead]])
async def replay_browser_space_events(
    workspace_id: str,
    space_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    try:
        events = await browser_space_service.list_events(
            db,
            workspace_id,
            space_id,
            _space_identity(identity, access),
            after_sequence=after_sequence,
            limit=limit,
        )
    except browser_space_service.BrowserSpaceError as exc:
        raise _service_error(exc) from exc
    return ApiResponse.ok([_event_read(event) for event in events])
