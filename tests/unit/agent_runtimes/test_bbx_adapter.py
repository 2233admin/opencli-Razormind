from __future__ import annotations

import json

import pytest

from backend.agent_runtimes.base import AgentTask
from backend.agent_runtimes.bbx_adapter import (
    _DOUBAO_CLICK_DELETE_MENU_EXPRESSION,
    _DOUBAO_CONFIRM_DELETE_EXPRESSION,
    _DOUBAO_EXTRACTION_EXPRESSION,
    _DOUBAO_OPEN_DELETE_MENU_EXPRESSION,
    BbxRuntimeAdapter,
    _active_tab,
    _answer_after_question,
    _doubao_conversation_id,
    _has_answer_content,
    _looks_like_doubao_login_page,
    _suggested_keywords_from_page_text,
)


def test_active_tab_ignores_browser_owned_pages():
    assert _active_tab([{"tabId": 1, "active": True, "origin": "chrome://extensions"}]) is None


def test_logged_in_doubao_page_is_not_misclassified_as_login_required():
    assert _looks_like_doubao_login_page(
        "豆包 - 字节跳动旗下 AI 智能助手\n有什么我能帮你的吗？"
    ) is False
    assert _looks_like_doubao_login_page("手机号登录\n扫码登录") is True


def test_doubao_capture_rejects_timestamp_chrome_and_targets_real_result_nodes():
    assert _has_answer_content("今天 08:05") is False
    assert ".md-box-root" in _DOUBAO_EXTRACTION_EXPRESSION
    assert ".suggest-list-item-title" in _DOUBAO_EXTRACTION_EXPRESSION
    assert "let actionRoot = answerNode;" in _DOUBAO_EXTRACTION_EXPRESSION
    assert "actionRoot.querySelector" in _DOUBAO_EXTRACTION_EXPRESSION
    assert "/推荐|继续问|猜你想问|相关问题|关键词/i" not in _DOUBAO_EXTRACTION_EXPRESSION


async def _collect(adapter: BbxRuntimeAdapter, task: AgentTask) -> list[dict]:
    return [event async for event in adapter.invoke(task)]


@pytest.mark.asyncio
async def test_list_tools_projects_bbx_runtime_methods(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run(self, args, config):
        calls.append(args)
        return {
            "v": "1.7",
            "methods": {
                "page": ["page.get_state", "page.get_text"],
                "inspect": ["dom.query"],
                "interact": ["input.click"],
            },
        }

    monkeypatch.setattr(BbxRuntimeAdapter, "_run_cli", fake_run)
    events = await _collect(
        BbxRuntimeAdapter(),
        AgentTask(task_id="bbx-list", workflow="tool.list"),
    )

    assert calls == [["skill"]]
    assert events[-1]["type"] == "done"
    assert events[-1]["result"]["methods"]["page"] == [
        "page.get_state",
        "page.get_text",
    ]


@pytest.mark.asyncio
async def test_call_tool_passes_tab_and_json_params_to_bbx(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run(self, args, config):
        calls.append(args)
        return {
            "ok": True,
            "summary": "Page text read.",
            "evidence": {"text": "OpenCLI"},
        }

    monkeypatch.setattr(BbxRuntimeAdapter, "_run_cli", fake_run)
    events = await _collect(
        BbxRuntimeAdapter(),
        AgentTask(
            task_id="bbx-call",
            workflow="tool.call",
            input={
                "tool": "page.get_text",
                "arguments": {"tabId": 27, "params": {"textBudget": 600}},
            },
        ),
    )

    assert calls == [
        [
            "call",
            "--tab",
            "27",
            "page.get_text",
            '{"textBudget":600}',
        ]
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["result"]["result"]["evidence"]["text"] == "OpenCLI"


@pytest.mark.asyncio
async def test_doubao_workflow_uses_bbx_browser_and_returns_structured_evidence(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run(self, args, config):
        calls.append(args)
        if args == ["call", "tabs.create", '{"url":"https://www.doubao.com/chat"}']:
            return {"ok": True, "tabId": 7}
        if args == ["call", "tabs.close", '{"tabId":7}']:
            return {"closed": True, "tabId": 7}
        if args[0:3] == ["call", "--tab", "7"]:
            method = args[3]
            if method == "dom.query":
                params = json.loads(args[4])
                if params["selector"] == "#flow-end-msg-send":
                    return {"nodes": [{"elementRef": "el_send", "tag": "button"}]}
                return {"nodes": [{"elementRef": "el_input", "tag": "textarea"}]}
            if method == "input.fill":
                return {"ok": True}
            if method == "input.click":
                return {"ok": True}
            if method == "page.get_state":
                return {"url": "https://www.doubao.com/chat/123", "title": "豆包"}
            if method == "page.get_text":
                return {"text": "问题 q\n答案 answer\n推荐问题 follow-up"}
            if method == "page.evaluate":
                return {
                    "value": {
                        "answer": "answer",
                        "answer_complete": True,
                        "data": [{"point": "value"}],
                        "links": [{"url": "https://example.test/source", "title": "source"}],
                        "suggested_keywords": ["follow-up"],
                        "session_share_data": {
                            "url": "https://www.doubao.com/chat/123",
                            "type": "conversation",
                        },
                    }
                }
        raise AssertionError(f"unexpected BBX call: {args}")

    monkeypatch.setattr(BbxRuntimeAdapter, "_run_cli", fake_run)

    streamed_types: list[str] = []

    async def fake_delete(self, task, tab_id, created_new, conversation_url):
        assert "evidence" in streamed_types
        assert conversation_url == "https://www.doubao.com/chat/123"
        return True

    monkeypatch.setattr(
        BbxRuntimeAdapter, "_delete_doubao_conversation", fake_delete, raising=False
    )
    events = []
    async for event in BbxRuntimeAdapter().invoke(
        AgentTask(
            task_id="bbx-doubao",
            workflow="workflow.gaojixing.doubao.browser",
            input={"question": "q", "message": "q"},
            config={"settle_seconds": 0},
        )
    ):
        streamed_types.append(event["type"])
        events.append(event)

    assert calls[0] == ["call", "tabs.create", '{"url":"https://www.doubao.com/chat"}']
    assert calls[-1] == ["call", "tabs.close", '{"tabId":7}']
    assert sum(args[3] == "page.evaluate" for args in calls if len(args) > 3) >= 3
    assert events[-1]["type"] == "done"
    evidence = next(event for event in events if event["type"] == "evidence")
    assert evidence["evidence"]["kind"] == "doubao.capture.pre_cleanup"
    assert evidence["evidence"]["response"]["answer"] == "answer"
    assert evidence["evidence"]["response"]["conversation_deleted"] is False
    response = __import__("json").loads(events[-1]["result"]["text"])
    assert response["answer"] == "answer"
    assert response["links"] == [{"url": "https://example.test/source", "title": "source"}]
    assert response["suggested_keywords"] == ["follow-up"]
    assert response["answer_complete"] is True
    assert response["conversation_deleted"] is True


@pytest.mark.asyncio
async def test_doubao_workflow_requeries_stale_input_reference(monkeypatch):
    fill_calls = 0

    async def fake_run(self, args, config):
        nonlocal fill_calls
        if args == ["call", "tabs.create", '{"url":"https://www.doubao.com/chat"}']:
            return {"ok": True, "tabId": 8}
        if args == ["call", "tabs.close", '{"tabId":8}']:
            return {"closed": True, "tabId": 8}
        if args[0:3] == ["call", "--tab", "8"]:
            method = args[3]
            if method == "dom.query":
                params = json.loads(args[4])
                if params["selector"] == "#flow-end-msg-send":
                    return {"nodes": [{"elementRef": "el_send", "tag": "button"}]}
                return {"nodes": [{"elementRef": f"el_input_{fill_calls}", "tag": "textarea"}]}
            if method == "input.fill":
                fill_calls += 1
                if fill_calls == 1:
                    return {"ok": False, "error": "Element reference is stale."}
                return {"ok": True}
            if method == "input.click":
                return {"ok": True}
            if method == "page.get_state":
                return {"url": "https://www.doubao.com/chat/456", "title": "豆包"}
            if method == "page.get_text":
                return {"text": "问题 q\n答案 answer"}
            if method == "page.evaluate":
                return {
                    "value": {
                        "answer": "answer",
                        "answer_complete": True,
                        "data": [],
                        "links": [],
                    }
                }
        raise AssertionError(f"unexpected BBX call: {args}")

    monkeypatch.setattr(BbxRuntimeAdapter, "_run_cli", fake_run)

    async def fake_delete(self, task, tab_id, created_new, conversation_url):
        assert conversation_url == "https://www.doubao.com/chat/456"
        return True

    monkeypatch.setattr(
        BbxRuntimeAdapter, "_delete_doubao_conversation", fake_delete, raising=False
    )
    events = await _collect(
        BbxRuntimeAdapter(),
        AgentTask(
            task_id="bbx-doubao-stale-ref",
            workflow="workflow.gaojixing.doubao.browser",
            input={"question": "q"},
            config={"settle_seconds": 0},
        ),
    )

    assert events[-1]["type"] == "done"
    assert json.loads(events[-1]["result"]["text"])["answer"] == "answer"
    assert fill_calls == 2


@pytest.mark.asyncio
async def test_doubao_workflow_does_not_accept_time_label_as_answer(monkeypatch):
    async def fake_run(self, args, config):
        if args == ["call", "tabs.create", '{"url":"https://www.doubao.com/chat"}']:
            return {"ok": True, "tabId": 9}
        if args == ["call", "tabs.close", '{"tabId":9}']:
            return {"closed": True, "tabId": 9}
        if args[0:3] == ["call", "--tab", "9"]:
            method = args[3]
            if method == "dom.query":
                params = json.loads(args[4])
                if params["selector"] == "#flow-end-msg-send":
                    return {"nodes": [{"elementRef": "el_send", "tag": "button"}]}
                return {"nodes": [{"elementRef": "el_input", "tag": "textarea"}]}
            if method in {"input.fill", "input.click"}:
                return {"ok": True}
            if method == "page.get_state":
                return {"url": "https://www.doubao.com/chat/local_1", "title": "豆包"}
            if method == "page.get_text":
                return {"text": "问题 q\n今天 08:05"}
            if method == "page.evaluate":
                return {
                    "value": {
                        "answer": "今天 08:05",
                        "answer_complete": False,
                        "data": [],
                        "links": [],
                        "suggested_keywords": [],
                    }
                }
        raise AssertionError(f"unexpected BBX call: {args}")

    monkeypatch.setattr(BbxRuntimeAdapter, "_run_cli", fake_run)
    events = await _collect(
        BbxRuntimeAdapter(),
        AgentTask(
            task_id="bbx-doubao-time-only",
            workflow="workflow.gaojixing.doubao.browser",
            input={"question": "q"},
            config={
                "settle_seconds": 0,
                "response_timeout_seconds": 0,
                "suggested_wait_seconds": 0,
                "delete_menu_timeout_seconds": 0,
            },
        ),
    )

    response = json.loads(events[-1]["result"]["text"])
    assert response["status"] == "blocked"
    assert response["error_type"] == "doubao_response_incomplete"
    assert response["answer"] == ""


@pytest.mark.asyncio
async def test_delete_doubao_conversation_waits_for_entry_to_disappear(monkeypatch):
    verification_checks = 0
    expressions: list[str] = []

    async def fake_call(self, task, command, tab_id, payload):
        nonlocal verification_checks
        assert command == "call"
        assert tab_id == 7
        expression = payload["params"]["expression"]
        expressions.append(expression)
        if expression in {
            _DOUBAO_OPEN_DELETE_MENU_EXPRESSION,
            _DOUBAO_CLICK_DELETE_MENU_EXPRESSION,
            _DOUBAO_CONFIRM_DELETE_EXPRESSION,
        }:
            return {"value": True}
        assert 'const conversationId = "123";' in expression
        verification_checks += 1
        return {"value": verification_checks >= 2}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(BbxRuntimeAdapter, "_doubao_call", fake_call)
    monkeypatch.setattr("backend.agent_runtimes.bbx_adapter.asyncio.sleep", no_sleep)

    deleted = await BbxRuntimeAdapter()._delete_doubao_conversation(
        AgentTask(task_id="delete", workflow="test"),
        7,
        True,
        "https://www.doubao.com/chat/123",
    )

    assert deleted is True
    assert expressions.count(_DOUBAO_CONFIRM_DELETE_EXPRESSION) == 1
    assert verification_checks == 3


@pytest.mark.asyncio
async def test_delete_doubao_conversation_fails_when_entry_remains(monkeypatch):
    async def fake_call(self, task, command, tab_id, payload):
        expression = payload["params"]["expression"]
        if expression in {
            _DOUBAO_OPEN_DELETE_MENU_EXPRESSION,
            _DOUBAO_CLICK_DELETE_MENU_EXPRESSION,
            _DOUBAO_CONFIRM_DELETE_EXPRESSION,
        }:
            return {"value": True}
        return {"value": False}

    monkeypatch.setattr(BbxRuntimeAdapter, "_doubao_call", fake_call)

    deleted = await BbxRuntimeAdapter()._delete_doubao_conversation(
        AgentTask(
            task_id="delete-timeout",
            workflow="test",
            config={"delete_verify_timeout_seconds": 0},
        ),
        7,
        True,
        "https://www.doubao.com/chat/123",
    )

    assert deleted is False


def test_doubao_conversation_id_rejects_non_doubao_urls():
    assert _doubao_conversation_id("https://www.doubao.com/chat/123") == "123"
    assert _doubao_conversation_id("https://example.test/chat/123") is None


def test_answer_after_question_requires_visible_answer_tail():
    assert _answer_after_question("页面\n问题 q", "q") == ""
    assert _answer_after_question(
        "问题 q\n对话\n帮我写作\nPPT 生成\n豆包 快速",
        "q",
    ) == ""
    assert _answer_after_question(
        "问题 q\n搜索 2 个关键词，参考 12 篇资料\n对话\n帮我写作\n豆包 快速",
        "q",
    ) == ""
    assert _answer_after_question("问题 q\n答案 answer\n推荐问题 follow-up", "q") == (
        "答案 answer\n推荐问题 follow-up"
    )
    assert _answer_after_question("豆包 快速\n问题 q\n答案 answer", "q") == "答案 answer"
    assert _answer_after_question(
        "常见的富含 DHA 的食物有哪些？\n答案 answer",
        "常见的富含DHA的食物有哪些？",
    ) == "答案 answer"


def test_suggested_keywords_are_read_from_the_visible_tail_before_footer():
    page_text = (
        "问题 q\n答案 answer\n"
        "哪些食物不能和 q 一起吃？\n如何搭配 q？\n"
        "对话\n帮我写作\nPPT 生成\n豆包 快速"
    )
    assert _suggested_keywords_from_page_text(page_text) == [
        "哪些食物不能和 q 一起吃？",
        "如何搭配 q？",
    ]
    assert _suggested_keywords_from_page_text(
        "问题 q\n答案最后一句是问句吗？\n对话\n豆包 快速"
    ) == []
