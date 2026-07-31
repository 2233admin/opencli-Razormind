"""Claude (Anthropic) AI processor."""

import asyncio
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from backend.llm.factory import build_anthropic_adapter
from backend.processors.base import AbstractProcessor, ProcessingResult
from backend.processors.registry import register_processor

if TYPE_CHECKING:
    from backend.models.record import CollectedRecord

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(template: str, data: dict[str, Any]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: str(data.get(m.group(1), "")), template)


@register_processor
class ClaudeProcessor(AbstractProcessor):
    """Process records using Anthropic Claude."""

    processor_type = "claude"

    async def process(
        self,
        records: list["CollectedRecord"],
        prompt_template: str,
        config: dict[str, Any],
    ) -> ProcessingResult:
        try:
            import anthropic  # noqa: F401 -- import-availability probe only
        except ImportError:
            return ProcessingResult(
                success=False, error="anthropic package not installed"
            )

        from backend.config import get_settings

        settings = get_settings()

        api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        model = config.get("model", "claude-haiku-4-5-20251001")
        max_tokens = config.get("max_tokens", 1024)
        # AUDIT C8: explicit per-request timeout (see openai_processor's twin
        # comment) — a source's ai_config can still override this per call
        # via config["timeout"].
        request_timeout = config.get("timeout", settings.llm_request_timeout_seconds)
        # AUDIT C25: bound how many records are in flight at once instead of
        # a plain await-in-a-for-loop (wall clock == record_count x latency).
        max_concurrency = max(1, settings.llm_max_concurrency)

        logger.info(
            "claude processor | model=%s max_tokens=%d records=%d "
            "timeout=%s max_concurrency=%d",
            model, max_tokens, len(records), request_timeout, max_concurrency,
        )

        # model-provider runtime PR-E: client construction consolidated through
        # backend.llm.anthropic.AnthropicAdapter (via
        # backend.llm.factory.build_anthropic_adapter). This processor never
        # configured a base_url (Anthropic's endpoint is effectively fixed),
        # so this is a pure passthrough construction — no new SSRF surface,
        # no behavior change.
        adapter = build_anthropic_adapter(api_key=api_key)
        client = await adapter.get_client()

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _process_one(i: int, record: "CollectedRecord") -> dict[str, Any]:
            # AUDIT C25: the semaphore (not the for-loop) is what bounds
            # concurrency now — every record's coroutine is created up front
            # and handed to gather, but only `max_concurrency` run their LLM
            # call at once.
            async with semaphore:
                try:
                    prompt = _render(prompt_template, record.normalized_data)
                    logger.debug("claude req [%d/%d] | prompt_preview=%s",
                                 i + 1, len(records), prompt[:200])
                    response = await client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        messages=[{"role": "user", "content": prompt}],
                        timeout=request_timeout,
                    )
                    text = response.content[0].text
                    usage = response.usage
                    logger.info("claude resp [%d/%d] | input_tokens=%d output_tokens=%d preview=%s",
                                i + 1, len(records),
                                usage.input_tokens, usage.output_tokens,
                                text[:200])
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"analysis": text}
                except Exception as exc:
                    # A single record's failure must not abort the batch —
                    # it becomes an {"error": ...} enrichment, exactly like
                    # the old sequential loop's inner except did.
                    logger.error("claude error [%d/%d] | %s", i + 1, len(records), exc)
                    return {"error": str(exc)}

        try:
            # asyncio.gather returns results in the same order as the input
            # awaitables (not completion order), so enrichments[i] still
            # lines up with records[i] — process_with_ai's zip(records,
            # enrichments) contract (and the C3 enriched-count fix) holds.
            enrichments: list[dict[str, Any]] = list(await asyncio.gather(
                *(_process_one(i, record) for i, record in enumerate(records))
            ))
        finally:
            await adapter.aclose()

        logger.info("claude processor done | success=%d errors=%d",
                    sum(1 for e in enrichments if "error" not in e),
                    sum(1 for e in enrichments if "error" in e))
        return ProcessingResult(success=True, enrichments=enrichments)
