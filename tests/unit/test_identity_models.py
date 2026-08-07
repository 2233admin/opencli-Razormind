import pytest
from sqlalchemy.exc import IntegrityError

from backend.database import Base
from backend.models import LocalAdmin, User, Workspace, WorkspaceMembership, WorkspaceRole


def test_identity_models_are_registered_for_migrations():
    assert {
        "users",
        "workspaces",
        "workspace_memberships",
        "teams",
        "team_memberships",
        "service_identities",
        "local_admin_credentials",
    } <= set(Base.metadata.tables)

    assert LocalAdmin.__tablename__ == "local_admin_credentials"


@pytest.mark.asyncio
async def test_workspace_membership_role_and_uniqueness(db_session):
    user = User(subject="oidc|42")
    workspace = Workspace(name="Acme", slug="acme")
    db_session.add_all((user, workspace))
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.ADMIN)
    )
    await db_session.commit()

    assert WorkspaceRole.ADMIN.value == "admin"

    db_session.add(
        WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.VIEWER)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
