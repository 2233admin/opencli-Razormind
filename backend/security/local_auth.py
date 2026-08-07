"""Local single-admin authentication for self-hosted first-run deployments."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock

from fastapi import HTTPException, status
from jose import JWTError, jwt

from backend.config import get_settings

_ALGORITHM = "HS256"
_SUBJECT = "local-admin"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class LoginAttemptLimiter:
    """Small per-process guard against rapid password guessing."""

    def __init__(
        self, max_attempts: int = 10, window_seconds: int = 60, max_clients: int = 1024
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, client_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts.get(client_id)
            if not attempts:
                return
            while attempts and now - attempts[0] >= self.window_seconds:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(client_id, None)
                return
            if len(attempts) >= self.max_attempts:
                retry_after = max(1, int(self.window_seconds - (now - attempts[0])))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many authentication attempts",
                    headers={"Retry-After": str(retry_after)},
                )

    def record_failure(self, client_id: str) -> None:
        with self._lock:
            if client_id not in self._attempts and len(self._attempts) >= self.max_clients:
                self._attempts.pop(next(iter(self._attempts)))
            self._attempts[client_id].append(time.monotonic())

    def reset(self, client_id: str) -> None:
        with self._lock:
            self._attempts.pop(client_id, None)


login_attempt_limiter = LoginAttemptLimiter()


def validate_password(password: str) -> str:
    if len(password) < 12:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Password must be at least 12 characters",
        )
    if len(password) > 256:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password is too long")
    return password


def hash_password(password: str) -> str:
    encoded = validate_password(password).encode("utf-8")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        encoded, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = password_hash.split("$", 5)
        if scheme != "scrypt" or (int(n), int(r), int(p)) != (
            _SCRYPT_N,
            _SCRYPT_R,
            _SCRYPT_P,
        ):
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt),
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode("ascii"), expected)


def issue_session() -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": _SUBJECT, "typ": "local-admin", "iat": now, "exp": now + timedelta(hours=12)},
        get_settings().secret_key,
        algorithm=_ALGORITHM,
    )


def is_local_session(token: str) -> bool:
    try:
        claims = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGORITHM])
    except JWTError:
        return False
    return claims.get("sub") == _SUBJECT and claims.get("typ") == "local-admin"
