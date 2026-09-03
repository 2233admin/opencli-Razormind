"""Collect a cited Doubao research answer through the installed OpenCLI adapter."""

import asyncio
import json
import math
import os
import re
from typing import Any

import httpx

from backend.channels.base import (
    AbstractChannel,
    Capabilities,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel

_URL_RE = re.compile(r"https?://[^\s<>\]\[\](){}\"']+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?\uff0c\u3002\uff1b\uff1a\uff01\uff1f"
_CAPTCHA_MARKERS = (
    "verification challenge",
    "captcha",
    "blocked the request",
    "人机验证",
    "验证码",
)
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
    """Extract the active Doubao chat URL from status output."""
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
    text = f"{stderr} {stdout}".lower()
    return any(marker in text for marker in _CAPTCHA_MARKERS)


def _is_transient_cdp_fault(stderr: str, stdout: str) -> bool:
    """Return whether OpenCLI hit a retryable browser/CDP race."""
    text = f"{stderr} {stdout}"
    return any(marker in text for marker in _TRANSIENT_CDP_MARKERS)


def _structured_response(text: str) -> dict[str, Any]:
    """Decode the JSON response requested by the Doubao research prompt.

    Doubao may wrap the JSON in a markdown fence or add a short preamble. Keep
    the raw answer as the fallback so a formatting deviation never discards
    the research result.
    """
    raw = text.strip()
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            answer = parsed.get("answer") or parsed.get("content") or raw
            share_data = (
                parsed.get("session_share_data")
                or parsed.get("conversation_share_data")
                or parsed.get("share_data")
                or parsed.get("share_urls")
                or []
            )
            response_data = (
                parsed.get("data")
                or parsed.get("details")
                or parsed.get("answer_data")
                or parsed.get("result")
                or parsed.get("key_points")
                or []
            )
            links = (
                parsed.get("links")
                or parsed.get("references")
                or parsed.get("sources")
                or parsed.get("urls")
                or []
            )
            suggested = (
                parsed.get("suggested_keywords")
                or parsed.get("suggested_keys")
                or parsed.get("recommend_keywords")
                or parsed.get("recommended_keywords")
                or []
            )
            search_keywords = (
                parsed.get("search_keywords") or parsed.get("searched_keywords") or []
            )
            video_contents = parsed.get("video_contents") or parsed.get("videos") or []
            if not isinstance(share_data, (list, dict, str)):
                share_data = []
            if not isinstance(response_data, (list, dict, str)):
                response_data = []
            if not isinstance(links, (list, dict, str)):
                links = []
            if not isinstance(suggested, list):
                suggested = [suggested] if suggested else []
            if not isinstance(search_keywords, list):
                search_keywords = [search_keywords] if search_keywords else []
            if not isinstance(video_contents, list):
                video_contents = [video_contents] if video_contents else []
            return {
                "answer": str(answer).strip(),
                "data": response_data,
                "links": links,
                "response_data": parsed,
                "session_share_data": share_data,
                "suggested_keywords": [
                    str(item).strip() for item in suggested if str(item).strip()
                ],
                "search_keywords": [
                    str(item).strip() for item in search_keywords if str(item).strip()
                ],
                "video_contents": [
                    str(item).strip() for item in video_contents if str(item).strip()
                ],
                "raw_answer": raw,
            }
    return {
        "answer": raw,
        "data": [],
        "links": [],
        "response_data": {},
        "session_share_data": [],
        "suggested_keywords": [],
        "search_keywords": [],
        "video_contents": [],
        "raw_answer": raw,
    }


async def _run_doubao_command(command: list[str]) -> tuple[int, str, str]:
    """Late import avoids the channel registry's legacy OpenCLI import cycle."""
    bridge_url = str(os.getenv("DOUBAO_CLI_BRIDGE_URL") or "").strip()
    if bridge_url:
        try:
            async with httpx.AsyncClient(timeout=130, follow_redirects=False) as client:
                headers: dict[str, str] = {}
                bridge_token = str(os.getenv("DOUBAO_CLI_BRIDGE_TOKEN") or "").strip()
                if bridge_token:
                    headers["X-Lark-CLI-Bridge-Token"] = bridge_token
                response = await client.post(
                    bridge_url,
                    json={"command": command[2] if len(command) > 2 else "", "args": command[3:]},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            return (
                int(payload.get("returncode", 1)),
                str(payload.get("stdout", "")),
                str(payload.get("stderr", "")),
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return 1, "", f"Doubao CLI bridge failed: {exc}"

    from backend.channels.opencli_channel import _run_opencli

    return await _run_opencli(command)


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

    async def readiness_code(self, config: dict[str, Any] | None = None) -> str | None:
        """Return a machine-readable session readiness failure when available."""

        return None

    async def collect(self, config: dict[str, Any], parameters: dict[str, Any]) -> ChannelResult:
        question = str(parameters.get("question") or config.get("question") or "").strip()
        if not question:
            return ChannelResult.fail("'question' is required for doubao_research channel")

        extract_citations = bool(config.get("extract_citations", True))
        try:
            settle_seconds = float(config.get("settle_seconds", 0))
        except (TypeError, ValueError):
            return ChannelResult.fail("'settle_seconds' must be a non-negative number")
        if not math.isfinite(settle_seconds) or settle_seconds < 0:
            return ChannelResult.fail("'settle_seconds' must be a non-negative number")
        site_session = str(config.get("site_session", "ephemeral"))
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
            site_session,
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
        if settle_seconds:
            await asyncio.sleep(settle_seconds)
            read_command = [
                _opencli_binary(),
                "doubao",
                "read",
                "-f",
                "json",
                "--site-session",
                site_session,
            ]
            try:
                returncode, stdout, stderr = await _run_doubao_command(read_command)
            except Exception as exc:
                return ChannelResult.fail(
                    f"Doubao read failed: {exc}", error_type=type(exc).__name__
                )
            if returncode:
                return ChannelResult.fail(
                    f"opencli doubao read exited with code {returncode}: {stderr[:500]}",
                    error_type=(
                        "ConnectionError"
                        if _is_transient_cdp_fault(stderr, stdout)
                        else None
                    ),
                )
        try:
            response_rows = _parse_opencli_rows(stdout)
            answer = _answer(response_rows)
        except Exception as exc:
            return ChannelResult.fail(
                f"Failed to parse Doubao answer: {exc}", error_type=type(exc).__name__
            )
        if not answer:
            return ChannelResult.fail("Doubao returned no assistant text")

        structured = _structured_response(answer)
        content = structured["answer"]
        citations_text = " ".join(
            [
                structured["raw_answer"],
                json.dumps(structured["session_share_data"], ensure_ascii=False),
            ]
        )
        citations = _citations(citations_text) if extract_citations else []
        links = structured["links"] or citations
        response_data = structured["response_data"] or {
            "answer": content,
            "links": citations,
        }
        conversation_url = (
            _conversation_url(stdout)
            if config.get("capture_conversation_url", True)
            else ""
        )
        if config.get("capture_conversation_url", True):
            status_command = [
                _opencli_binary(),
                "doubao",
                "status",
                "-f",
                "json",
                "--site-session",
                site_session,
            ]
            try:
                returncode, status_stdout, _ = await _run_doubao_command(status_command)
                if returncode == 0:
                    conversation_url = _conversation_url(status_stdout) or conversation_url
            except Exception:
                pass
        return ChannelResult.ok(
            [
                {
                    "title": question,
                    "content": content,
                    "author": "doubao",
                    "question": question,
                    "conversation_url": conversation_url,
                    "answer": content,
                    "data": structured["data"],
                    "links": links,
                    "response_data": response_data,
                    "raw_answer": structured["raw_answer"],
                    "session_share_data": structured["session_share_data"],
                    "suggested_keywords": structured["suggested_keywords"],
                    "search_keywords": structured["search_keywords"],
                    "video_contents": structured["video_contents"],
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

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        """Run the subprocess collector with bounded retry for transient failures."""
        from backend.pipeline.error_taxonomy import is_retryable

        max_retries = int(ctx.config.get("max_retries", 3))
        base_delay = float(ctx.config.get("retry_base_delay", 2.0))
        last: ChannelResult | None = None
        for attempt in range(max_retries + 1):
            result = await self.collect(ctx.config, ctx.params)
            if result.success:
                return FetchResult(items=result.items, metadata=result.metadata)
            if result.error_type == "captcha_challenge" or not is_retryable(
                result.error_type
            ):
                raise ChannelFetchError(
                    result.error or "doubao collect failed",
                    error_type=result.error_type,
                )
            last = result
            if attempt < max_retries:
                await asyncio.sleep(base_delay * (2**attempt))
        assert last is not None
        raise ChannelFetchError(
            last.error or "doubao collect failed",
            error_type=last.error_type,
        )

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        return (
            []
            if str(config.get("question") or "").strip()
            else ["'question' is required for doubao_research channel"]
        )
