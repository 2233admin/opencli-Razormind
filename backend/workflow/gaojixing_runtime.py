"""Gaojixing live Doubao source contract for WorkflowProject runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import ws_agent_manager
from backend.channels.base import ChannelResult
from backend.channels.doubao_research_channel import (
    DoubaoResearchChannel,
    _citations,
    _structured_response,
)
from backend.config import get_settings
from backend.models.edge_node import EdgeNode
from backend.workflow.gaojixing_archive import write_precleanup_capture_receipt

GAOJIXING_CAPABILITY_ID = "chat-ai.capture"
GAOJIXING_CHANNEL_TYPE = "doubao_research"
GAOJIXING_LIVE_MODE = "live"
GAOJIXING_FIXTURE_MODE = "fixture"
GAOJIXING_MOCK_MODE = "mock"
GAOJIXING_EXECUTION_MODES = frozenset(
    {GAOJIXING_LIVE_MODE, GAOJIXING_FIXTURE_MODE, GAOJIXING_MOCK_MODE}
)
GAOJIXING_PACKAGE_SCHEMA = "gaojixing.question-package.v1"
GAOJIXING_EVIDENCE_SCHEMA = "gaojixing.capture-evidence.v1"


class GaojixingReadinessError(RuntimeError):
    """Typed fail-closed blocker for a live Gaojixing source."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class GaojixingQuestionPackage:
    schema: str
    question: str
    options: Mapping[str, Any]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "question": self.question,
            "options": _thaw_json(self.options),
            "digest": self.digest,
        }


def build_question_package(
    *,
    node_params: dict[str, Any],
    adapter_config: dict[str, Any],
    runtime_payload: dict[str, Any],
) -> GaojixingQuestionPackage:
    """Resolve the effective question once and hash its canonical snapshot."""

    question = (
        _string(runtime_payload.get("question"))
        or _string(runtime_payload.get("query"))
        or _string(node_params.get("question"))
    )
    if question is None:
        question = _string(adapter_config.get("question"))
    if question is None:
        raise GaojixingReadinessError(
            "gaojixing_question_required",
            (
                "A live Gaojixing run requires an effective question in run input, "
                "node params, or adapter config."
            ),
            details={"required": "question"},
        )

    option_keys = (
        "extract_citations",
        "capture_conversation_url",
        "site_session",
        "settle_seconds",
        "capabilityId",
        "sourceGroup",
        "executionMode",
        "agentRuntime",
        "agentUrl",
        "agentTimeout",
        "agentChrome",
    )
    options = {
        key: value
        for key in option_keys
        for value in [
            _json_safe(runtime_payload.get(key, node_params.get(key, adapter_config.get(key))))
        ]
        if value is not None
    }
    canonical = {"schema": GAOJIXING_PACKAGE_SCHEMA, "question": question, "options": options}
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return GaojixingQuestionPackage(
        schema=GAOJIXING_PACKAGE_SCHEMA,
        question=question,
        options=_freeze_json(options),
        digest=hashlib.sha256(encoded).hexdigest(),
    )


async def capture_live_doubao(
    *,
    package: GaojixingQuestionPackage,
    node_params: dict[str, Any],
    adapter_config: dict[str, Any],
    network_allowed: bool,
    external_mutation_allowed: bool = False,
    session: AsyncSession | None = None,
    workflow_id: str | None = None,
    run_id: str | None = None,
) -> ChannelResult:
    """Preflight and execute one live Doubao question.

    ``executionMode=agent`` is the workflow-native path: the control plane
    selects a connected local Browser Bridge (BBX) or native Agent runtime and
    the edge Agent operates the real browser. The default is BBX so the
    workflow and the noVNC browser profile are one execution boundary. The
    legacy OpenCLI Doubao channel remains available when no Agent mode is
    selected so old published workflows keep their behavior.
    """

    capability_id = (
        _string(node_params.get("capabilityId"))
        or _string(adapter_config.get("capabilityId"))
        or GAOJIXING_CAPABILITY_ID
    )
    if capability_id not in {GAOJIXING_CAPABILITY_ID, "doubao.ask"}:
        raise GaojixingReadinessError(
            "gaojixing_capability_missing",
            f'Live Gaojixing capability "{capability_id}" is not registered.',
            details={"capabilityId": capability_id},
        )
    if (
        node_params.get("capabilityAvailable") is False
        or adapter_config.get("capabilityAvailable") is False
    ):
        raise GaojixingReadinessError(
            "gaojixing_capability_missing",
            "The live Gaojixing chat-ai.capture/Doubao capability is unavailable.",
            details={"capabilityId": capability_id},
        )
    _raise_configured_readiness_blocker(adapter_config)
    if not network_allowed:
        raise GaojixingReadinessError(
            "gaojixing_network_denied",
            "Live Gaojixing capture requires workflow network permission.",
            details={"requiredPermission": "canFetchNetwork"},
        )

    execution_mode = _execution_mode(node_params, adapter_config)
    if execution_mode == "agent":
        if not external_mutation_allowed:
            raise GaojixingReadinessError(
                "gaojixing_external_write_permission_required",
                (
                    "Agent-mode Gaojixing capture sends a question to Doubao through a "
                    "browser and requires workflow canMutateExternalSites permission."
                ),
                details={"requiredPermission": "canMutateExternalSites"},
            )
        if session is None:
            raise GaojixingReadinessError(
                "gaojixing_agent_runtime_required",
                "Agent-mode Gaojixing capture requires a database session to select a local Agent.",
                details={"requiredRuntime": "bbx, codex, or claude-code"},
            )
        agent_config = {
            **adapter_config,
            **{
                key: node_params[key]
                for key in (
                    "agentRuntime",
                    "agentUrl",
                    "agentModel",
                    "agentTimeout",
                    "agentChrome",
                )
                if key in node_params
            },
        }
        return await _capture_live_doubao_via_agent(
            package=package,
            adapter_config=agent_config,
            session=session,
            workflow_id=workflow_id,
            run_id=run_id,
        )

    channel = DoubaoResearchChannel()
    healthy = await channel.health_check(adapter_config)
    if not healthy:
        readiness_code = await channel.readiness_code(adapter_config)
        raise GaojixingReadinessError(
            f"gaojixing_{readiness_code or 'session_unavailable'}",
            _readiness_message(readiness_code),
            details={"site": "doubao", "session": adapter_config.get("site_session", "persistent")},
        )

    config = {
        **adapter_config,
        "question": package.question,
        "site_session": adapter_config.get("site_session", "persistent"),
        "extract_citations": adapter_config.get("extract_citations", True),
        "capture_conversation_url": adapter_config.get("capture_conversation_url", True),
    }
    return await channel.collect(config, {"question": package.question})


async def _capture_live_doubao_via_agent(
    *,
    package: GaojixingQuestionPackage,
    adapter_config: dict[str, Any],
    session: AsyncSession,
    workflow_id: str | None,
    run_id: str | None,
) -> ChannelResult:
    """Ask one connected native Agent to perform the browser interaction.

    The center never launches Codex/Claude and never receives their provider
    credentials.  It only sends a bounded task over the authenticated reverse
    WebSocket; the local Agent owns the installed CLI and browser session.
    """
    agent_url, runtime = await _select_local_agent(session, adapter_config)
    timeout_seconds = _agent_timeout(adapter_config)
    chrome = adapter_config.get("agentChrome")
    config: dict[str, Any] = {
        "timeout_seconds": timeout_seconds,
        "permission_mode": "full_auto",
    }
    if isinstance(chrome, bool):
        config["chrome"] = chrome
    for key in (
        "settle_seconds",
        "response_timeout_seconds",
        "suggested_wait_seconds",
        "stable_observations",
        "delete_menu_timeout_seconds",
        "delete_verify_timeout_seconds",
        "delete_stable_observations",
    ):
        if key in adapter_config:
            config[key] = adapter_config[key]

    instructions = (
        "You are the browser execution worker for a workflow. This is an exact, "
        "bounded research task. Do not use a Doubao CLI, Doubao HTTP/API request, "
        "curl, requests, or any provider SDK. Use the locally installed BBX/Browser "
        "Bridge browser capability and the real logged-in browser session only. "
        "Open or reuse Doubao, submit "
        "the exact question, wait for the final answer, and inspect the visible page. "
        "If a CAPTCHA or human verification appears, do not bypass it: return the "
        "blocked JSON below immediately. Do not invent URLs or data. Return ONLY one "
        "JSON object, with no Markdown fence or commentary, using this shape: "
        '{"status":"completed","answer":"...","answer_complete":true,'
        '"conversation_deleted":true,"data":[],"links":[],"search_keywords":[],'
        '"search_keyword_count":0,"reference_count":0,"video_contents":[],'
        '"conversation_url":"https://www.doubao.com/chat/<id>",'
        '"session_share_data":[],"suggested_keywords":[]}. '
        'For a verification wall use {"status":"blocked","error_type":"captcha_challenge",'
        '"message":"human verification is required","answer":"",'
        '"answer_complete":false,"conversation_deleted":false,"data":[],"links":[],'
        '"search_keywords":[],"search_keyword_count":0,"reference_count":0,'
        '"video_contents":[],'
        '"conversation_url":"","session_share_data":[],"suggested_keywords":[]}. '
        "The conversation_url must be the actual current Doubao chat URL, if visible. "
        "links must contain only observed source URLs. suggested_keywords must contain "
        "the follow-up questions actually shown by Doubao. search_keywords and the two "
        "counts must contain only the visible search summary. video_contents must contain "
        "only visible video-card titles or descriptions."
    )
    task = {
        "runtime": runtime,
        "workflow": "workflow.gaojixing.doubao.browser",
        "instructions": instructions,
        "input": {
            "question": package.question,
            "message": (
                f"Research this exact question and capture the complete visible result:\n"
                f"{package.question}"
            )
        },
        "config": config,
        "session_id": None,
        "provider": None,
        "model": _string(adapter_config.get("agentModel")),
        "required_capabilities": list(_required_agent_capabilities(runtime)),
        "permissions": {
            "mode": "full_auto",
            "tool_scope": ["bbx.browser" if runtime == "bbx" else "opencli.browser"],
            "action_scope": ["doubao.ask", "doubao.read", "doubao.delete"],
            "workflow_id": workflow_id,
            "run_id": run_id,
        },
        "budget": {"timeout_seconds": timeout_seconds, "max_questions": 1},
        "evidence_requirements": [
            "answer",
            "links",
            "conversation_url",
            "suggested_keywords",
            "answer_complete",
            "conversation_deleted",
        ],
    }

    events: list[dict[str, Any]] = []
    durable_capture_receipt: dict[str, Any] | None = None

    async def on_event(event: dict[str, Any]) -> None:
        nonlocal durable_capture_receipt
        captured_event = deepcopy(event)
        evidence = captured_event.get("evidence")
        if (
            captured_event.get("type") == "evidence"
            and isinstance(evidence, dict)
            and evidence.get("kind") == "doubao.capture.pre_cleanup"
        ):
            if not run_id:
                raise RuntimeError(
                    "A workflow run id is required before Doubao conversation cleanup"
                )
            durable_capture_receipt = await asyncio.to_thread(
                write_precleanup_capture_receipt,
                Path(get_settings().gaojixing_run_storage_path),
                run_id=run_id,
                workflow_id=workflow_id,
                question=package.question,
                package_digest=package.digest,
                evidence=evidence,
            )
            evidence["durable_receipt"] = durable_capture_receipt
        events.append(captured_event)

    try:
        from backend.ws_agent_manager import send_agent_task

        terminal = await send_agent_task(
            agent_url,
            task,
            on_event,
            timeout=float(timeout_seconds),
        )
    except TimeoutError:
        return _agent_failure(
            "Local Agent browser task timed out",
            "TimeoutError",
            agent_url=agent_url,
            agent_runtime=runtime,
        )
    except (RuntimeError, OSError) as exc:
        return _agent_failure(
            f"Local Agent browser task failed: {exc}",
            type(exc).__name__,
            agent_url=agent_url,
            agent_runtime=runtime,
        )

    if terminal.get("type") == "error":
        message = str(terminal.get("message") or "Local Agent browser task failed")
        error_type = str(terminal.get("error_type") or "agent_runtime_failed")
        if _contains_captcha(message):
            error_type = "captcha_challenge"
        return _agent_failure(
            message,
            error_type,
            agent_url=agent_url,
            agent_runtime=runtime,
        )
    if terminal.get("type") != "done":
        return _agent_failure(
            "Local Agent returned no terminal result",
            "agent_runtime_failed",
            agent_url=agent_url,
            agent_runtime=runtime,
        )

    result = terminal.get("result")
    result = result if isinstance(result, dict) else {}
    text = _string(result.get("text")) or ""
    structured = _structured_response(text)
    response_data = structured.get("response_data")
    response_data = response_data if isinstance(response_data, dict) else {}
    if str(response_data.get("status") or "").lower() == "blocked":
        error_type = _string(response_data.get("error_type")) or "agent_runtime_blocked"
        if _contains_captcha(json.dumps(response_data, ensure_ascii=False)):
            error_type = "captcha_challenge"
        return _agent_failure(
            _string(response_data.get("message")) or "Local Agent reported a blocked browser task",
            error_type,
            agent_url=agent_url,
            agent_runtime=runtime,
        )
    answer = _string(structured.get("answer"))
    if not answer:
        return _agent_failure(
            "Local Agent returned no Doubao answer",
            "gaojixing_answer_missing",
            agent_url=agent_url,
            agent_runtime=runtime,
        )
    if response_data.get("answer_complete") is not True:
        return _agent_failure(
            "Local Agent did not confirm that the Doubao answer was complete",
            "doubao_response_incomplete",
            agent_url=agent_url,
            agent_runtime=runtime,
        )
    if (
        not isinstance(durable_capture_receipt, dict)
        or durable_capture_receipt.get("persisted") is not True
    ):
        return _agent_failure(
            "Doubao evidence was not durably persisted before conversation cleanup",
            "doubao_capture_persistence_missing",
            agent_url=agent_url,
            agent_runtime=runtime,
        )
    if response_data.get("conversation_deleted") is not True:
        return _agent_failure(
            "Doubao conversation deletion was not confirmed; collection stopped",
            "doubao_conversation_cleanup_failed",
            agent_url=agent_url,
            agent_runtime=runtime,
        )

    conversation_url = _conversation_url_from_response(response_data)
    share_data = structured.get("session_share_data")
    if not share_data and conversation_url:
        share_data = {"url": conversation_url, "type": "conversation"}
    citations = [
        item for item in _citations(answer) if _is_external_evidence_url(item.get("url"))
    ]
    links = _normalize_links(structured.get("links"))
    if links:
        citations = _merge_links(citations, links)
    normalized_links = links or citations
    item = {
        "title": package.question,
        "content": answer,
        "author": "doubao",
        "question": package.question,
        "conversation_url": conversation_url,
        "answer": answer,
        "answer_complete": True,
        "conversation_deleted": True,
        "data": structured.get("data", []),
        "links": normalized_links,
        "response_data": response_data,
        "raw_answer": text,
        "session_share_data": share_data or [],
        "suggested_keywords": structured.get("suggested_keywords", []),
        "search_keywords": structured.get("search_keywords", []),
        "video_contents": structured.get("video_contents", []),
        "search_keyword_count": response_data.get("search_keyword_count"),
        "reference_count": response_data.get("reference_count"),
        "citations": citations,
        "citation_count": len(citations),
        "citation_capture": "agent_browser_observation",
        "provenance": f"agent:{runtime}:browser:{'bbx' if runtime == 'bbx' else 'opencli'}",
        "agent_runtime": runtime,
        "agent_url": agent_url,
        "capture_receipt": durable_capture_receipt,
    }
    return ChannelResult.ok(
        [item],
        citation_count=len(citations),
        citation_capture="agent_browser_observation",
        agent_url=agent_url,
        agent_runtime=runtime,
        runtime_event_count=len(events),
    )


async def _select_local_agent(
    session: AsyncSession,
    adapter_config: dict[str, Any],
) -> tuple[str, str]:
    preferred_runtime = _string(adapter_config.get("agentRuntime"))
    preferred_runtimes = (
        [preferred_runtime]
        if preferred_runtime
        else ["bbx", "codex", "claude-code"]
    )
    preferred_url = _string(adapter_config.get("agentUrl"))
    nodes = list(
        (
            await session.execute(
                select(EdgeNode).where(
                    EdgeNode.protocol == "ws",
                    EdgeNode.status == "online",
                )
            )
        )
        .scalars()
        .all()
    )
    connected = set(ws_agent_manager.list_connected())
    candidates: list[tuple[tuple[int, int], tuple[int, int], str, str]] = []
    required = set().union(
        *[set(_required_agent_capabilities(runtime)) for runtime in preferred_runtimes]
    )
    for node in nodes:
        if node.url not in connected or (preferred_url and node.url != preferred_url):
            continue
        manifests = node.runtime_capabilities
        if not isinstance(manifests, dict):
            continue
        for runtime_index, runtime in enumerate(preferred_runtimes):
            capabilities = manifests.get(runtime)
            if not isinstance(capabilities, list) or not required.issubset(
                {value for value in capabilities if isinstance(value, str)}
            ):
                continue
            url_rank = 0 if preferred_url and node.url == preferred_url else 1
            candidates.append(((url_rank, runtime_index), (0, runtime_index), node.url, runtime))
    if not candidates:
        raise GaojixingReadinessError(
            "gaojixing_agent_runtime_unavailable",
            "No connected local Agent advertises the requested BBX/Codex/Claude Code runtime.",
            details={
                "preferredRuntimes": preferred_runtimes,
                "preferredAgentUrl": preferred_url,
                "requiredCapabilities": sorted(required),
            },
        )
    _, _, agent_url, runtime = min(candidates, key=lambda item: item[:2] + item[2:])
    return agent_url, runtime


def _required_agent_capabilities(runtime: str) -> tuple[str, ...]:
    """Capabilities required by the browser workflow for one runtime."""
    if runtime == "bbx":
        return ("browser", "tool_events")
    return ("streaming", "tool_events")


def _execution_mode(node_params: dict[str, Any], adapter_config: dict[str, Any]) -> str:
    value = _string(node_params.get("executionMode")) or _string(
        adapter_config.get("executionMode")
    )
    return "agent" if value in {"agent", "local_agent", "native_agent"} else "channel"


def _agent_timeout(adapter_config: dict[str, Any]) -> int:
    value = adapter_config.get("agentTimeout", 900)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 900
    return max(30, min(int(value), 3600))


def _conversation_url_from_response(response: dict[str, Any]) -> str:
    for key in ("conversation_url", "conversationUrl", "session_share_url", "share_url"):
        value = _string(response.get(key))
        if value and "doubao.com/chat/" in value:
            return value
    for key in ("session_share_data", "conversation_share_data", "share_data"):
        value = response.get(key)
        for url in _urls_in_value(value):
            if "doubao.com/chat/" in url:
                return url
    return ""


def _normalize_links(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        value = [value] if isinstance(value, str) else []
    links: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            url = _string(item.get("url") or item.get("href"))
            if url and _is_external_evidence_url(url):
                links.append({**item, "url": url})
        elif (
            isinstance(item, str)
            and item.strip().startswith(("http://", "https://"))
            and _is_external_evidence_url(item.strip())
        ):
            links.append({"url": item.strip()})
    return _merge_links([], links)


def _merge_links(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*first, *second]:
        url = _string(item.get("url"))
        if not url or not _is_external_evidence_url(url) or url in seen:
            continue
        seen.add(url)
        merged.append(item)
    return merged


def _is_external_evidence_url(value: Any) -> bool:
    url = _string(value)
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    return bool(hostname) and hostname not in {"doubao.com", "www.doubao.com"}


def _urls_in_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item["url"] for item in _citations(value)]
    if isinstance(value, dict):
        return [url for child in value.values() for url in _urls_in_value(child)]
    if isinstance(value, list):
        return [url for child in value for url in _urls_in_value(child)]
    return []


def _contains_captcha(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("captcha", "verification", "验证码", "人机验证"))


def _agent_failure(error: str, error_type: str, **metadata: Any) -> ChannelResult:
    return ChannelResult(
        success=False,
        error=error,
        error_type=error_type,
        metadata=metadata,
    )


def map_capture_item(
    item: dict[str, Any],
    *,
    package: GaojixingQuestionPackage,
    workflow_id: str,
    run_id: str,
    node_id: str,
    artifact_id: str,
    mode: str = GAOJIXING_LIVE_MODE,
    provenance: str | None = None,
) -> dict[str, Any]:
    """Attach separate answer/citation/conversation evidence to one raw item."""
    if mode not in GAOJIXING_EXECUTION_MODES:
        raise ValueError(f"Unsupported Gaojixing execution mode: {mode}")
    provenance = provenance or (
        "opencli:doubao" if mode == GAOJIXING_LIVE_MODE else f"{mode}:unspecified"
    )

    answer = _string(item.get("content"))
    citations = item.get("citations") if isinstance(item.get("citations"), list) else []
    conversation_url = _string(item.get("conversation_url"))
    evidence = {
        "schema": GAOJIXING_EVIDENCE_SCHEMA,
        "mode": mode,
        "provenance": provenance,
        "packageDigest": package.digest,
        "runId": run_id,
        "workflowId": workflow_id,
        "nodeId": node_id,
        "answer": {
            "status": "captured" if answer else "unavailable",
            "artifactId": artifact_id,
            "text": answer,
        },
        "citations": {
            "status": "captured" if citations else "empty",
            "capture": item.get("citation_capture", "answer_url_extraction"),
            "verified": False,
            "items": citations,
        },
        "conversation": {
            "status": "captured" if conversation_url else "unknown",
            "url": conversation_url,
        },
    }
    mapped = {
        **item,
        "gaojixing": {
            "mode": mode,
            "provenance": provenance,
            "capabilityId": GAOJIXING_CAPABILITY_ID,
            "package": package.to_dict(),
            "artifactId": artifact_id,
            "evidence": evidence,
        },
        "packageDigest": package.digest,
        "questionPackage": package.to_dict(),
        "mode": mode,
        "provenance": provenance,
        "answerArtifactId": artifact_id,
    }
    if conversation_url:
        mapped["dedupe"] = {
            "type": "source-identity",
            "field": "conversation_url",
            "identity": conversation_url,
            "value": conversation_url,
            "status": "unique",
        }
    return mapped


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return str(value)


def _raise_configured_readiness_blocker(adapter_config: dict[str, Any]) -> None:
    if adapter_config.get("adapterAvailable") is False:
        raise GaojixingReadinessError(
            "gaojixing_adapter_missing",
            "The Doubao OpenCLI adapter is unavailable.",
            details={"requiredAdapter": "opencli:doubao"},
        )
    if (
        adapter_config.get("authenticated") is False
        or adapter_config.get("authenticationAvailable") is False
    ):
        raise GaojixingReadinessError(
            "gaojixing_authentication_required",
            "The configured Doubao session is not authenticated.",
            details={"site": "doubao"},
        )
    if adapter_config.get("sessionAvailable") is False:
        raise GaojixingReadinessError(
            "gaojixing_session_unavailable",
            "The configured Doubao OpenCLI session is unavailable.",
            details={"site": "doubao", "session": adapter_config.get("site_session", "persistent")},
        )


def _readiness_message(code: str | None) -> str:
    messages = {
        "adapter_missing": "The Doubao OpenCLI adapter is unavailable.",
        "authentication_required": "The configured Doubao session is not authenticated.",
        "captcha_challenge": "The Doubao session is blocked by a CAPTCHA challenge.",
    }
    return messages.get(
        code or "",
        "The Doubao OpenCLI session is unavailable or not logged in.",
    )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return deepcopy(value)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return deepcopy(value)


__all__ = [
    "GAOJIXING_CAPABILITY_ID",
    "GAOJIXING_CHANNEL_TYPE",
    "GAOJIXING_EVIDENCE_SCHEMA",
    "GAOJIXING_LIVE_MODE",
    "GAOJIXING_EXECUTION_MODES",
    "GAOJIXING_FIXTURE_MODE",
    "GaojixingQuestionPackage",
    "GaojixingReadinessError",
    "build_question_package",
    "capture_live_doubao",
    "GAOJIXING_MOCK_MODE",
    "map_capture_item",
]
