from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_agent_image_packages_runtime_adapter_modules():
    dockerfile = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY backend/agent_server.py ./backend/agent_server.py" in dockerfile
    assert "COPY backend/agent_runtimes/ ./backend/agent_runtimes/" in dockerfile
    assert "COPY backend/miniflow/ ./backend/miniflow/" in dockerfile


def test_agent_image_pins_managed_acquisition_runtime():
    dockerfile = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG OPENCLI_VERSION=1.8.5" in dockerfile
    assert (
        "ARG OHMYOPENCLI_COMMIT="
        "bfe1c25b4b12661058dd6e9980c562a09f230cc7"
    ) in dockerfile
    assert (
        "ARG DOUBAO_CAPABILITY_COMMIT="
        "bfe1c25b4b12661058dd6e9980c562a09f230cc7"
    ) in dockerfile
    assert "git checkout --detach ${OHMYOPENCLI_COMMIT}" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run bootstrap" in dockerfile
    assert "OHMYOPENCLI_ROOT=/opt/ohmyopencli" in dockerfile
    assert "COPY scripts/patch-opencli.js /tmp/patch-opencli.js" in dockerfile
    assert "node /tmp/patch-opencli.js" in dockerfile


def test_managed_runtime_plugin_is_registered_for_each_final_image_user():
    cases = [
        (ROOT / "Dockerfile", "appuser", "/home/appuser"),
        (ROOT / "agent" / "Dockerfile", "agent", "/home/agent"),
    ]

    for path, user, home in cases:
        dockerfile = path.read_text(encoding="utf-8")
        user_creation = dockerfile.index(f"useradd -m -u 1000 {user}")
        user_bootstrap = dockerfile.index(f"HOME={home} npm run bootstrap")
        final_user = dockerfile.index(f"USER {user}")

        assert user_creation < user_bootstrap < final_user


def test_every_installer_allows_an_audited_ohmyopencli_source_override():
    main_image = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    agent_image = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")
    windows = (ROOT / "scripts" / "install-managed-opencli.ps1").read_text(
        encoding="utf-8"
    )
    linux = (ROOT / "scripts" / "install-agent.sh").read_text(encoding="utf-8")

    assert "ARG OHMYOPENCLI_REPO=" in main_image
    assert "git clone ${OHMYOPENCLI_REPO}" in main_image
    assert "ARG OHMYOPENCLI_REPO=" in agent_image
    assert "git clone ${OHMYOPENCLI_REPO}" in agent_image
    assert '[string]$OhMyOpenCliRepo = "https://github.com/2233admin/OhMyOpenCLI.git"' in windows
    assert "git clone $OhMyOpenCliRepo $OhMyOpenCliRoot" in windows
    assert (
        'OHMYOPENCLI_REPO="${OHMYOPENCLI_REPO:-'
        'https://github.com/2233admin/OhMyOpenCLI.git}"'
    ) in linux
    assert 'git clone "$OHMYOPENCLI_REPO" "$OHMYOPENCLI_ROOT"' in linux


def test_both_linux_images_package_the_cross_platform_readiness_trace_verifier():
    for path in (ROOT / "Dockerfile", ROOT / "agent" / "Dockerfile"):
        dockerfile = path.read_text(encoding="utf-8")
        assert (
            "COPY scripts/verify_managed_opencli_runtime.py "
            "./scripts/verify_managed_opencli_runtime.py"
        ) in dockerfile


def test_anonymous_agent_profiles_are_fresh_per_agent_start():
    entrypoint = (ROOT / "agent" / "entrypoint.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install-agent.sh").read_text(encoding="utf-8")

    assert 'OPENCLI_BROWSER_PROFILE_KIND:-authenticated' in entrypoint
    assert "mktemp -d /tmp/opencli-anonymous-profile.XXXXXX" in entrypoint
    assert 'OPENCLI_BROWSER_PROFILE_KIND" == "anonymous"' in installer
    assert "mktemp -d /tmp/opencli-anonymous-profile.XXXXXX" in installer
