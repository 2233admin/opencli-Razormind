"""Execute workflow source nodes through the registered channel runner."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.pipeline.collector import collect
from backend.services import source_service

_SUPPORTED = {"feishu_table", "opencli", "doubao_research"}


@dataclass(frozen=True)
class WorkflowChannelSourceExecutionError(Exception):
    code: str
    message: str
    status: str = "failed"
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def supports_channel_source(binding_input: dict[str, Any]) -> bool:
    return _channel_type(binding_input) in _SUPPORTED


async def execute_workflow_channel_source(
    binding_input: dict[str, Any],
    *,
    max_items: int,
    session: AsyncSession | None = None,
    upstream_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    channel_type = _channel_type(binding_input)
    if channel_type not in _SUPPORTED:
        return None
    params = _dict(binding_input.get("params"))
    config = _dict(binding_input.get("adapterConfig"))
    merged = {**config, **params}
    source_id = _string(merged.get("sourceId") or merged.get("dataSourceId"))

    if channel_type == "feishu_table":
        if session is None or not source_id:
            raise WorkflowChannelSourceExecutionError(
                "feishu_source_connection_required",
                "Feishu source requires a configured DataSource connection.",
                "blocked",
            )
        source = await source_service.get_source(session, source_id)
        if source is None or source.channel_type != "feishu_table" or not source.enabled:
            raise WorkflowChannelSourceExecutionError(
                "feishu_source_unavailable",
                (
                    "The configured Feishu DataSource is missing, disabled, or has "
                    "the wrong channel type."
                ),
                "blocked",
                {"sourceId": source_id},
            )
        # The stored source config is authoritative; graph params can only select
        # a bounded runtime override, never credentials.
        runtime_source = SimpleNamespace(
            id=source.id,
            channel_type=source.channel_type,
            channel_config={**source.channel_config, **_feishu_overrides(merged)},
            enabled=source.enabled,
        )
    else:
        runtime_source = SimpleNamespace(
            id=source_id or f"workflow-node:{_string(merged.get('nodeId')) or channel_type}",
            channel_type=channel_type,
            channel_config=_channel_config(channel_type, merged),
            enabled=True,
        )

    inputs = upstream_items or [{}]
    outputs: list[dict[str, Any]] = []
    for upstream in inputs[:max_items]:
        call_params = _interpolate(merged, upstream)
        result = await collect(runtime_source, call_params)
        if not result.success:
            raise WorkflowChannelSourceExecutionError(
                "source_channel_failed",
                result.error or f"{channel_type} source failed",
                "failed",
                {"channelType": channel_type, "errorType": result.error_type},
            )
        outputs.extend(result.items)
        if len(outputs) >= max_items:
            return outputs[:max_items]
    return outputs


def _channel_type(binding_input: dict[str, Any]) -> str:
    params = _dict(binding_input.get("params"))
    config = _dict(binding_input.get("adapterConfig"))
    return _string(
        binding_input.get("channelType")
        or binding_input.get("channel_type")
        or params.get("channelType")
        or params.get("channel_type")
        or config.get("channelType")
        or config.get("channel_type")
        or config.get("channel")
    )


def _channel_config(channel_type: str, values: dict[str, Any]) -> dict[str, Any]:
    if channel_type == "opencli":
        return {
            key: values[key]
            for key in ("site", "command", "args", "positionalArgs", "format", "sourceGroup")
            if key in values
        }
    return {
        key: values[key]
        for key in ("question", "site_session", "extract_citations")
        if key in values
    }


def _feishu_overrides(values: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "appToken": "app_token",
        "tableId": "table_id",
        "keywordField": "keyword_field",
        "numberField": "number_field",
        "statusField": "status_field",
        "eligibleStatus": "eligible_status",
        "maxRows": "max_rows",
        "pageSize": "page_size",
        "viewId": "view_id",
        "fieldNames": "field_names",
        "sourceGroup": "source_group",
    }
    return {
        mapping.get(key, key): value
        for key, value in values.items()
        if key in mapping
        or key
        in {
            "eligible_status",
            "keyword_field",
            "number_field",
            "max_rows",
            "page_size",
            "status_field",
            "view_id",
            "field_names",
            "source_group",
        }
    }


def _interpolate(value: Any, row: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _interpolate(item, row) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, row) for item in value]
    if isinstance(value, str):
        keyword = str(row.get("keyword") or row.get("title") or "")
        return value.replace("{{keyword}}", keyword)
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""
