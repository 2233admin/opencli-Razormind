"""Thin wrapper around jackwener/discord-cli (opencli external)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

DEFAULT_BINARY = os.environ.get("DISCORD_CLI_BIN", "discord")
DEFAULT_TIMEOUT = int(os.environ.get("DISCORD_CLI_TIMEOUT", "120"))


class DiscordCliError(RuntimeError):
    pass


def _resolve_binary() -> str:
    binary = DEFAULT_BINARY
    if os.path.isabs(binary) or (os.sep in binary):
        return binary
    found = shutil.which(binary)
    if not found:
        raise DiscordCliError(f"discord-cli binary not found: {binary!r}")
    return found


def run_discord(args: list[str], *, timeout: int | None = None) -> dict[str, Any]:
    binary = _resolve_binary()
    cmd = [binary, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or DEFAULT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiscordCliError(f"discord-cli timed out after {timeout or DEFAULT_TIMEOUT}s") from exc
    except FileNotFoundError as exc:
        raise DiscordCliError(f"discord-cli not found: {binary!r}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise DiscordCliError(
            f"discord-cli failed (exit {proc.returncode}): {stderr or stdout}"
        )

    if not stdout:
        return {"ok": True, "data": None}

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DiscordCliError(f"discord-cli returned non-JSON: {stdout[:500]}") from exc

    if not envelope.get("ok", True):
        err = envelope.get("error") or envelope
        raise DiscordCliError(f"discord-cli error: {err}")

    return envelope


def status() -> dict[str, Any]:
    return run_discord(["status", "--json"])


def guilds() -> list[dict[str, Any]]:
    envelope = run_discord(["dc", "guilds", "--json"])
    data = envelope.get("data") or []
    return data if isinstance(data, list) else [data]


def channels(guild_id: str) -> list[dict[str, Any]]:
    envelope = run_discord(["dc", "channels", guild_id, "--json"])
    data = envelope.get("data") or []
    return data if isinstance(data, list) else [data]


def sync_channel(channel_id: str) -> dict[str, Any]:
    envelope = run_discord(["dc", "sync", channel_id, "--json"], timeout=300)
    data = envelope.get("data") or {}
    return data if isinstance(data, dict) else {"result": data}


def stats_channels() -> list[dict[str, Any]]:
    envelope = run_discord(["stats", "--json"])
    data = (envelope.get("data") or {}).get("channels") or []
    return data if isinstance(data, list) else [data]


def channel_name_for_id(channel_id: str) -> str | None:
    want = str(channel_id)
    for ch in stats_channels():
        if str(ch.get("channel_id") or "") == want:
            name = str(ch.get("channel_name") or "").strip()
            return name or None
    return None


def recent_messages(*, limit: int = 50, channel_name: str | None = None) -> list[dict[str, Any]]:
    args = ["recent", "--json", "-n", str(limit)]
    if channel_name:
        args.extend(["-c", channel_name])
    envelope = run_discord(args)
    data = envelope.get("data") or []
    return data if isinstance(data, list) else [data]
