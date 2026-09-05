from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging

import pytest
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models.connector_reply import ConnectorInboundReceipt, ConnectorInstallation
from backend.models.identity import Workspace
from backend.services import feishu_connector_runtime as runtime


@pytest.mark.parametrize(
    "content",
    [
        '{"text":"one","text":"two"}',
        '{"text":"one","extra":true}',
        json.dumps({"text": "<" * 20_001}),
    ],
)
def test_text_parser_rejects_ambiguous_or_oversize_content(content):
    with pytest.raises(ValueError):
        runtime._parse_text(content)


def _encrypt(key: str, payload: dict) -> str:
    plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    padding = AES.block_size - len(plaintext) % AES.block_size
    padded = plaintext + bytes([padding]) * padding
    iv = get_random_bytes(AES.block_size)
    cipher = AES.new(hashlib.sha256(key.encode()).digest(), AES.MODE_CBC, iv)
    return base64.b64encode(iv + cipher.encrypt(padded)).decode()


def _request(key: str, payload: dict, *, signature_valid=True):
    body = json.dumps({"encrypt": _encrypt(key, payload)}, separators=(",", ":")).encode()
    timestamp, nonce = "1770000000", "nonce"
    signature = hashlib.sha256((timestamp + nonce + key).encode() + body).hexdigest()
    if not signature_valid:
        signature = "0" * 64
    return body, {
        "x-lark-request-timestamp": timestamp,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": signature,
        "x-request-id": "request-id",
    }


def _plain_request(key: str, payload: dict):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp, nonce = "1770000000", "nonce"
    signature = hashlib.sha256((timestamp + nonce + key).encode() + body).hexdigest()
    return body, {
        "x-lark-request-timestamp": timestamp,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": signature,
    }


def _event(*, message_id="message-1", text="hello"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"event-{message_id}",
            "token": "verify",
            "create_time": "1770000000",
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant",
            "app_id": "cli",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou-user"},
                "sender_type": "user",
                "tenant_key": "tenant",
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc-p2p",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
        },
    }


def _challenge():
    return {"type": "url_verification", "challenge": "challenge-ok", "token": "verify"}


@pytest.fixture
async def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Workspace(id="ws", name="Workspace", slug="workspace", active=True))
        installation = ConnectorInstallation(
            public_id="public",
            workspace_id="ws",
            provider="feishu",
            name="Feishu",
            app_id="cli",
            tenant_key="tenant",
            status="active",
        )
        installation.app_secret = "secret"
        installation.encrypt_key = "encrypt-key"
        installation.verification_token = "verify"
        db.add(installation)
        await db.commit()
    yield factory, installation
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_sdk_strict_encrypted_signature_commits_before_ack(configured):
    factory, installation = configured
    body, headers = _request("encrypt-key", _event(text='<a title="x">'))
    response = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 200
    async with factory() as db:
        receipt = await db.scalar(select(ConnectorInboundReceipt))
        assert receipt.safe_content_text == "&lt;a title=&quot;x&quot;&gt;"
        assert receipt.status == "rejected" and receipt.stable_error_code == "principal_not_bound"


@pytest.mark.asyncio
async def test_real_sdk_rejects_bad_signature_without_database_write(configured):
    factory, installation = configured
    body, headers = _request("encrypt-key", _event(), signature_valid=False)
    response = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 500
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
async def test_real_sdk_accepts_only_encrypted_challenge(configured):
    factory, installation = configured
    encrypted_body, encrypted_headers = _request("encrypt-key", _challenge())
    encrypted = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=encrypted_headers,
        body=encrypted_body,
    )
    plain_body, plain_headers = _plain_request("encrypt-key", _challenge())
    plain = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=plain_headers,
        body=plain_body,
    )
    assert encrypted.status_code == 200
    assert b"challenge-ok" in encrypted.content
    assert plain.status_code == 400
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "header_change"),
    [
        (json.dumps(_event()).encode(), None),
        (b'{"encrypt":"","encrypt":"ambiguous"}', None),
        (b'{"encrypt":""}', None),
        (None, "missing"),
        (None, "duplicate"),
    ],
)
async def test_plain_ambiguous_or_unsigned_shape_never_reaches_sdk_or_database(
    configured, body, header_change
):
    factory, installation = configured
    encrypted_body, headers = _request("encrypt-key", _event())
    request_body = body if body is not None else encrypted_body
    request_headers: dict | list = headers
    if header_change == "missing":
        request_headers = {
            key: value for key, value in headers.items() if key != "x-lark-signature"
        }
    elif header_change == "duplicate":
        request_headers = [*headers.items(), ("X-Lark-Signature", headers["x-lark-signature"])]
    response = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=request_headers,
        body=request_body,
    )
    assert response.status_code == 400
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential_column", "stored_value"),
    [
        ("_app_secret_encrypted", ""),
        ("_encrypt_key_encrypted", "corrupt-ciphertext"),
        ("verification_token", ""),
    ],
)
async def test_missing_required_credential_is_503_before_sdk_or_receipt(
    configured, credential_column, stored_value, monkeypatch
):
    factory, installation = configured
    async with factory() as db:
        row = await db.get(ConnectorInstallation, installation.id)
        setattr(row, credential_column, stored_value)
        await db.commit()

    async def should_not_commit(*args, **kwargs):
        raise AssertionError("callback reached persistence")

    monkeypatch.setattr(runtime, "_commit_verified", should_not_commit)
    body, headers = _request("encrypt-key", _event())
    response = await runtime.dispatch_feishu_callback(
        installation=row,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 503
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
async def test_inactive_installation_without_verification_material_fails_closed(configured):
    factory, installation = configured
    async with factory() as db:
        row = await db.get(ConnectorInstallation, installation.id)
        row.status = "disabled"
        row._verification_token_encrypted = ""
        await db.commit()
    body, headers = _request("encrypt-key", _event(message_id="disabled-no-key"))
    response = await runtime.dispatch_feishu_callback(
        installation=row,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 503
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_error"),
    [("disabled", "installation_disabled"), ("revoked", "installation_revoked")],
)
async def test_inactive_installation_is_verified_then_rejected_and_acked(
    configured, state, expected_error
):
    factory, installation = configured
    async with factory() as db:
        row = await db.get(ConnectorInstallation, installation.id)
        if state == "disabled":
            row.status = "disabled"
        else:
            from datetime import UTC, datetime

            row.revoked_at = datetime.now(UTC)
        await db.commit()
    body, headers = _request("encrypt-key", _event(message_id=state))
    response = await runtime.dispatch_feishu_callback(
        installation=row,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 200
    async with factory() as db:
        receipt = await db.scalar(select(ConnectorInboundReceipt))
        assert receipt.status == "rejected"
        assert receipt.stable_error_code == expected_error
        assert receipt.binding_id is None


@pytest.mark.asyncio
async def test_disabled_installation_still_rejects_wrong_app_before_receipt(configured):
    factory, installation = configured
    async with factory() as db:
        row = await db.get(ConnectorInstallation, installation.id)
        row.status = "disabled"
        await db.commit()
    event = _event(message_id="disabled-wrong-app")
    event["header"]["app_id"] = "other-app"
    body, headers = _request("encrypt-key", event)
    response = await runtime.dispatch_feishu_callback(
        installation=row,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 500
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_field", "target_value"),
    [
        ("parent_id", "x" * 256),
        ("parent_id", {"invalid": True}),
        ("root_id", ["invalid"]),
    ],
)
async def test_reply_target_must_be_a_bounded_string(configured, target_field, target_value):
    factory, installation = configured
    event = _event(message_id="bad-target")
    event["event"]["message"][target_field] = target_value
    body, headers = _request("encrypt-key", event)
    response = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert response.status_code == 500
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 0


@pytest.mark.asyncio
async def test_persistence_failure_does_not_leak_sensitive_exception(
    configured, monkeypatch, caplog
):
    factory, installation = configured
    marker = "sensitive-body-binding-code-token"

    async def fail_commit(self):
        raise RuntimeError(marker)

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    body, headers = _request("encrypt-key", _event(text=marker))
    with caplog.at_level(logging.WARNING):
        response = await runtime.dispatch_feishu_callback(
            installation=installation,
            session_factory=factory,
            uri="/callback",
            headers=headers,
            body=body,
        )
    assert response.status_code == 500
    assert marker not in response.content.decode(errors="replace")
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_late_persistence_failure_is_observed_without_sensitive_text(
    configured, monkeypatch, caplog
):
    factory, installation = configured
    marker = "late-sensitive-sql-parameter"

    async def delayed_failure(self):
        await asyncio.sleep(0.02)
        raise RuntimeError(marker)

    monkeypatch.setattr(AsyncSession, "commit", delayed_failure)
    body, headers = _request("encrypt-key", _event(message_id="late-failure"))
    with caplog.at_level(logging.WARNING):
        response = await runtime.dispatch_feishu_callback(
            installation=installation,
            session_factory=factory,
            uri="/callback",
            headers=headers,
            body=body,
            commit_wait_seconds=0.001,
        )
        await asyncio.sleep(0.05)
    assert response.status_code == 500
    assert marker not in response.content.decode(errors="replace")
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_callback_exception_is_500_and_dispatcher_does_not_block_loop(configured):
    factory, installation = configured
    body, headers = _request("encrypt-key", _event(text=""))
    progressed = asyncio.Event()

    async def tick():
        await asyncio.sleep(0)
        progressed.set()

    tick_task = asyncio.create_task(tick())
    response = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    await tick_task
    assert progressed.is_set() and response.status_code == 500


@pytest.mark.asyncio
async def test_timeout_late_commit_then_retry_keeps_one_receipt(configured, monkeypatch):
    factory, installation = configured
    body, headers = _request("encrypt-key", _event(message_id="late"))
    original = runtime._commit_verified

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.05)
        await original(*args, **kwargs)

    monkeypatch.setattr(runtime, "_commit_verified", delayed)
    timed_out = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
        commit_wait_seconds=0.001,
    )
    assert timed_out.status_code == 500
    await asyncio.sleep(0.1)
    monkeypatch.setattr(runtime, "_commit_verified", original)
    replay = await runtime.dispatch_feishu_callback(
        installation=installation,
        session_factory=factory,
        uri="/callback",
        headers=headers,
        body=body,
    )
    assert replay.status_code == 200
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 1


@pytest.mark.asyncio
async def test_concurrent_same_message_is_one_committed_receipt(configured):
    factory, installation = configured
    body, headers = _request("encrypt-key", _event(message_id="concurrent"))
    responses = await asyncio.gather(
        *(
            runtime.dispatch_feishu_callback(
                installation=installation,
                session_factory=factory,
                uri="/callback",
                headers=headers,
                body=body,
            )
            for _ in range(2)
        )
    )
    assert [response.status_code for response in responses] == [200, 200]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorInboundReceipt)) == 1
