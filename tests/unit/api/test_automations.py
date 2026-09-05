from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.v1.automations import router
from backend.database import get_db
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.security.identity import RequestIdentity, get_request_identity


async def test_admin_can_create_list_and_update_disabled_automation_draft(db_session):
    user = User(subject="automation-admin")
    workspace = Workspace(name="Automation", slug="automation")
    db_session.add_all((user, workspace))
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    await db_session.commit()

    app = FastAPI()
    app.include_router(router)

    async def override_db():
        yield db_session

    async def override_identity():
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = override_identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            f"/workspaces/{workspace.id}/automations",
            json={
                "name": "Daily review",
                "prompt": "Review the workspace",
                "executor": "codex",
                "schedule": "daily@09:00",
                "timezone": "Asia/Shanghai",
                "enabled": False,
            },
        )
        automation_id = created.json()["data"]["id"]
        listed = await client.get(f"/workspaces/{workspace.id}/automations")
        updated = await client.patch(
            f"/workspaces/{workspace.id}/automations/{automation_id}",
            json={"name": "Updated review"},
        )

    assert created.status_code == 201
    assert listed.json()["data"][0]["name"] == "Daily review"
    assert updated.json()["data"]["name"] == "Updated review"
    assert updated.json()["data"]["enabled"] is False
    assert updated.json()["data"]["revision"] == 2
