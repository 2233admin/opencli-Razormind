"""Load declarative III cron schedules from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SCHEDULES_DIR = Path(__file__).resolve().parents[1] / "schedules"
DEFAULT_SCHEDULES_PATH = SCHEDULES_DIR / "discord.yaml"
DEFAULT_OPENCLI_SCHEDULES_PATH = SCHEDULES_DIR / "opencli.yaml"


def load_discord_schedules(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return enabled schedule dicts from discord.yaml."""
    file_path = Path(path) if path else DEFAULT_SCHEDULES_PATH
    if not file_path.is_file():
        return []

    with file_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    enabled: list[dict[str, Any]] = []
    for item in data.get("schedules") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        schedule_id = str(item.get("id") or "").strip()
        channel_id = str(item.get("channel_id") or "").strip()
        expression = str(item.get("expression") or "").strip()
        if not schedule_id:
            raise ValueError(f"Schedule missing id in {file_path}")
        if not channel_id:
            raise ValueError(f"Schedule {schedule_id!r} missing channel_id")
        if not expression:
            raise ValueError(f"Schedule {schedule_id!r} missing expression")
        channel_name = str(item.get("channel_name") or "").strip() or None
        enabled.append(
            {
                "id": schedule_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "expression": expression,
                "limit": int(item.get("limit") or 50),
                "source_id": item.get("source_id"),
            }
        )
    return enabled


def load_opencli_schedules(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return enabled opencli schedule dicts from opencli.yaml."""
    file_path = Path(path) if path else DEFAULT_OPENCLI_SCHEDULES_PATH
    if not file_path.is_file():
        return []

    with file_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    enabled: list[dict[str, Any]] = []
    for item in data.get("schedules") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        schedule_id = str(item.get("id") or "").strip()
        site = str(item.get("site") or "").strip()
        command = str(item.get("command") or "").strip()
        expression = str(item.get("expression") or "").strip()
        if not schedule_id:
            raise ValueError(f"Schedule missing id in {file_path}")
        if not site:
            raise ValueError(f"Schedule {schedule_id!r} missing site")
        if not command:
            raise ValueError(f"Schedule {schedule_id!r} missing command")
        if not expression:
            raise ValueError(f"Schedule {schedule_id!r} missing expression")
        row: dict[str, Any] = {
            "id": schedule_id,
            "site": site,
            "command": command,
            "expression": expression,
            "args": dict(item.get("args") or {}),
            "positional_args": list(item.get("positional_args") or []),
            "format": str(item.get("format") or "json"),
            "source_id": item.get("source_id"),
        }
        if item.get("mode"):
            row["mode"] = str(item["mode"])
        enabled.append(row)
    return enabled
