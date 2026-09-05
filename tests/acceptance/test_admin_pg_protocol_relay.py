from __future__ import annotations

import asyncio
import pytest
from tests.acceptance.fault_tools import admin_pg_protocol_relay as relay


def _startup() -> bytes:
    return (8).to_bytes(4, "big") + (196608).to_bytes(4, "big")


def _frontend(message_type: bytes, body: bytes) -> bytes:
    return message_type + (len(body) + 4).to_bytes(4, "big") + body


def _backend(message_type: bytes, body: bytes) -> bytes:
    return _frontend(message_type, body)


def _parse(name: bytes, sql: bytes) -> relay.FrontendFrame:
    body = name + b"\0" + sql + b"\0"
    return relay.FrontendFrame(_frontend(b"P", body), b"P", body, name, sql)


def _bind(name: bytes, value: bytes) -> relay.FrontendFrame:
    body = b"\0" + name + b"\0" + value
    return relay.FrontendFrame(_frontend(b"B", body), b"B", body, name)


CLAIM = (
    b"SELECT delivery_executions.id, delivery_executions.decision_id FROM delivery_executions "
    b"WHERE delivery_executions.decision_id = $1::UUID FOR UPDATE"
)
RESERVE = (
    b"UPDATE delivery_executions SET state=$1::VARCHAR, lease_token=$2::VARCHAR, "
    b"lease_acquired_at=$3::TIMESTAMP WITH TIME ZONE, send_started_at=$4::TIMESTAMP "
    b"WITH TIME ZONE, reserved_attempt_number=$5::INTEGER WHERE delivery_executions.id = $6::UUID"
)
LOCKED_READ = (
    b"SELECT delivery_executions.id, delivery_executions.decision_id FROM delivery_executions "
    b"WHERE delivery_executions.id = $1::UUID FOR UPDATE"
)
RESERVE_WITHOUT_SEND_STARTED = (
    b"UPDATE delivery_executions SET state=$1::VARCHAR, lease_token=$2::VARCHAR, "
    b"lease_acquired_at=$3::TIMESTAMP WITH TIME ZONE, reserved_attempt_number=$4::INTEGER "
    b"WHERE delivery_executions.id = $5::UUID"
)


def test_reservation_update_does_not_require_unchanged_send_started_column():
    assert relay._reservation_update(RESERVE) is True
    assert relay._reservation_update(RESERVE_WITHOUT_SEND_STARTED) is True



def test_relay_arm_requires_the_authenticated_token(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "relay-token")

    with pytest.raises(relay.HTTPException) as denied:
        relay._authorize("wrong-token")

    assert denied.value.status_code == 401
    relay._authorize("relay-token")






def test_frontend_frames_accept_fragmented_and_coalesced_messages():
    frames = relay.FrontendFrames()
    parse = _frontend(b"P", b"claim\0" + CLAIM + b"\0")
    commit = _frontend(b"Q", b"COMMIT\0")

    assert frames.feed(_startup()[:3]) == []
    assert len(frames.feed(_startup()[3:] + parse[:7])) == 1
    parsed = frames.feed(parse[7:] + commit)

    assert [(frame.message_type, frame.statement_name, frame.sql) for frame in parsed] == [
        (b"P", b"claim", CLAIM),
        (b"Q", b"", b"COMMIT"),
    ]


def test_backend_frames_accept_fragmented_and_coalesced_messages():
    frames = relay.BackendFrames()
    commit = _backend(b"C", b"COMMIT\0")
    ready = _backend(b"Z", b"I")

    assert frames.feed(commit[:4]) == []
    parsed = frames.feed(commit[4:] + ready)

    assert [(frame.message_type, frame.body) for frame in parsed] == [
        (b"C", b"COMMIT\0"),
        (b"Z", b"I"),
    ]


def test_backend_frames_forward_postgres_ssl_negotiation_byte_before_messages():
    frames = relay.BackendFrames()
    frames.expect_negotiation_response()

    parsed = frames.feed(b"N" + _backend(b"R", b"\0\0\0\0"))

    assert [(frame.message_type, frame.wire) for frame in parsed] == [
        (b"", b"N"),
        (b"R", _backend(b"R", b"\0\0\0\0")),
    ]


def test_connection_flow_holds_only_post_commit_locked_execution_read():
    flow = relay.ConnectionFlow()

    assert flow.should_hold(_parse(b"claim", CLAIM)) is False
    assert flow.stage == "await_reservation"
    assert flow.should_hold(_parse(b"reserve", RESERVE)) is False
    assert flow.stage == "await_commit"
    assert flow.should_hold(_bind(b"reserve", b"reserved")) is False
    assert flow.stage == "await_commit"
    assert flow.should_hold(
        relay.FrontendFrame(_frontend(b"Q", b"COMMIT\0"), b"Q", b"COMMIT\0", sql=b"COMMIT")
    ) is False
    assert flow.stage == "await_commit_success"

    # Interleaved backend traffic cannot open the gate until PostgreSQL confirms COMMIT.
    flow.observe_backend(relay.BackendFrame(_backend(b"D", b"payload"), b"D", b"payload"))
    assert flow.stage == "await_commit_success"
    flow.observe_backend(relay.BackendFrame(_backend(b"C", b"COMMIT\0"), b"C", b"COMMIT\0"))
    flow.observe_backend(relay.BackendFrame(_backend(b"Z", b"I"), b"Z", b"I"))
    assert flow.stage == "await_locked_read"

    assert flow.should_hold(_parse(b"locked", LOCKED_READ)) is True
    assert flow.stage == "held"


def test_cancellation_gate_holds_post_commit_read_after_pool_reconnect(monkeypatch, tmp_path):
    async def exercise() -> None:
        monkeypatch.setattr(relay, "_COORDINATION_ROOT", tmp_path)
        gate = relay.CancellationGate()
        first = relay.ConnectionFlow()
        second = relay.ConnectionFlow()

        await gate.arm("run-1")
        assert await gate.should_hold(first, _parse(b"claim", CLAIM)) is False
        assert await gate.should_hold(first, _parse(b"reserve", RESERVE)) is False
        assert await gate.should_hold(
            first,
            relay.FrontendFrame(
                _frontend(b"Q", b"COMMIT\0"), b"Q", b"COMMIT\0", sql=b"COMMIT"
            ),
        ) is False
        await gate.observe_backend(
            first, relay.BackendFrame(_backend(b"C", b"COMMIT\0"), b"C", b"COMMIT\0")
        )
        await gate.observe_backend(first, relay.BackendFrame(_backend(b"Z", b"I"), b"Z", b"I"))

        assert await gate.should_hold(second, _parse(b"locked", LOCKED_READ)) is True
        assert (tmp_path / "run-1.cancel-before-dispatch-held").read_text() == "held"
        await gate.release()

    asyncio.run(exercise())


def test_interleaved_connection_does_not_inherit_armed_flow_state():
    first = relay.ConnectionFlow()
    second = relay.ConnectionFlow()

    assert first.should_hold(_parse(b"claim", CLAIM)) is False
    assert second.should_hold(_parse(b"locked", LOCKED_READ)) is False
    assert second.stage == "await_claim"
