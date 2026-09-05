"""Load ~/.env or repo .env into os.environ for III workers (Windows-safe)."""

from __future__ import annotations

import os
from pathlib import Path

III_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = III_ROOT.parent

# Keys workers need when started outside start-local.ps1
REQUIRED_HINTS = ("DISCORD_TOKEN", "III_URL", "ODP_INGEST_URL")


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    name, _, value = stripped.partition("=")
    name = name.strip()
    value = value.strip().strip('"').strip("'")
    if not name:
        return None
    return name, value


def load_env_files(*, override: bool = False) -> list[str]:
    """Load first existing .env from ~/.env then repo .env. Returns paths loaded."""
    loaded: list[str] = []
    for path in (Path.home() / ".env", REPO_ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(line)
            if not parsed:
                continue
            name, value = parsed
            if override or not os.environ.get(name):
                os.environ[name] = value
        loaded.append(str(path))
        break
    return loaded


def apply_iii_defaults() -> None:
    os.environ.setdefault("III_URL", "ws://127.0.0.1:49134")
    os.environ.setdefault("ODP_INGEST_URL", "http://127.0.0.1:8040")
    os.environ.setdefault("DISCORD_CLI_BIN", "discord")


def bootstrap_worker_env() -> None:
    load_env_files()
    apply_iii_defaults()
