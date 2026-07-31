"""Local model processor via Ollama/vLLM compatible API.

model-provider runtime PR-E note: deliberately NOT routed through
``backend.llm.factory``/``OpenAICompatAdapter`` like the openai/claude
processors. Two real incompatibilities, not just "not bothered yet":

  * ``api_style="ollama"`` (the default) speaks Ollama's *native*
    ``POST /api/generate`` protocol (``{"model", "prompt", "stream"}`` in,
    ``{"response": ...}`` out) — a different wire protocol entirely from
    ``OpenAICompatAdapter``'s ``AsyncOpenAI`` client, which only knows the
    OpenAI ``/v1/chat/completions`` shape. There is no adapter call that
    reaches ``/api/generate`` without changing what gets sent over the wire.
  * even the ``api_style="openai"`` branch has a per-call configurable
    ``timeout`` (``config.get("timeout", ...)``, defaulting to the shared
    ``Settings.llm_request_timeout_seconds`` — AUDIT C8) threaded straight
    into the raw ``httpx.AsyncClient`` — ``OpenAICompatAdapter`` (frozen
    behavior, PR-B, 1599-test baseline) has no parameter to accept a
    caller-supplied timeout, so swapping in the adapter here would silently
    drop that config knob for anyone using it.

Also has no SSRF guard today for either branch (raw ``httpx.AsyncClient``,
no ``url_guard`` call) — unlike ``openai_processor``/``skill_channel``. This
is pre-existing behavior, left as-is; closing it would need its own change
(not a client-construction consolidation) and is out of PR-E's "zero
regression" scope.
"""

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

import httpx

from backend.processors.base import AbstractProcessor, ProcessingResult
from backend.processors.registry import register_processor

if TYPE_CHECKING:
    from backend.models.record import CollectedRecord

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(template: str, data: dict[str, Any]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: str(data.get(m.group(1), "")), template)


@register_processor
class LocalProcessor(AbstractProcessor):
    """Process records using a locally hosted model (Ollama/vLLM)."""

    processor_type = "local"

    async def process(
        self,
        records: list["CollectedRecord"],
        prompt_template: str,
        config: dict[str, Any],
    ) -> ProcessingResult:
        from backend.config import get_settings

        settings = get_settings()

        base_url = config.get("base_url", "http://localhost:11434")
        model = config.get("model", "llama3")
        # AUDIT C8: fallback now comes from the shared llm_request_timeout_seconds
        # setting instead of a hardcoded 120 — an explicit config["timeout"]
        # still wins, unchanged.
        timeout = config.get("timeout", settings.llm_request_timeout_seconds)
        # Support both Ollama (/api/generate) and OpenAI-compatible (/v1/chat/completions)
        api_style = config.get("api_style", "ollama")
        # AUDIT C25: bound how many records are in flight at once instead of
        # a plain await-in-a-for-loop (wall clock == record_count x latency).
        max_concurrency = max(1, settings.llm_max_concurrency)

        semaphore = asyncio.Semaphore(max_concurrency)

        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            # httpx.AsyncClient is safe to share across concurrent requests
            # (connection-pool backed) — the semaphore just caps how many of
            # `records` are in flight through it at once.
            async def _process_one(record: "CollectedRecord") -> dict[str, Any]:
                async with semaphore:
                    prompt = _render(prompt_template, record.normalized_data)
                    try:
                        if api_style == "ollama":
                            resp = await client.post(
                                "/api/generate",
                                json={"model": model, "prompt": prompt, "stream": False},
                            )
                            resp.raise_for_status()
                            text = resp.json().get("response", "")
                        else:
                            # OpenAI-compatible
                            resp = await client.post(
                                "/v1/chat/completions",
                                json={
                                    "model": model,
                                    "messages": [{"role": "user", "content": prompt}],
                                },
                            )
                            resp.raise_for_status()
                            text = resp.json()["choices"][0]["message"]["content"]

                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return {"analysis": text}
                    except Exception as exc:
                        # A single record's failure must not abort the batch.
                        return {"error": str(exc)}

            # asyncio.gather returns results in the same order as the input
            # awaitables (not completion order), so enrichments[i] still
            # lines up with records[i].
            enrichments: list[dict[str, Any]] = list(await asyncio.gather(
                *(_process_one(record) for record in records)
            ))

        return ProcessingResult(success=True, enrichments=enrichments)
