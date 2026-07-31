"""OpenAI AI processor."""

import asyncio
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from backend.llm.base import LlmAdapterError
from backend.llm.factory import build_openai_compat_adapter
from backend.processors.base import AbstractProcessor, ProcessingResult
from backend.processors.registry import register_processor

if TYPE_CHECKING:
    from backend.models.record import CollectedRecord

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(template: str, data: dict[str, Any]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: str(data.get(m.group(1), "")), template)


@register_processor
class OpenAIProcessor(AbstractProcessor):
    """Process records using OpenAI models."""

    processor_type = "openai"

    async def process(
        self,
        records: list["CollectedRecord"],
        prompt_template: str,
        config: dict[str, Any],
    ) -> ProcessingResult:
        try:
            from openai import AsyncOpenAI  # noqa: F401 -- import-availability probe only
        except ImportError:
            return ProcessingResult(success=False, error="openai package not installed")

        from backend.config import get_settings

        settings = get_settings()

        api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        base_url: str | None = config.get("base_url") or None
        model = config.get("model", "gpt-4o-mini")
        max_tokens = config.get("max_tokens", 1024)
        use_json_mode = config.get("json_mode", base_url is None)
        # AUDIT C8: explicit per-request timeout — the SDK default (600s x 2
        # retries) can otherwise pin a whole batch in ai_processing for hours
        # behind a dead/slow gateway. A source's ai_config can still override
        # this per call via config["timeout"].
        request_timeout = config.get("timeout", settings.llm_request_timeout_seconds)
        # AUDIT C25: bound how many records are in flight at once instead of
        # a plain await-in-a-for-loop (wall clock == record_count x latency).
        max_concurrency = max(1, settings.llm_max_concurrency)

        logger.info(
            "openai processor | model=%s base_url=%s max_tokens=%d records=%d "
            "timeout=%s max_concurrency=%d",
            model, base_url or "(default)", max_tokens, len(records),
            request_timeout, max_concurrency,
        )

        # model-provider runtime PR-E: client construction (SSRF guard + DNS-rebind pinning)
        # is consolidated through backend.llm.openai_compat.OpenAICompatAdapter
        # (via backend.llm.factory.build_openai_compat_adapter) — this used to
        # be a verbatim duplicate of the same wiring in chat.py/skill_channel.
        # Key-exfil guard behavior is unchanged: base_url is DB/config-supplied,
        # so if it doesn't pass the SSRF/public-host check, api_key is never
        # attached to a client pointed at it; None (OpenAI's own default
        # endpoint) is left unvalidated, exactly as before.
        adapter = build_openai_compat_adapter(base_url=base_url, api_key=api_key)
        try:
            client = await adapter.get_client()
        except LlmAdapterError as exc:
            return ProcessingResult(success=False, error=f"openai processor: {exc}")

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _process_one(i: int, record: "CollectedRecord") -> dict[str, Any]:
            # AUDIT C25: the semaphore (not the for-loop) is what bounds
            # concurrency now — every record's coroutine is created up front
            # and handed to gather, but only `max_concurrency` run their LLM
            # call at once.
            async with semaphore:
                try:
                    prompt = _render(prompt_template, record.normalized_data)
                    logger.debug("openai req [%d/%d] | prompt_preview=%s",
                                 i + 1, len(records), prompt[:200])
                    kwargs: dict[str, Any] = dict(
                        model=model,
                        max_tokens=max_tokens,
                        messages=[{"role": "user", "content": prompt}],
                        timeout=request_timeout,
                    )
                    if use_json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    response = await client.chat.completions.create(**kwargs)
                    text = response.choices[0].message.content or "{}"
                    usage = response.usage
                    logger.info("openai resp [%d/%d] | prompt_tokens=%d completion_tokens=%d preview=%s",
                                i + 1, len(records),
                                usage.prompt_tokens if usage else -1,
                                usage.completion_tokens if usage else -1,
                                text[:200])
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"analysis": text}
                except Exception as exc:
                    # A single record's failure must not abort the batch —
                    # it becomes an {"error": ...} enrichment, exactly like
                    # the old sequential loop's inner except did.
                    logger.error("openai error [%d/%d] | %s", i + 1, len(records), exc)
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
            # AsyncOpenAI does not close an externally-supplied http_client
            # (it doesn't own it) — close ours ourselves, same as the
            # `async with client:` scope guarded_async_client callers use.
            # OpenAICompatAdapter.aclose() is a no-op when base_url was never
            # set (no pinned transport was ever created), matching the old
            # `if pinned_http_client is not None` guard exactly.
            await adapter.aclose()

        logger.info("openai processor done | success=%d errors=%d",
                    sum(1 for e in enrichments if "error" not in e),
                    sum(1 for e in enrichments if "error" in e))
        return ProcessingResult(success=True, enrichments=enrichments)
