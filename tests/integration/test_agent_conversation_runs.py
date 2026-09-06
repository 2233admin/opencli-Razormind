import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.api.v1 import chat
from backend.config import Settings
from backend.models import (
    AgentConversation,
    AgentConversationTurn,
    AgentRun,
    AgentSession,
    ModelProvider,
    OperationsWorkItem,
    ProviderModel,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import agent_conversation_run_service as run_service
from backend.services import agent_conversation_service as service


def test_background_mode_rejects_explicit_multiworker_argv(monkeypatch):
    settings = Settings(
        _env_file=None,
        agent_conversation_execution_mode="local_single_process",
    )
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.setattr(run_service.sys, "argv", ["uvicorn", "backend.main:app", "--workers=2"])

    readiness = run_service.background_execution_readiness(settings)

    assert readiness["status"] == "blocked"
    assert readiness["reason_code"] == "multiple_workers_not_supported"
    with pytest.raises(RuntimeError, match="one API process"):
        run_service.validate_background_execution_settings(settings)


async def _seed_workspace_provider(db_session, *, subject: str = "runner@example.test"):
    user = User(subject=subject)
    workspace = Workspace(name="Runner Workspace", slug="runner-workspace")
    provider = ModelProvider(
        name="Deterministic Provider",
        provider_type="openai",
        default_model="deterministic-chat",
        enabled=True,
    )
    db_session.add_all((user, workspace, provider))
    await db_session.flush()
    db_session.add_all(
        (
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.ADMIN,
            ),
            ProviderModel(
                provider_id=provider.id,
                model_id="deterministic-chat",
                model_type="llm",
                capabilities={"tools": True},
                source="manual",
                enabled=True,
            ),
        )
    )
    await db_session.commit()
    return workspace, provider


async def _wait_for_terminal(client, conversation_id: str, turn_id: str) -> dict:
    for _ in range(100):
        response = await client.get(
            f"/api/v1/chat/sessions/{conversation_id}/turns/{turn_id}/events"
        )
        assert response.status_code == 200
        payload = response.json()["data"]
        if payload["terminal"]:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("background conversation run did not finish")


@pytest.mark.asyncio
async def test_background_two_turn_replay_idempotency_and_pinned_model(
    client, db_session, monkeypatch
):
    workspace, provider = await _seed_workspace_provider(db_session)

    async def identity_override():
        return RequestIdentity(subject="runner@example.test")

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    monkeypatch.setattr(
        app.state.connector_settings,
        "agent_conversation_execution_mode",
        "local_single_process",
    )
    monkeypatch.setattr(
        run_service,
        "AsyncSessionLocal",
        async_sessionmaker(bind=db_session.bind, expire_on_commit=False),
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    received_messages: list[list[dict[str, str]]] = []

    async def deterministic_chat(_db, body, _identity, *, tool_trace, **_kwargs):
        received_messages.append([message.model_dump() for message in body.messages])
        assert body.provider_id == provider.id
        assert body.model_id == "deterministic-chat"
        tool_trace.append(
            {
                "name": "list_projects",
                "kind": "read",
                "status": "completed",
                "argument_keys": [],
                "secret": "must-not-persist",
            }
        )
        activity_sink = chat._activity_sink.get()
        if activity_sink is not None:
            await activity_sink(
                {
                    "type": "approval.required",
                    "label": "Review token=keep-out",
                    "detail": "Inspect https://private.example/path api_key=keep-out",
                    "state": "waiting",
                }
            )
        if len(received_messages) == 1:
            first_started.set()
            await release_first.wait()
            return ApiResponse.ok(chat.ChatReply(type="message", content="first reply"))
        return ApiResponse.ok(chat.ChatReply(type="message", content="second reply"))

    monkeypatch.setattr(chat, "run_chat_request", deterministic_chat)
    created = await client.post(
        "/api/v1/chat/sessions",
        json={
            "workspace_id": workspace.id,
            "execution_target_id": f"provider:{provider.id}",
            "model_id": "deterministic-chat",
        },
    )
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]
    assert created.json()["data"]["execution_binding"]["provider_id"] == provider.id

    first = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={
            "request_id": "background-1",
            "content": "first question",
            "execution_mode": "background",
        },
    )
    assert first.status_code == 202
    first_turn = first.json()["data"]["turn"]
    first_run = first.json()["data"]["run"]
    assert first_turn["agent_run_id"] == first_run["id"]
    assert first_run["continuation"] == {"mode": "history_replay", "resumed": False}
    await asyncio.wait_for(first_started.wait(), timeout=1)
    close_while_running = await client.post(f"/api/v1/chat/sessions/{conversation_id}/close")
    assert close_while_running.status_code == 409

    same_request = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={
            "request_id": "background-1",
            "content": "first question",
            "execution_mode": "background",
        },
    )
    other_request = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={
            "request_id": "background-race",
            "content": "must not start",
            "execution_mode": "background",
        },
    )
    assert same_request.status_code == 202
    assert same_request.json()["data"]["turn"]["id"] == first_turn["id"]
    assert same_request.json()["data"]["run"]["id"] == first_run["id"]
    assert other_request.status_code == 409
    assert len(received_messages) == 1

    release_first.set()
    first_events = await _wait_for_terminal(client, conversation_id, first_turn["id"])
    assert first_events["run"]["status"] == "completed"
    assert [event["sequence"] for event in first_events["events"]] == list(
        range(1, len(first_events["events"]) + 1)
    )
    trace_event = next(event for event in first_events["events"] if event["type"] == "tool.trace")
    assert trace_event["payload"] == {
        "name": "list_projects",
        "kind": "read",
        "status": "completed",
        "argument_keys": [],
    }
    approval_event = next(
        event for event in first_events["events"] if event["type"] == "approval.required"
    )
    assert "keep-out" not in str(approval_event["payload"])
    assert "private.example" not in str(approval_event["payload"])

    first_page = await client.get(
        f"/api/v1/chat/sessions/{conversation_id}/turns/{first_turn['id']}/events",
        params={"after_sequence": 0, "limit": 1},
    )
    assert first_page.status_code == 200
    first_cursor = first_page.json()["data"]["next_sequence"]
    second_page = await client.get(
        f"/api/v1/chat/sessions/{conversation_id}/turns/{first_turn['id']}/events",
        params={"after_sequence": first_cursor, "limit": 1},
    )
    assert second_page.json()["data"]["events"][0]["sequence"] == first_cursor + 1

    replay = await client.get(
        f"/api/v1/chat/sessions/{conversation_id}/turns/{first_turn['id']}/events",
        params={"after_sequence": 1},
    )
    assert replay.status_code == 200
    assert all(event["sequence"] > 1 for event in replay.json()["data"]["events"])

    second = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={
            "request_id": "background-2",
            "content": "second question",
            "execution_mode": "background",
        },
    )
    assert second.status_code == 202
    second_turn = second.json()["data"]["turn"]
    await _wait_for_terminal(client, conversation_id, second_turn["id"])
    assert received_messages[1] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first reply"},
        {"role": "user", "content": "second question"},
    ]

    provider.enabled = False
    await db_session.commit()
    unavailable = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={"request_id": "background-3", "content": "third question"},
    )
    assert unavailable.status_code == 409
    assert len(received_messages) == 2


@pytest.mark.asyncio
async def test_execution_targets_scope_reopen_and_interrupted_recovery(
    client, db_session, monkeypatch
):
    workspace, provider = await _seed_workspace_provider(db_session, subject="scope@example.test")

    async def identity_override():
        return RequestIdentity(subject="scope@example.test")

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    assert app.state.connector_settings.agent_conversation_execution_mode == "disabled"
    targets = await client.get(
        "/api/v1/chat/execution-targets", params={"workspace_id": workspace.id}
    )
    assert targets.status_code == 200
    assert targets.json()["data"]["background_execution"]["status"] == "blocked"
    provider_target = targets.json()["data"]["targets"][0]
    assert provider_target["id"] == f"provider:{provider.id}"
    assert provider_target["readiness"]["status"] == "unverified"
    assert "api_key" not in str(provider_target)
    assert "agent_url" not in str(targets.json())

    created = await client.post(
        "/api/v1/chat/sessions",
        json={
            "workspace_id": workspace.id,
            "execution_target_id": f"provider:{provider.id}",
        },
    )
    conversation_id = created.json()["data"]["id"]
    closed = await client.post(f"/api/v1/chat/sessions/{conversation_id}/close")
    reopened = await client.post(f"/api/v1/chat/sessions/{conversation_id}/reopen")
    reopened_again = await client.post(f"/api/v1/chat/sessions/{conversation_id}/reopen")
    assert closed.json()["data"]["status"] == "closed"
    assert reopened.json()["data"]["status"] == "active"
    assert reopened_again.json()["data"]["revision"] == reopened.json()["data"]["revision"]

    conversation = await db_session.get(AgentConversation, conversation_id)
    agent_session = AgentSession(
        workspace_id=workspace.id,
        actor_subject="scope@example.test",
        context={"conversation_id": conversation_id},
    )
    db_session.add(agent_session)
    await db_session.flush()
    conversation.agent_session_id = agent_session.id
    turn = AgentConversationTurn(
        conversation_id=conversation_id,
        workspace_id=workspace.id,
        sequence=1,
        request_id="orphaned",
        active_slot=conversation_id,
        user_content="do not retry",
        context_binding={},
        tool_trace=[],
        status="running",
    )
    db_session.add(turn)
    await db_session.flush()
    run = AgentRun(
        session_id=agent_session.id,
        kind="conversation",
        status="running",
        goal="do not retry",
        request_payload={"conversation_id": conversation_id, "turn_id": turn.id},
    )
    db_session.add(run)
    await db_session.flush()
    turn.agent_run_id = run.id
    await db_session.commit()

    assert await run_service.recover_interrupted_runs(db_session) == 1
    await db_session.refresh(run)
    await db_session.refresh(turn)
    assert run.status == "interrupted"
    assert turn.status == "interrupted"
    assert turn.active_slot is None
    assert await run_service.recover_interrupted_runs(db_session) == 0


@pytest.mark.asyncio
async def test_background_execution_requires_explicit_single_process_mode(client, db_session):
    workspace, provider = await _seed_workspace_provider(
        db_session, subject="disabled@example.test"
    )

    async def identity_override():
        return RequestIdentity(subject="disabled@example.test")

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    created = await client.post(
        "/api/v1/chat/sessions",
        json={
            "workspace_id": workspace.id,
            "execution_target_id": f"provider:{provider.id}",
            "model_id": "deterministic-chat",
        },
    )
    response = await client.post(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/messages",
        json={
            "request_id": "disabled-background",
            "content": "do not queue",
            "execution_mode": "background",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "background_execution_disabled"


@pytest.mark.asyncio
async def test_queued_run_revalidates_pinned_model_before_calling_runner(
    client, db_session, monkeypatch
):
    workspace, provider = await _seed_workspace_provider(
        db_session, subject="revalidate@example.test"
    )
    identity = RequestIdentity(subject="revalidate@example.test")

    async def identity_override():
        return identity

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    monkeypatch.setattr(
        app.state.connector_settings,
        "agent_conversation_execution_mode",
        "local_single_process",
    )
    monkeypatch.setattr(
        run_service,
        "AsyncSessionLocal",
        async_sessionmaker(bind=db_session.bind, expire_on_commit=False),
    )
    scheduled: list[str] = []
    monkeypatch.setattr(run_service, "schedule", lambda run_id, _identity: scheduled.append(run_id))
    called = False

    async def forbidden_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled pinned model must not be called")

    monkeypatch.setattr(chat, "run_chat_request", forbidden_runner)
    created = await client.post(
        "/api/v1/chat/sessions",
        json={
            "workspace_id": workspace.id,
            "execution_target_id": f"provider:{provider.id}",
            "model_id": "deterministic-chat",
        },
    )
    queued = await client.post(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/messages",
        json={
            "request_id": "disable-before-run",
            "content": "must fail closed",
            "execution_mode": "background",
        },
    )
    run_id = queued.json()["data"]["run"]["id"]
    assert scheduled == [run_id]
    model = await db_session.scalar(
        select(ProviderModel).where(
            ProviderModel.provider_id == provider.id,
            ProviderModel.model_id == "deterministic-chat",
        )
    )
    model.enabled = False
    await db_session.commit()

    await service.execute_run(run_id, identity)

    run = await db_session.get(AgentRun, run_id)
    turn = await db_session.scalar(
        select(AgentConversationTurn).where(AgentConversationTurn.agent_run_id == run_id)
    )
    assert run.status == "failed"
    assert turn.status == "failed"
    assert turn.active_slot is None
    assert turn.error_code == "model_unavailable"
    assert turn.error_message
    assert called is False
    events = await client.get(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/turns/{turn.id}/events"
    )
    assert events.json()["data"]["terminal"] is True
    assert events.json()["data"]["events"][-1]["type"] == "run.failed"


@pytest.mark.asyncio
async def test_revoked_member_fails_queued_run_without_calling_model(
    client, db_session, monkeypatch
):
    workspace, provider = await _seed_workspace_provider(db_session, subject="revoked@example.test")
    identity = RequestIdentity(subject="revoked@example.test")

    async def identity_override():
        return identity

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    monkeypatch.setattr(
        app.state.connector_settings,
        "agent_conversation_execution_mode",
        "local_single_process",
    )
    monkeypatch.setattr(
        run_service,
        "AsyncSessionLocal",
        async_sessionmaker(bind=db_session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(run_service, "schedule", lambda *_args: None)
    called = False

    async def forbidden_runner(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(chat, "run_chat_request", forbidden_runner)
    created = await client.post(
        "/api/v1/chat/sessions",
        json={
            "workspace_id": workspace.id,
            "execution_target_id": f"provider:{provider.id}",
            "model_id": "deterministic-chat",
        },
    )
    queued = await client.post(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/messages",
        json={
            "request_id": "revoke-before-run",
            "content": "must fail closed",
            "execution_mode": "background",
        },
    )
    run_id = queued.json()["data"]["run"]["id"]
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace.id)
    )
    await db_session.delete(membership)
    await db_session.commit()

    await service.execute_run(run_id, identity)

    run = await db_session.get(AgentRun, run_id)
    turn = await db_session.scalar(
        select(AgentConversationTurn).where(AgentConversationTurn.agent_run_id == run_id)
    )
    assert called is False
    assert run.status == "failed"
    assert turn.status == "failed"
    assert turn.active_slot is None
    assert turn.error_code == "authorization_changed"


@pytest.mark.asyncio
async def test_background_proposal_persists_after_authorization_without_sqlite_deadlock(
    client, db_session, monkeypatch
):
    workspace, provider = await _seed_workspace_provider(
        db_session, subject="proposal@example.test"
    )
    identity = RequestIdentity(subject="proposal@example.test")

    async def identity_override():
        return identity

    from backend.main import app

    app.dependency_overrides[get_request_identity] = identity_override
    monkeypatch.setattr(
        app.state.connector_settings,
        "agent_conversation_execution_mode",
        "local_single_process",
    )
    monkeypatch.setattr(
        run_service,
        "AsyncSessionLocal",
        async_sessionmaker(bind=db_session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(run_service, "schedule", lambda *_args: None)

    async def real_proposal_runner(
        model_db,
        body,
        actor,
        *,
        proposal_provenance,
        tool_trace,
        **_kwargs,
    ):
        proposal = await chat._build_proposal(
            model_db,
            "update_provider",
            {"provider_id": provider.id, "enabled": False},
            identity=actor,
            workspace_id=body.workspace_id,
            provenance=proposal_provenance,
        )
        tool_trace.append(
            {
                "name": "update_provider",
                "kind": "write",
                "status": "proposed",
                "argument_keys": ["enabled", "provider_id"],
            }
        )
        activity_sink = chat._activity_sink.get()
        await activity_sink(
            {
                "type": "approval.required",
                "label": "等待确认",
                "detail": proposal.summary,
                "state": "attention",
                "target": {"type": "model_provider", "id": provider.id},
            }
        )
        return ApiResponse.ok(chat.ChatReply(type="proposal", proposal=proposal))

    monkeypatch.setattr(chat, "run_chat_request", real_proposal_runner)
    created = await client.post(
        "/api/v1/chat/sessions",
        json={
            "workspace_id": workspace.id,
            "execution_target_id": f"provider:{provider.id}",
            "model_id": "deterministic-chat",
        },
    )
    queued = await client.post(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/messages",
        json={
            "request_id": "proposal-run",
            "content": "prepare a provider change",
            "execution_mode": "background",
        },
    )
    turn_id = queued.json()["data"]["turn"]["id"]
    run_id = queued.json()["data"]["run"]["id"]

    await service.execute_run(run_id, identity)

    turn = await db_session.get(AgentConversationTurn, turn_id)
    run = await db_session.get(AgentRun, run_id)
    work_item = await db_session.get(OperationsWorkItem, turn.response["proposal"]["work_item_id"])
    events = await client.get(
        f"/api/v1/chat/sessions/{created.json()['data']['id']}/turns/{turn_id}/events"
    )
    event_types = [event["type"] for event in events.json()["data"]["events"]]
    await db_session.refresh(provider)
    assert turn.status == "proposal"
    assert run.status == "completed"
    assert work_item is not None
    assert work_item.status == "open"
    assert "approval.required" in event_types
    assert event_types[-1] == "reply"
    assert provider.enabled is True
