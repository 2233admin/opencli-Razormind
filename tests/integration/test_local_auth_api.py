"""Integration tests for first-run local admin setup + password login
(backend/api/v1/identity.py's /auth/setup, /auth/login, /auth/change-password).

Each test gets a fresh in-memory DB (tests/conftest.py's db_engine fixture),
so setup_required is always true at the start of a test.
"""

import pytest

from backend.main import app
from backend.security.identity import IdentitySettings, get_request_identity, identity_dependency

SETUP_BODY = {
    "email": "Admin@Example.com",
    "display_name": "Test Admin",
    "password": "correct-horse-battery-staple",
}


@pytest.mark.asyncio
async def test_setup_status_true_on_fresh_instance(client):
    response = await client.get("/api/v1/auth/setup-status")
    assert response.status_code == 200
    assert response.json()["data"] == {"setup_required": True}


@pytest.mark.asyncio
async def test_setup_creates_admin_and_flips_status(client):
    response = await client.post("/api/v1/auth/setup", json=SETUP_BODY)
    assert response.status_code == 201
    token = response.json()["data"]["token"]
    assert token

    status_response = await client.get("/api/v1/auth/setup-status")
    assert status_response.json()["data"] == {"setup_required": False}

    me_response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_response.status_code == 200
    identity = me_response.json()["data"]
    assert identity["auth_method"] == "local"
    # email is lowercased/normalized server-side
    assert identity["email"] == "admin@example.com"
    # workspace ADMIN role, not the bootstrap god-flag
    assert identity["is_platform_admin"] is False


@pytest.mark.asyncio
async def test_setup_second_call_conflicts(client):
    first = await client.post("/api/v1/auth/setup", json=SETUP_BODY)
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/auth/setup",
        json={**SETUP_BODY, "email": "someone-else@example.com"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_setup_rejects_password_over_72_bytes(client):
    # A CJK passphrase can blow past 72 bytes well under 72 characters.
    response = await client.post(
        "/api/v1/auth/setup",
        json={**SETUP_BODY, "password": "测试" * 40},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_wrong_password_is_401(client):
    await client.post("/api/v1/auth/setup", json=SETUP_BODY)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": SETUP_BODY["email"], "password": "not-the-password"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email_is_401_not_500(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever12345"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_correct_password_issues_working_token(client):
    await client.post("/api/v1/auth/setup", json=SETUP_BODY)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": SETUP_BODY["email"], "password": SETUP_BODY["password"]},
    )
    assert login.status_code == 200
    token = login.json()["data"]["token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["auth_method"] == "local"


@pytest.mark.asyncio
async def test_change_password_then_old_fails_new_succeeds(client):
    setup = await client.post("/api/v1/auth/setup", json=SETUP_BODY)
    token = setup.json()["data"]["token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    change = await client.post(
        "/api/v1/auth/change-password",
        headers=auth_header,
        json={"current_password": SETUP_BODY["password"], "new_password": "a-new-passphrase-2"},
    )
    assert change.status_code == 200

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": SETUP_BODY["email"], "password": SETUP_BODY["password"]},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": SETUP_BODY["email"], "password": "a-new-passphrase-2"},
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_change_password_wrong_current_password_is_401(client):
    setup = await client.post("/api/v1/auth/setup", json=SETUP_BODY)
    token = setup.json()["data"]["token"]

    response = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "totally-wrong", "new_password": "a-new-passphrase-2"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_change_password_rejected_for_non_local_identity(client):
    # get_request_identity is a module-level singleton built once at import
    # (identity_dependency() snapshots IdentitySettings.from_env() then), so
    # monkeypatching Settings.bootstrap_admin_token afterward has no effect on
    # it — override the dependency directly instead, matching the pattern in
    # tests/integration/test_auth_api.py's test_fleet_token_header_... test.
    app.dependency_overrides[get_request_identity] = identity_dependency(
        IdentitySettings(
            issuer="https://id.example",
            audience="opencli",
            bootstrap_admin_token="test-bootstrap-token",
        )
    )
    try:
        response = await client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": "Bearer test-bootstrap-token"},
            json={"current_password": "x", "new_password": "a-new-passphrase-2"},
        )
    finally:
        app.dependency_overrides.pop(get_request_identity, None)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_oidc_style_rs256_token_still_falls_through_to_oidc_verifier(client):
    """Regression guard: the new alg-based branch in get_request_identity must
    not swallow RS256 tokens meant for the existing OIDC path. An RS256-header
    token with no OIDC configured should still fail as an OIDC verification
    error (503/401), not be misrouted into local-token verification."""
    # A syntactically-valid JWT header/payload with alg=RS256, unsigned/garbage
    # signature — OIDC isn't configured in tests, so this must 401 or 503, and
    # specifically must NOT succeed as if it were a local token.
    fake_rs256_token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJ4In0."
        "fake-signature"
    )
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {fake_rs256_token}"}
    )
    assert response.status_code in (401, 503)
