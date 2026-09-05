"""REST API for persistent Global Agent conversations."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.agent_conversation import (
    AgentConversationCreate,
    AgentConversationDetail,
    AgentConversationMessageCreate,
    AgentConversationMessageRead,
    AgentConversationRead,
    AgentConversationTurnRead,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import agent_conversation_service as service

router = APIRouter(prefix="/chat", tags=["agent-conversations"])


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
    )
    return ApiResponse.ok(AgentConversationRead.model_validate(conversation))


@router.get("/sessions", response_model=ApiResponse[list[AgentConversationRead]])
async def list_sessions(
    workspace_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None, min_length=1, max_length=255),
    workflow_id: str | None = Query(default=None, min_length=1, max_length=255),
    run_id: str | None = Query(default=None, min_length=1, max_length=255),
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
    )
    return ApiResponse.ok([AgentConversationRead.model_validate(row) for row in rows])


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
    response_model=ApiResponse[AgentConversationMessageRead],
)
async def send_session_message(
    conversation_id: str,
    body: AgentConversationMessageCreate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
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
