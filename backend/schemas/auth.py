from pydantic import BaseModel, Field, field_validator

# bcrypt's limit is 72 BYTES, not characters — a Chinese passphrase can blow
# past that well under 72 characters (each CJK character is 3 bytes in
# UTF-8), so this validates the encoded length rather than relying on
# Pydantic's character-counting max_length.
_MAX_PASSWORD_BYTES = 72


def _check_password_bytes(password: str) -> str:
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {_MAX_PASSWORD_BYTES} bytes")
    return password


class SetupStatusRead(BaseModel):
    setup_required: bool


class SetupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8)

    _check_password = field_validator("password")(_check_password_bytes)


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_BYTES)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=_MAX_PASSWORD_BYTES)
    new_password: str = Field(min_length=8)

    _check_password = field_validator("new_password")(_check_password_bytes)


class TokenRead(BaseModel):
    token: str
