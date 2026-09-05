"""The write seam: where collected items go.

A channel fetches; the runner orchestrates; a **Sink** decides the destination.
Today the only destination is the legacy ``collected_records`` table
(``LegacyDbSink``). Next, the same items also flow to the ODP hot path
(``OdpSink``), and both at once for shadow validation (``DualSink``) — all behind
this one interface, chosen per source by ``write_strategy``, with no change to
channels or the runner.

This is the strangler-fig seam: the old path keeps working unchanged, the new
path is wired in beside it, and a source is migrated by flipping its strategy —
never by rewriting the pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CollectionLineage:
    """Immutable provenance envelope shared by collection projections.

    Every value is an identifier or reference owned by an existing subsystem.
    ``None`` means that subsystem did not establish the value at this boundary;
    callers must not synthesize replacements.
    """

    task_id: str | None = None
    source_id: str | None = None
    provider: str | None = None
    ingest_mode: str | None = None
    collection_run_id: str | None = None
    acquisition_execution_id: str | None = None
    source_revision_id: str | None = None
    source_binding_revision_id: str | None = None
    account_revision_id: str | None = None
    credential_revision_id: str | None = None
    project_id: str | None = None
    scope_ref: str | None = None
    worker_id: str | None = None
    runtime_id: str | None = None
    trace_id: str | None = None
    trace_ref: str | None = None
    artifact_refs: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        if self.artifact_refs is not None and not isinstance(self.artifact_refs, tuple):
            object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON representation persisted on projections."""
        values = {
            "task_id": self.task_id,
            "source_id": self.source_id,
            "provider": self.provider,
            "ingest_mode": self.ingest_mode,
            "collection_run_id": self.collection_run_id,
            "acquisition_execution_id": self.acquisition_execution_id,
            "source_revision_id": self.source_revision_id,
            "source_binding_revision_id": self.source_binding_revision_id,
            "account_revision_id": self.account_revision_id,
            "credential_revision_id": self.credential_revision_id,
            "project_id": self.project_id,
            "scope_ref": self.scope_ref,
            "worker_id": self.worker_id,
            "runtime_id": self.runtime_id,
            "trace_id": self.trace_id,
            "trace_ref": self.trace_ref,
            "artifact_refs": (
                list(self.artifact_refs) if self.artifact_refs is not None else None
            ),
        }
        return values

    @classmethod
    def from_dict(cls, values: dict[str, Any] | None) -> "CollectionLineage | None":
        if values is None:
            return None
        fields = {
            "task_id",
            "source_id",
            "provider",
            "ingest_mode",
            "collection_run_id",
            "acquisition_execution_id",
            "source_revision_id",
            "source_binding_revision_id",
            "account_revision_id",
            "credential_revision_id",
            "project_id",
            "scope_ref",
            "worker_id",
            "runtime_id",
            "trace_id",
            "trace_ref",
            "artifact_refs",
        }
        return cls(**{key: values[key] for key in fields if key in values})


@dataclass
class RunContext:
    """Identity of one collection run, threaded to whichever sink handles it.

    ``provider`` is the channel_type (e.g. ``"rss"``); it becomes the ODP
    ``provider`` and the legacy ``channel_type``. ``ingest_mode`` is
    ``snapshot`` (full re-list) or ``stream`` (incremental), mirroring the ODP
    contract. Optional references remain absent when the owning subsystem has
    not established them.
    """

    task_id: str
    source_id: str
    provider: str
    ingest_mode: str = "snapshot"
    run_id: str | None = None
    trace_id: str | None = None
    acquisition_execution_id: str | None = None
    source_revision_id: str | None = None
    source_binding_revision_id: str | None = None
    account_revision_id: str | None = None
    credential_revision_id: str | None = None
    project_id: str | None = None
    scope_ref: str | None = None
    worker_id: str | None = None
    runtime_id: str | None = None
    trace_ref: str | None = None
    artifact_refs: list[Any] | tuple[Any, ...] | None = None
    lineage: CollectionLineage | None = None

    def lineage_envelope(self) -> CollectionLineage:
        """Build one immutable envelope without inventing missing references."""
        values = self.lineage.to_dict() if self.lineage is not None else {}
        values.update(
            task_id=self.task_id,
            source_id=self.source_id,
            provider=self.provider,
            ingest_mode=self.ingest_mode,
        )
        optional = {
            "collection_run_id": self.run_id,
            "trace_id": self.trace_id,
            "acquisition_execution_id": self.acquisition_execution_id,
            "source_revision_id": self.source_revision_id,
            "source_binding_revision_id": self.source_binding_revision_id,
            "account_revision_id": self.account_revision_id,
            "credential_revision_id": self.credential_revision_id,
            "project_id": self.project_id,
            "scope_ref": self.scope_ref,
            "worker_id": self.worker_id,
            "runtime_id": self.runtime_id,
            "trace_ref": self.trace_ref,
            "artifact_refs": self.artifact_refs,
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        return CollectionLineage.from_dict(values)  # type: ignore[return-value]

@dataclass
class SinkResult:
    """Outcome of writing one batch.

    Counts share one vocabulary across sinks, but each is defined relative to
    that sink's OWN durable boundary — not a shared one. A DualSink comparison
    must account for the boundaries differing:

      * ``accepted``   — items the sink committed to its durable path. For
        ``LegacyDbSink`` this is rows inserted into ``collected_records``; for
        ``OdpSink`` it is events the ingest service *queued* (Redis Stream) —
        a weaker guarantee than an inserted row.
      * ``duplicates`` — items the sink recognized as already-seen before its
        durable write (legacy: ``content_hash`` hit; ODP: ``(source_id, event_id)``).
      * ``rejected``   — items dropped for validation or a permanent error;
        detail in ``errors``.
      * ``normalized`` — items that passed normalization (legacy bookkeeping).
      * ``records``    — persisted ORM rows, for sinks that own a local table
        (``LegacyDbSink``), so the downstream AI/notify steps can enrich them.
        Forward-only sinks (``OdpSink``) leave it empty and those steps no-op,
        because on the ODP path enrichment happens off the ``record.committed``
        stream.
      * ``shadow_meta`` — set only by ``DualSink``: the best-effort shadow
        leg's OWN ``accepted``/``duplicates``/``rejected`` counts (the top-level
        fields above stay the legacy/authoritative leg's numbers so existing
        callers are unaffected). ``None`` when there is no shadow leg, or when
        the shadow write raised before producing a result. Previously these
        counts were only logged (P1-7); this lets a caller (pipeline.py)
        surface them without changing what "accepted"/"duplicates" mean.
    """

    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    normalized: int = 0
    records: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    shadow_meta: dict[str, int] | None = None


class ItemSink(Protocol):
    """Accepts raw collected items and persists/forwards them somewhere.

    Implementations own their own normalization, dedup, and persistence so the
    orchestrator stays destination-agnostic. The whole surface is one method:
    everything else a sink does is hidden behind it.
    """

    async def write_batch(self, ctx: RunContext, items: Sequence[dict]) -> SinkResult:
        ...
