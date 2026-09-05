"""Strict official-SDK Feishu callback adapter with commit-before-ACK semantics."""

from __future__ import annotations

import asyncio
import html
import json
import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.connector_reply import ConnectorInboundReceipt, ConnectorInstallation
from backend.services.connector_installation_service import (
    ConnectorCredentialUnavailableError,
    read_installation_credentials,
)
from backend.services.connector_receipt_service import (
    ReceiptConflictError,
    VerifiedFeishuMessage,
    canonical_request_hash,
    persist_verified_message,
)

logger = logging.getLogger(__name__)
MAX_SAFE_TEXT_LENGTH = 20_000
COMMIT_WAIT_SECONDS = 2.0
MAX_CALLBACK_BODY_BYTES = 256 * 1024
_REQUIRED_SIGNATURE_HEADERS = (
    "X-Lark-Request-Timestamp",
    "X-Lark-Request-Nonce",
    "X-Lark-Signature",
)


@dataclass(frozen=True)
class ConnectorCallbackResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


def _parse_text(content: Any) -> str:
    if not isinstance(content, str):
        raise ValueError("message content must be a JSON string")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate message content key")
            result[key] = value
        return result

    parsed = json.loads(content, object_pairs_hook=pairs)
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"text"}
        or not isinstance(parsed["text"], str)
    ):
        raise ValueError("text message content must contain exactly one text string")
    escaped = html.escape(parsed["text"], quote=True)
    if not escaped or len(escaped) > MAX_SAFE_TEXT_LENGTH:
        raise ValueError("safe message text length is invalid")
    return escaped


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValueError(f"invalid {field}")
    return value


def _optional_reply_target(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _required_string(value, field)


def _header_items(
    headers: Mapping[str, str] | Sequence[tuple[str | bytes, str | bytes]],
) -> list[tuple[str, str]]:
    items = headers.items() if isinstance(headers, Mapping) else headers
    normalized: list[tuple[str, str]] = []
    for raw_name, raw_value in items:
        name = raw_name.decode("latin-1") if isinstance(raw_name, bytes) else raw_name
        value = raw_value.decode("latin-1") if isinstance(raw_value, bytes) else raw_value
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("invalid callback header")
        normalized.append((name.lower(), value))
    return normalized


def _validate_sdk_request_shape(
    body: bytes,
    headers: Mapping[str, str] | Sequence[tuple[str | bytes, str | bytes]],
) -> dict[str, str]:
    """Admit only the encrypted SDK envelope; the SDK verifies all cryptography."""

    if not body or len(body) > MAX_CALLBACK_BODY_BYTES:
        raise ValueError("invalid encrypted callback envelope")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate callback envelope key")
            result[key] = value
        return result

    try:
        envelope = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid encrypted callback envelope") from exc
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"encrypt"}
        or not isinstance(envelope["encrypt"], str)
        or not envelope["encrypt"].strip()
    ):
        raise ValueError("invalid encrypted callback envelope")

    items = _header_items(headers)
    sdk_headers: dict[str, str] = {}
    for canonical in _REQUIRED_SIGNATURE_HEADERS:
        values = [value for name, value in items if name == canonical.lower()]
        if len(values) != 1 or not values[0].strip() or len(values[0]) > 255:
            raise ValueError("invalid callback signature headers")
        sdk_headers[canonical] = values[0]
    request_ids = [value for name, value in items if name == "x-request-id"]
    if len(request_ids) > 1 or any(not value.strip() or len(value) > 255 for value in request_ids):
        raise ValueError("invalid callback request id")
    if request_ids:
        sdk_headers["X-Request-Id"] = request_ids[0]
    return sdk_headers


def _verified_message(event: Any) -> VerifiedFeishuMessage:
    if (
        event.header is None
        or event.event is None
        or event.event.sender is None
        or event.event.message is None
    ):
        raise ValueError("incomplete Feishu message event")
    sender = event.event.sender
    message = event.event.message
    if sender.sender_id is None or sender.sender_type != "user":
        raise ValueError("Feishu sender must be a user")
    tenant_key = _required_string(event.header.tenant_key, "header tenant_key")
    if _required_string(sender.tenant_key, "sender tenant_key") != tenant_key:
        raise ValueError("sender tenant does not match event tenant")
    parent_id = _optional_reply_target(message.parent_id, "parent_id")
    root_id = _optional_reply_target(message.root_id, "root_id")
    return VerifiedFeishuMessage(
        provider_message_id=_required_string(message.message_id, "message_id"),
        provider_event_id=_required_string(event.header.event_id, "event_id"),
        app_id=_required_string(event.header.app_id, "app_id"),
        tenant_key=tenant_key,
        sender_open_id=_required_string(sender.sender_id.open_id, "sender open_id"),
        chat_id=_required_string(message.chat_id, "chat_id"),
        chat_type=_required_string(message.chat_type, "chat_type"),
        message_type=_required_string(message.message_type, "message_type"),
        reply_to_message_id=parent_id or root_id,
        safe_content_text=_parse_text(message.content),
    )


async def _commit_verified(
    session_factory: async_sessionmaker[AsyncSession],
    installation_id: str,
    message: VerifiedFeishuMessage,
) -> None:
    try:
        async with session_factory() as db:
            receipt = await persist_verified_message(db, installation_id, message)
            await db.commit()
            if receipt.id:
                return
    except ReceiptConflictError as exc:
        if str(exc) != "concurrent_receipt_insert":
            raise

    # A concurrent transaction or a timed-out prior request may own the unique
    # message key. Resolve only the already committed immutable row.
    expected_intent = "binding" if message.safe_content_text.startswith("绑定 ") else "reply"
    expected_hash = canonical_request_hash(installation_id, message, expected_intent)
    for _ in range(10):
        async with session_factory() as db:
            existing = await db.scalar(
                select(ConnectorInboundReceipt).where(
                    ConnectorInboundReceipt.installation_id == installation_id,
                    ConnectorInboundReceipt.provider_message_id == message.provider_message_id,
                )
            )
            if existing is not None:
                if existing.request_hash != expected_hash:
                    raise ReceiptConflictError("provider_message_id_payload_conflict")
                return
        await asyncio.sleep(0.02)
    raise ReceiptConflictError("concurrent_receipt_not_committed")


def _observe_late_commit(future: Future[None]) -> None:
    try:
        exc = future.exception()
    except Exception as observe_error:  # pragma: no cover - defensive logging only
        logger.warning("connector late commit observation failed: %s", type(observe_error).__name__)
        return
    if exc is not None:
        logger.warning("connector late commit failed: %s", type(exc).__name__)


async def dispatch_feishu_callback(
    *,
    installation: ConnectorInstallation,
    session_factory: async_sessionmaker[AsyncSession],
    uri: str,
    headers: Mapping[str, str] | Sequence[tuple[str | bytes, str | bytes]],
    body: bytes,
    commit_wait_seconds: float = COMMIT_WAIT_SECONDS,
) -> ConnectorCallbackResponse:
    from lark_channel.channel.config import SecurityConfig
    from lark_channel.core.model import RawRequest
    from lark_channel.event.dispatcher_handler import EventDispatcherHandler

    loop = asyncio.get_running_loop()

    def callback(event: Any) -> None:
        message = _verified_message(event)
        if message.app_id != installation.app_id or message.tenant_key != installation.tenant_key:
            raise ValueError("event does not match connector installation")
        future = asyncio.run_coroutine_threadsafe(
            _commit_verified(session_factory, installation.id, message), loop
        )
        try:
            future.result(timeout=commit_wait_seconds)
        except FutureTimeoutError:
            future.add_done_callback(_observe_late_commit)
            raise RuntimeError("connector_persistence_timeout") from None
        except Exception:
            raise RuntimeError("connector_persistence_failed") from None

    try:
        credentials = read_installation_credentials(installation)
    except ConnectorCredentialUnavailableError:
        return ConnectorCallbackResponse(
            status_code=503,
            headers={"content-type": "application/json"},
            content=b'{"detail":"Connector verification unavailable"}',
        )
    try:
        sdk_headers = _validate_sdk_request_shape(body, headers)
    except ValueError:
        return ConnectorCallbackResponse(
            status_code=400,
            headers={"content-type": "application/json"},
            content=b'{"detail":"Invalid connector callback"}',
        )
    security = SecurityConfig(
        mode="strict",
        allow_unsigned_encrypted_webhook=False,
        strict_error_response=True,
        strict_content_text=True,
    )
    dispatcher = (
        EventDispatcherHandler.builder(
            credentials.encrypt_key, credentials.verification_token, security=security
        )
        .register_p2_im_message_receive_v1(callback)
        .build()
    )
    request = RawRequest()
    request.uri = uri
    # The SDK's RawRequest map uses exact canonical header names.
    request.headers = sdk_headers
    request.body = body
    response = await asyncio.to_thread(dispatcher.do, request)
    return ConnectorCallbackResponse(
        status_code=int(response.status_code or 500),
        headers=dict(response.headers),
        content=response.content or b"",
    )
