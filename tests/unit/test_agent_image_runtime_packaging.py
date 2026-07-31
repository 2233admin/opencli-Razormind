from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_agent_image_packages_runtime_adapter_modules():
    dockerfile = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY backend/agent_server.py ./backend/agent_server.py" in dockerfile
    assert "COPY backend/agent_runtimes/ ./backend/agent_runtimes/" in dockerfile
    assert "COPY backend/miniflow/ ./backend/miniflow/" in dockerfile


def test_public_images_package_opencli_without_a_private_checkout():
    main_image = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    agent_image = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    for dockerfile in (main_image, agent_image):
        assert "ARG OPENCLI_VERSION=1.8.5" in dockerfile
        assert "npm install -g @jackwener/opencli@${OPENCLI_VERSION}" in dockerfile
        assert "node /tmp/patch-opencli.js" in dockerfile
        assert "2233admin/OhMyOpenCLI" not in dockerfile
        assert "git clone ${OHMYOPENCLI_REPO}" not in dockerfile


def test_native_adapter_pack_install_requires_an_explicit_repository():
    windows = (ROOT / "scripts" / "install-managed-opencli.ps1").read_text(
        encoding="utf-8"
    )
    linux = (ROOT / "scripts" / "install-agent.sh").read_text(encoding="utf-8")

    assert '[string]$OhMyOpenCliRepo,' in windows
    assert "2233admin/OhMyOpenCLI" not in windows
    assert 'OHMYOPENCLI_REPO="${OHMYOPENCLI_REPO:-}"' in linux
    assert 'if [[ -n "$OHMYOPENCLI_REPO" ]]; then' in linux


def test_anonymous_agent_profiles_are_fresh_per_agent_start():
    entrypoint = (ROOT / "agent" / "entrypoint.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install-agent.sh").read_text(encoding="utf-8")

    assert 'OPENCLI_BROWSER_PROFILE_KIND:-authenticated' in entrypoint
    assert "mktemp -d /tmp/opencli-anonymous-profile.XXXXXX" in entrypoint
    assert 'OPENCLI_BROWSER_PROFILE_KIND" == "anonymous"' in installer
    assert "mktemp -d /tmp/opencli-anonymous-profile.XXXXXX" in installer
