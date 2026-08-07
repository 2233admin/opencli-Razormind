"""Unit tests for the retryable-vs-permanent error classification."""

import pytest

from backend.pipeline.error_taxonomy import (
    CAPTCHA_CHALLENGE,
    is_captcha,
    is_retryable,
    is_retryable_http_status,
)


@pytest.mark.parametrize("error_type", [
    "TimeoutException",
    "ConnectError",
    "ConnectionError",
    "ReadError",
    "RemoteProtocolError",
    "OSError",
])
def test_transient_faults_are_retryable(error_type):
    assert is_retryable(error_type) is True


@pytest.mark.parametrize("error_type", [
    "ValueError",
    "KeyError",
    "TypeError",
    "FileNotFoundError",
    "JSONDecodeError",
])
def test_deterministic_faults_are_permanent(error_type):
    assert is_retryable(error_type) is False


def test_unknown_error_type_defaults_to_permanent():
    assert is_retryable("SomeBrandNewExceptionNobodyClassifiedYet") is False


def test_none_error_type_is_permanent():
    assert is_retryable(None) is False


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_leaked_429_and_5xx_are_retryable(status):
    assert is_retryable_http_status(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_4xx_are_permanent(status):
    assert is_retryable_http_status(status) is False


# ── AUDIT C13: gateway statuses + 408 must classify retryable ───────────────

@pytest.mark.parametrize("status", [504, 520, 522, 524])
def test_gateway_statuses_are_retryable(status):
    """504 (gateway timeout) and Cloudflare's own 520/522/524 gateway-error
    codes are transient upstream/proxy conditions, same as 502/503 — not
    reasons to give up permanently."""
    assert is_retryable_http_status(status) is True


def test_request_timeout_408_is_retryable():
    """408 is a transient per-request timeout, not a durably broken request —
    it belongs with 429/5xx, not with the permanent 4xx family."""
    assert is_retryable_http_status(408) is True


# ── captcha_challenge: third category (needs human, not retry/permanent) ─────


def test_captcha_challenge_constant_value():
    assert CAPTCHA_CHALLENGE == "captcha_challenge"


def test_captcha_challenge_is_not_retryable():
    """A captcha wall is not a transient fault — retrying immediately burns
    retry budget on a wall that only a human can clear."""
    assert is_retryable(CAPTCHA_CHALLENGE) is False


def test_captcha_challenge_is_captcha():
    assert is_captcha(CAPTCHA_CHALLENGE) is True


def test_none_is_not_captcha():
    assert is_captcha(None) is False


def test_empty_string_is_not_captcha():
    assert is_captcha("") is False


def test_ordinary_errors_are_not_captcha():
    for t in ("TimeoutException", "ValueError", "RetryableHTTPStatus", "SomeNewError"):
        assert is_captcha(t) is False
