"""Edge node management API.

Handles registration, lifecycle events, and management of remote agent nodes.
Both HTTP-mode agents (center calls agent) and WS-mode agents (agent initiates
reverse channel) register here and have their online/offline history tracked.
"""
import io
import logging
import re
import shlex
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.edge_node import EdgeNodeEventRead, EdgeNodeRead

if TYPE_CHECKING:
    from backend.models.edge_node import EdgeNode

router = APIRouter(prefix="/nodes", tags=["nodes"])
logger = logging.getLogger(__name__)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _upsert_node(
    db: AsyncSession,
    url: str,
    label: str = "",
    protocol: str = "http",
    mode: str = "bridge",
    ip: str | None = None,
    node_type: str = "chrome",
    runtimes: list[str] | None = None,
    runtime_capabilities: dict[str, list[str]] | None = None,
) -> "EdgeNode":
    from backend.models.edge_node import EdgeNode

    result = await db.execute(select(EdgeNode).where(EdgeNode.url == url))
    node = result.scalar_one_or_none()
    now = _utcnow()
    if node:
        node.status = "online"
        node.last_seen_at = now
        node.protocol = protocol
        node.mode = mode
        node.node_type = node_type
        if label:
            node.label = label
        if ip:
            node.ip = ip
        if runtimes is not None:
            node.runtimes = runtimes
        if runtime_capabilities is not None:
            node.runtime_capabilities = runtime_capabilities
    else:
        node = EdgeNode(
            url=url,
            label=label or url,
            protocol=protocol,
            mode=mode,
            node_type=node_type,
            status="online",
            last_seen_at=now,
            ip=ip,
            runtimes=runtimes,
            runtime_capabilities=runtime_capabilities,
        )
        db.add(node)
    await db.flush()
    return node


async def _write_event(
    db: AsyncSession,
    node_id: str,
    event: str,
    ip: str | None = None,
    event_meta: dict | None = None,
) -> None:
    from backend.models.edge_node import EdgeNodeEvent

    db.add(EdgeNodeEvent(node_id=node_id, event=event, ip=ip, event_meta=event_meta))
    await db.flush()


def _pool_add(
    url: str,
    mode: str,
    protocol: str,
    node_type: str = "chrome",
    profile_kind: str = "authenticated",
) -> None:
    """Hot-add a node URL to the in-memory browser pool."""
    try:
        from backend.browser_pool import LocalBrowserPool, get_pool

        pool = get_pool()
        if isinstance(pool, LocalBrowserPool):
            if url not in pool.endpoints:
                pool.add_endpoint(url)
            pool.set_mode(url, mode)
            pool.set_agent_url(url, url)
            pool.set_agent_protocol(url, protocol)
            pool.set_node_type(url, node_type)
            pool.set_profile_kind(url, profile_kind)
    except Exception as exc:
        logger.warning("pool_add failed for %s: %s", url, exc)


def _pool_remove(url: str) -> None:
    try:
        from backend.browser_pool import LocalBrowserPool, get_pool

        pool = get_pool()
        if isinstance(pool, LocalBrowserPool) and url in pool.endpoints:
            pool.remove_endpoint(url)
    except Exception as exc:
        logger.warning("pool_remove failed for %s: %s", url, exc)


def _extract_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Registration (HTTP mode) ──────────────────────────────────────────────────


class NodeRegisterRequest(BaseModel):
    agent_url: str
    # Chrome connection mode: "bridge" | "cdp" — how opencli connects to Chrome during collection
    mode: str = "bridge"
    # Node startup/deployment type: "docker" | "shell" — orthogonal to Chrome connection mode
    node_type: str = "docker"
    label: str = ""
    agent_protocol: str = "http"
    runtimes: list[str] | None = None
    runtime_capabilities: dict[str, list[str]] | None = None
    profile_kind: str = "authenticated"


@router.post("/register", response_model=ApiResponse[EdgeNodeRead])
async def register_node(
    body: NodeRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Agent self-registration (HTTP mode).

    Agent POSTs its own URL; center adds it to the pool and records the event.
    Idempotent: calling again updates mode/label and records an 'online' event.
    """
    from backend.models.browser import BrowserInstance

    url = body.agent_url.rstrip("/")
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="agent_url must be an http/https URL")
    if body.mode not in ("bridge", "cdp"):
        raise HTTPException(status_code=400, detail="mode must be 'bridge' or 'cdp'")
    if body.node_type not in ("docker", "shell"):
        raise HTTPException(status_code=400, detail="node_type must be 'docker' or 'shell'")
    if body.agent_protocol not in ("http", "ws"):
        raise HTTPException(status_code=400, detail="agent_protocol must be 'http' or 'ws'")
    if body.profile_kind not in ("anonymous", "authenticated"):
        raise HTTPException(
            status_code=400,
            detail="profile_kind must be 'anonymous' or 'authenticated'",
        )

    ip = _extract_ip(request)
    node = await _upsert_node(
        db,
        url,
        body.label,
        body.agent_protocol,
        body.mode,
        ip,
        body.node_type,
        runtimes=body.runtimes,
        runtime_capabilities=body.runtime_capabilities,
    )
    await _write_event(
        db,
        node.id,
        "registered",
        ip=ip,
        event_meta={
            "mode": body.mode,
            "node_type": body.node_type,
            "protocol": body.agent_protocol,
            "runtimes": body.runtimes,
            "runtime_capabilities": body.runtime_capabilities,
            "profile_kind": body.profile_kind,
        },
    )

    # Maintain backwards-compatible BrowserInstance record for pool config
    result = await db.execute(select(BrowserInstance).where(BrowserInstance.endpoint == url))
    inst = result.scalar_one_or_none()
    if inst:
        inst.mode = body.mode
        inst.agent_url = url
        inst.agent_protocol = body.agent_protocol
        inst.profile_kind = body.profile_kind
        if body.label:
            inst.label = body.label
    else:
        inst = BrowserInstance(
            endpoint=url,
            mode=body.mode,
            agent_url=url,
            agent_protocol=body.agent_protocol,
            label=body.label,
            profile_kind=body.profile_kind,
            profile_name=url,
        )
        db.add(inst)

    await db.commit()
    await db.refresh(node)

    _pool_add(
        url,
        body.mode,
        body.agent_protocol,
        body.node_type,
        body.profile_kind,
    )
    logger.info(
        "Node registered (HTTP): %s (node_type=%s mode=%s label=%r)",
        url,
        body.node_type,
        body.mode,
        body.label,
    )
    return ApiResponse.ok(EdgeNodeRead.model_validate(node))


# ── Node list & events ────────────────────────────────────────────────────────


@router.get("", response_model=ApiResponse[list[EdgeNodeRead]])
async def list_nodes(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """List all registered edge nodes, with real-time WS online status overlaid."""
    from backend import ws_agent_manager
    from backend.models.edge_node import EdgeNode

    result = await db.execute(select(EdgeNode).order_by(EdgeNode.created_at))
    nodes = result.scalars().all()

    ws_connected = set(ws_agent_manager.list_connected())
    out = []
    for node in nodes:
        data = EdgeNodeRead.model_validate(node)
        # WS-connected nodes are always "online" regardless of DB status
        if node.protocol == "ws" and node.url in ws_connected:
            data = data.model_copy(update={"status": "online"})
        out.append(data)
    return ApiResponse.ok(out)


@router.get("/{node_id}/stats", response_model=ApiResponse[dict])
async def get_node_stats(
    node_id: str,
    range: str = Query("7d", description="Time range: all | today | yesterday | 7d | 30d | custom"),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Return task run statistics for a specific edge node."""
    from backend.api.v1.dashboard import _parse_time_range
    from backend.models.edge_node import EdgeNode
    from backend.models.task import TaskRun

    result = await db.execute(select(EdgeNode).where(EdgeNode.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    since, until = _parse_time_range(range, start, end)

    base_q = select(func.count()).select_from(TaskRun).where(TaskRun.node_url == node.url)
    if since:
        base_q = base_q.where(TaskRun.created_at >= since)
    if until:
        base_q = base_q.where(TaskRun.created_at < until)

    total = (await db.execute(base_q)).scalar_one()
    success = (await db.execute(base_q.where(TaskRun.status == "completed"))).scalar_one()
    failed = (await db.execute(base_q.where(TaskRun.status == "failed"))).scalar_one()

    # Sum of records collected
    rec_q = select(func.coalesce(func.sum(TaskRun.records_collected), 0)).where(
        TaskRun.node_url == node.url
    )
    if since:
        rec_q = rec_q.where(TaskRun.created_at >= since)
    if until:
        rec_q = rec_q.where(TaskRun.created_at < until)
    records_collected = (await db.execute(rec_q)).scalar_one() or 0

    finished = success + failed
    return ApiResponse.ok(
        {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": round(success / finished * 100, 1) if finished > 0 else 0.0,
            "records_collected": int(records_collected),
        }
    )


@router.get("/{node_id}/events", response_model=ApiResponse[list[EdgeNodeEventRead]])
async def list_node_events(node_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """Return the last 100 lifecycle events for a node."""
    from backend.models.edge_node import EdgeNode, EdgeNodeEvent

    result = await db.execute(select(EdgeNode).where(EdgeNode.id == node_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Node not found")

    result = await db.execute(
        select(EdgeNodeEvent)
        .where(EdgeNodeEvent.node_id == node_id)
        .order_by(EdgeNodeEvent.created_at.desc())
        .limit(100)
    )
    events = result.scalars().all()
    return ApiResponse.ok([EdgeNodeEventRead.model_validate(e) for e in events])


@router.delete("/{node_id}", response_model=ApiResponse[None])
async def delete_node(node_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """Remove a node from the DB and from the in-memory pool."""
    from backend.models.edge_node import EdgeNode

    result = await db.execute(select(EdgeNode).where(EdgeNode.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    _pool_remove(node.url)

    # Also remove BrowserInstance record
    from backend.models.browser import BrowserInstance

    bi_result = await db.execute(
        select(BrowserInstance).where(BrowserInstance.endpoint == node.url)
    )
    bi = bi_result.scalar_one_or_none()
    if bi:
        await db.delete(bi)

    await db.delete(node)
    await db.commit()
    logger.info("Node deleted: %s", node.url)
    return ApiResponse.ok(None)


# ── Install script ────────────────────────────────────────────────────────────


@router.get("/install/patch-opencli.js", response_class=PlainTextResponse)
async def get_opencli_runtime_patch() -> PlainTextResponse:
    """Serve the pinned OpenCLI 1.8.7 managed-CDP routing patch."""
    candidates = [
        Path(__file__).parent.parent.parent.parent / "scripts" / "patch-opencli.js",
        Path("/app/scripts/patch-opencli.js"),
    ]
    for path in candidates:
        if path.exists():
            return PlainTextResponse(
                path.read_text(encoding="utf-8"),
                media_type="application/javascript",
            )
    raise HTTPException(status_code=404, detail="OpenCLI runtime patch not packaged")

@router.get("/install/agent-runtime.tar.gz")
async def get_agent_runtime_bundle() -> Response:
    """Serve the Python packages required by non-Docker Agents."""
    source_roots = [
        Path(__file__).parent.parent.parent.parent,
        Path("/app"),
    ]
    source_root = next(
        (
            root
            for root in source_roots
            if (root / "backend" / "agent_runtimes").is_dir()
        ),
        None,
    )
    if source_root is None:
        raise HTTPException(status_code=404, detail="Agent runtime package not packaged")

    package_dirs = (
        Path("backend/agent_runtimes"),
        Path("backend/miniflow"),
        Path("backend/security"),
    )
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for package_dir in package_dirs:
            package_root = source_root / package_dir
            for path in sorted(package_root.rglob("*.py")):
                info = tar.gettarinfo(
                    str(path),
                    arcname=path.relative_to(source_root).as_posix(),
                )
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                with path.open("rb") as stream:
                    tar.addfile(info, stream)

    return Response(
        content=archive.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="opencli-agent-runtime.tar.gz"'},
    )


@router.get("/install/agent.sh", response_class=PlainTextResponse)
async def get_install_script(request: Request) -> PlainTextResponse:
    """Return the agent install script with CENTRAL_API_URL pre-filled.

    URL resolution priority:
    1. PUBLIC_URL env var (most reliable, admin-configured)
    2. X-Forwarded-Host / X-Forwarded-Proto headers (reverse proxy)
    3. Host header + scheme (direct access)
    4. request.base_url fallback (may be internal when behind changeOrigin proxy)
    """
    from backend.config import get_settings

    settings = get_settings()

    if settings.public_url:
        base_url = settings.public_url.rstrip("/")
    else:
        # Try to reconstruct from proxy headers (nginx, Vite proxy, etc.)
        forwarded_host = request.headers.get("x-forwarded-host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        host = request.headers.get("host", "")

        if forwarded_host:
            proto = forwarded_proto or ("https" if "443" in forwarded_host else "http")
            base_url = f"{proto}://{forwarded_host}"
        elif host and not host.startswith(("api:", "localhost:800", "127.0.0.1:800")):
            # Host is the original client-visible host (not an internal service name)
            proto = "https" if request.url.scheme == "https" else "http"
            base_url = f"{proto}://{host}"
        else:
            # Last resort: request.base_url (may be internal in Docker)
            base_url = str(request.base_url).rstrip("/")

    # Try file path first (local dev where scripts/ is accessible)
    for candidate in [
        Path(__file__).parent.parent.parent.parent / "scripts" / "install-agent.sh",
        Path("/app/scripts/install-agent.sh"),
    ]:
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            content = _render_install_script(content, base_url, settings)
            return PlainTextResponse(content, media_type="text/plain")

    # Inline fallback (Docker: only ./backend is mounted)
    content = _install_script_template(
        base_url,
        image_tag=settings.image_tag,
        agent_api_token=settings.api_auth_token,
        fleet_network_provider=settings.fleet_network_provider,
        netbird_mode=settings.netbird_mode,
        netbird_setup_key=settings.netbird_setup_key,
        netbird_management_url=settings.netbird_management_url,
        netbird_image_tag=settings.netbird_image_tag,
    )
    return PlainTextResponse(content, media_type="text/plain")


def _shell_default_sub(content: str, placeholder: str, value: str | None) -> str:
    """Substitute a `${VAR:-__PLACEHOLDER__}` default with a shell-safe value.

    install-agent.sh wraps each placeholder in an outer pair of double quotes,
    e.g. ``VAR="${VAR:-__PLACEHOLDER__}"``. This script is fetched and run via
    `curl | bash`, so naively substituting an operator-controlled value there
    (NetBird setup key, API token, ...) would let a '"', backtick, or
    '$(...)' in the value break out of the double quotes into arbitrary
    shell. shlex.quote() wraps the value in its own single quotes; we strip
    the now-redundant outer double quotes so that single-quoted word is the
    only quoting in play (leaving the outer quotes would let a literal '"'
    or backtick in the value escape them, since neither is neutralized
    inside an already-open double-quoted string).
    """
    quoted = shlex.quote(value or "")
    pattern = re.compile(
        r'"(\$\{[A-Za-z_][A-Za-z0-9_]*:-(?:\$\{[A-Za-z_][A-Za-z0-9_]*:-)?)'
        + re.escape(placeholder)
        + r'(\}\}?)"'
    )
    new_content, count = pattern.subn(lambda m: m.group(1) + quoted + m.group(2), content)
    if count:
        return new_content
    # Placeholder wasn't in the expected quoted-default form (template
    # changed) -- fall back to escaping for a bare double-quoted context so
    # '"', '`', and '$' in the value still can't break out.
    escaped = (
        (value or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return content.replace(placeholder, escaped)


def _render_install_script(content: str, base_url: str, settings) -> str:
    replacements = {
        "__CENTRAL_API_URL__": base_url,
        "__IMAGE_TAG__": settings.image_tag,
        "__AGENT_API_TOKEN__": settings.api_auth_token,
        "__FLEET_NETWORK_PROVIDER__": settings.fleet_network_provider,
        "__NETBIRD_MODE__": settings.netbird_mode,
        "__NETBIRD_SETUP_KEY__": settings.netbird_setup_key,
        "__NETBIRD_MANAGEMENT_URL__": settings.netbird_management_url,
        "__NETBIRD_IMAGE_TAG__": settings.netbird_image_tag,
    }
    for placeholder, value in replacements.items():
        content = _shell_default_sub(content, placeholder, value)
    return content


def _install_script_template(
    central_url: str,
    image_tag: str = "latest",
    agent_api_token: str = "",
    fleet_network_provider: str = "lan",
    netbird_mode: str = "off",
    netbird_setup_key: str = "",
    netbird_management_url: str = "",
    netbird_image_tag: str = "latest",
) -> str:
    # These become bash `VAR="${VAR:-default}"` fallbacks below. This script
    # is fetched and run via `curl | bash`, so shlex.quote() each
    # operator-controlled value and drop the surrounding literal quotes in
    # the template: the single-quoted word shlex.quote produces must be the
    # *only* quoting in play, since embedding it inside an existing `"..."`
    # would let a '"', backtick, or '$(...)' in the value break back out.
    central_url_q = shlex.quote(central_url)
    image_tag_q = shlex.quote(image_tag)
    agent_api_token_q = shlex.quote(agent_api_token)
    fleet_network_provider_q = shlex.quote(fleet_network_provider)
    netbird_mode_q = shlex.quote(netbird_mode)
    netbird_setup_key_q = shlex.quote(netbird_setup_key)
    netbird_management_url_q = shlex.quote(netbird_management_url)
    netbird_image_tag_q = shlex.quote(netbird_image_tag)
    return f"""#!/usr/bin/env bash
# OpenCLI Agent — one-line install
# Usage: curl -fsSL {central_url}/api/v1/nodes/install/agent.sh | bash
# Or with API auth:
#   curl -fsSL -H "Authorization: Bearer $API_AUTH_TOKEN" \\
#     {central_url}/api/v1/nodes/install/agent.sh | bash

set -euo pipefail
CENTRAL_API_URL=${{CENTRAL_API_URL:-{central_url_q}}}
AGENT_API_TOKEN=${{AGENT_API_TOKEN:-${{API_AUTH_TOKEN:-{agent_api_token_q}}}}}
AGENT_REGISTER="${{AGENT_REGISTER:-ws}}"
AGENT_PORT="${{AGENT_PORT:-19823}}"
AGENT_ADVERTISE_URL="${{AGENT_ADVERTISE_URL:-}}"
AGENT_LABEL="${{AGENT_LABEL:-$(hostname)}}"
IMAGE_TAG=${{IMAGE_TAG:-{image_tag_q}}}
FLEET_NETWORK_PROVIDER=${{FLEET_NETWORK_PROVIDER:-{fleet_network_provider_q}}}
NETBIRD_MODE=${{NETBIRD_MODE:-{netbird_mode_q}}}
NETBIRD_SETUP_KEY=${{NETBIRD_SETUP_KEY:-{netbird_setup_key_q}}}
NETBIRD_MANAGEMENT_URL=${{NETBIRD_MANAGEMENT_URL:-{netbird_management_url_q}}}
NETBIRD_IMAGE_TAG=${{NETBIRD_IMAGE_TAG:-{netbird_image_tag_q}}}
INSTALL_MODE="${{1:-docker}}"

if [[ "$NETBIRD_MODE" == "off" && -n "$NETBIRD_SETUP_KEY" ]]; then
  NETBIRD_MODE="host"
fi

info() {{ printf "\\e[32m[INFO]\\e[0m  %s\\n" "$*"; }}
warn() {{ printf "\\e[33m[WARN]\\e[0m  %s\\n" "$*"; }}
die()  {{ printf "\\e[31m[ERROR]\\e[0m %s\\n" "$*" >&2; exit 1; }}

[[ -z "$CENTRAL_API_URL" ]] && die "CENTRAL_API_URL is required"
info "Center: $CENTRAL_API_URL | Register: $AGENT_REGISTER | Mode: $INSTALL_MODE"
info "Fleet Network: $FLEET_NETWORK_PROVIDER | NetBird: $NETBIRD_MODE"

case "$FLEET_NETWORK_PROVIDER" in
  lan|netbird|wireguard|ssh|custom) ;;
  *)
    die "Unknown FLEET_NETWORK_PROVIDER '$FLEET_NETWORK_PROVIDER'. "\
"Use lan, netbird, wireguard, ssh, or custom."
    ;;
esac

run_netbird() {{
  if netbird "$@"; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo netbird "$@"
  else
    return 1
  fi
}}

install_netbird_host() {{
  [[ -n "$NETBIRD_SETUP_KEY" ]] || die "NETBIRD_SETUP_KEY is required when NETBIRD_MODE=host"
  if ! command -v netbird >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || die "curl is required to install NetBird"
    if [[ "$(id -u)" == "0" ]]; then
      curl -fsSL https://pkgs.netbird.io/install.sh | sh
    elif command -v sudo >/dev/null 2>&1; then
      curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh
    else
      die "sudo is required to install NetBird"
    fi
  fi
  up_args=(up --setup-key "$NETBIRD_SETUP_KEY")
  [[ -n "$NETBIRD_MANAGEMENT_URL" ]] && up_args+=(--management-url "$NETBIRD_MANAGEMENT_URL")
  run_netbird "${{up_args[@]}}" || die "netbird up failed"
  run_netbird status || warn "netbird status failed; continuing after successful up"
}}

install_netbird_docker() {{
  [[ -n "$NETBIRD_SETUP_KEY" ]] || die "NETBIRD_SETUP_KEY is required when NETBIRD_MODE=docker"
  command -v docker >/dev/null 2>&1 || die "Docker is required for NETBIRD_MODE=docker"
  container_name="opencli-netbird"
  docker rm -f "$container_name" >/dev/null 2>&1 || true
  netbird_env=(-e NB_SETUP_KEY="$NETBIRD_SETUP_KEY")
  if [[ -n "$NETBIRD_MANAGEMENT_URL" ]]; then
    netbird_env+=(-e NB_MANAGEMENT_URL="$NETBIRD_MANAGEMENT_URL")
  fi
  docker run -d --name "$container_name" --restart unless-stopped --network host --privileged \\
    "${{netbird_env[@]}}" \\
    -v netbird-client:/var/lib/netbird \\
    "netbirdio/netbird:${{NETBIRD_IMAGE_TAG}}"
}}

install_netbird() {{
  case "$NETBIRD_MODE" in
    off)
      if [[ "$FLEET_NETWORK_PROVIDER" == "netbird" ]]; then
        warn "FLEET_NETWORK_PROVIDER=netbird but NETBIRD_MODE=off and no setup key was provided"
      fi
      ;;
    host) install_netbird_host ;;
    docker) install_netbird_docker ;;
    *) die "Unknown NETBIRD_MODE '$NETBIRD_MODE'. Use off, host, or docker." ;;
  esac
}}

install_fleet_network() {{
  case "$FLEET_NETWORK_PROVIDER" in
    netbird)
      install_netbird
      ;;
    lan)
      ;;
    wireguard)
      info "WireGuard provider selected; assuming the WireGuard interface is already up."
      [[ -n "$AGENT_ADVERTISE_URL" ]] || \
        warn "Set AGENT_ADVERTISE_URL to the WireGuard-reachable agent URL "\
"when center HTTP callbacks are required."
      ;;
    ssh)
      info "SSH provider selected; assuming the SSH tunnel is already established."
      [[ -n "$AGENT_ADVERTISE_URL" ]] || \
        warn "Set AGENT_ADVERTISE_URL to the forwarded agent URL "\
"when center HTTP callbacks are required."
      ;;
    custom)
      info "Custom network provider selected; assuming reachability is managed "\
"outside this installer."
      [[ -n "$AGENT_ADVERTISE_URL" ]] || \
        warn "Set AGENT_ADVERTISE_URL to the center-reachable agent URL "\
"when center HTTP callbacks are required."
      ;;
  esac
}}

install_docker() {{
  command -v docker >/dev/null 2>&1 || die "Docker not found"
  CONTAINER_NAME="opencli-agent"
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  # Auto-find a free port starting from AGENT_PORT
  ORIG_PORT="$AGENT_PORT"
  while docker ps --format '{{{{.Ports}}}}' | grep -q "0.0.0.0:${{AGENT_PORT}}->"; do
    AGENT_PORT=$(( AGENT_PORT + 1 ))
  done
  if [[ "$AGENT_PORT" != "$ORIG_PORT" ]]; then
    printf "\\e[33m[WARN]\\e[0m  Port $ORIG_PORT in use, using $AGENT_PORT instead\\n"
  fi
  # Inside Docker, 'localhost'/'127.0.0.1' refers to the container itself, not the host.
  DOCKER_CENTRAL_URL=$(echo "$CENTRAL_API_URL" | sed \\
    -e 's|localhost|host.docker.internal|g' \\
    -e 's|127\\.0\\.0\\.1|host.docker.internal|g')
  if [[ "$DOCKER_CENTRAL_URL" != "$CENTRAL_API_URL" ]]; then
    printf "\\e[33m[WARN]\\e[0m  Docker networking: using $DOCKER_CENTRAL_URL\\n"
  fi
  PROXY_ARGS=""
  [[ -n "${{HTTP_PROXY:-}}" ]]  && PROXY_ARGS="$PROXY_ARGS -e HTTP_PROXY=$HTTP_PROXY"
  [[ -n "${{HTTPS_PROXY:-}}" ]] && PROXY_ARGS="$PROXY_ARGS -e HTTPS_PROXY=$HTTPS_PROXY"
  # shellcheck disable=SC2086
  # --add-host makes host.docker.internal work on Linux (no-op on Docker Desktop)
  docker run -d --name "$CONTAINER_NAME" --restart unless-stopped \\
    --add-host=host.docker.internal:host-gateway \\
    -e CENTRAL_API_URL="$DOCKER_CENTRAL_URL" -e AGENT_REGISTER="$AGENT_REGISTER" \\
    -e AGENT_PORT="$AGENT_PORT" -e AGENT_ADVERTISE_URL="$AGENT_ADVERTISE_URL" \\
    -e AGENT_LABEL="$AGENT_LABEL" -e AGENT_MODE="cdp" \\
    -e AGENT_API_TOKEN="$AGENT_API_TOKEN" \\
    $PROXY_ARGS -p "${{AGENT_PORT}}:${{AGENT_PORT}}" \\
    "ghcr.io/2233admin/opencli-admin-agent:${{IMAGE_TAG}}"
  info "Agent container started!"
}}

install_python() {{
  command -v python3 >/dev/null 2>&1 || die "Python 3 not found"
  VENV_DIR="$HOME/.opencli-agent-venv"
  if python3 -m pip install --user --quiet fastapi uvicorn httpx pyyaml websockets 2>/dev/null; then
    PYTHON_BIN="python3"
  elif python3 -m venv "$VENV_DIR" 2>/dev/null && \\
      "$VENV_DIR/bin/pip" install --quiet fastapi uvicorn httpx pyyaml websockets; then
    PYTHON_BIN="$VENV_DIR/bin/python3"
  else
    die "Cannot install Python deps. Try a venv and install fastapi uvicorn httpx pyyaml websockets"
  fi
  CENTRAL_API_URL="$CENTRAL_API_URL" AGENT_REGISTER="$AGENT_REGISTER" \\
  AGENT_PORT="$AGENT_PORT" AGENT_ADVERTISE_URL="$AGENT_ADVERTISE_URL" \\
  AGENT_LABEL="$AGENT_LABEL" AGENT_MODE="cdp" \\
  AGENT_API_TOKEN="$AGENT_API_TOKEN" \\
  nohup "$PYTHON_BIN" -m backend.agent_server > /tmp/opencli-agent.log 2>&1 &
  info "Agent started (PID=$!). Logs: /tmp/opencli-agent.log"
}}

install_fleet_network

case "$INSTALL_MODE" in
  docker) install_docker ;;
  python) install_python ;;
  *) die "Usage: $0 [docker|python]" ;;
esac
info "Done! Nodes will appear at: $CENTRAL_API_URL → 节点管理"
"""


# ── WebSocket reverse channel ─────────────────────────────────────────────────


@router.websocket("/ws")
async def node_ws_endpoint(ws: WebSocket) -> None:
    """Reverse WebSocket channel for NAT/unreachable edge agents.

    Agent initiates this connection, sends a 'register' handshake, then
    listens for 'collect' tasks from the center and sends back 'result' messages.
    """
    from backend import ws_agent_manager
    from backend.database import AsyncSessionLocal
    from backend.models.browser import BrowserInstance

    await ws.accept()
    agent_url: str | None = None

    try:
        # ── 1. Registration handshake ─────────────────────────────────────
        data = await ws.receive_json()
        if data.get("type") != "register":
            await ws.close(code=1008, reason="Expected 'register' message first")
            return

        agent_url = data.get("agent_url", "").rstrip("/")
        mode = data.get("mode", "bridge")
        node_type = data.get("node_type", "chrome")
        label = data.get("label", "")
        runtimes = data.get("runtimes")
        runtime_capabilities = data.get("runtime_capabilities")
        profile_kind = data.get("profile_kind", "authenticated")

        if not agent_url.startswith("http"):
            await ws.close(code=1008, reason="agent_url must be an http/https URL")
            return
        if mode not in ("bridge", "cdp"):
            await ws.close(code=1008, reason="mode must be 'bridge' or 'cdp'")
            return
        if node_type not in ("docker", "shell"):
            await ws.close(code=1008, reason="node_type must be 'docker' or 'shell'")
            return
        if runtimes is not None and (
            not isinstance(runtimes, list) or not all(isinstance(r, str) for r in runtimes)
        ):
            await ws.close(code=1008, reason="runtimes must be a list of strings")
            return
        if runtime_capabilities is not None and (
            not isinstance(runtime_capabilities, dict)
            or any(
                not isinstance(runtime_name, str)
                or not isinstance(capabilities, list)
                or not all(isinstance(capability, str) for capability in capabilities)
                for runtime_name, capabilities in runtime_capabilities.items()
            )
            or set(runtime_capabilities) != set(runtimes or [])
        ):
            await ws.close(
                code=1008,
                reason="runtime_capabilities must map every advertised runtime to string names",
            )
            return
        if profile_kind not in ("anonymous", "authenticated"):
            await ws.close(
                code=1008,
                reason="profile_kind must be 'anonymous' or 'authenticated'",
            )
            return

        # ── 2. Upsert node + write event ──────────────────────────────────
        try:
            async with AsyncSessionLocal() as db:
                node = await _upsert_node(
                    db,
                    agent_url,
                    label,
                    "ws",
                    mode,
                    node_type=node_type,
                    runtimes=runtimes,
                    runtime_capabilities=runtime_capabilities,
                )
                await _write_event(
                    db,
                    node.id,
                    "online",
                    event_meta={
                        "mode": mode,
                        "node_type": node_type,
                        "protocol": "ws",
                        "profile_kind": profile_kind,
                        "runtime_capabilities": runtime_capabilities,
                    },
                )
                # BrowserInstance compat
                result = await db.execute(
                    select(BrowserInstance).where(BrowserInstance.endpoint == agent_url)
                )
                inst = result.scalar_one_or_none()
                if inst:
                    inst.mode = mode
                    inst.agent_url = agent_url
                    inst.agent_protocol = "ws"
                    inst.profile_kind = profile_kind
                    if label:
                        inst.label = label
                else:
                    inst = BrowserInstance(
                        endpoint=agent_url,
                        mode=mode,
                        agent_url=agent_url,
                        agent_protocol="ws",
                        label=label,
                        profile_kind=profile_kind,
                        profile_name=agent_url,
                    )
                    db.add(inst)
                await db.commit()
        except Exception as exc:
            logger.warning("WS node %s: DB upsert failed (non-fatal): %s", agent_url, exc)

        _pool_add(agent_url, mode, "ws", node_type, profile_kind)
        ws_agent_manager.register_connection(agent_url, ws)
        await ws.send_json({"type": "registered", "agent_url": agent_url})
        logger.info(
            "WS node registered: %s (node_type=%s mode=%s label=%r)",
            agent_url,
            node_type,
            mode,
            label,
        )

        # ── 3. Receive loop ───────────────────────────────────────────────
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            if msg_type == "result":
                ws_agent_manager.resolve_response(msg.get("request_id", ""), msg)
            elif msg_type == "agent_event":
                await ws_agent_manager.resolve_agent_event(
                    msg.get("request_id", ""), msg, source_ws=ws
                )
            elif msg_type == "agent_result":
                ws_agent_manager.resolve_agent_result(msg.get("request_id", ""), msg)
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})
            else:
                logger.debug("WS node %s: unknown message type %r", agent_url, msg_type)

    except WebSocketDisconnect:
        logger.info("WS node disconnected: %s", agent_url or "<unregistered>")
    except Exception as exc:
        logger.exception("WS node %s: error: %s", agent_url or "<unregistered>", exc)
    finally:
        if agent_url:
            ws_agent_manager.unregister_connection(agent_url)
            # Write offline event
            try:
                async with AsyncSessionLocal() as db:
                    from backend.models.edge_node import EdgeNode

                    result = await db.execute(select(EdgeNode).where(EdgeNode.url == agent_url))
                    node = result.scalar_one_or_none()
                    if node:
                        node.status = "offline"
                        await _write_event(db, node.id, "offline")
                        await db.commit()
            except Exception as exc:
                logger.warning("WS node %s: offline event write failed: %s", agent_url, exc)
