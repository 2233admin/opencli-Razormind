"""cookiecloud_sync: mocks PyCookieCloud (the only place that speaks
CookieCloud's own protocol) and asserts the decrypted jar lands correctly in
our own domain-keyed cookie_jar via AuthManager."""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.auth import crypto
from backend.auth.cookiecloud_sync import CookieCloudSyncError, sync_from_cookiecloud
from backend.auth.manager import AuthManager

KEY = Fernet.generate_key().decode()


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


def _fake_client(decrypted_data):
    client = MagicMock()
    client.get_decrypted_data.return_value = decrypted_data
    return client


@pytest.mark.asyncio
async def test_sync_upserts_every_cookie_into_domain_keyed_jar(db_engine, monkeypatch):
    monkeypatch.setenv(crypto.ENV_KEY, KEY)
    decrypted = {
        "cookie_data": {
            "example.com": [
                {
                    "name": "session_id",
                    "value": "abc",
                    "domain": ".example.com",
                    "path": "/",
                    "sameSite": "unspecified",
                },
                {
                    "name": "csrf",
                    "value": "xyz",
                    "domain": "example.com",
                    "path": "/",
                    "secure": True,
                },
            ]
        },
        "local_storage_data": {"example.com": {"ignored": "not v1 scope"}},
    }
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)), \
         patch("PyCookieCloud.PyCookieCloud", return_value=_fake_client(decrypted)):
        synced = await sync_from_cookiecloud("http://cc.local", "uuid-1", "pw")
        assert synced == 2
        cookies = await AuthManager().resolve_cookies("example.com")

    by_name = {c["name"]: c for c in cookies}
    assert by_name["session_id"]["value"] == "abc"
    # CookieCloud's own "unspecified" normalization
    assert by_name["session_id"]["sameSite"] == "Lax"
    assert by_name["csrf"]["secure"] is True


@pytest.mark.asyncio
async def test_sync_raises_on_empty_response():
    with patch("PyCookieCloud.PyCookieCloud", return_value=_fake_client(None)):
        with pytest.raises(CookieCloudSyncError):
            await sync_from_cookiecloud("http://cc.local", "uuid-1", "wrong-pw")


@pytest.mark.asyncio
async def test_sync_skips_cookies_missing_domain_or_name(db_engine, monkeypatch):
    monkeypatch.setenv(crypto.ENV_KEY, KEY)
    decrypted = {
        "cookie_data": {
            "g1": [
                {"name": "", "value": "no-name", "domain": "example.com"},
                {"value": "no-name-key", "domain": "example.com"},
                {"name": "ok", "value": "kept", "domain": ""},
            ]
        }
    }
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)), \
         patch("PyCookieCloud.PyCookieCloud", return_value=_fake_client(decrypted)):
        synced = await sync_from_cookiecloud("http://cc.local", "uuid-1", "pw")
    assert synced == 0
