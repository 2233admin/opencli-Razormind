#!/usr/bin/env python3
"""Acceptance-only PostgreSQL relay that holds exactly one ODP attempt-page query.

It forwards PostgreSQL bytes unchanged.  The gate recognizes only the prepared
attempt-page statement used by ODP query, letting transaction setup, exact, and
DLQ statements reach the real database without delay.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

_TARGET_HOST = os.environ.get("TARGET_HOST", "proof-odp-postgres")
_TARGET_PORT = int(os.environ.get("TARGET_PORT", "5432"))
_GATE_PORT = int(os.environ.get("GATE_PORT", "5432"))


def _attempt_page_sql(sql: bytes) -> bool:
    """Match ODP's scoped attempt-page SELECT, not broad record reads."""
    normalized = b" ".join(sql.lower().split())
    return all(
        needle in normalized
        for needle in (
            b"select id, source_id, event_id, committed_at, provider, source_ts",
            b"from odp_records",
            b"where task_id = $1",
            b"and trace_id = $2",
            b"and source_id = any($3::uuid[])",
            b"and committed_at <= $4",
        )
    )


def _sql_from_frame(message_type: bytes, body: bytes) -> bytes:
    if message_type == b"Q":
        return body.split(b"\0", 1)[0]
    if message_type == b"P":
        parts = body.split(b"\0", 2)
        if len(parts) >= 2:
            return parts[1]
    return b""


@dataclass(frozen=True)
class FrontendFrame:
    wire: bytes
    sql: bytes


class FrontendFrames:
    """Incrementally split startup and regular PostgreSQL frontend frames."""

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
                # SSL/GSS negotiation is followed by the actual startup packet.
                code = int.from_bytes(wire[4:8], "big")
                if code not in {80877103, 80877104}:
                    self._startup_pending = False
                frames.append(FrontendFrame(wire, b""))
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
            frames.append(FrontendFrame(wire, _sql_from_frame(message_type, body)))


class PageGate:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._armed = False
        self._held = False
        self._release = asyncio.Event()
        self._release.set()

    async def arm(self, armed: bool) -> None:
        async with self._lock:
            self._armed = armed
            self._held = False
            if armed:
                self._release.clear()
            else:
                self._release.set()

    async def claim(self, sql: bytes) -> bool:
        if not _attempt_page_sql(sql):
            return False
        async with self._lock:
            if not self._armed or self._held:
                return False
            self._held = True
            return True

    async def wait_for_release(self) -> None:
        try:
            await asyncio.wait_for(self._release.wait(), timeout=30)
        except TimeoutError as exc:
            raise ConnectionError("attempt-page gate timed out") from exc

    async def release(self) -> None:
        async with self._lock:
            self._release.set()

    async def held(self) -> bool:
        async with self._lock:
            return self._held


_gate = PageGate()


async def _relay_backend(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    backend_task: asyncio.Task[None] | None = None
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(_TARGET_HOST, _TARGET_PORT)
        backend_task = asyncio.create_task(_relay_backend(upstream_reader, client_writer))
        frames = FrontendFrames()
        while data := await client_reader.read(65536):
            for frame in frames.feed(data):
                if await _gate.claim(frame.sql):
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
    armed: bool


def _authorize(token: str | None) -> None:
    if token != os.environ.get("API_AUTH_TOKEN"):
        raise HTTPException(401, "gate credential denied")


@app.post("/_gate/query-page/arm")
async def arm(body: ArmRequest, x_api_token: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(x_api_token)
    await _gate.arm(body.armed)
    return {"armed": body.armed}


@app.post("/_gate/query-page/release")
async def release(x_api_token: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(x_api_token)
    await _gate.release()
    return {"released": True}


@app.get("/_gate/query-page/held")
async def held(x_api_token: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(x_api_token)
    return {"held": await _gate.held()}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
