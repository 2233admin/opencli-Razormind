from __future__ import annotations

import importlib

import httpx
from fastapi.testclient import TestClient
from starlette.requests import Request


def _request(path: str, headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "query_string": b"",
            "headers": headers,
            "server": ("proof-delivery-proxy", 8000),
        }
    )


def test_delivery_proxy_requires_authenticated_mode_changes(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "proof-token")
    proxy = importlib.import_module("tests.acceptance.fault_tools.proof_delivery_proxy")
    proxy._mode = proxy.DeliveryProxyMode.PASS_THROUGH
    client = TestClient(proxy.app)

    assert client.post("/_gate/delivery", json={"mode": "corrupt_mac"}).status_code == 401
    response = client.post(
        "/_gate/delivery",
        json={"mode": "corrupt_mac"},
        headers={"X-API-Token": "proof-token"},
    )

    assert response.status_code == 200
    assert proxy._mode is proxy.DeliveryProxyMode.CORRUPT_MAC


def test_delivery_proxy_corrupts_only_delivery_mac_and_preserves_other_headers(monkeypatch):
    proxy = importlib.import_module("tests.acceptance.fault_tools.proof_delivery_proxy")
    proxy._mode = proxy.DeliveryProxyMode.CORRUPT_MAC
    delivered: dict[str, object] = {}

    async def fake_forward(request: Request, path: str) -> httpx.Response:
        delivered["path"] = path
        delivered["headers"] = proxy._forward_headers(request)
        return httpx.Response(200, json={"receipt": {"version": "v2"}})

    monkeypatch.setattr(proxy, "_forward", fake_forward)
    client = TestClient(proxy.app)
    response = client.post(
        "/api/v1/controlled-receiver/v2/deliver",
        content=b"{}",
        headers={"X-Controlled-Receiver-Mac": "original", "X-Retained": "value"},
    )

    assert response.status_code == 200
    assert delivered["path"] == "/api/v1/controlled-receiver/v2/deliver"
    assert delivered["headers"]["x-retained"] == "value"
    assert delivered["headers"]["X-Controlled-Receiver-Mac"] == "sha256=corrupted-by-proof-proxy"

    status_headers = proxy._forward_headers(
        _request(
            "/api/v1/controlled-receiver/v2/status",
            [(b"x-controlled-receiver-mac", b"original")],
        )
    )
    assert status_headers["x-controlled-receiver-mac"] == "original"


def test_delivery_proxy_gate_status_and_resolution_require_authentication(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "proof-token")
    proxy = importlib.import_module("tests.acceptance.fault_tools.proof_delivery_proxy")
    proxy._reset_delivery_response_gate()
    client = TestClient(proxy.app)

    assert client.get("/_gate/delivery/status").status_code == 401
    assert client.post(
        "/_gate/delivery", json={"mode": "hold_valid_response"}
    ).status_code == 401
    assert client.post(
        "/_gate/delivery",
        json={"mode": "hold_valid_response"},
        headers={"X-API-Token": "proof-token"},
    ).json() == {"status": "configured"}
    assert client.get(
        "/_gate/delivery/status", headers={"X-API-Token": "proof-token"}
    ).json() == {"responseHeld": False}
    assert client.post(
        "/_gate/delivery",
        json={"mode": "drop_valid_response"},
        headers={"X-API-Token": "proof-token"},
    ).status_code == 409
def test_delivery_proxy_drops_only_a_gate_held_valid_response(monkeypatch):
    import asyncio

    monkeypatch.setenv("API_AUTH_TOKEN", "proof-token")
    proxy = importlib.import_module("tests.acceptance.fault_tools.proof_delivery_proxy")
    proxy._reset_delivery_response_gate()

    async def valid_response(*_: object) -> bool:
        return True

    async def drop_held_response() -> httpx.Response:
        await proxy.set_mode(
            proxy.ModeRequest(mode="hold_valid_response"), "proof-token"
        )
        held = asyncio.create_task(
            proxy._gate_valid_delivery_response(
                _request("/api/v1/controlled-receiver/v2/deliver", []),
                httpx.Response(200, json={"receipt": {"version": "v2"}}),
            )
        )
        deadline = asyncio.get_running_loop().time() + 1
        while not proxy._response_held:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        assert await proxy.delivery_gate_status("proof-token") == {"responseHeld": True}
        assert await proxy.set_mode(
            proxy.ModeRequest(mode="drop_valid_response"), "proof-token"
        ) == {"status": "configured"}
        response = await held
        assert response is not None
        return response

    monkeypatch.setattr(proxy, "_has_valid_signed_delivery_response", valid_response)
    assert asyncio.run(drop_held_response()).status_code == 503
