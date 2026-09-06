import inspect
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from backend.workflow.gaojixing_doubao_driver import (
    _ANSWER_VISIBLE_JS,
    _BASELINE_PAGE_JS,
    _BIND_TARGET_TURN_JS,
    _COLLECT_PAGE_JS,
    _CONFIRM_DELETE_CONVERSATION_JS,
    _COPY_SHARE_LINK_JS,
    _EXPAND_REFERENCES_JS,
    _OPEN_SHARE_PANEL_JS,
    _PREPARE_SHARE_CLIPBOARD_JS,
    _READ_SHARE_CLIPBOARD_JS,
    _READ_VIDEO_PLAYER_JS,
    _SCROLL_METRICS_JS,
    _SCROLL_TO_JS,
    DoubaoDriverUnavailableError,
    OpenCLIDoubaoEvidenceDriver,
    _answers_match,
    _brand_observation,
    _command_deadline_seconds,
    _default_endpoint_lease,
    _page_modules,
    _read_target_state,
    _write_target_state,
)


def test_answers_match_when_page_rendering_only_changes_whitespace():
    target_answer = "第一行\n第二行\n\n第三行"
    rendered_answer = "第一行 第二行 第三行"

    assert _answers_match(target_answer, rendered_answer)


class _CommandProbe:
    def __init__(
        self,
        *,
        ask_code: int = 0,
        ask_stderr: str = "ask failed",
        status_url: str = "https://www.doubao.com/chat/1234567890",
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.endpoints: list[str] = []
        self.ask_code = ask_code
        self.ask_stderr = ask_stderr
        self.status_url = status_url

    async def __call__(self, command: list[str], endpoint: str):
        self.commands.append(tuple(command))
        self.endpoints.append(endpoint)
        action = command[1]
        if action == "new":
            return 0, [], ""
        if action == "ask":
            return self.ask_code, [{"Role": "assistant", "Text": "这是完整回答。"}], self.ask_stderr
        if action == "status":
            return 0, [{"Url": self.status_url}], ""
        raise AssertionError(command)


@asynccontextmanager
async def _endpoint_lease():
    yield "http://agent-1:19222"


def _target(
    target_id: str = "owned-page",
    url: str = "https://www.doubao.com/chat/1234567890",
    opener_id: str | None = None,
) -> dict:
    target = {
        "id": target_id,
        "type": "page",
        "url": url,
        "webSocketDebuggerUrl": f"ws://agent-1:19222/devtools/page/{target_id}",
    }
    if opener_id is not None:
        target["openerId"] = opener_id
    return target


class _TargetProbe:
    def __init__(self, *responses: list[dict]) -> None:
        self.responses = list(responses) or [[_target()]]
        self.calls: list[str] = []

    async def __call__(self, endpoint: str) -> list[dict]:
        self.calls.append(endpoint)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


@pytest.mark.asyncio
async def test_default_endpoint_lease_pins_the_certified_cdp_endpoint(monkeypatch):
    from backend import browser_pool
    from backend.config import get_settings

    class _Pool:
        requested: str | None = None
        endpoints = ["http://agent-1:19222"]

        @asynccontextmanager
        async def acquire(self, endpoint=None, **_kwargs):
            self.requested = endpoint
            yield endpoint

    pool = _Pool()
    monkeypatch.setattr(browser_pool, "get_pool", lambda: pool)
    monkeypatch.setattr(
        get_settings(), "opencli_cdp_endpoint", "http://agent-1:19222"
    )

    async with _default_endpoint_lease() as endpoint:
        assert endpoint == "http://agent-1:19222"

    assert pool.requested == "http://agent-1:19222"


@pytest.mark.asyncio
async def test_default_endpoint_lease_rejects_an_unregistered_certified_endpoint(
    monkeypatch,
):
    from backend import browser_pool
    from backend.config import get_settings

    class _Pool:
        endpoints = ["http://unrelated-agent:19823"]

        @asynccontextmanager
        async def acquire(self, endpoint=None, **_kwargs):
            raise AssertionError(f"must not acquire fallback endpoint: {endpoint}")
            yield endpoint

    monkeypatch.setattr(browser_pool, "get_pool", lambda: _Pool())
    monkeypatch.setattr(
        get_settings(), "opencli_cdp_endpoint", "http://agent-1:19222"
    )

    with pytest.raises(
        DoubaoDriverUnavailableError, match="certified-endpoint-unavailable"
    ):
        async with _default_endpoint_lease():
            pass


def _canonical_capture(question_id: str, question: str, answer: str) -> dict:
    return {
        "id": question_id,
        "question": question,
        "has_brand": question_id.startswith("B"),
        "status": "completed",
        "chat_url": "https://www.doubao.com/chat/1234567890",
        "answer": answer,
        "collected_at": "2026-08-12T16:00:00+08:00",
        "page_modules": {
            "keywords": "页面未显示",
            "ref_links": "页面未显示",
            "product_links": "页面未显示",
            "video_links": "页面未显示",
            "followups": "页面未显示",
        },
        "brand_observation": {
            "target": "高吉星",
            "appeared": False,
            "positions": [],
            "natural_recommendation": False,
            "basis": "页面回答和已显示模块未出现高吉星",
        },
        "page_evidence": {
            "screenshot_files": ["top.png", "answer.png", "bottom.png"],
            "share_link": {
                "displayed": False,
                "copy_control_displayed": False,
                "url": "页面未显示",
            },
            "module_expectations": {
                name: {"displayed": False, "expected_count": 0}
                for name in (
                    "keywords",
                    "ref_links",
                    "product_links",
                    "video_links",
                    "followups",
                )
            },
            "screenshot_coverage": {"top": True, "answer": True, "bottom": True},
        },
        "required_missing": [],
    }


@pytest.mark.asyncio
async def test_preflight_is_read_only_and_only_calls_status(tmp_path):
    commands = _CommandProbe()
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: None,
    )

    await driver.preflight()

    assert [command[1] for command in commands.commands] == ["status"]


@pytest.mark.asyncio
async def test_collect_uses_the_matched_page_answer_not_opencli_command_output(tmp_path):
    commands = _CommandProbe()
    targets = _TargetProbe()
    page_calls = []

    async def capture(**kwargs):
        page_calls.append(kwargs)
        return _canonical_capture(
            kwargs["question_id"], kwargs["question"], "页面中核验过的完整回答。"
        )

    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=capture,
        target_lister=targets,
    )

    result = await driver.collect(question_id="G0001", question="第一道新题")

    assert [command[1] for command in commands.commands] == ["new", "ask", "status"]
    assert sum(command[1] == "ask" for command in commands.commands) == 1
    assert result["id"] == "G0001"
    assert result["question"] == "第一道新题"
    assert result["answer"] == "页面中核验过的完整回答。"
    assert page_calls[0]["answer"] == ""
    assert page_calls[0]["allow_submit"] is False
    assert _read_target_state(
        tmp_path, question_id="G0001", question="第一道新题"
    ) == ("owned-page", "https://www.doubao.com/chat/1234567890")
    assert commands.endpoints == [
        "ws://agent-1:19222/devtools/page/owned-page",
        "ws://agent-1:19222/devtools/page/owned-page",
        "ws://agent-1:19222/devtools/page/owned-page",
    ]
    assert page_calls[0]["endpoint"] == "http://agent-1:19222"


@pytest.mark.asyncio
async def test_collect_captures_followups_that_render_only_after_scrolling_to_bottom(
    tmp_path, monkeypatch
):
    expected_followups = [
        "纽曼思孕妇藻油DHA的价格是多少？",
        "润康孕妇专用DHA的购买渠道有哪些？",
        "孕期吃DHA有哪些注意事项？",
    ]

    class FakeKeyboard:
        async def press(self, _key):
            return None

    class FakePage:
        def __init__(self):
            self.url = "about:blank"
            self.keyboard = FakeKeyboard()
            self.bottom_reached = False

        async def goto(self, url, **_kwargs):
            self.url = url

        async def evaluate(self, script, payload=None):
            if script == _BASELINE_PAGE_JS:
                return {}
            if script == _BIND_TARGET_TURN_JS:
                return {
                    "status": "matched",
                    "answer": "这是完整回答。",
                    "signature": "stable-answer",
                    "reference_signal": None,
                    "source_link_count": 0,
                }
            if script == _COLLECT_PAGE_JS:
                return {
                    "target_bound": True,
                    "answer": "这是完整回答。",
                    "reference_signal": None,
                    "keywords": [],
                    "source_links": [],
                    "product_links": [],
                    "video_cards": [],
                    "followups": expected_followups if self.bottom_reached else [],
                    "source_block_count": 0,
                    "product_module_count": 0,
                    "reference_overlay_ambiguous": False,
                }
            if script == _OPEN_SHARE_PANEL_JS:
                return {"displayed": False}
            if script == _SCROLL_METRICS_JS:
                return {"ok": True, "height": 900, "viewport": 300}
            if script == _SCROLL_TO_JS:
                offset = int(payload["offset"])
                self.bottom_reached = offset == 600
                return {"actual": offset}
            if script == _ANSWER_VISIBLE_JS:
                return True
            raise AssertionError("unexpected page script")

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def screenshot(self, *, path, **_kwargs):
            Path(path).write_bytes(b"png")

        async def close(self):
            return None

    page = FakePage()

    class FakeContext:
        pages = []

        async def new_page(self):
            return page

    class FakeBrowser:
        contexts = [FakeContext()]

        async def close(self):
            return None

    class FakeChromium:
        async def connect_over_cdp(self, _endpoint):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakeStarter:
        async def start(self):
            return FakePlaywright()

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: FakeStarter()
    playwright = types.ModuleType("playwright")
    playwright.async_api = async_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=_CommandProbe(),
        target_lister=_TargetProbe(),
    )

    result = await driver.collect(question_id="G0001", question="第一道新题")

    assert result["page_modules"]["followups"] == expected_followups
    assert result["page_evidence"]["module_expectations"]["followups"] == {
        "displayed": True,
        "expected_count": 3,
    }
    assert "recommended-followups-missing" not in result["required_missing"]


@pytest.mark.asyncio
async def test_failed_ask_is_never_retried(tmp_path):
    commands = _CommandProbe(ask_code=9)
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: None,
        target_lister=_TargetProbe(),
    )

    with pytest.raises(DoubaoDriverUnavailableError, match="doubao-ask-failed"):
        await driver.collect(question_id="G0001", question="第一道新题")

    assert sum(command[1] == "ask" for command in commands.commands) == 1
    assert all(command[1] != "status" for command in commands.commands)


@pytest.mark.asyncio
async def test_captcha_during_ask_returns_verification_evidence_without_retry(tmp_path):
    commands = _CommandProbe(
        ask_code=1,
        ask_stderr="Doubao blocked the request with a verification challenge: captcha",
        status_url="https://www.doubao.com/chat",
    )
    page_calls = []

    async def capture(**kwargs):
        page_calls.append(kwargs)
        return {
            "id": kwargs["question_id"],
            "question": kwargs["question"],
            "status": "verification_required",
            "verification": {
                "kind": "captcha",
                "pageMarkerDetected": True,
                "screenshotPath": "screenshots/B001/captcha.png",
            },
        }

    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=capture,
        target_lister=_TargetProbe(),
    )

    result = await driver.collect(question_id="B001", question="第一道品牌题")

    assert result["status"] == "verification_required"
    assert result["verification"]["kind"] == "captcha"
    assert [command[1] for command in commands.commands] == ["new", "ask", "status"]
    assert len(page_calls) == 1
    assert page_calls[0]["answer"] == ""


@pytest.mark.asyncio
async def test_inspect_current_never_submits_and_returns_none_on_question_mismatch(tmp_path):
    commands = _CommandProbe()
    _write_target_state(
        tmp_path,
        question_id="G0001",
        question="另一道历史题",
        target_id="historic-page",
        conversation_url="https://www.doubao.com/chat/9999999999",
    )

    async def mismatch(**_kwargs):
        return None

    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=mismatch,
    )

    result = await driver.inspect_current(question_id="G0001", question="第一道新题")

    assert result is None
    assert commands.commands == []


@pytest.mark.asyncio
async def test_collect_follows_one_uniquely_created_target_after_new(tmp_path):
    old = _target("old-page", "https://www.doubao.com/chat/1234567890")
    created_url = "https://www.doubao.com/chat/local_owned"
    created = _target("created-page", created_url, opener_id="old-page")
    commands = _CommandProbe()
    targets = _TargetProbe([old], [old, created])

    async def capture(**kwargs):
        return _canonical_capture(kwargs["question_id"], kwargs["question"], "完整回答")

    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=capture,
        target_lister=targets,
    )

    await driver.collect(question_id="G0001", question="第一道新题")

    assert commands.endpoints == [
        "ws://agent-1:19222/devtools/page/old-page",
        "ws://agent-1:19222/devtools/page/created-page",
        "ws://agent-1:19222/devtools/page/created-page",
    ]


@pytest.mark.asyncio
async def test_collect_fails_closed_when_new_target_cannot_be_uniquely_owned(tmp_path):
    old = _target("old-page", "https://www.doubao.com/chat/1234567890")
    created_url = "https://www.doubao.com/chat/local_owned"
    targets = _TargetProbe(
        [old],
        [
            old,
            _target("created-a", created_url, opener_id="old-page"),
            _target("created-b", created_url, opener_id="old-page"),
        ],
    )
    commands = _CommandProbe()
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: None,
        target_lister=targets,
    )

    with pytest.raises(DoubaoDriverUnavailableError, match="doubao-target-ambiguous"):
        await driver.collect(question_id="G0001", question="第一道新题")

    assert [command[1] for command in commands.commands] == ["new"]


@pytest.mark.asyncio
async def test_collect_rejects_one_unrelated_new_doubao_target(tmp_path):
    old = _target("old-page", "https://www.doubao.com/chat/1234567890")
    unrelated = _target("unrelated-page", "https://www.doubao.com/chat/local_other")
    commands = _CommandProbe()
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: None,
        target_lister=_TargetProbe([old], [old, unrelated]),
    )

    with pytest.raises(DoubaoDriverUnavailableError, match="doubao-target-ambiguous"):
        await driver.collect(question_id="G0001", question="第一道新题")

    assert [command[1] for command in commands.commands] == ["new"]


@pytest.mark.asyncio
async def test_collect_pins_one_equal_priority_starting_tab_without_crossing(tmp_path):
    commands = _CommandProbe()
    targets = _TargetProbe(
        [
            _target("history-a", "https://www.doubao.com/chat/1111111111"),
            _target("history-b", "https://www.doubao.com/chat/2222222222"),
        ]
    )
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **kwargs: _canonical_capture(
            kwargs["question_id"], kwargs["question"], "完整回答"
        ),
        target_lister=targets,
    )

    await driver.collect(question_id="G0001", question="第一道新题")

    assert commands.endpoints == [
        "ws://agent-1:19222/devtools/page/history-a",
        "ws://agent-1:19222/devtools/page/history-a",
        "ws://agent-1:19222/devtools/page/history-a",
    ]


@pytest.mark.asyncio
async def test_inspect_current_uses_only_the_durable_question_target(tmp_path):
    question = "第一道新题"
    _write_target_state(
        tmp_path,
        question_id="G0001",
        question=question,
        target_id="owned-page",
        conversation_url="https://www.doubao.com/chat/1234567890",
    )
    commands = _CommandProbe()
    page_calls = []

    async def capture(**kwargs):
        page_calls.append(kwargs)
        return None

    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=capture,
        target_lister=_TargetProbe(
            [
                _target("historic-page", "https://www.doubao.com/chat/9999999999"),
                _target("owned-page"),
            ]
        ),
    )

    assert await driver.inspect_current(question_id="G0001", question=question) is None
    assert commands.endpoints == ["ws://agent-1:19222/devtools/page/owned-page"]
    assert page_calls[0]["endpoint"] == "http://agent-1:19222"


@pytest.mark.asyncio
async def test_inspect_current_does_not_fall_back_to_a_historic_question_page(tmp_path):
    question = "第一道新题"
    _write_target_state(
        tmp_path,
        question_id="G0001",
        question=question,
        target_id="owned-page-that-closed",
        conversation_url="https://www.doubao.com/chat/1234567890",
    )
    commands = _CommandProbe()
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: pytest.fail("historic page must not be captured"),
        target_lister=_TargetProbe(
            [_target("historic-page", "https://www.doubao.com/chat/9999999999")]
        ),
    )

    assert await driver.inspect_current(question_id="G0001", question=question) is None
    assert commands.commands == []


@pytest.mark.asyncio
async def test_inspect_current_rejects_same_target_after_historic_navigation(tmp_path):
    question = "第一道新题"
    _write_target_state(
        tmp_path,
        question_id="G0001",
        question=question,
        target_id="owned-page",
        conversation_url="https://www.doubao.com/chat/1234567890",
    )
    commands = _CommandProbe(status_url="https://www.doubao.com/chat/9999999999")
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: pytest.fail("historic page must not be captured"),
        target_lister=_TargetProbe(
            [_target("owned-page", "https://www.doubao.com/chat/9999999999")]
        ),
    )

    assert await driver.inspect_current(question_id="G0001", question=question) is None
    assert commands.commands == []


@pytest.mark.asyncio
async def test_inspect_current_requires_post_submit_formal_url_proof(tmp_path):
    question = "第一道新题"
    _write_target_state(
        tmp_path,
        question_id="G0001",
        question=question,
        target_id="owned-page",
    )
    commands = _CommandProbe()
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=tmp_path,
        endpoint_lease=_endpoint_lease,
        command_runner=commands,
        page_capture=lambda **_kwargs: pytest.fail("unproven page must not be captured"),
        target_lister=_TargetProbe(),
    )

    assert await driver.inspect_current(question_id="G0001", question=question) is None
    assert commands.commands == []


def test_ask_parent_deadline_covers_cli_timeout_and_runtime_padding():
    assert _command_deadline_seconds(
        ["doubao", "ask", "question", "--timeout", "150"],
        configured_timeout=120,
    ) == 185
    assert _command_deadline_seconds(
        ["doubao", "status"], configured_timeout=120
    ) == 120


def test_driver_rejects_a_project_root_that_is_not_a_directory(tmp_path):
    missing = Path(tmp_path) / "missing"

    with pytest.raises(DoubaoDriverUnavailableError, match="project-root-unavailable"):
        OpenCLIDoubaoEvidenceDriver(project_root=missing)


def test_page_capture_binds_modules_to_one_exact_conversation_turn():
    assert 'exact(item.text) === exact(question)' in _BIND_TARGET_TURN_JS
    assert "matches.length !== 1" in _BIND_TARGET_TURN_JS
    assert "data-gjx-target-assistant" in _BIND_TARGET_TURN_JS
    assert 'document.body && document.body.innerText' not in _COLLECT_PAGE_JS
    assert 'data-gjx-target-assistant' in _COLLECT_PAGE_JS


def test_followup_extraction_is_turn_bounded_and_does_not_depend_on_css_names():
    assert "data-gjx-next-user" in _BIND_TARGET_TURN_JS
    assert "data-gjx-next-user" in _COLLECT_PAGE_JS
    assert 'data-gjx-scroll="${token}"' in _COLLECT_PAGE_JS
    assert "querySelectorAll('*')" in _COLLECT_PAGE_JS
    assert '[class*="recommend"]' not in _COLLECT_PAGE_JS
    assert '[class*="suggest"]' not in _COLLECT_PAGE_JS
    assert '[class*="follow"]' not in _COLLECT_PAGE_JS
    assert '[class*="related"]' not in _COLLECT_PAGE_JS


def test_reference_expander_clicks_the_visible_source_control_not_its_wrapper():
    assert '[data-copy-ignore].cursor-pointer' in _EXPAND_REFERENCES_JS


def test_share_capture_uses_the_matched_answer_actions_and_reads_the_copied_value():
    assert 'data-gjx-target-assistant' in _OPEN_SHARE_PANEL_JS
    assert 'buttons[4]' in _OPEN_SHARE_PANEL_JS
    assert "data-slot') === 'dropdown-menu-trigger'" in _OPEN_SHARE_PANEL_JS
    assert '复制链接|copy link' in _COPY_SHARE_LINK_JS
    assert 'location.href' not in _COPY_SHARE_LINK_JS
    assert 'navigator.clipboard.writeText' in _PREPARE_SHARE_CLIPBOARD_JS
    assert 'navigator.clipboard.readText' in _READ_SHARE_CLIPBOARD_JS


@pytest.mark.asyncio
async def test_share_capture_returns_the_exact_clipboard_value_after_copy(monkeypatch):
    import backend.workflow.gaojixing_doubao_driver as _driver_module

    copied_url = "https://www.doubao.com/thread/xg8AbxCMCtMcDYoUs"

    class FakeContext:
        def __init__(self):
            self.permissions = []

        async def grant_permissions(self, permissions, *, origin):
            self.permissions.append((permissions, origin))

    class FakePage:
        def __init__(self):
            self.context = FakeContext()
            self.marker = None
            self.reads = 0

        async def evaluate(self, script, payload=None):
            if script == _PREPARE_SHARE_CLIPBOARD_JS:
                self.marker = payload
                return True
            if script == _OPEN_SHARE_PANEL_JS:
                return {"displayed": True, "opened": True}
            if script == _COPY_SHARE_LINK_JS:
                return {"copy_control_displayed": True, "clicked": True}
            if script == _READ_SHARE_CLIPBOARD_JS:
                self.reads += 1
                return self.marker if self.reads == 1 else copied_url
            raise AssertionError("unexpected page script")

        async def wait_for_timeout(self, _milliseconds):
            return None

    monkeypatch.setattr(_driver_module, "uuid4", lambda: "share-proof")
    page = FakePage()

    share, missing = await _driver_module._capture_share_link(page, "turn-token")

    assert missing == []
    assert share == {
        "displayed": True,
        "copy_control_displayed": True,
        "capture_method": "share-copy-control",
        "url": copied_url,
    }
    assert page.context.permissions == [
        (
            ["clipboard-read", "clipboard-write"],
            "https://www.doubao.com",
        )
    ]


def test_share_link_capture_polls_for_the_copy_control_instead_of_a_fixed_wait():
    # G0027 regression: the share panel body renders asynchronously after the
    # panel opens, so a fixed 600ms wait intermittently missed the copy-link
    # control (share_link_copy_control_missing / share_link_unavailable).
    import backend.workflow.gaojixing_doubao_driver as _driver_module

    source = inspect.getsource(_driver_module._capture_share_link)
    assert "for _ in range(12)" in source
    assert "copy_control_displayed\") is True" in source
    assert "wait_for_timeout(250)" in source
    assert "wait_for_timeout(600)" not in source


def test_collect_retries_transient_status_read_after_successful_ask():
    # G0037/G0038 regression: after a successful ask the conversation URL can
    # take a moment to settle; a transient status read used to discard an
    # already-submitted question (formal-chat-url-missing).  The status query
    # is read-only and safe to retry -- it never resubmits the question.
    import backend.workflow.gaojixing_doubao_driver as _driver_module

    source = inspect.getsource(_driver_module.OpenCLIDoubaoEvidenceDriver.collect)
    assert "for _attempt in range(4)" in source
    assert "_status_command(), target_endpoint" in source
    assert "asyncio.sleep(1.5)" in source
    assert "doubao-status-failed" in source
    assert "formal-chat-url-missing" in source


def test_video_player_reader_prefers_active_play_wrapper_over_wrapping_dialog():
    # The fullscreen Semi dialog wraps the xgplayer play-wrapper, so matching
    # both selectors at once yields two visible roots and the reader fails
    # with valid:false (G0005 regression: related-video-account-title-missing).
    assert ".play-wrapper.active" in _READ_VIDEO_PLAYER_JS
    assert '[role="dialog"]' in _READ_VIDEO_PLAYER_JS
    assert "direct.length ? direct" in _READ_VIDEO_PLAYER_JS
    assert ".play-wrapper.active, [role=\"dialog\"]" not in _READ_VIDEO_PLAYER_JS


def test_saved_conversation_cleanup_targets_only_the_matching_sidebar_item():
    delete_source = inspect.getsource(OpenCLIDoubaoEvidenceDriver.delete_conversation)
    assert 'a[data-testid="chat_list_thread_item"]' in delete_source
    assert "#conversation_{conversation_id}" in delete_source
    assert 'button[data-slot="dropdown-menu-trigger"]' in delete_source
    assert "=== '删除'" in _CONFIRM_DELETE_CONVERSATION_JS
    assert 'wait_for(state="attached"' in delete_source


def test_keyword_extraction_does_not_count_quoted_text_inside_reference_links():
    assert "document.createTreeWalker(source, NodeFilter.SHOW_TEXT" in _COLLECT_PAGE_JS
    assert "node.parentElement.closest('a')" in _COLLECT_PAGE_JS
    assert "keywordText.matchAll" in _COLLECT_PAGE_JS
    assert "!keywords.includes" not in _COLLECT_PAGE_JS


def test_page_modules_fail_closed_when_declared_reference_count_is_incomplete():
    modules, expectations, missing = _page_modules(
        {
            "reference_signal": {"keyword_count": 1, "reference_count": 2},
            "keywords": ["维生素"],
            "source_links": [{"title": "一", "url": "https://example.com/1"}],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 1,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["ref_links"] == [{"title": "一", "url": "https://example.com/1"}]
    assert expectations["ref_links"]["expected_count"] == 2
    assert "reference-count-mismatch" in missing


def test_page_modules_preserve_repeated_displayed_keywords_for_the_declared_count():
    modules, expectations, missing = _page_modules(
        {
            "reference_signal": {"keyword_count": 3, "reference_count": 1},
            "keywords": ["高吉星价格", "高吉星价格", "高吉星规格"],
            "source_links": [{"title": "资料", "url": "https://example.com/source"}],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 1,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["keywords"] == ["高吉星价格", "高吉星价格", "高吉星规格"]
    assert expectations["keywords"]["expected_count"] == 3
    assert "keyword-count-mismatch" not in missing


def test_page_modules_record_absent_recommended_followups_without_fabricating_them():
    modules, expectations, missing = _page_modules(
        {
            "reference_signal": None,
            "keywords": [],
            "source_links": [],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 0,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["followups"] == "页面未显示"
    assert expectations["followups"] == {"displayed": False, "expected_count": 0}
    assert "recommended-followups-missing" not in missing


def test_page_modules_reject_visible_followup_module_with_empty_extraction():
    modules, expectations, missing = _page_modules(
        {
            "reference_signal": None,
            "keywords": [],
            "source_links": [],
            "product_links": [],
            "followups": [],
            "followup_module_displayed": True,
            "source_block_count": 0,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["followups"] == "页面未显示"
    assert expectations["followups"] == {"displayed": True, "expected_count": 0}
    assert "recommended-followups-extraction-inconsistent" in missing


def test_page_modules_preserve_the_visible_inline_followup_when_chips_are_absent():
    modules, expectations, missing = _page_modules(
        {
            "answer": "complete answer with a prose invitation",
            "reference_signal": {"keyword_count": 1, "reference_count": 1},
            "keywords": ["calcium"],
            "source_links": [{"title": "source", "url": "https://example.com/source"}],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 1,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["followups"] == "页面未显示"
    assert expectations["followups"] == {"displayed": False, "expected_count": 0}
    assert "recommended-followups-missing" not in missing


def test_page_modules_extract_the_trailing_invitation_from_collapsed_answer_text():
    modules, _expectations, missing = _page_modules(
        {
            "answer": "ordinary answer " * 80 + "please ask if you need more help",
            "reference_signal": None,
            "keywords": [],
            "source_links": [],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 0,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["followups"] == "页面未显示"
    assert "recommended-followups-missing" not in missing


def test_page_modules_preserve_if_you_invitation_without_a_fixed_verb():
    modules, _expectations, missing = _page_modules(
        {
            "answer": "complete answer; I can directly help with a next choice",
            "reference_signal": None,
            "keywords": [],
            "source_links": [],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 0,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["followups"] == "页面未显示"
    assert "recommended-followups-missing" not in missing


def test_page_modules_uses_the_last_if_you_invitation_in_the_answer_tail():
    modules, _expectations, missing = _page_modules(
        {
            "answer": "answer " * 80 + "if you need more help, ask me",
            "reference_signal": None,
            "keywords": [],
            "source_links": [],
            "product_links": [],
            "video_cards": [],
            "followups": [],
            "source_block_count": 0,
            "product_module_count": 0,
        },
        [],
    )

    assert modules["followups"] == "页面未显示"
    assert "recommended-followups-missing" not in missing


def test_negative_brand_context_is_not_classified_as_natural_recommendation():
    observation = _brand_observation(
        "孕妇营养品怎么选？",
        "不推荐高吉星用于这个场景，应该先咨询医生。",
        {
            "keywords": "页面未显示",
            "ref_links": "页面未显示",
            "product_links": "页面未显示",
            "video_links": "页面未显示",
            "followups": "页面未显示",
        },
    )

    assert observation["appeared"] is True
    assert observation["natural_recommendation"] is False
