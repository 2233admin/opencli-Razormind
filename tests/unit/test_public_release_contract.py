from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_release_has_a_runnable_frontend_and_safe_compose_defaults() -> None:
    compose = source("docker-compose.yml")
    frontend_config = source("frontend/next.config.mjs")

    assert "\n  frontend:\n" in compose
    assert "\n  agent-1:\n" in compose
    assert "opencli-admin-frontend:${IMAGE_TAG:-0.4.0}" in compose
    assert "opencli-admin-chrome:${IMAGE_TAG:-0.4.0}" in compose
    assert '"127.0.0.1:${NOVNC_PORT:-6080}:6080"' in compose
    assert "CHROME_IMAGE:" in compose
    assert "./backend:/app/backend" not in compose
    assert "${INVOKEAI_ATTESTED_IMAGE:?" not in compose
    assert "${API_AUTH_TOKEN:?" in compose
    assert "${BOOTSTRAP_ADMIN_TOKEN:?" in compose
    assert 'output: "standalone"' in frontend_config
    assert (ROOT / "frontend" / "Dockerfile").is_file()


def test_public_release_has_one_ci_frontend_job_and_installers() -> None:
    workflow = source(".github/workflows/ci.yml")
    release_workflow = source(".github/workflows/release.yml")
    windows_installer = source("scripts/install.ps1")
    unix_installer = source("scripts/install.sh")

    assert workflow.count("\n  frontend:\n") == 1
    assert (ROOT / "scripts" / "install.sh").is_file()
    assert (ROOT / "scripts" / "install.ps1").is_file()
    assert (ROOT / ".env.docker.example").is_file()
    assert "BOOTSTRAP_ADMIN_TOKEN" in source(".env.docker.example")
    assert "BOOTSTRAP_ADMIN_TOKEN" in unix_installer
    assert "BOOTSTRAP_ADMIN_TOKEN" in windows_installer
    assert 'os.environ.get("NOVNC_BASE_PORT", 6080)' in source(
        "backend/api/v1/browsers.py"
    )
    assert "Assert-NativeSuccess" in windows_installer
    assert "-UseBasicParsing" in windows_installer
    assert "http://localhost:$frontendPort/login" in windows_installer
    assert 'raw="$(openssl rand -base64 32)" || return 1' in unix_installer
    assert 'if [ -z "$credential_encryption_key" ]; then' in unix_installer
    assert "packages: write" in release_workflow
    assert "id-token: write" not in release_workflow
