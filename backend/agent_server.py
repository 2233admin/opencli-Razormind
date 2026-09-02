"""OpenCLI Agent Server — runs on LAN/NAT edge nodes.

Accepts HTTP POST /collect requests from the center API, executes opencli
locally (pointing at the node's own Chrome instance), and returns results.

Registration modes (AGENT_REGISTER):
  http  — LAN mode: agent POSTs its URL to center; center calls back via HTTP.
           Requires the agent to be reachable from the center.
  ws    — NAT/reverse-channel mode: agent initiates a persistent WebSocket to
           the center's /api/v1/nodes/ws endpoint.  The center pushes
           collect tasks down the WS connection; agent returns results in-band.
           Use this when the center cannot reach the agent (NAT, firewall, etc.).
  off   — Disable auto-registration entirely.

Usage on the edge node:
    pip install fastapi uvicorn httpx pyyaml websockets
    python -m backend.agent_server
    # or standalone:
    uvicorn backend.agent_server:app --host 0.0.0.0 --port 19823

Environment variables:
    AGENT_PORT              HTTP port to listen on (default: 19823)
    AGENT_ADVERTISE_URL     Canonical URL the center uses to identify this agent
                            (default: auto-detected from outbound IP)
    AGENT_MODE              Collection mode reported to center: bridge | cdp (default: bridge)
    AGENT_LABEL             Human-readable label for this agent (default: hostname)
    AGENT_REGISTER          Registration mode: http | ws | off (default: http)
    CENTRAL_API_URL         Center API base URL for self-registration
                            e.g. http://192.168.1.1:8031
                            Leave empty to skip auto-registration.
    HTTP_PROXY              HTTP proxy for outbound requests (agent → center)
    HTTPS_PROXY             HTTPS proxy for outbound requests (agent → center)
    AGENT_API_TOKEN         Fleet auth bearer token (ADR-0005) attached to both the
                            HTTP register call and the WS reverse-channel handshake.
                            Preferred name for this process; takes priority.
    API_AUTH_TOKEN          Fallback token env var — a node sharing the center's
                            process environment (e.g. same .env) just works without
                            a separate AGENT_API_TOKEN. Ignored if AGENT_API_TOKEN is set.
    OPENCLI_BRIDGE_BIN      Path to opencli 1.0 binary (default: /opt/opencli-bridge/bin/opencli)
    OPENCLI_CDP_BIN         Path to opencli 0.9 binary (default: /opt/opencli-cdp/bin/opencli)
    OPENCLI_CDP_ENDPOINT    Default Chrome CDP endpoint (default: http://localhost:19222)
    OPENCLI_DAEMON_PORT     Bridge daemon port (default: 19825)
    OPENCLI_TIMEOUT         opencli subprocess timeout in seconds (default: 120)
    AGENT_CODEX_ISOLATED_RUNNER Absolute path to an administrator-owned, externally
                            isolated Codex-compatible runner. Direct CLI execution
                            is disabled.
    AGENT_CODEX_ALLOWED_ROOTS JSON array of server-owned roots allowed for Codex cwd.
                            Empty or invalid configuration disables Codex dispatch.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import uuid
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse
from urllib.request import proxy_bypass

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from backend.agent_runtime_dispatch import (
    RuntimeInvokeRequest,
    cleanup_cdp_tabs,
    invoke_runtime,
    parse_output,
    snapshot_tab_ids,
)

# Imported directly from the registry submodule (not the `backend.agent_runtimes`
# package __init__) so this module's import graph is pinned to what registry.py
# itself pulls in — stdlib only, verified against pi_adapter.py's imports (also
# stdlib-only). agent_server.py runs standalone on edge nodes with a minimal
# dependency set (see module docstring); this avoids depending on the package
# __init__ staying lightweight as more adapters are added later.
from backend.agent_runtimes.base import AgentTask, RuntimeInvocationError
from backend.agent_runtimes.registry import (
    available_runtime_capabilities,
    available_runtimes,
    get_runtime,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("agent_server")

_OPENCLI_BIN = os.environ.get("OPENCLI_BIN") or "opencli"


def _resolve_bin(mode: str) -> str:  # noqa: ARG001
    configured = _OPENCLI_BIN or "opencli"
    if os.path.isabs(configured) or os.path.dirname(configured):
        return configured
    if os.name == "nt" and not os.path.splitext(configured)[1]:
        for suffix in (".cmd", ".bat", ".exe", ".ps1"):
            resolved = shutil.which(f"{configured}{suffix}")
            if resolved:
                return resolved
    return shutil.which(configured) or configured


_DEFAULT_CDP = os.environ.get("OPENCLI_CDP_ENDPOINT", "http://localhost:19222")
_BROWSER_PROFILE_KIND = os.environ.get("OPENCLI_BROWSER_PROFILE_KIND", "authenticated")
_DAEMON_PORT = int(os.environ.get("OPENCLI_DAEMON_PORT", "19825"))
_AGENT_PORT = int(os.environ.get("AGENT_PORT", "19823"))
_CENTRAL_API_URL = os.environ.get("CENTRAL_API_URL", "").rstrip("/")
_AGENT_ADVERTISE_URL = os.environ.get("AGENT_ADVERTISE_URL", "")
_AGENT_MODE = os.environ.get("AGENT_MODE", "cdp")
# Deployment/startup type reported to center:
# "docker" (container) | "shell" (native process).
_AGENT_DEPLOY_TYPE = os.environ.get("AGENT_DEPLOY_TYPE", "docker")
# True when the image was built with INSTALL_CHROME=true (Chrome bundled inside container).
# False → Chrome runs on the host; localhost must be remapped to host.docker.internal.
_AGENT_HAS_CHROME = os.environ.get("AGENT_HAS_CHROME", "false").lower() == "true"
_RUNTIME_BUNDLE_MANIFEST = os.environ.get(
    "BROWSER_RUNTIME_BUNDLE_MANIFEST",
    "/opt/browser-runtime-bundles/opencli-default/1/manifest.json",
)


def _bundle_declared_runtimes() -> set[str]:
    try:
        with open(_RUNTIME_BUNDLE_MANIFEST, encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError, TypeError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    capabilities = manifest.get("capabilities", [])
    if not isinstance(capabilities, list):
        return set()
    return {
        capability.get("runtime")
        for capability in capabilities
        if isinstance(capability, dict) and isinstance(capability.get("runtime"), str)
    }


def _available_agent_runtimes() -> list[str]:
    runtimes = list(available_runtimes())
    declared = _bundle_declared_runtimes()
    if "script-host" in declared and "script-host" not in runtimes:
        runtimes.append("script-host")
    return runtimes


_EDGE_RUNTIME_CONFIG_KEYS = frozenset(
    {"binary", "args", "env", "cwd", "project_root", "provider_dir", "usage_file"}
)



_AGENT_LABEL = os.environ.get("AGENT_LABEL", socket.gethostname())
# Registration mode:
#   http — LAN mode: agent POSTs its URL to center, center calls back via HTTP (default)
#   ws   — NAT/reverse-channel mode: agent opens WS to center, then
#          registers through the WS handshake.
#   off  — disable auto-registration entirely
_AGENT_REGISTER = os.environ.get("AGENT_REGISTER", "http").lower()
# opencli subprocess execution timeout in seconds
_OPENCLI_TIMEOUT = int(os.environ.get("OPENCLI_TIMEOUT", "120"))
# Outbound proxy for agent → center communication (optional)
_HTTP_PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
_HTTPS_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
# Fleet auth token (ADR-0005): AGENT_API_TOKEN preferred, API_AUTH_TOKEN accepted
# as a fallback for nodes that share the center's environment.
_AGENT_API_TOKEN = os.environ.get("AGENT_API_TOKEN") or os.environ.get("API_AUTH_TOKEN") or ""
_OHMYOPENCLI_ROOT = os.environ.get("OHMYOPENCLI_ROOT", "/opt/ohmyopencli")
_ACTIVE_COLLECTS: dict[str, asyncio.subprocess.Process] = {}


def _auth_headers() -> dict[str, str]:
    """Bearer-auth header for center requests, or {} when no token is configured.

    Reads the module global at call time (rather than closing over it) so
    tests can monkeypatch backend.agent_server._AGENT_API_TOKEN directly.
    """
    if not _AGENT_API_TOKEN:
        return {}
    return {"Authorization": f"Bearer {_AGENT_API_TOKEN}"}


def _require_collect_auth(authorization: str | None) -> None:
    """Fail closed when the edge node has no inbound fleet secret."""
    import hmac

    expected = f"Bearer {_AGENT_API_TOKEN}" if _AGENT_API_TOKEN else ""
    if not expected or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or unconfigured agent bearer token")


def _process_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            getattr(os, "killpg")(proc.pid, getattr(signal, "SIGKILL"))
    except (OSError, ProcessLookupError):
        proc.kill()
    await proc.wait()


async def _runtime_lineage(bin_path: str) -> dict[str, str]:
    """Measure the binaries/source used by this node; never echo declarations."""

    async def output(*argv: str, cwd: str | None = None) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd
            )
            stdout, _ = await proc.communicate()
            return stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""
        except (OSError, ValueError):
            return ""

    repo_commit = await output("git", "rev-parse", "HEAD", cwd=_OHMYOPENCLI_ROOT)
    source_commit = await output(
        "git",
        "log",
        "-1",
        "--format=%H",
        "--",
        "adapters/official-site/observe.js",
        cwd=_OHMYOPENCLI_ROOT,
    )
    version_text = await output(bin_path, "--version")
    match = re.search(r"\d+\.\d+\.\d+(?:[-+][\w.-]+)?", version_text)
    return {
        "ohmyopencli_repo_commit": repo_commit,
        "capability_source_commit": source_commit,
        "opencli_version": match.group(0) if match else version_text,
    }


def _detect_advertise_url() -> str:
    """Auto-detect the IP this node would use to reach the center, then build agent URL."""
    if _AGENT_ADVERTISE_URL:
        return _AGENT_ADVERTISE_URL.rstrip("/")
    try:
        # Use center host to detect outbound IP
        target = urlparse(_CENTRAL_API_URL).hostname or "8.8.8.8"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target, 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = socket.gethostbyname(socket.gethostname())
    return f"http://{ip}:{_AGENT_PORT}"


def _center_proxy() -> str | None:
    """Return the configured outbound proxy unless the center host is bypassed."""
    center_host = urlparse(_CENTRAL_API_URL).hostname
    if center_host and proxy_bypass(center_host):
        return None
    return _HTTPS_PROXY or _HTTP_PROXY or None


def _build_proxies() -> dict:
    """Build the legacy httpx proxy map for center registration."""
    proxy = _center_proxy()
    return {"https://": proxy, "http://": proxy} if proxy else {}


async def _register_with_center(advertise_url: str) -> None:
    """POST agent registration to the center API. Retries up to 5 times."""
    import httpx

    url = f"{_CENTRAL_API_URL}/api/v1/nodes/register"
    payload = {
        "agent_url": advertise_url,
        "mode": _AGENT_MODE,
        "node_type": _AGENT_DEPLOY_TYPE,
        "label": _AGENT_LABEL,
        "agent_protocol": "http",
        "runtimes": _available_agent_runtimes(),
        "runtime_capabilities": available_runtime_capabilities(),
        "profile_kind": _BROWSER_PROFILE_KIND,
    }
    proxies = _build_proxies()

    for attempt in range(1, 6):
        try:
            # httpx >= 0.28 removed 'proxies'; use 'proxy' (single URL) or mounts
            client_kwargs: dict = {"timeout": 10}
            headers = _auth_headers()
            if proxies:
                proxy_url = proxies.get("https://") or proxies.get("http://")
                try:
                    client_kwargs["proxy"] = proxy_url
                    async with httpx.AsyncClient(**client_kwargs) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                        resp.raise_for_status()
                except TypeError:
                    # Older httpx: fall back to 'proxies'
                    client_kwargs.pop("proxy", None)
                    client_kwargs["proxies"] = proxies
                    async with httpx.AsyncClient(**client_kwargs) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                        resp.raise_for_status()
            else:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
            logger.info("Registered with center %s as %s", _CENTRAL_API_URL, advertise_url)
            return
        except Exception as exc:
            wait = attempt * 3
            logger.warning(
                "Registration attempt %d failed: %s — retrying in %ds",
                attempt,
                exc,
                wait,
            )
            await asyncio.sleep(wait)
    logger.error("Could not register with center after 5 attempts")


async def _handle_ws_collect(ws, msg: dict) -> None:
    """Execute a collect task received over the WS channel and send back the result."""
    request_id = msg.get("request_id", "")
    req = CollectRequest(
        site=msg.get("site", ""),
        command=msg.get("command", ""),
        args=msg.get("args", {}),
        positional_args=msg.get("positional_args", []),
        format=msg.get("format", "json"),
        mode=msg.get("mode", "bridge"),
        execution_id=request_id,
    )
    try:
        result = await collect(req)
    except Exception as exc:
        logger.exception("WS collect error for request_id=%s: %s", request_id, exc)
        result = {"success": False, "items": [], "error": str(exc)}
    result["type"] = "result"
    result["request_id"] = request_id
    try:
        await ws.send(json.dumps(result))
    except Exception as exc:
        logger.error("WS: failed to send result for request_id=%s: %s", request_id, exc)


async def _handle_ws_agent_task(ws, msg: dict) -> None:
    """Execute an agent_task received over the WS channel: run the requested
    runtime adapter, streaming each RuntimeEvent back as an 'agent_event'
    frame, and finish with exactly one 'agent_result' frame carrying the
    terminal done/error event.

    Never raises out of this function — a single task crashing must not kill
    the WS receive loop (the caller fires this via asyncio.create_task).
    """
    request_id = msg.get("request_id", "")

    async def _send_result(result: dict) -> None:
        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "agent_result",
                        "request_id": request_id,
                        "result": result,
                    }
                )
            )
        except Exception as exc:
            logger.error("WS: failed to send agent_result for request_id=%s: %s", request_id, exc)

    runtime_type = msg.get("runtime", "")
    # Everything below is one outer try/except: get_runtime() lookup, adapter
    # construction of AgentTask, and the invoke() stream are all treated the
    # same way — any exception, of any type, must resolve the center's
    # pending future with an error result rather than propagate. The caller
    # fires this coroutine via asyncio.create_task, so an uncaught exception
    # here would otherwise vanish into an unretrieved task exception and the
    # center would hang until its own send_agent_task timeout.
    try:
        try:
            adapter = get_runtime(runtime_type)
        except ValueError as exc:
            logger.warning(
                "WS agent_task request_id=%s: unknown runtime %r: %s", request_id, runtime_type, exc
            )
            await _send_result(
                {
                    "type": "error",
                    "task_id": request_id,
                    "message": str(exc),
                    "error_type": "ValueError",
                }
            )
            return

        task = AgentTask(
            task_id=request_id,
            workflow=msg.get("workflow", ""),
            instructions=msg.get("instructions", ""),
            input=msg.get("input") or {},
            config=msg.get("config") or {},
            session_id=msg.get("session_id"),
            provider=msg.get("provider"),
            model=msg.get("model"),
            required_capabilities=tuple(msg.get("required_capabilities") or ()),
            permissions=msg.get("permissions") or {},
            budget=msg.get("budget") or {},
            evidence_requirements=tuple(msg.get("evidence_requirements") or ()),
        )
        config_errors = adapter.validate_config(task.config)
        if config_errors:
            raise RuntimeInvocationError("; ".join(config_errors), error_type="ConfigError")
        readiness = await adapter.readiness(task.config)
        if readiness.status != "ready":
            raise RuntimeInvocationError(
                readiness.reason or f"runtime {runtime_type!r} is not ready",
                error_type=readiness.reason_code or "RuntimeNotReady",
            )

        terminal_event: dict | None = None
        async for event in adapter.invoke(task):
            terminal_event = event
            try:
                await ws.send(
                    json.dumps(
                        {
                            "type": "agent_event",
                            "request_id": request_id,
                            "event": event,
                        }
                    )
                )
            except Exception as exc:
                logger.error(
                    "WS: failed to send agent_event for request_id=%s: %s",
                    request_id,
                    exc,
                )
        if terminal_event is None:
            # Contract violation (adapter yielded nothing) — still must resolve
            # the center's pending future rather than hang it until timeout.
            terminal_event = {
                "type": "error",
                "task_id": request_id,
                "message": f"runtime {runtime_type!r} adapter yielded no events",
                "error_type": "RuntimeInvocationError",
            }
        await _send_result(terminal_event)
        logger.info(
            "WS agent_task finished request_id=%s runtime=%s terminal=%s",
            request_id,
            runtime_type,
            terminal_event.get("type"),
        )
    except RuntimeInvocationError as exc:
        logger.exception(
            "WS agent_task request_id=%s: adapter invocation error: %s",
            request_id,
            exc,
        )
        await _send_result(
            {
                "type": "error",
                "task_id": request_id,
                "message": str(exc),
                "error_type": exc.error_type or type(exc).__name__,
            }
        )
    except Exception as exc:
        logger.exception("WS agent_task request_id=%s: unexpected error: %s", request_id, exc)
        await _send_result(
            {
                "type": "error",
                "task_id": request_id,
                "message": str(exc),
                "error_type": type(exc).__name__,
            }
        )


_ACTIVE_AGENT_TASKS: dict[str, asyncio.Task[None]] = {}


def _forget_ws_agent_task(request_id: str, task: asyncio.Task[None]) -> None:
    if _ACTIVE_AGENT_TASKS.get(request_id) is task:
        _ACTIVE_AGENT_TASKS.pop(request_id, None)


def _start_ws_agent_task(ws, msg: dict) -> None:
    request_id = msg.get("request_id", "")
    task = asyncio.create_task(_handle_ws_agent_task(ws, msg))
    _ACTIVE_AGENT_TASKS[request_id] = task
    task.add_done_callback(lambda completed: _forget_ws_agent_task(request_id, completed))


async def _register_via_ws(advertise_url: str) -> None:
    """Initiate persistent reverse WebSocket to center and handle collect tasks.

    Keeps reconnecting with exponential back-off so transient outages are
    recovered automatically.  The loop exits only when the process shuts down.
    """
    import websockets  # requires: pip install websockets

    ws_url = (
        _CENTRAL_API_URL.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        + "/api/v1/nodes/ws"
    )
    _proxy = _HTTPS_PROXY or _HTTP_PROXY or None
    # Computed once (not per reconnect attempt): runtime availability includes
    # fixed-binary compatibility probes and does not change without a restart.
    register_payload = json.dumps(
        {
            "type": "register",
            "agent_url": advertise_url,
            "mode": _AGENT_MODE,
            "node_type": _AGENT_DEPLOY_TYPE,
            "label": _AGENT_LABEL,
            "runtimes": _available_agent_runtimes(),
            "profile_kind": _BROWSER_PROFILE_KIND,
        }
    )

    attempt = 0
    while True:
        attempt += 1
        try:
            logger.info("WS connecting to center %s (attempt %d)", ws_url, attempt)
            connect_kwargs: dict = {"ping_interval": 30, "ping_timeout": 10}
            if _proxy:
                connect_kwargs["proxy"] = _proxy
            headers = _auth_headers()
            if headers:
                try:
                    # websockets >= 14 renamed extra_headers -> additional_headers.
                    connector = websockets.connect(
                        ws_url, additional_headers=headers, **connect_kwargs
                    )
                except TypeError:
                    connector = websockets.connect(ws_url, extra_headers=headers, **connect_kwargs)
            else:
                connector = websockets.connect(ws_url, **connect_kwargs)
            async with connector as ws:
                attempt = 0  # reset on successful connect
                await ws.send(register_payload)

                ack_raw = await asyncio.wait_for(ws.recv(), timeout=15)
                ack = json.loads(ack_raw)
                if ack.get("type") != "registered":
                    raise RuntimeError(f"Unexpected handshake response: {ack}")
                logger.info("WS registered with center as %s", advertise_url)

                # Main receive loop
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        logger.warning("WS: invalid JSON from center: %r", raw_msg[:200])
                        continue
                    msg_type = msg.get("type")
                    if msg_type == "collect":
                        asyncio.create_task(_handle_ws_collect(ws, msg))
                    elif msg_type == "agent_task":
                        _start_ws_agent_task(ws, msg)
                    elif msg_type == "cancel":
                        request_id = msg.get("request_id", "")
                        proc = _ACTIVE_COLLECTS.get(request_id)
                        if proc is not None:
                            asyncio.create_task(_kill_process_tree(proc))
                        agent_task = _ACTIVE_AGENT_TASKS.get(request_id)
                        if agent_task is not None:
                            agent_task.cancel()
                    elif msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                    elif msg_type == "pong":
                        pass
                    else:
                        logger.debug("WS: unknown message type %r", msg_type)

        except asyncio.CancelledError:
            logger.info("WS registration task cancelled — shutting down")
            return
        except Exception as exc:
            wait = min(attempt * 3, 60)
            logger.warning(
                "WS connection lost (attempt %d): %s — reconnecting in %ds", attempt, exc, wait
            )
            await asyncio.sleep(wait)
        finally:
            for task in tuple(_ACTIVE_AGENT_TASKS.values()):
                task.cancel()


_ws_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ws_task
    if not _CENTRAL_API_URL or _AGENT_REGISTER == "off":
        logger.info(
            "Auto-registration disabled (CENTRAL_API_URL=%r AGENT_REGISTER=%s)",
            _CENTRAL_API_URL or "",
            _AGENT_REGISTER,
        )
    elif _AGENT_REGISTER == "http":
        advertise_url = _detect_advertise_url()
        logger.info(
            "LAN registration: advertise_url=%s → center=%s",
            advertise_url,
            _CENTRAL_API_URL,
        )
        asyncio.get_event_loop().create_task(_register_with_center(advertise_url))
    elif _AGENT_REGISTER == "ws":
        advertise_url = _detect_advertise_url()
        logger.info(
            "WS registration: advertise_url=%s → center=%s",
            advertise_url,
            _CENTRAL_API_URL,
        )
        _ws_task = asyncio.get_event_loop().create_task(_register_via_ws(advertise_url))
    yield
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="OpenCLI Agent Server", version="0.4.1", lifespan=lifespan)


class CollectRequest(BaseModel):
    site: str
    command: str
    args: dict[str, Any] = {}
    # Values passed as positional CLI arguments (no --key prefix), inserted
    # right after [site] [command] and before any named --options.
    positional_args: list[str] = []
    format: str = "json"
    mode: str = "bridge"
    # CDP endpoint override; falls back to OPENCLI_CDP_ENDPOINT env var
    cdp_endpoint: str = ""
    execution_id: str = ""


@app.get("/health")
def health() -> dict:
    bin_path = _resolve_bin(_AGENT_MODE)
    return {
        "status": "ok",
        "opencli_bin": bin_path,
        "opencli_bin_exists": shutil.which(bin_path) is not None or os.path.isfile(bin_path),
        "default_cdp_endpoint": _DEFAULT_CDP,
    }


@app.post("/runtime/invoke")
async def invoke_runtime_http(
    req: RuntimeInvokeRequest, authorization: str | None = Header(default=None)
) -> dict:
    _require_collect_auth(authorization)
    if req.runtime == "codex":
        raise HTTPException(
            status_code=403,
            detail="Codex runtime is only available through controller WS dispatch",
        )
    if req.runtime not in _bundle_declared_runtimes():
        raise HTTPException(
            status_code=403,
            detail=f"runtime {req.runtime!r} is not declared by the installed bundle",
        )
    unsafe_keys = sorted(_EDGE_RUNTIME_CONFIG_KEYS.intersection(req.config))
    if unsafe_keys:
        raise HTTPException(
            status_code=400,
            detail="edge runtime config cannot override: " + ", ".join(unsafe_keys),
        )
    return await invoke_runtime(str(uuid.uuid4()), req, cdp_endpoint=_DEFAULT_CDP)


async def collect(req: CollectRequest) -> dict:
    cdp_ep = req.cdp_endpoint.strip() or _DEFAULT_CDP
    mode = req.mode

    bin_path = _resolve_bin(mode)

    cmd = [bin_path, req.site, req.command]
    cmd.extend([str(v) for v in req.positional_args])
    for k, v in req.args.items():
        cmd.extend([f"--{k}", str(v)])
    cmd.extend(["-f", req.format])

    env = os.environ.copy()
    if mode == "bridge":
        hostname = urlparse(cdp_ep).hostname or "localhost"
        # When running in a Docker agent WITHOUT bundled Chrome, localhost refers
        # to the container itself — remap to host.docker.internal so opencli
        # reaches the Chrome bridge daemon on the host machine.
        # Agents built with INSTALL_CHROME=true have their own Chrome and should
        # keep localhost as-is.
        if (
            _AGENT_DEPLOY_TYPE == "docker"
            and not _AGENT_HAS_CHROME
            and hostname in ("localhost", "127.0.0.1", "::1")
        ):
            hostname = "host.docker.internal"
        env.pop("OPENCLI_CDP_ENDPOINT", None)
        env["OPENCLI_DAEMON_HOST"] = hostname
        env["OPENCLI_DAEMON_PORT"] = str(_DAEMON_PORT)
        logger.info("bridge | cmd=%s daemon=%s:%s", " ".join(cmd), hostname, _DAEMON_PORT)
    else:
        # Same logic for CDP: remap localhost to host.docker.internal only when
        # running in Docker without bundled Chrome.
        if _AGENT_DEPLOY_TYPE == "docker" and not _AGENT_HAS_CHROME:
            cdp_ep = re.sub(r"(localhost|127\.0\.0\.1)", "host.docker.internal", cdp_ep)
        env["OPENCLI_CDP_ENDPOINT"] = cdp_ep
        logger.info("cdp | cmd=%s cdp=%s", " ".join(cmd), cdp_ep)

    pre_tab_ids: set[str] = set()
    if mode == "cdp":
        pre_tab_ids = await snapshot_tab_ids(cdp_ep)

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **_process_group_kwargs(),
        )
        if req.execution_id:
            _ACTIVE_COLLECTS[req.execution_id] = proc
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_OPENCLI_TIMEOUT)
        rc = proc.returncode
    except TimeoutError:
        logger.error("timeout | cmd=%s", " ".join(cmd))
        if proc:
            await _kill_process_tree(proc)
        if mode == "cdp":
            await cleanup_cdp_tabs(cdp_ep, pre_tab_ids)
        return {"success": False, "items": [], "error": "opencli timed out after 120s"}
    except Exception as exc:
        logger.exception("subprocess error | %s", exc)
        if mode == "cdp":
            await cleanup_cdp_tabs(cdp_ep, pre_tab_ids)
        return {"success": False, "items": [], "error": str(exc)}
    finally:
        if req.execution_id:
            _ACTIVE_COLLECTS.pop(req.execution_id, None)

    if mode == "cdp":
        await cleanup_cdp_tabs(cdp_ep, pre_tab_ids)

    stderr_str = stderr.decode().strip()
    stdout_str = stdout.decode()

    if stderr_str:
        logger.warning("stderr | %s", stderr_str[:500])
    if rc != 0:
        logger.error("exit=%d | %s", rc, stderr_str[:500])
        return {"success": False, "items": [], "error": f"opencli exit {rc}: {stderr_str}"}

    try:
        items = parse_output(stdout_str, req.format)
    except Exception as exc:
        logger.error("parse error | %s", exc)
        return {"success": False, "items": [], "error": f"parse error: {exc}"}

    logger.info("done | site=%s cmd=%s items=%d", req.site, req.command, len(items))
    trace_match = re.search(r"OpenCLI trace artifact:\s*([^\r\n]+)", stderr_str)
    metadata: dict[str, Any] = {"runtime": await _runtime_lineage(bin_path)}
    if trace_match:
        metadata["trace_artifact"] = trace_match.group(1)
    return {
        "success": True,
        "items": items,
        "error": None,
        "runtime": metadata["runtime"],
        "trace_artifact": metadata.get("trace_artifact"),
        "metadata": metadata,
    }


@app.post("/collect")
async def collect_http(
    req: CollectRequest, authorization: str | None = Header(default=None)
) -> dict:
    _require_collect_auth(authorization)
    return await collect(req)


@app.post("/collect/{execution_id}/cancel")
async def cancel_collect(
    execution_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _require_collect_auth(authorization)
    proc = _ACTIVE_COLLECTS.get(execution_id)
    if proc is not None:
        await _kill_process_tree(proc)
    return {"cancelled": True, "execution_id": execution_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=_AGENT_PORT, log_level="info")
