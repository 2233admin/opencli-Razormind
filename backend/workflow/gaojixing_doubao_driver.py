# ruff: noqa: E501 -- embedded, audited DOM scripts remain readable as JavaScript
"""Certified OpenCLI + Playwright adapter for Gaojixing Doubao evidence.

The adapter owns the browser-side mutation boundary: ``collect`` is the only
method allowed to create a conversation and submit a question.  Recovery uses
``inspect_current`` and is therefore read-only.  Every capture is written to a
unique attempt directory; the durable collection runner decides which capture
is accepted under its fencing lease.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

_FORMAL_CHAT_URL = re.compile(r"^https://www\.doubao\.com/chat/\d+$")
_PLACEHOLDER_ANSWERS = {
    "已生成代码",
    "已生成图片",
    "已生成视频",
    "已生成音频",
    "已生成文件",
    "思考中",
    "生成中",
    "加载中",
    "正在生成",
    "正在思考",
    "稍等片刻",
    "计算中",
    "正在计算",
    "正在进行计算",
    "正在为您计算",
    "正在查询",
}
_MODULE_NAMES = (
    "keywords",
    "ref_links",
    "product_links",
    "video_links",
    "followups",
)
_DOUBAO_SHARE_URL = re.compile(
    r"^https://www\.doubao\.com/thread/[A-Za-z0-9_-]+(?:[?#].*)?$"
)
CommandRunner = Callable[
    [list[str], str], Awaitable[tuple[int, list[dict[str, Any]], str]]
]
PageCapture = Callable[..., Awaitable[dict[str, Any] | None] | dict[str, Any] | None]
EndpointLease = Callable[[], AbstractAsyncContextManager[str]]
TargetLister = Callable[[str], Awaitable[list[dict[str, Any]]]]

_OPENCLI_RUNTIME_PADDING_SECONDS = 30
_SUBPROCESS_SHUTDOWN_GRACE_SECONDS = 5
_TARGET_STATE_VERSION = 2


class DoubaoDriverUnavailableError(RuntimeError):
    """Stable fail-closed error raised when the certified driver cannot proceed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OpenCLIDoubaoEvidenceDriver:
    """Submit once through OpenCLI and capture strict page evidence via CDP."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        endpoint_lease: EndpointLease | None = None,
        command_runner: CommandRunner | None = None,
        page_capture: PageCapture | None = None,
        target_lister: TargetLister | None = None,
        target_state_root: str | Path | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise DoubaoDriverUnavailableError("project-root-unavailable")
        self._project_root = root
        self._endpoint_lease = endpoint_lease or _default_endpoint_lease
        self._command_runner = command_runner or _default_command_runner
        self._page_capture = page_capture or _capture_page_evidence
        self._target_lister = target_lister or _list_cdp_page_targets
        state_root = Path(target_state_root).resolve() if target_state_root else root
        if not state_root.is_dir():
            raise DoubaoDriverUnavailableError("target-state-root-unavailable")
        self._target_state_root = state_root

    async def preflight(self) -> None:
        """Verify the logged-in Doubao session without creating or submitting."""

        async with self._endpoint_lease() as endpoint:
            code, rows, _stderr = await self._command_runner(
                _status_command(), endpoint
            )
            if code:
                raise DoubaoDriverUnavailableError("doubao-status-failed")
            if not _chat_url(rows, allow_non_chat=True):
                raise DoubaoDriverUnavailableError("doubao-session-unavailable")

    async def delete_conversation(self, *, chat_url: str) -> bool:
        """Delete one saved, fully captured conversation from the visible sidebar."""

        if not _FORMAL_CHAT_URL.fullmatch(chat_url):
            return False
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise DoubaoDriverUnavailableError("playwright-unavailable") from exc

        async with self._endpoint_lease() as endpoint:
            pw = await async_playwright().start()
            browser = None
            page = None
            try:
                browser = await pw.chromium.connect_over_cdp(endpoint)
                context = browser.contexts[0] if browser.contexts else None
                if context is None:
                    return False
                page = await context.new_page()
                await page.goto(chat_url, wait_until="domcontentloaded", timeout=30_000)
                id_match = _FORMAL_CHAT_URL.fullmatch(chat_url)
                if id_match is None:
                    return False
                conversation_id = chat_url.rsplit("/", 1)[-1]
                row = page.locator(
                    f'a[data-testid="chat_list_thread_item"], '
                    f'#conversation_{conversation_id}, '
                    f'a[href="/chat/{conversation_id}"]'
                )
                await row.wait_for(state="attached", timeout=10_000)
                if await row.count() != 1:
                    return False
                await row.hover()
                menu = row.locator('button[data-slot="dropdown-menu-trigger"]')
                if await menu.count() != 1:
                    return False
                await menu.click()
                await page.wait_for_timeout(200)
                selected = await page.evaluate(_CONFIRM_DELETE_CONVERSATION_JS, "menu")
                if not isinstance(selected, dict) or selected.get("stage") != "menu":
                    return False
                await page.wait_for_timeout(200)
                confirmed = await page.evaluate(_CONFIRM_DELETE_CONVERSATION_JS, "confirm")
                return isinstance(confirmed, dict) and confirmed.get("stage") == "confirmed"
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                await pw.stop()

    async def collect(self, *, question_id: str, question: str) -> dict[str, Any]:
        """Create one conversation, submit ``question`` exactly once, and capture."""

        if not question_id or not question.strip():
            raise DoubaoDriverUnavailableError("question-invalid")
        async with self._endpoint_lease() as endpoint:
            before_targets = await self._target_lister(endpoint)
            starting_target = _select_starting_doubao_target(before_targets)
            new_code, _new_rows, _new_stderr = await self._command_runner(
                _new_command(), _target_websocket_url(starting_target)
            )
            if new_code:
                raise DoubaoDriverUnavailableError("doubao-new-failed")

            after_targets = await self._target_lister(endpoint)
            owned_target = _resolve_owned_doubao_target(
                before_targets=before_targets,
                after_targets=after_targets,
                starting_target=starting_target,
            )
            target_endpoint = _target_websocket_url(owned_target)
            _write_target_state(
                self._target_state_root,
                question_id=question_id,
                question=question,
                target_id=_target_id(owned_target),
            )

            ask_code, _ask_rows, _ask_stderr = await self._command_runner(
                _ask_command(question), target_endpoint
            )
            if ask_code:
                # Never retry here: the browser may have accepted the question
                # even if the CLI response failed. A verified challenge is the
                # one exception: capture its on-page marker and let the
                # governed worker notify Hermes/Feishu rather than disguising
                # it as an unexplained reconciliation failure.
                if _verification_challenge(_ask_stderr):
                    status_code, status_rows, _status_stderr = (
                        await self._command_runner(_status_command(), target_endpoint)
                    )
                    challenge_url = (
                        _chat_url(status_rows, allow_non_chat=True)
                        if status_code == 0
                        else None
                    )
                    if challenge_url is not None:
                        challenge_capture = await _maybe_await(
                            self._page_capture(
                                endpoint=endpoint,
                                project_root=self._project_root,
                                question_id=question_id,
                                question=question,
                                answer="",
                                chat_url=challenge_url,
                                allow_submit=False,
                            )
                        )
                        if (
                            isinstance(challenge_capture, dict)
                            and challenge_capture.get("status")
                            == "verification_required"
                        ):
                            return challenge_capture
                raise DoubaoDriverUnavailableError("doubao-ask-failed")
            # After a successful ask the conversation URL can take a moment
            # to settle; a transient status read (G0037/G0038 regression:
            # formal-chat-url-missing) must not discard an already-submitted
            # question.  Retry the read-only status query briefly before
            # failing closed -- this never resubmits the question.
            chat_url = None
            status_code = 1
            for _attempt in range(4):
                status_code, status_rows, _status_stderr = await self._command_runner(
                    _status_command(), target_endpoint
                )
                if not status_code:
                    chat_url = _chat_url(status_rows)
                    if chat_url is not None:
                        break
                await asyncio.sleep(1.5)
            if status_code:
                raise DoubaoDriverUnavailableError("doubao-status-failed")
            if chat_url is None:
                raise DoubaoDriverUnavailableError("formal-chat-url-missing")
            _write_target_state(
                self._target_state_root,
                question_id=question_id,
                question=question,
                target_id=_target_id(owned_target),
                conversation_url=chat_url,
            )
            capture = await _maybe_await(
                self._page_capture(
                    endpoint=endpoint,
                    project_root=self._project_root,
                    question_id=question_id,
                    question=question,
                    # The OpenCLI command confirms the click/submission only.
                    # Page capture must derive the answer from the exact matched
                    # Doubao turn, otherwise a stale CLI response can reject a
                    # valid visible answer or persist the wrong text.
                    answer="",
                    chat_url=chat_url,
                    allow_submit=False,
                )
            )
            if capture is None:
                raise DoubaoDriverUnavailableError("page-question-not-proven")
            return capture

    async def inspect_current(
        self, *, question_id: str, question: str
    ) -> dict[str, Any] | None:
        """Read the current conversation; never create a chat or submit a question."""

        async with self._endpoint_lease() as endpoint:
            target_state = _read_target_state(
                self._target_state_root,
                question_id=question_id,
                question=question,
            )
            if target_state is None:
                return None
            target_id, expected_chat_url = target_state
            targets = await self._target_lister(endpoint)
            matches = [row for row in targets if _target_id(row) == target_id]
            if (
                len(matches) != 1
                or str(matches[0].get("url") or "").strip() != expected_chat_url
            ):
                return None
            target_endpoint = _target_websocket_url(matches[0])
            status_code, status_rows, _stderr = await self._command_runner(
                _status_command(), target_endpoint
            )
            if status_code:
                return None
            chat_url = _chat_url(status_rows)
            if chat_url != expected_chat_url:
                return None
            return await _maybe_await(
                self._page_capture(
                    endpoint=endpoint,
                    project_root=self._project_root,
                    question_id=question_id,
                    question=question,
                    answer="",
                    chat_url=chat_url,
                    allow_submit=False,
                )
            )


def build_opencli_doubao_evidence_driver(
    *, project_root: str | Path, target_state_root: str | Path | None = None
) -> OpenCLIDoubaoEvidenceDriver:
    """Compose the production driver used by local and Celery workers."""

    return OpenCLIDoubaoEvidenceDriver(
        project_root=project_root,
        target_state_root=target_state_root,
    )


def _new_command() -> list[str]:
    return ["doubao", "new", "--site-session", "persistent", "-f", "json"]


def _ask_command(question: str) -> list[str]:
    return [
        "doubao",
        "ask",
        question,
        "--site-session",
        "persistent",
        "--timeout",
        "150",
        "-f",
        "json",
    ]


def _status_command() -> list[str]:
    return ["doubao", "status", "--site-session", "persistent", "-f", "json"]


@asynccontextmanager
async def _default_endpoint_lease() -> AsyncIterator[str]:
    from backend.browser_pool import get_pool
    from backend.config import get_settings

    try:
        pool = get_pool()
    except RuntimeError as exc:
        raise DoubaoDriverUnavailableError("browser-pool-unavailable") from exc
    # The certified Gaojixing session is intentionally pinned to the configured
    # authenticated CDP endpoint. An unrelated registered edge node may be
    # offline or logged into another account and must not win an unrouted race.
    configured_endpoint = get_settings().opencli_cdp_endpoint.strip() or None
    if configured_endpoint is None or configured_endpoint not in pool.endpoints:
        raise DoubaoDriverUnavailableError("certified-endpoint-unavailable")
    async with pool.acquire(endpoint=configured_endpoint) as endpoint:
        yield endpoint


async def _default_command_runner(
    command: list[str], endpoint: str
) -> tuple[int, list[dict[str, Any]], str]:
    from backend.config import get_settings
    from backend.opencli_runtime import resolve_opencli_bin

    if get_settings().collection_mode != "local":
        raise DoubaoDriverUnavailableError("doubao-local-runtime-required")
    env = os.environ.copy()
    # OpenCLI 0.9.x uses the CDP endpoint. Obsolete daemon variables override
    # this value in older deployments, so the certified adapter removes them.
    env.pop("OPENCLI_DAEMON_HOST", None)
    env.pop("OPENCLI_DAEMON_PORT", None)
    env["OPENCLI_CDP_ENDPOINT"] = endpoint
    try:
        process = await asyncio.create_subprocess_exec(
            resolve_opencli_bin(),
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=_command_deadline_seconds(
                command, configured_timeout=get_settings().opencli_timeout
            ),
        )
    except FileNotFoundError as exc:
        raise DoubaoDriverUnavailableError("opencli-runtime-unavailable") from exc
    except TimeoutError as exc:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        raise DoubaoDriverUnavailableError("opencli-runtime-timeout") from exc

    code = process.returncode
    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode().strip()
    rows: list[dict[str, Any]] = []
    if stdout.strip():
        try:
            json_start = next(
                (index for index, char in enumerate(stdout) if char in "[{"),
                None,
            )
            if json_start is None:
                raise ValueError("opencli returned no JSON")
            parsed = json.loads(stdout[json_start:])
            rows = parsed if isinstance(parsed, list) else [parsed]
            rows = [item for item in rows if isinstance(item, dict)]
        except (ValueError, TypeError, json.JSONDecodeError):
            raise DoubaoDriverUnavailableError("opencli-json-invalid")
    return code, rows, stderr


def _command_deadline_seconds(
    command: list[str], *, configured_timeout: int | float
) -> float:
    """Keep the parent deadline beyond OpenCLI's own command timeout and padding."""

    command_timeout = 0.0
    try:
        timeout_index = command.index("--timeout")
        command_timeout = float(command[timeout_index + 1])
    except (ValueError, IndexError, TypeError):
        pass
    return max(
        float(configured_timeout),
        command_timeout
        + _OPENCLI_RUNTIME_PADDING_SECONDS
        + _SUBPROCESS_SHUTDOWN_GRACE_SECONDS,
    )


async def _list_cdp_page_targets(endpoint: str) -> list[dict[str, Any]]:
    """Read page targets from the leased HTTP CDP endpoint."""

    if not endpoint.startswith(("http://", "https://")):
        raise DoubaoDriverUnavailableError("cdp-http-endpoint-required")

    def read() -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"{endpoint.rstrip('/')}/json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
        if not isinstance(payload, list):
            raise ValueError("CDP target response is not a list")
        return [row for row in payload if isinstance(row, dict)]

    try:
        return await asyncio.to_thread(read)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DoubaoDriverUnavailableError("cdp-targets-unavailable") from exc


def _doubao_page_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for row in targets:
        url = str(row.get("url") or "").strip()
        parsed_url = urlsplit(url)
        if (
            row.get("type") != "page"
            or parsed_url.scheme != "https"
            or parsed_url.hostname != "www.doubao.com"
        ):
            continue
        if not _target_id(row) or not str(row.get("webSocketDebuggerUrl") or "").startswith(
            ("ws://", "wss://")
        ):
            continue
        candidates.append(row)
    return candidates


def _select_starting_doubao_target(
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = _doubao_page_targets(targets)
    if not candidates:
        raise DoubaoDriverUnavailableError("doubao-target-missing")

    def score(row: dict[str, Any]) -> int:
        url = str(row.get("url") or "")
        if "/chat/local_" in url or url.rstrip("/").endswith("/chat"):
            return 2
        return 1

    best_score = max(score(row) for row in candidates)
    best = [row for row in candidates if score(row) == best_score]
    # Preserve the CDP target order OpenCLI would have used, then pin that one
    # exact page for every command. Multiple existing tabs are safe once the
    # chosen target cannot drift between subprocesses.
    return best[0]


def _resolve_owned_doubao_target(
    *,
    before_targets: list[dict[str, Any]],
    after_targets: list[dict[str, Any]],
    starting_target: dict[str, Any],
) -> dict[str, Any]:
    """Identify only the page causally owned by this exact ``doubao new`` call."""

    before_ids = {_target_id(row) for row in _doubao_page_targets(before_targets)}
    after = _doubao_page_targets(after_targets)
    created = [row for row in after if _target_id(row) not in before_ids]
    if created:
        # The lease serializes managed work, but it cannot exclude a human or
        # another process using the browser. Only a single new Doubao target
        # whose CDP opener is the exact starting page is causally attributable
        # to this ``new`` call.
        if (
            len(created) != 1
            or str(created[0].get("openerId") or "").strip()
            != _target_id(starting_target)
        ):
            raise DoubaoDriverUnavailableError("doubao-target-ambiguous")
        return created[0]

    starting_id = _target_id(starting_target)
    retained = [row for row in after if _target_id(row) == starting_id]
    if len(retained) != 1:
        raise DoubaoDriverUnavailableError("doubao-target-lost")
    return retained[0]


def _target_id(target: dict[str, Any]) -> str:
    return str(target.get("id") or "").strip()


def _target_websocket_url(target: dict[str, Any]) -> str:
    value = str(target.get("webSocketDebuggerUrl") or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"ws", "wss"}
        or not parsed.netloc
        or not parsed.path.endswith(f"/{_target_id(target)}")
    ):
        raise DoubaoDriverUnavailableError("doubao-target-websocket-missing")
    return value


def _target_state_path(root: Path, question_id: str) -> Path:
    key = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    return root / "logs" / "doubao-targets" / f"{key}.json"


def _write_target_state(
    root: Path,
    *,
    question_id: str,
    question: str,
    target_id: str,
    conversation_url: str | None = None,
) -> None:
    if conversation_url is not None and not _FORMAL_CHAT_URL.fullmatch(conversation_url):
        raise DoubaoDriverUnavailableError("formal-chat-url-missing")
    state_path = _target_state_path(root, question_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_suffix(f".{uuid4().hex}.tmp")
    payload = {
        "version": _TARGET_STATE_VERSION,
        "questionId": question_id,
        "questionSha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "targetId": target_id,
        "phase": "submitted-formal" if conversation_url else "target-owned",
        "conversationUrl": conversation_url,
    }
    temporary_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, state_path)


def _read_target_state(
    root: Path, *, question_id: str, question: str
) -> tuple[str, str] | None:
    state_path = _target_state_path(root, question_id)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    expected_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _TARGET_STATE_VERSION
        or payload.get("questionId") != question_id
        or payload.get("questionSha256") != expected_hash
        or payload.get("phase") != "submitted-formal"
    ):
        return None
    target_id = str(payload.get("targetId") or "").strip()
    conversation_url = str(payload.get("conversationUrl") or "").strip()
    if not target_id or not _FORMAL_CHAT_URL.fullmatch(conversation_url):
        return None
    return target_id, conversation_url


def _chat_url(
    rows: list[dict[str, Any]], *, allow_non_chat: bool = False
) -> str | None:
    for row in rows:
        for key in ("Url", "URL", "url"):
            value = str(row.get(key) or "").strip()
            if _FORMAL_CHAT_URL.fullmatch(value):
                return value
            if allow_non_chat and value.startswith("https://www.doubao.com"):
                return value
    return None


def _verification_challenge(stderr: str) -> bool:
    """Recognize an OpenCLI-reported page challenge, never a generic failure."""

    return bool(
        re.search(
            r"(?:captcha|verification\s+challenge|登录验证|访问异常)",
            stderr or "",
            flags=re.IGNORECASE,
        )
    )


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


async def _capture_page_evidence(
    *,
    endpoint: str,
    project_root: Path,
    question_id: str,
    question: str,
    answer: str,
    chat_url: str,
    allow_submit: bool,
) -> dict[str, Any] | None:
    if allow_submit:
        raise DoubaoDriverUnavailableError("page-capture-submit-forbidden")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise DoubaoDriverUnavailableError("playwright-unavailable") from exc

    attempt_token = uuid4().hex
    attempt_root = project_root / "screenshots" / question_id / f"attempt-{attempt_token}"
    attempt_root.mkdir(parents=True, exist_ok=False)
    pw = await async_playwright().start()
    browser = None
    page = None
    try:
        browser = await pw.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else None
        if context is None:
            raise DoubaoDriverUnavailableError("doubao-browser-context-missing")
        page = next(
            (candidate for candidate in context.pages if candidate.url == chat_url),
            None,
        )
        if page is None:
            page = await context.new_page()
            await page.goto(chat_url, wait_until="domcontentloaded", timeout=30_000)
        baseline = await page.evaluate(_BASELINE_PAGE_JS)
        baseline = baseline if isinstance(baseline, dict) else {}
        exception_kind = _page_exception_kind(baseline)
        if exception_kind:
            artifact = await _save_verification_screenshot(
                page, attempt_root, project_root, exception_kind
            )
            return {
                "id": question_id,
                "question": question,
                "status": "verification_required",
                "verification": {
                    "kind": exception_kind,
                    "pageMarkerDetected": True,
                    "screenshotPath": artifact,
                },
            }
        target = await _wait_for_stable_target_turn(
            page,
            question=question,
            token=attempt_token,
        )
        if target is None:
            return None
        if target.get("status") == "verification_required":
            exception_kind = str(target.get("exception_kind") or "")
            if exception_kind not in {"captcha", "login", "access"}:
                raise DoubaoDriverUnavailableError("verification-kind-invalid")
            artifact = await _save_verification_screenshot(
                page, attempt_root, project_root, exception_kind
            )
            return {
                "id": question_id,
                "question": question,
                "status": "verification_required",
                "verification": {
                    "kind": exception_kind,
                    "pageMarkerDetected": True,
                    "screenshotPath": artifact,
                },
            }
        final_answer = str(target.get("answer") or "").strip()
        if _placeholder_answer(final_answer):
            raise DoubaoDriverUnavailableError("full-answer-missing")
        if (
            answer.strip()
            and not _placeholder_answer(answer)
            and not _answers_match(answer, final_answer)
        ):
            raise DoubaoDriverUnavailableError("opencli-page-answer-mismatch")

        await _expand_references(page, target, attempt_token)
        snapshot = await _wait_for_stable_module_snapshot(page, attempt_token)
        if snapshot.get("target_bound") is not True:
            return None
        if not _answers_match(str(snapshot.get("answer") or ""), final_answer):
            raise DoubaoDriverUnavailableError("answer-changed-during-capture")

        share_link, share_missing = await _capture_share_link(page, attempt_token)
        await _dismiss_share_panel(page)

        screenshots, coverage = await _save_sequential_screenshots(
            page, attempt_root, project_root, attempt_token
        )
        # Doubao renders the recommended follow-up chips lazily at the bottom
        # of the matched answer.  The first snapshot is intentionally kept for
        # references/products, but follow-ups must be sampled again after the
        # required top/body/bottom pass has reached that lazy-render boundary.
        bottom_snapshot = await _wait_for_stable_module_snapshot(page, attempt_token)
        if bottom_snapshot.get("target_bound") is not True:
            return None
        if not _answers_match(str(bottom_snapshot.get("answer") or ""), final_answer):
            raise DoubaoDriverUnavailableError("answer-changed-during-capture")
        snapshot["followups"] = bottom_snapshot.get("followups")
        snapshot["followup_module_displayed"] = bottom_snapshot.get(
            "followup_module_displayed"
        )
        videos = await _capture_video_evidence(
            page,
            snapshot.get("video_cards"),
            attempt_root,
            project_root,
            attempt_token,
        )
        modules, expectations, missing = _page_modules(snapshot, videos)
        missing.extend(share_missing)
        observation = _brand_observation(question, final_answer, modules)
        return {
            "id": question_id,
            "question": question,
            "has_brand": question_id.startswith("B"),
            "status": "completed",
            "chat_url": chat_url,
            "answer": final_answer,
            "collected_at": datetime.now(timezone(timedelta(hours=8))).isoformat(
                timespec="seconds"
            ),
            "page_modules": modules,
            "brand_observation": observation,
            "page_evidence": {
                "screenshot_files": screenshots,
                "module_expectations": expectations,
                "screenshot_coverage": coverage,
                "share_link": share_link,
            },
            "required_missing": missing,
        }
    except DoubaoDriverUnavailableError:
        raise
    except Exception as exc:
        raise DoubaoDriverUnavailableError("doubao-page-capture-failed") from exc
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        await pw.stop()


async def _wait_for_stable_target_turn(
    page,
    *,
    question: str,
    token: str,
) -> dict[str, Any] | None:
    """Bind one exact user turn and require five stable assistant samples."""

    previous_signature = ""
    stable_samples = 0
    for _ in range(65):
        baseline = await page.evaluate(_BASELINE_PAGE_JS)
        baseline = baseline if isinstance(baseline, dict) else {}
        exception_kind = _page_exception_kind(baseline)
        if exception_kind:
            return {
                "status": "verification_required",
                "exception_kind": exception_kind,
            }
        current = await page.evaluate(
            _BIND_TARGET_TURN_JS,
            {"question": question, "token": token},
        )
        if not isinstance(current, dict):
            current = {}
        status = current.get("status")
        if status == "ambiguous":
            raise DoubaoDriverUnavailableError("page-question-ambiguous")
        if status != "matched":
            previous_signature = ""
            stable_samples = 0
            await page.wait_for_timeout(1_000)
            continue
        signature = str(current.get("signature") or "")
        if signature and signature == previous_signature:
            stable_samples += 1
            if stable_samples >= 5:
                return current
        else:
            previous_signature = signature
            stable_samples = 1
        await page.wait_for_timeout(1_000)
    raise DoubaoDriverUnavailableError("doubao-page-unstable")


def _page_exception_kind(snapshot: dict[str, Any]) -> str | None:
    for kind in ("captcha", "login", "access"):
        if snapshot.get(kind) is True:
            return kind
    return None


async def _save_verification_screenshot(
    page, attempt_root: Path, project_root: Path, kind: str
) -> str:
    path = attempt_root / f"verification-{kind}.png"
    await page.screenshot(path=str(path), full_page=False)
    return path.relative_to(project_root).as_posix()


async def _expand_references(
    page, baseline: dict[str, Any], token: str
) -> None:
    signal = baseline.get("reference_signal")
    if not isinstance(signal, dict):
        return
    expected = signal.get("reference_count")
    if isinstance(expected, int) and baseline.get("source_link_count") == expected:
        return
    result = await page.evaluate(_EXPAND_REFERENCES_JS, token)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DoubaoDriverUnavailableError("reference-module-expand-failed")
    await page.wait_for_timeout(800)


async def _capture_share_link(page, token: str) -> tuple[dict[str, Any], list[str]]:
    """Return the exact value produced by the visible Share → Copy link flow."""

    opened = await page.evaluate(_OPEN_SHARE_PANEL_JS, token)
    if not isinstance(opened, dict) or opened.get("displayed") is not True:
        return (
            {
                "displayed": False,
                "copy_control_displayed": False,
                "url": "页面未显示",
            },
            ["share-copy-control-missing"],
        )
    if opened.get("opened") is not True:
        return (
            {"displayed": True, "copy_control_displayed": False, "url": "未获取"},
            ["share-panel-open-failed"],
        )

    try:
        await page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin="https://www.doubao.com",
        )
    except Exception:
        return (
            {"displayed": True, "copy_control_displayed": False, "url": "未获取"},
            ["share-clipboard-permission-unavailable"],
        )

    clipboard_marker = f"gjx-share-proof:{uuid4()}"
    prepared = await page.evaluate(_PREPARE_SHARE_CLIPBOARD_JS, clipboard_marker)
    if prepared is not True:
        return (
            {"displayed": True, "copy_control_displayed": False, "url": "未获取"},
            ["share-clipboard-unavailable"],
        )

    # The share panel body (including the copy-link control) renders
    # asynchronously after the panel opens; a fixed 600ms wait was flaky
    # (G0027 regression: share_link_copy_control_missing).  Poll until the
    # control appears, up to ~3.3s, before clicking it once.
    copied: dict[str, Any] | None = None
    for _ in range(12):
        result = await page.evaluate(_COPY_SHARE_LINK_JS, token)
        if isinstance(result, dict) and result.get("copy_control_displayed") is True:
            copied = result
            break
        await page.wait_for_timeout(250)
    if not isinstance(copied, dict) or copied.get("copy_control_displayed") is not True:
        return (
            {"displayed": True, "copy_control_displayed": False, "url": "未获取"},
            ["share-copy-control-missing"],
        )

    url = ""
    for _ in range(12):
        clipboard_value = await page.evaluate(_READ_SHARE_CLIPBOARD_JS)
        candidate = str(clipboard_value or "").strip()
        if candidate and candidate != clipboard_marker:
            url = candidate
            break
        await page.wait_for_timeout(250)
    if not _DOUBAO_SHARE_URL.match(url):
        return (
            {"displayed": True, "copy_control_displayed": True, "url": "未获取"},
            ["share-link-unavailable"],
        )
    return (
        {
            "displayed": True,
            "copy_control_displayed": True,
            "capture_method": "share-copy-control",
            "url": url,
        },
        [],
    )


async def _dismiss_share_panel(page) -> None:
    """Keep the share popover out of the required top/body/bottom screenshots."""

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(100)
    except Exception:
        return


async def _wait_for_stable_module_snapshot(page, token: str) -> dict[str, Any]:
    previous = ""
    stable_samples = 0
    for _ in range(15):
        snapshot = await page.evaluate(_COLLECT_PAGE_JS, token)
        if not isinstance(snapshot, dict):
            raise DoubaoDriverUnavailableError("page-capture-invalid")
        signature = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if signature == previous:
            stable_samples += 1
            if stable_samples >= 2:
                return snapshot
        else:
            previous = signature
            stable_samples = 1
        await page.wait_for_timeout(1_000)
    raise DoubaoDriverUnavailableError("page-module-unstable")


async def _save_sequential_screenshots(
    page,
    attempt_root: Path,
    project_root: Path,
    token: str,
) -> tuple[list[str], dict[str, bool]]:
    metrics = await page.evaluate(_SCROLL_METRICS_JS, token)
    if not isinstance(metrics, dict) or metrics.get("ok") is not True:
        raise DoubaoDriverUnavailableError("target-scroll-container-missing")
    height = max(1, int((metrics or {}).get("height") or 1))
    viewport = max(1, int((metrics or {}).get("viewport") or 1))
    maximum = max(0, height - viewport)
    overlap = min(240, max(120, viewport // 4))
    step = max(1, viewport - overlap)
    offsets = [0]
    while offsets[-1] < maximum:
        offsets.append(min(maximum, offsets[-1] + step))
    if len(offsets) < 3:
        offsets = [0, maximum // 2, maximum]

    artifacts: list[str] = []
    answer_seen = False
    for index, offset in enumerate(offsets):
        positioned = await page.evaluate(
            _SCROLL_TO_JS,
            {"token": token, "offset": offset},
        )
        if not isinstance(positioned, dict):
            raise DoubaoDriverUnavailableError("target-scroll-position-failed")
        actual = int(positioned.get("actual") or 0)
        if abs(actual - offset) > 4:
            raise DoubaoDriverUnavailableError("target-scroll-position-mismatch")
        await page.wait_for_timeout(250)
        answer_seen = answer_seen or bool(
            await page.evaluate(_ANSWER_VISIBLE_JS, token)
        )
        label = "顶部" if index == 0 else "底部" if index == len(offsets) - 1 else f"正文{index:02d}"
        path = attempt_root / f"{index + 1:02d}_{label}.png"
        await page.screenshot(path=str(path), full_page=False)
        artifacts.append(path.relative_to(project_root).as_posix())
    if not answer_seen:
        raise DoubaoDriverUnavailableError("answer-screenshot-coverage-missing")
    return artifacts, {"top": True, "answer": True, "bottom": True}


async def _capture_video_evidence(
    page,
    video_cards: Any,
    attempt_root: Path,
    project_root: Path,
    token: str,
) -> list[dict[str, str]]:
    count = len(video_cards) if isinstance(video_cards, list) else 0
    records: list[dict[str, str]] = []
    for index in range(count):
        details = await page.evaluate(
            _OPEN_VIDEO_JS,
            {"token": token, "index": index},
        )
        if not isinstance(details, dict) or details.get("opened") is not True:
            records.append({"account": "", "title": "", "screenshot_file": ""})
            continue
        await page.wait_for_timeout(700)
        player_token = f"{token}-{index}"
        player = await page.evaluate(_READ_VIDEO_PLAYER_JS, player_token)
        player = player if isinstance(player, dict) else {}
        path = attempt_root / f"相关视频{index + 1:02d}_账号标题.png"
        if player.get("valid") is True:
            await page.locator(
                f'[data-gjx-video-player="{player_token}"]'
            ).screenshot(path=str(path))
        records.append(
            {
                "account": str(player.get("account") or "").strip(),
                "title": str(
                    details.get("card_title") or player.get("title") or ""
                ).strip(),
                "screenshot_file": (
                    path.relative_to(project_root).as_posix()
                    if path.is_file()
                    else ""
                ),
            }
        )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        closed = await page.evaluate(_VIDEO_PLAYER_CLOSED_JS, player_token)
        if closed is not True:
            records[-1]["account"] = ""
            records[-1]["screenshot_file"] = ""
    return records


def _page_modules(
    snapshot: dict[str, Any], videos: list[dict[str, str]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    signal = snapshot.get("reference_signal")
    signal = signal if isinstance(signal, dict) else {}
    expected_keywords = signal.get("keyword_count")
    expected_refs = signal.get("reference_count")
    # The page's declared keyword count is a count of displayed chips, not a
    # count of distinct text values.  Preserve repeated chips verbatim so the
    # saved evidence remains faithful to the UI and can be checked against it.
    keywords = _displayed_texts(snapshot.get("keywords"))
    references = _unique_links(snapshot.get("source_links"))
    products = _unique_links(snapshot.get("product_links"))
    # Recommendation chips are an optional, separately-rendered UI module.
    # Do not turn an assistant's prose invitation into a chip: that would
    # fabricate page evidence when Doubao intentionally renders no module.
    followups = _unique_texts(snapshot.get("followups"))
    followup_module_displayed = snapshot.get("followup_module_displayed") is True
    modules: dict[str, Any] = {
        "keywords": keywords if isinstance(expected_keywords, int) else "页面未显示",
        "ref_links": references if isinstance(expected_refs, int) else "页面未显示",
        "product_links": products or "页面未显示",
        "video_links": videos or "页面未显示",
        "followups": followups or "页面未显示",
    }
    expectations = {
        "keywords": {
            "displayed": isinstance(expected_keywords, int),
            "expected_count": expected_keywords if isinstance(expected_keywords, int) else 0,
        },
        "ref_links": {
            "displayed": isinstance(expected_refs, int),
            "expected_count": expected_refs if isinstance(expected_refs, int) else 0,
        },
        "product_links": {"displayed": bool(products), "expected_count": len(products)},
        "video_links": {"displayed": bool(videos), "expected_count": len(videos)},
        "followups": {
            "displayed": followup_module_displayed or bool(followups),
            "expected_count": len(followups),
        },
    }
    missing: list[str] = []
    source_count = snapshot.get("source_block_count")
    if isinstance(source_count, int) and source_count > 1:
        missing.append("reference-source-block-not-unique")
    elif isinstance(signal.get("reference_count"), int) and source_count != 1:
        missing.append("reference-source-block-not-unique")
    if snapshot.get("reference_overlay_ambiguous") is True:
        missing.append("reference-overlay-ambiguous")
    if isinstance(expected_keywords, int) and len(keywords) != expected_keywords:
        missing.append("keyword-count-mismatch")
    if isinstance(expected_refs, int) and len(references) != expected_refs:
        missing.append("reference-count-mismatch")
    if any(not row["account"] or not row["title"] for row in videos):
        missing.append("related-video-account-title-missing")
    product_module_count = snapshot.get("product_module_count")
    if isinstance(product_module_count, int) and product_module_count > len(products):
        missing.append("product-module-link-missing")
    if followup_module_displayed and not followups:
        missing.append("recommended-followups-extraction-inconsistent")
    return modules, expectations, missing


def _unique_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    output: list[str] = []
    for item in value:
        text = str(
            item.get("text") or item.get("title") or ""
            if isinstance(item, dict)
            else item
        ).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _displayed_texts(value: Any) -> list[str]:
    """Keep every non-empty displayed text item, including repeated chips."""

    if not isinstance(value, list):
        return []
    return [
        text
        for item in value
        if (
            text := str(
                item.get("text") or item.get("title") or ""
                if isinstance(item, dict)
                else item
            ).strip()
        )
    ]


def _unique_links(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("text") or "").strip()
        url = str(item.get("url") or item.get("href") or "").strip()
        if title and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            output.append({"title": title, "url": url})
    return output


def _brand_observation(
    question: str, answer: str, modules: dict[str, Any]
) -> dict[str, Any]:
    target = "高吉星"
    positions: list[dict[str, str]] = []
    start = answer.find(target)
    while start >= 0 and len(positions) < 10:
        positions.append(
            {
                "module": "回答正文",
                "text": answer[max(0, start - 40) : start + len(target) + 60]
                .replace("\n", " ")
                .strip(),
            }
        )
        start = answer.find(target, start + len(target))
    for key, label in (
        ("ref_links", "参考资料"),
        ("product_links", "产品外链"),
        ("video_links", "相关视频"),
        ("followups", "推荐追问"),
    ):
        values = modules.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = (
                " ".join(str(part) for part in value.values())
                if isinstance(value, dict)
                else str(value)
            )
            if target in text:
                positions.append({"module": label, "text": text})
    appeared = bool(positions)
    if target in question:
        natural: bool | None = None
        basis = "品牌词问句：只记录出现位置，不判断自然推荐"
    elif not appeared:
        natural = False
        basis = "页面回答和已显示模块未出现高吉星"
    else:
        recommend_words = ("推荐", "建议", "可选", "适合", "优先", "值得", "首选")
        natural = any(
            position["module"] in {"产品外链", "相关视频", "推荐追问"}
            or (
                any(word in position["text"] for word in recommend_words)
                and not any(
                    negative in position["text"]
                    for negative in ("不推荐", "不建议", "避免", "慎选", "不适合")
                )
            )
            for position in positions
        )
        basis = (
            "非品牌问句中，高吉星以推荐性内容或产品/视频/追问形式出现"
            if natural
            else "非品牌问句中仅出现高吉星，未见推荐性线索"
        )
    return {
        "target": target,
        "appeared": appeared,
        "positions": positions[:20],
        "natural_recommendation": natural,
        "basis": basis,
    }


def _answers_match(left: str, right: str) -> bool:
    """Compare one rendered answer without treating layout whitespace as content."""

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()

    return normalize(left) == normalize(right)


def _placeholder_answer(answer: str) -> bool:
    value = answer.strip()
    return not value or value in _PLACEHOLDER_ANSWERS or (
        len(value) < 6 and re.search(r"[。！？!?]|\n", value) is None
    )


_BASELINE_PAGE_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const style = getComputedStyle(n);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const hasVisible = selector => [...document.querySelectorAll(selector)].some(visible);
  const overlays = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], .semi-modal, .modal, [role="alert"]')]
    .filter(visible)
    .map(el => norm(el.innerText || el.textContent || ''))
    .filter(text => text && text.length <= 500);
  const routerLogin = window._ROUTER_DATA?.loaderData?.chat_layout?.userSetting?.data?.is_login;
  const captcha = hasVisible('iframe[src*="captcha"], iframe[src*="verify"], iframe[src*="rmc"], input[placeholder*="验证码"], input[aria-label*="验证码"]')
    || overlays.some(text => /人机验证|完成安全验证|滑动验证|拖动滑块/.test(text));
  const login = !captcha && (routerLogin === false || overlays.some(text => /扫码登录|请登录后使用|登录后继续/.test(text)));
  const access = !captcha && !login && overlays.some(text => /访问异常|访问受限|服务异常|当前访问人数过多|网络不给力/.test(text));
  return {
    captcha,
    login,
    access,
  };
}
"""

_BIND_TARGET_TURN_JS = r"""
({question, token}) => {
  const clean = value => (value || '').replace(/\u00a0/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  const exact = value => clean(value).normalize('NFKC')
    .replace(/^\s*\d+\s*[.、．]\s*/, '')
    .replace(/[^\p{L}\p{N}]/gu, '').toLowerCase();
  const visible = el => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const role = root => {
    if (root.matches('[data-testid="send_message"], [class*="send-message"], [class*="bg-g-send-msg-bubble"]')
      || root.querySelector('[data-testid="send_message"], [class*="send-message"], [class*="bg-g-send-msg-bubble"], [data-foundation-type="send-message-action-bar"]')) return 'User';
    if (root.matches('[data-testid="receive_message"], [data-testid*="receive_message"], [class*="receive-message"], [class*="bg-g-receive-msg-bubble"]')
      || root.querySelector('[data-testid="receive_message"], [data-testid*="receive_message"], [class*="receive-message"], [class*="bg-g-receive-msg-bubble"], [data-foundation-type="receive-message-action-bar"]')) return 'Assistant';
    if ((root.matches('[class*="inner-item-"], [class*="top-item-"]') || root.closest('[class*="inner-item-"], [class*="top-item-"]'))
      && (root.matches('.flow-markdown-body, .md-box-root, [class*="md-box-root"]') || root.querySelector('.flow-markdown-body, .md-box-root, [class*="md-box-root"]'))
      && !root.matches('[class*="bg-g-send-msg-bubble"]') && !root.querySelector('[class*="bg-g-send-msg-bubble"]')) return 'Assistant';
    return '';
  };
  const textSelectors = [
    '[data-testid="message_text_content"]', '[data-testid="message_content"]',
    '[data-testid*="message_text"]', '[data-testid*="message_content"]',
    '[class*="message-text"]', '[class*="message-content"]',
    '[class*="bg-g-send-msg-bubble"]', '[class*="bg-g-receive-msg-bubble"]',
    '.flow-markdown-body', '.md-box-root', '[class*="md-box-root"]', '[class*="bubble"]'
  ];
  const text = root => {
    for (const selector of textSelectors) {
      const chunks = [...root.querySelectorAll(selector)].filter(visible)
        .map(el => clean(el.innerText || el.textContent || '')).filter(Boolean);
      if (chunks.length) return clean([...new Set(chunks)].join('\n'));
    }
    return clean(root.innerText || root.textContent || '');
  };
  const lists = [...document.querySelectorAll('[class*="message-list-"], .container-PvPoAn, .scroll-view-OEiNXD, [data-testid="message-list"]')].filter(visible);
  if (!lists.length) return {status: 'missing'};
  const selectors = [
    '[class*="inner-item-"]', '[class*="top-item-"]', '[class*="item-kDun2N"]',
    '[data-testid="union_message"]', '[data-testid="message-block-container"]',
    '[data-message-id]', '[class*="bg-g-send-msg-bubble"]', '[class*="bg-g-receive-msg-bubble"]'
  ];
  const roots = [];
  const seen = new Set();
  for (const list of lists) for (const selector of selectors) list.querySelectorAll(selector).forEach(el => {
    if (!seen.has(el)) { seen.add(el); roots.push(el); }
  });
  const ordered = roots.filter(visible)
    .filter((el, index, items) => !items.some((other, otherIndex) => otherIndex !== index && other.contains(el)))
    .map(el => ({el, role: role(el), text: text(el)}))
    .filter(item => item.role && item.text)
    .sort((a, b) => a.el === b.el ? 0 : (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  const matches = ordered.map((item, index) => ({item, index}))
    .filter(({item}) => item.role === 'User' && exact(item.text) === exact(question));
  if (matches.length !== 1) return {status: matches.length > 1 ? 'ambiguous' : 'missing'};
  const start = matches[0].index;
  const assistants = [];
  let nextUser = null;
  for (let index = start + 1; index < ordered.length; index += 1) {
    if (ordered[index].role === 'User') {
      nextUser = ordered[index];
      break;
    }
    if (ordered[index].role === 'Assistant') assistants.push(ordered[index]);
  }
  if (!assistants.length) return {status: 'pending'};
  document.querySelectorAll('[data-gjx-target-assistant], [data-gjx-target-user], [data-gjx-next-user], [data-gjx-scroll]').forEach(el => {
    el.removeAttribute('data-gjx-target-assistant');
    el.removeAttribute('data-gjx-target-user');
    el.removeAttribute('data-gjx-next-user');
    el.removeAttribute('data-gjx-scroll');
  });
  matches[0].item.el.setAttribute('data-gjx-target-user', token);
  assistants.forEach(item => item.el.setAttribute('data-gjx-target-assistant', token));
  if (nextUser) nextUser.el.setAttribute('data-gjx-next-user', token);
  let scroller = assistants[0].el.parentElement;
  while (scroller && scroller !== document.body && scroller.scrollHeight <= scroller.clientHeight + 40) scroller = scroller.parentElement;
  (scroller || document.scrollingElement || document.documentElement).setAttribute('data-gjx-scroll', token);
  const answer = clean(assistants.map(item => item.text).join('\n'));
  const sourceBlocks = assistants.flatMap(item => [...item.el.querySelectorAll('[data-plugin-identifier*="block_type:10025"]')])
    .filter(visible).filter(el => /搜索\s*\d+\s*个关键词[，,、\s]*参考\s*\d+\s*篇资料/.test(clean(el.innerText)));
  const signalMatch = sourceBlocks.length === 1 ? clean(sourceBlocks[0].innerText).match(/搜索\s*(\d+)\s*个关键词[，,、\s]*参考\s*(\d+)\s*篇资料/) : null;
  const links = root => [...root.querySelectorAll('a[href]')].filter(visible)
    .filter(a => /^https?:\/\//i.test(a.href) && !/doubao\.com|bytedance|zijieapi|byteimg|feiliao/i.test(a.href));
  const signature = clean(assistants.map(item => item.el.innerText || '').join('\n'));
  return {
    status: 'matched', answer, signature,
    reference_signal: signalMatch ? {keyword_count: Number(signalMatch[1]), reference_count: Number(signalMatch[2])} : null,
    source_link_count: sourceBlocks.length === 1 ? links(sourceBlocks[0]).length : 0,
  };
}
"""

_EXPAND_REFERENCES_JS = r"""
(token) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const roots = [...document.querySelectorAll(`[data-gjx-target-assistant="${token}"]`)];
  const sources = roots.flatMap(root => [...root.querySelectorAll('[data-plugin-identifier*="block_type:10025"]')])
    .filter(el => /搜索\s*\d+\s*个关键词[，,、\s]*参考\s*\d+\s*篇资料/.test(norm(el.innerText)));
  if (sources.length !== 1) return {ok: false, reason: 'source-block-not-unique'};
  const source = sources[0];
  const trigger = [...source.querySelectorAll('[data-copy-ignore].cursor-pointer, [data-copy-ignore][class*="cursor-pointer"]')]
    .filter(el => /搜索\s*\d+\s*个关键词[，,、\s]*参考\s*\d+\s*篇资料/.test(norm(el.innerText)))
    .filter(el => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    })[0];
  if (!trigger) return {ok: false, reason: 'source-trigger-missing'};
  const rect = trigger.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return {ok: false, reason: 'source-trigger-hidden'};
  trigger.scrollIntoView({block: 'center'});
  trigger.click();
  return {ok: true};
}
"""

_OPEN_SHARE_PANEL_JS = r"""
(token) => {
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const label = el => norm([
    el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'),
    el.getAttribute('data-testid'), el.getAttribute('data-tooltip')
  ].filter(Boolean).join(' '));
  const roots = [...document.querySelectorAll(`[data-gjx-target-assistant="${token}"]`)].filter(visible);
  let controls = [...new Set(roots.flatMap(root => [...root.querySelectorAll('button, [role="button"], [data-testid], [data-tooltip]')]))]
    .filter(visible)
    .filter(el => /分享|share/i.test(label(el)));
  // Doubao's current answer bar exposes the share icon without accessible text.
  // Its order is copy, read, like, dislike, share, regenerate, more.  Anchor the
  // fallback to the already matched assistant turn and require the unique menu
  // trigger at position 6 so a visual change fails closed instead of sharing a
  // different conversation.
  if (!controls.length) {
    const candidates = roots.flatMap(root => {
      const buttons = [...root.querySelectorAll('button')].filter(visible);
      return buttons.length >= 7
        && buttons[6]?.getAttribute('data-slot') === 'dropdown-menu-trigger'
        ? [buttons[4]]
        : [];
    }).filter(Boolean);
    controls = [...new Set(candidates)];
  }
  if (!controls.length) return {displayed: false};
  if (controls.length !== 1) return {displayed: true, opened: false, reason: 'share-control-ambiguous'};
  controls[0].scrollIntoView({block: 'center'});
  controls[0].click();
  return {displayed: true, opened: true};
}
"""

_COPY_SHARE_LINK_JS = r"""
(token) => {
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const label = el => norm([
    el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'),
    el.getAttribute('data-testid'), el.getAttribute('data-tooltip')
  ].filter(Boolean).join(' '));
  const controls = [...document.querySelectorAll('button, [role="button"], [data-testid], [data-tooltip]')]
    .filter(visible).filter(el => /^(复制链接|copy link)$/i.test(label(el)));
  if (controls.length !== 1) return {copy_control_displayed: false, url: null};
  controls[0].click();
  return {copy_control_displayed: true, clicked: true};
}
"""

_PREPARE_SHARE_CLIPBOARD_JS = r"""
async (marker) => {
  try {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') return false;
    await navigator.clipboard.writeText(marker);
    return true;
  } catch (_) {
    return false;
  }
}
"""

_READ_SHARE_CLIPBOARD_JS = r"""
async () => {
  try {
    if (!navigator.clipboard || typeof navigator.clipboard.readText !== 'function') return null;
    return await navigator.clipboard.readText();
  } catch (_) {
    return null;
  }
}
"""

_CONFIRM_DELETE_CONVERSATION_JS = r"""
(stage) => {
  const visible = el => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  if (stage === 'menu') {
    const menuItems = [...document.querySelectorAll('[role="menu"] [role="menuitem"]')]
      .filter(visible).filter(el => norm(el.innerText || el.textContent) === '删除');
    if (menuItems.length !== 1) return {stage: 'missing'};
    menuItems[0].click();
    return {stage: 'menu'};
  }
  const dialogs = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')]
    .filter(visible).filter(el => /删除/.test(norm(el.innerText || el.textContent)));
  const confirms = dialogs.flatMap(dialog => [...dialog.querySelectorAll('button, [role="button"]')]
    .filter(visible).filter(el => norm(el.innerText || el.textContent) === '删除'));
  if (confirms.length !== 1) return {stage: 'missing'};
  confirms[0].click();
  return {stage: 'confirmed'};
}
"""

_COLLECT_PAGE_JS = r"""
(token) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const linkRows = root => [...root.querySelectorAll('a[href]')]
    .filter(visible)
    .map(a => ({title: norm(a.innerText || a.getAttribute('aria-label') || a.title), url: (a.href || '').trim()}))
    .filter(x => x.title && /^https?:\/\//i.test(x.url) && !/doubao\.com|bytedance|zijieapi|byteimg|feiliao/i.test(x.url));
  const roots = [...document.querySelectorAll(`[data-gjx-target-assistant="${token}"]`)].filter(visible);
  if (!roots.length) return {target_bound: false};
  const answerSelector = '[data-testid="message_text_content"], [data-testid="message_content"], [data-testid*="message_text"], [data-testid*="message_content"], [class*="message-text"], [class*="message-content"], .flow-markdown-body, .md-box-root, [class*="md-box-root"]';
  const answerNodes = roots.flatMap(root => [root, ...root.querySelectorAll(answerSelector)])
    .filter(el => el.matches(answerSelector)).filter(visible);
  const answer = norm([...new Set(answerNodes.map(el => norm(el.innerText || el.textContent || '')).filter(Boolean))].join('\n'));
  const sources = roots.flatMap(root => [...root.querySelectorAll('[data-plugin-identifier*="block_type:10025"]')])
    .filter(visible).filter(el => /搜索\s*\d+\s*个关键词[，,、\s]*参考\s*\d+\s*篇资料/.test(norm(el.innerText)));
  const source = sources.length === 1 ? sources[0] : null;
  const sourceText = norm(source && source.innerText);
  const signal = sourceText.match(/搜索\s*(\d+)\s*个关键词[，,、\s]*参考\s*(\d+)\s*篇资料/);
  const keywordText = source ? (() => {
    const walker = document.createTreeWalker(source, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement && node.parentElement.closest('a')
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const values = [];
    while (walker.nextNode()) values.push(walker.currentNode.nodeValue || '');
    return norm(values.join(' '));
  })() : '';
  const keywords = [];
  for (const match of keywordText.matchAll(/[“"]([^”"]+)[”"]/g)) {
    const value = norm(match[1]);
    if (value && value.length < 160) keywords.push(value);
  }
  const productRoots = roots.flatMap(root => [...root.querySelectorAll('[data-plugin-identifier*="product"], [class*="product-card"], [class*="commodity-card"], [class*="goods-card"]')]).filter(visible);
  const productLinks = productRoots.flatMap(linkRows);
  const videoBlocks = roots.flatMap(root => [...root.querySelectorAll('[data-plugin-identifier*="block_type:10050"]')]).filter(visible);
  const videoCards = videoBlocks
    .map(block => {
      const card = block.querySelector('.video-card-CJKKPp, [class*="video-card"], .video-wrapper-YpeXnI, [class*="video-wrapper"]');
      return card && visible(card) ? {card_title: norm(card.innerText)} : null;
    }).filter(Boolean);
  // Follow-up chips are lazy-rendered after the answer action bar and their
  // generated class names are not stable.  Bind by conversation order instead:
  // inside the same message scroller, after this answer and before the next
  // user turn.  Only concise clickable questions qualify; answer/reference/
  // product/video content is excluded so a question-shaped citation cannot be
  // mistaken for a recommended follow-up.
  const scroller = document.querySelector(`[data-gjx-scroll="${token}"]`);
  const nextUser = document.querySelector(`[data-gjx-next-user="${token}"]`);
  const lastRoot = roots[roots.length - 1];
  const excludedSelector = `${answerSelector}, [data-plugin-identifier*="block_type:10025"], [data-plugin-identifier*="block_type:10050"], [data-plugin-identifier*="product"], [class*="product-card"], [class*="commodity-card"], [class*="goods-card"]`;
  const followupModule = scroller instanceof HTMLElement
    ? [...scroller.querySelectorAll('[data-testid*="recommend"], [data-testid*="follow"], [aria-label*="追问"], [aria-label*="推荐问题"]')]
        .find(el => visible(el) && (lastRoot === el || Boolean(lastRoot.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING))
          && (!nextUser || Boolean(el.compareDocumentPosition(nextUser) & Node.DOCUMENT_POSITION_FOLLOWING)))
    : null;
  const followupElements = scroller instanceof HTMLElement
    ? [...scroller.querySelectorAll('*')].filter(visible).filter(el => {
        const text = norm(el.innerText || el.textContent || '');
        if (text.length <= 4 || text.length >= 160 || !/[?？]$/.test(text)) return false;
        if (el.closest(excludedSelector)) return false;
        const insideAnswer = roots.some(root => root === el || root.contains(el));
        const afterAnswer = insideAnswer || Boolean(lastRoot.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
        const beforeNext = !nextUser || Boolean(el.compareDocumentPosition(nextUser) & Node.DOCUMENT_POSITION_FOLLOWING);
        if (!afterAnswer || !beforeNext) return false;
        return el.matches('button, [role="button"], a[href], [tabindex]:not([tabindex="-1"])')
          || getComputedStyle(el).cursor === 'pointer';
      })
    : [];
  const followups = followupElements
    .filter(el => !followupElements.some(other => other !== el && el.contains(other)
      && norm(other.innerText || other.textContent || '') === norm(el.innerText || el.textContent || '')))
    .map(el => norm(el.innerText || el.textContent || ''))
    .filter((text, index, all) => all.indexOf(text) === index);
  let references = source ? linkRows(source) : [];
  let overlay_ambiguous = false;
  if (signal && references.length !== Number(signal[2])) {
    const overlays = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], [class*="popover"], [class*="reference"]')]
      .filter(visible).map(root => ({root, links: linkRows(root)})).filter(item => item.links.length > 0);
    if (overlays.length === 1) references = overlays[0].links;
    else if (overlays.length > 1) overlay_ambiguous = true;
  }
  return {
    target_bound: true, answer,
    reference_signal: signal ? {keyword_count: Number(signal[1]), reference_count: Number(signal[2])} : null,
    keywords,
    source_links: references,
    product_links: productLinks,
    product_module_count: productRoots.length,
    video_cards: videoCards,
    followups,
    followup_module_displayed: Boolean(followupModule) || followups.length > 0,
    source_block_count: sources.length,
    reference_overlay_ambiguous: overlay_ambiguous,
  };
}
"""

_SCROLL_METRICS_JS = r"""
(token) => {
  const scroller = document.querySelector(`[data-gjx-scroll="${token}"]`);
  if (!(scroller instanceof HTMLElement)) return {ok: false};
  return {ok: true, height: scroller.scrollHeight, viewport: scroller.clientHeight || window.innerHeight};
}
"""

_SCROLL_TO_JS = r"""
({token, offset}) => {
  const scroller = document.querySelector(`[data-gjx-scroll="${token}"]`);
  if (!(scroller instanceof HTMLElement)) return null;
  scroller.scrollTop = offset;
  scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
  return {actual: scroller.scrollTop};
}
"""

_ANSWER_VISIBLE_JS = r"""
(token) => [...document.querySelectorAll(`[data-gjx-target-assistant="${token}"]`)].some(root => {
  const rect = root.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
})
"""

_OPEN_VIDEO_JS = r"""
({token, index}) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const roots = [...document.querySelectorAll(`[data-gjx-target-assistant="${token}"]`)];
  const block = roots.flatMap(root => [...root.querySelectorAll('[data-plugin-identifier*="block_type:10050"]')])[index];
  const card = block && block.querySelector('.video-card-CJKKPp, [class*="video-card"], .video-wrapper-YpeXnI, [class*="video-wrapper"]');
  if (!card) return {opened: false};
  card.scrollIntoView({block: 'center'});
  const card_title = norm(card.innerText);
  card.click();
  return {opened: true, card_title};
}
"""

_READ_VIDEO_PLAYER_JS = r"""
(token) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  // Prefer the active xgplayer wrapper: the fullscreen Semi dialog wraps the
  // same player, so matching both at once double-counts one visible player.
  const direct = [...document.querySelectorAll('.play-wrapper.active')].filter(visible);
  const roots = direct.length ? direct : [...document.querySelectorAll('[role="dialog"]')].filter(visible);
  if (roots.length !== 1) return {valid: false};
  const root = roots[0];
  const unique = entries => entries.filter((entry, index, all) => all.findIndex(other => other.text === entry.text) === index);
  const accountEntries = unique([...root.querySelectorAll('.name-lctyxx, [class*="author"], [class*="account"], [class*="name-"]')]
    .filter(visible).map(el => ({el, text: norm(el.innerText)})).filter(entry => entry.text)
    .filter(entry => entry.text.startsWith('@') || entry.text.length <= 80));
  const titleEntries = unique([...root.querySelectorAll('.captions-DxoqDI, [class*="caption"], [class*="title"]')]
    .filter(visible).map(el => ({el, text: norm(el.innerText)})).filter(entry => entry.text));
  if (accountEntries.length !== 1 || titleEntries.length !== 1) return {valid: false};
  const rootRect = root.getBoundingClientRect();
  const inside = el => {
    const rect = el.getBoundingClientRect();
    return rect.left >= rootRect.left && rect.right <= rootRect.right
      && rect.top >= rootRect.top && rect.bottom <= rootRect.bottom;
  };
  if (!inside(accountEntries[0].el) || !inside(titleEntries[0].el)) return {valid: false};
  root.setAttribute('data-gjx-video-player', token);
  return {
    valid: true,
    account: accountEntries[0].text,
    title: titleEntries[0].text,
  };
}
"""

_VIDEO_PLAYER_CLOSED_JS = r"""
(token) => {
  const root = document.querySelector(`[data-gjx-video-player="${token}"]`);
  if (!root) return true;
  const style = getComputedStyle(root);
  const rect = root.getBoundingClientRect();
  return style.display === 'none' || style.visibility === 'hidden' || rect.width < 1 || rect.height < 1;
}
"""


__all__ = [
    "DoubaoDriverUnavailableError",
    "OpenCLIDoubaoEvidenceDriver",
    "build_opencli_doubao_evidence_driver",
]
