"""Typed mirror of the Rust ODP contract (``odp-rs/crates/odp-contracts``).

These dataclasses pin the wire shape opencli-admin exchanges with the ODP ingest
service so the forward path has one testable source of truth. Field names and
semantics track ``RecordEvent`` / ``IngestBatchResponse`` in ``odp-contracts``
(``record_v2.schema.json``); the integer :data:`SCHEMA_VERSION` is ``1`` — the
"v2" in the file name is the schema revision, not the wire version.

Wire-form note: :meth:`RecordEvent.to_wire` reproduces the EXACT JSON the legacy
``odp_client.triple_to_event`` forwarder emits today — explicit ``null`` for an
absent ``cursor``/``trace_id``/``task_id``/``raw_data``, ids stringified — NOT
Rust's skip-when-null serialization. The Rust side accepts both via
``#[serde(default)]``; keeping the bytes identical is what lets a later step swap
the forwarder for an ``OdpSink`` behind a characterization test proving the
payload is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Mirrors ``odp_contracts::SCHEMA_VERSION``.
SCHEMA_VERSION = 1

# Mirrors the Rust ``IngestMode`` enum (serde snake_case).
IngestMode = Literal["snapshot", "stream"]


@dataclass
class RecordEvent:
    """One event on the ODP ingest wire. Mirrors ``odp_contracts::RecordEvent``."""

    provider: str
    source_id: str
    event_id: str
    source_ts: str  # RFC3339, e.g. "2026-06-30T12:00:00Z"
    payload: dict[str, Any]
    raw_data: Any = None
    ingest_mode: IngestMode = "snapshot"
    cursor: str | None = None
    trace_id: str | None = None
    task_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def to_wire(self) -> dict[str, Any]:
        """JSON-ready dict, byte-compatible with the legacy forwarder.

        Key order and explicit-null fields match the historical payload exactly;
        do not "tidy" this into skip-when-null without re-locking the
        characterization tests.
        """
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "source_id": self.source_id,
            "event_id": self.event_id,
            "ingest_mode": self.ingest_mode,
            "source_ts": self.source_ts,
            "cursor": self.cursor,
            "payload": self.payload,
            "raw_data": self.raw_data,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
        }


@dataclass
class IngestReject:
    """One rejected event in a batch response. Mirrors ``odp_contracts::IngestReject``."""

    index: int
    reason: str
    event_id: str | None = None

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> IngestReject:
        return cls(
            index=int(d.get("index", 0)),
            reason=str(d.get("reason", "")),
            event_id=d.get("event_id"),
        )


@dataclass
class OdpIngestResponse:
    """Batch ingest result. Mirrors ``odp_contracts::IngestBatchResponse``.

    The legacy forwarder reads only the three counts; ``errors`` is parsed here
    so callers that want per-event reject detail no longer have to re-derive it.
    """

    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: list[IngestReject] = field(default_factory=list)

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> OdpIngestResponse:
        return cls(
            accepted=int(d.get("accepted", 0)),
            duplicates=int(d.get("duplicates", 0)),
            rejected=int(d.get("rejected", 0)),
            errors=[IngestReject.from_wire(e) for e in (d.get("errors") or [])],
        )
