import asyncio
import hashlib
import json
import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.browser import SlotRuntimeReport
from backend.schemas.common import ApiResponse
from backend.services import browser_service

router = APIRouter(prefix="/browsers", tags=["browsers"])
logger = logging.getLogger(__name__)


def docker_client():
    try:
        import docker  # type: ignore[import]

        return docker.from_env()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Docker socket not available: {exc}")


def _project_name() -> str:
    return os.environ.get("COMPOSE_PROJECT_NAME", "opencli-admin")


def _resolve_env_path() -> str:
    if explicit := os.environ.get("ENV_FILE_PATH"):
        return explicit
    for candidate in [
        "/app/.env",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")


def update_env_file(key: str, value: str) -> None:
    """Update or append KEY=value in the .env file."""
    path = _resolve_env_path()
    try:
        with open(path) as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    new_line = f"{key}={value}"
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{new_line}\n"
    with open(path, "w") as f:
        f.write(content)


@router.post("/chrome-instances", response_model=ApiResponse[dict])
async def add_chrome_instance(
    count: int = 1,
    mode: str = "bridge",
    agent_url: str = "",
    agent_protocol: str = "",
    runtime_bundle_id: str = "",
    profile_name: str = "",
    startup_pages: str = "",
    resource_class: str = "standard",
    network_policy: str = "",
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Start new Chrome containers with immutable runtime configuration."""
    from backend.browser_pool import LocalBrowserPool, get_pool
    from backend.models.browser import BrowserInstance

    if count < 1 or count > 10:
        raise HTTPException(status_code=400, detail="count must be between 1 and 10")
    if mode not in ("bridge", "cdp"):
        raise HTTPException(status_code=400, detail="mode must be 'bridge' or 'cdp'")
    clean_agent_url = agent_url.strip() or None
    clean_agent_protocol = agent_protocol.strip() or None
    if clean_agent_protocol and clean_agent_protocol not in ("http", "ws"):
        raise HTTPException(status_code=400, detail="agent_protocol must be 'http' or 'ws'")
    clean_profile_name = profile_name.strip() or None
    if count > 1 and clean_profile_name:
        raise HTTPException(
            status_code=400,
            detail="profile_name can only be used when creating one instance",
        )

    selected_bundle = None
    if runtime_bundle_id:
        selected_bundle = await browser_service.get_runtime_bundle(db, runtime_bundle_id)
        if selected_bundle is None:
            raise HTTPException(status_code=404, detail="Runtime bundle not found")

    parsed_startup_pages: list[str] = []
    if startup_pages:
        try:
            candidate_pages = json.loads(startup_pages)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="startup_pages must be JSON") from exc
        if (
            not isinstance(candidate_pages, list)
            or len(candidate_pages) > 10
            or any(
                not isinstance(page, str) or not page.startswith(("http://", "https://"))
                for page in candidate_pages
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="startup_pages must contain at most 10 HTTP(S) URLs",
            )
        parsed_startup_pages = candidate_pages

    resource_limits = {
        "standard": {"mem_limit": "1g", "cpu_period": 100_000, "cpu_quota": 100_000},
        "medium": {"mem_limit": "2g", "cpu_period": 100_000, "cpu_quota": 200_000},
        "large": {"mem_limit": "4g", "cpu_period": 100_000, "cpu_quota": 400_000},
    }
    if resource_class not in resource_limits:
        raise HTTPException(
            status_code=400,
            detail="resource_class must be standard, medium, or large",
        )
    try:
        parsed_network_policy = json.loads(network_policy) if network_policy else {"mode": "direct"}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="network_policy must be JSON") from exc
    if not isinstance(parsed_network_policy, dict) or parsed_network_policy.get("mode") not in {
        "direct",
        "restricted",
    }:
        raise HTTPException(
            status_code=400,
            detail="network_policy.mode must be direct or restricted",
        )

    pool = get_pool()
    project = _project_name()
    from backend.config import get_settings

    novnc_base = get_settings().novnc_base_port
    network = f"{project}_default"
    image = os.environ.get("CHROME_IMAGE", f"{project}-chrome")
    client = docker_client()
    if selected_bundle is not None:
        packaged_manifest = (
            f"/opt/browser-runtime-bundles/{selected_bundle.name}/"
            f"{selected_bundle.version}/manifest.json"
        )
        try:
            client.containers.run(
                image,
                ["test", "-f", packaged_manifest],
                entrypoint="",
                remove=True,
                network_disabled=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Runtime bundle {selected_bundle.name}@{selected_bundle.version} "
                    "is not packaged in the selected Chrome image"
                ),
            ) from exc

    created: list[dict] = []
    for _ in range(count):
        # A removed slot can leave holes; never select an existing account slot
        # merely because the pool's size decreased.
        known_endpoints = set(pool.endpoints) | set(
            await db.scalars(select(BrowserInstance.endpoint))
        )
        # Other previews or a previous failed request may have created containers
        # not present in this database. Their names/ports are still occupied.
        known_endpoints.update(
            f"http://{container.name}:19222"
            for container in client.containers.list(all=True, filters={"label": "agent.pool.extra=true"})
            if re.fullmatch(r"agent-[1-9]\d*", container.name)
        )
        indices = [
            int(match[1] or 1)
            for endpoint in known_endpoints
            if (match := re.fullmatch(r"https?://agent(?:-([1-9]\d*))?(?::\d+)?/?", endpoint))
        ]
        instance_index = max(indices, default=1) + 1
        name = f"agent-{instance_index}"
        novnc_port = novnc_base + instance_index - 1
        resolved_profile_name = clean_profile_name or name
        profile_key = hashlib.sha256(resolved_profile_name.encode("utf-8")).hexdigest()[:16]
        volume = f"{project}_browser_profile_{profile_key}"
        new_endpoint = f"http://{name}:19222"
        requested_bundle_id = runtime_bundle_id or None
        inst = (
            await db.execute(
                select(BrowserInstance).where(BrowserInstance.endpoint == new_endpoint)
            )
        ).scalar_one_or_none()
        if inst is not None and (
            inst.profile_name != resolved_profile_name
            or inst.runtime_bundle_id != requested_bundle_id
            or inst.resource_class != resource_class
            or inst.startup_pages != parsed_startup_pages
            or inst.network_policy != parsed_network_policy
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Existing container must be removed before changing its Profile, "
                    "Runtime Bundle, resource class, startup pages, or network policy"
                ),
            )
        profile_owner = (
            await db.execute(
                select(BrowserInstance).where(
                    BrowserInstance.profile_name == resolved_profile_name,
                    BrowserInstance.endpoint != new_endpoint,
                )
            )
        ).scalar_one_or_none()
        if profile_owner is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Profile {resolved_profile_name!r} is already assigned",
            )

        if inst is None:
            inst = BrowserInstance(
                endpoint=new_endpoint,
                mode=mode,
                agent_url=clean_agent_url,
                agent_protocol=clean_agent_protocol,
                label="",
                profile_name=resolved_profile_name,
                runtime_bundle_id=requested_bundle_id,
                resource_class=resource_class,
                startup_pages=parsed_startup_pages,
                network_policy=parsed_network_policy,
            )
            db.add(inst)
        else:
            inst.mode = mode
            inst.agent_url = clean_agent_url
            inst.agent_protocol = clean_agent_protocol
        await db.flush()

        container_environment = {
            "CHROME_HOSTNAME": name,
            "BROWSER_RUNTIME_BUNDLE_ROOT": "/opt/browser-runtime-bundles",
            "BROWSER_NETWORK_POLICY": json.dumps(parsed_network_policy, separators=(",", ":")),
        }
        if selected_bundle is not None:
            container_environment["BROWSER_RUNTIME_BUNDLE_MANIFEST"] = (
                f"/opt/browser-runtime-bundles/{selected_bundle.name}/"
                f"{selected_bundle.version}/manifest.json"
            )
        if parsed_startup_pages:
            container_environment["BROWSER_STARTUP_PAGES"] = json.dumps(
                parsed_startup_pages, separators=(",", ":")
            )
        runtime_config_hash = hashlib.sha256(
            json.dumps(
                {
                    "profile_name": resolved_profile_name,
                    "runtime_bundle_id": requested_bundle_id,
                    "resource_class": resource_class,
                    "startup_pages": parsed_startup_pages,
                    "network_policy": parsed_network_policy,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        try:
            runtime_container = client.containers.get(name)
            if runtime_container.labels.get("agent.pool.runtime-config") != runtime_config_hash:
                raise HTTPException(
                    status_code=409,
                    detail=f"Container {name} does not match the persisted runtime configuration",
                )
            if runtime_container.status != "running":
                runtime_container.start()
                logger.info("agent-pool: restarted existing container %s", name)
            else:
                logger.info("agent-pool: %s already running", name)
        except HTTPException:
            raise
        except Exception:
            try:
                runtime_container = client.containers.run(
                    image,
                    detach=True,
                    name=name,
                    network=network,
                    labels={
                        "agent.pool.extra": "true",
                        "agent.pool.index": str(instance_index),
                        "agent.pool.runtime-config": runtime_config_hash,
                    },
                    environment=container_environment,
                    ports={"6080/tcp": ("127.0.0.1", novnc_port)},
                    volumes={
                        volume: {
                            "bind": "/home/chrome/.config/chromium",
                            "mode": "rw",
                        }
                    },
                    restart_policy={"Name": "unless-stopped"},
                    **resource_limits[resource_class],
                )
                logger.info(
                    "agent-pool: started new container %s on noVNC :%d",
                    name,
                    novnc_port,
                )
            except Exception as exc:
                logger.exception("agent-pool: failed to start %s", name)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        if isinstance(pool, LocalBrowserPool) and new_endpoint not in pool.endpoints:
            pool.add_endpoint(new_endpoint)
        pool.set_mode(new_endpoint, mode)
        if isinstance(pool, LocalBrowserPool):
            pool.set_agent_url(new_endpoint, clean_agent_url)
            pool.set_agent_protocol(new_endpoint, clean_agent_protocol)
            pool.set_profile_name(new_endpoint, resolved_profile_name)
            pool.set_runtime_status(new_endpoint, "DEGRADED" if requested_bundle_id else "LEGACY")

        if selected_bundle is not None:
            for _ in range(120):
                result = runtime_container.exec_run(["cat", "/tmp/browser-runtime-report.json"])
                if result.exit_code == 0:
                    report = SlotRuntimeReport.model_validate_json(result.output)
                    deployment = await browser_service.report_runtime_deployment(db, inst, report)
                    if isinstance(pool, LocalBrowserPool):
                        pool.set_runtime_status(new_endpoint, deployment.state)
                    break
                await asyncio.sleep(0.25)

        created.append(
            {
                "endpoint": new_endpoint,
                "novnc_port": novnc_port,
                "runtime_status": (
                    pool.runtime_status(new_endpoint)
                    if isinstance(pool, LocalBrowserPool)
                    else "DEGRADED"
                ),
            }
        )

    await db.commit()
    try:
        update_env_file("AGENT_POOL_ENDPOINTS", ",".join(pool.endpoints))
    except Exception as exc:
        logger.warning("agent-pool: could not update .env: %s", exc)
    return ApiResponse.ok({"created": created, "total": len(pool.endpoints)})
