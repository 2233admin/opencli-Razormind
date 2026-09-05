import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.api.v1.studio import create_studio_router
from backend.database import Base, get_db
from tests.fixtures.workflow_conformance import workflow_conformance_project


def _bootstrap_payload() -> dict:
    return {
        "project": {"name": "Concurrent project", "slug": "concurrent-publish"},
        "workflow": {
            "name": "Primary workflow",
            "graph": workflow_conformance_project(),
        },
    }


@pytest.mark.asyncio
async def test_studio_concurrent_publish_returns_conflict_instead_of_server_error(tmp_path):
    database_path = (tmp_path / "concurrent-publish.db").as_posix()
    db_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        poolclass=NullPool,
    )
    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    test_app = FastAPI()
    test_app.include_router(create_studio_router(), prefix="/api/v1")

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    test_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as api:
            workspace_id = (await api.get("/api/v1/workspaces")).json()["data"][0]["id"]
            bootstrap = await api.post(
                f"/api/v1/workspaces/{workspace_id}/projects/bootstrap",
                json=_bootstrap_payload(),
            )
            assert bootstrap.status_code == 201, bootstrap.text
            data = bootstrap.json()["data"]
            base_url = (
                f"/api/v1/workspaces/{workspace_id}/projects/{data['project']['id']}"
                f"/workflows/{data['primary_workflow']['id']}"
            )
            validation = (
                await api.post(f"{base_url}/draft/validation-runs", json={})
            ).json()["data"]
            publish_body = {
                "reason": "Concurrent release",
                "expectedRevision": 1,
                "validationRunId": validation["runId"],
            }

            responses = await asyncio.gather(
                api.post(f"{base_url}/versions", json=publish_body),
                api.post(f"{base_url}/versions", json=publish_body),
            )

            assert sorted(response.status_code for response in responses) == [201, 409]
            versions = (await api.get(f"{base_url}/versions")).json()["data"]
            assert [version["version"] for version in versions] == [1]
    finally:
        await db_engine.dispose()
