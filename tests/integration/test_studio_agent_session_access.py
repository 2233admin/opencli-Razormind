"""Regression coverage for the local Studio Agent-session authorization bridge."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.v1 import agent_conversations, chat
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.studio import StudioProject, StudioWorkspace
from backend.security.identity import RequestIdentity
from backend.services import agent_conversation_service as conversations


async def _local_admin_scope(db_session):
    user = User(subject="local-admin", email="local-admin@example.test")
    governed = Workspace(name="Governed", slug="governed-local-admin")
    studio = StudioWorkspace(name="Studio", slug="studio-local-admin")
    db_session.add_all([user, governed, studio])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            user_id=user.id,
            workspace_id=governed.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    project = StudioProject(
        workspace_id=studio.id,
        name="Studio project",
        slug="studio-project",
        created_by_user_id="local-development-user",
    )
    db_session.add(project)
    await db_session.commit()
    identity = RequestIdentity(
        subject="local-admin",
        is_platform_admin=True,
        auth_method="local",
    )
    return identity, governed, studio, project


@pytest.mark.asyncio
async def test_local_admin_can_use_studio_project_session_through_governed_storage(db_session):
    identity, governed, studio, project = await _local_admin_scope(db_session)

    created = await conversations.create_conversation(
        db_session,
        identity,
        workspace_id=studio.id,
        title="Project agent",
        context={"project_id": project.id, "surface": "studio-project"},
    )

    assert created.workspace_id == governed.id
    assert created.context_binding == {
        "project_id": project.id,
        "surface": "studio-project",
        "studio_workspace_id": studio.id,
    }
    listed = await agent_conversations.list_sessions(
        workspace_id=studio.id,
        project_id=project.id,
        workflow_id=None,
        run_id=None,
        limit=20,
        identity=identity,
        db=db_session,
    )
    assert [session.id for session in listed.data] == [created.id]
    detail = await agent_conversations.get_session(
        created.id,
        after_sequence=0,
        limit=50,
        identity=identity,
        db=db_session,
    )
    assert detail.data.context_binding["studio_workspace_id"] == studio.id

    async def runner(*_args, **_kwargs):
        return chat.ChatReply(type="message", content="已连接到当前项目")

    _, turn = await conversations.send_message(
        db_session,
        identity,
        created.id,
        request_id="studio-message-1",
        content="检查这个项目",
        context={"project_id": project.id, "surface": "studio-project"},
        chat_runner=runner,
    )
    assert turn.status == "completed"
    assert turn.context_binding["studio_workspace_id"] == studio.id


@pytest.mark.asyncio
async def test_studio_session_rejects_cross_project_or_workspace_context(db_session):
    identity, _, studio, project = await _local_admin_scope(db_session)
    other_studio = StudioWorkspace(name="Other Studio", slug="other-studio-local-admin")
    db_session.add(other_studio)
    await db_session.flush()
    other_project = StudioProject(
        workspace_id=other_studio.id,
        name="Other project",
        slug="other-project",
        created_by_user_id="local-development-user",
    )
    same_workspace_project = StudioProject(
        workspace_id=studio.id,
        name="Second project",
        slug="second-project",
        created_by_user_id="local-development-user",
    )
    db_session.add_all([other_project, same_workspace_project])
    await db_session.commit()

    with pytest.raises(HTTPException) as workspace_error:
        await conversations.create_conversation(
            db_session,
            identity,
            workspace_id=studio.id,
            title=None,
            context={"project_id": other_project.id},
        )
    assert workspace_error.value.status_code == 409

    created = await conversations.create_conversation(
        db_session,
        identity,
        workspace_id=studio.id,
        title=None,
        context={"project_id": project.id},
    )
    with pytest.raises(HTTPException) as project_error:
        await conversations.send_message(
            db_session,
            identity,
            created.id,
            request_id="cross-project",
            content="switch project",
            context={"project_id": same_workspace_project.id},
            chat_runner=lambda *_args, **_kwargs: None,
        )
    assert project_error.value.status_code == 409
    assert "cannot switch projects" in str(project_error.value.detail)


@pytest.mark.asyncio
async def test_studio_session_rejects_unanchored_and_oidc_admin_access(db_session):
    identity, _, studio, project = await _local_admin_scope(db_session)

    with pytest.raises(HTTPException) as unanchored:
        await conversations.create_conversation(
            db_session,
            identity,
            workspace_id=studio.id,
            title=None,
            context={"surface": "studio-project"},
        )
    assert unanchored.value.status_code == 409

    oidc_admin = RequestIdentity(subject="local-admin", is_platform_admin=True, auth_method="oidc")
    with pytest.raises(HTTPException) as oidc_denied:
        await conversations.create_conversation(
            db_session,
            oidc_admin,
            workspace_id=studio.id,
            title=None,
            context={"project_id": project.id},
        )
    assert oidc_denied.value.status_code == 403


@pytest.mark.asyncio
async def test_governed_workspace_sessions_keep_membership_rbac(db_session):
    identity, governed, _, _ = await _local_admin_scope(db_session)
    conversation = await conversations.create_conversation(
        db_session,
        identity,
        workspace_id=governed.id,
        title=None,
        context={"surface": "dashboard"},
    )
    assert conversation.workspace_id == governed.id
    assert "studio_workspace_id" not in conversation.context_binding

    outsider = RequestIdentity(subject="unrelated-oidc-subject", auth_method="oidc")
    with pytest.raises(HTTPException) as denied:
        await conversations.get_conversation(db_session, outsider, conversation.id)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_studio_sessions_are_hidden_from_governed_workspace_lists(db_session):
    identity, governed, studio, project = await _local_admin_scope(db_session)
    studio_session = await conversations.create_conversation(
        db_session,
        identity,
        workspace_id=studio.id,
        title="Studio only",
        context={"project_id": project.id},
    )
    governed_session = await conversations.create_conversation(
        db_session,
        identity,
        workspace_id=governed.id,
        title="Governed session",
        context={"surface": "dashboard"},
    )

    member = User(subject="governed-member", email="governed-member@example.test")
    db_session.add(member)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            user_id=member.id,
            workspace_id=governed.id,
            role=WorkspaceRole.VIEWER,
        )
    )
    await db_session.commit()
    member_identity = RequestIdentity(subject="governed-member", auth_method="oidc")

    local_rows = await conversations.list_conversations(
        db_session,
        identity,
        workspace_id=governed.id,
        limit=20,
        context={},
    )
    member_rows = await conversations.list_conversations(
        db_session,
        member_identity,
        workspace_id=governed.id,
        limit=20,
        context={},
    )

    assert [row.id for row in local_rows] == [governed_session.id]
    assert [row.id for row in member_rows] == [governed_session.id]
    assert studio_session.id not in {row.id for row in local_rows + member_rows}
