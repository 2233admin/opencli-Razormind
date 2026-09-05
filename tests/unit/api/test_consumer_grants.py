import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.v1 import create_v1_router
from backend.api.v1.consumer_grants import router
from backend.database import get_db
from backend.models.identity import (
    ServiceIdentity,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow.workflow_plugins import WorkflowPluginRegistry


async def _client(db_session, subject: str, api_router=router) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router)

    async def override_db():
        yield db_session

    async def override_identity():
        return RequestIdentity(subject=subject)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = override_identity
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_member(db_session, role: WorkspaceRole, suffix: str = "primary"):
    user = User(subject=f"{role.value}-{suffix}")
    workspace = Workspace(name=f"Workspace {suffix}", slug=f"workspace-{suffix}")
    db_session.add_all((user, workspace))
    await db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role))
    service_identity = ServiceIdentity(workspace_id=workspace.id, name="external-consumer")
    db_session.add(service_identity)
    await db_session.commit()
    return workspace, service_identity


async def _add_member(db_session, workspace: Workspace, role: WorkspaceRole, suffix: str):
    user = User(subject=f"{role.value}-{suffix}")
    db_session.add(user)
    await db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role))
    await db_session.commit()
    return user


async def test_consumer_grants_are_registered_on_v1_api(db_session):
    workspace, _ = await _seed_member(db_session, WorkspaceRole.VIEWER)
    client = await _client(db_session, "viewer-primary", create_v1_router(WorkflowPluginRegistry()))

    async with client:
        response = await client.get(f"/api/v1/workspaces/{workspace.id}/consumer-grants")

    assert response.status_code == 200


async def test_admin_creates_bounded_consumer_grant(db_session):
    workspace, service_identity = await _seed_member(db_session, WorkspaceRole.ADMIN)
    client = await _client(db_session, "admin-primary")

    async with client:
        response = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": service_identity.id,
                "name": "accepted-news",
                "resource_scope": {
                    "source_ids": ["source-news"],
                    "record_schema_ids": ["news.v1"],
                },
                "data_scope": {
                    "accepted_records": True,
                    "allowed_summaries": ["editorial"],
                    "explicit_evidence": ["source_url", "accepted_at"],
                },
                "quota": {
                    "requests_per_minute": 30,
                    "records_per_day": 1000,
                    "egress_bytes_per_day": 10000000,
                },
            },
        )

    assert response.status_code == 201
    grant = response.json()["data"]
    assert grant["service_identity_id"] == service_identity.id
    assert grant["status"] == "enabled"
    assert grant["resource_scope"] == {
        "all_accepted_records": False,
        "source_ids": ["source-news"],
        "record_schema_ids": ["news.v1"],
        "record_ids": [],
    }
    assert grant["data_scope"] == {
        "accepted_records": True,
        "allowed_summaries": ["editorial"],
        "explicit_evidence": ["source_url", "accepted_at"],
    }
    assert "workspace_role" not in grant
    assert "plans" not in grant["data_scope"]
    assert "agents" not in grant["data_scope"]
    assert "actuator" not in grant["data_scope"]


async def test_viewer_can_list_workspace_consumer_grants(db_session):
    workspace, service_identity = await _seed_member(db_session, WorkspaceRole.ADMIN)
    viewer = await _add_member(db_session, workspace, WorkspaceRole.VIEWER, "reader")
    admin_client = await _client(db_session, "admin-primary")
    async with admin_client:
        created = await admin_client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": service_identity.id,
                "name": "viewer-visible",
                "resource_scope": {"record_ids": ["record-1"]},
                "data_scope": {"accepted_records": True},
                "quota": {
                    "requests_per_minute": 5,
                    "records_per_day": 10,
                    "egress_bytes_per_day": 1000,
                },
            },
        )

    viewer_client = await _client(db_session, viewer.subject)
    async with viewer_client:
        response = await viewer_client.get(f"/workspaces/{workspace.id}/consumer-grants")

    assert created.status_code == 201
    assert response.status_code == 200
    assert [grant["name"] for grant in response.json()["data"]] == ["viewer-visible"]


async def test_maintainer_disables_enables_and_revokes_consumer_grant(db_session):
    workspace, service_identity = await _seed_member(db_session, WorkspaceRole.MAINTAINER)
    client = await _client(db_session, "maintainer-primary")
    async with client:
        created = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": service_identity.id,
                "name": "lifecycle",
                "resource_scope": {"all_accepted_records": True},
                "data_scope": {"allowed_summaries": ["brief"]},
                "quota": {
                    "requests_per_minute": 5,
                    "records_per_day": 10,
                    "egress_bytes_per_day": 1000,
                },
            },
        )
        grant_id = created.json()["data"]["id"]
        disabled = await client.patch(
            f"/workspaces/{workspace.id}/consumer-grants/{grant_id}",
            json={"enabled": False},
        )
        enabled = await client.patch(
            f"/workspaces/{workspace.id}/consumer-grants/{grant_id}",
            json={"enabled": True},
        )
        revoked = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants/{grant_id}/revoke",
            json={"reason": "Consumer integration retired"},
        )
        cannot_restore = await client.patch(
            f"/workspaces/{workspace.id}/consumer-grants/{grant_id}",
            json={"enabled": True},
        )

    assert created.status_code == 201
    assert disabled.json()["data"]["status"] == "disabled"
    assert enabled.json()["data"]["status"] == "enabled"
    assert revoked.json()["data"]["status"] == "revoked"
    assert revoked.json()["data"]["enabled"] is False
    assert revoked.json()["data"]["revocation_reason"] == "Consumer integration retired"
    assert cannot_restore.status_code == 409


@pytest.mark.parametrize("role", [WorkspaceRole.OPERATOR, WorkspaceRole.VIEWER])
async def test_operator_and_viewer_can_view_but_cannot_manage_grants(db_session, role):
    workspace, service_identity = await _seed_member(db_session, role)
    client = await _client(db_session, f"{role.value}-primary")
    async with client:
        listed = await client.get(f"/workspaces/{workspace.id}/consumer-grants")
        denied = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": service_identity.id,
                "name": "not-allowed",
                "resource_scope": {"all_accepted_records": True},
                "data_scope": {"accepted_records": True},
                "quota": {
                    "requests_per_minute": 1,
                    "records_per_day": 1,
                    "egress_bytes_per_day": 1,
                },
            },
        )

    assert listed.status_code == 200
    assert denied.status_code == 403


async def test_consumer_grant_rejects_operational_authority_and_run_data(db_session):
    workspace, service_identity = await _seed_member(db_session, WorkspaceRole.ADMIN)
    client = await _client(db_session, "admin-primary")
    async with client:
        response = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": service_identity.id,
                "name": "unsafe",
                "workspace_role": "viewer",
                "resource_scope": {"plans": ["plan-1"]},
                "data_scope": {
                    "accepted_records": True,
                    "run_trace": True,
                    "runtime_artifacts": True,
                    "record_candidates": True,
                    "credentials": True,
                    "operational_config": True,
                    "agents": ["agent-1"],
                    "actuator": True,
                },
                "quota": {
                    "requests_per_minute": 1,
                    "records_per_day": 1,
                    "egress_bytes_per_day": 1,
                },
            },
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "forbidden_data_class",
    [
        "operational_config",
        "credentials",
        "record_candidates",
        "runtime_artifacts",
        "run_trace",
    ],
)
async def test_consumer_grant_rejects_forbidden_explicit_evidence(db_session, forbidden_data_class):
    workspace, service_identity = await _seed_member(db_session, WorkspaceRole.ADMIN)
    client = await _client(db_session, "admin-primary")
    async with client:
        response = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": service_identity.id,
                "name": "unsafe-evidence",
                "resource_scope": {"all_accepted_records": True},
                "data_scope": {
                    "accepted_records": True,
                    "explicit_evidence": [forbidden_data_class],
                },
                "quota": {
                    "requests_per_minute": 1,
                    "records_per_day": 1,
                    "egress_bytes_per_day": 1,
                },
            },
        )

    assert response.status_code == 422


async def test_consumer_grant_rejects_cross_workspace_service_identity(db_session):
    workspace, _ = await _seed_member(db_session, WorkspaceRole.ADMIN)
    other_workspace = Workspace(name="Other", slug="other-consumer-workspace")
    db_session.add(other_workspace)
    await db_session.flush()
    other_identity = ServiceIdentity(workspace_id=other_workspace.id, name="other-consumer")
    db_session.add(other_identity)
    await db_session.commit()

    client = await _client(db_session, "admin-primary")
    async with client:
        response = await client.post(
            f"/workspaces/{workspace.id}/consumer-grants",
            json={
                "service_identity_id": other_identity.id,
                "name": "cross-workspace",
                "resource_scope": {"all_accepted_records": True},
                "data_scope": {"accepted_records": True},
                "quota": {
                    "requests_per_minute": 1,
                    "records_per_day": 1,
                    "egress_bytes_per_day": 1,
                },
            },
        )

    assert response.status_code == 422
