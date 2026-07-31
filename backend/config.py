from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "opencli-admin"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    # Fernet key used to encrypt provider credentials at rest. Keep this
    # stable after providers have been saved, or their stored API keys cannot
    # be decrypted on the next process start.
    credential_encryption_key: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./opencli_admin.db"

    # Task execution mode: "local" (in-process asyncio) or "celery" (distributed)
    task_executor: Literal["local", "celery"] = "local"

    # AUDIT C6: process-wide cap on concurrently-RUNNING pipeline executions in
    # the local (in-process asyncio) executor — independent of the per-domain
    # cap in pipeline/domain_limiter.py. Bounds how many schedules firing on
    # the same tick plus manual/webhook triggers can drive Chrome/opencli
    # subprocesses at once on the one event loop. Only meaningful when
    # task_executor="local" (celery fans out across worker processes
    # instead). Env: LOCAL_MAX_CONCURRENT_PIPELINES.
    local_max_concurrent_pipelines: int = 8

    # Collection orchestrator:
    # admin — API内置 scheduler.py / Celery Beat 驱动定时采集（默认）
    # iii   — III engine + schedule-bootstrap 驱动 cron；API 仅保留 UI/手动任务
    collection_orchestrator: Literal["admin", "iii"] = "admin"

    # Redis / Celery — only required when task_executor="celery"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # API Security
    # (api_key_enabled/api_key predate fleet auth and were never enforced by
    # any dependency — kept only so existing .env files don't break parsing.
    # api_auth_token below is the one that counts.)
    api_key_enabled: bool = False
    api_key: str = ""
    # Fleet auth (ADR-0005, closeout issue 04): single static bearer token
    # required on every /api and /mcp route once set (backend/security/fleet_auth.py).
    # Empty (default) = auth disabled — dev posture, which the startup bind
    # guard only allows on a localhost bind. Env: API_AUTH_TOKEN.
    api_auth_token: str = ""

    # CLI channel binary allowlist (ADR-0005, audit P0-4). The cli channel is
    # an arbitrary-binary-execution surface, so it only runs binaries the
    # operator explicitly listed here. Comma-separated binary paths/names,
    # e.g. "/usr/bin/mycli,C:\\tools\\other.exe". Empty (default) = deny all.
    # Deliberately orthogonal to API auth: a stolen token must not grant
    # arbitrary code execution.
    cli_channel_allowed_binaries: str = ""

    @property
    def cli_allowed_binaries(self) -> list[str]:
        return [b.strip() for b in self.cli_channel_allowed_binaries.split(",") if b.strip()]

    # MiniFlow runtime confinement. MiniFlow workflow files are imported and
    # executed (spec.loader.exec_module) on the edge host, so a stolen fleet
    # token must not be able to run arbitrary Python by pointing the runtime at
    # any path. Only workflow/cwd/audit paths under this root are loadable.
    # Empty (default) = deny all (fail closed), mirroring cli_channel_allowed_binaries.
    miniflow_workflow_root: str = ""

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Collection mode:
    # local — default; API directly drives Chrome containers in the same Docker network
    # agent — distributed edge nodes; collection is dispatched to remote agent servers
    #         each agent runs opencli locally and returns results via HTTP or WS
    collection_mode: Literal["local", "agent"] = "local"

    # Docker image tag used in agent install scripts / node wizard.
    # Set IMAGE_TAG env var (or bake it in at build time) to match the deployed version.
    image_tag: str = "latest"

    # Public-facing URL of this deployment (used in install scripts and invite links).
    # Set this to the URL your remote agents will use to reach the center API.
    # e.g. http://192.168.1.1:8031  or  https://admin.example.com
    # If empty, the system tries to derive it from request headers (may give internal URL
    # when behind a reverse proxy with changeOrigin=true).
    public_url: str = ""

    # Fleet network bootstrap used by the generated edge-agent installer.
    # The collection layer only needs reachability between CENTRAL_API_URL and
    # AGENT_ADVERTISE_URL. Providers are bootstrap hints for that reachability,
    # not part of collection routing semantics. API_AUTH_TOKEN still gates
    # every /api route regardless of the network provider.
    fleet_network_provider: Literal["lan", "netbird", "wireguard", "ssh", "custom"] = "lan"
    netbird_mode: Literal["off", "host", "docker"] = "off"
    netbird_setup_key: str = ""
    netbird_management_url: str = ""
    netbird_image_tag: str = "latest"

    # Agent pool: comma-separated agent/CDP endpoint URLs.
    # Each entry is a Chrome agent node (local or remote).
    # Single-instance fallback when agent_pool_endpoints is empty.
    opencli_cdp_endpoint: str = "http://localhost:9222"
    # Multi-agent pool: overrides opencli_cdp_endpoint when set.
    # e.g. http://agent-1:19222,http://agent-2:19222,http://192.168.1.100:19222
    agent_pool_endpoints: str = ""
    # noVNC base port for the first agent instance (agent-1). Additional
    # instances use base+1, base+2, …  Matches docker-compose NOVNC_PORT.
    novnc_base_port: int = 6080

    @property
    def cdp_endpoints(self) -> list[str]:
        if self.agent_pool_endpoints.strip():
            return [ep.strip() for ep in self.agent_pool_endpoints.split(",") if ep.strip()]
        return [self.opencli_cdp_endpoint]

    # Collect timeouts (seconds)
    # opencli subprocess execution timeout (local mode and agent-side)
    opencli_timeout: int = 120
    # Pinned Graphon compatibility sidecar used for Dify DSL inspection/runs.
    dify_graphon_runtime_url: str = "http://localhost:8095"
    dify_graphon_timeout_seconds: float = 15.0
    # Kats keeps a legacy scientific-Python dependency stack, so it runs in a
    # pinned Python 3.10 sidecar instead of the Python 3.13 API process.
    kats_runtime_url: str = "http://localhost:8096"
    kats_runtime_timeout_seconds: float = 120.0
    # Managed acquisition runtime. The commit/version are code-owned pins;
    # this path merely locates the installed checkout on every platform.
    ohmyopencli_root: str = "/opt/ohmyopencli"
    # browser-act CLI subprocess execution timeout, per call (Browser Act integration PR-B).
    # Binary path is BROWSER_ACT_BIN env var (mirrors OPENCLI_BIN), not a
    # Settings field — this is only the per-call timeout default.
    browser_act_timeout: int = 120
    # HTTP dispatch timeout when center POSTs to a LAN agent (should be > opencli_timeout)
    agent_http_timeout: int = 130
    # WS dispatch timeout when center sends a task over a reverse WS channel
    agent_ws_timeout: int = 130

    # AI enrichment processors (processors/openai_processor.py, claude_processor.py,
    # local_processor.py): explicit per-request timeout on the LLM API call itself
    # (AUDIT C8) — the SDKs' own default is a 600s x 2-retry black hole that can
    # otherwise pin a whole batch in ai_processing for hours behind a dead/slow
    # gateway. A source's ai_config can still override this per call via
    # config["timeout"]; this is only the fallback default.
    llm_request_timeout_seconds: int = 120
    # Bound how many per-record LLM calls run concurrently within one enrichment
    # batch (AUDIT C25) — replaces a plain await-in-a-for-loop, where wall-clock
    # cost was record_count x per-call latency.
    llm_max_concurrency: int = 4

    # Webhooks
    webhook_secret: str = "change-me-webhook-secret"

    # Image Studio / InvokeAI sidecar. These values are server-only; the
    # browser talks exclusively to OpenCLI's allowlisted image-studio API.
    # Disabled by default so an absent or unconfigured GPU service fails
    # closed instead of silently dispatching work elsewhere.
    invokeai_enabled: bool = False
    invokeai_base_url: str = "http://invokeai:9090"
    invokeai_api_token: str = ""
    invokeai_request_timeout_seconds: int = 30
    image_asset_storage_path: str = "/data/image-studio/assets"

    # Timezone
    default_timezone: str = "UTC"

    # Pagination
    default_page_size: int = 20
    max_page_size: int = 100

    # Control layer (docs/CONTROL_THEORY_ARCHITECTURE.md §4-5): "advisory"
    # means backend.control only classifies state and suggests ControlActions
    # — nothing executes. "automatic" is surfaced here for the frontend and a
    # FUTURE PR (PR-Control-4, actuators.py) to read; this PR does NOT wire up
    # any execution path even when control_mode="automatic" is set — there is
    # no actuator yet. Changing this setting alone has no runtime effect today.
    control_mode: Literal["advisory", "automatic"] = "advisory"

    # PR-Control-3.5 (advisory evidence ledger). The control-state endpoint is
    # polled by the frontend, so identical consecutive suggestions must
    # deduplicate instead of spamming control_actions rows: a suggestion is
    # skipped when the latest ledger row for the same (source_id, action_type)
    # carries the same state and is younger than this window. 600s ≈ well over
    # any sane poll interval while still recording a fresh row when the same
    # problem persists across a new decision epoch.
    control_advisory_dedup_seconds: int = 600
    # Outcome judgment (backend/control/outcomes.py): how long a ledger row
    # must age before its suggestion is judged against subsequent
    # source_measurements evidence (the plant needs time to produce a
    # post-decision reading)...
    control_outcome_min_age_seconds: int = 3600
    # ...and after how long with NO post-decision measurement at all we stop
    # waiting and record "insufficient_data" — an honest "we never got to see"
    # rather than a verdict.
    control_outcome_stale_seconds: int = 86400

    # Issue 03 (Control Cycle + Actuator, ADR-0007). Background cycle period —
    # deliberately NOT tied to the collection scheduler's own cadence.
    control_cycle_period_seconds: int = 60

    # Global kill switch (config half; the other half is the in-memory
    # runtime toggle under backend.control.kill_switch, POST/GET
    # /api/v1/control/kill-switch — resets to THIS value on restart). Off by
    # default: shipped configuration must execute nothing.
    control_kill_switch: bool = False

    # Execution gate (docs/CONTROL_THEORY_ARCHITECTURE.md, ADR-0007): a
    # (state, action_type) advisory-report bucket must clear BOTH a minimum
    # sample size and a minimum recovery rate before the actuator may execute
    # that suggestion automatically.
    control_gate_min_samples: int = 10
    control_gate_min_recovery_rate: float = 0.6

    # Anti-oscillation guards. Cooldown is per (source_id, action_type);
    # the hourly cap is global across every executed action.
    control_action_cooldown_seconds: int = 3600
    control_max_actions_per_hour: int = 20

    # increase_interval actuator (bounded multiplicative backoff on a
    # source's CronSchedule step interval, e.g. "*/5 * * * *" -> "*/10 * * * *").
    control_increase_interval_factor: float = 2.0
    control_increase_interval_max_minutes: int = 1440

    # pause actuator TTL — how long an executed pause disables a source
    # before the Control Cycle auto-resumes it.
    control_pause_ttl_seconds: int = 3600

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.database_url

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
