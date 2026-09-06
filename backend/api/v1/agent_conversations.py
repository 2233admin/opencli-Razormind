"""REST API for persistent Global Agent conversations."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent_conversation import AgentConversationTurn
from backend.schemas.agent_conversation import (
    AgentConversationBackgroundMessageRead,
    AgentConversationCreate,
    AgentConversationDetail,
    AgentConversationMessageCreate,
    AgentConversationMessageRead,
    AgentConversationRead,
    AgentConversationRunEventRead,
    AgentConversationRunEventsRead,
    AgentConversationRunRead,
    AgentConversationTurnRead,
    AgentExecutionTargetsRead,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import agent_conversation_run_service as run_service
from backend.services import agent_conversation_service as service

router = APIRouter(prefix="/chat", tags=["agent-conversations"])


def _run_read(run: object) -> AgentConversationRunRead:
    return AgentConversationRunRead.model_validate(
        {
            "id": run.id,
            "status": run.status,
            "continuation": run_service.continuation_payload(run),
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }
    )


def _detail(conversation: object, turns: list[object]) -> AgentConversationDetail:
    data = AgentConversationRead.model_validate(conversation).model_dump()
    data["turns"] = [AgentConversationTurnRead.model_validate(turn) for turn in turns]
    return AgentConversationDetail.model_validate(data)


@router.post(
    "/sessions",
    response_model=ApiResponse[AgentConversationRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: AgentConversationCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    conversation = await service.create_conversation(
        db,
        identity,
        workspace_id=body.workspace_id,
        title=body.title,
        context=body.context,
        execution_target_id=body.execution_target_id,
        model_id=body.model_id,
    )
    return ApiResponse.ok(AgentConversationRead.model_validate(conversation))


@router.get("/sessions", response_model=ApiResponse[list[AgentConversationRead]])
async def list_sessions(
    workspace_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None, min_length=1, max_length=255),
    workflow_id: str | None = Query(default=None, min_length=1, max_length=255),
    run_id: str | None = Query(default=None, min_length=1, max_length=255),
    include_project_sessions: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=50),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    context = {
        key: value
        for key, value in {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
        }.items()
        if value is not None
    }
    rows = await service.list_conversations(
        db,
        identity,
        workspace_id=workspace_id,
        limit=limit,
        context=context,
        include_project_sessions=include_project_sessions,
    )
    return ApiResponse.ok([AgentConversationRead.model_validate(row) for row in rows])


@router.get(
    "/execution-targets",
    response_model=ApiResponse[AgentExecutionTargetsRead],
)
async def list_execution_targets(
    request: Request,
    workspace_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None, min_length=1, max_length=255),
    workflow_id: str | None = Query(default=None, min_length=1, max_length=255),
    run_id: str | None = Query(default=None, min_length=1, max_length=255),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    context = {
        key: value
        for key, value in {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
        }.items()
        if value is not None
    }
    resolved_workspace_id, targets = await service.list_execution_targets(
        db,
        identity,
        workspace_id=workspace_id,
        context=context,
    )
    return ApiResponse.ok(
        AgentExecutionTargetsRead(
            workspace_id=resolved_workspace_id,
            background_execution=run_service.background_execution_readiness(
                request.app.state.connector_settings
            ),
            targets=targets,
        )
    )


async def _get_detail(
    conversation_id: str,
    after_sequence: int,
    limit: int,
    identity: RequestIdentity,
    db: AsyncSession,
) -> AgentConversationDetail:
    conversation, turns = await service.get_conversation(
        db,
        identity,
        conversation_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return _detail(conversation, turns)


@router.get(
    "/sessions/{conversation_id}",
    response_model=ApiResponse[AgentConversationDetail],
)
async def get_session(
    conversation_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    return ApiResponse.ok(await _get_detail(conversation_id, after_sequence, limit, identity, db))


@router.get(
    "/sessions/{conversation_id}/replay",
    response_model=ApiResponse[AgentConversationDetail],
)
async def replay_session(
    conversation_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    return ApiResponse.ok(await _get_detail(conversation_id, after_sequence, limit, identity, db))


@router.post(
    "/sessions/{conversation_id}/messages",
    response_model=ApiResponse[
        AgentConversationMessageRead | AgentConversationBackgroundMessageRead
    ],
)
async def send_session_message(
    conversation_id: str,
    body: AgentConversationMessageCreate,
    response: Response,
    request: Request,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    if body.execution_mode == "background":
        readiness = run_service.background_execution_readiness(request.app.state.connector_settings)
        if readiness["status"] != "ready":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": readiness["reason_code"],
                    "message": readiness["reason"],
                },
            )
        conversation, turn, run = await service.queue_message(
            db,
            identity,
            conversation_id,
            request_id=body.request_id,
            content=body.content,
            context=body.context,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return ApiResponse.ok(
            AgentConversationBackgroundMessageRead(
                conversation_id=conversation.id,
                turn=AgentConversationTurnRead.model_validate(turn),
                run=_run_read(run),
            )
        )
    conversation, turn = await service.send_message(
        db,
        identity,
        conversation_id,
        request_id=body.request_id,
        content=body.content,
        context=body.context,
    )
    return ApiResponse.ok(
        AgentConversationMessageRead(
            conversation_id=conversation.id,
            turn=AgentConversationTurnRead.model_validate(turn),
        )
    )


@router.get(
    "/sessions/{conversation_id}/turns/{turn_id}/events",
    response_model=ApiResponse[AgentConversationRunEventsRead],
)
async def get_session_turn_events(
    conversation_id: str,
    turn_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await service.get_conversation(db, identity, conversation_id, limit=1)
    turn = await db.scalar(
        select(AgentConversationTurn).where(
            AgentConversationTurn.id == turn_id,
            AgentConversationTurn.conversation_id == conversation_id,
        )
    )
    if turn is None or not turn.agent_run_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent conversation run not found")
    run, events = await run_service.get_run_events(
        db,
        run_id=turn.agent_run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    next_sequence = events[-1].sequence if events else after_sequence
    return ApiResponse.ok(
        AgentConversationRunEventsRead(
            run=_run_read(run),
            events=[
                AgentConversationRunEventRead(
                    sequence=event.sequence,
                    type=event.event_type,
                    payload=event.payload,
                    created_at=event.created_at,
                )
                for event in events
            ],
            next_sequence=next_sequence,
            terminal=run.status in run_service.TERMINAL_RUN_STATUSES,
        )
    )


@router.post(
    "/sessions/{conversation_id}/close",
    response_model=ApiResponse[AgentConversationRead],
)
async def close_session(
    conversation_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    conversation = await service.close_conversation(db, identity, conversation_id)
    return ApiResponse.ok(AgentConversationRead.model_validate(conversation))


@router.post(
    "/sessions/{conversation_id}/reopen",
    response_model=ApiResponse[AgentConversationRead],
)
async def reopen_session(
    conversation_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    conversation = await service.reopen_conversation(db, identity, conversation_id)
    return ApiResponse.ok(AgentConversationRead.model_validate(conversation))
