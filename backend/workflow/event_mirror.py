"""ODP/Redis mirror for workflow-run event evidence."""

from __future__ import annotations

import inspect
import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.config import get_settings
from backend.schemas.workflow_runtime import WorkflowNodeRunEvent

SCHEMA_VERSION = 1
WORKFLOW_EVENT_MIRROR_PROVIDER = "opencli-admin/workflow-run-event"
DEFAULT_WORKFLOW_EVENT_STREAM = "odp.workflow_run.events"
WorkflowEventMirrorBackend = Literal["memory", "redis"]

_MEMORY_STREAMS: dict[str, list[tuple[str, dict[str, str]]]] = {}


@dataclass(frozen=True)
class WorkflowRunEventMirrorRecord:
    """One stable workflow-run event fact mirrored to an ODP/Redis stream."""

    workflow_id: str
    workflow_run_id: str
    trace_id: str
    event_id: str
    sequence: int
    node_id: str
    event_type: str
    source_ts: str
    payload: dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    provider: str = WORKFLOW_EVENT_MIRROR_PROVIDER
    ingest_mode: Literal["stream"] = "stream"
    stable_facts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: WorkflowNodeRunEvent) -> WorkflowRunEventMirrorRecord:
        event_payload = event.model_dump(mode="json")
        return cls(
            workflow_id=event.workflowId,
            workflow_run_id=event.workflowRunId,
            trace_id=event.traceId,
            event_id=event.id,
            sequence=event.sequence,
            node_id=event.nodeId,
            event_type=event.eventType,
            source_ts=event.createdAt,
            payload={"event": event_payload},
            stable_facts=_stable_facts(event_payload),
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "workflow_id": self.workflow_id,
            "workflow_run_id": self.workflow_run_id,
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "node_id": self.node_id,
            "event_type": self.event_type,
            "ingest_mode": self.ingest_mode,
            "source_ts": self.source_ts,
            "stable_facts": self.stable_facts,
            "payload": self.payload,
        }

    @classmethod
    def from_wire(cls, payload: dict[str, Any]) -> WorkflowRunEventMirrorRecord:
        return cls(
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            provider=str(payload.get("provider", "")),
            workflow_id=str(payload.get("workflow_id", "")),
            workflow_run_id=str(payload.get("workflow_run_id", "")),
            trace_id=str(payload.get("trace_id", "")),
            event_id=str(payload.get("event_id", "")),
            sequence=int(payload.get("sequence", 0)),
            node_id=str(payload.get("node_id", "")),
            event_type=str(payload.get("event_type", "")),
            ingest_mode="stream",
            source_ts=str(payload.get("source_ts", "")),
            stable_facts=_read_dict(payload.get("stable_facts")),
            payload=_read_dict(payload.get("payload")),
        )

    def to_transcript_event(self) -> dict[str, Any]:
        return _read_dict(self.payload.get("event"))


async def publish_workflow_run_event_mirror(
    events: list[WorkflowNodeRunEvent],
    *,
    backend: WorkflowEventMirrorBackend | None = None,
    stream: str | None = None,
) -> list[str]:
    records = [WorkflowRunEventMirrorRecord.from_event(event) for event in events]
    if not records:
        return []

    resolved_backend = backend or _mirror_backend()
    resolved_stream = stream or _mirror_stream()
    if resolved_backend == "redis":
        return await _publish_to_redis(records, stream=resolved_stream)
    return await _publish_to_memory(records, stream=resolved_stream)


async def list_workflow_event_mirror_records(
    run_id: str,
    *,
    backend: WorkflowEventMirrorBackend | None = None,
    stream: str | None = None,
) -> list[WorkflowRunEventMirrorRecord]:
    resolved_backend = backend or _mirror_backend()
    resolved_stream = stream or _mirror_stream()
    records = (
        await _read_redis_records(resolved_stream)
        if resolved_backend == "redis"
        else _read_memory_records(resolved_stream)
    )
    return [record for record in records if record.workflow_run_id == run_id]


async def list_workflow_event_mirror_transcript(
    run_id: str,
    *,
    backend: WorkflowEventMirrorBackend | None = None,
    stream: str | None = None,
) -> list[dict[str, Any]]:
    records = await list_workflow_event_mirror_records(
        run_id,
        backend=backend,
        stream=stream,
    )
    return [record.to_transcript_event() for record in records]


def reset_memory_workflow_event_mirror() -> None:
    _MEMORY_STREAMS.clear()


async def _publish_to_memory(
    records: list[WorkflowRunEventMirrorRecord],
    *,
    stream: str,
) -> list[str]:
    entries = _MEMORY_STREAMS.setdefault(stream, [])
    ids: list[str] = []
    for record in records:
        entry_id = f"memory-{len(entries) + 1}"
        entries.append((entry_id, {"event": json.dumps(record.to_wire(), sort_keys=True)}))
        ids.append(entry_id)
    return ids


def _read_memory_records(stream: str) -> list[WorkflowRunEventMirrorRecord]:
    return [_record_from_fields(fields) for _, fields in _MEMORY_STREAMS.get(stream, [])]


async def _publish_to_redis(
    records: list[WorkflowRunEventMirrorRecord],
    *,
    stream: str,
) -> list[str]:
    client = _redis_client()
    ids: list[str] = []
    try:
        for record in records:
            entry_id = await client.xadd(
                stream,
                {"event": json.dumps(record.to_wire(), sort_keys=True)},
            )
            ids.append(str(entry_id))
    finally:
        await _close_redis(client)
    return ids


async def _read_redis_records(stream: str) -> list[WorkflowRunEventMirrorRecord]:
    client = _redis_client()
    try:
        entries = await client.xrange(stream, min="-", max="+")
    finally:
        await _close_redis(client)
    return [_record_from_fields(fields) for _, fields in entries]


def _record_from_fields(fields: dict[Any, Any]) -> WorkflowRunEventMirrorRecord:
    raw = fields.get("event") or fields.get(b"event")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise ValueError("workflow event mirror entry missing 'event' field")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("workflow event mirror entry must be a JSON object")
    return WorkflowRunEventMirrorRecord.from_wire(payload)


def _redis_client() -> Any:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    return aioredis.from_url(_mirror_redis_url(), decode_responses=True)


async def _close_redis(client: Any) -> None:
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is None:
        return
    result = closer()
    if inspect.isawaitable(result):
        await result


def _mirror_backend() -> WorkflowEventMirrorBackend:
    value = os.getenv("WORKFLOW_EVENT_MIRROR_BACKEND", "memory").strip().lower()
    return "redis" if value == "redis" else "memory"


def _mirror_stream() -> str:
    return os.getenv("WORKFLOW_EVENT_MIRROR_STREAM", DEFAULT_WORKFLOW_EVENT_STREAM)


def _mirror_redis_url() -> str:
    return (
        os.getenv("WORKFLOW_EVENT_MIRROR_REDIS_URL")
        or os.getenv("ODP_REDIS_URL")
        or os.getenv("REDIS_URL")
        or get_settings().redis_url
    )


def _stable_facts(event: dict[str, Any]) -> dict[str, Any]:
    block_reason = _read_dict(event.get("blockReason"))
    details = _read_dict(event.get("details"))
    block_details = _read_dict(block_reason.get("details"))
    return {
        "nodeId": event.get("nodeId"),
        "eventType": event.get("eventType"),
        "bindingId": details.get("bindingId") or block_details.get("bindingId"),
        "blockReasonCode": block_reason.get("code"),
    }


def _read_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "DEFAULT_WORKFLOW_EVENT_STREAM",
    "SCHEMA_VERSION",
    "WORKFLOW_EVENT_MIRROR_PROVIDER",
    "WorkflowRunEventMirrorRecord",
    "list_workflow_event_mirror_records",
    "list_workflow_event_mirror_transcript",
    "publish_workflow_run_event_mirror",
    "reset_memory_workflow_event_mirror",
]
