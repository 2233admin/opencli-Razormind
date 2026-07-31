"""Pipeline Step 4: Optional AI enrichment of records."""

import logging
from typing import Any

from backend.models.record import CollectedRecord
from backend.processors.registry import get_processor

logger = logging.getLogger(__name__)


async def _resolve_llm_config(ai_config: dict[str, Any], source_id: Any) -> dict[str, Any]:
    """model-provider runtime PR-F (decision #9): soft dual-track convergence between
    ``DataSource.ai_config``'s legacy inline ``api_key``/``base_url`` and the
    governed ``ModelProvider`` catalog (``backend.models.provider``).

    * No ``provider_id`` in ``ai_config`` -> returned byte-identical to
      today's behavior (same dict, same keys, same values) — the only
      addition is a deprecation warning when inline ``api_key``/``base_url``
      are present, since that's the legacy channel this decision is steering
      callers away from.
    * ``provider_id`` set and resolves -> the provider's ``provider_type``/
      ``api_key``/``base_url``/``default_model`` win over anything supplied
      inline (a warning is logged when inline creds were *also* supplied,
      since they're now silently ignored in favor of the provider).
    * ``provider_id`` set but does NOT resolve (deleted/bad id) -> warn and
      fall back to ``ai_config`` unchanged, exactly like the no-``provider_id``
      case (inline creds if present, otherwise whatever the processor's own
      env-var fallback does) — this mirrors this module's existing fail-soft
      posture (unknown ``processor_type`` / missing config never raise, they
      just skip/continue) rather than introducing a new crash path.
    """
    provider_id = ai_config.get("provider_id")
    has_inline = bool(ai_config.get("api_key") or ai_config.get("base_url"))

    if not provider_id:
        if has_inline:
            logger.warning(
                "DataSource %s ai_config uses inline LLM credentials; "
                "reference a provider_id instead (inline config is deprecated)",
                source_id,
            )
        return ai_config

    from backend.database import AsyncSessionLocal
    from backend.services.provider_model_service import get_provider

    async with AsyncSessionLocal() as session:
        provider = await get_provider(session, provider_id)

    if provider is None:
        logger.warning(
            "DataSource %s ai_config.provider_id=%s does not resolve to an "
            "existing ModelProvider; falling back to inline config",
            source_id, provider_id,
        )
        return ai_config

    if has_inline:
        logger.warning(
            "DataSource %s ai_config supplies both provider_id=%s and inline "
            "api_key/base_url; provider_id takes precedence",
            source_id, provider_id,
        )

    resolved = dict(ai_config)
    resolved["processor_type"] = provider.provider_type
    resolved["api_key"] = provider.api_key
    resolved["base_url"] = provider.base_url
    if provider.default_model:
        resolved["model"] = provider.default_model
    return resolved


async def process_with_ai(
    records: list[CollectedRecord],
    ai_config: dict[str, Any] | None,
    *,
    source_id: Any = None,
    resolve_provider: bool = True,
) -> int:
    """Enrich records with AI processing in-place. Returns the number of
    records actually enriched.

    ai_config keys:
        processor_type: claude | openai | local
        model: model name
        prompt_template: Jinja2 template
        provider_id: model-provider runtime PR-F — governed ModelProvider reference; wins
            over inline api_key/base_url when both are present (decision #9)
        ...processor-specific options

    ``source_id`` (the owning DataSource's id) is only used to identify the
    source in deprecation/fallback warning log lines above; it never affects
    resolution logic.

    ``resolve_provider`` gates the decision #9 dual-track resolution above.
    Callers pass ``False`` when ``ai_config`` is really an *agent*-level
    config (``ai_agents.processor_config`` merged with its own
    ``ai_agents.provider_id`` resolution in ``backend.pipeline.runner`` phase
    2) rather than a ``DataSource.ai_config`` value — that merge already
    happened upstream through a separate, pre-existing, intentionally
    untouched mechanism (decision #9 explicitly keeps ``ai_agents.provider_id``
    a loose string column, out of this PR's scope), and it routinely leaves
    inline ``api_key``/``base_url`` in the dict with no ``provider_id`` key.
    Running this function's deprecation-warning logic against that dict would
    misfire on every agent-driven run, not just legacy inline
    ``DataSource.ai_config`` — so agent-sourced configs skip resolution
    entirely and are used exactly as before.

    AUDIT C3: an unknown/misconfigured ``processor_type`` used to no-op with
    no signal at all, while the caller (``pipeline.py``) unconditionally
    reported ``len(new_records)`` as "processed" regardless of what actually
    happened here. This now returns 0 and logs a warning so the caller can
    tell the difference between "enriched" and "silently skipped".
    """
    if not ai_config or not records:
        return 0

    resolved_config = (
        await _resolve_llm_config(ai_config, source_id) if resolve_provider else ai_config
    )

    processor_type = resolved_config.get("processor_type", "claude")
    try:
        processor = get_processor(processor_type)
    except ValueError:
        logger.warning(
            "DataSource %s ai_config.processor_type=%r is not a registered "
            "processor; AI enrichment skipped for %d record(s)",
            source_id, processor_type, len(records),
        )
        return 0

    result = await processor.process(
        records=records,
        prompt_template=resolved_config.get("prompt_template", ""),
        config=resolved_config,
    )

    enriched = 0
    for record, enrichment in zip(records, result.enrichments):
        record.ai_enrichment = enrichment
        record.status = "ai_processed"
        enriched += 1

    return enriched
