"""LegacyDbSink — the original write path, now behind the ItemSink seam.

Normalizes items (``content_hash`` dedup) and stores them in
``collected_records``, exactly as the pipeline did inline before the seam
existed. Extracting it changes no behavior: it still calls
``normalizer.normalize_items`` then ``storer.store_records`` inside a
short-lived session.

Two things stay where they were on purpose, to keep this slice behavior-only:
  * The ODP forward still lives inside ``storer.store_records`` (fires only
    when ``ODP_INGEST_URL`` is set AND ``forward_to_odp`` is True), now behind
    a ``forward_to_odp`` gate so DualSink can suppress it on the legacy leg.
    The dedicated ``OdpSink`` owns the forward going forward; ``write_strategy``
    (``backend/pipeline/sinks/strategy.py``) picks the destination explicitly.
  * Dedup here remains ``content_hash`` (title|url|content|source_id). The ODP
    path keys on ``(source_id, event_id)`` instead; the two will disagree, and
    surfacing that disagreement under shadow is the point of the migration.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from backend.pipeline.sinks.base import RunContext, SinkResult

logger = logging.getLogger(__name__)


class LegacyDbSink:
    """Persist collected items to the legacy ``collected_records`` table.

    ``forward_to_odp`` gates the ODP shadow-forward that lives inside
    ``storer.store_records``. Defaults to False: the ``legacy`` write_strategy
    (this sink's default construction in ``select_sink``) must NOT forward to
    ODP just because a bare ``ODP_INGEST_URL`` env var happens to be set
    elsewhere in the deployment — that was the P1-1 strangler-collapse bug
    (an unmigrated source silently leaking into ODP, bypassing the
    write_strategy state machine entirely). Only a sink built for an explicit
    ODP-aware strategy (``odp_shadow`` / ``odp_dual_required`` / ``odp_primary``,
    via ``DualSink``) opts a source into the forward now, and DualSink already
    constructs its legacy leg with ``forward_to_odp=False`` regardless (so
    ``OdpSink`` is the single sender, avoiding a double-send).
    """

    def __init__(self, forward_to_odp: bool = False) -> None:
        self.forward_to_odp = forward_to_odp

    async def write_batch(self, ctx: RunContext, items: Sequence[dict]) -> SinkResult:
        # Function-local imports mirror the orchestrator: ``AsyncSessionLocal`` is
        # rebound per call so tests can patch ``backend.database.AsyncSessionLocal``,
        # and ``storer``/``normalizer`` are reached as module attributes so
        # ``patch("backend.pipeline.storer.store_records")`` takes effect.
        from backend.channels.registry import get_channel
        from backend.database import AsyncSessionLocal
        from backend.pipeline import normalizer, storer

        triples = normalizer.normalize_items(list(items), ctx.source_id)

        # C7: ask the channel for each item's stable native id (RSS entry id,
        # etc.) so store_records can update an edited item in place instead
        # of inserting a duplicate. A channel without identity() returns None
        # for every item — identical to the pre-C7 behavior (falls through to
        # unchanged content_hash-only dedup). Best-effort: any failure here
        # (unregistered channel_type, a channel's identity() raising on odd
        # input, ...) also degrades to that unchanged behavior rather than
        # breaking storage over a dedup nicety.
        try:
            channel = get_channel(ctx.provider)
            identities: list[str | None] | None = [
                channel.identity(raw) for raw, _, _ in triples
            ]
        except Exception as exc:
            logger.debug(
                "could not resolve identity() for provider=%s (falling back "
                "to content_hash-only dedup this batch): %s",
                ctx.provider, exc,
            )
            identities = None

        # The ODP shadow-forward still fires inside storer.store_records; the
        # forward_to_odp gate lets DualSink(LegacyDbSink + OdpSink) turn it off on
        # the legacy leg so ODP is not double-sent.
        async with AsyncSessionLocal() as session:
            new_records, skipped = await storer.store_records(
                session, ctx.task_id, ctx.source_id, triples,
                channel_type=ctx.provider, forward_to_odp=self.forward_to_odp,
                identities=identities,
                lineage=ctx.lineage_envelope(),
            )
            await session.commit()

        return SinkResult(
            accepted=len(new_records),
            duplicates=skipped,
            normalized=len(triples),
            records=new_records,
        )
