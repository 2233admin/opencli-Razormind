"""Collect a cited Doubao research answer through the installed OpenCLI adapter."""

import os
import re
from typing import Any

from backend.channels.base import (
    AbstractChannel,
    Capabilities,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel

_URL_RE = re.compile(r"https?://[^\s<>\[\](){}'\"]+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?\uff0c\u3002\uff1b\uff1a\uff01\uff1f"
#: OpenCLI adapter reports a captcha wall this way (verified on opencli 1.8.6).
_CAPTCHA_MARKERS = (
    "verification challenge",
    "captcha",
    "blocked the request",
    "人机验证",
    "验证码",
)
#: Transient CDP/browser race conditions — retrying the same question in the
#: same session usually succeeds once the navigation settles. Classified as
#: ``ConnectionError`` (retryable in error_taxonomy) so the thick ``fetch()``
#: retry loop picks them up, while a captcha wall (above) is NOT retried.
_TRANSIENT_CDP_MARKERS = (
    "CDP connection is not open",
    "Inspected target navigated or closed",
    "Execution context was destroyed",
    "Target closed",
    "Session closed",
    "cloneNode",
)


def _citations(text: str) -> list[dict[str, str]]:
    """Extract and de-duplicate URLs while preserving the answer's order."""
    seen: set[str] = set()
    citations: list[dict[str, str]] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
        if url and url not in seen:
            seen.add(url)
            citations.append({"url": url})
    return citations


def _answer(rows: list[dict[str, Any]]) -> str:
    """Return the assistant text from OpenCLI's case-preserving table JSON."""
    assistant_rows = [
        row
        for row in rows
        if str(row.get("Role", row.get("role", ""))).strip().lower()
        in {"assistant", "ai", "bot", "助手"}
    ]
    candidates = assistant_rows or rows
    return "\n".join(
        str(row.get("Text", row.get("text", ""))).strip()
        for row in candidates
        if row.get("Text", row.get("text"))
    ).strip()


def _conversation_url(stdout: str) -> str:
    """Extract https://www.doubao.com/chat/<id> from `doubao status -f json` output."""
    try:
        rows = _parse_opencli_rows(stdout)
    except Exception:
        return ""
    for row in rows:
        url = str(row.get("Url", row.get("url", "")) or "").strip()
        if "/chat/" in url:
            return url
    return ""


def _is_captcha_block(stderr: str, stdout: str) -> bool:
    """True when the adapter reports a captcha/verification wall."""
    text = f"{stderr} {stdout}".lower()
    return any(marker in text for marker in _CAPTCHA_MARKERS)


def _is_transient_cdp_fault(stderr: str, stdout: str) -> bool:
    """True when the adapter hit a CDP/browser race (navigation, closed tab,
    session teardown) — retrying the same question usually succeeds."""
    text = f"{stderr} {stdout}"
    return any(marker in text for marker in _TRANSIENT_CDP_MARKERS)


async def _run_doubao_command(command: list[str]) -> tuple[int, str, str]:
    """Late import avoids the channel registry's legacy OpenCLI import cycle."""
    from backend.channels.opencli_channel import _run_opencli

    return await _run_opencli(command, os.environ.copy())


def _opencli_binary() -> str:
    from backend.channels.opencli_channel import _resolve_bin

    return _resolve_bin("direct")


def _parse_opencli_rows(stdout: str) -> list[dict[str, Any]]:
    from backend.channels.opencli_channel import _parse_json, _parse_yaml

    try:
        return _parse_json(stdout)
    except ValueError:
        return _parse_yaml(stdout)


@register_channel
class DoubaoResearchChannel(AbstractChannel):
    """One cited Doubao answer per pipeline run.

    OpenCLI owns login/session handling; this channel deliberately owns only
    prompt construction and evidence-shaped output for the normal pipeline.
    """

    channel_type = "doubao_research"
    capabilities = Capabilities(auth_kind="session", session_affinity=True, default_rate="6/min")

    async def collect(self, config: dict[str, Any], parameters: dict[str, Any]) -> ChannelResult:
        question = str(parameters.get("question") or config.get("question") or "").strip()
        if not question:
            return ChannelResult.fail("'question' is required for doubao_research channel")

        extract_citations = bool(config.get("extract_citations", True))
        # Prompt wording belongs to the research brief.  Appending a fixed
        # instruction made the browser adapter lose its active conversation;
        # extract URLs from the returned answer without altering the query.
        request = question
        command = [
            _opencli_binary(),
            "doubao",
            "ask",
            request,
            "-f",
            "json",
            "--site-session",
            str(config.get("site_session", "ephemeral")),
        ]
        try:
            returncode, stdout, stderr = await _run_doubao_command(command)
        except TimeoutError as exc:
            return ChannelResult.fail("Doubao request timed out", error_type=type(exc).__name__)
        except FileNotFoundError as exc:
            return ChannelResult.fail("opencli binary not found", error_type=type(exc).__name__)
        except Exception as exc:
            return ChannelResult.fail(
                f"Doubao request failed: {exc}", error_type=type(exc).__name__
            )

        if returncode:
            # Classify: captcha walls (human-cleared — never auto-retry) and
            # transient CDP races (retryable) get structured error_types so
            # the runner / thick fetch() can act on them; anything else stays
            # a generic failure instead of a permanent misclassification.
            if _is_captcha_block(stderr, stdout):
                error_type = "captcha_challenge"
            elif _is_transient_cdp_fault(stderr, stdout):
                error_type = "ConnectionError"
            else:
                error_type = None
            return ChannelResult.fail(
                f"opencli doubao ask exited with code {returncode}: {stderr[:500]}",
                error_type=error_type,
            )
        try:
            answer = _answer(_parse_opencli_rows(stdout))
        except Exception as exc:
            return ChannelResult.fail(
                f"Failed to parse Doubao answer: {exc}", error_type=type(exc).__name__
            )
        if not answer:
            return ChannelResult.fail("Doubao returned no assistant text")

        # Best-effort conversation URL: `doubao status -f json` exposes the
        # active chat id (https://www.doubao.com/chat/<id>).  This is a
        # read-only query against the same browser session; a failure here
        # must not fail the collect — the answer is already in hand.
        conversation_url = ""
        if config.get("capture_conversation_url", True):
            status_command = [
                _opencli_binary(),
                "doubao",
                "status",
                "-f",
                "json",
                "--site-session",
                str(config.get("site_session", "ephemeral")),
            ]
            try:
                rc, so, se = await _run_doubao_command(status_command)
                if rc == 0:
                    conversation_url = _conversation_url(so)
            except Exception:
                conversation_url = ""

        citations = _citations(answer) if extract_citations else []
        return ChannelResult.ok(
            [
                {
                    "title": question,
                    "content": answer,
                    "author": "doubao",
                    "question": question,
                    "conversation_url": conversation_url,
                    "citations": citations,
                    "citation_count": len(citations),
                    "citation_capture": (
                        "answer_url_extraction" if extract_citations else "disabled"
                    ),
                }
            ],
            citation_count=len(citations),
            citation_capture="answer_url_extraction" if extract_citations else "disabled",
        )

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        return (
            []
            if str(config.get("question") or "").strip()
            else ["'question' is required for doubao_research channel"]
        )

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        """Thick-contract entry point: migrate doubao onto the runner protocol
        (``type(chan).fetch is not AbstractChannel.fetch``) and own a bounded
        retry on TRANSIENT faults only.

        ``collect()`` stays the single source of truth for prompt construction
        and evidence output; this override adds the retry loop around it:
        - captcha walls (``captcha_challenge``) are never auto-retried — the
          pipeline's captcha branch (PR #65) pauses the source for a human
          instead;
        - non-retryable failures (``error_taxonomy.is_retryable`` False) fail
          immediately;
        - transient faults (CDP races classified as ``ConnectionError``,
          timeouts) retry with exponential backoff up to ``max_retries``
          (default 3, config key ``max_retries``; ``retry_base_delay``
          seconds, default 2).

        ``ctx.http`` is deliberately not threaded into ``collect()`` — the
        transport is a local opencli subprocess, not an HTTP request the
        runner's RateLimitedClient was built for (same trade-off documented in
        ``opencli_channel.fetch()``).
        """
        import asyncio

        from backend.pipeline.error_taxonomy import is_retryable

        # Local captcha marker (equals error_taxonomy.CAPTCHA_CHALLENGE, which
        # PR #65 exposes as is_captcha()) — kept inline so this PR merges
        # independently of #65.
        _CAPTCHA = "captcha_challenge"

        max_retries = int(ctx.config.get("max_retries", 3))
        base_delay = float(ctx.config.get("retry_base_delay", 2.0))
        last: ChannelResult | None = None
        for attempt in range(max_retries + 1):
            result = await self.collect(ctx.config, ctx.params)
            if result.success:
                return FetchResult(items=result.items, metadata=result.metadata)
            if result.error_type == _CAPTCHA or not is_retryable(result.error_type):
                raise ChannelFetchError(
                    result.error or "doubao collect failed",
                    error_type=result.error_type,
                )
            last = result
            if attempt < max_retries:
                await asyncio.sleep(base_delay * (2**attempt))
        raise ChannelFetchError(
            last.error or "doubao collect failed",
            error_type=last.error_type,
        )
