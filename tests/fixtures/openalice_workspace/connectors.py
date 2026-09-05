"""Additional local-only scopes for real connector browser acceptance."""

from pathlib import Path


async def seed_connector_scopes(db_path: Path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.models.connector_reply import ConnectorInstallation
    from backend.models.identity import Workspace, WorkspaceMembership, WorkspaceRole
    from tests.fixtures.openalice_workspace.seed import USER_ID

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for suffix, role in [("other", WorkspaceRole.ADMIN), ("viewer", WorkspaceRole.VIEWER)]:
            workspace_id = f"openalice-e2e-{suffix}"
            db.add(Workspace(id=workspace_id, name=f"Connector {suffix}", slug=workspace_id))
            await db.flush()
            db.add(WorkspaceMembership(workspace_id=workspace_id, user_id=USER_ID, role=role))
        installation = ConnectorInstallation(
            public_id="openalice-viewer-connector",
            workspace_id="openalice-e2e-viewer",
            provider="feishu",
            name="Viewer workspace connector",
            app_id="cli_e2e_viewer",
            tenant_key="tenant_e2e_viewer",
            status="active",
        )
        installation.app_secret = "dummy-viewer-app-secret"
        installation.encrypt_key = "dummy-viewer-encrypt-key"
        installation.verification_token = "dummy-viewer-verification-token"
        db.add(installation)
        await db.commit()
    await engine.dispose()
