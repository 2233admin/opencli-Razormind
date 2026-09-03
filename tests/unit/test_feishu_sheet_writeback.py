from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.schemas.workflow import WorkflowProjectNode
from backend.workflow import feishu_sheet_writeback as writeback
from backend.workflow.feishu_sheet_writeback import (
    DEFAULT_DOUBAO_RESULT_COLUMNS,
    build_feishu_writeback_rows,
    sync_feishu_sheet_writeback,
)
from backend.workflow.opencli_hda_tracer import (
    _execute_native_node,
    _feishu_writeback_block_reason,
)
from backend.workflow.runtime_registry import resolve_runtime_metadata


def _stored_reference() -> dict:
    return {
        "recordId": "record-1",
        "outcome": "stored",
        "normalizedData": {},
        "raw": {
            "author": "doubao",
            "question": "儿童补钙应该选择哪种钙片？",
            "answer": "回答提到高吉星，也包含 https://item.jd.com/100.html。",
            "answer_complete": True,
            "source_number": "G0268-3",
            "source_fields": {"阶段": "非品牌题", "自定义": "来源值"},
            "search_keywords": ["儿童补钙", "钙片选择"],
            "search_keyword_count": 2,
            "reference_count": 3,
            "links": [
                {"title": "医学资料", "url": "https://example.test/calcium"},
                {"title": "商品", "url": "https://item.jd.com/100.html"},
            ],
            "suggested_keywords": ["儿童补钙需要补维D吗？", "钙片什么时候吃？"],
            "video_contents": ["孩子补钙怎么选？"],
            "conversation_url": "https://www.doubao.com/chat/123",
            "session_share_data": {"url": "https://www.doubao.com/chat/share-123"},
            "completed_at": "2026-09-01T08:50:00+08:00",
            "gaojixing": {"artifactId": "artifact-1"},
        },
    }


def test_doubao_result_projection_preserves_enriched_evidence_and_links() -> None:
    columns, rows = build_feishu_writeback_rows(
        [_stored_reference()],
        {"stage": "非品牌题"},
        run_id="run-1",
    )

    assert columns == list(DEFAULT_DOUBAO_RESULT_COLUMNS)
    assert len(rows) == 1
    row = dict(zip(columns, rows[0], strict=True))
    assert row["序号"] is None
    assert row["题号"] == "G0268-3"
    assert row["原问句"] == "儿童补钙应该选择哪种钙片？"
    assert row["关键词数"] == 2
    assert row["关键词（全部）"] == "1. 儿童补钙\n2. 钙片选择"
    assert row["参考资料数"] == 3
    assert row["参考资料（全部）"] == "1. 医学资料 — https://example.test/calcium"
    assert row["推荐追问数"] == 2
    assert row["商品链接（全部）"] == "https://item.jd.com/100.html"
    assert row["视频内容（全部）"] == "1. 孩子补钙怎么选？"
    assert row["高吉星是否出现"] == "是"
    assert row["正式会话链接"] == "https://www.doubao.com/chat/123"
    assert row["分享链接"] == "https://www.doubao.com/chat/share-123"
    assert row["证据状态"] == "通过"
    assert row["运行ID"] == "doubao-run-run-1-question-G0268-3"


def test_projection_columns_and_labels_are_configurable() -> None:
    columns, rows = build_feishu_writeback_rows(
        [_stored_reference()],
        {
            "columns": ["问句", "作者", "来源标签"],
            "columnMapping": {
                "问句": "computed.question",
                "作者": "raw.author",
                "来源标签": "raw.source_fields.自定义",
            },
        },
        run_id="run-1",
    )

    assert columns == ["问句", "作者", "来源标签"]
    assert rows == [["儿童补钙应该选择哪种钙片？", "doubao", "来源值"]]


@pytest.mark.asyncio
async def test_sync_posts_to_host_bridge_and_returns_verified_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "ok": True,
                "data": {
                    "appended_count": 1,
                    "skipped_count": 0,
                    "verified_addresses": ["U279"],
                    "sheet_id": "sheet-id",
                    "sheet_name": "结果",
                },
            }

    class _Client:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url, *, json, headers):
            captured.update({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setenv("LARK_CLI_BRIDGE_URL", "http://host.docker.internal:18765/feishu/records")
    monkeypatch.setenv("LARK_CLI_BRIDGE_TOKEN", "secret")
    monkeypatch.setattr(writeback.httpx, "AsyncClient", _Client)

    result = await sync_feishu_sheet_writeback(
        {
            "enabled": True,
            "spreadsheetToken": "spreadsheet",
            "sheetId": "sheet-id",
            "sheetName": "结果",
            "stage": "非品牌题",
        },
        [_stored_reference()],
        run_id="run-1",
    )

    assert captured["url"] == "http://host.docker.internal:18765/feishu/sheets/append"
    assert captured["headers"]["X-Lark-CLI-Bridge-Token"] == "secret"
    assert captured["json"]["idempotency_column"] == "运行ID"
    assert captured["json"]["rows"][0][-1] == "doubao-run-run-1-question-G0268-3"
    assert result == {
        "enabled": True,
        "status": "synced",
        "requestedRowCount": 1,
        "appendedRowCount": 1,
        "skippedRowCount": 0,
        "verifiedAddresses": ["U279"],
    }


def test_runtime_registry_preserves_explicit_feishu_writeback_config() -> None:
    node = WorkflowProjectNode(
        id="records",
        kind="sink",
        capability="store",
        params={
            "target": "records",
            "feishuWriteback": {
                "enabled": True,
                "spreadsheetToken": "spreadsheet",
                "sheetId": "sheet-id",
            },
        },
        ui={"catalogId": "intelligence.sink.records"},
    )

    runtime = resolve_runtime_metadata(node, None)

    assert runtime["binding"]["input"]["feishuWriteback"] == {
        "enabled": True,
        "spreadsheetToken": "spreadsheet",
        "sheetId": "sheet-id",
    }


def test_enabled_writeback_requires_external_mutation_permission() -> None:
    node = SimpleNamespace(
        id="records",
        runtime={
            "binding": {
                "binding_id": "workflow.record-sink.records",
                "input": {"feishuWriteback": {"enabled": True}},
            }
        },
    )

    blocked = _feishu_writeback_block_reason(
        node, SimpleNamespace(canMutateExternalSites=False)
    )
    allowed = _feishu_writeback_block_reason(
        node, SimpleNamespace(canMutateExternalSites=True)
    )

    assert blocked is not None
    assert blocked.code == "feishu_write_permission_required"
    assert allowed is None


@pytest.mark.asyncio
async def test_record_sink_invokes_enabled_writeback_with_resolved_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = [_stored_reference()]
    store = AsyncMock(return_value=(stored, 0))
    call_order: list[str] = []
    sync = AsyncMock(
        side_effect=lambda *_args, **_kwargs: call_order.append("sync")
        or {
            "enabled": True,
            "status": "synced",
            "requestedRowCount": 1,
            "appendedRowCount": 1,
            "skippedRowCount": 0,
            "verifiedAddresses": ["U279"],
        },
    )
    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer._store_record_sink_outputs", store
    )
    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer.sync_feishu_sheet_writeback", sync
    )
    commit = AsyncMock(side_effect=lambda *_args, **_kwargs: call_order.append("commit"))
    monkeypatch.setattr("backend.workflow.opencli_hda_tracer.commit_session", commit)
    config = {
        "enabled": True,
        "spreadsheetToken": "spreadsheet",
        "sheetId": "sheet-id",
    }
    node = SimpleNamespace(
        id="records",
        kind="sink",
        capability="store",
        adapter=None,
        depends_on=["source"],
        params={},
        runtime={
            "binding": {
                "binding_id": "workflow.record-sink.records",
                "input": {"target": "records", "feishuWriteback": config},
            }
        },
    )

    details, outputs = await _execute_native_node(
        node,
        {"source": [{"raw": {"question": "q"}, "lineage": []}]},
        {},
        "run-1",
        workflow_id="workflow-1",
        trace_id="trace-1",
        session=SimpleNamespace(),
    )

    commit.assert_awaited_once()
    sync.assert_awaited_once()
    assert call_order == ["commit", "sync"]
    assert sync.await_args.args[0] == config
    assert sync.await_args.kwargs["run_id"] == "run-1"
    assert details["feishuWriteback"]["status"] == "synced"
    assert outputs[0]["recordId"] == "record-1"


@pytest.mark.asyncio
async def test_record_sink_writeback_requires_authoritative_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer._store_record_sink_outputs",
        AsyncMock(return_value=([], 0)),
    )
    sync = AsyncMock()
    monkeypatch.setattr(
        "backend.workflow.opencli_hda_tracer.sync_feishu_sheet_writeback", sync
    )
    node = SimpleNamespace(
        id="records",
        kind="sink",
        capability="store",
        adapter=None,
        depends_on=["source"],
        params={},
        runtime={
            "binding": {
                "binding_id": "workflow.record-sink.records",
                "input": {
                    "target": "records",
                    "feishuWriteback": {
                        "enabled": True,
                        "spreadsheetToken": "spreadsheet",
                        "sheetId": "sheet-id",
                    },
                },
            }
        },
    )

    with pytest.raises(
        writeback.FeishuSheetWritebackError,
        match="authoritative record storage",
    ):
        await _execute_native_node(
            node,
            {"source": [{"raw": {"question": "q"}, "lineage": []}]},
            {},
            "run-1",
            workflow_id="workflow-1",
            trace_id="trace-1",
            session=None,
        )

    sync.assert_not_awaited()


def test_source_identity_is_stable_when_doubao_conversation_changes() -> None:
    first = _stored_reference()
    second = _stored_reference()
    second["raw"]["conversation_url"] = "https://www.doubao.com/chat/456"

    columns, rows = build_feishu_writeback_rows(
        [first, second],
        {"stage": "非品牌题"},
        run_id="run-1",
    )

    run_id_index = columns.index("运行ID")
    assert rows[0][run_id_index] == "doubao-run-run-1-question-G0268-3"
    assert rows[1][run_id_index] == "doubao-run-run-1-question-G0268-3"


def test_projection_combines_root_question_and_followup_sequence() -> None:
    reference = _stored_reference()
    reference["raw"].update(
        {
            "source_row_id": "recv-followup-2",
            "source_number": "G0001",
            "source_fields": {
                "题号": "G0001",
                "追问序号": "2",
                "阶段": "非品牌题",
            },
        }
    )

    columns, rows = build_feishu_writeback_rows(
        [reference],
        {"stage": "非品牌题"},
        run_id="run-1",
    )

    row = dict(zip(columns, rows[0], strict=True))
    assert row["题号"] == "G0001-2"
    assert row["运行ID"] == "doubao-run-run-1-row-recv-followup-2"


def test_writeback_idempotency_is_scoped_to_source_row_and_run() -> None:
    reference = _stored_reference()
    reference["raw"]["source_row_id"] = "rec-23"

    columns, first_rows = build_feishu_writeback_rows(
        [reference],
        {"stage": "非品牌题"},
        run_id="run-1",
    )
    _, replay_rows = build_feishu_writeback_rows(
        [reference],
        {"stage": "非品牌题"},
        run_id="run-1",
    )
    _, next_run_rows = build_feishu_writeback_rows(
        [reference],
        {"stage": "非品牌题"},
        run_id="run-2",
    )

    idempotency_index = columns.index("运行ID")
    assert first_rows[0][idempotency_index] == replay_rows[0][idempotency_index]
    assert first_rows[0][idempotency_index] != next_run_rows[0][idempotency_index]


@pytest.mark.asyncio
async def test_sync_rejects_incomplete_bridge_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"ok": True, "data": {}}

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setenv("LARK_CLI_BRIDGE_URL", "http://host.docker.internal:18765/feishu/records")
    monkeypatch.setattr(writeback.httpx, "AsyncClient", _Client)

    with pytest.raises(writeback.FeishuSheetWritebackError) as exc_info:
        await sync_feishu_sheet_writeback(
            {
                "enabled": True,
                "spreadsheetToken": "spreadsheet",
                "sheetId": "sheet-id",
                "sheetName": "结果",
            },
            [_stored_reference()],
            run_id="run-1",
        )

    assert exc_info.value.code == "feishu_sheet_writeback_invalid_receipt"
