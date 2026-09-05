from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _gate_module():
    path = ROOT / "tests/acceptance/fault_tools/odp_query_pg_gate.py"
    spec = importlib.util.spec_from_file_location("odp_query_pg_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _startup() -> bytes:
    return (8).to_bytes(4, "big") + (196608).to_bytes(4, "big")


def _parse(sql: bytes) -> bytes:
    body = b"sqlx_s_1\0" + sql + b"\0\0\0"
    return b"P" + (4 + len(body)).to_bytes(4, "big") + body


def test_protocol_gate_holds_only_scoped_attempt_page_parse():
    gate = _gate_module()
    attempt_page = b"""
        SELECT id, source_id, event_id, committed_at, provider, source_ts
        FROM odp_records
        WHERE task_id = $1
          AND trace_id = $2
          AND source_id = ANY($3::uuid[])
          AND committed_at <= $4
        ORDER BY committed_at ASC, id ASC
        LIMIT $7
    """
    exact = b"SELECT requested.source_id FROM UNNEST($1::uuid[], $2::text[]) AS requested"
    dlq = b"SELECT record.source_id FROM odp_records AS record WHERE record.source_id = ANY($1::uuid[])"

    frames = gate.FrontendFrames()
    assert frames.feed(_startup()) == [gate.FrontendFrame(_startup(), b"")]
    partial = _parse(attempt_page)
    assert frames.feed(partial[:9]) == []
    parsed = frames.feed(partial[9:])

    assert len(parsed) == 1
    assert parsed[0].sql == attempt_page
    assert gate._attempt_page_sql(parsed[0].sql)
    assert not gate._attempt_page_sql(b"BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    assert not gate._attempt_page_sql(b"SELECT NOW()")
    assert not gate._attempt_page_sql(exact)
    assert not gate._attempt_page_sql(dlq)
