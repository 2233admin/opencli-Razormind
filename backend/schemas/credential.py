from pydantic import BaseModel, Field


class CredentialCreate(BaseModel):
    """A single secret to store, encrypted, for a source. ``key_name`` matches the
    field AuthManager.resolve_context() expects for the source's auth type —
    ``token`` (bearer), ``key`` (api_key), or ``username``/``password`` (basic)."""

    # Keep this aligned with SourceCredential.key_name's String(64) column.
    key_name: str = Field(..., min_length=1, max_length=64)
    secret: str = Field(..., min_length=1)


class CredentialKeyRead(BaseModel):
    """A stored credential's key name only — never the decrypted value."""

    key_name: str
