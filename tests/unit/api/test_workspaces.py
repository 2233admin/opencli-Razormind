from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.v1.workspaces import router
from backend.database import get_db
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.studio import StudioWorkspace
from backend.models.workflow import Project
from backend.security.identity import RequestIdentity, get_request_identity


async def _client(db_session, identity: RequestIdentity) -> AsyncClient:
    app = FastAPI()
    app.include_router(router)

    async def override_db():
        yield db_session

    async def override_identity():
        return identity

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = override_identity
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_workspace(db_session):
    admin = User(subject="workspace-admin")
    maintainer = User(subject="workspace-maintainer")
    workspace = Workspace(name="Operations", slug="operations-members")
    db_session.add_all((admin, maintainer, workspace))
    await db_session.flush()
    db_session.add_all(
        (
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=admin.id, role=WorkspaceRole.ADMIN
            ),
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=maintainer.id,
                role=WorkspaceRole.MAINTAINER,
            ),
        )
    )
    await db_session.commit()
    return admin, maintainer, workspace


async def test_governance_context_lists_only_accessible_workspaces(db_session):
    _, _, workspace = await _seed_workspace(db_session)
    db_session.add(Workspace(name="Hidden", slug="hidden-workspace"))
    await db_session.commit()

    client = await _client(db_session, RequestIdentity(subject="workspace-admin"))
    async with client:
        response = await client.get("/governance/workspaces")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [workspace.id]


async def test_governance_context_lists_workspace_projects(db_session):
    admin, _, workspace = await _seed_workspace(db_session)
    project = Project(
        workspace_id=workspace.id,
        name="Research",
        slug="research",
        created_by_user_id=admin.id,
    )
    archived = Project(
        workspace_id=workspace.id,
        name="Archived",
        slug="archived",
        created_by_user_id=admin.id,
        archived=True,
    )
    db_session.add_all((project, archived))
    await db_session.commit()

    client = await _client(db_session, RequestIdentity(subject="workspace-admin"))
    async with client:
        response = await client.get(f"/governance/workspaces/{workspace.id}/projects")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [project.id]


async def test_governance_context_rejects_non_member_project_access(db_session):
    _, _, workspace = await _seed_workspace(db_session)
    client = await _client(db_session, RequestIdentity(subject="workspace-outsider"))
    async with client:
        response = await client.get(f"/governance/workspaces/{workspace.id}/projects")

    assert response.status_code == 403


async def test_platform_admin_creates_workspace_with_explicit_first_admin(db_session):
    client = await _client(
        db_session,
        RequestIdentity(subject="platform-root", is_platform_admin=True),
    )
    async with client:
        response = await client.post(
            "/platform/workspaces",
            json={
                "name": "News Operations",
                "slug": "news-operations",
                "first_admin_subject": "first-admin",
                "first_admin_email": "admin@example.test",
            },
        )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["slug"] == "news-operations"
    assert data["first_admin"]["subject"] == "first-admin"
    assert data["first_admin"]["role"] == "admin"

    platform_user = await db_session.scalar(select(User).where(User.subject == "platform-root"))
    assert platform_user is None
    studio_workspace = await db_session.get(StudioWorkspace, data["id"])
    assert studio_workspace is not None
    assert (studio_workspace.name, studio_workspace.slug) == (
        "News Operations",
        "news-operations",
    )


async def test_non_platform_admin_cannot_create_workspace(db_session):
    client = await _client(db_session, RequestIdentity(subject="ordinary-user"))
    async with client:
        response = await client.post(
            "/platform/workspaces",
            json={"name": "Denied", "slug": "denied", "first_admin_subject": "admin"},
        )

    assert response.status_code == 403



async def test_workspace_admin_can_assign_admin_role(db_session):
    _, _, workspace = await _seed_workspace(db_session)
    client = await _client(db_session, RequestIdentity(subject="workspace-admin"))
    async with client:
        response = await client.post(
            f"/workspaces/{workspace.id}/members",
            json={"subject": "second-admin", "role": "admin"},
        )

    assert response.status_code == 201
    assert response.json()["data"]["role"] == "admin"


async def test_maintainer_manages_non_admin_roles_but_not_admins(db_session):
    _, _, workspace = await _seed_workspace(db_session)
    client = await _client(db_session, RequestIdentity(subject="workspace-maintainer"))
    async with client:
        added = await client.post(
            f"/workspaces/{workspace.id}/members",
            json={"subject": "new-operator", "role": "operator"},
        )
        denied = await client.post(
            f"/workspaces/{workspace.id}/members",
            json={"subject": "forbidden-admin", "role": "admin"},
        )

    assert added.status_code == 201
    assert denied.status_code == 403


async def test_maintainer_cannot_change_existing_admin(db_session):
    admin, _, workspace = await _seed_workspace(db_session)
    client = await _client(db_session, RequestIdentity(subject="workspace-maintainer"))
    async with client:
        response = await client.patch(
            f"/workspaces/{workspace.id}/members/{admin.id}",
            json={"role": "viewer"},
        )

    assert response.status_code == 403


async def test_last_admin_cannot_be_demoted(db_session):
    admin, _, workspace = await _seed_workspace(db_session)
    client = await _client(db_session, RequestIdentity(subject="workspace-admin"))
    async with client:
        response = await client.patch(
            f"/workspaces/{workspace.id}/members/{admin.id}",
            json={"role": "maintainer"},
        )

    assert response.status_code == 409


async def test_member_list_requires_workspace_membership(db_session):
    _, _, workspace = await _seed_workspace(db_session)
    client = await _client(
        db_session,
        RequestIdentity(subject="platform-outsider", is_platform_admin=True),
    )
    async with client:
        response = await client.get(f"/workspaces/{workspace.id}/members")

    assert response.status_code == 403


async def test_platform_admin_disables_workspace_and_member_access_stops(db_session):
    _, _, workspace = await _seed_workspace(db_session)
    platform_client = await _client(
        db_session,
        RequestIdentity(subject="platform-root", is_platform_admin=True),
    )
    async with platform_client:
        disabled = await platform_client.patch(
            f"/platform/workspaces/{workspace.id}",
            json={"active": False},
        )

    admin_client = await _client(db_session, RequestIdentity(subject="workspace-admin"))
    async with admin_client:
        denied = await admin_client.get(f"/workspaces/{workspace.id}/members")

    assert disabled.status_code == 200
    assert disabled.json()["data"]["active"] is False
    assert denied.status_code == 403
