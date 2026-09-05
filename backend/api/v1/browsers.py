import asyncio
import base64
import logging
import secrets
import socket
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.browser_containers import docker_client, update_env_file
from backend.config import get_settings
from backend.database import get_db
from backend.schemas.browser import (
    BrowserBindingCreate,
    BrowserBindingRead,
    BrowserCapabilityInvocationRead,
    BrowserInstanceConfigUpdate,
    BrowserInstanceCreate,
    BrowserInstanceRead,
    BrowserRuntimeBundleCreate,
    BrowserRuntimeBundleRead,
    BrowserRuntimeDeploymentRead,
    CapabilityInvokeRequest,
    SlotRuntimeReport,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import browser_capability_service, browser_service

router = APIRouter(prefix="/browsers", tags=["browsers"])

runtime_router = APIRouter(tags=["browser-runtime"])


def _runtime_http_error(exc: browser_service.BrowserRuntimeError) -> HTTPException:
    status_code = (
        409
        if exc.code
        in {
            "bundle_version_exists",
            "immutable_bundle_version",
            "system_bundle_immutable",
            "bundle_in_use",
            "profile_in_use",
        }
        else 400
    )
    return HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)})


def _is_platform_admin(identity: RequestIdentity) -> bool:
    if identity.is_platform_admin:
        return True
    roles = identity.claims.get("roles") if identity.claims else None
    return isinstance(roles, (list, tuple)) and any(
        isinstance(role, str) and role == "platform-admin" for role in roles
    )


async def _get_restart_request_identity(request: Request) -> RequestIdentity:
    """Resolve an operator identity without treating fleet transport auth as one."""
    fleet_token = get_settings().api_auth_token
    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    bearer = bearer if scheme.lower() == "bearer" else ""
    if fleet_token and (
        not bearer or secrets.compare_digest(bearer.encode(), fleet_token.encode())
    ):
        raise HTTPException(status_code=403, detail="Platform administrator access required")

    return await get_request_identity(request)


def _decode_endpoint(endpoint_b64: str) -> str:
    try:
        padded = endpoint_b64 + "=" * (-len(endpoint_b64) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid endpoint encoding") from exc


async def _browser_instance_or_404(db: AsyncSession, instance_id: str):
    from backend.models.browser import BrowserInstance

    instance = await db.get(BrowserInstance, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Browser instance not found")
    return instance


logger = logging.getLogger(__name__)

# ── Bindings ──────────────────────────────────────────────────────────────────


@router.get("/bindings", response_model=ApiResponse[list[BrowserBindingRead]])
async def list_bindings(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    bindings = await browser_service.list_bindings(db)
    return ApiResponse.ok([BrowserBindingRead.model_validate(b) for b in bindings])


@router.post("/bindings", response_model=ApiResponse[BrowserBindingRead])
async def create_binding(
    body: BrowserBindingCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    existing = await browser_service.get_binding_by_site(db, body.site)
    if existing:
        raise HTTPException(status_code=409, detail=f"Site '{body.site}' is already bound")
    binding = await browser_service.create_binding(db, body.browser_endpoint, body.site, body.notes)
    await db.commit()
    return ApiResponse.ok(BrowserBindingRead.model_validate(binding))


@router.delete("/bindings/{binding_id}", response_model=ApiResponse[None])
async def delete_binding(binding_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    deleted = await browser_service.delete_binding(db, binding_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Binding not found")
    await db.commit()
    return ApiResponse.ok(None)


# ── Versioned browser runtime bundles ─────────────────────────────────────────


@router.get("/runtime-bundles", response_model=ApiResponse[list[BrowserRuntimeBundleRead]])
async def list_runtime_bundles(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    bundles = await browser_service.list_runtime_bundles(db)
    return ApiResponse.ok([BrowserRuntimeBundleRead.model_validate(bundle) for bundle in bundles])


@router.post("/runtime-bundles", response_model=ApiResponse[BrowserRuntimeBundleRead])
async def create_runtime_bundle(
    body: BrowserRuntimeBundleCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    try:
        bundle = await browser_service.create_runtime_bundle(db, body)
        await db.commit()
        await db.refresh(bundle)
    except browser_service.BrowserRuntimeError as exc:
        await db.rollback()
        raise _runtime_http_error(exc) from exc
    return ApiResponse.ok(BrowserRuntimeBundleRead.model_validate(bundle))


@router.put("/runtime-bundles/{bundle_id}", response_model=ApiResponse[BrowserRuntimeBundleRead])
async def update_runtime_bundle(
    bundle_id: str,
    body: BrowserRuntimeBundleCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    bundle = await browser_service.get_runtime_bundle(db, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Runtime bundle not found")
    try:
        bundle = await browser_service.update_runtime_bundle(db, bundle, body)
        await db.commit()
        await db.refresh(bundle)
    except browser_service.BrowserRuntimeError as exc:
        await db.rollback()
        raise _runtime_http_error(exc) from exc
    return ApiResponse.ok(BrowserRuntimeBundleRead.model_validate(bundle))


@router.delete("/runtime-bundles/{bundle_id}", response_model=ApiResponse[None])
async def delete_runtime_bundle(bundle_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    bundle = await browser_service.get_runtime_bundle(db, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Runtime bundle not found")
    try:
        await browser_service.delete_runtime_bundle(db, bundle)
        await db.commit()
    except browser_service.BrowserRuntimeError as exc:
        await db.rollback()
        raise _runtime_http_error(exc) from exc
    return ApiResponse.ok(None)


@router.get("/instances", response_model=ApiResponse[list[BrowserInstanceRead]])
async def list_runtime_instances(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    instances = await browser_service.list_browser_instances(db)
    return ApiResponse.ok([BrowserInstanceRead.model_validate(item) for item in instances])


@router.post("/instances", response_model=ApiResponse[BrowserInstanceRead])
async def create_runtime_instance(
    body: BrowserInstanceCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    try:
        instance = await browser_service.create_browser_instance(db, body)
        await db.commit()
        await db.refresh(instance)
    except browser_service.BrowserRuntimeError as exc:
        await db.rollback()
        raise _runtime_http_error(exc) from exc
    return ApiResponse.ok(BrowserInstanceRead.model_validate(instance))


@router.post(
    "/instances/{instance_id}/runtime-report",
    response_model=ApiResponse[BrowserRuntimeDeploymentRead],
)
async def report_runtime_deployment(
    instance_id: str,
    body: SlotRuntimeReport,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    instance = await _browser_instance_or_404(db, instance_id)
    deployment = await browser_service.report_runtime_deployment(db, instance, body)
    await db.commit()
    await db.refresh(deployment)
    try:
        from backend.browser_pool import LocalBrowserPool, get_pool

        pool = get_pool()
        if isinstance(pool, LocalBrowserPool):
            pool.set_runtime_status(instance.endpoint, deployment.state)
    except RuntimeError:
        pass
    return ApiResponse.ok(BrowserRuntimeDeploymentRead.model_validate(deployment))


@router.get(
    "/instances/{instance_id}/runtime",
    response_model=ApiResponse[BrowserRuntimeDeploymentRead],
)
async def get_runtime_deployment(
    instance_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    await _browser_instance_or_404(db, instance_id)
    deployment = await browser_service.get_runtime_deployment(db, instance_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Runtime deployment has not reported yet")
    return ApiResponse.ok(BrowserRuntimeDeploymentRead.model_validate(deployment))


@runtime_router.post(
    "/browser-sessions/{instance_id}/capabilities/{capability}/invoke",
    response_model=ApiResponse[BrowserCapabilityInvocationRead],
)
async def invoke_runtime_capability(
    instance_id: str,
    capability: str,
    body: CapabilityInvokeRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    instance = await _browser_instance_or_404(db, instance_id)
    try:
        claimed_roles = identity.claims.get("roles", []) if identity.claims else []
        gate_authorized = identity.is_platform_admin or "platform-admin" in claimed_roles
        invocation = await browser_capability_service.invoke_capability(
            db,
            instance,
            capability,
            body.args,
            body.gate,
            gate_authorized=gate_authorized,
        )
        await db.commit()
        await db.refresh(invocation)
    except browser_service.BrowserRuntimeError as exc:
        await db.commit()
        raise _runtime_http_error(exc) from exc
    return ApiResponse.ok(BrowserCapabilityInvocationRead.model_validate(invocation))


# ── Chrome pool management ────────────────────────────────────────────────────


class CdpEndpointRequest(BaseModel):
    url: str
    mode: str = "cdp"


@router.post("/cdp-endpoint", response_model=ApiResponse[dict])
async def add_cdp_endpoint(
    body: CdpEndpointRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Register an arbitrary CDP endpoint (e.g. http://localhost:9222) into the pool.

    Use this when Chrome is already running locally with --remote-debugging-port
    or when pointing to any accessible CDP URL.
    """
    from backend.browser_pool import LocalBrowserPool, get_pool
    from backend.models.browser import BrowserInstance

    url = body.url.rstrip("/")
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url must start with http/https")
    mode = body.mode if body.mode in ("bridge", "cdp") else "cdp"

    pool = get_pool()
    if isinstance(pool, LocalBrowserPool):
        if url not in pool.endpoints:
            pool.add_endpoint(url)
        pool.set_mode(url, mode)

    result = await db.execute(select(BrowserInstance).where(BrowserInstance.endpoint == url))
    inst = result.scalar_one_or_none()
    if inst:
        inst.mode = mode
    else:
        inst = BrowserInstance(endpoint=url, mode=mode, profile_name=url)
        db.add(inst)
    await db.commit()

    return ApiResponse(success=True, data={"endpoint": url, "mode": mode})


class AgentRegisterRequest(BaseModel):
    agent_url: str  # e.g. http://192.168.1.100:19823
    mode: str = "bridge"  # bridge | cdp
    label: str = ""
    agent_protocol: str = "http"  # http | ws


@router.post("/agents/register", response_model=ApiResponse[BrowserInstanceRead])
async def register_agent(
    body: AgentRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Agent self-registration: agent POSTs its own URL, center adds it to the pool.

    The agent_url is used as the pool endpoint (logical key for routing).
    Idempotent: calling again with the same agent_url updates mode/label.
    """
    from backend.browser_pool import LocalBrowserPool, get_pool
    from backend.models.browser import BrowserInstance

    agent_url = body.agent_url.rstrip("/")
    if not agent_url.startswith("http"):
        raise HTTPException(status_code=400, detail="agent_url must be an http/https URL")
    if body.mode not in ("bridge", "cdp"):
        raise HTTPException(status_code=400, detail="mode must be 'bridge' or 'cdp'")
    if body.agent_protocol not in ("http", "ws"):
        raise HTTPException(status_code=400, detail="agent_protocol must be 'http' or 'ws'")

    pool = get_pool()

    # Add to pool if not already present (agent_url is the pool endpoint key)
    if isinstance(pool, LocalBrowserPool):
        if agent_url not in pool.endpoints:
            pool.add_endpoint(agent_url)
        pool.set_mode(agent_url, body.mode)
        pool.set_agent_url(agent_url, agent_url)
        pool.set_agent_protocol(agent_url, body.agent_protocol)

    # Upsert in DB
    result = await db.execute(select(BrowserInstance).where(BrowserInstance.endpoint == agent_url))
    inst = result.scalar_one_or_none()
    if inst:
        inst.mode = body.mode
        inst.agent_url = agent_url
        inst.agent_protocol = body.agent_protocol
        if body.label:
            inst.label = body.label
    else:
        inst = BrowserInstance(
            endpoint=agent_url,
            mode=body.mode,
            agent_url=agent_url,
            agent_protocol=body.agent_protocol,
            label=body.label,
            profile_name=agent_url,
        )
        db.add(inst)
    await db.commit()
    await db.refresh(inst)

    # Also upsert EdgeNode so the Nodes UI can see HTTP-registered agents
    try:
        from datetime import datetime

        from backend.models.edge_node import EdgeNode, EdgeNodeEvent

        _now = datetime.now(UTC)
        result2 = await db.execute(select(EdgeNode).where(EdgeNode.url == agent_url))
        node = result2.scalar_one_or_none()
        if node:
            node.status = "online"
            node.last_seen_at = _now
            node.protocol = "http"
            node.mode = body.mode
            if body.label:
                node.label = body.label
        else:
            node = EdgeNode(
                url=agent_url,
                label=body.label or agent_url,
                protocol="http",
                mode=body.mode,
                status="online",
                last_seen_at=_now,
            )
            db.add(node)
            await db.flush()
            db.add(EdgeNodeEvent(node_id=node.id, event="registered"))
        await db.commit()
    except Exception as exc:
        logger.warning("Agent %s: EdgeNode upsert failed (non-fatal): %s", agent_url, exc)

    logger.info("Agent registered: %s (mode=%s)", agent_url, body.mode)
    return ApiResponse.ok(BrowserInstanceRead.model_validate(inst))


@router.patch("/instances/{endpoint_b64}", response_model=ApiResponse[BrowserInstanceRead])
async def update_instance_config(
    endpoint_b64: str,
    body: BrowserInstanceConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Update a slot's desired connection, profile, and immutable bundle selection."""

    from backend.browser_pool import LocalBrowserPool, get_pool
    from backend.models.browser import BrowserInstance

    endpoint = _decode_endpoint(endpoint_b64)

    pool = get_pool()
    if endpoint not in pool.endpoints:
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint!r} not in pool")

    inst = (
        await db.execute(select(BrowserInstance).where(BrowserInstance.endpoint == endpoint))
    ).scalar_one_or_none()
    if inst is None:
        inst = BrowserInstance(
            endpoint=endpoint,
            mode=pool.get_mode(endpoint),
            label="",
            profile_name=endpoint,
        )
        db.add(inst)
        await db.flush()
    previous_runtime_config = (
        inst.runtime_bundle_id,
        inst.profile_name,
        inst.resource_class,
        inst.startup_pages,
        inst.network_policy,
    )
    try:
        inst = await browser_service.update_browser_instance(db, inst, body)
        await db.commit()
        await db.refresh(inst)
    except browser_service.BrowserRuntimeError as exc:
        await db.rollback()
        raise _runtime_http_error(exc) from exc

    pool.set_mode(endpoint, inst.mode)
    pool.set_profile_kind(endpoint, inst.profile_kind)
    current_runtime_config = (
        inst.runtime_bundle_id,
        inst.profile_name,
        inst.resource_class,
        inst.startup_pages,
        inst.network_policy,
    )
    if previous_runtime_config != current_runtime_config:
        pool.set_runtime_status(endpoint, "RESTART_REQUIRED")
    if isinstance(pool, LocalBrowserPool):
        pool.set_agent_url(endpoint, inst.agent_url)
        pool.set_agent_protocol(endpoint, inst.agent_protocol)
        pool.set_profile_name(endpoint, inst.profile_name or endpoint)
    return ApiResponse.ok(BrowserInstanceRead.model_validate(inst))


@router.delete("/instances/{endpoint_b64}", response_model=ApiResponse[dict])
async def remove_instance(
    endpoint_b64: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Remove any pool entry by endpoint URL (base64-encoded). Does NOT touch Docker containers."""

    from backend.browser_pool import LocalBrowserPool, get_pool
    from backend.models.browser import BrowserInstance

    endpoint = _decode_endpoint(endpoint_b64)

    pool = get_pool()
    if endpoint not in pool.endpoints:
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint!r} not in pool")

    if isinstance(pool, LocalBrowserPool):
        pool.remove_endpoint(endpoint)

    result = await db.execute(select(BrowserInstance).where(BrowserInstance.endpoint == endpoint))
    inst = result.scalar_one_or_none()
    if inst:
        await db.delete(inst)
        await db.commit()

    logger.info("Removed pool entry: %s", endpoint)
    return ApiResponse.ok({"removed": endpoint, "total": len(pool.endpoints)})


@router.delete("/chrome-instances/{n}", response_model=ApiResponse[dict])
async def remove_chrome_instance(n: int) -> ApiResponse:
    """Stop and remove agent-N (N >= 2). Instance 1 is managed by docker-compose."""
    if n < 2:
        raise HTTPException(status_code=400, detail="Instance 1 is managed by docker-compose")

    from backend.browser_pool import LocalBrowserPool, get_pool

    pool = get_pool()
    name = f"agent-{n}"
    endpoint = f"http://{name}:19222"

    client = docker_client()
    try:
        container = client.containers.get(name)
        container.remove(force=True)
        logger.info("agent-pool: removed container %s", name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Container {name} not found: {exc}")

    if isinstance(pool, LocalBrowserPool):
        pool.remove_endpoint(endpoint)

    all_endpoints = ",".join(ep for ep in pool.endpoints if ep != endpoint)
    try:
        update_env_file("AGENT_POOL_ENDPOINTS", all_endpoints)
    except Exception as exc:
        logger.warning("agent-pool: could not update .env: %s", exc)

    return ApiResponse.ok({"removed": name, "total": len(pool.endpoints)})


@router.get("/agents/ws-status", response_model=ApiResponse[dict])
async def ws_agents_status() -> ApiResponse:
    """Return the list of agent URLs that currently have an active WS connection."""
    from backend import ws_agent_manager

    connected = ws_agent_manager.list_connected()
    return ApiResponse.ok({"connected": connected})


@router.websocket("/agents/ws")
async def agent_ws_endpoint(ws: WebSocket) -> None:
    """Reverse WebSocket channel for NAT/unreachable edge agents.

    The agent initiates this connection, sends a 'register' handshake, then
    listens for 'collect' tasks from the center and sends back 'result' messages.
    The center keeps the connection alive and uses it to dispatch collect requests.
    """
    from backend import ws_agent_manager
    from backend.browser_pool import LocalBrowserPool, get_pool
    from backend.models.browser import BrowserInstance

    await ws.accept()
    agent_url: str | None = None

    try:
        # ── 1. Registration handshake ─────────────────────────────────────────
        data = await ws.receive_json()
        if data.get("type") != "register":
            await ws.close(code=1008, reason="Expected 'register' message first")
            return

        agent_url = data.get("agent_url", "").rstrip("/")
        mode = data.get("mode", "bridge")
        label = data.get("label", "")

        if not agent_url.startswith("http"):
            await ws.close(code=1008, reason="agent_url must be an http/https URL")
            return
        if mode not in ("bridge", "cdp"):
            await ws.close(code=1008, reason="mode must be 'bridge' or 'cdp'")
            return

        # ── 2. Add/update in pool ─────────────────────────────────────────────
        pool = get_pool()
        if isinstance(pool, LocalBrowserPool):
            if agent_url not in pool.endpoints:
                pool.add_endpoint(agent_url)
            pool.set_mode(agent_url, mode)
            pool.set_agent_url(agent_url, agent_url)
            pool.set_agent_protocol(agent_url, "ws")

        # Upsert in DB (fire-and-forget; don't block the WS receive loop)
        try:
            from backend.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(BrowserInstance).where(BrowserInstance.endpoint == agent_url)
                )
                inst = result.scalar_one_or_none()
                if inst:
                    inst.mode = mode
                    inst.agent_url = agent_url
                    inst.agent_protocol = "ws"
                    if label:
                        inst.label = label
                else:
                    inst = BrowserInstance(
                        endpoint=agent_url,
                        mode=mode,
                        agent_url=agent_url,
                        agent_protocol="ws",
                        label=label,
                        profile_name=agent_url,
                    )
                    db.add(inst)
                await db.commit()
        except Exception as exc:
            logger.warning("WS agent %s: DB upsert failed (non-fatal): %s", agent_url, exc)

        # Also upsert into EdgeNode so the Nodes UI can see this agent
        try:
            from datetime import datetime

            from backend.database import AsyncSessionLocal
            from backend.models.edge_node import EdgeNode, EdgeNodeEvent

            _now = datetime.now(UTC)
            async with AsyncSessionLocal() as _db:
                from sqlalchemy import select as _select

                _res = await _db.execute(_select(EdgeNode).where(EdgeNode.url == agent_url))
                _node = _res.scalar_one_or_none()
                if _node:
                    _node.status = "online"
                    _node.last_seen_at = _now
                    _node.protocol = "ws"
                    _node.mode = mode
                    if label:
                        _node.label = label
                else:
                    _node = EdgeNode(
                        url=agent_url,
                        label=label or agent_url,
                        protocol="ws",
                        mode=mode,
                        status="online",
                        last_seen_at=_now,
                    )
                    _db.add(_node)
                    await _db.flush()
                    _db.add(EdgeNodeEvent(node_id=_node.id, event="registered"))
                await _db.commit()
        except Exception as _exc:
            logger.warning("WS agent %s: EdgeNode upsert failed (non-fatal): %s", agent_url, _exc)

        ws_agent_manager.register_connection(agent_url, ws)
        await ws.send_json({"type": "registered", "agent_url": agent_url})
        logger.info("WS agent registered: %s (mode=%s label=%r)", agent_url, mode, label)

        # ── 3. Receive loop: results + pings ──────────────────────────────────
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
                logger.debug("WS agent %s: unknown message type %r", agent_url, msg_type)

    except WebSocketDisconnect:
        logger.info("WS agent disconnected: %s", agent_url or "<unregistered>")
    except Exception as exc:
        logger.exception("WS agent %s: unexpected error: %s", agent_url or "<unregistered>", exc)
    finally:
        if agent_url:
            ws_agent_manager.unregister_connection(agent_url)
            try:
                from datetime import datetime

                from sqlalchemy import select as _select

                from backend.database import AsyncSessionLocal
                from backend.models.edge_node import EdgeNode, EdgeNodeEvent

                async with AsyncSessionLocal() as _db:
                    _res = await _db.execute(_select(EdgeNode).where(EdgeNode.url == agent_url))
                    _node = _res.scalar_one_or_none()
                    if _node:
                        _node.status = "offline"
                        _node.last_seen_at = datetime.now(UTC)
                        _db.add(EdgeNodeEvent(node_id=_node.id, event="offline"))
                        await _db.commit()
            except Exception as _exc:
                logger.warning("WS agent %s: EdgeNode offline update failed: %s", agent_url, _exc)


@router.post("/restart-api", response_model=ApiResponse[dict])
async def restart_api(
    request: Request,
    identity: RequestIdentity = Depends(_get_restart_request_identity),
) -> ApiResponse:
    """Restart the current API container without recreating it or reloading host env files."""
    if not _is_platform_admin(identity):
        raise HTTPException(status_code=403, detail="Platform administrator access required")

    container_id = socket.gethostname()
    client = docker_client()
    try:
        container = client.containers.get(container_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not find own container: {exc}")

    logger.info("API restart requested — restarting container %s in 1s", container_id)
    # Delay restart so the HTTP response can be sent first
    asyncio.get_event_loop().call_later(1.0, container.restart)
    return ApiResponse.ok(
        {
            "restarting": True,
            "container": container_id,
            "instance_id": request.app.state.api_instance_id,
        }
    )
