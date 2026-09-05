"""Read bounded keyword rows from a Feishu Bitable.

The channel intentionally supports a tenant access token supplied through the
existing encrypted source-credential store (credential key: ``token``). App
tokens and table ids are identifiers, not credentials, and stay in the source
configuration. The channel is read-only and never calls a write endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import httpx

from backend.channels.base import (
    AbstractChannel,
    Capabilities,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel

_DEFAULT_BASE_URL = "https://open.feishu.cn/open-apis"
_MAX_PAGE_SIZE = 500
_DEFAULT_PAGE_SIZE = 100
_DEFAULT_MAX_ROWS = 500


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, list):
        return "".join(_text(part) for part in value).strip()
    if isinstance(value, dict):
        return _text(value.get("text") or value.get("name") or value.get("value"))
    return ""


def _business_number(fields: dict[str, Any], config: dict[str, Any]) -> str:
    """Read the configured business number without tying it to one sheet schema."""
    configured = _text(config.get("number_field"))
    candidates = [configured] if configured else []
    candidates.extend(["编号", "序号", "No.", "NO", "no", "ID", "id"])
    for key in candidates:
        if key and key in fields:
            value = _text(fields.get(key))
            if value:
                return value
    folded = {str(key).casefold(): value for key, value in fields.items()}
    for key in candidates:
        value = _text(folded.get(key.casefold())) if key else ""
        if value:
            return value
    return ""


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(1, parsed), maximum)


def _non_negative_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(0, parsed), maximum)


def _cli_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        columns = data.get("fields")
        record_ids = data.get("record_id_list")
        if isinstance(columns, list):
            return [
                {
                    "record_id": record_ids[index]
                    if isinstance(record_ids, list) and index < len(record_ids)
                    else str(index),
                    "fields": {
                        str(column): row[column_index]
                        for column_index, column in enumerate(columns)
                        if column_index < len(row)
                    },
                }
                for index, row in enumerate(data["data"])
                if isinstance(row, list)
            ]
    for candidate in (
        payload.get("records"),
        payload.get("items"),
        (payload.get("data") or {}).get("items") if isinstance(payload.get("data"), dict) else None,
    ):
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


@register_channel
class FeishuTableChannel(AbstractChannel):
    """Fetch eligible keyword rows from a Feishu Bitable table."""

    channel_type = "feishu_table"
    capabilities = Capabilities(
        incremental=False,
        paginated=True,
        auth_kind="bearer",
        session_affinity=False,
        default_rate="30/min",
    )

    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        """Keep direct calls useful for validation/tests; production uses fetch()."""
        return ChannelResult.fail(
            "feishu_table requires the source runner so its encrypted token can be injected",
            error_type="MissingSourceAuth",
        )

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        config = ctx.config
        errors = await self.validate_config(config)
        if errors:
            raise ChannelFetchError("; ".join(errors), error_type="InvalidSourceConfig")
        if str(config.get("transport") or "cli").lower() == "cli":
            return await self._fetch_with_lark_cli(ctx)

        token = (ctx.auth.token if ctx.auth else None) or ""
        if not token:
            raise ChannelFetchError(
                "Feishu source credential 'token' is not configured",
                error_type="MissingSourceAuth",
            )

        # Keep the API host fixed. A caller-controlled base URL would turn this
        # source connection into an SSRF primitive; regional endpoints can be
        # added only through an explicit, allowlisted implementation change.
        base_url = _DEFAULT_BASE_URL
        endpoint = (
            f"{base_url}/bitable/v1/apps/{config['app_token']}"
            f"/tables/{config['table_id']}/records"
        )
        page_size = _positive_int(config.get("page_size"), _DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE)
        max_rows = _positive_int(config.get("max_rows"), _DEFAULT_MAX_ROWS, 5000)
        page_token = (ctx.cursor or {}).get("page_token")
        if page_token is None:
            page_token = ""
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        view_id = _text(config.get("view_id"))
        if view_id:
            params["view_id"] = view_id
        field_names = config.get("field_names")
        if isinstance(field_names, list) and field_names:
            params["field_names"] = ",".join(_text(name) for name in field_names if _text(name))

        client = ctx.http
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=30, follow_redirects=False)
        try:
            response = await client.get(
                endpoint,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ChannelFetchError("Feishu table request timed out", type(exc).__name__) from exc
        except httpx.HTTPStatusError as exc:
            raise ChannelFetchError(
                f"Feishu table returned HTTP {exc.response.status_code}",
                type(exc).__name__,
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ChannelFetchError(
                f"Feishu table response was invalid: {exc}", type(exc).__name__
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(payload, dict) or payload.get("code", 0) not in (0, None):
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("msg") if isinstance(payload, dict) else "invalid response"
            raise ChannelFetchError(
                f"Feishu table API error {code}: {message}", "FeishuAPIError"
            )

        data = payload.get("data") if isinstance(payload, dict) else None
        data = data if isinstance(data, dict) else {}
        records = data.get("items")
        if not isinstance(records, list):
            records = []
        remaining = max_rows
        items: list[dict[str, Any]] = []
        keyword_field = str(config["keyword_field"])
        status_field = _text(config.get("status_field"))
        eligible_status = _text(config.get("eligible_status"))
        source_group = _text(config.get("source_group")) or "feishu-keywords"
        for record in records[:remaining]:
            if not isinstance(record, dict):
                continue
            fields = record.get("fields")
            if not isinstance(fields, dict):
                continue
            keyword = _text(fields.get(keyword_field))
            if not keyword:
                continue
            status = _text(fields.get(status_field)) if status_field else ""
            if eligible_status and status != eligible_status:
                continue
            row_id = _text(record.get("record_id")) or _text(record.get("id"))
            if not row_id:
                continue
            source_number = _business_number(fields, config)
            items.append(
                {
                    "id": f"feishu:{ctx.source_id or 'source'}:{row_id}",
                    "source_row_id": row_id,
                    "source_number": source_number,
                    "keyword": keyword,
                    "title": keyword,
                    "content": keyword,
                    "status": status,
                    "source": "feishu_table",
                    "source_group": source_group,
                    "sourceGroup": source_group,
                    "feishu": {
                        "record_id": row_id,
                        "number": source_number,
                        "app_token": config["app_token"],
                        "table_id": config["table_id"],
                    },
                    "fields": fields,
                }
            )

        has_more = bool(data.get("has_more")) and len(records) < max_rows
        next_page_token = _text(data.get("page_token"))
        next_cursor = {"page_token": next_page_token} if has_more and next_page_token else None
        return FetchResult(
            items=items,
            next_cursor=next_cursor,
            has_more=bool(next_cursor),
            metadata={"source": "feishu_table", "rowCount": len(items), "bounded": True},
        )

    async def _fetch_with_lark_cli(self, ctx: FetchContext) -> FetchResult:
        config = ctx.config
        bridge_url = _text(config.get("cli_bridge_url") or os.getenv("LARK_CLI_BRIDGE_URL"))
        if bridge_url:
            return await self._fetch_with_lark_cli_bridge(ctx, bridge_url)

        binary = shutil.which(str(config.get("cli_binary") or "lark-cli"))
        if not binary:
            raise ChannelFetchError("lark-cli binary was not found", "FileNotFoundError")
        base_token = _text(config.get("app_token"))
        if not base_token:
            raise ChannelFetchError("'app_token' is required for lark-cli", "InvalidSourceConfig")
        max_rows = _positive_int(config.get("max_rows"), _DEFAULT_MAX_ROWS, 5000)
        offset = _non_negative_int((ctx.cursor or {}).get("offset"), 0, 5_000_000)
        remaining = max_rows - offset
        if remaining <= 0:
            return FetchResult(
                items=[],
                next_cursor=None,
                has_more=False,
                metadata={"source": "feishu_table", "transport": "lark-cli", "rowCount": 0},
            )
        limit = min(_positive_int(config.get("page_size"), _DEFAULT_PAGE_SIZE, 200), remaining)
        args = [
            binary,
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            str(config["table_id"]),
            "--limit",
            str(limit),
            "--format",
            "json",
        ]
        if _text(config.get("view_id")):
            args.extend(["--view-id", _text(config["view_id"])])
        if _text(config.get("profile")):
            args.extend(["--profile", _text(config["profile"])])
        if offset:
            args.extend(["--offset", str(offset)])
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except TimeoutError as exc:
            raise ChannelFetchError("lark-cli record-list timed out", "TimeoutError") from exc
        if proc.returncode:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise ChannelFetchError(
                f"lark-cli record-list failed: {detail or proc.returncode}",
                "LarkCLIError",
            )
        try:
            payload = json.loads(stdout.decode(errors="replace"))
        except json.JSONDecodeError as exc:
            raise ChannelFetchError("lark-cli returned invalid JSON", "JSONDecodeError") from exc
        rows = _cli_rows(payload)[:remaining]
        result = await self._rows_to_result(ctx, rows)
        cli_data = payload.get("data") if isinstance(payload, dict) else None
        cli_has_more = bool(cli_data.get("has_more")) if isinstance(cli_data, dict) else False
        has_more = cli_has_more and offset + len(rows) < max_rows
        next_cursor = {"offset": offset + len(rows)} if has_more and rows else None
        return FetchResult(
            items=result,
            next_cursor=next_cursor,
            has_more=bool(next_cursor),
            metadata={"source": "feishu_table", "transport": "lark-cli", "rowCount": len(result)},
        )

    async def _fetch_with_lark_cli_bridge(self, ctx: FetchContext, bridge_url: str) -> FetchResult:
        """Run the host's authenticated lark-cli through the read-only bridge."""
        config = ctx.config
        offset = _non_negative_int((ctx.cursor or {}).get("offset"), 0, 5_000_000)
        max_rows = _positive_int(config.get("max_rows"), _DEFAULT_MAX_ROWS, 5000)
        remaining = max_rows - offset
        if remaining <= 0:
            return FetchResult(
                items=[],
                next_cursor=None,
                has_more=False,
                metadata={"source": "feishu_table", "transport": "lark-cli-bridge", "rowCount": 0},
            )
        limit = min(_positive_int(config.get("page_size"), _DEFAULT_PAGE_SIZE, 200), remaining)
        payload = {
            "app_token": config.get("app_token"),
            "table_id": config.get("table_id"),
            "view_id": config.get("view_id"),
            "profile": config.get("profile"),
            "limit": limit,
            "offset": offset,
        }
        headers: dict[str, str] = {}
        bridge_token = _text(os.getenv("LARK_CLI_BRIDGE_TOKEN"))
        if bridge_token:
            headers["X-Lark-CLI-Bridge-Token"] = bridge_token
        try:
            async with httpx.AsyncClient(timeout=130, follow_redirects=False) as client:
                response = await client.post(bridge_url, json=payload, headers=headers)
                response.raise_for_status()
                cli_payload = response.json()
        except httpx.TimeoutException as exc:
            raise ChannelFetchError("lark-cli bridge timed out", type(exc).__name__) from exc
        except httpx.HTTPStatusError as exc:
            raise ChannelFetchError(
                f"lark-cli bridge returned HTTP {exc.response.status_code}", type(exc).__name__
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ChannelFetchError(
                f"lark-cli bridge response was invalid: {exc}", type(exc).__name__
            ) from exc

        rows = _cli_rows(cli_payload)[:remaining]
        result = await self._rows_to_result(ctx, rows)
        cli_data = cli_payload.get("data") if isinstance(cli_payload, dict) else None
        cli_has_more = bool(cli_data.get("has_more")) if isinstance(cli_data, dict) else False
        has_more = cli_has_more and offset + len(rows) < max_rows
        next_cursor = {"offset": offset + len(rows)} if has_more and rows else None
        return FetchResult(
            items=result,
            next_cursor=next_cursor,
            has_more=bool(next_cursor),
            metadata={
                "source": "feishu_table",
                "transport": "lark-cli-bridge",
                "rowCount": len(result),
            },
        )

    async def _rows_to_result(
        self, ctx: FetchContext, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        config = ctx.config
        remaining = _positive_int(config.get("max_rows"), _DEFAULT_MAX_ROWS, 5000)
        keyword_field = str(config["keyword_field"])
        status_field = _text(config.get("status_field"))
        eligible_status = _text(config.get("eligible_status"))
        source_group = _text(config.get("source_group")) or "feishu-keywords"
        items: list[dict[str, Any]] = []
        for record in records[:remaining]:
            fields = record.get("fields")
            if not isinstance(fields, dict):
                fields = record
            keyword = _text(fields.get(keyword_field))
            if not keyword:
                continue
            status = _text(fields.get(status_field)) if status_field else ""
            if eligible_status and status != eligible_status:
                continue
            row_id = _text(record.get("record_id")) or _text(record.get("id"))
            if not row_id:
                continue
            source_number = _business_number(fields, config)
            items.append(
                {
                    "id": f"feishu:{ctx.source_id or 'source'}:{row_id}",
                    "source_row_id": row_id,
                    "source_number": source_number,
                    "keyword": keyword,
                    "title": keyword,
                    "content": keyword,
                    "status": status,
                    "source": "feishu_table",
                    "source_group": source_group,
                    "sourceGroup": source_group,
                    "feishu": {
                        "record_id": row_id,
                        "number": source_number,
                        "app_token": config["app_token"],
                        "table_id": config["table_id"],
                    },
                    "fields": fields,
                }
            )
        return items

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for key in ("app_token", "table_id", "keyword_field"):
            if not _text(config.get(key)):
                errors.append(f"'{key}' is required for feishu_table")
        if config.get("max_rows") is not None:
            try:
                if int(config["max_rows"]) < 1 or int(config["max_rows"]) > 5000:
                    errors.append("'max_rows' must be between 1 and 5000")
            except (TypeError, ValueError):
                errors.append("'max_rows' must be an integer")
        return errors

    async def health_check(
        self, config: dict[str, Any] | None = None, source_id: str | None = None
    ) -> bool:
        resolved_config = config or {}
        errors = await self.validate_config(resolved_config)
        if errors:
            return False

        # CLI transport authenticates through the operator's local lark-cli
        # session, so probe one bounded row instead of requiring a second
        # encrypted bearer token.
        if str(resolved_config.get("transport") or "cli").lower() == "cli":
            try:
                await self.fetch(
                    FetchContext(
                        config={**resolved_config, "page_size": 1, "max_rows": 1},
                        params={},
                        source_id=source_id,
                    )
                )
            except Exception:
                return False
            return True

        if not source_id:
            return False
        from backend.auth.manager import AuthManager

        return "token" in await AuthManager().list_keys(source_id)

    def identity(self, item: dict[str, Any]) -> str | None:
        source_row_id = _text(item.get("source_row_id"))
        return f"feishu:{source_row_id}" if source_row_id else None
