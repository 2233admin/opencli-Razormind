from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import pytest
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
