#!/usr/bin/env python3
"""Acceptance-only III bridge caller for 101st/102nd correlated records.

The actuator neither synthesizes callbacks nor writes a database.  Its response
is deliberately not a certificate input: only the public Admin boundary may be
normalized by the failure driver.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class IngressRequest(BaseModel):
    phase: Literal["pre_snapshot_101", "late_102", "amendment_duplicate"]
    workspace_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    studio_workflow_version_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    node_id: str = Field(min_length=1, max_length=255)
    command_id: str = Field(min_length=1, max_length=36)
    attempt_id: str = Field(min_length=1, max_length=36)
    attempt_number: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=36)
    trace_id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=36)
    source_binding_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_number: int | None = Field(default=None, ge=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    event_id: str = Field(min_length=1, max_length=256)


def _trigger_options(iii_url: str) -> list[str]:
    parsed = urlparse(iii_url)
    if (
        parsed.scheme != "ws"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("III URL must be a root ws endpoint")
    port = parsed.port or 49134
    if not 1 <= port <= 65535:
        raise ValueError("III URL has an invalid port")
    return ["--address", parsed.hostname, "--port", str(port)]


def _event(body: IngressRequest) -> dict[str, object]:
    payload = {
        "actor": "proof-iii-actuator",
        "phase": body.phase,
        "command_id": body.command_id,
        "attempt_id": body.attempt_id,
        "task_id": body.task_id,
        "trace_id": body.trace_id,
        "source_id": body.source_id,
        "event_id": body.event_id,
    }
    return {
        "schema_version": 1,
        "provider": "opencli/proof-iii-actuator",
        "source_id": body.source_id,
        "event_id": body.event_id,
        "ingest_mode": "snapshot",
        "source_ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "payload": payload,
        "raw_data": payload,
        "task_id": body.task_id,
        "trace_id": body.trace_id,
    }


def _expected_key_set_sha256(event: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "expected_keys": [
                    {"source_id": str(event["source_id"]), "event_id": str(event["event_id"])}
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _admin_collection(body: IngressRequest, event: dict[str, object]) -> dict[str, object]:
    return {
        "version": "v1",
        "workspace_id": body.workspace_id,
        "project_id": body.project_id,
        "workflow_id": body.workflow_id,
        "studio_workflow_version_id": body.studio_workflow_version_id,
        "run_id": body.run_id,
        "node_id": body.node_id,
        "command_id": body.command_id,
        "attempt_id": body.attempt_id,
        "attempt_number": body.attempt_number,
        "task_id": body.task_id,
        "trace_id": body.trace_id,
        "source_id": body.source_id,
        "source_binding_id": body.source_binding_id,
        "source_binding_revision_id": body.source_binding_revision_id,
        "source_binding_revision_number": body.source_binding_revision_number,
        "payload_sha256": body.payload_sha256,
        "expected_key_set_sha256": _expected_key_set_sha256(event),
    }


def _trigger_command(iii_url: str, body: IngressRequest) -> list[str]:
    event = _event(body)
    payload = {
        "events": [event],
        "task_id": body.task_id,
        "trace_id": body.trace_id,
        "admin_collection": _admin_collection(body, event),
    }
    return [
        os.environ.get("III_CLI_PATH", "/opt/iii/iii"),
        "trigger",
        *_trigger_options(iii_url),
        "odp.ingest::batch",
        "--json",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ]


@app.post("/actuate/ingress")
async def ingress(
    body: IngressRequest, x_api_token: str | None = Header(default=None)
) -> dict[str, str]:
    token = os.environ.get("API_AUTH_TOKEN")
    if not token or x_api_token != token:
        raise HTTPException(401, "actor credential denied")
    iii_url = os.environ.get("PROOF_III_URL")
    if not iii_url:
        raise HTTPException(503, "real III identity is unavailable")
    try:
        command = _trigger_command(iii_url, body)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "real III invocation failed") from exc
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise HTTPException(502, "real III invocation timed out") from exc
    if process.returncode != 0:
        raise HTTPException(
            502, "real III invocation failed: " + (stderr or b"").decode(errors="replace")[:256]
        )
    # Do not propagate actor response/control state into a proof artifact.
    return {"status": "submitted"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
