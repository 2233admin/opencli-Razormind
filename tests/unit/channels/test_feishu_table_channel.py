import httpx
import pytest

from backend.channels.base import AuthContext, FetchContext, FetchResult
from backend.channels.feishu_table_channel import FeishuTableChannel, _cli_rows
from backend.schemas.source import DataSourceCreate


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(200, json=self.payload, request=httpx.Request("GET", url))


class FakeBridgeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(200, json=self.payload, request=httpx.Request("POST", url))


def _config(**overrides):
    return {
        "transport": "http",
        "app_token": "bascn-keywords",
        "table_id": "tblKeywords",
        "keyword_field": "关键词",
        "status_field": "状态",
        "eligible_status": "待采集",
        **overrides,
    }


@pytest.mark.asyncio
async def test_fetch_maps_eligible_rows_and_preserves_stable_lineage():
    client = FakeClient(
        {
            "code": 0,
            "data": {
                "items": [
                    {"record_id": "rec_1", "fields": {"关键词": "高吉星", "状态": "待采集"}},
                    {"record_id": "rec_2", "fields": {"关键词": "忽略", "状态": "已完成"}},
                ],
                "has_more": False,
            },
        }
    )
    result = await FeishuTableChannel().fetch(
        FetchContext(
            config=_config(),
            params={},
            source_id="source-1",
            auth=AuthContext(kind="bearer", token="tenant-token"),
            http=client,
        )
    )

    assert [item["keyword"] for item in result.items] == ["高吉星"]
    assert result.items[0]["id"] == "feishu:source-1:rec_1"
    assert result.items[0]["source_row_id"] == "rec_1"
    assert client.calls[0][1]["headers"] == {"Authorization": "Bearer tenant-token"}
    assert client.calls[0][1]["params"]["page_size"] == 100


@pytest.mark.asyncio
async def test_fetch_returns_next_cursor_for_bounded_pagination():
    client = FakeClient(
        {"code": 0, "data": {"items": [], "has_more": True, "page_token": "next-page"}}
    )
    result = await FeishuTableChannel().fetch(
        FetchContext(config=_config(), params={}, auth=AuthContext(token="token"), http=client)
    )

    assert result.has_more
    assert result.next_cursor == {"page_token": "next-page"}


@pytest.mark.asyncio
async def test_fetch_fails_closed_without_encrypted_token():
    with pytest.raises(Exception, match="token"):
        await FeishuTableChannel().fetch(
            FetchContext(config=_config(), params={}, auth=AuthContext())
        )


@pytest.mark.asyncio
async def test_validate_config_requires_table_identifiers():
    errors = await FeishuTableChannel().validate_config({})
    assert {
        "'app_token' is required for feishu_table",
        "'table_id' is required for feishu_table",
        "'keyword_field' is required for feishu_table",
    } <= set(errors)


def test_source_schema_accepts_feishu_table_channel():
    source = DataSourceCreate(
        name="Feishu keywords", channel_type="feishu_table", channel_config=_config()
    )
    assert source.channel_type == "feishu_table"


def test_cli_matrix_response_becomes_record_rows():
    rows = _cli_rows(
        {
            "data": {
                "fields": ["序号", "关键词"],
                "data": [["1", "高吉星"], ["2", "DHA"]],
                "record_id_list": ["rec-1", "rec-2"],
            }
        }
    )
    assert rows == [
        {"record_id": "rec-1", "fields": {"序号": "1", "关键词": "高吉星"}},
        {"record_id": "rec-2", "fields": {"序号": "2", "关键词": "DHA"}},
    ]


@pytest.mark.asyncio
async def test_cli_transport_uses_host_bridge_when_configured(monkeypatch):
    bridge = FakeBridgeClient(
        {
            "ok": True,
            "data": {
                "fields": ["序号", "关键词", "状态"],
                "data": [["1", "高吉星", "待采集"]],
                "record_id_list": ["rec-1"],
                "has_more": False,
            },
        }
    )
    monkeypatch.setattr(
        "backend.channels.feishu_table_channel.httpx.AsyncClient",
        lambda **_: bridge,
    )
    monkeypatch.setenv("LARK_CLI_BRIDGE_URL", "http://host.docker.internal:18765/feishu/records")
    monkeypatch.setenv("LARK_CLI_BRIDGE_TOKEN", "bridge-token")

    result = await FeishuTableChannel().fetch(
        FetchContext(
            config={**_config(transport="cli"), "max_rows": 500},
            params={},
            source_id="source-1",
        )
    )

    assert [item["keyword"] for item in result.items] == ["高吉星"]
    assert result.metadata["transport"] == "lark-cli-bridge"
    assert bridge.calls[0][0].endswith("/feishu/records")
    assert bridge.calls[0][1]["headers"] == {"X-Lark-CLI-Bridge-Token": "bridge-token"}


@pytest.mark.asyncio
async def test_cli_bridge_applies_max_rows_across_a_page(monkeypatch):
    bridge = FakeBridgeClient(
        {
            "ok": True,
            "data": {
                "fields": ["序号", "关键词", "状态"],
                "data": [["1", "高吉星", "待采集"], ["2", "DHA", "待采集"]],
                "record_id_list": ["rec-1", "rec-2"],
                "has_more": True,
            },
        }
    )
    monkeypatch.setattr(
        "backend.channels.feishu_table_channel.httpx.AsyncClient",
        lambda **_: bridge,
    )
    monkeypatch.setenv("LARK_CLI_BRIDGE_URL", "http://host.docker.internal:18765/feishu/records")

    result = await FeishuTableChannel().fetch(
        FetchContext(
            config={**_config(transport="cli"), "max_rows": 1},
            params={},
            source_id="source-1",
        )
    )

    assert [item["keyword"] for item in result.items] == ["高吉星"]
    assert not result.has_more
    assert bridge.calls[0][1]["json"]["limit"] == 1
    assert bridge.calls[0][1]["json"]["offset"] == 0


@pytest.mark.asyncio
async def test_cli_rows_preserve_business_number_for_downstream_resume():
    result = await FeishuTableChannel()._rows_to_result(
        FetchContext(
            config=_config(number_field="序号"),
            params={},
            source_id="source-1",
        ),
        [
            {
                "record_id": "rec-23",
                "fields": {"序号": "23", "关键词": "宝宝DHA", "状态": "待采集"},
            }
        ],
    )

    assert result[0]["source_row_id"] == "rec-23"
    assert result[0]["source_number"] == "23"
    assert result[0]["feishu"]["number"] == "23"


@pytest.mark.asyncio
async def test_health_check_cli_transport_uses_local_session_without_source_token(monkeypatch):
    channel = FeishuTableChannel()
    observed = {}

    async def fetch(ctx):
        observed["ctx"] = ctx
        return FetchResult(items=[], has_more=False)

    monkeypatch.setattr(channel, "fetch", fetch)

    assert await channel.health_check(_config(transport="cli")) is True
    assert observed["ctx"].config["transport"] == "cli"
    assert observed["ctx"].config["page_size"] == 1
    assert observed["ctx"].config["max_rows"] == 1


@pytest.mark.asyncio
async def test_health_check_defaults_to_cli_transport(monkeypatch):
    channel = FeishuTableChannel()
    observed = {}
    config = _config()
    config.pop("transport")

    async def fetch(ctx):
        observed["ctx"] = ctx
        return FetchResult(items=[], has_more=False)

    monkeypatch.setattr(channel, "fetch", fetch)

    assert await channel.health_check(config) is True
    assert observed["ctx"].config["page_size"] == 1


@pytest.mark.asyncio
async def test_health_check_cli_transport_reports_failed_local_probe(monkeypatch):
    channel = FeishuTableChannel()

    async def fetch(_ctx):
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(channel, "fetch", fetch)

    assert await channel.health_check(_config(transport="cli")) is False
