"""Password hashing for local (non-OIDC) accounts.

Uses the bcrypt package directly rather than passlib.context.CryptContext:
passlib 1.7.4's bcrypt backend self-test (detect_wrap_bug) breaks under
bcrypt>=4.1, which raises ValueError on its >72-byte internal test secret
instead of the silent truncation passlib's self-test expects. bcrypt's own
API doesn't carry that self-test, so it isn't exposed to the bug — pydantic's
max_length=72 on the password field (backend/schemas/auth.py) is the actual
72-byte-limit guard.
"""

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
