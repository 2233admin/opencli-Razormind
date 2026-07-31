"""Integration tests for the websocket branch of FleetAuthMiddleware (ADR-0005).

Uses starlette.testclient.TestClient (sync, real websocket handshake) against
the actual app + real endpoints (/api/v1/nodes/ws, /api/v1/browsers/agents/ws)
rather than the httpx ASGITransport `client` fixture used elsewhere in this
suite, which cannot drive websockets at all.

TestClient(app) is instantiated WITHOUT a `with` block so the app's lifespan
does not run (no scheduler / control-cycle / DB-migration startup needed for
these handshake-only assertions). The websocket endpoints under test open
their own AsyncSessionLocal() sessions directly and swallow DB errors as
non-fatal (see backend/api/v1/nodes.py / browsers.py), so no DB fixture is
required either — a real (possibly empty/misconfigured) DB session may fail
those upserts, and the handshake still completes.

One exception: api/v1/browsers.py's agent_ws_endpoint() calls
backend.browser_pool.get_pool(), which raises (uncaught) if init_pool() was
never called — normally done by main.py's lifespan. Without it the handshake
would hang (the endpoint's except-block logs and returns without ever
answering ws.receive_json()). The `_browser_pool_initialized` autouse fixture
below calls init_pool([]) directly (synchronous, no DB/Redis) so browsers.py
tests don't depend on running the full lifespan.
"""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import browser_pool
from backend.config import get_settings
from backend.main import app

TOKEN = "fleet-ws-integration-token"

REGISTER_MSG_NODES = {
    "type": "register",
    "agent_url": "http://127.0.0.1:19999",
    "mode": "cdp",
    "node_type": "shell",
    "label": "t",
}

REGISTER_MSG_BROWSERS = {
    "type": "register",
    "agent_url": "http://127.0.0.1:19999",
    "mode": "cdp",
    "label": "t",
}


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_auth_token", TOKEN)


@pytest.fixture(autouse=True)
def _browser_pool_initialized():
    """browsers.py's agent_ws_endpoint() needs get_pool() to succeed; normally
    done by main.py's lifespan, which we deliberately don't run here.

    Restores backend.browser_pool._pool afterward: it's a bare module global
    with no reset hook, and other suites (e.g. opencli_channel's health_check
    tests) rely on get_pool() raising RuntimeError when uninitialized to
    exercise their own fallback path. Leaving it initialized here would leak
    into every test collected after this file.
    """
    previous = browser_pool._pool
    browser_pool.init_pool([])
    yield
    browser_pool._pool = previous


@pytest.fixture
def test_client():
    # No `with` block: lifespan does not run.
    return TestClient(app)


# ── no credential -> rejected before accept ─────────────────────────────────


def test_nodes_ws_no_credential_is_rejected(test_client, auth_enabled):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect("/api/v1/nodes/ws"):
            pass
    assert exc_info.value.code == 4401


def test_browsers_ws_no_credential_is_rejected(test_client, auth_enabled):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect("/api/v1/browsers/agents/ws"):
            pass
    assert exc_info.value.code == 4401


# ── correct bearer header -> handshake succeeds ─────────────────────────────


def test_nodes_ws_bearer_header_accepted(test_client, auth_enabled):
    with test_client.websocket_connect(
        "/api/v1/nodes/ws", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as ws:
        ws.send_json(REGISTER_MSG_NODES)
        reply = ws.receive_json()
        assert reply["type"] == "registered"


def test_browsers_ws_bearer_header_accepted(test_client, auth_enabled):
    with test_client.websocket_connect(
        "/api/v1/browsers/agents/ws", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as ws:
        ws.send_json(REGISTER_MSG_BROWSERS)
        reply = ws.receive_json()
        assert reply["type"] == "registered"


# ── query-param token -> handshake succeeds ─────────────────────────────────


def test_nodes_ws_query_token_accepted(test_client, auth_enabled):
    with test_client.websocket_connect(f"/api/v1/nodes/ws?token={TOKEN}") as ws:
        ws.send_json(REGISTER_MSG_NODES)
        reply = ws.receive_json()
        assert reply["type"] == "registered"


# ── runtime advertisement ─────────────────────────────────────────────────
#
# nodes.py's node_ws_endpoint opens its own backend.database.AsyncSessionLocal
# session per-call (not the `get_db` dependency TestClient normally overrides
# for the async httpx client fixture elsewhere in this suite). Repointing that
# module-level sessionmaker at a throwaway engine while driving TestClient's
# background-threaded event loop from a sync test proved flaky (hangs) rather
# than reliably deterministic, so the actual EdgeNode.runtimes persistence
# (the _upsert_node(..., runtimes=...) write path) is covered at the unit
# level instead — see tests/unit/api/test_nodes_upsert.py — and these
# handshake-level tests stick to what TestClient can assert reliably: the
# 'registered' reply and validation-rejection behavior.


def test_nodes_ws_register_with_runtimes_accepted(test_client, auth_enabled):
    msg = {**REGISTER_MSG_NODES, "runtimes": ["pi", "stub"]}
    with test_client.websocket_connect(
        "/api/v1/nodes/ws", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as ws:
        ws.send_json(msg)
        reply = ws.receive_json()
        assert reply["type"] == "registered"


def test_nodes_ws_register_invalid_runtimes_type_rejected(test_client, auth_enabled):
    msg = {**REGISTER_MSG_NODES, "runtimes": "not-a-list"}
    with pytest.raises(WebSocketDisconnect):
        with test_client.websocket_connect(
            "/api/v1/nodes/ws", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as ws:
            ws.send_json(msg)
            ws.receive_json()


def test_browsers_ws_register_tolerates_extra_runtimes_key(test_client, auth_enabled):
    """browsers.py's agent_ws_endpoint never declared a runtimes field, but
    must tolerate an extra key in the register payload without error."""
    msg = {**REGISTER_MSG_BROWSERS, "runtimes": ["pi"]}
    with test_client.websocket_connect(
        "/api/v1/browsers/agents/ws", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as ws:
        ws.send_json(msg)
        reply = ws.receive_json()
        assert reply["type"] == "registered"
