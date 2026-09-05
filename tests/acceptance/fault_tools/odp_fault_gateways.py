#!/usr/bin/env python3
"""Failure-only transparent gateways for the four ODP durability subcases.

They relay real protocol bytes.  Their process-local controls are deliberately
not proof inputs: the matrix derives facts exclusively from authenticated Admin
public APIs.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

GatewayMode = Literal[
    "http-schema-mutator",
    "ingest-redis-cut",
    "ingest-redis-payload-mutator",
    "store-pg-cut",
    "store-redis-committed-xadd",
]

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@dataclass
class GatewayState:
    mode: GatewayMode
    armed: bool = False


STATES = {
    "http-schema-mutator": GatewayState("http-schema-mutator"),
    "ingest-redis-cut": GatewayState("ingest-redis-cut"),
    "ingest-redis-payload-mutator": GatewayState("ingest-redis-payload-mutator"),
    "store-pg-cut": GatewayState("store-pg-cut"),
    "store-redis-committed-xadd": GatewayState("store-redis-committed-xadd"),
}


class ArmRequest(BaseModel):
    armed: bool


def _state(name: str) -> GatewayState:
    try:
        return STATES[name]
    except KeyError as exc:
        raise HTTPException(404, "unknown ODP fault gateway") from exc


def _authenticated(token: str | None) -> None:
    if token != os.environ.get("API_AUTH_TOKEN"):
        raise HTTPException(401, "gateway credential denied")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _commit_marker() -> Path:
    return Path(os.environ.get("COMMIT_MARKER_PATH", "/coordination/store-commit-ready"))


def _mark_commit_ready() -> None:
    path = _commit_marker()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text("ready", encoding="ascii")


class PostgresCommitObserver:
    """Per-relay PostgreSQL frame observer; never changes forwarded bytes."""

    def __init__(self) -> None:
        self._frontend = bytearray()
        self._backend = bytearray()
        self._startup_pending = True
        self.pending_commit = False

    @staticmethod
    def _is_commit_sql(sql: bytes) -> bool:
        return sql.rstrip(b"\0").strip().rstrip(b";").strip().upper() == b"COMMIT"

    def feed_frontend(self, data: bytes) -> None:
        self._frontend.extend(data)
        while True:
            if self._startup_pending:
                if len(self._frontend) < 4:
                    return
                length = int.from_bytes(self._frontend[:4], "big")
                if length < 8 or len(self._frontend) < length:
                    return
                del self._frontend[:length]
                self._startup_pending = False
                continue
            if len(self._frontend) < 5:
                return
            length = int.from_bytes(self._frontend[1:5], "big")
            total = 1 + length
            if length < 4 or len(self._frontend) < total:
                return
            message_type, body = self._frontend[0:1], bytes(self._frontend[5:total])
            del self._frontend[:total]
            sql = (
                body
                if message_type == b"Q"
                else body.split(b"\0", 1)[1]
                if message_type == b"P" and b"\0" in body
                else b""
            )
            if self._is_commit_sql(sql):
                self.pending_commit = True

    def feed_backend(self, data: bytes) -> None:
        self._backend.extend(data)
        while len(self._backend) >= 5:
            length = int.from_bytes(self._backend[1:5], "big")
            total = 1 + length
            if length < 4 or len(self._backend) < total:
                return
            message_type, body = self._backend[0:1], bytes(self._backend[5:total])
            del self._backend[:total]
            if message_type == b"E":
                self.pending_commit = False
            elif message_type == b"Z":
                if self.pending_commit and body == b"I":
                    _mark_commit_ready()
                self.pending_commit = False


def _redis_filter_enabled(state: GatewayState) -> bool:
    return state.armed and _commit_marker().exists()


@app.post("/_gate/{name}/arm")
async def arm(
    name: str,
    body: ArmRequest,
    x_api_token: str | None = Header(default=None),
) -> dict[str, bool]:
    _authenticated(x_api_token)
    _state(name).armed = body.armed
    return {"armed": body.armed}


@app.api_route("/{path:path}", methods=["POST", "PUT"])
async def http_schema_mutator(path: str, request: Request) -> Response:
    """Forward the actual ingress request, changing only JSON schema_version."""
    body = await request.body()
    if _state("http-schema-mutator").armed:
        document = json.loads(body)
        event = (
            document["events"][0]
            if isinstance(document, dict)
            and isinstance(document.get("events"), list)
            and document["events"]
            else document
        )
        if not isinstance(event, dict) or "schema_version" not in event:
            raise HTTPException(422, "ingress document has no event schema_version")
        event["schema_version"] = 999
        body = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in {"authorization", "content-type", "x-api-token", "x-iii-bridge-token"}
    }
    async with httpx.AsyncClient() as client:
        upstream = await client.request(
            request.method,
            os.environ["HTTP_UPSTREAM_URL"].rstrip("/") + "/" + path,
            content=body,
            headers=headers,
            timeout=10,
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


def _is_committed_xadd(command: bytes) -> bool:
    """Recognize only the exact RESP XADD command and committed stream."""
    parts = _resp_parts(command)
    return (
        parts is not None
        and len(parts) >= 2
        and parts[0].upper() == b"XADD"
        and parts[1] == b"odp.record.committed"
    )


def _resp_parts(command: bytes) -> list[bytes] | None:
    """Decode one complete RESP array command without interpreting its payload."""
    if not command.startswith(b"*"):
        return None
    count_end = command.find(b"\r\n")
    if count_end < 0:
        return None
    try:
        count = int(command[1:count_end])
    except ValueError:
        return None
    cursor = count_end + 2
    parts: list[bytes] = []
    for _ in range(count):
        if command[cursor : cursor + 1] != b"$":
            return None
        size_end = command.find(b"\r\n", cursor)
        if size_end < 0:
            return None
        try:
            size = int(command[cursor + 1 : size_end])
        except ValueError:
            return None
        data_start = size_end + 2
        data_end = data_start + size
        if command[data_end : data_end + 2] != b"\r\n":
            return None
        parts.append(command[data_start:data_end])
        cursor = data_end + 2
    return parts if cursor == len(command) else None


def _encode_resp(parts: list[bytes]) -> bytes:
    return (
        b"*"
        + str(len(parts)).encode()
        + b"\r\n"
        + b"".join(b"$" + str(len(part)).encode() + b"\r\n" + part + b"\r\n" for part in parts)
    )


def _poison_ingest_xadd(command: bytes) -> bytes:
    """Only poison the `event` payload on an actual ingest-stream XADD."""
    parts = _resp_parts(command)
    if (
        parts is None
        or len(parts) < 5
        or parts[0].upper() != b"XADD"
        or parts[1] != b"odp.ingest.raw"
    ):
        return command
    for index in range(3, len(parts) - 1, 2):
        if parts[index] != b"event":
            continue
        try:
            event = json.loads(parts[index + 1])
        except (TypeError, ValueError):
            return command
        if not isinstance(event, dict):
            return command
        # PostgreSQL cannot persist this JSONB raw_data value, while the
        # record remains otherwise valid and is therefore retained in DLQ.
        event["raw_data"] = "\x00"
        parts[index + 1] = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
        return _encode_resp(parts)
    return command


class RespCommandBuffer:
    """Bounded RESP request framing sufficient for transparent command gating."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        commands: list[bytes] = []
        while self._buffer:
            end = self._buffer.find(b"\r\n")
            if end < 0:
                break
            # RESP array requests begin '*<count>'; use the final bulk payload
            # boundary scanner, otherwise forward an opaque line safely.
            if self._buffer[0:1] != b"*":
                commands.append(bytes(self._buffer[: end + 2]))
                del self._buffer[: end + 2]
                continue
            cursor = end + 2
            try:
                count = int(self._buffer[1:end])
            except ValueError:
                commands.append(bytes(self._buffer[:cursor]))
                del self._buffer[:cursor]
                continue
            complete = True
            for _ in range(count):
                if len(self._buffer) <= cursor or self._buffer[cursor : cursor + 1] != b"$":
                    complete = False
                    break
                size_end = self._buffer.find(b"\r\n", cursor)
                if size_end < 0:
                    complete = False
                    break
                try:
                    size = int(self._buffer[cursor + 1 : size_end])
                except ValueError:
                    complete = False
                    break
                cursor = size_end + 2 + size + 2
                if len(self._buffer) < cursor:
                    complete = False
                    break
            if not complete:
                break
            commands.append(bytes(self._buffer[:cursor]))
            del self._buffer[:cursor]
        return commands


async def _copy(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: GatewayState,
    *,
    outbound: bool,
    observer: PostgresCommitObserver | None = None,
) -> None:
    resp = (
        RespCommandBuffer()
        if state.mode in {"ingest-redis-payload-mutator", "store-redis-committed-xadd"} and outbound
        else None
    )
    while data := await reader.read(65536):
        if observer:
            (observer.feed_frontend if outbound else observer.feed_backend)(data)
        for chunk in resp.feed(data) if resp else [data]:
            if state.armed and state.mode in {"ingest-redis-cut", "store-pg-cut"} and outbound:
                writer.close()
                await writer.wait_closed()
                return
            if (
                state.mode == "store-redis-committed-xadd"
                and outbound
                and _is_committed_xadd(chunk)
                and _redis_filter_enabled(state)
            ):
                chunk = chunk.replace(b"odp.record.committed", b"odp.record.discarded")
            if state.mode == "ingest-redis-payload-mutator" and state.armed and outbound:
                chunk = _poison_ingest_xadd(chunk)
            writer.write(chunk)
            await writer.drain()
    writer.close()
    await writer.wait_closed()


async def relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    state: GatewayState,
) -> None:
    upstream_reader, upstream_writer = await asyncio.open_connection(
        os.environ["UPSTREAM_HOST"],
        int(os.environ["UPSTREAM_PORT"]),
    )
    observer = PostgresCommitObserver() if state.mode == "store-pg-cut" else None
    await asyncio.gather(
        _copy(client_reader, upstream_writer, state, outbound=True, observer=observer),
        _copy(upstream_reader, client_writer, state, outbound=False, observer=observer),
    )


async def serve_tcp(mode: GatewayMode) -> None:
    state = _state(mode)
    tcp_server = await asyncio.start_server(
        lambda reader, writer: relay(reader, writer, state),
        "0.0.0.0",
        int(os.environ["GATEWAY_PORT"]),
    )
    control = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=int(os.environ.get("CONTROL_PORT", "8080")),
            log_level="warning",
        )
    )
    async with tcp_server:
        await asyncio.gather(tcp_server.serve_forever(), control.serve())


if __name__ == "__main__":
    asyncio.run(serve_tcp(os.environ["GATEWAY_MODE"]))
