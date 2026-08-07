"""Fleet-LAN static bearer-token auth (ADR-0005, control-closeout issue 04).

The deployment surface is the operator's NetBird fleet LAN, so network
reachability must not equal operability: once a token is configured
(``API_AUTH_TOKEN`` / ``Settings.api_auth_token``), every HTTP request under
``/api`` or ``/mcp`` must carry ``Authorization: Bearer <token>``.

Dev posture: with no token configured (the default) the API stays open, and
``enforce_bind_guard`` only allows that posture on a localhost bind. The
existing test suite therefore runs unchanged with no token configured.

Exemptions (deliberate — issue 04: "exempt if and only if they leak nothing"):

- ``GET /health`` — liveness only. docker-compose's healthcheck curls it with
  no credentials, so it must stay open; its body is slimmed to
  ``{"status": "ok"}`` (see backend/main.py) so it leaks nothing. The
  config-bearing detail (task_executor, ...) lives at the authenticated
  ``GET /api/v1/system/config`` instead.
- ``/docs``, ``/redoc``, ``/openapi.json`` — outside the ``/api`` prefix.
  They disclose the API *schema* but no data; issue 04's scope is "every
  /api route". Tighten separately if schema disclosure becomes a concern.
- ``/api/v1/auth/local/status``, ``/setup``, and ``/login`` — the minimum
  unauthenticated surface required to establish a local administrator session.
  Setup still requires the Bootstrap credential, login is rate-limited, and
  status returns only a boolean.

Websocket endpoints under ``/api`` (the agent reverse channel in
api/v1/nodes.py and api/v1/browsers.py) are guarded by this same middleware.
A connecting agent may present the token through either channel:

- ``Authorization: Bearer <token>`` header — the primary path, set by
  agent_server.py's ``_auth_headers()`` when ``AGENT_API_TOKEN`` /
  ``API_AUTH_TOKEN`` is present in the agent's environment.
- ``?token=<token>`` query parameter — a fallback for clients that cannot
  set a WebSocket handshake header (browser ``WebSocket`` API, ``wscat``
  debugging, etc.).

A handshake that fails either check is rejected *before* ``ws.accept()`` is
ever reached: the middleware sends a raw ``websocket.close`` ASGI event with
code ``4401`` (the conventional "auth failure" websocket close code, chosen
to mirror HTTP 401) and never calls the wrapped app, so no endpoint code
runs against an unauthenticated socket.

Migration path (rollout is two independent, order-tolerant steps):

1. Deploy the updated agent_server.py to fleet nodes first. With no
   ``AGENT_API_TOKEN``/``API_AUTH_TOKEN`` set in the agent's environment,
   ``_auth_headers()`` returns ``{}`` and the connect call is byte-for-byte
   what it was before — a no-op rollout.
2. Once ready, set ``API_AUTH_TOKEN`` on the center. Any agent that hasn't
   picked up the env var yet gets its handshake closed with 4401 instead of
   accepted. That is not a crash: ``_register_via_ws``'s reconnect loop
   catches the close, backs off, and retries indefinitely (see
   agent_server.py). The fleet self-heals the moment an operator sets
   ``AGENT_API_TOKEN`` on that node's environment and restarts/redeploys it
   — no center-side action required beyond having set the token.

The standalone MCP stdio process and CLI are HTTP clients of this API; they
read ``API_AUTH_TOKEN`` from their own environment and attach the same header.
The built-in Streamable HTTP MCP endpoint is guarded directly by this middleware.

HTTP callers that also need an OIDC bearer token may send the fleet token in
``X-API-Token`` and reserve ``Authorization`` for OIDC. WebSocket clients keep
using the bearer header or query parameter described above.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Sequence
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from backend.config import get_settings
from backend.security.local_auth import is_local_session

#: Path prefixes guarded by :class:`FleetAuthMiddleware`.
PROTECTED_PREFIXES = ("/api", "/mcp")
PUBLIC_LOCAL_AUTH_PATHS = frozenset(
    {
        "/api/v1/auth/local/status",
        "/api/v1/auth/local/setup",
        "/api/v1/auth/local/login",
    }
)

_LOCALHOST_HOSTS = frozenset({"localhost", "::1"})


def is_localhost_host(host: str) -> bool:
    """True when *host* only accepts loopback connections (127/8, ::1, localhost)."""
    normalized = host.strip().strip("[]").lower()
    return normalized in _LOCALHOST_HOSTS or normalized.startswith("127.")


def resolve_uvicorn_host(argv: Sequence[str] | None = None) -> str:
    """Best-effort bind-host discovery for the running server process.

    The bind host is decided by uvicorn's own CLI — the Dockerfile CMD passes
    ``--host 0.0.0.0``; ``uv run uvicorn backend.main:app`` defaults to
    127.0.0.1 — and never reaches the ASGI app, so parse it back out of the
    process argv. No ``--host`` flag (pytest, programmatic ASGI transports,
    plain ``uvicorn app``) means uvicorn's default of 127.0.0.1.
    """
    args = sys.argv if argv is None else argv
    host = "127.0.0.1"
    for i, arg in enumerate(args):
        if arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
    return host


def enforce_bind_guard(host: str, token: str) -> None:
    """Refuse to serve a non-localhost bind without a token (ADR-0005).

    Called at the top of the lifespan startup in backend/main.py; raising
    there aborts uvicorn startup before a single request is served.
    """
    if token.strip() or is_localhost_host(host):
        return
    raise RuntimeError(
        f"Refusing to bind {host!r} without an API auth token: the fleet-LAN "
        "deployment surface (ADR-0005) requires API_AUTH_TOKEN to be set for "
        "any non-localhost bind. Set API_AUTH_TOKEN, or bind 127.0.0.1 for "
        "local development."
    )


def _token_matches(candidate: str, token: str) -> bool:
    """Constant-time comparison of a caller-supplied credential against *token*."""
    return secrets.compare_digest(candidate.strip().encode("utf-8"), token.encode("utf-8"))


def _bearer_credential(headers: Headers) -> str:
    """Extract the credential from an ``Authorization: Bearer <token>`` header, or ''."""
    auth = headers.get("authorization", "")
    scheme, _, credential = auth.partition(" ")
    return credential if scheme.lower() == "bearer" else ""


def _query_token(query_string: bytes) -> str:
    """Extract ``?token=`` from a raw ASGI query string, or ''."""
    values = parse_qs(query_string.decode("utf-8", errors="ignore")).get("token")
    return values[0] if values else ""


class FleetAuthMiddleware:
    """Pure-ASGI middleware validating a static bearer token on API/MCP routes.

    Guards both ``http`` and ``websocket`` scopes — see module docstring for
    the websocket credential channels, the 4401 close code, and the
    /health exemption rationale.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith(
            PROTECTED_PREFIXES
        ):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["path"] in PUBLIC_LOCAL_AUTH_PATHS:
            await self.app(scope, receive, send)
            return

        # Read per request: get_settings() is lru_cached (cheap), but
        # api/v1/system.py may cache_clear() it at runtime after a config
        # patch, so don't freeze the token at middleware construction time.
        token = get_settings().api_auth_token
        if not token:
            # Dev posture: no token configured -> API open. Only reachable on
            # a localhost bind thanks to enforce_bind_guard at startup.
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            headers = Headers(scope=scope)
            credential = _bearer_credential(headers) or _query_token(scope.get("query_string", b""))
            if credential and _token_matches(credential, token):
                await self.app(scope, receive, send)
                return
            await WebSocketClose(code=4401, reason="Invalid or missing API token")(
                scope, receive, send
            )
            return

        headers = Headers(scope=scope)
        credentials = (
            headers.get("x-api-token", ""),
            _bearer_credential(headers),
        )
        bootstrap = get_settings().bootstrap_admin_token
        if any(
            credential
            and (
                _token_matches(credential, token)
                or (bootstrap and _token_matches(credential, bootstrap))
                or is_local_session(credential)
            )
            for credential in credentials
        ):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=401,
            content={"success": False, "error": "Invalid or missing API token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)
