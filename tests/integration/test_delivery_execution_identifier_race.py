"""Identifier preservation and initial execution-claim race proofs."""

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import get_settings
from backend.database import Base
from backend.models.delivery_execution import (
    ControlledReceiverDelivery,
    DeliveryExecution,
    DeliveryExecutionResult,
)
from backend.security import controlled_receiver as receiver
from backend.workflow import delivery_execution
from tests.integration.test_delivery_execution_migration_guards import _stored_frozen_decision


@pytest.fixture(autouse=True)
def receiver_registry(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "controlled_receiver_registry_json",
        json.dumps(
            {
                "receiver-primary": {
                    "url": "https://receiver.example/deliver",
                    "receiverIdentity": "receiver-a",
                    "credentialReference": "credential-a",
                    "requestKeyId": "request-a",
                    "receiptKeyId": "receipt-a",
                    "allowedNetworks": ["93.184.216.0/24"],
                    "durableStatus": "accepted",
                }
            }
        ),
    )
    request_secret = "request-secret-that-is-at-least-thirty-two-bytes"
    receipt_secret = "receipt-secret-that-is-at-least-thirty-two-bytes"
    monkeypatch.setattr(
        settings,
        "controlled_receiver_credentials_json",
        json.dumps({"credential-a": request_secret}),
    )
    monkeypatch.setattr(
        settings, "controlled_receiver_inbound_keys_json", json.dumps({"request-a": request_secret})
    )
    monkeypatch.setattr(
        settings, "controlled_receiver_receipt_keys_json", json.dumps({"receipt-a": receipt_secret})
    )


_IDENTIFIER_ROUNDTRIPS = (
    ("operation/with/slash", "claim/with/slash"),
    ("operation with spaces", "claim with spaces"),
    ("操作-雪", "声明-雪"),
    ("o" * 255, "c" * 255),
)


@pytest.mark.parametrize(("operation_id", "claim_id"), _IDENTIFIER_ROUNDTRIPS)
@pytest.mark.asyncio
async def test_receiver_roundtrip_preserves_raw_operation_and_claim_identifiers(
    client: AsyncClient, operation_id: str, claim_id: str
):
    payload = {
        "schemaVersion": "delivery-claim-manifest-v1",
        "claims": [{"claimId": claim_id, "contentHash": "a" * 64}],
        "manifestHashes": ["b" * 64],
    }
    value = {
        "version": "v2",
        "receiverIdentity": "receiver-a",
        "operationId": operation_id,
        "decisionHash": "d" * 64,
        "payloadHash": receiver.canonical_hash(payload),
        "payload": payload,
    }
    body = receiver.canonical_json(value)
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    headers = receiver.request_headers(
        body=body,
        endpoint=endpoint,
        operation_id=operation_id,
        decision_hash=value["decisionHash"],
        payload_hash=value["payloadHash"],
    )
    assert json.loads(body) == value
    assert headers["X-Controlled-Receiver-Operation-Id"] == base64.urlsafe_b64encode(
        operation_id.encode("utf-8")
    ).decode("ascii").rstrip("=")
    assert operation_id not in headers.values()
    receiver.verify_request(
        body=body,
        headers=headers,
        receiver_identity="receiver-a",
        operation_id=operation_id,
        decision_hash=value["decisionHash"],
        payload_hash=value["payloadHash"],
    )
    response = await client.post(
        "/api/v1/controlled-receiver/v2/deliver", content=body, headers=headers
    )
    assert response.status_code == 200
    receipt = response.json()["receipt"]
    assert receipt["operationId"] == operation_id
    assert (
        receiver.verify_receipt(
            receipt=receipt,
            endpoint=endpoint,
            operation_id=operation_id,
            decision_hash=value["decisionHash"],
            payload_hash=value["payloadHash"],
        )
        == "accepted"
    )


@pytest.mark.parametrize(("operation_id", "claim_id"), _IDENTIFIER_ROUNDTRIPS)
@pytest.mark.asyncio
async def test_executor_roundtrip_preserves_raw_operation_and_claim_identifiers(
    client: AsyncClient, db_session, monkeypatch, operation_id: str, claim_id: str
):
    scope, decision_id = await _stored_frozen_decision(
        db_session, operation_id=operation_id, claim_id=claim_id
    )
    posts: list[tuple[bytes, dict[str, str]]] = []

    async def send_to_durable_receiver(
        endpoint, body, headers, *, timeout_seconds, status_query=False
    ):
        posts.append((body, headers))
        path = (
            "/api/v1/controlled-receiver/v2/status"
            if status_query
            else "/api/v1/controlled-receiver/v2/deliver"
        )
        return await client.post(path, content=body, headers=headers)

    monkeypatch.setattr(delivery_execution, "pinned_post", send_to_durable_receiver)
    result = await delivery_execution.execute_delivery(
        db_session, scope=scope, decision_id=decision_id
    )
    await db_session.commit()
    assert result.outcome == "accepted"
    assert len(posts) == 1
    body, headers = posts[0]
    request_value = json.loads(body)
    assert request_value["operationId"] == operation_id
    assert request_value["payload"]["claims"][0]["claimId"] == claim_id
    assert headers["X-Controlled-Receiver-Operation-Id"] == base64.urlsafe_b64encode(
        operation_id.encode("utf-8")
    ).decode("ascii").rstrip("=")
    assert operation_id not in headers.values()
    stored = await db_session.scalar(select(ControlledReceiverDelivery))
    assert stored is not None
    assert stored.operation_id == operation_id


@pytest.mark.asyncio
async def test_empty_execution_table_claim_race_preserves_one_result_and_stable_replay(
    tmp_path: Path, monkeypatch
):
    database = tmp_path / "initial-claim-race.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 10},
        poolclass=NullPool,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as seed:
            scope, decision_id = await _stored_frozen_decision(seed)
        posts: list[bytes] = []

        async def signed_receiver_post(
            endpoint, body, headers, *, timeout_seconds, status_query=False
        ):
            posts.append(body)
            request = json.loads(body)
            receipt = receiver.receipt_payload(
                receiver_identity=endpoint.receiver_identity,
                operation_id=request["operationId"],
                decision_hash=request["decisionHash"],
                payload_hash=request["payloadHash"],
                durable_status=endpoint.durable_status,
                receipt_id=f"race-{len(posts)}",
                issued_at=datetime.now(UTC),
            )
            return Response(
                200,
                json={
                    "receipt": {
                        **receipt,
                        "keyId": endpoint.receipt_key_id,
                        "signature": receiver.sign_receipt(receipt, endpoint.receipt_key_id),
                    }
                },
            )

        original_flush = AsyncSession.flush
        initial_flushes = 0
        both_initial_inserts_ready = asyncio.Event()
        release_initial_inserts = asyncio.Event()

        async def synchronize_initial_execution_flush(session, *args, **kwargs):
            nonlocal initial_flushes
            if any(isinstance(value, DeliveryExecution) for value in session.new):
                initial_flushes += 1
                if initial_flushes == 2:
                    both_initial_inserts_ready.set()
                if initial_flushes <= 2:
                    await release_initial_inserts.wait()
            return await original_flush(session, *args, **kwargs)

        monkeypatch.setattr(delivery_execution, "pinned_post", signed_receiver_post)
        monkeypatch.setattr(AsyncSession, "flush", synchronize_initial_execution_flush)
        async with sessions() as first_session, sessions() as second_session:
            assert not list((await first_session.execute(select(DeliveryExecution))).scalars())
            first = asyncio.create_task(
                delivery_execution.execute_delivery(
                    first_session, scope=scope, decision_id=decision_id
                )
            )
            second = asyncio.create_task(
                delivery_execution.execute_delivery(
                    second_session, scope=scope, decision_id=decision_id
                )
            )
            await asyncio.wait_for(both_initial_inserts_ready.wait(), timeout=5)
            assert initial_flushes == 2
            release_initial_inserts.set()
            initial = await asyncio.gather(first, second, return_exceptions=True)
            assert all(not isinstance(value, BaseException) for value in initial)
            await first_session.commit()
            await second_session.commit()
        async with sessions() as verify:
            executions = list((await verify.execute(select(DeliveryExecution))).scalars())
            results = list((await verify.execute(select(DeliveryExecutionResult))).scalars())
            replay = await delivery_execution.execute_delivery(
                verify, scope=scope, decision_id=decision_id
            )
            await verify.commit()
        assert len(executions) == 1
        assert len(results) == 1
        assert results[0].execution_id == executions[0].id
        assert results[0].outcome == "accepted"
        assert replay.execution_id == executions[0].id
        assert replay.outcome == "accepted"
        assert replay.attempt_count == 1
        assert len(posts) == 1
    finally:
        await engine.dispose()
