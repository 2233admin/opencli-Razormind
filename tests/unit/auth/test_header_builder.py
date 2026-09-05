"""build_auth_header: the single source of truth AuthManager.resolve_context()
and ApiChannel._resolve_auth_headers() both delegate to."""

import base64

from backend.auth.header_builder import build_auth_header


def test_bearer_with_token():
    assert build_auth_header("bearer", {"token": "tok-1"}) == {"Authorization": "Bearer tok-1"}


def test_bearer_without_token_returns_empty():
    assert build_auth_header("bearer", {}) == {}


def test_api_key_with_key_default_header():
    assert build_auth_header("api_key", {"key": "k-1"}) == {"X-API-Key": "k-1"}


def test_api_key_custom_header_name():
    assert build_auth_header(
        "api_key", {"key": "k-1"}, header_name="X-Custom"
    ) == {"X-Custom": "k-1"}


def test_api_key_without_key_returns_empty():
    assert build_auth_header("api_key", {}) == {}


def test_basic_with_both_username_and_password():
    headers = build_auth_header("basic", {"username": "u", "password": "p"})
    expected = base64.b64encode(b"u:p").decode()
    assert headers == {"Authorization": f"Basic {expected}"}


def test_basic_with_only_username():
    headers = build_auth_header("basic", {"username": "u"})
    expected = base64.b64encode(b"u:").decode()
    assert headers == {"Authorization": f"Basic {expected}"}


def test_basic_with_neither_returns_empty():
    """Never send a placeholder 'Basic <base64 of \":\">' header — that's worse
    than sending no auth at all."""
    assert build_auth_header("basic", {}) == {}


def test_unknown_auth_kind_returns_empty():
    assert build_auth_header("oauth2", {"token": "x"}) == {}
