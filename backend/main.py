"""FastAPI application factory."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.v1 import v1_router
from backend.config import get_settings
from backend.database import run_migrations
from backend.security.fleet_auth import (
    FleetAuthMiddleware,
    enforce_bind_guard,
    resolve_uvicorn_host,
)


def _configure_logging() -> None:
    """Restore backend.* logging after uvicorn's dictConfig disables pre-existing loggers.

    uvicorn calls logging.config.dictConfig(LOGGING_CONFIG) with disable_existing_loggers=True,
    which disables all loggers that were created before the config ran (i.e. all loggers
    imported at module level). Also, alembic resets the root logger level to WARNING.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Re-enable all backend.* loggers that uvicorn's dictConfig disabled
    for name, lgr in logging.root.manager.loggerDict.items():
        if name.startswith("backend") and isinstance(lgr, logging.Logger):
            lgr.disabled = False
            lgr.setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger(__name__)

settings = get_settings()


def _read_chrome_endpoints() -> list[str]:
    """Read AGENT_POOL_ENDPOINTS from the .env file directly.

    `docker restart` reuses the env vars baked in at container creation time,
    so the env var value is stale after the chrome-pool API updates .env.
    Reading the file directly always gets the current value.

    Checks multiple candidate paths so both Docker (/app/.env) and native
    shell (project root .env) deployments work correctly.
    """
    import os
    candidates = [
        "/app/.env",
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    try:
        from dotenv import dotenv_values
        for path in candidates:
            env = dotenv_values(path)
            raw = (env.get("AGENT_POOL_ENDPOINTS") or "").strip()
            if raw:
                return [ep.strip() for ep in raw.split(",") if ep.strip()]
    except Exception:
        pass
    return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ADR-0005 bind guard: refuse to serve a non-localhost bind without an
    # API auth token. Raising here aborts uvicorn startup before a single
    # request is served. get_settings() is read fresh (not the module-level
    # snapshot) so env changes between import and startup are honored.
    enforce_bind_guard(resolve_uvicorn_host(), get_settings().api_auth_token)

    from backend.mcp_server import mcp_http_app

    mcp_lifespan = mcp_http_app.router.lifespan_context(mcp_http_app)
    await mcp_lifespan.__aenter__()
    await run_migrations()
    # Re-apply logging config: alembic resets root logger level to WARNING during migrations
    # and uvicorn's dictConfig disables pre-existing loggers
    _configure_logging()

    # Initialise Chrome browser pool.
    # Read AGENT_POOL_ENDPOINTS directly from the .env file so that updates
    # written by the chrome-pool API survive a plain `docker restart` — docker
    # restart reuses the env vars injected at container creation time, so the
    # pydantic-settings value (which comes from those env vars) would be stale.
    from backend import browser_pool
    from_env = _read_chrome_endpoints()
    endpoints = from_env or settings.cdp_endpoints
    browser_pool.init_pool(
        endpoints=endpoints,
        use_redis=settings.task_executor == "celery",
        redis_url=settings.redis_url,
    )
    await browser_pool.ensure_ready()

    # Sync browser instance modes and agent_urls from DB into pool memory.
    # When using the single fallback endpoint (no AGENT_POOL_ENDPOINTS configured),
    # apply opencli_pool_mode as its default unless the DB already has a record.
    from sqlalchemy import select

    from backend.browser_pool import LocalBrowserPool
    from backend.database import AsyncSessionLocal
    from backend.models.browser import BrowserInstance
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BrowserInstance))
        pool = browser_pool.get_pool()
        db_endpoints: set[str] = set()
        for inst in result.scalars().all():
            db_endpoints.add(inst.endpoint)
            if isinstance(pool, LocalBrowserPool):
                if inst.endpoint not in pool.endpoints:
                    # Only re-add registered agents (have agent_url); skip bare CDP records
                    if inst.agent_url:
                        pool.add_endpoint(inst.endpoint)
                    else:
                        continue
                pool.set_mode(inst.endpoint, inst.mode)
                pool.set_agent_url(inst.endpoint, inst.agent_url)
                pool.set_agent_protocol(inst.endpoint, inst.agent_protocol)
                pool.set_profile_kind(inst.endpoint, inst.profile_kind)
            elif inst.endpoint in pool.endpoints:
                pool.set_mode(inst.endpoint, inst.mode)
                pool.set_profile_kind(inst.endpoint, inst.profile_kind)

        # The single fallback endpoint (no AGENT_POOL_ENDPOINTS) defaults to cdp mode.
        # Agent registration writes a DB record which takes priority above.
        if not from_env and isinstance(pool, LocalBrowserPool):
            fallback = settings.opencli_cdp_endpoint
            if fallback in pool.endpoints and fallback not in db_endpoints:
                pool.set_mode(fallback, "cdp")

    # Mark stale pending/running tasks as failed (lost on previous restart)
    from sqlalchemy import update

    from backend.models.operations_agent import OperationsAgentRun
    from backend.models.task import CollectionTask
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(CollectionTask)
            .where(CollectionTask.status.in_(["pending", "running", "ai_processing"]))
            .values(status="failed", error_message="Task lost on server restart")
        )
        await session.execute(
            update(OperationsAgentRun)
            .where(OperationsAgentRun.status.in_(["queued", "running"]))
            .values(
                status="failed",
                error_message="Operations Agent run interrupted by server restart",
            )
        )
        await session.commit()
    logger.info("Recovered stale tasks on startup")

    # Managed acquisitions are durable submit-and-observe work. Unlike legacy
    # collection tasks, accepted/queued/running records are requeued rather than
    # declared lost when the API or worker restarts.
    from backend.acquisition.runner import (
        recover_acquisition_executions,
        sweep_acquisition_executions,
    )

    recovered_acquisitions = await recover_acquisition_executions()
    logger.info("Requeued %d managed acquisitions", len(recovered_acquisitions))
    acquisition_sweeper_stop = asyncio.Event()
    acquisition_sweeper = asyncio.create_task(
        sweep_acquisition_executions(stop=acquisition_sweeper_stop)
    )

    use_admin_scheduler = (
        settings.collection_orchestrator == "admin"
        and settings.task_executor == "local"
    )
    if use_admin_scheduler:
        from backend.scheduler import start_scheduler
        start_scheduler()
    elif settings.task_executor == "celery":
        # Bulk-sync redis with the current DB state at startup. redbeat's
        # entries are otherwise only kept current by the schedule CRUD
        # endpoints (backend.services.schedule_service._sync_redbeat) — this
        # catches drift from anything that changed the DB without going
        # through them (a migration, a direct DB edit, a fresh deploy against
        # an existing DB). Best-effort: a redis hiccup here must not block
        # the app from starting.
        try:
            from backend.worker.redbeat_sync import populate_all
            await populate_all()
        except Exception as exc:
            logger.warning("redbeat populate_all failed at startup: %s", exc)
    # Control Cycle (issue 03 / PR-Control-4, ADR-0007): a dedicated
    # background task, deliberately NOT hung on the collection scheduler
    # above — the controller and the plant it supervises must not share a
    # scheduling domain. Always started; the cycle itself is a no-op mutator
    # in Advisory Mode (the shipped default) and stays a no-op mutator
    # whenever the kill switch is engaged.
    from backend.control import cycle_task
    cycle_task.start()

    logger.info(
        "OpenCLI Admin started (env=%s, executor=%s, orchestrator=%s)",
        settings.app_env,
        settings.task_executor,
        settings.collection_orchestrator,
    )
    yield
    # Shutdown
    acquisition_sweeper_stop.set()
    await acquisition_sweeper
    await cycle_task.stop()
    if use_admin_scheduler:
        from backend.scheduler import stop_scheduler
        stop_scheduler()
    await mcp_lifespan.__aexit__(None, None, None)


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenCLI Admin",
        description=(
            "Agent-driven workflow and data collection platform. Authenticate protected REST "
            "and MCP calls with `Authorization: Bearer <API_AUTH_TOKEN>`. Agent workflow: "
            "inspect `/api/v1/workflows/capabilities`, draft with "
            "`/api/v1/workflows/demand-draft`, validate with `/api/v1/workflows/compile`, "
            "then review before publishing or running."
        ),
        version="0.4.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Fleet auth (ADR-0005): static bearer token on every /api route.
    # Registered BEFORE CORSMiddleware on purpose — Starlette treats the
    # last-added middleware as outermost, so CORS ends up wrapping auth:
    # 401 responses still carry CORS headers and preflight OPTIONS requests
    # (which browsers send without an Authorization header) are answered by
    # CORSMiddleware before ever reaching the token check.
    app.add_middleware(FleetAuthMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error"},
        )

    # Routes
    app.include_router(v1_router)
    default_openapi = app.openapi

    def openapi_schema() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = default_openapi()
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Operator-provisioned OpenCLI Admin API token.",
        }
        for path, path_item in schema.get("paths", {}).items():
            if not path.startswith("/api/"):
                continue
            for method, operation in path_item.items():
                if method.lower() in {"get", "post", "put", "patch", "delete"}:
                    operation.setdefault("security", [{"BearerAuth": []}])
        schema["x-opencli-agent"] = {
            "mcp": {
                "url": "/mcp",
                "transport": "streamable-http",
                "authentication": "BearerAuth",
            },
            "workflow": [
                "list_workflow_node_capabilities",
                "draft_workflow_from_intent",
                "preview_workflow_node_patch",
                "compile_workflow_draft",
                "run_published_workflow",
            ],
        }
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_schema

    @app.get("/", include_in_schema=False)
    async def discovery() -> dict:
        """Return the stable public entrypoints a human or Agent needs to begin."""

        return {
            "name": "OpenCLI Admin",
            "version": app.version,
            "interfaces": {
                "openapi": "/openapi.json",
                "docs": "/docs",
                "redoc": "/redoc",
                "mcp": {
                    "url": "/mcp",
                    "transport": "streamable-http",
                },
            },
            "authentication": {
                "type": "http",
                "scheme": "bearer",
                "header": "Authorization: Bearer <API_AUTH_TOKEN>",
                "provisioning": "operator-supplied",
            },
            "agentWorkflow": [
                "discover capabilities",
                "arrange a review-only node draft",
                "compile and preflight",
                "request operator review for effects",
                "run an immutable published workflow",
                "inspect trace and evidence",
            ],
        }

    @app.get("/health")
    async def health() -> dict:
        # Liveness only. This endpoint is exempt from FleetAuthMiddleware
        # (it sits outside the /api prefix; docker-compose's healthcheck
        # curls it with no credentials), so per closeout issue 04 it must
        # leak nothing: no version, no config flags. The deployment detail
        # (task_executor, collection_mode, ...) lives at the authenticated
        # GET /api/v1/system/config instead.
        return {"status": "ok"}

    # Keep MCP on the same deployment and auth boundary as the REST API.
    # Mounting at the root preserves the protocol's canonical exact /mcp path.
    from backend.mcp_server import mcp_http_app

    app.mount("/", mcp_http_app, name="mcp")

    return app


app = create_app()
