from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.v1.project_source_bindings import router as source_bindings_router
from backend.api.v1.workspace_sources import router as sources_router
from backend.database import get_db
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.workflow import Project
from backend.security.identity import RequestIdentity, get_request_identity


def _build_client(db_session, user):
    app = FastAPI()
    app.include_router(sources_router)
    app.include_router(source_bindings_router)

    async def override_db():
        yield db_session

    async def override_identity():
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = override_identity
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_workspace_admin(db_session, user, workspace_name: str, workspace_slug: str) -> Workspace:
    workspace = Workspace(name=workspace_name, slug=workspace_slug)
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.ADMIN)
    )
    await db_session.commit()
    return workspace


async def test_source_is_workspace_owned_and_not_visible_from_another_workspace(db_session):
    user = User(subject="source-owner")
    db_session.add(user)
    await db_session.flush()
    workspace_a = await _seed_workspace_admin(db_session, user, "Workspace A", "workspace-a")
    workspace_b = await _seed_workspace_admin(db_session, user, "Workspace B", "workspace-b")

    async with _build_client(db_session, user) as client:
        created = await client.post(
            f"/workspaces/{workspace_a.id}/sources",
            json={
                "name": "RSS Feed",
                "slug": "rss-feed",
                "adapter_type": "rss",
                "adapter_config": {"feed_url": "https://example.com/feed.xml"},
            },
        )
        assert created.status_code == 201
        source_id = created.json()["data"]["id"]

        found_in_owner = await client.get(f"/workspaces/{workspace_a.id}/sources/{source_id}")
        assert found_in_owner.status_code == 200
        assert found_in_owner.json()["data"]["workspace_id"] == workspace_a.id

        not_found_elsewhere = await client.get(f"/workspaces/{workspace_b.id}/sources/{source_id}")
        assert not_found_elsewhere.status_code == 404


async def test_source_revisions_are_immutable_and_accumulate(db_session):
    user = User(subject="revision-owner")
    db_session.add(user)
    await db_session.flush()
    workspace = await _seed_workspace_admin(db_session, user, "Workspace", "workspace-rev")

    async with _build_client(db_session, user) as client:
        created = await client.post(
            f"/workspaces/{workspace.id}/sources",
            json={
                "name": "OpenCLI Source",
                "slug": "opencli-source",
                "adapter_type": "opencli",
                "adapter_config": {"endpoint": "https://one.example.com"},
            },
        )
        source_id = created.json()["data"]["id"]
        assert created.json()["data"]["current_revision_number"] == 1

        new_revision = await client.post(
            f"/workspaces/{workspace.id}/sources/{source_id}/revisions",
            json={"adapter_config": {"endpoint": "https://two.example.com"}},
        )
        assert new_revision.status_code == 201
        assert new_revision.json()["data"]["revision_number"] == 2

        revisions = (
            await client.get(f"/workspaces/{workspace.id}/sources/{source_id}/revisions")
        ).json()["data"]
        assert [r["revision_number"] for r in revisions] == [1, 2]
        assert revisions[0]["adapter_config"] == {"endpoint": "https://one.example.com"}
        assert revisions[1]["adapter_config"] == {"endpoint": "https://two.example.com"}

        source_after = (
            await client.get(f"/workspaces/{workspace.id}/sources/{source_id}")
        ).json()["data"]
        assert source_after["current_revision_number"] == 2


async def test_binding_pins_exact_revision_and_does_not_silently_drift(db_session):
    user = User(subject="binding-owner")
    db_session.add(user)
    await db_session.flush()
    workspace = await _seed_workspace_admin(db_session, user, "Workspace", "workspace-bind")
    project = Project(
        workspace_id=workspace.id, name="Project", slug="project", created_by_user_id=user.id
    )
    db_session.add(project)
    await db_session.commit()

    async with _build_client(db_session, user) as client:
        source = (
            await client.post(
                f"/workspaces/{workspace.id}/sources",
                json={
                    "name": "Source",
                    "slug": "source",
                    "adapter_type": "rss",
                    "adapter_config": {"feed_url": "https://v1.example.com"},
                },
            )
        ).json()["data"]

        binding = (
            await client.post(
                f"/workspaces/{workspace.id}/projects/{project.id}/source-bindings",
                json={
                    "source_id": source["id"],
                    "name": "Binding",
                    "slug": "binding",
                    "source_revision_number": 1,
                    "scope_config": {"targets": ["*"]},
                },
            )
        ).json()["data"]
        assert binding["current_revision_number"] == 1

        # Source gets a new revision after the binding is created.
        await client.post(
            f"/workspaces/{workspace.id}/sources/{source['id']}/revisions",
            json={"adapter_config": {"feed_url": "https://v2.example.com"}},
        )

        # The binding must still be pinned to revision 1 — no silent drift.
        binding_revisions = (
            await client.get(
                f"/workspaces/{workspace.id}/projects/{project.id}/source-bindings/{binding['id']}/revisions"
            )
        ).json()["data"]
        assert len(binding_revisions) == 1
        assert binding_revisions[0]["revision_number"] == 1

        source_revisions = (
            await client.get(f"/workspaces/{workspace.id}/sources/{source['id']}/revisions")
        ).json()["data"]
        rev1_id = next(r["id"] for r in source_revisions if r["revision_number"] == 1)
        rev2_id = next(r["id"] for r in source_revisions if r["revision_number"] == 2)
        assert binding_revisions[0]["pinned_source_revision_id"] == rev1_id
        assert binding_revisions[0]["pinned_source_revision_id"] != rev2_id

        # Explicit re-pin to revision 2 creates a new immutable binding revision.
        repinned = await client.post(
            f"/workspaces/{workspace.id}/projects/{project.id}/source-bindings/{binding['id']}/revisions",
            json={"source_revision_number": 2, "scope_config": {"targets": ["*"]}},
        )
        assert repinned.status_code == 201
        assert repinned.json()["data"]["revision_number"] == 2
        assert repinned.json()["data"]["pinned_source_revision_id"] == rev2_id


async def test_project_cannot_bind_source_from_another_workspace(db_session):
    user = User(subject="cross-workspace-user")
    db_session.add(user)
    await db_session.flush()
    workspace_a = await _seed_workspace_admin(db_session, user, "Workspace A", "cross-a")
    workspace_b = await _seed_workspace_admin(db_session, user, "Workspace B", "cross-b")
    project_b = Project(
        workspace_id=workspace_b.id, name="Project B", slug="project-b", created_by_user_id=user.id
    )
    db_session.add(project_b)
    await db_session.commit()

    async with _build_client(db_session, user) as client:
        source_a = (
            await client.post(
                f"/workspaces/{workspace_a.id}/sources",
                json={
                    "name": "Source A",
                    "slug": "source-a",
                    "adapter_type": "rss",
                    "adapter_config": {"feed_url": "https://a.example.com"},
                },
            )
        ).json()["data"]

        rejected = await client.post(
            f"/workspaces/{workspace_b.id}/projects/{project_b.id}/source-bindings",
            json={
                "source_id": source_a["id"],
                "name": "Cross Binding",
                "slug": "cross-binding",
                "source_revision_number": 1,
                "scope_config": {},
            },
        )
        assert rejected.status_code == 404
