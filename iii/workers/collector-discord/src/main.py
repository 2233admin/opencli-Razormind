"""M1: Discord snapshot collector — discord-cli → Record v2 → odp.ingest::batch."""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from iii_observability import Logger

from iii import InitOptions, register_worker

III_ROOT = Path(__file__).resolve().parents[2]
if str(III_ROOT) not in sys.path:
    sys.path.insert(0, str(III_ROOT))

from lib import discord_cli  # noqa: E402
from lib.env_bootstrap import bootstrap_worker_env  # noqa: E402
from lib.odp_record import discord_messages_to_events, source_id_for_channel  # noqa: E402

bootstrap_worker_env()

worker = register_worker(
    os.environ.get("III_URL", "ws://localhost:49134"),
    InitOptions(
        worker_name="collector-discord",
        worker_description="Discord channel snapshots via discord-cli into ODP ingest",
    ),
)
logger = Logger()

DEFAULT_LIMIT = int(os.environ.get("DISCORD_SNAPSHOT_LIMIT", "50"))


def _filter_channel(messages: list[dict[str, Any]], channel_id: str) -> list[dict[str, Any]]:
    want = str(channel_id)
    return [m for m in messages if str(m.get("channel_id") or "") == want]


def _channel_name_filters(channel_id: str, explicit: str | None) -> list[str]:
    """Build discord recent -c filter candidates (full name, then emoji-stripped)."""
    names: list[str] = []
    if explicit and explicit.strip():
        names.append(explicit.strip())
    resolved = discord_cli.channel_name_for_id(channel_id)
    if resolved and resolved not in names:
        names.append(resolved)
    for name in list(names):
        stripped = re.sub(r"^[^\w\u4e00-\u9fff]+", "", name).strip()
        if stripped and stripped not in names:
            names.append(stripped)
    return names


def _fetch_channel_messages(
    channel_id: str,
    *,
    limit: int,
    channel_name: str | None,
) -> tuple[list[dict[str, Any]], str | None, int]:
    """Prefer channel-scoped recent; fall back to global recent + filter."""
    for filt in _channel_name_filters(channel_id, channel_name):
        recent = discord_cli.recent_messages(limit=limit, channel_name=filt)
        matched = _filter_channel(recent, channel_id)
        if not matched and recent:
            matched = recent
        if matched:
            return matched, filt, len(recent)

    recent = discord_cli.recent_messages(limit=limit)
    return _filter_channel(recent, channel_id), None, len(recent)


def discord_snapshot_handler(payload: dict[str, Any]) -> dict[str, Any]:
    channel_id = str(payload.get("channel_id") or "").strip()
    if not channel_id:
        raise ValueError("channel_id is required")

    limit = int(payload.get("limit") or DEFAULT_LIMIT)
    source_id = source_id_for_channel(channel_id, payload.get("source_id"))
    task_id = payload.get("task_id") or str(uuid.uuid4())
    trace_id = payload.get("trace_id") or str(uuid.uuid4())

    logger.info(
        "odp.collect::discord_snapshot",
        {
            "channel_id": channel_id,
            "limit": limit,
            "schedule_id": payload.get("schedule_id"),
            "cron_job_id": (payload.get("cron") or {}).get("job_id"),
        },
    )

    sync_result = discord_cli.sync_channel(channel_id)
    explicit_name = payload.get("channel_name")
    messages, recent_filter, recent_total = _fetch_channel_messages(
        channel_id,
        limit=limit,
        channel_name=str(explicit_name).strip() if explicit_name else None,
    )

    events = discord_messages_to_events(
        messages,
        source_id=source_id,
        task_id=task_id,
        trace_id=trace_id,
    )

    ingest_result: dict[str, Any] = {"sent": 0, "accepted": 0, "duplicates": 0, "rejected": 0}
    if events:
        ingest_result = worker.trigger(
            {
                "function_id": "odp.ingest::batch",
                "payload": {
                    "events": events,
                    "trace_id": trace_id,
                    "task_id": task_id,
                },
            }
        )

    return {
        "ok": True,
        "channel_id": channel_id,
        "source_id": source_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "schedule_id": payload.get("schedule_id"),
        "sync": sync_result,
        "recent_filter": recent_filter,
        "messages_fetched": recent_total,
        "messages_matched": len(messages),
        "ingest": ingest_result,
    }


def status_handler(_payload: dict[str, Any]) -> dict[str, Any]:
    return discord_cli.status()


def guilds_handler(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"guilds": discord_cli.guilds()}


def channels_handler(payload: dict[str, Any]) -> dict[str, Any]:
    guild_id = str(payload.get("guild_id") or "").strip()
    if not guild_id:
        raise ValueError("guild_id is required")
    return {"guild_id": guild_id, "channels": discord_cli.channels(guild_id)}


worker.register_function(
    "odp.collect::discord_snapshot",
    discord_snapshot_handler,
    description="Sync a Discord channel and ingest recent messages as Record v2",
)
worker.register_function("discord::status", status_handler, description="discord-cli auth status")
worker.register_function("discord::guilds", guilds_handler, description="List Discord guilds")
worker.register_function(
    "discord::channels",
    channels_handler,
    description="List channels for a guild_id",
)

print(
    "collector-discord started — "
    "odp.collect::discord_snapshot, discord::{status,guilds,channels}"
)
