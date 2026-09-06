from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.api.v1.chat import ApiResponse, ChatReply, Proposal
from backend.llm.base import LlmAdapterError
from backend.models import (
    AgentConversation,
    AgentConversationTurn,
    AgentRunEvent,
    ModelProvider,
    Project,
    StudioProject,
    StudioWorkspace,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from backend.security.identity import RequestIdentity
from backend.services import agent_conversation_service as service


async def _identity_and_workspace(db_session, subject: str = "agent@example.test"):
    user = User(subject=subject)
    workspace = Workspace(name="Workspace", slug=subject.split("@")[0])
    provider = ModelProvider(
        name=f"{subject} provider",
        provider_type="openai",
        default_model="test-model",
        enabled=True,
    )
    db_session.add_all([user, workspace, provider])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            user_id=user.id,
            workspace_id=workspace.id,
            role=WorkspaceRole.OPERATOR,
        )
    )
    await db_session.commit()
    return RequestIdentity(subject=subject), workspace, user


@pytest.mark.asyncio
async def test_bounded_history_rejects_overflow_instead_of_truncating():
    turns = [
        SimpleNamespace(
            user_content="u" * 2_000,
            response={"type": "message", "content": "a" * 2_000},
        )
        for _ in range(20)
    ]

    with pytest.raises(service.AgentConversationError, match="start a new session"):
        service.bounded_history(turns, "current" * 3_000)


@pytest.mark.asyncio
async def test_context_binding_rejects_cross_workspace_project(db_session):
    identity, workspace, _ = await _identity_and_workspace(db_session, "owner@example.test")
    other_identity, other_workspace, other_user = await _identity_and_workspace(
        db_session, "other@example.test"
    )
    project = Project(
        workspace_id=other_workspace.id,
        name="Other Project",
        slug="other-project",
        created_by_user_id=other_user.id,
    )
    db_session.add(project)
    await db_session.commit()

    with pytest.raises(service.AgentConversationError):
        await service.validate_context_binding(db_session, workspace.id, {"project_id": project.id})


@pytest.mark.asyncio
async def test_unsafe_content_is_rejected_before_persistence(db_session):
    identity, workspace, _ = await _identity_and_workspace(db_session)
    conversation = await service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title=None,
        context=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.send_message(
            db_session,
            identity,
            conversation.id,
            request_id="unsafe-1",
            content="please use api_key=do-not-store",
            context=None,
            chat_runner=lambda *args, **kwargs: None,
        )
    assert exc_info.value.status_code == 400
    assert (
        await db_session.scalar(
            select(AgentConversationTurn).where(
                AgentConversationTurn.conversation_id == conversation.id
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_conversation_metadata_rejects_credential_like_values(db_session):
    identity, workspace, _ = await _identity_and_workspace(db_session, "metadata@example.test")

    with pytest.raises(HTTPException) as title_error:
        await service.create_conversation(
            db_session,
            identity,
            workspace_id=workspace.id,
            title="token=do-not-store",
            context=None,
        )
    assert title_error.value.status_code == 409

    with pytest.raises(HTTPException) as context_error:
        await service.create_conversation(
            db_session,
            identity,
            workspace_id=workspace.id,
            title="safe title",
            context={"surface": "Authorization: Bearer do-not-store"},
        )
    assert context_error.value.status_code == 409


@pytest.mark.asyncio
async def test_failed_model_call_is_persisted_and_duplicate_is_idempotent(db_session):
    identity, workspace, _ = await _identity_and_workspace(db_session)
    conversation = await service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title=None,
        context=None,
    )
    calls = 0

    async def failing_runner(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise LlmAdapterError("provider failed", retryable=True)

    with pytest.raises(HTTPException) as exc_info:
        await service.send_message(
            db_session,
            identity,
            conversation.id,
            request_id="retry-1",
            content="hello",
            context=None,
            chat_runner=failing_runner,
        )
    assert exc_info.value.status_code == 502
    turn = await db_session.scalar(
        select(AgentConversationTurn).where(
            AgentConversationTurn.conversation_id == conversation.id,
            AgentConversationTurn.request_id == "retry-1",
        )
    )
    assert turn is not None
    assert turn.status == "failed"
    assert turn.error_code == "model_unavailable"
    with pytest.raises(HTTPException):
        await service.send_message(
            db_session,
            identity,
            conversation.id,
            request_id="retry-1",
            content="hello",
            context=None,
            chat_runner=failing_runner,
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_proposal_response_is_stored_without_execution(db_session, monkeypatch):
    identity, workspace, _ = await _identity_and_workspace(db_session)
    conversation = await service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title=None,
        context=None,
    )
    executed = False

    async def runner(*args, **kwargs):
        return ApiResponse.ok(
            ChatReply(
                type="proposal",
                proposal=Proposal(
                    tool="update_provider",
                    args={"provider_id": "opaque"},
                    summary="Change provider",
                    diff="provider enabled",
                    workspace_id=workspace.id,
                    work_item_id="work-item",
                    proposal_version="version",
                ),
            )
        )

    async def forbidden_execute(*args, **kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("session sends must not execute proposals")

    monkeypatch.setattr(service.agent_control_service, "execute_confirmed", forbidden_execute)
    _, turn = await service.send_message(
        db_session,
        identity,
        conversation.id,
        request_id="proposal-1",
        content="propose it",
        context=None,
        chat_runner=runner,
    )

    assert turn.status == "proposal"
    assert turn.response["type"] == "proposal"
    assert executed is False


@pytest.mark.asyncio
async def test_proposal_must_be_bound_to_conversation_workspace(db_session):
    identity, workspace, _ = await _identity_and_workspace(
        db_session, "proposal-bound@example.test"
    )
    _, other_workspace, _ = await _identity_and_workspace(db_session, "proposal-other@example.test")
    conversation = await service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title=None,
        context=None,
    )

    async def runner(*args, **kwargs):
        return ApiResponse.ok(
            ChatReply(
                type="proposal",
                proposal=Proposal(
                    tool="update_provider",
                    args={"provider_id": "opaque"},
                    summary="Change provider",
                    diff="provider enabled",
                    workspace_id=other_workspace.id,
                    work_item_id="work-item",
                    proposal_version="version",
                ),
            )
        )

    with pytest.raises(HTTPException) as exc_info:
        await service.send_message(
            db_session,
            identity,
            conversation.id,
            request_id="proposal-wrong-workspace",
            content="propose it",
            context=None,
            chat_runner=runner,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_close_conversation_uses_row_lock_before_turn_insertion(db_session):
    identity, workspace, _ = await _identity_and_workspace(db_session, "close-lock@example.test")
    conversation = await service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title=None,
        context=None,
    )
    captured = {}

    async def runner(model_db, body, actor, **kwargs):
        captured["workspace_id"] = body.workspace_id
        return ApiResponse.ok(ChatReply(type="message", content="done"))

    await service.send_message(
        db_session,
        identity,
        conversation.id,
        request_id="close-lock-1",
        content="hello",
        context=None,
        chat_runner=runner,
    )

    closed = await service.close_conversation(db_session, identity, conversation.id)

    assert captured["workspace_id"] == workspace.id
    assert closed.status == "closed"


@pytest.mark.asyncio
async def test_finalize_rechecks_membership_before_persisting_reply(db_session):
    identity, workspace, _ = await _identity_and_workspace(db_session, "finalize-auth@example.test")
    conversation = await service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title=None,
        context=None,
    )
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)

    async def revoke_during_model_wait(*args, **kwargs):
        activity_sink = service.chat._activity_sink.get()
        await activity_sink(
            {
                "type": "run.completed",
                "label": "premature",
                "detail": "must be discarded",
                "state": "completed",
            }
        )
        async with session_factory() as other_db:
            await other_db.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace.id)
            )
            await other_db.commit()
        return ApiResponse.ok(ChatReply(type="message", content="must not persist"))

    with pytest.raises(HTTPException) as exc_info:
        await service.send_message(
            db_session,
            identity,
            conversation.id,
            request_id="finalize-auth",
            content="wait then reply",
            context=None,
            chat_runner=revoke_during_model_wait,
        )

    assert exc_info.value.status_code == 403
    turn = await db_session.scalar(
        select(AgentConversationTurn).where(AgentConversationTurn.request_id == "finalize-auth")
    )
    assert turn.status == "failed"
    assert turn.active_slot is None
    assert turn.error_code == "authorization_changed"
    assert turn.response is None
    event_types = list(
        await db_session.scalars(
            select(AgentRunEvent.event_type).where(AgentRunEvent.run_id == turn.agent_run_id)
        )
    )
    assert "run.completed" not in event_types
    assert event_types[-1] == "run.failed"


@pytest.mark.asyncio
async def test_project_sessions_are_opt_in_and_reauthorized_per_candidate(db_session):
    identity, workspace, user = await _identity_and_workspace(
        db_session, "studio-list@example.test"
    )
    identity = RequestIdentity(
        subject=identity.subject,
        auth_method="local",
        is_platform_admin=True,
    )
    studio = StudioWorkspace(name="Studio", slug="studio-list")
    db_session.add(studio)
    await db_session.flush()
    project = StudioProject(
        workspace_id=studio.id,
        name="Project",
        slug="project",
        app_type="agent",
        created_by_user_id=user.id,
    )
    db_session.add(project)
    await db_session.flush()
    plain = AgentConversation(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        context_binding={},
        execution_binding={},
        status="active",
    )
    valid_project = AgentConversation(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        context_binding={
            "studio_workspace_id": studio.id,
            "project_id": project.id,
        },
        execution_binding={},
        status="active",
    )
    invalid_project = AgentConversation(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        context_binding={
            "studio_workspace_id": studio.id,
            "project_id": "missing-project",
        },
        execution_binding={},
        status="active",
    )
    db_session.add_all((plain, valid_project, invalid_project))
    await db_session.commit()

    default_rows = await service.list_conversations(
        db_session,
        identity,
        workspace_id=workspace.id,
        limit=10,
    )
    included_rows = await service.list_conversations(
        db_session,
        identity,
        workspace_id=workspace.id,
        limit=10,
        include_project_sessions=True,
    )

    assert {row.id for row in default_rows} == {plain.id}
    assert {row.id for row in included_rows} == {plain.id, valid_project.id}
