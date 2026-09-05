"""OdpSink — forward collected items to the Rust ODP ingest hot path.

Forward-only: it normalizes and posts events through ``odp_client`` but owns no
local table, so ``SinkResult.records`` is empty and the pipeline's AI/notify
steps no-op (on the ODP path that enrichment happens off the ``record.committed``
stream, not here).

``accepted`` here means *queued* by the ingest service (a Redis Stream), a weaker
guarantee than ``LegacyDbSink``'s inserted row — see ``SinkResult``.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.pipeline.sinks.base import RunContext, SinkResult


class OdpSink:
    """Post collected items to odp-ingest via the shared mapper/client."""

    async def write_batch(self, ctx: RunContext, items: Sequence[dict]) -> SinkResult:
        from backend.pipeline import normalizer, odp_client

        triples = normalizer.normalize_items(list(items), ctx.source_id)
        if not triples:
            return SinkResult()

        accepted, duplicates, rejected = await odp_client.forward_triples(
            channel_type=ctx.provider,
            task_id=ctx.task_id,
            source_id=ctx.source_id,
            triples=triples,
        )
        return SinkResult(
            accepted=accepted,
            duplicates=duplicates,
            rejected=rejected,
            normalized=len(triples),
            records=[],
        )
