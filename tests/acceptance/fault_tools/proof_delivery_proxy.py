"""Authenticated failure proxy for the controlled-receiver delivery boundary.

The proxy is the only receiver endpoint visible to Admin in failure proofs.  It
always forwards the request to the isolated TLS receiver before withholding or
replacing its response, so public execution/reconciliation APIs—not proxy
state—remain the only proof facts.
"""
from __future__ import annotations

import asyncio
import json
import os
from enum import StrEnum

import httpx
from backend.security.controlled_receiver import (
    ControlledReceiverSecurityError,
    resolve_receiver_identity,
    verify_receipt,
)
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel


class DeliveryProxyMode(StrEnum):
    PASS_THROUGH = "pass_through"
    CORRUPT_MAC = "corrupt_mac"
    WITHHOLD_RESPONSE = "withhold_response"
    REPLACE_WITH_503 = "replace_with_503"
    HOLD_VALID_RESPONSE = "hold_valid_response"
    RELEASE_VALID_RESPONSE = "release_valid_response"
    DROP_VALID_RESPONSE = "drop_valid_response"


class ModeRequest(BaseModel):
    mode: DeliveryProxyMode


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_mode = DeliveryProxyMode.PASS_THROUGH
_response_held = False
_response_resolution: DeliveryProxyMode | None = None


def _configured_token() -> str:
    token = os.environ.get("API_AUTH_TOKEN")
    if not token:
        raise RuntimeError("API_AUTH_TOKEN is required")
    return token


def _upstream_url(path: str) -> str:
    base = os.environ.get("PROOF_RECEIVER_BACKEND_URL")
    if not base:
        raise RuntimeError("PROOF_RECEIVER_BACKEND_URL is required")
    return base.rstrip("/") + path


def _forward_headers(request: Request) -> dict[str, str]:
    corrupt_mac = (
        _mode is DeliveryProxyMode.CORRUPT_MAC
        and request.url.path.endswith("/deliver")
    )
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in {"host", "content-length"}
        and (not corrupt_mac or name.lower() != "x-controlled-receiver-mac")
    }
    if corrupt_mac:
        headers["X-Controlled-Receiver-Mac"] = "sha256=corrupted-by-proof-proxy"
    return headers


async def _forward(request: Request, path: str) -> httpx.Response:
    body = await request.body()
    verify = os.environ.get("SSL_CERT_FILE", "/run/proof/ca.pem")
    async with httpx.AsyncClient(verify=verify, timeout=10) as client:
        return await client.request(
            request.method,
            _upstream_url(path),
            content=body,
            headers=_forward_headers(request),
        )



def _reset_delivery_response_gate() -> None:
    global _mode, _response_held, _response_resolution
    _mode = DeliveryProxyMode.PASS_THROUGH
    _response_held = False
    _response_resolution = None


async def _has_valid_signed_delivery_response(
    request: Request, response: httpx.Response
) -> bool:
    if not 200 <= response.status_code < 300:
        return False
    try:
        value = json.loads(await request.body())
        if not isinstance(value, dict):
            return False
        endpoint = resolve_receiver_identity(value["receiverIdentity"])
        verify_receipt(
            receipt=response.json().get("receipt"),
            endpoint=endpoint,
            operation_id=value["operationId"],
            decision_hash=value["decisionHash"],
            payload_hash=value["payloadHash"],
        )
    except (
        ControlledReceiverSecurityError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False
    return True


async def _gate_valid_delivery_response(
    request: Request, response: httpx.Response
) -> Response | None:
    global _response_held, _response_resolution
    if _mode is not DeliveryProxyMode.HOLD_VALID_RESPONSE:
        return None
    if not await _has_valid_signed_delivery_response(request, response):
        return None
    _response_held = True
    while _response_resolution is None:
        await asyncio.sleep(0.01)
    resolution = _response_resolution
    _response_held = False
    _response_resolution = None
    if resolution is DeliveryProxyMode.DROP_VALID_RESPONSE:
        return Response(status_code=503)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={
            name: value
            for name, value in response.headers.items()
            if name.lower() in {"content-type", "content-length"}
        },
    )

@app.post("/_gate/delivery")
async def set_mode(
    body: ModeRequest, x_api_token: str | None = Header(default=None)
) -> dict[str, str]:
    if x_api_token != _configured_token():
        raise HTTPException(401, "delivery proxy credential denied")
    global _mode, _response_held, _response_resolution
    if body.mode is DeliveryProxyMode.HOLD_VALID_RESPONSE:
        _mode = body.mode
        _response_held = False
        _response_resolution = None
    elif body.mode in {
        DeliveryProxyMode.RELEASE_VALID_RESPONSE,
        DeliveryProxyMode.DROP_VALID_RESPONSE,
    }:
        if not _response_held:
            raise HTTPException(409, "delivery response is not held")
        _response_resolution = body.mode
    else:
        _mode = body.mode
    return {"status": "configured"}


@app.get("/_gate/delivery/status")
async def delivery_gate_status(
    x_api_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if x_api_token != _configured_token():
        raise HTTPException(401, "delivery proxy credential denied")
    return {"responseHeld": _response_held and _response_resolution is None}


@app.api_route(
    "/api/v1/controlled-receiver/v2/{action}", methods=["POST"], include_in_schema=False
)
async def proxy_delivery(action: str, request: Request) -> Response:
    if action not in {"deliver", "status"}:
        raise HTTPException(404, "receiver action is unavailable")
    response = await _forward(request, f"/api/v1/controlled-receiver/v2/{action}")
    if action == "deliver":
        held_response = await _gate_valid_delivery_response(request, response)
        if held_response is not None:
            return held_response
        if _mode is DeliveryProxyMode.WITHHOLD_RESPONSE:
            await asyncio.sleep(31)
        elif _mode is DeliveryProxyMode.REPLACE_WITH_503:
            return Response(status_code=503)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={
            name: value
            for name, value in response.headers.items()
            if name.lower() in {"content-type", "content-length"}
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
