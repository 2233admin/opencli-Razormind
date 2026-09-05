import json
import os
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_RELEASE_VERSION = os.environ.get("PUBLIC_RELEASE_VERSION", "0.4.1")
PUBLIC_REPOSITORY = "2233admin/opencli-Razormind"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def compose_contract() -> dict:
    return yaml.safe_load(source("docker-compose.yml"))


def env_contract() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in source(".env.docker.example").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def source_docker_recipes(readme: str) -> list[str]:
    return [
        block
        for block in re.findall(r"~~~bash\n(.*?)\n~~~", readme, re.DOTALL)
        if "# 仅首次执行以下初始化步骤" in block
    ]


def test_public_release_has_a_runnable_control_plane_and_durable_defaults() -> None:
    compose = compose_contract()
    services = compose["services"]
    frontend_config = source("frontend/next.config.mjs")

    expected_images = {
        "api": "opencli-admin-api",
        "frontend": "opencli-admin-frontend",
        "agent-1": "opencli-admin-chrome",
        "agent": "opencli-admin-agent",
    }
    for service, image_name in expected_images.items():
        assert (
            services[service]["image"]
            == "${DOCKER_REGISTRY:-ghcr.io/}${DOCKER_IMAGE_NAMESPACE:-2233admin}/"
            f"{image_name}:${{IMAGE_TAG:-{PUBLIC_RELEASE_VERSION}}}"
            + ("${CHROME_SUFFIX:-}" if service == "agent" else "")
        )

    # Browser degradation must not block control-plane creation. Browser work
    # remains fail-closed in the runtime pool; Compose only removes the startup
    # dependency that previously kept the API and frontend offline.
    assert "agent-1" not in services["api"].get("depends_on", {})
    assert services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"
    for service in ("api", "frontend", "agent-1"):
        assert services[service]["restart"] == "unless-stopped"

    assert services["api"]["volumes"][0] == "db_data:/data"
    assert services["agent-1"]["volumes"][0] == ("agent_profile_1:/home/chrome/.config/chromium")
    assert {"db_data", "agent_profile_1"} <= compose["volumes"].keys()
    assert services["agent-1"]["ports"] == ["127.0.0.1:${NOVNC_PORT:-6080}:6080"]
    assert "./backend:/app/backend" not in source("docker-compose.yml")
    assert "${INVOKEAI_ATTESTED_IMAGE:?" not in source("docker-compose.yml")
    assert "${API_AUTH_TOKEN:?" in source("docker-compose.yml")
    assert "${BOOTSTRAP_ADMIN_TOKEN:?" in source("docker-compose.yml")
    assert 'output: "standalone"' in frontend_config
    assert (ROOT / "frontend" / "Dockerfile").is_file()


def test_public_artifacts_resolve_to_the_tagged_release_contract() -> None:
    readme = source("README.md")
    product_context = source("CONTEXT.md")
    windows_installer = source("scripts/install.ps1")
    unix_installer = source("scripts/install.sh")

    assert env_contract()["IMAGE_TAG"] == PUBLIC_RELEASE_VERSION
    assert tomllib.loads(source("pyproject.toml"))["project"]["version"] == PUBLIC_RELEASE_VERSION
    assert json.loads(source("package.json"))["version"] == PUBLIC_RELEASE_VERSION
    assert json.loads(source("package-lock.json"))["version"] == PUBLIC_RELEASE_VERSION
    assert json.loads(source("frontend/package.json"))["version"] == PUBLIC_RELEASE_VERSION
    for application_module in (
        "backend/main.py",
        "backend/agent_server.py",
        "backend/mcp_server.py",
    ):
        assert f'version="{PUBLIC_RELEASE_VERSION}"' in source(application_module)
    assert f'name = "opencli-admin"\nversion = "{PUBLIC_RELEASE_VERSION}"' in source("uv.lock")
    installer_urls = (
        f"https://raw.githubusercontent.com/{PUBLIC_REPOSITORY}/v{PUBLIC_RELEASE_VERSION}/scripts/install.sh",
        f"https://raw.githubusercontent.com/{PUBLIC_REPOSITORY}/v{PUBLIC_RELEASE_VERSION}/scripts/install.ps1",
    )
    for image in ("api", "frontend", "chrome", "agent"):
        assert f"ghcr.io/2233admin/opencli-admin-{image}:{PUBLIC_RELEASE_VERSION}" in readme

    assert f"OPENCLI_ADMIN_VERSION:-{PUBLIC_RELEASE_VERSION}" in unix_installer
    assert f"OPENCLI_ADMIN_REPOSITORY:-{PUBLIC_REPOSITORY}" in unix_installer
    assert f'"{PUBLIC_RELEASE_VERSION}"' in windows_installer
    assert f'"{PUBLIC_REPOSITORY}"' in windows_installer
    assert "v0.4.0" not in readme
    assert f"The public v{PUBLIC_RELEASE_VERSION} release" in product_context
    assert "The public v0.4.0 release" not in product_context
    assert "密码：`admin`" not in readme
    assert "首次初始化生成并保存在 `.local-admin-password` 中的随机密码" in readme
    assert "下一版本安装器完成后" not in readme
    assert "next-release 安装器结束时" not in readme
    if PUBLIC_RELEASE_VERSION == "0.4.1":
        assert all(url not in readme for url in installer_urls)
        assert "not present in the immutable `v0.4.1` source archive" in readme
        assert "no next-release tag or installer URL exists yet" in readme
        assert "v0.4.1` 不具备此契约" in readme
    else:
        assert all(url in readme for url in installer_urls)


def test_source_docker_recipes_initialize_local_admin_before_starting_services() -> None:
    recipes = source_docker_recipes(source("README.md"))
    assert len(recipes) == 2
    assert "/.local-admin-password" in source(".gitignore").splitlines()
    dockerignore = source(".dockerignore").splitlines()
    assert ".local-admin-password" in dockerignore
    assert ".opencli-restart-recovery-state*" in dockerignore

    compose_prefix = (
        "IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml"
    )
    build = f"{compose_prefix} build api frontend agent-1"
    run_initializer = f"{compose_prefix} run --rm -T --no-deps api python -c"
    start = f"{compose_prefix} up -d --no-build --wait"
    build_guard = f"if ! {build}; then"
    init_guard = f"if ! printf '%s' \"$local_admin_password\" | {run_initializer} " + "\\"
    for recipe in recipes:
        assert "# 仅首次执行以下初始化步骤" in recipe
        assert recipe.count(compose_prefix) == 3
        assert "local_admin_password_file=.local-admin-password" in recipe
        assert "abort_local_admin_password()" in recipe
        assert "  exit 1\n}" in recipe
        assert "return 1 2>/dev/null || exit 1" not in recipe
        assert 'if ! local_admin_password="$(cat "$local_admin_password_file")"; then' in recipe
        assert 'if ! local_admin_password="$(openssl rand -hex 24)"; then' in recipe
        assert "grep -Eq '^[0-9A-Fa-f]{48}$'" in recipe
        assert "validate_local_admin_password" in recipe
        assert 'if [ -s "$local_admin_password_file" ]; then' in recipe
        assert "password must be exactly 48 hexadecimal characters" in recipe
        assert build_guard in recipe
        assert init_guard in recipe
        assert (
            'if ! printf \'%s\\n\' "$local_admin_password" > "$local_admin_password_file"; then'
        ) in recipe
        assert 'if ! chmod 600 "$local_admin_password_file"; then' in recipe
        assert "initialize_password_hash 只写入一次 /data/local-admin-password.hash 及其" in recipe
        assert "/data/local-admin-password.hash.initialized marker" in recipe
        assert "printf '%s' \"$local_admin_password\" |" in recipe
        assert recipe.index(build) < recipe.index(run_initializer) < recipe.index(start)
        assert recipe.index("validate_local_admin_password\n") < recipe.index(
            "printf '%s\\n' \"$local_admin_password\" >"
        )
        assert (
            "docker compose -f docker-compose.yml -f docker-compose.build.yml up --build"
            not in recipe
        )
        assert "hash_password(sys.stdin.read().strip())" in recipe
        assert '"/data/local-admin-password.hash"' in recipe
        initializer_line = next(
            line for line in recipe.splitlines() if "run --rm -T --no-deps api python -c" in line
        )
        compose_command = initializer_line[initializer_line.index("docker compose") :]
        assert "local_admin_password" not in compose_command


def test_installers_report_boot_recovery_without_mutating_host_services_or_logging_tokens() -> None:
    windows_installer = source("scripts/install.ps1")
    unix_installer = source("scripts/install.sh")
    windows_recovery = source("scripts/install-recovery.ps1")
    unix_recovery = source("scripts/install-recovery.sh")

    for installer in (windows_recovery, unix_recovery):
        assert "Restart prerequisite verified" in installer
        assert "boot prerequisites only" in installer
        assert "has not tested a host restart" in installer
        assert "Restart recovery unverified" in installer
        assert "docker compose up/start/restart" in installer
        assert "docker info" in installer
        assert "api" in installer
        assert "frontend" in installer
        assert "agent-1" in installer
        assert "{{.State.Status}}" in installer
        assert "{{if .State.Health}}{{.State.Health.Status}}" in installer
        assert "PRAGMA quick_check" in installer
        assert "/data/local-admin-password.hash" in installer
        assert 'state.with_name(f"{state.name}.initialized")' in installer
        assert "/home/chrome/.config/chromium" in installer
        assert "http://localhost:" in installer
        assert "Restart recovery: verified" not in installer
        assert "SENTINEL" in installer.upper()
        assert "/api/v1/auth/me" in installer
        assert "load_password_hash" in installer
        assert "opencli-local-auth-state-v1:initial" in installer
        assert "opencli-local-auth-state-v1:changed" in installer
        assert "sha256" not in installer
        assert "AUTH_DIGEST" not in installer
        assert "auth_digest" not in installer

    assert "/proc/sys/kernel/osrelease" in unix_recovery
    assert "/proc/version" in unix_recovery
    assert "grep -qi microsoft" in unix_recovery
    assert "opencli_is_wsl && return 1" in unix_recovery
    assert '[ -z "${DOCKER_HOST:-}" ] || return 1' in unix_recovery
    assert "docker context show 2>/dev/null || true" in unix_recovery
    assert "systemctl is-enabled docker" in unix_recovery
    assert 'container_id="$(docker compose ps -q "$service")" || return 1' in unix_recovery
    assert '[ -n "$container_id" ] || return 1' in unix_recovery
    assert 'state="$(docker inspect --format' in unix_recovery
    assert '[ "$state" = "running" ] || return 1' in unix_recovery
    assert 'health="$(docker inspect --format' in unix_recovery
    assert '[ "$health" = "healthy" ] || return 1' in unix_recovery
    assert "test -r /home/chrome/.config/chromium" in unix_recovery
    assert 'INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd -P)"' in unix_installer
    assert 'replace_env COMPOSE_PROJECT_NAME "$compose_project_name"' in unix_installer
    assert "opencli_shell_quote" in unix_recovery
    assert "sudo systemctl enable docker" in unix_recovery
    assert "systemctl enable docker >/" not in unix_recovery
    assert "Test-OpenCliWsl" in windows_recovery
    assert "Test-OpenCliDockerBootPrerequisite" in windows_recovery
    assert "$env:DOCKER_HOST" in windows_recovery
    assert "docker context show" in windows_recovery
    assert 'Get-OpenCliRecoveredContainer "api"' in windows_recovery
    assert 'Get-OpenCliRecoveredContainer "frontend"' in windows_recovery
    assert 'Get-OpenCliRecoveredContainer "agent-1"' in windows_recovery
    assert "test -r /home/chrome/.config/chromium" in windows_recovery
    assert "Get-CimInstance Win32_StartupCommand" in windows_recovery
    assert 'Set-EnvValue "COMPOSE_PROJECT_NAME" $composeProjectName' in windows_installer
    assert "Set-Service" not in windows_installer
    assert "BOOTSTRAP_ADMIN_TOKEN: $bootstrapToken" not in windows_installer
    assert "API_AUTH_TOKEN: $apiToken" not in windows_installer
    assert "legitimate password change" in source("README.md")
    assert "not compared with the installation-time hash" in source("README.md")


def test_source_and_release_smokes_gate_daemon_recovery_before_release() -> None:
    workflow = source(".github/workflows/ci.yml")
    release_workflow = source(".github/workflows/release.yml")
    gate = source(".github/scripts/verify-daemon-restart.sh")
    agent_gate = source(".github/scripts/verify-candidate-agent-images.sh")
    ci_jobs = yaml.safe_load(workflow)["jobs"]
    release_jobs = yaml.safe_load(release_workflow)["jobs"]

    assert workflow.count("\n  frontend:\n") == 1
    assert ci_jobs["release-contract"]["name"] == "Source Recovery Smoke"
    assert "Public Install Smoke" not in workflow
    gate_step = "bash .github/scripts/verify-daemon-restart.sh"
    assert gate_step in workflow
    assert workflow.index(gate_step) < workflow.index("down -v")
    assert gate_step in release_workflow
    assert "sudo systemctl restart docker" in gate
    assert "services=(api frontend agent-1)" in gate
    assert "OPENCLI_DAEMON_GATE_INCLUDE_BUILD_OVERRIDE" in gate
    assert "compose+=(-f docker-compose.build.yml)" in gate
    assert "no Compose recovery command will be run" in gate
    assert "db_revision_before" in gate
    assert "/data/local-admin-password.hash" in gate
    assert "/data/local-admin-password.hash.initialized" in gate
    assert "/home/chrome/.config/chromium/.ci-daemon-restart-sentinel" in gate
    assert "agent_profile_volume_before" in gate
    assert "agent_profile_volume_after" in gate
    assert "test -r /home/chrome/.config/chromium" in gate
    assert "database, authentication, and browser-profile sentinels persisted" in gate
    assert "state={{.State.Status}}" in gate
    assert "health={{if .State.Health}}" in gate
    assert "{{range .Mounts}}" in gate
    assert "docker compose up -d" not in gate
    assert "docker compose start" not in gate
    assert "docker compose restart" not in gate
    assert "daemon_deadline" in gate
    assert "service_deadline=$((SECONDS + service_timeout))" in gate
    assert "deadline=$((SECONDS + 180))" not in gate

    contract_steps = "\n".join(step.get("run", "") for step in release_jobs["contract"]["steps"])
    assert "PUBLIC_RELEASE_VERSION" in contract_steps
    assert "IMAGE_TAG" in contract_steps
    assert "tests/unit/test_public_release_contract.py" in contract_steps
    assert (
        "candidate=$release_version-candidate-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" in contract_steps
    )
    assert release_jobs["images"]["needs"] == "contract"
    image_push_step = next(
        step
        for step in release_jobs["images"]["steps"]
        if step.get("uses") == "docker/build-push-action@v6"
    )
    image_tags = image_push_step["with"]["tags"]
    assert "needs.contract.outputs.candidate" in image_tags
    assert "needs.contract.outputs.version" not in image_tags
    assert ":latest" not in image_tags

    published_job = release_jobs["published-image-smoke"]
    published_steps = "\n".join(step.get("run", "") for step in published_job["steps"])
    assert set(published_job["needs"]) == {"contract", "images"}
    assert published_job["runs-on"] == "ubuntu-24.04"
    assert published_job["env"]["OPENCLI_DAEMON_GATE_INCLUDE_BUILD_OVERRIDE"] == "0"
    assert published_job["env"]["IMAGE_TAG"] == "${{ needs.contract.outputs.candidate }}"
    assert "systemctl is-active --quiet docker.service" in published_steps
    assert "pull api frontend agent-1" in published_steps
    assert "up -d --no-build --wait api frontend agent-1" in published_steps
    assert "bash .github/scripts/verify-candidate-agent-images.sh" in published_steps
    assert "opencli-admin-agent:$candidate_tag" in agent_gate
    assert "opencli-admin-agent:$candidate_tag-chrome" in agent_gate
    assert "AGENT_HAS_CHROME=false" in agent_gate
    assert "AGENT_HAS_CHROME=true" in agent_gate
    assert "/tmp/browser-runtime-report.json" in agent_gate
    assert gate_step in published_steps
    assert published_steps.index("pull api frontend agent-1") < published_steps.index(gate_step)
    assert published_steps.index(gate_step) < published_steps.index("down -v")
    assert "docker-compose.build.yml" not in published_steps

    promote_job = release_jobs["promote"]
    assert set(promote_job["needs"]) == {"contract", "published-image-smoke"}
    promote_steps = "\n".join(step.get("run", "") for step in promote_job["steps"])
    assert "docker buildx imagetools create" in promote_steps
    assert "opencli-admin-api" in promote_steps
    assert "opencli-admin-frontend" in promote_steps
    assert "opencli-admin-chrome" in promote_steps
    assert "opencli-admin-agent:$CANDIDATE_TAG" in promote_steps
    assert "opencli-admin-agent:$CANDIDATE_TAG-chrome" in promote_steps
    assert "opencli-admin-agent:$RELEASE_VERSION-chrome" in promote_steps
    assert "opencli-admin-agent:latest-chrome" in promote_steps
    assert release_jobs["github-release"]["needs"] == "promote"


def test_public_release_keeps_existing_security_and_packaging_guards() -> None:
    workflow = source(".github/workflows/ci.yml")
    release_workflow = source(".github/workflows/release.yml")
    windows_installer = source("scripts/install.ps1")
    unix_installer = source("scripts/install.sh")

    assert (ROOT / "scripts" / "install.sh").is_file()
    assert (ROOT / "scripts" / "install.ps1").is_file()
    assert "BOOTSTRAP_ADMIN_TOKEN" in env_contract()
    assert "BOOTSTRAP_ADMIN_TOKEN" in unix_installer
    assert "BOOTSTRAP_ADMIN_TOKEN" in windows_installer
    assert 'os.environ.get("NOVNC_BASE_PORT", 6080)' in source(
        "backend/api/v1/browser_containers.py"
    )
    assert "Assert-NativeSuccess" in windows_installer
    assert "-UseBasicParsing" in windows_installer
    assert "http://localhost:$FrontendPort/login" in windows_installer
    assert 'raw="$(openssl rand -base64 32)" || return 1' in unix_installer
    assert 'if [ -z "$credential_encryption_key" ]; then' in unix_installer
    assert "packages: write" in release_workflow
    assert "id-token: write" not in release_workflow
    assert "Source Recovery Smoke" in workflow
    ephemeral_secret = 'echo "SECRET_KEY=$(openssl rand -hex 32)" >> "$GITHUB_ENV"'
    assert ephemeral_secret in workflow
    assert ephemeral_secret in release_workflow
    assert "ci-release-secret" not in workflow
    assert "ci-release-secret" not in release_workflow
