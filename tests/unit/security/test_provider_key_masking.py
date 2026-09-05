"""Unit tests for provider API key response masking (AUDIT item B3, task 3).

GET/POST/PATCH /providers must never echo back the plaintext api_key —
backend.schemas.provider.ModelProviderRead exposes only a boolean
``has_api_key`` and a masked ``api_key_preview`` instead.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from backend.schemas.provider import ModelProviderRead, _mask_api_key


def _fake_provider(**overrides):
    base = dict(
        id="prov-1",
        name="Test Provider",
        provider_type="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-abcd1234efgh5678",
        default_model="gpt-4o-mini",
        notes=None,
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_read_schema_has_no_api_key_field():
    assert "api_key" not in ModelProviderRead.model_fields


def test_read_schema_has_has_api_key_and_preview_fields():
    assert "has_api_key" in ModelProviderRead.model_fields
    assert "api_key_preview" in ModelProviderRead.model_fields


def test_from_model_masks_api_key():
    provider = _fake_provider()
    read = ModelProviderRead.from_model(provider)

    assert read.has_api_key is True
    assert read.api_key_preview is not None
    assert "sk-abcd1234efgh5678" not in read.api_key_preview
    assert read.api_key_preview.endswith("5678")


def test_from_model_dump_never_contains_raw_key():
    provider = _fake_provider()
    read = ModelProviderRead.from_model(provider)
    dumped = read.model_dump()

    assert "api_key" not in dumped
    assert "sk-abcd1234efgh5678" not in str(dumped)


def test_from_model_no_api_key_configured():
    provider = _fake_provider(api_key=None)
    read = ModelProviderRead.from_model(provider)

    assert read.has_api_key is False
    assert read.api_key_preview is None


def test_mask_api_key_short_key_still_masked():
    assert _mask_api_key("abc") == "....abc"


def test_mask_api_key_empty_or_none():
    assert _mask_api_key("") is None
    assert _mask_api_key(None) is None


def test_mask_api_key_typical_openai_style_key():
    masked = _mask_api_key("sk-proj-XXXXXXXXXXXXXXXXXXXXwxyz")
    assert masked == "sk-...wxyz"
