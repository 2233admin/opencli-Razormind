"""Record v2 builders and ODP ingest HTTP client (Rust hot path)."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

SCHEMA_VERSION = 1
INGEST_TIMEOUT = float(os.environ.get("ODP_INGEST_TIMEOUT", "15"))


def ingest_base_url() -> str:
    base = os.environ.get("ODP_INGEST_URL", "").strip().rstrip("/")
    if not base:
        raise ValueError("ODP_INGEST_URL is not set")
    return base


def source_id_for_channel(channel_id: str, explicit: str | None = None) -> str:
    """Return a stable UUID for a Discord channel source."""
    if explicit and explicit.strip():
        return explicit.strip()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/discord/{channel_id}"))


def source_id_for_opencli(site: str, command: str, explicit: str | None = None) -> str:
    """Return a stable UUID for an opencli site/command source."""
    if explicit and explicit.strip():
        return explicit.strip()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/opencli/{site}/{command}"))


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    except ValueError:
        return datetime.now(UTC)


def discord_message_to_payload(msg: dict[str, Any]) -> dict[str, Any]:
    guild_id = str(msg.get("guild_id") or "")
    channel_id = str(msg.get("channel_id") or "")
    msg_id = str(msg.get("msg_id") or "")
    guild_name = str(msg.get("guild_name") or "")
    channel_name = str(msg.get("channel_name") or "")
    sender = str(msg.get("sender_name") or msg.get("sender_id") or "")
    content = str(msg.get("content") or "")
    timestamp = str(msg.get("timestamp") or "")

    title = f"{guild_name}/{channel_name}".strip("/") or channel_id
    url = ""
    if guild_id and channel_id and msg_id:
        url = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"

    payload: dict[str, Any] = {
        "title": title,
        "url": url,
        "content": content,
        "author": sender,
        "published_at": timestamp,
        "extra_platform": msg.get("platform", "discord"),
        "extra_guild_id": guild_id,
        "extra_guild_name": guild_name,
        "extra_channel_id": channel_id,
        "extra_channel_name": channel_name,
        "extra_msg_id": msg_id,
        "extra_sender_id": str(msg.get("sender_id") or ""),
    }
    return payload


def build_record_event(
    *,
    provider: str,
    source_id: str,
    event_id: str,
    payload: dict[str, Any],
    raw_data: dict[str, Any] | None = None,
    ingest_mode: str = "snapshot",
    source_ts: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    ts = _parse_ts(source_ts or payload.get("published_at"))
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "source_id": source_id,
        "event_id": event_id,
        "ingest_mode": ingest_mode,
        "source_ts": ts.isoformat().replace("+00:00", "Z"),
        "cursor": cursor,
        "payload": payload,
        "raw_data": raw_data or {},
    }
    if trace_id:
        event["trace_id"] = trace_id
    if task_id:
        event["task_id"] = task_id
    return event


_TITLE_KEYS = ("title", "name", "word", "topic", "headline", "subject")
_URL_KEYS = ("url", "link", "href", "permalink")
_CONTENT_KEYS = ("content", "text", "body", "summary", "description")
_AUTHOR_KEYS = ("author", "channel", "creator", "by", "user")
_DATE_KEYS = (
    "created_at", "published_at", "published", "date", "time", "listed",
    "updated", "timestamp",
)


def _first_field(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    lower_map = {k.lower(): v for k, v in item.items()}
    for key in keys:
        val = lower_map.get(key.lower())
        if val and isinstance(val, str):
            return val
    return ""


def _opencli_item_payload(item: dict[str, Any], *, site: str, command: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _first_field(item, _TITLE_KEYS),
        "url": _first_field(item, _URL_KEYS),
        "content": _first_field(item, _CONTENT_KEYS),
        "author": _first_field(item, _AUTHOR_KEYS),
        "published_at": _first_field(item, _DATE_KEYS),
        "extra_site": site,
        "extra_command": command,
    }
    standard = {
        k.lower()
        for group in (
            _TITLE_KEYS, _URL_KEYS, _CONTENT_KEYS, _AUTHOR_KEYS, _DATE_KEYS
        )
        for k in group
    }
    for key, value in item.items():
        if key.lower() not in standard:
            payload[f"extra_{key}"] = value
    return payload


def _event_id_for_opencli_item(item: dict[str, Any], source_id: str) -> str:
    for key in ("id", "msg_id", "eid", "url", "link"):
        val = item.get(key)
        if val:
            return str(val)
    dedup = hashlib.sha256(
        json.dumps(item, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return f"{source_id}:{dedup[:32]}"


def opencli_items_to_events(
    items: list[dict[str, Any]],
    *,
    site: str,
    command: str,
    source_id: str,
    task_id: str | None = None,
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in items:
        payload = _opencli_item_payload(item, site=site, command=command)
        payload["source_id"] = source_id
        events.append(
            build_record_event(
                provider=f"opencli/{site}",
                source_id=source_id,
                event_id=_event_id_for_opencli_item(item, source_id),
                payload=payload,
                raw_data=item,
                source_ts=payload.get("published_at"),
                task_id=task_id,
                trace_id=trace_id,
            )
        )
    return events


def discord_messages_to_events(
    messages: list[dict[str, Any]],
    *,
    source_id: str,
    task_id: str | None = None,
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for msg in messages:
        msg_id = str(msg.get("msg_id") or "")
        if not msg_id:
            dedup = hashlib.sha256(
                json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            msg_id = dedup
        payload = discord_message_to_payload(msg)
        payload["source_id"] = source_id
        events.append(
            build_record_event(
                provider="opencli-admin/discord",
                source_id=source_id,
                event_id=msg_id,
                payload=payload,
                raw_data=msg,
                source_ts=str(msg.get("timestamp") or ""),
                task_id=task_id,
                trace_id=trace_id,
            )
        )
    return events


def post_batch_sync(
    events: list[dict[str, Any]], *, receipt_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not events:
        return {"accepted": 0, "duplicates": 0, "rejected": 0, "sent": 0}
    url = f"{ingest_base_url()}/v1/ingest/batch"
    request_body: dict[str, Any] = {"events": events}
    if receipt_context is not None:
        request_body["receipt_context"] = receipt_context
    with httpx.Client(timeout=INGEST_TIMEOUT) as client:
        resp = client.post(url, json=request_body)
        resp.raise_for_status()
        data = resp.json()
    result = {
        "accepted": int(data.get("accepted", 0)),
        "duplicates": int(data.get("duplicates", 0)),
        "rejected": int(data.get("rejected", 0)),
        "sent": len(events),
    }
    if isinstance(data.get("ingress_receipt"), dict):
        result["ingress_receipt"] = data["ingress_receipt"]
    return result
