import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from backend.security.local_auth import load_password_hash, verify_password

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
        if re.search(
            r"^[ \t]*(?:IMAGE_TAG=\S+[ \t]+)?docker compose\b[^\n]*docker-compose\.build\.yml"
            r"[ \t]+up(?:[ \t]+--?[\w-]+)*[ \t]*$",
            block,
            re.MULTILINE,
        )
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


def bash_executable() -> str:
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("A native Bash executable is unavailable.")


def run_source_recipe(
    recipe: str, sandbox: Path, *, initializer_fails: bool = False
) -> subprocess.CompletedProcess[str]:
    sandbox.mkdir()
    state_directory = sandbox / "data"
    state_directory.mkdir()
    if initializer_fails:
        # Exercise a real durable-state write failure, not a Docker mock error.
        (state_directory / "local-admin-password.hash").mkdir()
    # Only host setup and Docker are stubbed: password generation, validation,
    # persistence, permissions, pipelines, and failure handling run in real Bash.
    harness = r"""
git() { return 0; }
cd() { return 0; }
cp() { return 0; }
docker() {
  printf '%s\n' "$@" >> docker-argv
  [ "$1" = compose ] || return 90
  shift
  while [ "$1" = -f ]; do shift 2; done
  case "$1" in
    build) printf 'build\n' >> lifecycle ;;
    run)
      printf 'initialize\n' >> lifecycle
      cat > initializer-stdin
      shift
      while [[ "$1" = -* ]]; do shift; done
      [ "$1" = api ] && [ "$2" = python ] && [ "$3" = -c ] || return 92
      "$RECIPE_PYTHON" -c \
        'import sys
exec(compile(sys.argv[1].replace("/data/", "data/"), "<README initializer>", "exec"))' \
        "$4" < initializer-stdin || return $?
      printf 'initialized\n' >> lifecycle
      ;;
    up) printf 'up\n' >> lifecycle ;;
    *) return 91 ;;
  esac
}
"""
    return subprocess.run(
        [bash_executable(), "-c", harness + recipe],
        cwd=sandbox,
        env={**os.environ, "RECIPE_PYTHON": sys.executable, "PYTHONPATH": str(ROOT)},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_source_recipe_selection_excludes_service_only_examples() -> None:
    prefix = "docker compose -f docker-compose.yml -f docker-compose.build.yml"
    browser_only = f"{prefix} build agent-1\n{prefix} up -d --no-build agent-1"
    # Deliberately missing initialization: classification must not hide the bug.
    incomplete_install = f"{prefix} build api frontend agent-1\n{prefix} up -d --no-build --wait"
    readme = f"~~~bash\n{browser_only}\n~~~\n~~~bash\n{incomplete_install}\n~~~"
    assert source_docker_recipes(readme) == [incomplete_install]
    assert source_docker_recipes(f"~~~bash\n{prefix} up\n~~~") == [f"{prefix} up"]


@pytest.mark.parametrize("initializer_fails", [False, True], ids=["success", "init-failure"])
def test_source_docker_recipes_initialize_local_admin_before_starting_services(
    tmp_path: Path, initializer_fails: bool
) -> None:
    recipes = source_docker_recipes(source("README.md"))
    assert recipes, "README must contain a complete source installation recipe"
    for index, recipe in enumerate(recipes):
        sandbox = tmp_path / str(index)
        result = run_source_recipe(recipe, sandbox, initializer_fails=initializer_fails)
        lifecycle = (sandbox / "lifecycle").read_text().splitlines()
        if initializer_fails:
            assert result.returncode != 0
            assert lifecycle == ["build", "initialize"]
        else:
            assert result.returncode == 0, result.stderr
            assert lifecycle == ["build", "initialize", "initialized", "up"]
        password = (sandbox / ".local-admin-password").read_text().strip()
        assert re.fullmatch(r"[0-9a-fA-F]{48}", password)
        assert (sandbox / "initializer-stdin").read_text() == password
        if not initializer_fails:
            state_path = sandbox / "data" / "local-admin-password.hash"
            assert verify_password(password, load_password_hash("", str(state_path)))
        assert password not in (sandbox / "docker-argv").read_text()
        assert password not in result.stdout + result.stderr
        if os.name != "nt":
            assert (sandbox / ".local-admin-password").stat().st_mode & 0o777 == 0o600


def test_local_credentials_are_excluded_from_source_and_build_context() -> None:
    assert "/.local-admin-password" in source(".gitignore").splitlines()
    dockerignore = source(".dockerignore").splitlines()
    assert ".local-admin-password" in dockerignore
    assert ".opencli-restart-recovery-state*" in dockerignore


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
