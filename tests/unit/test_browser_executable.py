import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def run_resolver(
    engine: str | None, overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if engine is not None:
        env["BROWSER_ENGINE"] = engine
    if overrides:
        env.update(overrides)
    command = ["node", str(ROOT / "scripts" / "resolve-browser-executable.mjs")]
    if engine is not None:
        command.append(engine)
    return subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_resolver_accepts_stock_chromium():
    result = run_resolver("chromium", {"CHROMIUM_BINARY": ""})
    assert result.returncode == 0
    assert result.stdout.strip() == "chromium"


def test_resolver_uses_existing_cloak_binary(tmp_path):
    binary = tmp_path / "cloak"
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    result = run_resolver("cloakbrowser", {"CLOAKBROWSER_BINARY_PATH": str(binary)})
    assert result.returncode == 0
    assert result.stdout.strip() == str(binary)


def test_resolver_rejects_cloak_directory(tmp_path):
    binary_directory = tmp_path / "cloak"
    binary_directory.mkdir()
    result = run_resolver("cloakbrowser", {"CLOAKBROWSER_BINARY_PATH": str(binary_directory)})
    assert result.returncode != 0
    assert result.stdout == ""


def test_resolver_rejects_unknown_engine():
    result = run_resolver("webkit")
    assert result.returncode != 0
    assert "webkit" in result.stderr


def test_resolver_rejects_empty_environment_engine():
    result = run_resolver(None, {"BROWSER_ENGINE": ""})
    assert result.returncode != 0
    assert result.stdout == ""


def test_resolver_rejects_empty_engine():
    result = run_resolver("")
    assert result.returncode != 0
    assert result.stdout == ""


def test_resolver_does_not_fallback_when_override_missing(tmp_path):
    result = run_resolver(
        "cloakbrowser",
        {"CLOAKBROWSER_BINARY_PATH": str(tmp_path / "missing")},
    )
    assert result.returncode != 0
    assert "fallback" not in result.stderr.lower()


def bash_executable() -> str:
    git = shutil.which("git")
    candidates = [
        Path(git).parent / "bash.exe" if git else None,
        Path(git).parent.parent / "bin" / "bash.exe" if git else None,
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ]
    if os.name != "nt":
        candidates.append(Path(shutil.which("bash") or "/bin/bash"))
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    pytest.skip("A native Bash executable is unavailable.")


def run_entrypoint(
    tmp_path: Path,
    name: str,
    engine: str | None,
    *,
    image_has_chrome: bool = False,
    stock_chromium: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the complete startup script with real Node resolvers, not real services."""
    bash = bash_executable()
    node = shutil.which("node")
    assert node is not None, "Node is required by the browser entrypoints"
    for directory in ("bin", "etc/nginx/conf.d", "tmp", "home", "usr/local/bin", "opt"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/nginx/conf.d/cdp.conf.template").write_text("")
    (tmp_path / "etc/browser-bridge-extension-id").write_text("")
    manifest = tmp_path / "opt/manifest.json"
    manifest.write_text(json.dumps({"name": "test", "version": "1", "components": []}))
    for resolver in (
        "resolve-browser-executable.mjs",
        "resolve-browser-runtime-bundle.mjs",
    ):
        shutil.copyfile(ROOT / "scripts" / resolver, tmp_path / "usr/local/bin" / resolver)

    # Only filesystem locations change; all production branching remains intact.
    source = (ROOT / name / "entrypoint.sh").read_text(encoding="utf-8")
    source = source.replace("/tmp/", f"{tmp_path.as_posix()}/tmp/")
    for prefix in ("/etc/", "/home/", "/usr/local/bin/", "/usr/share/", "/opt/"):
        source = source.replace(prefix, f"{tmp_path.as_posix()}{prefix}")
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(source, encoding="utf-8", newline="\n")
    events = tmp_path / "events"
    for executable, event in (
        ("chromium-double", "chromium"),
        ("cloak-double", "cloak"),
        ("uvicorn", "server"),
    ):
        binary = tmp_path / "bin" / executable
        binary.write_text(
            f'#!/bin/bash\nprintf \'{event} %s\\n\' "$*" >> "$STARTUP_EVENTS"\n',
            encoding="utf-8",
            newline="\n",
        )
        binary.chmod(0o755)

    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("BROWSER_", "CHROMIUM_", "CLOAKBROWSER_", "AGENT_HAS_CHROME")):
            env.pop(key)
    env.update(
        {
            "NODE_BIN": Path(node).as_posix(),
            "STARTUP_EVENTS": events.as_posix(),
            "SANDBOX_BIN": (tmp_path / "bin").as_posix(),
            "BROWSER_RUNTIME_BUNDLE_ROOT": (tmp_path / "opt").as_posix(),
            "BROWSER_RUNTIME_BUNDLE_MANIFEST": manifest.as_posix(),
            "CHROMIUM_BINARY": (tmp_path / "bin/chromium-double").as_posix(),
            "CLOAKBROWSER_BINARY_PATH": (tmp_path / "bin/cloak-double").as_posix(),
            "AGENT_HAS_CHROME": str(image_has_chrome).lower(),
            "OPENCLI_BROWSER_PROFILE_KIND": "authenticated",
            "CLOAKBROWSER_LICENSE_KEY": "startup-test-private-license",
        }
    )
    if engine is not None:
        env["BROWSER_ENGINE"] = engine
    script = r"""
# A private PATH makes stock Chromium detection independent of the test host.
export PATH="$(cd "$SANDBOX_BIN" && pwd)"
node() { "$NODE_BIN" "$@"; }
Xvfb() { :; }
nginx() { :; }
x11vnc() { :; }
websockify() { :; }
envsubst() { :; }
rm() { :; }
find() { :; }
xargs() { :; }
tr() { :; }
npm() { printf '%s\n' "$SANDBOX_BIN"; }
seq() { printf '1\n'; }
curl() { return 1; }
# End daemon/browser restart loops after their first invocation.
bbx-daemon() { exit 0; }
sleep() { if [ "$1" = 2 ]; then exit 0; fi; }
"""
    if stock_chromium:
        script += "\nchromium() { :; }\n"
    script += f"\nsource {shlex.quote(entrypoint.as_posix())}\n"
    result = subprocess.run(
        [bash, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result, events.read_text().splitlines() if events.exists() else []


@pytest.mark.parametrize("name", ["chrome", "agent"])
def test_entrypoints_reject_explicit_empty_engine(tmp_path, name):
    result, events = run_entrypoint(tmp_path, name, "", stock_chromium=True)
    assert result.returncode != 0
    assert "unsupported browser engine" in result.stderr
    assert events == []


@pytest.mark.parametrize("name", ["chrome", "agent"])
def test_entrypoints_default_unset_engine_to_chromium(tmp_path, name):
    result, events = run_entrypoint(tmp_path, name, None, stock_chromium=True)
    assert result.returncode == 0, result.stderr
    assert any(
        event.startswith("chromium ") and "--remote-debugging-port=9222" in event
        for event in events
    )
    assert "startup-test-private-license" not in result.stdout + result.stderr


def test_agent_image_marker_starts_cloak_without_stock_chromium(tmp_path):
    result, events = run_entrypoint(tmp_path, "agent", "cloakbrowser", image_has_chrome=True)
    assert result.returncode == 0, result.stderr
    assert any(
        event.startswith("cloak ") and "--remote-debugging-port=9222" in event for event in events
    )
    assert any(event.startswith("server ") for event in events)
    assert "startup-test-private-license" not in result.stdout + result.stderr


def test_agent_host_mode_does_not_resolve_browser_engine(tmp_path):
    # An invalid engine would abort startup if host mode called the resolver.
    result, events = run_entrypoint(tmp_path, "agent", "")
    assert result.returncode == 0, result.stderr
    assert len(events) == 1
    assert events[0].startswith("server ")
    assert "unsupported browser engine" not in result.stderr
