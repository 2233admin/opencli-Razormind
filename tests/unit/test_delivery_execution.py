"""Frozen delivery execution invariants independent of HTTP transport."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from backend.security.controlled_receiver import (
    ControlledReceiverSecurityError,
    canonical_hash,
)
from backend.workflow import delivery_execution
from backend.workflow.delivery_authorization import _current_policy
from backend.workflow.delivery_execution import (
    DeliveryExecutionConflictError,
    _payload,
    _retry_policy,
)


def _decision(*, payload_hash: str | None = None):
    payload = {
        "schemaVersion": "delivery-claim-manifest-v1",
        "claims": [{"claimId": "claim-1", "contentHash": "a" * 64}],
        "manifestHashes": ["b" * 64],
    }
    return SimpleNamespace(
        payload_schema_version=payload["schemaVersion"],
        selected_claims=payload["claims"],
        manifest_set=[{"manifestHash": "b" * 64}],
        payload_hash=payload_hash or canonical_hash(payload),
    )


def test_execution_reconstructs_only_frozen_projection_and_hash():
    assert _payload(_decision())["claims"] == [
        {"claimId": "claim-1", "contentHash": "a" * 64}
    ]


def test_execution_rejects_payload_hash_drift_before_network_io():
    with pytest.raises(DeliveryExecutionConflictError, match="payload hash"):
        _payload(_decision(payload_hash="x" * 64))


def test_execution_accepts_retry_values_only_from_the_exact_frozen_policy():
    _, snapshot, _ = _current_policy()
    assert _retry_policy(snapshot) == (30.0, 3)
    for mutation in (
        {"timeout": {}},
        {"retry": {**snapshot["retry"], "maxAttempts": 4}},
        {"retry": {**snapshot["retry"], "retryOn": []}},
    ):
        candidate = {**snapshot, **mutation}
        with pytest.raises(DeliveryExecutionConflictError, match="retry policy"):
            _retry_policy(candidate)


def _executor_fixture(monkeypatch, statuses: list[int], *, header_failure: bool = False):
    _, policy, _ = _current_policy()
    decision = SimpleNamespace(
        id="decision-1",
        operation_id="op-1",
        decision_hash="d" * 64,
        payload_hash="e" * 64,
        policy_snapshot=policy,
    )
    now = datetime.now(UTC)
    execution = SimpleNamespace(
        id="execution-1",
        decision_id=decision.id,
        operation_id=decision.operation_id,
        decision_hash=decision.decision_hash,
        payload_hash=decision.payload_hash,
        state="pending",
        final_outcome=None,
        final_result_id=None,
        final_reconciliation_id=None,
        lease_token=None,
        lease_acquired_at=None,
        send_started_at=None,
        reserved_attempt_number=None,
        cancel_requested_at=None,
        created_at=now,
        updated_at=now,
    )
    endpoint = SimpleNamespace(receiver_identity="receiver-a")
    results = []
    sleeps = []

    class DB:
        async def commit(self):
            return None

        async def refresh(self, _value):
            return None

        async def scalar(self, _statement):
            return execution

        async def flush(self):
            return None

    async def scoped(*_args, **_kwargs):
        return decision

    async def claim(*_args, **_kwargs):
        return execution, True

    async def result_rows(*_args, **_kwargs):
        return results

    async def target(*_args, **_kwargs):
        return None, endpoint

    async def record(
        _db,
        _execution,
        *,
        attempt,
        transport,
        http_status,
        receipt,
        protocol,
        outcome,
        receipt_id=None,
        receipt_hash=None,
    ):
        row = SimpleNamespace(
            id=f"result-{attempt}",
            attempt_number=attempt,
            transport_classification=transport,
            http_status=http_status,
            receipt_classification=receipt,
            protocol_classification=protocol,
            outcome=outcome,
            receipt_id=receipt_id,
            receipt_hash=receipt_hash,
            observed_at=now,
        )
        results.append(row)
        return row

    async def send(*_args, **_kwargs):
        status = statuses.pop(0)
        receipt = {"receiptId": "receipt-1"} if status == 200 else None
        return httpx.Response(status, json={"receipt": receipt})

    async def sleep(delay):
        sleeps.append(delay)

    def verify(**kwargs):
        if kwargs["receipt"] is None:
            raise ControlledReceiverSecurityError()
        return "accepted"

    monkeypatch.setattr(delivery_execution, "_scoped_decision", scoped)
    monkeypatch.setattr(
        delivery_execution,
        "_payload",
        lambda _decision: {
            "schemaVersion": "delivery-claim-manifest-v1",
            "claims": [{"claimId": "claim-1", "contentHash": "a" * 64}],
            "manifestHashes": ["b" * 64],
        },
    )
    monkeypatch.setattr(delivery_execution, "_claim", claim)
    monkeypatch.setattr(delivery_execution, "_results", result_rows)
    monkeypatch.setattr(delivery_execution, "_validated_target", target)
    monkeypatch.setattr(delivery_execution, "_record", record)
    monkeypatch.setattr(delivery_execution, "pinned_post", send)
    monkeypatch.setattr(
        delivery_execution,
        "request_headers",
        lambda **_kwargs: (
            (_ for _ in ()).throw(ControlledReceiverSecurityError())
            if header_failure
            else {}
        ),
    )
    monkeypatch.setattr(delivery_execution, "verify_receipt", verify)
    monkeypatch.setattr(delivery_execution.asyncio, "sleep", sleep)
    return DB(), decision, execution, results, sleeps


@pytest.mark.asyncio
async def test_executor_retries_only_5xx_exactly_three_times_with_one_then_two_second_delays(
    monkeypatch,
):
    db, decision, execution, results, sleeps = _executor_fixture(monkeypatch, [500, 500, 200])
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "accepted"
    assert [item.attempt_number for item in results] == [1, 2, 3]
    assert sleeps == [1, 2]
    assert execution.lease_token is None


@pytest.mark.asyncio
async def test_executor_retries_timeout_with_the_same_bounded_schedule(monkeypatch):
    db, decision, _execution, results, sleeps = _executor_fixture(monkeypatch, [200])
    responses = [
        httpx.TimeoutException("first"),
        httpx.TimeoutException("second"),
        httpx.Response(200, json={"receipt": {"receiptId": "receipt-1"}}),
    ]

    async def send(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(delivery_execution, "pinned_post", send)
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "accepted"
    assert [item.transport_classification for item in results] == [
        "transport-timeout",
        "transport-timeout",
        "http-success",
    ]
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_executor_converts_inflight_cancellation_to_terminal_unknown_without_retry(
    monkeypatch,
):
    db, decision, execution, results, sleeps = _executor_fixture(monkeypatch, [500])

    async def send(*_args, **_kwargs):
        execution.cancel_requested_at = datetime.now(UTC)
        return httpx.Response(500, json={"receipt": None})

    monkeypatch.setattr(delivery_execution, "pinned_post", send)
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "unknown"
    assert execution.state == "cancelled"
    assert len(results) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_executor_marks_stale_reserved_lease_unknown_without_resending(monkeypatch):
    db, decision, execution, results, _sleeps = _executor_fixture(monkeypatch, [])
    execution.lease_token = "old-lease"
    execution.lease_acquired_at = datetime(2000, 1, 1, tzinfo=UTC)
    execution.reserved_attempt_number = 2
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "unknown"
    assert execution.state == "blocked"
    assert [item.attempt_number for item in results] == [2]
    assert results[0].transport_classification == "crash-ambiguous"


@pytest.mark.asyncio
async def test_executor_recovers_stale_pre_send_reservation_without_ambiguous_result(monkeypatch):
    db, decision, execution, results, _sleeps = _executor_fixture(monkeypatch, [200])
    execution.state = "reserved"
    execution.lease_token = "old-lease"
    execution.lease_acquired_at = datetime(2000, 1, 1, tzinfo=UTC)
    execution.reserved_attempt_number = 1
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "accepted"
    assert [item.attempt_number for item in results] == [1]


@pytest.mark.asyncio
async def test_executor_does_not_send_while_a_competing_live_lease_exists(monkeypatch):
    db, decision, execution, results, _sleeps = _executor_fixture(monkeypatch, [])
    execution.lease_token = "competing-lease"
    execution.lease_acquired_at = datetime.now(UTC)
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome is None
    assert execution.state == "pending"
    assert results == []


@pytest.mark.asyncio
async def test_executor_honors_cancellation_before_reserving_a_send(monkeypatch):
    db, decision, execution, results, _sleeps = _executor_fixture(monkeypatch, [])
    execution.cancel_requested_at = datetime.now(UTC)
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "unknown"
    assert execution.state == "cancelled"
    assert results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 404])
async def test_executor_treats_unverified_2xx_and_4xx_as_terminal_unknown(monkeypatch, status):
    db, decision, execution, results, sleeps = _executor_fixture(monkeypatch, [status])
    monkeypatch.setattr(
        delivery_execution,
        "verify_receipt",
        lambda **_kwargs: (_ for _ in ()).throw(ControlledReceiverSecurityError()),
    )
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "unknown"
    assert execution.state == "blocked"
    assert len(results) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_executor_header_key_failure_finalizes_reserved_attempt(monkeypatch):
    db, decision, execution, results, sleeps = _executor_fixture(
        monkeypatch, [], header_failure=True
    )
    result = await delivery_execution.execute_delivery(
        db, scope=SimpleNamespace(), decision_id=decision.id
    )
    assert result.outcome == "unknown"
    assert execution.state == "blocked"
    assert execution.lease_token is None
    assert len(results) == 1
    assert sleeps == []
