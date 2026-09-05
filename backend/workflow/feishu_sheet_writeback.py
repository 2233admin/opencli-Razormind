"""Optional Feishu Sheets projection for durable workflow records.

The local ``CollectedRecord`` remains the source of truth.  This module only
projects accepted Doubao records into a configured worksheet through the
host-side lark-cli bridge, where user credentials remain isolated from Docker.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

DEFAULT_DOUBAO_RESULT_COLUMNS = (
    "序号",
    "题号",
    "阶段",
    "原问句",
    "完整回答",
    "关键词数",
    "关键词（全部）",
    "参考资料数",
    "参考资料（全部）",
    "推荐追问数",
    "推荐追问（全部）",
    "商品链接（全部）",
    "视频内容（全部）",
    "高吉星是否出现",
    "高吉星观察",
    "正式会话链接",
    "分享链接",
    "连续截图",
    "完成时间",
    "证据状态",
    "运行ID",
)

_COLUMN_FIELDS = {
    "序号": "sequence",
    "题号": "question_id",
    "阶段": "stage",
    "原问句": "question",
    "完整回答": "answer",
    "关键词数": "search_keyword_count",
    "关键词（全部）": "search_keywords",
    "参考资料数": "reference_count",
    "参考资料（全部）": "references",
    "推荐追问数": "followup_count",
    "推荐追问（全部）": "followups",
    "商品链接（全部）": "ecommerce_links",
    "视频内容（全部）": "video_contents",
    "高吉星是否出现": "brand_present",
    "高吉星观察": "brand_observation",
    "正式会话链接": "conversation_url",
    "分享链接": "share_url",
    "连续截图": "screenshots",
    "完成时间": "completed_at",
    "证据状态": "evidence_status",
    "运行ID": "idempotency_key",
}

_ECOMMERCE_HOSTS = (
    "1688.com",
    "amazon.cn",
    "amazon.com",
    "dangdang.com",
    "dewu.com",
    "jd.com",
    "kaola.com",
    "pinduoduo.com",
    "suning.com",
    "taobao.com",
    "tmall.com",
    "vip.com",
    "yangkeduo.com",
)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_DOUBAO_CHAT_RE = re.compile(r"https?://(?:www\.)?doubao\.com/chat/([^/?#]+)", re.IGNORECASE)


class FeishuSheetWritebackError(RuntimeError):
    """Typed workflow failure for an enabled but unsuccessful sheet sync."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def feishu_writeback_enabled(config: Any) -> bool:
    return isinstance(config, dict) and config.get("enabled") is True


def build_feishu_writeback_rows(
    stored_refs: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    run_id: str,
    completed_at: str | None = None,
) -> tuple[list[str], list[list[Any]]]:
    """Map durable Doubao record references into a configurable sheet schema."""

    columns = _configured_columns(config)
    mapping = config.get("columnMapping")
    mapping = mapping if isinstance(mapping, dict) else {}
    rows: list[list[Any]] = []
    timestamp = completed_at or datetime.now(UTC).isoformat()

    for reference in stored_refs:
        raw = _dict(reference.get("raw"))
        if not _is_doubao_record(raw):
            continue
        values = _canonical_values(reference, raw, config, run_id=run_id, completed_at=timestamp)
        row: list[Any] = []
        for column in columns:
            selector = mapping.get(column, _COLUMN_FIELDS.get(column, column))
            row.append(
                _mapped_value(
                    selector, values, raw, reference, empty=config.get("emptyValue")
                )
            )
        rows.append(row)
    return columns, rows


async def sync_feishu_sheet_writeback(
    config: Any,
    stored_refs: list[dict[str, Any]],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Append mapped rows through the authenticated host bridge and verify its receipt."""

    if not feishu_writeback_enabled(config):
        return {"enabled": False, "status": "disabled", "requestedRowCount": 0}
    assert isinstance(config, dict)
    spreadsheet_token = _text(config.get("spreadsheetToken"))
    sheet_id = _text(config.get("sheetId"))
    sheet_name = _text(config.get("sheetName"))
    if not spreadsheet_token or not (sheet_id or sheet_name):
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_config_invalid",
            "Feishu sheet writeback requires spreadsheetToken and sheetId or sheetName.",
        )

    columns, rows = build_feishu_writeback_rows(stored_refs, config, run_id=run_id)
    if not rows:
        return {
            "enabled": True,
            "status": "no_eligible_records",
            "requestedRowCount": 0,
            "appendedRowCount": 0,
            "skippedRowCount": 0,
        }

    bridge_url = _writeback_bridge_url()
    if not bridge_url:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_bridge_unavailable",
            "Feishu sheet writeback is enabled, but the host lark-cli bridge URL is unavailable.",
        )
    payload = {
        "spreadsheet_token": spreadsheet_token,
        "sheet_id": sheet_id,
        "sheet_name": sheet_name,
        "columns": columns,
        "rows": rows,
        "idempotency_column": _text(config.get("idempotencyColumn")) or "运行ID",
        "sequence_column": _text(config.get("sequenceColumn")) or "序号",
    }
    headers = {"Content-Type": "application/json"}
    bridge_token = _text(os.getenv("LARK_CLI_BRIDGE_TOKEN"))
    if bridge_token:
        headers["X-Lark-CLI-Bridge-Token"] = bridge_token
    try:
        async with httpx.AsyncClient(timeout=130, follow_redirects=False) as client:
            response = await client.post(bridge_url, json=payload, headers=headers)
            response.raise_for_status()
            receipt = response.json()
    except httpx.TimeoutException as exc:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_timeout",
            "Feishu sheet writeback bridge timed out.",
            details={"errorType": type(exc).__name__},
        ) from exc
    except httpx.HTTPStatusError as exc:
        detail = _bridge_error_message(exc.response)
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_http_error",
            f"Feishu sheet writeback bridge returned HTTP {exc.response.status_code}: {detail}",
            details={"statusCode": exc.response.status_code},
        ) from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_response",
            f"Feishu sheet writeback bridge response was invalid: {exc}",
            details={"errorType": type(exc).__name__},
        ) from exc

    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        message = _text(receipt.get("message")) if isinstance(receipt, dict) else None
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_rejected",
            message or "Feishu sheet writeback bridge rejected the append.",
        )
    data = receipt.get("data")
    if not isinstance(data, dict):
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            "Feishu sheet writeback bridge omitted its verification receipt.",
        )
    appended_count = _receipt_count(data, "appended_count")
    skipped_count = _receipt_count(data, "skipped_count")
    verified_addresses = _required_string_list(data, "verified_addresses")
    if appended_count + skipped_count != len(rows):
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            "Feishu sheet writeback receipt counts do not match the requested rows.",
        )
    if len(verified_addresses) != appended_count or len(set(verified_addresses)) != len(
        verified_addresses
    ):
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            "Feishu sheet writeback receipt does not prove every appended row exactly once.",
        )
    returned_sheet_id = _text(data.get("sheet_id"))
    returned_sheet_name = _text(data.get("sheet_name"))
    if sheet_id and returned_sheet_id != sheet_id:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            "Feishu sheet writeback receipt identifies a different sheet.",
        )
    if sheet_name and returned_sheet_name != sheet_name:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            "Feishu sheet writeback receipt identifies a different sheet name.",
        )
    return {
        "enabled": True,
        "status": "synced",
        "requestedRowCount": len(rows),
        "appendedRowCount": appended_count,
        "skippedRowCount": skipped_count,
        "verifiedAddresses": verified_addresses,
    }


def _canonical_values(
    reference: dict[str, Any],
    raw: dict[str, Any],
    config: dict[str, Any],
    *,
    run_id: str,
    completed_at: str,
) -> dict[str, Any]:
    empty = _text(config.get("emptyValue")) or "页面未显示"
    source_fields = _dict(raw.get("source_fields"))
    response_data = _dict(raw.get("response_data"))
    question = _first_text(
        raw.get("question"),
        raw.get("title"),
        source_fields.get("推荐追问"),
        source_fields.get("原问句"),
    )
    answer = _first_text(raw.get("answer"), raw.get("content"))
    conversation_url = _text(raw.get("conversation_url"))
    links = _links(raw.get("links"), raw.get("citations"))
    references = [item for item in links if not _is_ecommerce_url(item[0])]
    ecommerce_urls = _unique_strings(
        [url for url, _title in links if _is_ecommerce_url(url)]
        + [
            cleaned
            for url in _URL_RE.findall(answer or "")
            if (cleaned := url.rstrip(".,;:!?，。；：！？")) and _is_ecommerce_url(cleaned)
        ]
    )
    followups = _string_list(raw.get("suggested_keywords"))
    search_keywords = _string_list(
        raw.get("search_keywords") or response_data.get("search_keywords")
    )
    videos = _string_list(
        raw.get("video_contents") or raw.get("videos") or response_data.get("video_contents")
    )
    screenshots = _string_list(
        raw.get("screenshots") or response_data.get("screenshots")
    )
    brand_present = "高吉星" in (answer or "")
    question_id = _question_id(raw, source_fields, config)
    reference_count = _integer(
        raw.get("reference_count"),
        _integer(response_data.get("reference_count"), len(references)),
    )
    search_keyword_count = _integer(
        raw.get("search_keyword_count"),
        _integer(response_data.get("search_keyword_count"), len(search_keywords)),
    )
    return {
        "sequence": None,
        "question_id": question_id or empty,
        "stage": _first_text(config.get("stage"), source_fields.get("阶段")) or empty,
        "question": question or empty,
        "answer": answer or empty,
        "search_keyword_count": search_keyword_count,
        "search_keywords": _numbered(search_keywords) or empty,
        "reference_count": reference_count,
        "references": _formatted_links(references) or empty,
        "followup_count": len(followups),
        "followups": _numbered(followups) or empty,
        "ecommerce_links": "\n".join(ecommerce_urls) or empty,
        "video_contents": _numbered(videos) or empty,
        "brand_present": "是" if brand_present else "否",
        "brand_observation": (
            "是否出现：是；依据：完整回答正文出现“高吉星”"
            if brand_present
            else "是否出现：否；是否自然推荐：否；位置：无；依据：完整回答正文未出现高吉星"
        ),
        "conversation_url": conversation_url or empty,
        "share_url": _share_url(raw.get("session_share_data")) or conversation_url or empty,
        "screenshots": "\n".join(screenshots) or empty,
        "completed_at": _first_text(
            raw.get("completed_at"), response_data.get("completed_at"), completed_at
        ),
        "evidence_status": (
            "通过" if answer and raw.get("answer_complete") is not False else "需复核"
        ),
        "idempotency_key": _idempotency_key(reference, raw, run_id=run_id),
    }


def _question_id(
    raw: dict[str, Any],
    source_fields: dict[str, Any],
    config: dict[str, Any],
) -> str | None:
    root = _first_text(
        raw.get("source_number"),
        source_fields.get("题号"),
        source_fields.get("编号"),
        source_fields.get("序号"),
    )
    if not root:
        return None
    followup_field = _text(config.get("followupSequenceField")) or "追问序号"
    sequence = _first_text(
        source_fields.get(followup_field),
        source_fields.get("追问序号"),
        source_fields.get("推荐追问序号"),
    )
    if not sequence or root.endswith(f"-{sequence}"):
        return root
    return f"{root}-{sequence}"


def _configured_columns(config: dict[str, Any]) -> list[str]:
    raw_columns = config.get("columns")
    if raw_columns is None:
        return list(DEFAULT_DOUBAO_RESULT_COLUMNS)
    columns = _string_list(raw_columns)
    if not columns or len(columns) > 100:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_config_invalid",
            "Feishu sheet writeback columns must contain 1 to 100 unique labels.",
        )
    if len(set(columns)) != len(columns):
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_config_invalid",
            "Feishu sheet writeback columns must be unique.",
        )
    return columns


def _mapped_value(
    selector: Any,
    values: dict[str, Any],
    raw: dict[str, Any],
    reference: dict[str, Any],
    *,
    empty: Any,
) -> Any:
    if isinstance(selector, str) and selector in values:
        return values[selector]
    if isinstance(selector, str):
        resolved = _resolve_path({"raw": raw, "record": reference, "computed": values}, selector)
        if resolved is not None:
            return _sheet_value(resolved)
        for source in (
            _dict(raw.get("source_fields")),
            raw,
            _dict(reference.get("normalizedData")),
        ):
            if selector in source:
                return _sheet_value(source[selector])
    return _text(empty) or "页面未显示"


def _resolve_path(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _sheet_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return "\n".join(_string_list(value))
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _is_doubao_record(raw: dict[str, Any]) -> bool:
    gaojixing = _dict(raw.get("gaojixing"))
    return bool(gaojixing) or (_text(raw.get("author")) or "").lower() == "doubao"


def _links(*sources: Any) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, list):
            continue
        for item in source:
            if isinstance(item, str):
                url, title = _text(item), ""
            elif isinstance(item, dict):
                url = _first_text(item.get("url"), item.get("href"))
                title = _first_text(item.get("title"), item.get("name")) or ""
            else:
                continue
            if not url or url in seen:
                continue
            seen.add(url)
            result.append((url, title))
    return result


def _formatted_links(links: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"{index}. {title} — {url}" if title else f"{index}. {url}"
        for index, (url, title) in enumerate(links, start=1)
    )


def _is_ecommerce_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ECOMMERCE_HOSTS)


def _share_url(value: Any) -> str | None:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return _first_text(value.get("url"), value.get("share_url"))
    if isinstance(value, list):
        for item in value:
            if result := _share_url(item):
                return result
    return None


def _idempotency_key(reference: dict[str, Any], raw: dict[str, Any], *, run_id: str) -> str:
    run_scope = _text(run_id) or "unknown-run"
    source_row = _first_text(
        raw.get("source_row_id"), _dict(raw.get("source_record")).get("record_id")
    )
    if source_row:
        return f"doubao-run-{run_scope}-row-{source_row}"
    source_fields = _dict(raw.get("source_fields"))
    source_number = _first_text(
        raw.get("source_number"),
        source_fields.get("题号"),
        source_fields.get("编号"),
        source_fields.get("序号"),
    )
    if source_number:
        return f"doubao-run-{run_scope}-question-{source_number}"
    conversation_url = _text(raw.get("conversation_url")) or ""
    match = _DOUBAO_CHAT_RE.search(conversation_url)
    if match:
        return f"doubao-run-{run_scope}-conversation-{match.group(1)}"
    record_id = _text(reference.get("recordId"))
    return f"doubao-run-{run_scope}-record-{record_id or 'unknown-record'}"


def _writeback_bridge_url() -> str | None:
    explicit = _text(os.getenv("FEISHU_SHEET_WRITEBACK_BRIDGE_URL"))
    if explicit:
        return explicit
    source = _text(os.getenv("LARK_CLI_BRIDGE_URL"))
    if not source:
        return None
    parsed = urlsplit(source)
    return urlunsplit((parsed.scheme, parsed.netloc, "/feishu/sheets/append", "", ""))


def _bridge_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:300] or "unknown bridge error"
    if isinstance(payload, dict):
        return _first_text(payload.get("message"), payload.get("error")) or "unknown bridge error"
    return "unknown bridge error"


def _numbered(values: list[str]) -> str:
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in (_text(item) for item in values) if value))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            candidate = _text(item)
        elif isinstance(item, dict):
            candidate = _first_text(
                item.get("text"),
                item.get("title"),
                item.get("content"),
                item.get("url"),
            )
        else:
            candidate = _text(str(item)) if item is not None else None
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _first_text(*values: Any) -> str | None:
    for value in values:
        if result := _text(value):
            return result
    return None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _receipt_count(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            f"Feishu sheet writeback receipt has an invalid {field}.",
        )
    return value


def _required_string_list(data: dict[str, Any], field: str) -> list[str]:
    value = data.get(field)
    if not isinstance(value, list) or any(not _text(item) for item in value):
        raise FeishuSheetWritebackError(
            "feishu_sheet_writeback_invalid_receipt",
            f"Feishu sheet writeback receipt has an invalid {field}.",
        )
    return [str(item).strip() for item in value]


__all__ = [
    "DEFAULT_DOUBAO_RESULT_COLUMNS",
    "FeishuSheetWritebackError",
    "build_feishu_writeback_rows",
    "feishu_writeback_enabled",
    "sync_feishu_sheet_writeback",
]
