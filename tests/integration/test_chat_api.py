"""Chat dock AI-provider tool coverage: read (list_providers), write/propose
(update_provider), and confirm (POST /chat/confirm) — the LLM round trip
itself is out of scope (no prior art for mocking it in this repo); these
exercise the exact tool-dispatch logic the LLM would trigger.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.api.v1.chat import _build_proposal, _run_read_tool
from backend.main import app
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.operations_work_item import OperationsWorkItem, WorkItemStatus
from backend.models.provider import ModelProvider
from backend.security.identity import RequestIdentity, get_request_identity


async def _make_provider(db_session, **overrides) -> ModelProvider:
    provider = ModelProvider(
        name=overrides.get("name", "Test Provider"),
        provider_type=overrides.get("provider_type", "openai"),
        base_url=overrides.get("base_url", "https://api.openai.com/v1"),
        api_key=overrides.get("api_key", "sk-test-key"),
        default_model=overrides.get("default_model", "gpt-4o-mini"),
        enabled=overrides.get("enabled", True),
    )
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)
    return provider


async def _authorize_chat(
    db_session,
    *,
    subject: str = "chat-admin",
    role: WorkspaceRole = WorkspaceRole.ADMIN,
) -> tuple[RequestIdentity, User, Workspace]:
    identity = RequestIdentity(subject=subject)
    user = User(subject=subject)
    workspace = Workspace(name=f"Chat {subject}", slug=f"chat-{subject}")
    db_session.add_all([user, workspace])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=role,
        )
    )
    await db_session.commit()

    async def override_identity():
        return identity

    app.dependency_overrides[get_request_identity] = override_identity
    return identity, user, workspace


# ── read: list_providers ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_providers_read_tool_empty(db_session):
    result = await _run_read_tool(db_session, "list_providers", {})
    assert result == []


@pytest.mark.asyncio
async def test_list_providers_read_tool_returns_enabled_and_disabled(db_session):
    enabled = await _make_provider(db_session, name="Enabled One", enabled=True)
    disabled = await _make_provider(db_session, name="Disabled One", enabled=False)

    result = await _run_read_tool(db_session, "list_providers", {})

    assert {p["id"] for p in result} == {enabled.id, disabled.id}
    by_id = {p["id"]: p for p in result}
    assert by_id[enabled.id]["enabled"] is True
    assert by_id[disabled.id]["enabled"] is False
    assert set(by_id[enabled.id]) == {
        "id",
        "name",
        "provider_type",
        "default_model",
        "base_url",
        "enabled",
    }


# ── write: update_provider proposal ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_update_provider_proposal_default_model(db_session):
    provider = await _make_provider(db_session, default_model="gpt-4o-mini")

    proposal = await _build_proposal(
        db_session, "update_provider", {"provider_id": provider.id, "default_model": "qwen3:4b"}
    )

    assert proposal.tool == "update_provider"
    assert proposal.args == {"provider_id": provider.id, "default_model": "qwen3:4b"}
    assert "gpt-4o-mini" in proposal.diff and "qwen3:4b" in proposal.diff
    # proposing never mutates the row
    await db_session.refresh(provider)
    assert provider.default_model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_update_provider_proposal_enabled_toggle(db_session):
    provider = await _make_provider(db_session, enabled=True)

    proposal = await _build_proposal(
        db_session,
        "update_provider",
        {"provider_id": provider.id, "enabled": False},
    )

    assert proposal.args == {"provider_id": provider.id, "enabled": False}
    assert "启用" in proposal.diff or "停用" in proposal.diff


@pytest.mark.asyncio
async def test_update_provider_proposal_not_found(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await _build_proposal(
            db_session,
            "update_provider",
            {"provider_id": "nonexistent-id", "enabled": True},
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_provider_proposal_no_fields(db_session):
    provider = await _make_provider(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await _build_proposal(db_session, "update_provider", {"provider_id": provider.id})
    assert exc_info.value.status_code == 400


# ── confirm: POST /api/v1/chat/confirm ───────────────────────────────────────
@pytest.mark.asyncio
async def test_confirm_update_provider(client, db_session):
    provider = await _make_provider(db_session, default_model="gpt-4o-mini", enabled=True)
    _, user, workspace = await _authorize_chat(db_session)

    response = await client.post(
        "/api/v1/chat/confirm",
        json={
            "proposal": {
                "tool": "update_provider",
                "args": {"provider_id": provider.id, "default_model": "qwen3:4b", "enabled": False},
                "summary": "配置 AI 模型提供商",
                "diff": "default_model gpt-4o-mini -> qwen3:4b",
            }
        },
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["applied"] is True
    assert body["tool"] == "update_provider"

    await db_session.refresh(provider)
    assert provider.default_model == "qwen3:4b"
    assert provider.enabled is False
    work_item = await db_session.get(OperationsWorkItem, body["work_item_id"])
    assert work_item is not None
    assert work_item.workspace_id == workspace.id
    assert work_item.author_actor_type == "user"
    assert work_item.author_actor_id == user.id
    assert work_item.status == WorkItemStatus.RESOLVED
    assert work_item.evidence["proposal_version"] == body["proposal_version"]
    assert work_item.evidence["approval_grant"]["proposal_version"] == body["proposal_version"]


@pytest.mark.asyncio
async def test_confirm_update_provider_not_found(client, db_session):
    await _authorize_chat(db_session)
    response = await client.post(
        "/api/v1/chat/confirm",
        json={
            "proposal": {
                "tool": "update_provider",
                "args": {"provider_id": "nonexistent-id", "enabled": True},
                "summary": "配置 AI 模型提供商",
                "diff": "enabled -> true",
            }
        },
    )
    assert response.status_code == 404


# ── confirm: trigger_task dispatch failure ───────────────────────────────────
@pytest.mark.asyncio
async def test_confirm_trigger_task_reports_dispatch_failure(client, db_session, monkeypatch):
    """Dispatch blowing up after the task row is committed must surface as 502,
    not applied=True with a silently dead task."""
    from backend.models.source import DataSource

    source = DataSource(
        name="Chat Trigger Source",
        channel_type="rss",
        channel_config={"feed_url": "https://example.com/feed.xml"},
        enabled=True,
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    await _authorize_chat(db_session)

    class _BoomExecutor:
        async def dispatch_collection(self, task_id: str, parameters: dict) -> dict:
            raise RuntimeError("broker down")

    monkeypatch.setattr("backend.executor.get_executor", lambda: _BoomExecutor())

    response = await client.post(
        "/api/v1/chat/confirm",
        json={
            "proposal": {
                "tool": "trigger_task",
                "args": {"source_id": source.id},
                "summary": "触发采集",
                "diff": "",
            }
        },
    )

    assert response.status_code == 502
    assert "派发失败" in response.json()["detail"]
    work_item = await db_session.scalar(select(OperationsWorkItem))
    assert work_item is not None
    assert work_item.status == WorkItemStatus.IN_PROGRESS
    assert work_item.evidence["execution"]["status"] == "failed_after_commit"


@pytest.mark.asyncio
async def test_viewer_confirmation_is_denied_without_mutation(client, db_session):
    provider = await _make_provider(db_session, default_model="gpt-4o-mini", enabled=True)
    await _authorize_chat(
        db_session,
        subject="chat-viewer",
        role=WorkspaceRole.VIEWER,
    )

    response = await client.post(
        "/api/v1/chat/confirm",
        json={
            "proposal": {
                "tool": "update_provider",
                "args": {"provider_id": provider.id, "enabled": False},
                "summary": "配置 AI 模型提供商",
                "diff": "enabled true -> false",
            }
        },
    )

    assert response.status_code == 403
    await db_session.refresh(provider)
    assert provider.enabled is True
    work_items = (await db_session.scalars(select(OperationsWorkItem))).all()
    assert work_items == []


@pytest.mark.asyncio
async def test_cross_workspace_confirmation_cannot_apply_recorded_proposal(
    client,
    db_session,
):
    provider = await _make_provider(db_session, default_model="gpt-4o-mini", enabled=True)
    identity_a, user_a, workspace_a = await _authorize_chat(
        db_session,
        subject="workspace-a-admin",
    )
    proposal = await _build_proposal(
        db_session,
        "update_provider",
        {"provider_id": provider.id, "enabled": False},
        identity=identity_a,
        workspace_id=workspace_a.id,
    )
    await db_session.commit()

    _, _, workspace_b = await _authorize_chat(
        db_session,
        subject="workspace-b-admin",
    )
    response = await client.post(
        "/api/v1/chat/confirm",
        json={"proposal": proposal.model_dump()},
    )

    assert response.status_code == 403
    await db_session.refresh(provider)
    assert provider.enabled is True
    work_item = await db_session.get(OperationsWorkItem, proposal.work_item_id)
    assert work_item is not None
    assert work_item.workspace_id == workspace_a.id
    assert work_item.workspace_id != workspace_b.id
    assert work_item.author_actor_id == user_a.id
    assert work_item.status == WorkItemStatus.OPEN
    assert work_item.evidence["schema_version"] == "agent-control-evidence/v1"
    assert work_item.evidence["proposal_version"] == proposal.proposal_version
    assert work_item.evidence["confirmation"]["state"] == "pending"
