from types import SimpleNamespace

import pytest

from backend.channels.base import ChannelResult
from backend.workflow.channel_source_executor import (
    _feishu_overrides,
    execute_workflow_channel_source,
)


def test_feishu_source_accepts_snake_case_max_rows_runtime_override():
    assert _feishu_overrides({"max_rows": 1000}) == {"max_rows": 1000}


def test_feishu_source_maps_all_camel_case_runtime_overrides():
    assert _feishu_overrides(
        {
            "pageSize": 200,
            "viewId": "view-1",
            "fieldNames": ["编号", "推荐追问"],
            "sourceGroup": "recommendations",
        }
    ) == {
        "page_size": 200,
        "view_id": "view-1",
        "field_names": ["编号", "推荐追问"],
        "source_group": "recommendations",
    }


@pytest.mark.asyncio
async def test_feishu_source_applies_snake_case_row_filter_overrides(monkeypatch):
    source = SimpleNamespace(
        id="source-1",
        channel_type="feishu_table",
        channel_config={
            "keyword_field": "推荐追问",
            "status_field": "",
            "eligible_status": "",
        },
        enabled=True,
    )
    captured_config = {}

    async def fake_get_source(_session, _source_id):
        return source

    async def fake_collect(runtime_source, _params):
        captured_config.update(runtime_source.channel_config)
        return ChannelResult.ok([{"keyword": "selected"}])

    monkeypatch.setattr(
        "backend.workflow.channel_source_executor.source_service.get_source",
        fake_get_source,
    )
    monkeypatch.setattr("backend.workflow.channel_source_executor.collect", fake_collect)

    await execute_workflow_channel_source(
        {
            "channelType": "feishu_table",
            "params": {
                "sourceId": "source-1",
                "keyword_field": "推荐追问",
                "status_field": "序号",
                "eligible_status": "71",
            },
        },
        max_items=1,
        session=object(),
    )

    assert captured_config["keyword_field"] == "推荐追问"
    assert captured_config["status_field"] == "序号"
    assert captured_config["eligible_status"] == "71"


@pytest.mark.asyncio
async def test_channel_source_executor_passes_each_upstream_keyword_directly(monkeypatch):
    calls = []

    async def fake_collect(source, params):
        calls.append((source.channel_type, params))
        return ChannelResult.ok([{"keyword": params["question"], "id": params["question"]}])

    monkeypatch.setattr("backend.workflow.channel_source_executor.collect", fake_collect)
    items = await execute_workflow_channel_source(
        {
            "channelType": "doubao_research",
            "params": {"question": "{{keyword}}", "site_session": "ephemeral"},
        },
        max_items=10,
        upstream_items=[{"keyword": "gjs"}, {"keyword": "dha"}],
    )

    assert [params["question"] for _, params in calls] == ["gjs", "dha"]
    assert [item["id"] for item in items] == ["gjs", "dha"]


@pytest.mark.asyncio
async def test_channel_source_executor_rejects_missing_feishu_connection():
    with pytest.raises(Exception, match="DataSource"):
        await execute_workflow_channel_source(
            {"channelType": "feishu_table", "params": {}}, max_items=10
        )
