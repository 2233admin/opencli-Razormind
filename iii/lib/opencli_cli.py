"""Sync opencli subprocess wrapper for III collectors."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import urlparse

import yaml

OPENCLI_BIN = os.environ.get("OPENCLI_BIN", "opencli")
DEFAULT_TIMEOUT = float(os.environ.get("OPENCLI_TIMEOUT", "120"))
DAEMON_PORT = int(os.environ.get("OPENCLI_DAEMON_PORT", "19825"))

_help_cache: dict[tuple[str, str, str], frozenset[str]] = {}


def _named_options(site: str, command: str) -> frozenset[str]:
    key = (OPENCLI_BIN, site, command)
    if key in _help_cache:
        return _help_cache[key]
    try:
        proc = subprocess.run(
            [OPENCLI_BIN, site, command, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        names = frozenset(
            re.findall(r"--([a-zA-Z][a-zA-Z0-9-]*)", proc.stdout or "")
        ) - {"format", "verbose", "help"}
    except Exception:
        names = frozenset()
    _help_cache[key] = names
    return names


def _parse_json(raw: str) -> list[dict[str, Any]]:
    start = next((i for i, ch in enumerate(raw) if ch in ("{", "[")), None)
    if start is None:
        raise ValueError(f"No JSON in opencli output: {raw[:200]!r}")
    data = json.loads(raw[start:])
    return data if isinstance(data, list) else [data]


def _parse_yaml(raw: str) -> list[dict[str, Any]]:
    data = yaml.safe_load(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return [{"content": str(data)}]


def _build_env(mode: str, chrome_endpoint: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if mode == "bridge":
        host = os.environ.get("OPENCLI_DAEMON_HOST", "agent-1")
        if chrome_endpoint:
            host = urlparse(chrome_endpoint).hostname or host
        env.pop("OPENCLI_CDP_ENDPOINT", None)
        env["OPENCLI_DAEMON_HOST"] = host
        env["OPENCLI_DAEMON_PORT"] = str(DAEMON_PORT)
    else:
        endpoint = chrome_endpoint or os.environ.get(
            "OPENCLI_CDP_ENDPOINT", "http://host.docker.internal:9222"
        )
        env["OPENCLI_CDP_ENDPOINT"] = endpoint
    return env


def run_collect(
    *,
    site: str,
    command: str,
    args: dict[str, Any] | None = None,
    positional_args: list[str] | None = None,
    output_format: str = "json",
    mode: str | None = None,
    chrome_endpoint: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run `opencli <site> <command>` and return parsed items."""
    if not shutil.which(OPENCLI_BIN) and not os.path.isfile(OPENCLI_BIN):
        raise FileNotFoundError(f"opencli binary not found: {OPENCLI_BIN}")

    resolved_mode = (mode or os.environ.get("OPENCLI_MODE", "bridge")).lower()
    raw_args = dict(args or {})
    pos = list(positional_args or [])
    named = _named_options(site, command)
    cli_args: dict[str, Any] = {}
    extra_pos: list[str] = []
    for key, value in raw_args.items():
        if named and key not in named:
            extra_pos.append(str(value))
        else:
            cli_args[key] = value
    pos = extra_pos + pos

    cmd = [OPENCLI_BIN, site, command, *pos]
    for key, value in cli_args.items():
        cmd.extend([f"--{key}", str(value)])
    cmd.extend(["-f", output_format])

    env = _build_env(resolved_mode, chrome_endpoint)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout or DEFAULT_TIMEOUT,
        env=env,
        check=False,
    )
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"opencli exit {proc.returncode}: {stderr[:500] or proc.stdout[:200]}"
        )

    raw = proc.stdout or ""
    parser = _parse_json if output_format == "json" else _parse_yaml
    items = parser(raw)
    return {
        "ok": True,
        "site": site,
        "command": command,
        "mode": resolved_mode,
        "items": items,
        "count": len(items),
        "stderr": stderr[:300] if stderr else None,
    }
