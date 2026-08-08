import pytest
from fastapi import HTTPException

from backend.config import get_settings
from backend.security.local_auth import LoginAttemptLimiter, verify_password


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_first_run_setup_creates_local_admin_and_session(client, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "first-run-secret")
    monkeypatch.setenv("SECRET_KEY", "test-session-signing-key")
    monkeypatch.setenv("API_AUTH_TOKEN", "fleet-secret")
    get_settings.cache_clear()

    status = await client.get("/api/v1/auth/local/status")
    assert status.status_code == 200
    assert status.json()["data"] == {"configured": False}
    assert (await client.get("/api/v1/auth/local/status/extra")).status_code == 401

    denied = await client.post(
        "/api/v1/auth/local/setup",
        json={"bootstrap_token": "wrong", "password": "long-enough-password"},
    )
    assert denied.status_code == 401

    setup = await client.post(
        "/api/v1/auth/local/setup",
        json={"bootstrap_token": "first-run-secret", "password": "long-enough-password"},
    )
    assert setup.status_code == 200
    token = setup.json()["data"]["access_token"]

    identity = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert identity.status_code == 200
    assert identity.json()["data"]["auth_method"] == "local"
    assert identity.json()["data"]["is_platform_admin"] is True

    identity_with_stale_fleet_token = await client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-API-Token": "stale-browser-token",
        },
    )
    assert identity_with_stale_fleet_token.status_code == 200
    assert identity_with_stale_fleet_token.json()["data"]["auth_method"] == "local"


@pytest.mark.asyncio
async def test_local_admin_login_and_second_setup_are_guarded(client, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "first-run-secret")
    monkeypatch.setenv("SECRET_KEY", "test-session-signing-key")
    get_settings.cache_clear()
    payload = {"bootstrap_token": "first-run-secret", "password": "long-enough-password"}
    assert (await client.post("/api/v1/auth/local/setup", json=payload)).status_code == 200
    assert (await client.post("/api/v1/auth/local/setup", json=payload)).status_code == 409

    denied = await client.post(
        "/api/v1/auth/local/login", json={"password": "incorrect-password"}
    )
    assert denied.status_code == 401
    accepted = await client.post(
        "/api/v1/auth/local/login", json={"password": "long-enough-password"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["access_token"]


def test_login_attempt_limiter_blocks_and_can_reset():
    limiter = LoginAttemptLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("client")
    limiter.record_failure("client")

    with pytest.raises(HTTPException) as exc_info:
        limiter.check("client")
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"]

    limiter.reset("client")
    limiter.check("client")


def test_login_attempt_limiter_bounds_tracked_clients():
    limiter = LoginAttemptLimiter(max_attempts=1, window_seconds=60, max_clients=1)
    limiter.record_failure("first")
    limiter.check("new-client")
    limiter.record_failure("second")

    limiter.check("first")
    with pytest.raises(HTTPException):
        limiter.check("second")


def test_password_hash_rejects_untrusted_work_factor():
    forged = "scrypt$1073741824$8$1$c2FsdA==$ZGlnZXN0"
    assert verify_password("long-enough-password", forged) is False
