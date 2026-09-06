import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.api.v1.chat import ChatReply
from backend.models.agent_conversation import AgentConversationTurn
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.provider import ModelProvider
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import agent_conversation_service as conversation_service


async def _seed_member(db_session, subject: str, slug: str):
    user = User(subject=subject)
    workspace = Workspace(name=slug, slug=slug)
    provider = ModelProvider(
        name=f"{slug} provider",
        provider_type="openai",
        default_model="test-model",
        enabled=True,
    )
    db_session.add_all((user, workspace, provider))
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.ADMIN)
    )
    await db_session.commit()
    return workspace


@pytest.mark.asyncio
async def test_session_create_restore_message_and_idempotency(client, db_session, monkeypatch):
    workspace = await _seed_member(db_session, "session-user", "session-workspace")

    async def identity_override():
        return RequestIdentity(subject="session-user")

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    calls = 0

    async def fake_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ApiResponse.ok(ChatReply(type="message", content="persisted reply"))

    monkeypatch.setattr(conversation_service.chat, "run_chat_request", fake_chat)

    created = await client.post(
        "/api/v1/chat/sessions", json={"workspace_id": workspace.id, "context": {"surface": "home"}}
    )
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]
    payload = {"request_id": "same-request", "content": "hello", "context": {"surface": "project"}}
    first = await client.post(f"/api/v1/chat/sessions/{conversation_id}/messages", json=payload)
    second = await client.post(f"/api/v1/chat/sessions/{conversation_id}/messages", json=payload)
    restored = await client.get(f"/api/v1/chat/sessions/{conversation_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1
    assert restored.json()["data"]["turns"][0]["sequence"] == 1
    assert restored.json()["data"]["turns"][0]["response"]["content"] == "persisted reply"
    assert restored.json()["data"]["turns"][0]["context_binding"] == {"surface": "project"}


@pytest.mark.asyncio
async def test_cross_workspace_read_is_denied_and_model_failure_is_durable(
    client, db_session, monkeypatch
):
    workspace_a = await _seed_member(db_session, "session-a", "workspace-a")
    await _seed_member(db_session, "session-b", "workspace-b")
    subject = "session-a"

    async def identity_override():
        return RequestIdentity(subject=subject)

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    created = await client.post("/api/v1/chat/sessions", json={"workspace_id": workspace_a.id})
    conversation_id = created.json()["data"]["id"]
    subject = "session-b"
    denied = await client.get(f"/api/v1/chat/sessions/{conversation_id}")
    assert denied.status_code == 403

    subject = "session-a"

    async def failing_chat(*args, **kwargs):
        raise HTTPException(status_code=502, detail="provider unavailable")

    monkeypatch.setattr(conversation_service.chat, "run_chat_request", failing_chat)
    failed = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={"request_id": "failure", "content": "hello"},
    )
    turn = await db_session.scalar(
        select(AgentConversationTurn).where(AgentConversationTurn.request_id == "failure")
    )
    assert failed.status_code == 502
    assert turn is not None
    assert turn.status == "failed"
    assert turn.error_code == "model_error"


@pytest.mark.asyncio
async def test_workspace_member_cannot_take_over_another_users_conversation(client, db_session):
    workspace = await _seed_member(db_session, "creator", "shared-workspace")
    other = User(subject="other-member")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=other.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    await db_session.commit()
    subject = "creator"

    async def identity_override():
        return RequestIdentity(subject=subject)

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    created = await client.post("/api/v1/chat/sessions", json={"workspace_id": workspace.id})
    subject = "other-member"
    message = await client.post(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/messages",
        json={"request_id": "take-over", "content": "do not attach me"},
    )
    closed = await client.post(f"/api/v1/chat/sessions/{created.json()['data']['id']}/close")
    assert message.status_code == 403
    assert closed.status_code == 403
    assert (
        await db_session.scalar(
            select(AgentConversationTurn).where(
                AgentConversationTurn.conversation_id == created.json()["data"]["id"]
            )
        )
        is None
    )
