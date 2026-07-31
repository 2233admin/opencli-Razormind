from pathlib import Path

import yaml

from backend.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_invokeai_settings_are_server_only_and_fail_closed() -> None:
    settings = Settings(_env_file=None)

    assert settings.invokeai_enabled is False
    assert settings.invokeai_base_url == "http://invokeai:9090"
    assert settings.invokeai_request_timeout_seconds > 0
    assert settings.image_asset_storage_path


def test_invokeai_compose_service_is_private_and_fails_closed_without_an_image() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["invokeai"]

    assert service["profiles"] == ["image-studio"]
    assert "ports" not in service
    assert service["image"].startswith(
        "${INVOKEAI_ATTESTED_IMAGE:-invalid.invalid/"
    )
    assert "@sha256:" in service["image"]
    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["networks"] == ["invokeai-private"]
    assert {"default", "invokeai-private"}.issubset(compose["services"]["api"]["networks"])


def test_invokeai_secrets_are_not_frontend_environment_variables() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "INVOKEAI_BASE_URL=" in example
    assert "INVOKEAI_API_TOKEN=" in example
    assert "INVOKEAI_ATTESTED_IMAGE=" in example
    assert "NEXT_PUBLIC_INVOKEAI" not in example
    assert "VITE_INVOKEAI" not in example
