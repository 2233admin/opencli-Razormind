from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.models.delivery_connection import DeliveryAttempt, DeliveryConnection
from backend.services.feishu_bitable_delivery import (
    FeishuDeliveryError,
    create_record,
    deliver_record_once,
    probe_bitable,
)


@pytest.mark.asyncio
async def test_create_record_uses_fixed_host_and_never_returns_token():
    connection = SimpleNamespace(app_id="app", app_secret="secret")
    response = MagicMock(status_code=200)
    response.json.side_effect = [
        {"code": 0, "tenant_access_token": "tenant-secret"},
        {"code": 0, "data": {"records": [{"record_id": "rec_1"}]}},
    ]
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    with patch("backend.services.feishu_bitable_delivery.httpx.AsyncClient", return_value=context):
        assert await create_record(connection, "app_token", "table", {"Title": "x"}) == "rec_1"
    assert "tenant-secret" not in str(response.json.call_args_list)


@pytest.mark.asyncio
async def test_create_record_redacts_business_failure():
    connection = SimpleNamespace(app_id="app", app_secret="secret")
    response = MagicMock(status_code=200)
    response.json.return_value = {"code": 999, "msg": "secret diagnostic"}
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    with patch("backend.services.feishu_bitable_delivery.httpx.AsyncClient", return_value=context):
        with pytest.raises(FeishuDeliveryError, match="authentication_failed"):
            await create_record(connection, "app_token", "table", {})


@pytest.mark.asyncio
async def test_delivery_is_idempotent_per_target_and_record(db_session):
    connection = DeliveryConnection(
        name="Feishu", app_id="cli_test", app_secret="secret", enabled=True
    )
    db_session.add(connection)
    await db_session.flush()
    with patch(
        "backend.services.feishu_bitable_delivery.create_record",
        new=AsyncMock(return_value="remote_1"),
    ) as create:
        first = await deliver_record_once(
            db_session,
            connection=connection,
            app_token="app_token",
            table_id="table",
            record_id="record-1",
            workflow_run_id="run-1",
            evidence_digest="a" * 64,
            fields={"Record ID": "record-1"},
            field_map={"recordId": "Record ID"},
        )
        second = await deliver_record_once(
            db_session,
            connection=connection,
            app_token="app_token",
            table_id="table",
            record_id="record-1",
            workflow_run_id="run-1",
            evidence_digest="a" * 64,
            fields={"Record ID": "record-1"},
            field_map={"recordId": "Record ID"},
        )

    assert first.id == second.id
    assert second.status == "succeeded"
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_pending_attempt_is_explicitly_in_progress(db_session):
    connection = DeliveryConnection(
        name="Feishu", app_id="cli_test", app_secret="secret", enabled=True
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        DeliveryAttempt(
            connection_id=connection.id,
            app_token="app_token",
            table_id="table",
            record_id="record-1",
            workflow_run_id="run-1",
            evidence_digest="a" * 64,
            field_map={"recordId": "Record ID"},
            status="pending",
        )
    )
    await db_session.commit()

    with patch(
        "backend.services.feishu_bitable_delivery.create_record",
        new=AsyncMock(return_value="remote-1"),
    ) as create:
        with pytest.raises(FeishuDeliveryError, match="delivery_in_progress"):
            await deliver_record_once(
                db_session,
                connection=connection,
                app_token="app_token",
                table_id="table",
                record_id="record-1",
                workflow_run_id="run-1",
                evidence_digest="a" * 64,
                fields={"Record ID": "record-1"},
                field_map={"recordId": "Record ID"},
            )
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_pending_attempt_can_retry_after_reservation_crash(db_session):
    connection = DeliveryConnection(
        name="Feishu", app_id="cli_test", app_secret="secret", enabled=True
    )
    db_session.add(connection)
    await db_session.flush()
    stale = DeliveryAttempt(
        connection_id=connection.id,
        app_token="app_token",
        table_id="table",
        record_id="record-1",
        workflow_run_id="run-1",
        evidence_digest="a" * 64,
        field_map={"recordId": "Record ID"},
        status="pending",
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(stale)
    await db_session.commit()

    with patch(
        "backend.services.feishu_bitable_delivery.create_record",
        new=AsyncMock(return_value="remote-1"),
    ) as create:
        retried = await deliver_record_once(
            db_session,
            connection=connection,
            app_token="app_token",
            table_id="table",
            record_id="record-1",
            workflow_run_id="run-1",
            evidence_digest="a" * 64,
            fields={"Record ID": "record-1"},
            field_map={"recordId": "Record ID"},
        )

    assert retried.id == stale.id
    assert retried.status == "succeeded"
    assert retried.remote_record_id == "remote-1"
    create.assert_awaited_once()

@pytest.mark.asyncio
async def test_probe_bitable_uses_official_host_and_counts_fields():
    connection = SimpleNamespace(app_id="app", app_secret="secret")
    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"code": 0, "tenant_access_token": "tenant-secret"}
    fields_response = MagicMock(status_code=200)
    fields_response.json.return_value = {"code": 0, "data": {"items": [{}, {}]}}
    client = AsyncMock()
    client.post = AsyncMock(return_value=token_response)
    client.get = AsyncMock(return_value=fields_response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    with patch(
        "backend.services.feishu_bitable_delivery.httpx.AsyncClient",
        return_value=context,
    ) as client_factory:
        result = await probe_bitable(connection, "app_token", "table")

    assert result == {"ok": True, "field_count": 2}
    assert all(
        call.kwargs["base_url"] == "https://open.feishu.cn"
        for call in client_factory.call_args_list
    )
    assert client.get.await_args.kwargs["headers"] == {
        "Authorization": "Bearer tenant-secret"
    }


@pytest.mark.asyncio
async def test_failed_attempt_is_durable_and_can_retry(db_session):
    connection = DeliveryConnection(
        name="Feishu", app_id="cli_test", app_secret="secret", enabled=True
    )
    db_session.add(connection)
    await db_session.flush()
    arguments = {
        "connection": connection,
        "app_token": "app_token",
        "table_id": "table",
        "record_id": "record-1",
        "workflow_run_id": "run-1",
        "evidence_digest": "a" * 64,
        "fields": {"Record ID": "record-1"},
        "field_map": {"recordId": "Record ID"},
    }
    with patch(
        "backend.services.feishu_bitable_delivery.create_record",
        new=AsyncMock(side_effect=FeishuDeliveryError("rate_limited")),
    ):
        with pytest.raises(FeishuDeliveryError, match="rate_limited"):
            await deliver_record_once(db_session, **arguments)

    failed = (await db_session.execute(select(DeliveryAttempt))).scalar_one()
    assert failed.status == "failed"
    assert failed.error_code == "rate_limited"

    with patch(
        "backend.services.feishu_bitable_delivery.create_record",
        new=AsyncMock(return_value="remote-1"),
    ) as create:
        retried = await deliver_record_once(db_session, **arguments)
    assert retried.id == failed.id
    assert retried.status == "succeeded"
    create.assert_awaited_once()
