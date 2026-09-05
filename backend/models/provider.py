from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.auth import crypto
from backend.models.base import TimestampMixin


class ModelProvider(TimestampMixin):
    """Saved AI model provider configuration (credentials + endpoint).

    ``api_key`` at rest (AUDIT item B3 follow-up): the DB column stores Fernet
    ciphertext (see ``backend/auth/crypto.py``, the same scheme
    ``SourceCredential``/``AuthManager`` use), but every existing read site
    (``skill_channel``, ``crawl4ai``, ``distill``, ``openai_processor``, ...)
    reads ``provider.api_key`` expecting the raw plaintext key. To keep those
    call sites unchanged, the DB column is mapped to a private attribute
    (``_api_key_encrypted``) and ``api_key`` is exposed as a Python property:
    the getter transparently decrypts, the setter transparently encrypts.
    ``ModelProvider(api_key=plaintext)`` and ``setattr(provider, "api_key",
    plaintext)`` both go through the setter, so callers never see ciphertext.
    """

    __tablename__ = "model_providers"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # claude | openai | local
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, default="openai")
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # DB column is still named "api_key" (no migration-visible rename) but is
    # ciphertext-only; use the `api_key` property below to read/write plaintext.
    _api_key_encrypted: Mapped[str | None] = mapped_column(
        "api_key", Text, nullable=True
    )
    default_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def api_key(self) -> str | None:
        """Decrypt the stored ciphertext back to the plaintext API key.

        None/empty passthrough (no key configured — nothing to decrypt).
        A value that fails to decrypt is treated as legacy plaintext (e.g. a
        row written before this property existed, or before the data
        migration ran) and is returned as-is rather than raising: this is
        what makes the accompanying data migration safe to run twice and
        safe to run "late" against a row the app already touched — the
        getter never crashes a read path over an unmigrated value, it just
        hands back exactly what earlier code would have returned.
        """
        value = self._api_key_encrypted
        if not value:
            return value
        try:
            return crypto.decrypt(value)
        except crypto.CredentialCryptoError:
            return value

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        """Encrypt on write. None/empty stored as-is (nothing to encrypt)."""
        if not value:
            self._api_key_encrypted = value
            return
        self._api_key_encrypted = crypto.encrypt(value)
