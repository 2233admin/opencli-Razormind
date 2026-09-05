"""Runtime adapter for the Browser Bridge (BBX) CLI."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote, urlsplit

from backend.agent_runtimes.base import (
    AgentTask,
    RuntimeAdapter,
    RuntimeCapabilities,
    RuntimeInvocationError,
    event_done,
    event_error,
    event_evidence,
    event_started,
    event_tool_call,
    event_tool_result,
)
from backend.agent_runtimes.registry import register_runtime

_DEFAULT_TIMEOUT_SECONDS = 60.0
_LIST_WORKFLOWS = {"tool.list", "tools.list", "list_tools", "bbx_list_tools"}
_CALL_WORKFLOWS = {"tool.call", "tools.call", "call_tool", "bbx_call"}
_HEALTH_WORKFLOWS = {"health", "server.health", "bbx_health"}
_DOUBAO_WORKFLOWS = {"workflow.gaojixing.doubao.browser"}
_DOUBAO_URL = "https://www.doubao.com/chat"
_DOUBAO_INPUT_SELECTOR = 'textarea, [contenteditable="true"], [role="textbox"]'


@register_runtime
class BbxRuntimeAdapter(RuntimeAdapter):
    """Translate BBX CLI JSON responses into normalized runtime events."""

    runtime_type = "bbx"
    capabilities = RuntimeCapabilities(
        transport="stdio",
        streaming=False,
        resume_by_id=False,
        checkpoint="none",
        concurrent_sessions=True,
        features=frozenset({"browser", "tool_events"}),
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if "binary" in config and config["binary"] is not None:
            binary = config["binary"]
            if not isinstance(binary, str) or not binary.strip():
                errors.append("'binary' must be a non-empty string when provided")
        if "remote" in config and config["remote"] is not None:
            remote = config["remote"]
            if not isinstance(remote, str) or not remote.strip():
                errors.append("'remote' must be a non-empty string when provided")
        if "timeout_seconds" in config and config["timeout_seconds"] is not None:
            timeout = config["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                errors.append("'timeout_seconds' must be a positive number when provided")
        return errors

    async def health(self) -> bool:
        try:
            result = await self._run_cli(["status"], {})
            return result.get("ok") is True
        except Exception:
            return False

    @classmethod
    def is_available(cls, binary: str = "bbx") -> bool:
        return bool(os.environ.get("BBX_BINARY")) or shutil.which(binary) is not None

    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        errors = self.validate_config(task.config or {})
        if errors:
            yield event_error(task.task_id, "; ".join(errors), error_type="ValueError")
            return

        yield event_started(task.task_id)
        try:
            if task.workflow in _LIST_WORKFLOWS:
                async for event in self._invoke_list_tools(task):
                    yield event
                return
            if task.workflow in _HEALTH_WORKFLOWS:
                async for event in self._invoke_health(task):
                    yield event
                return
            if task.workflow in _DOUBAO_WORKFLOWS:
                async for event in self._invoke_doubao_browser(task):
                    yield event
                return
            async for event in self._invoke_call_tool(task):
                yield event
        except RuntimeInvocationError as exc:
            yield event_error(
                task.task_id,
                str(exc),
                error_type=exc.error_type or type(exc).__name__,
            )
        except TimeoutError as exc:
            yield event_error(task.task_id, f"BBX request timed out: {exc}", type(exc).__name__)
        except Exception as exc:
            yield event_error(task.task_id, f"BBX adapter failed: {exc}", type(exc).__name__)

    async def _invoke_list_tools(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        yield event_tool_call(task.task_id, "bbx_list_tools", args={})
        result = await self._run_cli(["skill"], task.config)
        methods = result.get("methods")
        if not isinstance(methods, dict):
            raise RuntimeInvocationError("BBX skill response did not contain methods", "ValueError")
        yield event_tool_result(task.task_id, "bbx_list_tools", result=result)
        yield event_done(task.task_id, result=result)

    async def _invoke_health(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        yield event_tool_call(task.task_id, "bbx_health", args={})
        result = await self._run_cli(["status"], task.config)
        yield event_tool_result(
            task.task_id,
            "bbx_health",
            result=result,
            is_error=result.get("ok") is not True,
        )
        if result.get("ok") is not True:
            yield event_error(
                task.task_id,
                _result_error_message(result, "BBX is not ready"),
                error_type="BbxHealthError",
            )
            return
        yield event_done(task.task_id, result={"health": result})

    async def _invoke_call_tool(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        tool_name, tab_id, params = _tool_call_request(task)
        if not tool_name:
            yield event_error(
                task.task_id,
                'BBX tool call requires input.tool/name or workflow="<method_name>"',
                error_type="ValueError",
            )
            return

        arguments = {"params": params, **({"tabId": tab_id} if tab_id is not None else {})}
        yield event_tool_call(task.task_id, tool_name, args=arguments)
        args = ["call"]
        if tab_id is not None:
            args.extend(["--tab", str(tab_id)])
        args.extend([tool_name, json.dumps(params, separators=(",", ":"), ensure_ascii=False)])
        result = await self._run_cli(args, task.config)
        is_error = result.get("ok") is False
        yield event_tool_result(
            task.task_id,
            tool_name,
            result=result,
            is_error=is_error,
        )
        if is_error:
            yield event_error(
                task.task_id,
                _result_error_message(result, f"BBX method {tool_name!r} failed"),
                error_type="BbxToolError",
            )
            return
        yield event_done(task.task_id, result={"tool": tool_name, "result": result})

    async def _invoke_doubao_browser(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        """Run the bounded Gaojixing browser flow through the local BBX daemon.

        This is intentionally a deterministic browser protocol, rather than a
        prompt sent to another model: the question is filled into the same
        persistent Chrome profile that noVNC exposes, and the final visible
        page plus observed links/share metadata are returned as one structured
        response for the center-side evidence mapper.
        """
        question = _question_from_task(task)
        if not question:
            yield event_error(
                task.task_id,
                "Doubao browser workflow requires input.question or input.message",
                error_type="ValueError",
            )
            return

        # Isolate every source item in a fresh conversation. Reusing the
        # active tab can mix a previous answer into the next page snapshot.
        selected = None
        created_new = False
        try:
            created = await self._doubao_call(
                task,
                "call",
                None,
                {"method": "tabs.create", "params": {"url": _DOUBAO_URL}},
            )
            selected = _tab_from_result(created)
            if selected is not None:
                created_new = True
                selected["origin"] = _DOUBAO_URL
                await self._wait_for_tab_url(task, selected["tab_id"])
        except RuntimeInvocationError:
            tabs = await self._doubao_call(task, "tabs", None, {})
            records = _tab_records(tabs)
            selected = _select_doubao_tab(records) or _active_tab(records)
        if selected is None:
            raise RuntimeInvocationError(
                "BBX could not find or create a browser tab for Doubao",
                "DoubaoTabUnavailable",
            )

        tab_id = selected["tab_id"]
        if not _is_doubao_tab(selected):
            await self._doubao_call(
                task,
                "call",
                tab_id,
                {"method": "navigation.navigate", "params": {"url": _DOUBAO_URL}},
            )
            await self._wait_for_tab_url(task, tab_id)
        elif not created_new:
            await self._doubao_call(task, "tab-activate", tab_id, {})

        # A newly created tab can report a complete URL before Doubao has
        # mounted its editor. Wait for the actual editable node instead of
        # treating that short navigation window as a missing login session.
        input_ref = await self._wait_for_doubao_input_ref(task, tab_id)
        if input_ref is None:
            page_text = await self._doubao_call(
                task,
                "call",
                tab_id,
                {"method": "page.get_text", "params": {"textBudget": 1200}},
            )
            visible_text = _page_text(page_text)
            login_required = _looks_like_doubao_login_page(visible_text)
            await self._close_created_doubao_tab(task, tab_id, created_new)
            for event in _drain_bbx_events(task):
                yield event
            yield event_done(
                task.task_id,
                result={
                    "text": json.dumps(
                        {
                            "status": "blocked",
                            "error_type": (
                                "doubao_login_required"
                                if login_required
                                else "doubao_input_unavailable"
                            ),
                            "message": (
                                "Doubao login session is unavailable; please log in through noVNC."
                                if login_required
                                else (
                                    "Doubao page loaded, but its input editor did not become "
                                    "available."
                                )
                            ),
                            "answer": "",
                            "data": [],
                            "links": [],
                            "conversation_url": "",
                            "session_share_data": [],
                            "suggested_keywords": [],
                            "page_text": visible_text,
                        },
                        ensure_ascii=False,
                    )
                },
            )
            return

        input_ref = await self._fill_doubao_input(task, tab_id, input_ref, question)
        send_dom = await self._doubao_call(
            task,
            "call",
            tab_id,
            {
                "method": "dom.query",
                "params": {
                    "selector": "#flow-end-msg-send",
                    "maxNodes": 5,
                    "maxDepth": 2,
                    "textBudget": 100,
                },
            },
        )
        send_ref = _clickable_ref(send_dom)
        if send_ref is not None:
            try:
                await self._doubao_call(
                    task,
                    "call",
                    tab_id,
                    {
                        "method": "input.click",
                        "params": {"target": {"elementRef": send_ref}},
                    },
                )
            except RuntimeInvocationError as exc:
                if not _is_stale_element_error(exc):
                    raise
                await asyncio.sleep(0.25)
                retry_dom = await self._doubao_call(
                    task,
                    "call",
                    tab_id,
                    {
                        "method": "dom.query",
                        "params": {
                            "selector": "#flow-end-msg-send",
                            "maxNodes": 5,
                            "maxDepth": 2,
                            "textBudget": 100,
                        },
                    },
                )
                retry_ref = _clickable_ref(retry_dom)
                if retry_ref is not None:
                    await self._doubao_call(
                        task,
                        "call",
                        tab_id,
                        {
                            "method": "input.click",
                            "params": {"target": {"elementRef": retry_ref}},
                        },
                    )
                else:
                    input_ref = await self._query_doubao_input_ref(task, tab_id) or input_ref
                    await self._press_doubao_enter(task, tab_id, input_ref)
        else:
            # Some Doubao layouts omit the send button while composing. Enter
            # remains a safe fallback for those layouts.
            await self._press_doubao_enter(task, tab_id, input_ref)

        settle_seconds = _nonnegative_number(task.config.get("settle_seconds"), 5.0)
        if settle_seconds:
            await asyncio.sleep(settle_seconds)
        # Deep-research answers can continue rendering after the initial
        # settle period.  Keep the browser task bounded, but do not discard a
        # visible final answer merely because Doubao needed more than a short
        # chat response window.
        response_timeout = _nonnegative_number(
            task.config.get("response_timeout_seconds"), 180.0
        )
        deadline = asyncio.get_running_loop().time() + response_timeout
        page_text_result: dict[str, Any] = {}
        state: dict[str, Any] = {}
        extracted: dict[str, Any] = {}
        value: dict[str, Any] = {}
        answer_tail = ""
        stable_answer = ""
        stable_observations = 0
        required_stable_observations = max(
            1, int(_nonnegative_number(task.config.get("stable_observations"), 2.0))
        )
        answer_complete = False
        while True:
            page_text_result = await self._doubao_call(
                task,
                "call",
                tab_id,
                {"method": "page.get_text", "params": {"textBudget": 100000}},
            )
            state = await self._doubao_call(
                task,
                "call",
                tab_id,
                {"method": "page.get_state", "params": {}},
            )
            extracted = await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "page.evaluate",
                    "params": {
                        "expression": _DOUBAO_EXTRACTION_EXPRESSION,
                        "returnByValue": True,
                    },
                },
            )
            value = extracted.get("value") if isinstance(extracted, dict) else {}
            value = value if isinstance(value, dict) else {}
            answer_tail = _answer_after_question(_page_text(page_text_result), question)
            candidate = _select_doubao_answer(value.get("answer"), answer_tail)
            if value.get("answer_complete") is True and _answer_ready(
                candidate, answer_tail, question
            ):
                if candidate == stable_answer:
                    stable_observations += 1
                else:
                    stable_answer = candidate
                    stable_observations = 1
                if stable_observations >= required_stable_observations:
                    answer_complete = True
                    break
            else:
                stable_answer = ""
                stable_observations = 0
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(2.0, remaining))

        page_text = _page_text(page_text_result)
        value = extracted.get("value") if isinstance(extracted, dict) else None
        value = value if isinstance(value, dict) else {}
        if answer_complete:
            expanded = await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "page.evaluate",
                    "params": {
                        "expression": _DOUBAO_EXPAND_SOURCES_EXPRESSION,
                        "returnByValue": True,
                    },
                },
            )
            if _evaluate_bool(expanded) is True:
                await asyncio.sleep(0.35)
                extracted = await self._doubao_call(
                    task,
                    "call",
                    tab_id,
                    {
                        "method": "page.evaluate",
                        "params": {
                            "expression": _DOUBAO_EXTRACTION_EXPRESSION,
                            "returnByValue": True,
                        },
                    },
                )
                refreshed = extracted.get("value") if isinstance(extracted, dict) else {}
                if isinstance(refreshed, dict):
                    value = refreshed
        suggestion_deadline = asyncio.get_running_loop().time() + _nonnegative_number(
            task.config.get("suggested_wait_seconds"), 5.0
        )
        suggested_keywords = _string_list(value.get("suggested_keywords"))
        while not suggested_keywords:
            remaining = suggestion_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(1.0, remaining))
            extracted = await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "page.evaluate",
                    "params": {
                        "expression": _DOUBAO_EXTRACTION_EXPRESSION,
                        "returnByValue": True,
                    },
                },
            )
            value = extracted.get("value") if isinstance(extracted, dict) else {}
            value = value if isinstance(value, dict) else {}
            suggested_keywords = _string_list(value.get("suggested_keywords"))
        answer_tail = _answer_after_question(page_text, question)
        conversation_url = _string(state.get("url")) or _string(value.get("conversation_url")) or ""
        links = _links(value.get("links"))
        share_data = value.get("session_share_data")
        if not share_data and conversation_url:
            share_data = {"url": conversation_url, "type": "conversation"}
        answer = _select_doubao_answer(value.get("answer"), answer_tail)
        answer = _remove_suggested_keyword_tail(answer, suggested_keywords)
        if not answer_complete or not _answer_ready(answer, answer_tail, question):
            response = {
                "status": "blocked",
                "error_type": "doubao_response_incomplete",
                "message": "Doubao did not expose a complete final answer before timeout.",
                "answer": "",
                "answer_complete": False,
                "data": [],
                "links": links,
                "conversation_url": conversation_url,
                "session_share_data": share_data or [],
                "suggested_keywords": suggested_keywords,
                "search_keywords": _string_list(value.get("search_keywords")),
                "video_contents": _string_list(value.get("video_contents")),
                "page_text": page_text,
                "conversation_deleted": False,
            }
            for event in _drain_bbx_events(task):
                yield event
            yield event_evidence(
                task.task_id,
                {
                    "kind": "doubao.capture.pre_cleanup",
                    "response": dict(response),
                },
            )
            response["conversation_deleted"] = await self._delete_doubao_conversation(
                task, tab_id, created_new, conversation_url
            )
            await self._close_created_doubao_tab(task, tab_id, created_new)
            for event in _drain_bbx_events(task):
                yield event
            yield event_done(
                task.task_id,
                result={
                    "text": json.dumps(response, ensure_ascii=False),
                    "tab_id": tab_id,
                    "browser_transport": "bbx",
                },
            )
            return
        response = {
            "status": "completed",
            "answer": answer,
            "answer_complete": True,
            "data": value.get("data") if isinstance(value.get("data"), list) else [],
            "links": links,
            "conversation_url": conversation_url,
            "session_share_data": share_data or [],
            "suggested_keywords": suggested_keywords,
            "search_keywords": _string_list(value.get("search_keywords")),
            "video_contents": _string_list(value.get("video_contents")),
            "search_keyword_count": value.get("search_keyword_count"),
            "reference_count": value.get("reference_count"),
            "conversation_deleted": False,
        }
        for event in _drain_bbx_events(task):
            yield event
        yield event_evidence(
            task.task_id,
            {
                "kind": "doubao.capture.pre_cleanup",
                "response": dict(response),
            },
        )
        response["conversation_deleted"] = await self._delete_doubao_conversation(
            task, tab_id, created_new, conversation_url
        )
        await self._close_created_doubao_tab(task, tab_id, created_new)
        for event in _drain_bbx_events(task):
            yield event
        yield event_done(
            task.task_id,
            result={
                "text": json.dumps(response, ensure_ascii=False),
                "tab_id": tab_id,
                "browser_transport": "bbx",
            },
        )

    async def _query_doubao_input_ref(
        self, task: AgentTask, tab_id: int | str
    ) -> str | None:
        dom = await self._doubao_call(
            task,
            "call",
            tab_id,
            {
                "method": "dom.query",
                "params": {
                    "selector": _DOUBAO_INPUT_SELECTOR,
                    "maxNodes": 20,
                    "maxDepth": 3,
                    "textBudget": 200,
                },
            },
        )
        return _editable_ref(dom)

    async def _close_created_doubao_tab(
        self, task: AgentTask, tab_id: int | str, created_new: bool
    ) -> None:
        """Close only tabs opened for this item; preserve the user's session tab."""
        if not created_new:
            return
        try:
            await self._doubao_call(
                task,
                "call",
                None,
                {"method": "tabs.close", "params": {"tabId": tab_id}},
            )
        except RuntimeInvocationError:
            # Cleanup must never turn an otherwise valid Doubao answer into a
            # failed collection item. The next run can still recover the tab.
            return

    async def _delete_doubao_conversation(
        self,
        task: AgentTask,
        tab_id: int | str,
        created_new: bool,
        conversation_url: str,
    ) -> bool:
        """Delete only the temporary conversation created for this source item."""
        conversation_id = _doubao_conversation_id(conversation_url)
        if not created_new or not conversation_id:
            return False
        try:
            opened: dict[str, Any] = {}
            open_deadline = asyncio.get_running_loop().time() + _nonnegative_number(
                task.config.get("delete_menu_timeout_seconds"), 3.0
            )
            while True:
                opened = await self._doubao_call(
                    task,
                    "call",
                    tab_id,
                    {
                        "method": "page.evaluate",
                        "params": {
                            "expression": _DOUBAO_OPEN_DELETE_MENU_EXPRESSION,
                            "returnByValue": True,
                        },
                    },
                )
                if _evaluate_bool(opened) is True:
                    break
                remaining = open_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                await asyncio.sleep(min(0.2, remaining))
            clicked = await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "page.evaluate",
                    "params": {
                        "expression": _DOUBAO_CLICK_DELETE_MENU_EXPRESSION,
                        "returnByValue": True,
                    },
                },
            )
            if _evaluate_bool(clicked) is not True:
                return False
            confirmed = await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "page.evaluate",
                    "params": {
                        "expression": _DOUBAO_CONFIRM_DELETE_EXPRESSION,
                        "returnByValue": True,
                    },
                },
            )
            if _evaluate_bool(confirmed) is not True:
                return False
            verification_expression = _doubao_verify_delete_expression(conversation_id)
            verify_deadline = asyncio.get_running_loop().time() + _nonnegative_number(
                task.config.get("delete_verify_timeout_seconds"), 5.0
            )
            required_observations = max(
                1,
                int(
                    _nonnegative_number(
                        task.config.get("delete_stable_observations"), 2.0
                    )
                ),
            )
            stable_observations = 0
            while True:
                verified = await self._doubao_call(
                    task,
                    "call",
                    tab_id,
                    {
                        "method": "page.evaluate",
                        "params": {
                            "expression": verification_expression,
                            "returnByValue": True,
                        },
                    },
                )
                if _evaluate_bool(verified) is True:
                    stable_observations += 1
                    if stable_observations >= required_observations:
                        return True
                else:
                    stable_observations = 0
                remaining = verify_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                await asyncio.sleep(min(0.15, remaining))
        except RuntimeInvocationError:
            return False

    async def _wait_for_doubao_input_ref(
        self, task: AgentTask, tab_id: int | str
    ) -> str | None:
        deadline = asyncio.get_running_loop().time() + _nonnegative_number(
            task.config.get("input_wait_seconds"), 30.0
        )
        while True:
            try:
                input_ref = await self._query_doubao_input_ref(task, tab_id)
            except RuntimeInvocationError as exc:
                # The DOM can be replaced while Doubao mounts its editor. A
                # stale reference is transient; keep polling the page instead
                # of surfacing it as a login or collection failure.
                if not _is_stale_element_error(exc):
                    raise
                input_ref = None
            if input_ref is not None:
                return input_ref
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(0.5, remaining))

    async def _fill_doubao_input(
        self,
        task: AgentTask,
        tab_id: int | str,
        input_ref: str,
        question: str,
    ) -> str:
        try:
            await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "input.fill",
                    "params": {"target": {"elementRef": input_ref}, "value": question},
                },
            )
        except RuntimeInvocationError as exc:
            if not _is_stale_element_error(exc):
                raise
            await asyncio.sleep(0.25)
            fresh_ref = await self._query_doubao_input_ref(task, tab_id)
            if fresh_ref is None:
                raise
            await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "input.fill",
                    "params": {"target": {"elementRef": fresh_ref}, "value": question},
                },
            )
            return fresh_ref
        return input_ref

    async def _press_doubao_enter(
        self, task: AgentTask, tab_id: int | str, input_ref: str
    ) -> None:
        try:
            await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "input.press_key",
                    "params": {"target": {"elementRef": input_ref}, "key": "ENTER"},
                },
            )
        except RuntimeInvocationError as exc:
            if not _is_stale_element_error(exc):
                raise
            await asyncio.sleep(0.25)
            fresh_ref = await self._query_doubao_input_ref(task, tab_id)
            if fresh_ref is None:
                raise
            await self._doubao_call(
                task,
                "call",
                tab_id,
                {
                    "method": "input.press_key",
                    "params": {"target": {"elementRef": fresh_ref}, "key": "ENTER"},
                },
            )

    async def _wait_for_tab_url(self, task: AgentTask, tab_id: int | str) -> dict[str, Any]:
        """Wait for BBX to materialize a newly-created tab before DOM access."""
        deadline = asyncio.get_running_loop().time() + 15.0
        last_error: RuntimeInvocationError | None = None
        while True:
            try:
                state = await self._doubao_call(
                    task,
                    "call",
                    tab_id,
                    {"method": "page.get_state", "params": {}},
                )
            except RuntimeInvocationError as exc:
                last_error = exc
            else:
                if _string(state.get("url")):
                    return state
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if last_error is not None:
                    raise last_error
                raise RuntimeInvocationError(
                    "BBX tab did not expose a URL before timeout",
                    "DoubaoTabUnavailable",
                )
            await asyncio.sleep(min(0.5, remaining))

    async def _doubao_call(
        self,
        task: AgentTask,
        command: str,
        tab_id: int | str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call one BBX method and emit normalized tool events."""
        if command == "tabs":
            method = "tabs.list"
            params: dict[str, Any] = {}
            args = ["tabs"]
        elif command in {"tab-activate", "call"}:
            method = str(payload.get("method") or command)
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            args = [command]
            if command == "tab-activate":
                method = "tabs.activate"
                if tab_id is None:
                    raise RuntimeInvocationError(
                        "BBX tab activation requires a tab id", "ValueError"
                    )
                args.append(str(tab_id))
            elif tab_id is not None:
                args.extend(["--tab", str(tab_id)])
            if command == "call":
                args.extend([method, json.dumps(params, separators=(",", ":"), ensure_ascii=False)])
            elif params:
                args.append(json.dumps(params, separators=(",", ":"), ensure_ascii=False))
        else:  # pragma: no cover - private helper guard
            raise RuntimeInvocationError(f"Unsupported Doubao BBX command: {command}", "ValueError")

        arguments = {"tabId": tab_id, "params": params} if tab_id is not None else params
        # The helper cannot yield, so callers receive tool events through a
        # lightweight side channel attached to the task config.
        task_events = task.config.setdefault("_bbx_events", [])
        task_events.append(event_tool_call(task.task_id, method, args=arguments))
        result = await self._run_cli(args, task.config)
        task_events.append(
            event_tool_result(
                task.task_id,
                method,
                result=result,
                is_error=result.get("ok") is False,
            )
        )
        if result.get("ok") is False:
            raise RuntimeInvocationError(
                _result_error_message(result, f"BBX method {method!r} failed"),
                "BbxToolError",
            )
        return result

    async def _run_cli(
        self,
        args: list[str],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        binary = _resolve_binary(config)
        if binary is None:
            raise RuntimeInvocationError("BBX CLI was not found on PATH", "FileNotFoundError")
        command = [binary]
        remote = _read_optional_string(config.get("remote"))
        if remote:
            command.extend(["--remote", remote])
        command.extend(args)

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=_timeout_seconds(config),
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        output = stdout.decode("utf-8", errors="replace").strip()
        error_output = stderr.decode("utf-8", errors="replace").strip()
        if not output:
            raise RuntimeInvocationError(
                f"BBX returned no JSON output: {error_output[:500]}",
                "BbxCliError",
            )
        try:
            payload = json.loads(output)
        except ValueError as exc:
            raise RuntimeInvocationError(
                f"BBX returned invalid JSON: {output[:500]}",
                "ValueError",
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeInvocationError("BBX response was not an object", "ValueError")
        if process.returncode not in {0, None} and payload.get("ok") is not False:
            raise RuntimeInvocationError(
                error_output or f"BBX exited with status {process.returncode}",
                "BbxCliError",
            )
        return payload


_DOUBAO_EXTRACTION_EXPRESSION = r'''(() => {
  const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const answers = Array.from(document.querySelectorAll('.md-box-root'));
  const answerNode = answers.length ? answers[answers.length - 1] : null;
  let root = answerNode;
  for (let depth = 0; root && depth < 10; depth += 1) {
    const hasResultNodes = root.querySelector(
      '.suggest-list-item-title, [data-plugin-identifier*="search_query_result_block"]'
    );
    if (hasResultNodes) break;
    root = root.parentElement;
  }
  root = root || answerNode;
  let actionRoot = answerNode;
  for (let depth = 0; actionRoot && depth < 10; depth += 1) {
    const hasFinalAction = actionRoot.querySelector(
      'button[aria-label*="朗读"], button[aria-label*="复制"]'
    );
    const hasGeneratingAction = Array.from(
      actionRoot.querySelectorAll('button, [role="button"]')
    ).some((node) => /停止生成|停止回答|停止思考|生成中/.test(
      compact(node.getAttribute('aria-label') || node.textContent)
    ));
    if (hasFinalAction || hasGeneratingAction) break;
    actionRoot = actionRoot.parentElement;
  }
  actionRoot = actionRoot || root;
  const suggested_keywords = root ? Array.from(root.querySelectorAll('.suggest-list-item-title'))
    .map((node) => compact(node.innerText || node.textContent))
    .filter((value, index, values) => value && values.indexOf(value) === index)
    .slice(0, 20) : [];
  const search_keywords = root ? Array.from(root.querySelectorAll(
    '[data-plugin-identifier*="search_query_result_block"] [class*="query"], ' +
    '[data-plugin-identifier*="search_query_result_block"] [class*="keyword"]'
  )).map((node) => compact(node.innerText || node.textContent))
    .filter((value, index, values) => value && values.indexOf(value) === index)
    .slice(0, 50) : [];
  const video_contents = root ? Array.from(root.querySelectorAll(
    '[data-plugin-identifier*="video"], a[href*="douyin.com/video"], ' +
    'a[href*="douyin.com/note"]'
  )).map((node) => {
    const container = node.closest('[data-plugin-identifier], article, li') || node;
    return compact(container.innerText || container.textContent || node.getAttribute('aria-label'));
  }).filter((value, index, values) => value && values.indexOf(value) === index)
    .slice(0, 20) : [];
  const links = root ? Array.from(root.querySelectorAll('a[href]'))
    .map((node) => ({url: node.href, title: compact(node.innerText || node.textContent)}))
    .filter((item) => /^https?:/i.test(item.url) &&
      !/^https:\/\/(?:www\.)?doubao\.com(?:\/|$)/i.test(item.url))
    .filter((item, index, values) =>
      values.findIndex((other) => other.url === item.url) === index
    ) : [];
  const rootText = compact(root?.innerText || root?.textContent);
  const summary = rootText.match(/搜索\s*(\d+)\s*个关键词，参考\s*(\d+)\s*篇资料/);
  const is_generating = Boolean(actionRoot && Array.from(
    actionRoot.querySelectorAll('button, [role="button"]')
  ).some((node) => /停止生成|停止回答|停止思考|生成中/.test(
    compact(node.getAttribute('aria-label') || node.textContent)
  )));
  const has_final_actions = Boolean(actionRoot && actionRoot.querySelector(
    'button[aria-label*="朗读"], button[aria-label*="复制"]'
  ));
  const answer = String(answerNode?.innerText || answerNode?.textContent || '').trim();
  return {
    answer,
    answer_complete: Boolean(answer && !is_generating &&
      (suggested_keywords.length || has_final_actions)),
    is_generating,
    links,
    suggested_keywords,
    search_keywords,
    video_contents,
    search_keyword_count: summary ? Number(summary[1]) : null,
    reference_count: summary ? Number(summary[2]) : null,
    conversation_url: location.href,
    session_share_data: []
  };
})()'''

_DOUBAO_EXPAND_SOURCES_EXPRESSION = r'''(() => {
  const blocks = Array.from(document.querySelectorAll(
    '[data-plugin-identifier*="search_query_result_block"] [data-copy-ignore]'
  ));
  const trigger = blocks.length ? blocks[blocks.length - 1] : null;
  if (!trigger) return false;
  const text = String(trigger.textContent || '').replace(/\s+/g, ' ').trim();
  if (!/搜索\s*\d+\s*个关键词，参考\s*\d+\s*篇资料/.test(text)) return false;
  trigger.click();
  return true;
})()'''

_DOUBAO_OPEN_DELETE_MENU_EXPRESSION = r'''(() => {
  const match = location.pathname.match(/^\/chat\/([^/?#]+)/);
  if (!match) return false;
  const item = document.querySelector(`a#conversation_${match[1]}`) ||
    document.querySelector('a[id^="conversation_"][class*="e2e-test-active"]');
  const trigger = item?.querySelector('button[aria-haspopup="menu"]');
  if (!trigger) return false;
  let holder = trigger.parentElement;
  while (holder && holder !== item &&
      !String(holder.className || '').includes('group-hover/conversation-item')) {
    holder = holder.parentElement;
  }
  if (!holder || holder === item) return false;
  holder.style.display = 'flex';
  holder.style.opacity = '1';
  holder.style.visibility = 'visible';
  const init = {bubbles: true, cancelable: true, composed: true, view: window,
    clientX: 0, clientY: 0, button: 0, buttons: 1};
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    trigger.dispatchEvent(new MouseEvent(type, init));
  }
  return trigger.getAttribute('aria-expanded') === 'true';
})()'''

_DOUBAO_CLICK_DELETE_MENU_EXPRESSION = r'''(() => {
  const item = Array.from(document.querySelectorAll('[role="menuitem"]'))
    .find((node) => String(node.textContent || '').trim() === '删除' &&
      node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0);
  if (!item) return false;
  item.click();
  return true;
})()'''

_DOUBAO_CONFIRM_DELETE_EXPRESSION = r'''(() => {
  const dialog = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
    .find((node) => String(node.textContent || '').includes('确定删除对话'));
  const button = dialog && Array.from(dialog.querySelectorAll('button'))
    .find((node) => String(node.textContent || '').trim() === '删除');
  if (!button) return false;
  button.click();
  return true;
})()'''


def _doubao_conversation_id(conversation_url: str) -> str | None:
    try:
        parsed = urlsplit(conversation_url)
    except ValueError:
        return None
    if (parsed.hostname or "").casefold().rstrip(".") not in {
        "doubao.com",
        "www.doubao.com",
    }:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    return parts[1] if len(parts) == 2 and parts[0] == "chat" and parts[1] else None


def _doubao_verify_delete_expression(conversation_id: str) -> str:
    encoded_id = json.dumps(conversation_id, ensure_ascii=False)
    return rf'''(() => {{
  const conversationId = {encoded_id};
  const dialog = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
    .find((node) => String(node.textContent || '').includes('确定删除对话'));
  if (dialog) return false;
  if (document.getElementById(`conversation_${{conversationId}}`)) return false;
  return !Array.from(document.querySelectorAll('a[href]')).some((node) => {{
    try {{
      return decodeURIComponent(new URL(node.getAttribute('href'), location.href).pathname)
        .replace(/\/$/, '') === `/chat/${{conversationId}}`;
    }} catch (_error) {{
      return false;
    }}
  }});
}})()'''


def _question_from_task(task: AgentTask) -> str:
    payload = task.input if isinstance(task.input, dict) else {}
    for key in ("question", "query", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().split("\n")[-1].strip() if key == "message" else value.strip()
    return ""


def _tab_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("evidence", "tabs", "items"):
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _evaluate_bool(result: dict[str, Any]) -> bool | None:
    value = result.get("value") if isinstance(result, dict) else None
    return value if isinstance(value, bool) else None


def _tab_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    records = _tab_records(result)
    if records:
        return _normalize_tab(records[0])
    raw_id = result.get("tabId", result.get("tab_id"))
    if isinstance(raw_id, (int, str)) and str(raw_id).strip():
        return {"tab_id": raw_id, "active": True, "origin": ""}
    return None


def _select_doubao_tab(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in records:
        normalized = _normalize_tab(record)
        if _is_doubao_tab(normalized):
            return normalized
    return None


def _active_tab(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in records:
        normalized = _normalize_tab(record)
        if normalized.get("active") and _is_automatable_tab(normalized):
            return normalized
    for record in records:
        normalized = _normalize_tab(record)
        if _is_automatable_tab(normalized):
            return normalized
    return None


def _normalize_tab(record: dict[str, Any]) -> dict[str, Any]:
    raw_id = record.get("tabId", record.get("tab_id"))
    return {
        "tab_id": raw_id,
        "active": record.get("active") is True,
        "origin": _string(record.get("origin")) or _string(record.get("url")) or "",
        "title": _string(record.get("title")) or "",
    }


def _is_doubao_tab(tab: dict[str, Any]) -> bool:
    return "doubao.com" in (_string(tab.get("origin")) or "").lower()


def _is_automatable_tab(tab: dict[str, Any]) -> bool:
    """Exclude browser-owned pages that Browser Bridge cannot script."""
    origin = (_string(tab.get("origin")) or "").lower()
    return not origin.startswith(
        ("about:", "chrome:", "chrome-extension:", "chrome-search:", "devtools:", "edge:")
    )


def _editable_ref(result: dict[str, Any]) -> str | None:
    for node in result.get("nodes", []) if isinstance(result, dict) else []:
        if not isinstance(node, dict):
            continue
        ref = _string(node.get("elementRef"))
        tag = (_string(node.get("tag")) or "").lower()
        role = (_string(node.get("role")) or "").lower()
        if ref and (tag in {"textarea", "input"} or role == "textbox"):
            return ref
    return None


def _clickable_ref(result: dict[str, Any]) -> str | None:
    for node in result.get("nodes", []) if isinstance(result, dict) else []:
        if not isinstance(node, dict):
            continue
        ref = _string(node.get("elementRef"))
        tag = (_string(node.get("tag")) or "").lower()
        role = (_string(node.get("role")) or "").lower()
        if ref and (tag == "button" or role == "button"):
            return ref
    return None


def _is_stale_element_error(error: RuntimeInvocationError) -> bool:
    return "stale" in str(error).casefold()


def _looks_like_doubao_login_page(page_text: str) -> bool:
    """Recognize explicit login UI without treating an empty editor as login."""
    normalized = " ".join(page_text.split())
    return any(
        marker in normalized
        for marker in (
            "手机号登录",
            "扫码登录",
            "登录/注册",
            "验证码登录",
            "请先登录",
            "请登录后",
        )
    )


def _clean_doubao_visible_text(value: str) -> str:
    text = value.strip()
    footer = re.search(r"(?m)^\s*对话\s*$", text)
    return text[: footer.start()].strip() if footer else text


def _has_answer_content(value: str) -> bool:
    normalized = " ".join(value.split())
    if not normalized:
        return False
    if normalized in {"正在思考", "正在生成", "生成中", "思考中"}:
        return False
    if re.fullmatch(r"搜索\s*\d+\s*个关键词，参考\s*\d+\s*篇资料", normalized):
        return False
    if re.fullmatch(
        r"(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])?\s*\d{1,2}:\d{2}",
        normalized,
    ):
        return False
    return len(normalized) > 5


def _select_doubao_answer(answer: object, answer_tail: str) -> str:
    """Prefer the assistant node extracted by BBX over page chrome."""
    extracted = _clean_doubao_visible_text(_string(answer) or "")
    if _has_answer_content(extracted):
        return extracted
    fallback = _clean_doubao_visible_text(answer_tail)
    return fallback if _has_answer_content(fallback) else ""


def _answer_after_question(page_text: str, question: str) -> str:
    if not page_text or not question:
        return ""
    position = page_text.rfind(question)
    if position >= 0:
        after = _clean_doubao_visible_text(page_text[position + len(question) :])
        if _has_answer_content(after):
            return after

    # Doubao may insert whitespace inside a submitted term such as ``DHA``.
    # Match the last whitespace-tolerant occurrence before extracting the tail.
    compact_question = [char for char in question if not char.isspace()]
    if not compact_question:
        return ""
    pattern = r"\s*".join(re.escape(char) for char in compact_question)
    matches = list(re.finditer(pattern, page_text))
    if matches:
        after = _clean_doubao_visible_text(page_text[matches[-1].end() :])
        if _has_answer_content(after):
            return after

    # Doubao may render the submitted question after the assistant message.
    # In that layout keep only the answer body and remove the app chrome.
    before = page_text[:position].strip()
    marker = before.rfind("豆包 快速")
    fallback = before[marker + len("豆包 快速") :].strip() if marker >= 0 else ""
    return fallback if _has_answer_content(fallback) else ""


def _answer_ready(answer: str | None, answer_tail: str, question: str) -> bool:
    question_text = " ".join(question.split())
    candidate = _clean_doubao_visible_text(answer or "")
    tail = _clean_doubao_visible_text(answer_tail)
    if _has_answer_content(tail):
        return True
    return bool(_has_answer_content(candidate) and " ".join(candidate.split()) != question_text)


def _suggested_keywords_from_page_text(page_text: str) -> list[str]:
    visible = _clean_doubao_visible_text(page_text)
    suggestions: list[str] = []
    for line in reversed([item.strip() for item in visible.splitlines() if item.strip()]):
        if len(line) <= 120 and line.endswith(("？", "?")):
            suggestions.append(line)
            continue
        if suggestions:
            break
    return list(reversed(suggestions[-20:])) if len(suggestions) >= 2 else []


def _remove_suggested_keyword_tail(answer: str, suggested_keywords: list[str]) -> str:
    if not answer or not suggested_keywords:
        return answer
    suggestions = set(suggested_keywords)
    lines = answer.splitlines()
    while lines and lines[-1].strip() in suggestions:
        lines.pop()
    return "\n".join(lines).strip()


def _page_text(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    value = result.get("text", result.get("value"))
    return value.strip() if isinstance(value, str) else ""


def _links(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    links: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            links.append({"url": item})
        elif isinstance(item, dict):
            url = _string(item.get("url")) or _string(item.get("href"))
            if url and url.startswith(("http://", "https://")):
                link = {"url": url}
                title = _string(item.get("title"))
                if title:
                    link["title"] = title
                links.append(link)
    return links


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return [value.strip()] if isinstance(value, str) and value.strip() else []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _nonnegative_number(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return default


def _drain_bbx_events(task: AgentTask) -> list[dict[str, Any]]:
    events = task.config.pop("_bbx_events", [])
    return events if isinstance(events, list) else []


def _resolve_binary(config: dict[str, Any]) -> str | None:
    configured = _read_optional_string(config.get("binary")) or os.environ.get("BBX_BINARY")
    if configured:
        return configured
    return shutil.which("bbx.cmd") or shutil.which("bbx")


def _timeout_seconds(config: dict[str, Any]) -> float:
    raw = config.get("timeout_seconds")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return _DEFAULT_TIMEOUT_SECONDS


def _tool_call_request(
    task: AgentTask,
) -> tuple[str, int | None, dict[str, Any]]:
    payload = task.input if isinstance(task.input, dict) else {}
    workflow = _read_optional_string(task.workflow) or ""
    tool_name = (
        _read_optional_string(payload.get("tool"))
        or _read_optional_string(payload.get("name"))
        or ("" if workflow in _CALL_WORKFLOWS else workflow)
    )
    arguments = payload.get("arguments", payload.get("args"))
    arguments = arguments if isinstance(arguments, dict) else {}
    raw_tab_id = arguments.get("tabId", payload.get("tabId"))
    tab_id = (
        raw_tab_id
        if isinstance(raw_tab_id, int) and not isinstance(raw_tab_id, bool)
        else None
    )
    raw_params = arguments.get("params", payload.get("params"))
    if isinstance(raw_params, dict):
        params = dict(raw_params)
    else:
        params = {
            str(key): value
            for key, value in arguments.items()
            if key not in {"tabId", "params"}
        }
    return tool_name, tab_id, params


def _result_error_message(result: dict[str, Any], fallback: str) -> str:
    summary = _read_optional_string(result.get("summary"))
    if summary:
        return summary
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or fallback)
    return str(error) if error else fallback


def _read_optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = ["BbxRuntimeAdapter"]
