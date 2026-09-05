"""Acceptance-only PostgreSQL protocol relay for cancellation-before-dispatch.

This sits only between the primary Admin and its PostgreSQL service.  Once
armed, a single connection must progress through the real execution claim,
durable reservation update, and successful COMMIT before the relay withholds
the next locked execution read.  No application seam participates in that
boundary.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

_TARGET_HOST = os.environ.get("TARGET_HOST", "proof-admin-postgres")
_TARGET_PORT = int(os.environ.get("TARGET_PORT", "5432"))
_GATE_PORT = int(os.environ.get("GATE_PORT", "5432"))
_COORDINATION_ROOT = Path(os.environ.get("PROOF_ARTIFACT_DIR", "/proof-artifacts")) / "coordination"




def _normalized_sql(sql: bytes) -> bytes:
    return b" ".join(sql.lower().split())


def _execution_claim(sql: bytes) -> bool:
    normalized = _normalized_sql(sql)
    return (
        b"from delivery_executions" in normalized
        and b"where delivery_executions.decision_id" in normalized
        and b"for update" in normalized
    )


def _reservation_update(sql: bytes) -> bool:
    """Recognize the durable reservation UPDATE after its execution claim.

    The source assigns ``send_started_at = None`` at reservation, but an
    already-NULL database column need not appear in the emitted UPDATE.
    """
    normalized = _normalized_sql(sql)
    return (
        normalized.startswith(b"update delivery_executions set")
        and b"state" in normalized
        and b"lease_token" in normalized
        and b"lease_acquired_at" in normalized
        and b"reserved_attempt_number" in normalized
    )

def _locked_execution_read(sql: bytes) -> bool:
    normalized = _normalized_sql(sql)
    return (
        b"from delivery_executions" in normalized
        and b"where delivery_executions.id" in normalized
        and b"for update" in normalized
    )


def _commit(sql: bytes) -> bool:
    return _normalized_sql(sql).rstrip(b";") == b"commit"


def _cstring(value: bytes, offset: int = 0) -> tuple[bytes, int]:
    end = value.index(b"\0", offset)
    return value[offset:end], end + 1


def _parse_name_and_sql(body: bytes) -> tuple[bytes, bytes]:
    statement, offset = _cstring(body)
    sql, _ = _cstring(body, offset)
    return statement, sql


def _bind_statement(body: bytes) -> bytes:
    _, offset = _cstring(body)
    statement, _ = _cstring(body, offset)
    return statement


@dataclass(frozen=True)
class FrontendFrame:
    wire: bytes
    message_type: bytes
    body: bytes
    statement_name: bytes = b""
    sql: bytes = b""


class FrontendFrames:
    """Incrementally split startup and regular PostgreSQL frontend messages."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._startup_pending = True

    def feed(self, data: bytes) -> list[FrontendFrame]:
        self._buffer.extend(data)
        frames: list[FrontendFrame] = []
        while True:
            if self._startup_pending:
                if len(self._buffer) < 4:
                    return frames
                length = int.from_bytes(self._buffer[:4], "big")
                if length < 8:
                    raise ValueError("invalid PostgreSQL startup frame")
                if len(self._buffer) < length:
                    return frames
                wire = bytes(self._buffer[:length])
                del self._buffer[:length]
                code = int.from_bytes(wire[4:8], "big")
                if code not in {80877103, 80877104}:
                    self._startup_pending = False
                frames.append(FrontendFrame(wire, b"", b""))
                continue
            if len(self._buffer) < 5:
                return frames
            length = int.from_bytes(self._buffer[1:5], "big")
            total = 1 + length
            if length < 4:
                raise ValueError("invalid PostgreSQL frontend frame")
            if len(self._buffer) < total:
                return frames
            message_type = bytes(self._buffer[:1])
            body = bytes(self._buffer[5:total])
            wire = bytes(self._buffer[:total])
            del self._buffer[:total]
            statement_name = b""
            sql = b""
            if message_type == b"P":
                statement_name, sql = _parse_name_and_sql(body)
            elif message_type == b"B":
                statement_name = _bind_statement(body)
            elif message_type == b"Q":
                sql, _ = _cstring(body)
            frames.append(FrontendFrame(wire, message_type, body, statement_name, sql))


@dataclass(frozen=True)
class BackendFrame:
    wire: bytes
    message_type: bytes
    body: bytes


class BackendFrames:
    """Incrementally split regular PostgreSQL backend messages."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._negotiation_response_pending = False

    def expect_negotiation_response(self) -> None:
        self._negotiation_response_pending = True

    def feed(self, data: bytes) -> list[BackendFrame]:
        self._buffer.extend(data)
        frames: list[BackendFrame] = []
        if self._negotiation_response_pending:
            if not self._buffer:
                return frames
            response = bytes(self._buffer[:1])
            del self._buffer[:1]
            self._negotiation_response_pending = False
            frames.append(BackendFrame(response, b"", b""))
        while len(self._buffer) >= 5:
            length = int.from_bytes(self._buffer[1:5], "big")
            total = 1 + length
            if length < 4:
                raise ValueError("invalid PostgreSQL backend frame")
            if len(self._buffer) < total:
                return frames
            message_type = bytes(self._buffer[:1])
            body = bytes(self._buffer[5:total])
            wire = bytes(self._buffer[:total])
            del self._buffer[:total]
            frames.append(BackendFrame(wire, message_type, body))
        return frames


@dataclass
class ConnectionFlow:
    """Track one connection's exact durable-reservation transition."""

    statements: dict[bytes, bytes] = field(default_factory=dict)
    stage: str = "await_claim"
    _commit_completed: bool = False

    def _sql(self, frame: FrontendFrame) -> bytes:
        if frame.message_type == b"B":
            return self.statements.get(frame.statement_name, b"")
        return frame.sql

    def should_hold(self, frame: FrontendFrame) -> bool:
        if frame.message_type == b"P":
            self.statements[frame.statement_name] = frame.sql
        sql = self._sql(frame)
        if self.stage == "await_claim" and _execution_claim(sql):
            self.stage = "await_reservation"
            return False
        if self.stage == "await_reservation" and _reservation_update(sql):
            self.stage = "await_commit"
            return False
        if self.stage == "await_commit" and _commit(sql):
            self.stage = "await_commit_success"
            self._commit_completed = False
            return False
        if self.stage == "await_locked_read" and _locked_execution_read(sql):
            self.stage = "held"
            return True
        return False

    def observe_backend(self, frame: BackendFrame) -> None:
        if self.stage != "await_commit_success":
            return
        if frame.message_type == b"E":
            self.stage = "await_claim"
            return
        if frame.message_type == b"C" and frame.body.startswith(b"COMMIT\0"):
            self._commit_completed = True
            return
        if frame.message_type == b"Z" and self._commit_completed:
            self.stage = "await_locked_read"




class CancellationGate:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._armed = False
        self._held = False
        self._run: str | None = None
        self._release = asyncio.Event()
        self._release.set()
        self._stage = "await_claim"
        self._reservation_flow: ConnectionFlow | None = None
        self._claim_flow: ConnectionFlow | None = None
        self._commit_completed = False

    async def arm(self, run: str) -> None:
        async with self._lock:
            self._armed = True
            self._held = False
            self._run = run
            self._release.clear()
            self._stage = "await_claim"
            self._reservation_flow = None
            self._claim_flow = None
            self._commit_completed = False
            signal = _COORDINATION_ROOT / f"{run}.cancel-before-dispatch-held"
            signal.unlink(missing_ok=True)

    async def should_hold(self, flow: ConnectionFlow, frame: FrontendFrame) -> bool:
        flow.should_hold(frame)
        sql = flow._sql(frame)
        async with self._lock:
            if not self._armed or self._held:
                return False
            if self._stage == "await_claim" and _execution_claim(sql):
                self._claim_flow = flow
                self._stage = "await_reservation"
            elif (
                self._stage == "await_reservation"
                and self._claim_flow is flow
                and _reservation_update(sql)
            ):
                self._reservation_flow = flow
                self._stage = "await_commit"
            elif (
                self._stage == "await_commit"
                and self._reservation_flow is flow
                and _commit(sql)
            ):
                self._stage = "await_commit_success"
                self._commit_completed = False
            elif self._stage == "await_locked_read" and _locked_execution_read(sql):
                self._held = True
                _COORDINATION_ROOT.mkdir(parents=True, exist_ok=True)
                (
                    _COORDINATION_ROOT / f"{self._run}.cancel-before-dispatch-held"
                ).write_text("held", encoding="utf-8")
                return True
            return False

    async def observe_backend(self, flow: ConnectionFlow, frame: BackendFrame) -> None:
        flow.observe_backend(frame)
        async with self._lock:
            if self._stage != "await_commit_success" or self._reservation_flow is not flow:
                return
            if frame.message_type == b"E":
                self._stage = "await_claim"
                self._claim_flow = None
                self._reservation_flow = None
                self._commit_completed = False
            elif frame.message_type == b"C" and frame.body.startswith(b"COMMIT\0"):
                self._commit_completed = True
            elif frame.message_type == b"Z" and self._commit_completed:
                self._stage = "await_locked_read"

    async def wait_for_release(self) -> None:
        try:
            await asyncio.wait_for(self._release.wait(), timeout=90)
        except TimeoutError as exc:
            raise ConnectionError("cancel-before-dispatch gate timed out") from exc

    async def release(self) -> None:
        async with self._lock:
            self._release.set()


_gate = CancellationGate()


async def _relay_backend(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    flow: ConnectionFlow,
    frames: BackendFrames,
) -> None:
    try:
        while data := await reader.read(65536):
            for frame in frames.feed(data):
                await _gate.observe_backend(flow, frame)
                writer.write(frame.wire)
                await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _negotiates_tls(frame: FrontendFrame) -> bool:
    return (
        frame.message_type == b""
        and len(frame.wire) == 8
        and int.from_bytes(frame.wire[4:8], "big") in {80877103, 80877104}
    )


async def _handle_client(
    client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    backend_task: asyncio.Task[None] | None = None
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(_TARGET_HOST, _TARGET_PORT)
        flow = ConnectionFlow()
        backend_frames = BackendFrames()
        backend_task = asyncio.create_task(
            _relay_backend(upstream_reader, client_writer, flow, backend_frames)
        )
        frames = FrontendFrames()
        while data := await client_reader.read(65536):
            for frame in frames.feed(data):
                if _negotiates_tls(frame):
                    backend_frames.expect_negotiation_response()
                if await _gate.should_hold(flow, frame):
                    await _gate.wait_for_release()
                upstream_writer.write(frame.wire)
                await upstream_writer.drain()
    except (ConnectionError, ValueError):
        pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            await upstream_writer.wait_closed()
        if backend_task is not None:
            backend_task.cancel()
            try:
                await backend_task
            except asyncio.CancelledError:
                pass
        client_writer.close()
        await client_writer.wait_closed()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    server = await asyncio.start_server(_handle_client, "0.0.0.0", _GATE_PORT)
    try:
        yield
    finally:
        server.close()
        await server.wait_closed()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


class ArmRequest(BaseModel):
    run: str = Field(min_length=1, max_length=128)


def _authorize(token: str | None) -> None:
    if token != os.environ.get("API_AUTH_TOKEN"):
        raise HTTPException(401, "gate credential denied")


@app.post("/_gate/cancel-before-dispatch/arm")
async def arm(body: ArmRequest, x_api_token: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(x_api_token)
    await _gate.arm(body.run)
    return {"armed": True}


@app.post("/_gate/cancel-before-dispatch/release")
async def release(x_api_token: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(x_api_token)
    await _gate.release()
    return {"released": True}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
