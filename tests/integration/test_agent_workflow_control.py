from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from backend.api.v1 import chat
from backend.api.v1.studio_schemas import DraftUpdate, ProjectBootstrapCreate, VersionCreate
from backend.control.agent_control import ProposalProvenance, agent_control_service
from backend.database import clear_after_commit_callbacks
from backend.models.agent_conversation import (
    AgentConversation,
    AgentConversationTurn,
    AgentConversationTurnStatus,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.operations_work_item import OperationsWorkItem
from backend.models.studio import StudioWorkflowValidationRun, StudioWorkflowVersion
from backend.models.workflow_run import WorkflowRun
from backend.security.identity import RequestIdentity
from backend.services import agent_project_service
from backend.services.agent_managed_workflow_service import _login_state


def _managed_graph() -> dict:
    shared = {"sourceMode": "offline_fixture", "fixtureId": "gaojixing-doubao-offline-v1"}
    return {
        "id": "agent-managed-doubao",
        "name": "Agent managed Doubao",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "trigger",
                "kind": "schedule",
                "capability": "trigger",
                "params": {"mode": "manual", "timezone": "Asia/Shanghai"},
                "ui": {"catalogId": "intelligence.schedule.cron"},
            },
            {
                "id": "batch",
                "kind": "agent",
                "capability": "normalize",
                "params": {"template": "gaojixing-doubao-batch", **shared},
                "ui": {"catalogId": "package.gaojixing.doubao-batch"},
            },
            {
                "id": "certify",
                "kind": "agent",
                "capability": "normalize",
                "params": {"template": "gaojixing-batch-certification", **shared},
                "ui": {"catalogId": "package.gaojixing.batch-certification"},
            },
            {
                "id": "delivery",
                "kind": "inbox",
                "capability": "store",
                "params": {"queue": "gaojixing-doubao-certified", "archive": True},
                "ui": {"catalogId": "intelligence.output.inbox"},
            },
        ],
        "edges": [
            {
                "id": "trigger-batch",
                "source": "trigger",
                "target": "batch",
                "sourcePort": "tick",
                "targetPort": "in",
            },
            {
                "id": "batch-certify",
                "source": "batch",
                "target": "certify",
                "sourcePort": "out",
                "targetPort": "in",
            },
            {
                "id": "certify-delivery",
                "source": "certify",
                "target": "delivery",
                "sourcePort": "out",
                "targetPort": "in",
            },
        ],
        "adapters": [],
        "agentPermissions": {
            "canFetchNetwork": True,
            "canSendNotifications": False,
            "canWriteInbox": True,
            "canMutateExternalSites": True,
        },
    }


def test_runtime_readiness_requires_an_explicit_login_field():
    assert _login_state([{"Login": True, "Url": "https://www.doubao.com/chat/1"}]) == "ready"
    assert _login_state([{"Login": "Unknown"}]) == "unknown"
    assert _login_state([{"Login": False}]) == "not_ready"
    assert _login_state([{"Url": "https://www.doubao.com/chat/1"}]) == "missing"


async def _local_admin(db_session):
    user = User(subject="local-agent-workflow", display_name="Local Agent Workflow")
    workspace = Workspace(name="Agent Workflow", slug="agent-workflow")
    db_session.add_all([user, workspace])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    await db_session.flush()
    return (
        RequestIdentity(
            subject=user.subject,
            auth_method="local",
            is_platform_admin=True,
        ),
        user,
        workspace,
    )


async def _propose_from_conversation(
    db_session,
    *,
    identity: RequestIdentity,
    conversation: AgentConversation,
    action_name: str,
    args: dict,
):
    sequence = (
        await db_session.scalar(
            select(func.count(AgentConversationTurn.id)).where(
                AgentConversationTurn.conversation_id == conversation.id
            )
        )
    ) + 1
    turn = AgentConversationTurn(
        conversation_id=conversation.id,
        workspace_id=conversation.workspace_id,
        sequence=sequence,
        request_id=f"{action_name}-{sequence}",
        user_content=action_name,
        context_binding=dict(conversation.context_binding or {}),
        tool_trace=[],
        status=AgentConversationTurnStatus.RUNNING.value,
    )
    db_session.add(turn)
    await db_session.flush()
    proposal = await agent_control_service.create_proposal(
        db_session,
        workspace_id=conversation.workspace_id,
        identity=identity,
        action_name=action_name,
        args=args,
        origin="agent_conversation",
        provenance=ProposalProvenance(
            conversation_id=conversation.id,
            turn_id=turn.id,
            context=dict(conversation.context_binding or {}),
        ),
    )
    turn.response = {
        "type": "proposal",
        "proposal": {
            "tool": proposal.preview.action_name,
            "work_item_id": proposal.work_item_id,
            "proposal_version": proposal.proposal_version,
        },
    }
    turn.status = AgentConversationTurnStatus.PROPOSAL.value
    await db_session.flush()
    return proposal


async def _confirm(db_session, identity, workspace_id: str, proposal):
    return await agent_control_service.execute_confirmed(
        db_session,
        workspace_id=workspace_id,
        identity=identity,
        work_item_id=proposal.work_item_id,
        proposal_version=proposal.proposal_version,
        confirmation_path="test.agent-workflow.confirm",
        expected_action=proposal.preview.action_name,
    )


@pytest.mark.asyncio
async def test_confirmed_project_creation_adds_verified_studio_scope_for_local_admin(
    db_session,
):
    identity, user, workspace = await _local_admin(db_session)
    conversation = AgentConversation(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="创建真实工作流",
        status="active",
        context_binding={"surface": "agent-dock"},
    )
    db_session.add(conversation)
    await db_session.flush()
    proposal = await _propose_from_conversation(
        db_session,
        identity=identity,
        conversation=conversation,
        action_name="create_project",
        args={
            "project": {"name": "会话项目", "slug": "conversation-project"},
            "workflow": {"name": "受管采集", "graph": _managed_graph()},
        },
    )

    result = await _confirm(db_session, identity, workspace.id, proposal)

    assert conversation.context_binding == {
        "surface": "agent-dock",
        "studio_workspace_id": result["studio_workspace_id"],
        "project_id": result["project_id"],
        "workflow_id": result["workflow_id"],
    }
    assert result["studio_workspace_id"] == workspace.id


@pytest.mark.asyncio
async def test_tool_catalog_is_registry_backed_and_permission_projected(db_session):
    identity, user, workspace = await _local_admin(db_session)
    response = await chat.list_chat_tools(workspace.id, identity, db_session)
    rows = {row["name"]: row for row in response.data["tools"]}

    assert set(rows) == {tool["function"]["name"] for tool in chat.TOOLS}
    for name in (
        "validate_workflow_draft",
        "publish_workflow",
        "run_managed_doubao_question",
    ):
        assert name in chat.ACTION_REGISTRY.action_names
        assert rows[name]["kind"] == "proposal"
        assert rows[name]["requires_confirmation"] is True
        assert rows[name]["available"] is True

    viewer = User(subject="agent-workflow-viewer", display_name="Viewer")
    db_session.add(viewer)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=viewer.id,
            role=WorkspaceRole.VIEWER,
        )
    )
    await db_session.flush()
    viewer_response = await chat.list_chat_tools(
        workspace.id,
        RequestIdentity(subject=viewer.subject),
        db_session,
    )
    viewer_rows = {row["name"]: row for row in viewer_response.data["tools"]}
    assert viewer_rows["list_projects"]["available"] is True
    assert viewer_rows["publish_workflow"]["available"] is False
    assert viewer_rows["publish_workflow"]["reason"].startswith(
        "workspace_permission_required:"
    )
    assert user.id != viewer.id


@pytest.mark.asyncio
async def test_validation_confirmation_rejects_a_changed_draft_without_side_effect(
    db_session,
):
    identity, user, workspace = await _local_admin(db_session)
    created = await agent_project_service.create_project_bundle(
        db_session,
        workspace_id=workspace.id,
        body=ProjectBootstrapCreate.model_validate(
            {
                "project": {"name": "版本冲突", "slug": "validation-conflict"},
                "workflow": {"name": "受管采集", "graph": _managed_graph()},
            }
        ),
        actor_user_id=user.id,
    )
    proposal = await agent_control_service.create_proposal(
        db_session,
        workspace_id=workspace.id,
        identity=identity,
        action_name="validate_workflow_draft",
        args={
            "project_id": created.project.id,
            "workflow_id": created.workflow.id,
            "expected_revision": 1,
        },
        origin="chat",
    )
    changed = deepcopy(created.draft.graph)
    changed["name"] = "确认前发生的编辑"
    await agent_project_service.update_workflow_draft(
        db_session,
        workspace_id=workspace.id,
        project_id=created.project.id,
        workflow_id=created.workflow.id,
        body=DraftUpdate.model_validate({"revision": 1, "graph": changed}),
        actor_user_id=user.id,
    )

    with pytest.raises(HTTPException) as stale:
        await _confirm(db_session, identity, workspace.id, proposal)

    assert stale.value.status_code == 409
    assert await db_session.scalar(select(StudioWorkflowValidationRun.id)) is None


@pytest.mark.asyncio
async def test_confirmed_validation_publish_and_single_question_run_preserve_origin(
    db_session,
    tmp_path: Path,
    monkeypatch,
):
    identity, user, workspace = await _local_admin(db_session)
    created = await agent_project_service.create_project_bundle(
        db_session,
        workspace_id=workspace.id,
        body=ProjectBootstrapCreate.model_validate(
            {
                "project": {"name": "真实单题", "slug": "real-one-question"},
                "workflow": {"name": "受管采集", "graph": _managed_graph()},
            }
        ),
        actor_user_id=user.id,
    )
    conversation = AgentConversation(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="运行真实单题",
        status="active",
        context_binding={
            "surface": "agent-dock",
            "studio_workspace_id": workspace.id,
            "project_id": created.project.id,
            "workflow_id": created.workflow.id,
        },
    )
    db_session.add(conversation)
    await db_session.flush()

    validation = await _propose_from_conversation(
        db_session,
        identity=identity,
        conversation=conversation,
        action_name="validate_workflow_draft",
        args={
            "project_id": created.project.id,
            "workflow_id": created.workflow.id,
            "expected_revision": 1,
        },
    )
    assert await db_session.scalar(select(StudioWorkflowValidationRun.id)) is None
    validation_result = await _confirm(db_session, identity, workspace.id, validation)
    assert validation_result["valid"] is True

    publication = await _propose_from_conversation(
        db_session,
        identity=identity,
        conversation=conversation,
        action_name="publish_workflow",
        args={
            "project_id": created.project.id,
            "workflow_id": created.workflow.id,
            "expected_revision": 1,
            "validation_run_id": validation_result["validation_run_id"],
            "expected_current_published_version": None,
            "reason": "确认后发布真实单题工作流",
        },
    )
    assert await db_session.scalar(select(StudioWorkflowVersion.id)) is None
    publication_result = await _confirm(db_session, identity, workspace.id, publication)
    assert publication_result["published_version"] == 1

    async def ready() -> None:
        return None

    monkeypatch.setattr(
        "backend.services.agent_managed_workflow_service.preflight_managed_doubao_runtime",
        ready,
    )
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    run_proposal = await _propose_from_conversation(
        db_session,
        identity=identity,
        conversation=conversation,
        action_name="run_managed_doubao_question",
        args={
            "project_id": created.project.id,
            "workflow_id": created.workflow.id,
            "expected_published_version": 1,
            "question": "高吉星有哪些公开产品信息？",
        },
    )
    assert await db_session.scalar(select(WorkflowRun.id)) is None
    assert not (tmp_path / "runs").exists()

    run_result = await _confirm(db_session, identity, workspace.id, run_proposal)
    run = await db_session.get(WorkflowRun, run_result["run_id"])
    assert run is not None
    assert run.request["_serverConversationOrigin"] == {
        "conversation_id": conversation.id,
        "conversation_revision": 0,
        "governed_workspace_id": workspace.id,
        "studio_workspace_id": workspace.id,
        "project_id": created.project.id,
        "workflow_id": created.workflow.id,
        "user_id": user.id,
    }
    assert conversation.context_binding["run_id"] == run.id
    assert run_result["question_count"] == 1
    assert len(list((tmp_path / "runs" / run.id).glob("**/question-bank.json"))) == 1

    with pytest.raises(HTTPException) as repeated:
        await _confirm(db_session, identity, workspace.id, run_proposal)
    assert repeated.value.status_code == 409
    assert (
        await db_session.scalar(select(func.count(WorkflowRun.id)).where(WorkflowRun.id == run.id))
        == 1
    )
    clear_after_commit_callbacks(db_session)


@pytest.mark.asyncio
async def test_managed_run_preflight_failure_creates_no_proposal_staging_or_run(
    db_session,
    tmp_path: Path,
    monkeypatch,
):
    identity, user, workspace = await _local_admin(db_session)
    created = await agent_project_service.create_project_bundle(
        db_session,
        workspace_id=workspace.id,
        body=ProjectBootstrapCreate.model_validate(
            {
                "project": {"name": "未登录", "slug": "login-required"},
                "workflow": {"name": "受管采集", "graph": _managed_graph()},
            }
        ),
        actor_user_id=user.id,
    )
    validation = await agent_project_service.validate_workflow_draft(
        db_session,
        workspace_id=workspace.id,
        project_id=created.project.id,
        workflow_id=created.workflow.id,
        expected_revision=1,
    )
    version = await agent_project_service.publish_workflow_version(
        db_session,
        workspace_id=workspace.id,
        project_id=created.project.id,
        workflow_id=created.workflow.id,
        body=VersionCreate.model_validate(
            {
                "reason": "test",
                "expectedRevision": 1,
                "validationRunId": validation.id,
            }
        ),
        actor_user_id=user.id,
    )

    allowed_graph = deepcopy(version.graph)
    disallowed_graph = deepcopy(version.graph)
    disallowed_graph["agentPermissions"]["canSendNotifications"] = True
    version.graph = disallowed_graph
    await db_session.flush()
    with pytest.raises(HTTPException) as notifications:
        await agent_control_service.create_proposal(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            action_name="run_managed_doubao_question",
            args={
                "project_id": created.project.id,
                "workflow_id": created.workflow.id,
                "expected_published_version": version.version,
                "question": "高吉星是什么？",
            },
            origin="chat",
        )
    assert notifications.value.status_code == 409
    assert "disable notifications" in str(notifications.value.detail)
    assert await db_session.scalar(select(OperationsWorkItem.id)) is None
    version.graph = allowed_graph
    await db_session.flush()

    async def not_logged_in() -> None:
        raise HTTPException(409, "Managed Doubao runtime is not ready: doubao-login-required")

    monkeypatch.setattr(
        "backend.services.agent_managed_workflow_service.preflight_managed_doubao_runtime",
        not_logged_in,
    )
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    with pytest.raises(HTTPException) as unavailable:
        await agent_control_service.create_proposal(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            action_name="run_managed_doubao_question",
            args={
                "project_id": created.project.id,
                "workflow_id": created.workflow.id,
                "expected_published_version": version.version,
                "question": "高吉星是什么？",
            },
            origin="chat",
        )
    assert unavailable.value.status_code == 409
    assert await db_session.scalar(select(OperationsWorkItem.id)) is None
    assert await db_session.scalar(select(WorkflowRun.id)) is None
    assert not (tmp_path / "runs").exists()
