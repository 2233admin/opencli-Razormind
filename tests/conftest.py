"""Shared pytest fixtures."""

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Deployment config in the repo-root .env must not leak into tests: with
# API_AUTH_TOKEN set, every unauthenticated test request would 401 (fleet
# auth, ADR-0005). Clear before backend.main import snapshots settings —
# an empty env var wins over the .env file value in pydantic-settings.
os.environ["API_AUTH_TOKEN"] = ""
os.environ["AGENT_API_TOKEN"] = ""

from backend.auth import crypto
from backend.database import Base, get_db
from backend.main import app
from backend.models import *  # noqa: F401, F403 — register all models

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True, scope="session")
def _default_credential_key():
    """Provide a valid Fernet key for the whole test session so any code path
    that encrypts/decrypts secrets at rest works without every test wiring it
    up. ModelProvider.api_key is now an encrypting property (its setter calls
    crypto.encrypt), so constructing ModelProvider(api_key=...) — done all over
    the channel/provider tests — requires the key to be present; providing one
    default here is the single choke point instead of patching each test.

    Tests that specifically assert the missing-key behavior override this per
    function via monkeypatch.delenv(crypto.ENV_KEY); tests wanting a known key
    still monkeypatch.setenv their own. Set only if unset so those keep control."""
    if not os.environ.get(crypto.ENV_KEY):
        os.environ[crypto.ENV_KEY] = Fernet.generate_key().decode()
    yield


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession]:
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        rollback = session.rollback
        yield session
        await rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """HTTP test client with DB session override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def sample_source_data() -> dict:
    return {
        "name": "Test RSS Source",
        "channel_type": "rss",
        "channel_config": {"feed_url": "https://example.com/feed.xml", "max_entries": 10},
        "tags": ["test"],
        "enabled": True,
    }
