"""Map a normalized pipeline record to a :class:`RecordEvent`.

The input is the normalizer's output (the ``normalized`` dict plus its ``raw``
source item and ``content_hash``), NOT the raw collector item. The ODP payload
must carry the same fields the legacy DB stores in ``normalized_data``, or a
shadow comparison would diff on shape instead of substance.

This mapper is the single source of truth for the ODP wire shape: both the
legacy forwarder (``odp_client.triple_to_event``) and the future ``OdpSink`` go
through it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.odp.schemas import IngestMode, RecordEvent


def provider_for_channel(channel_type: str) -> str:
    return f"opencli-admin/{channel_type}"


def _source_ts(normalized: dict[str, Any]) -> str:
    """RFC3339 ``source_ts`` from ``normalized['published_at']``.

    Falls back to ``now(UTC)`` when the field is absent or unparseable, and
    assumes UTC for a naive timestamp — identical to the legacy forwarder.
    """
    published = normalized.get("published_at") or ""
    try:
        if published:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        else:
            ts = datetime.now(UTC)
    except ValueError:
        ts = datetime.now(UTC)
    return ts.isoformat().replace("+00:00", "Z")


class RecordEventMapper:
    """Normalized record -> :class:`RecordEvent`."""

    @staticmethod
    def from_triple(
        *,
        channel_type: str,
        source_id: str,
        task_id: str,
        raw: dict[str, Any],
        normalized: dict[str, Any],
        content_hash: str,
        ingest_mode: IngestMode = "snapshot",
        cursor: str | None = None,
        trace_id: str | None = None,
    ) -> RecordEvent:
        return RecordEvent(
            provider=provider_for_channel(channel_type),
            source_id=str(source_id),
            event_id=content_hash,
            source_ts=_source_ts(normalized),
            payload=normalized,
            raw_data=raw,
            ingest_mode=ingest_mode,
            cursor=cursor,
            trace_id=trace_id,
            task_id=str(task_id),
        )
