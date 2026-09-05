import subprocess

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_agent_image_packages_runtime_adapter_modules():
    dockerfile = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY backend/agent_server.py ./backend/agent_server.py" in dockerfile
    assert "COPY backend/agent_runtimes/ ./backend/agent_runtimes/" in dockerfile
    assert "COPY backend/miniflow/ ./backend/miniflow/" in dockerfile

    assert "COPY backend/security/ ./backend/security/" in dockerfile


def test_browser_images_declare_selectable_cloakbrowser_runtime():
    dockerfiles = (
        ROOT / "chrome" / "Dockerfile",
        ROOT / "agent" / "Dockerfile",
    )

    for path in dockerfiles:
        dockerfile = path.read_text(encoding="utf-8")

        assert "ARG BROWSER_ENGINE=chromium" in dockerfile
        assert "ARG CLOAKBROWSER_VERSION=0.5.10" in dockerfile
        assert (
            "COPY scripts/resolve-browser-executable.mjs "
            "/usr/local/bin/resolve-browser-executable.mjs"
        ) in dockerfile
        assert 'if [ "$BROWSER_ENGINE" = "cloakbrowser" ]; then' in dockerfile
        assert '"cloakbrowser@${CLOAKBROWSER_VERSION}"' in dockerfile
        assert '"playwright-core@^1.53.0"' in dockerfile
        assert (
            "env -u CLOAKBROWSER_VERSION "
            "CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser/cache node "
            "--input-type=module -e"
        ) in dockerfile


def test_browser_image_build_override_passes_engine_and_version_args():
    compose = (ROOT / "docker-compose.build.yml").read_text(encoding="utf-8")

    for anchor in ("x-agent-build:", "x-chrome-build:"):
        section_start = compose.index(anchor)
        section_end = compose.find("\nx-", section_start + len(anchor))
        if section_end == -1:
            section_end = compose.find("\nservices:", section_start)
        section = compose[section_start:section_end]

        assert "BROWSER_ENGINE: ${BROWSER_ENGINE-chromium}" in section
        assert "BROWSER_ENGINE: ${BROWSER_ENGINE:-chromium}" not in section
        assert "CLOAKBROWSER_VERSION: ${CLOAKBROWSER_VERSION:-0.5.10}" in section

def test_public_images_package_opencli_without_a_private_checkout():
    main_image = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    chrome_image = (ROOT / "chrome" / "Dockerfile").read_text(encoding="utf-8")
    agent_image = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    for dockerfile in (main_image, chrome_image, agent_image):
        assert "ARG OPENCLI_VERSION=1.8.7" in dockerfile
        assert "npm install -g @jackwener/opencli@${OPENCLI_VERSION}" in dockerfile
        assert "node /tmp/patch-opencli.js" in dockerfile
        assert "2233admin/OhMyOpenCLI" not in dockerfile
        assert "git clone ${OHMYOPENCLI_REPO}" not in dockerfile

    assert "command -v npm" in agent_image

def test_browser_images_package_the_pinned_violentmonkey_bundle_with_notices():
    chrome_image = (ROOT / "chrome" / "Dockerfile").read_text(encoding="utf-8")
    agent_image = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    for dockerfile in (chrome_image, agent_image):
        assert (
            "https://github.com/violentmonkey/violentmonkey/releases/download/"
            "v2.48.0/Violentmonkey-mv3-v2.48.0.zip"
        ) in dockerfile
        assert (
            "583ac595bb698a926eadb6064431fce1108dc2f2adb966ed984738824d2d5a54"
        ) in dockerfile
        assert "sha256sum -c -" in dockerfile
        assert (
            "f996dce5391963ed6badd93ad9e2ce2f957b2ad4e587112ea63b1f26317dfb57"
        ) in dockerfile
        assert "extensions/violentmonkey/LICENSE-MIT.txt" in dockerfile
        assert "LICENSE_SOURCE_URL" in dockerfile
        assert "https://github.com/violentmonkey/violentmonkey/tree/v2.48.0" in dockerfile


def test_pinned_violentmonkey_checksum_rejects_a_tampered_archive(tmp_path):
    archive = tmp_path / "Violentmonkey-mv3-v2.48.0.zip"
    archive.write_bytes(b"tampered")

    result = subprocess.run(
        ["sha256sum", "-c", "-"],
        capture_output=True,
        check=False,
        input=(
            "583ac595bb698a926eadb6064431fce1108dc2f2adb966ed984738824d2d5a54  "
            f"{archive}\n"
        ),
        text=True,
    )

    assert result.returncode != 0

def test_browser_entrypoints_start_the_opencli_1_8_7_daemon_module():
    chrome_entrypoint = (ROOT / "chrome" / "entrypoint.sh").read_text(encoding="utf-8")
    agent_entrypoint = (ROOT / "agent" / "entrypoint.sh").read_text(encoding="utf-8")

    for entrypoint in (chrome_entrypoint, agent_entrypoint):
        assert "@jackwener/opencli/dist/src/daemon.js" in entrypoint



def test_browser_images_fail_closed_until_userscripts_access_is_verified():
    chrome_image = (ROOT / "chrome" / "Dockerfile").read_text(encoding="utf-8")
    agent_image = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")
    chrome_entrypoint = (ROOT / "chrome" / "entrypoint.sh").read_text(encoding="utf-8")
    agent_entrypoint = (ROOT / "agent" / "entrypoint.sh").read_text(encoding="utf-8")

    for dockerfile in (chrome_image, agent_image):
        assert (
            "COPY scripts/ensure-violentmonkey-userscripts-access.mjs "
            "/usr/local/bin/ensure-violentmonkey-userscripts-access.mjs"
        ) in dockerfile
        assert "opencli-default/1/extensions/opencli-browser-bridge" in dockerfile
        assert "opencli-default/1/extensions/opencli-script-host" in dockerfile
    for entrypoint in (chrome_entrypoint, agent_entrypoint):
        assert "ensure-violentmonkey-userscripts-access.mjs" in entrypoint
        assert "violentmonkey_user_scripts_access" in entrypoint
        assert 'VIOLENTMONKEY_VERSION="$(read_manifest_component_version violentmonkey)"' in entrypoint
        assert "rm -f /tmp/browser-runtime-report.json" in entrypoint


def test_compose_and_native_start_use_the_opencli_v2_contract():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    native_start = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert compose.count("opencli-default/2/manifest.json") == 2
    assert "opencli-default/1/manifest.json" not in compose
    assert "npm install -g @jackwener/opencli@1.8.7" in native_start
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


def test_vnc_agent_image_is_the_registered_browser_bridge_runtime():
    dockerfile = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "agent" / "entrypoint.sh").read_text(encoding="utf-8")
    auto_enable = (ROOT / "agent" / "headless-auto-enable.js").read_text(encoding="utf-8")
    patcher = (ROOT / "agent" / "patch-browser-bridge-autostart.mjs").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    build = (ROOT / "docker-compose.build.yml").read_text(encoding="utf-8")

    assert "@browserbridge/bbx@${BBX_VERSION}" in dockerfile
    assert "BBX_EXTENSION_COMMIT" in dockerfile
    assert "scripts/package-extension.mjs" in dockerfile
    assert "x11vnc novnc websockify nginx" in dockerfile
    assert "COPY backend/agent_server.py ./backend/agent_server.py" in dockerfile
    assert "bbx install" in entrypoint
    assert "bbx-daemon" in entrypoint
    assert 'BBX_EXTENSION_DIR="/opt/browser-bridge-extension"' in entrypoint
    assert 'BUNDLE_EXTENSION_DIRS+=("$BBX_EXTENSION_DIR")' in entrypoint
    assert "env -u OPENCLI_DAEMON_PORT" in entrypoint
    assert "--profile-directory=Default" in entrypoint
    assert "--no-first-run" in entrypoint
    assert "patch-browser-bridge-autostart.mjs" in dockerfile
    assert "headless-auto-enable.js" in dockerfile
    assert "autoEnableHeadlessWindow" in auto_enable
    assert "/^https?:\\/\\//i.test(candidate.url)" in auto_enable
    adapter = (ROOT / "backend" / "agent_runtimes" / "bbx_adapter.py").read_text(
        encoding="utf-8"
    )
    assert "conversation-item" in adapter
    assert "initializeState" in patcher
    assert "websockify --web /usr/share/novnc 6080 localhost:5900" in entrypoint
    assert "https://www.doubao.com/chat" in entrypoint
    assert "AGENT_MODE: ${AGENT_MODE:-bridge}" in compose
    assert "AGENT_REGISTER: ${AGENT_REGISTER:-ws}" in compose
    assert "CENTRAL_API_URL: ${CENTRAL_API_URL:-http://api:8000}" in compose
    assert "AGENT_ADVERTISE_URL: ${AGENT_ADVERTISE_URL:-http://agent-1:19823}" in compose
    assert "API_AUTH_TOKEN: ${API_AUTH_TOKEN:-}" in compose
    assert "agent_profile_1:/home/agent/.config/chromium" in compose
    assert "${AGENT_PORT:-19823}:19823" in compose
    assert "agent-1:\n    <<: *agent-build" in build
