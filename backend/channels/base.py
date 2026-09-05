from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChannelResult:
    """Result from a channel collect() call."""

    success: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    #: The failing exception's class name (e.g. "TimeoutException"), when the
    #: failure came from a caught exception — lets callers build a retryable-vs-
    #: permanent taxonomy without re-parsing the free-text error message.
    error_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)

    @classmethod
    def ok(cls, items: list[dict[str, Any]], **metadata: Any) -> "ChannelResult":
        return cls(success=True, items=items, metadata=metadata)

    @classmethod
    def fail(cls, error: str, error_type: str | None = None) -> "ChannelResult":
        return cls(success=False, error=error, error_type=error_type)


# ── Thick channel contract (Phase 0) ─────────────────────────────────────────
# A channel should only declare what it can do and implement the source-specific
# "fetch one batch + parse"; the runner owns every cross-cutting concern (auth
# refresh, pagination, rate limiting, cursor persistence). These types are that
# seam. In Phase 0 they are purely additive: the default fetch() bridges to the
# legacy collect(), so the existing channels inherit the contract for free and
# runtime behaviour is unchanged. Later phases migrate channels onto fetch() and
# wire the runner that calls it.


@dataclass(frozen=True)
class Capabilities:
    """What a channel can do; the runner orchestrates against this declaration."""

    incremental: bool = False        # can resume from a persisted cursor
    paginated: bool = False          # can fetch page by page
    auth_kind: str = "none"          # none | api_key | bearer | oauth2 | session
    session_affinity: bool = False   # must run on the node holding a live session
    default_rate: str = "60/min"     # token-bucket default the runner applies


@dataclass
class AuthContext:
    """Resolved, already-refreshed credentials the runner injects. Phase 2 fills
    this from an AuthManager + encrypted store; Phase 0 keeps the placeholder so
    FetchContext's type is stable and channels never touch raw secrets."""

    kind: str = "none"
    token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FetchContext:
    """Everything the runner feeds a channel to make ONE fetch. The channel reads
    these and does only source-specific work — it never refreshes a token, sleeps
    for a rate limit, or persists a cursor itself."""

    config: dict[str, Any]
    params: dict[str, Any]
    # Persisted "where we left off" (etag / since_id / page_token).
    cursor: dict[str, Any] | None = None
    # The DataSource id, for channels that need credential lookup.
    source_id: str | None = None
    # Resolved credentials (Phase 2).
    auth: AuthContext | None = None
    # Shared httpx.AsyncClient, with rate-limit + retry built in (Phase 1).
    http: Any = None
    # Logger injected by the runner.
    log: Any = None


@dataclass
class FetchResult:
    """One batch out of a channel. The runner persists next_cursor and, when the
    channel is paginated and has_more, calls fetch() again with it."""

    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: dict[str, Any] | None = None
    has_more: bool = False
    #: Non-item result data (e.g. opencli's node_url/chrome_mode, skill's
    #: awaiting_confirm) that must reach PipelineResult.metadata unchanged — the
    #: default fetch() adapter forwards collect()'s ChannelResult.metadata here so
    #: routing a channel through run_channel() never drops it.
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelFetchError(Exception):
    """Raised by the default fetch() adapter when the wrapped collect() failed, so
    the runner applies its retry/backoff policy instead of silently dropping.

    ``error_type`` carries an explicit retry-classification hint (see
    ``error_taxonomy.effective_error_type``) for callers that already know the
    fault category (e.g. an HTTP status code) but don't want to lose it behind
    a generic wrapper exception."""

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


class AbstractChannel(ABC):
    """Base class for all data collection channels."""

    channel_type: str
    #: What this channel can do. Defaults to the most conservative profile
    #: (one-shot, no auth, no pagination) so existing channels keep their current
    #: behaviour; a channel overrides this with its own Capabilities(...).
    capabilities: Capabilities = Capabilities()

    @abstractmethod
    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        """Collect data from the channel.

        Args:
            config: Channel-specific configuration (from DataSource.channel_config).
            parameters: Runtime parameters (e.g., from task trigger).

        Returns:
            ChannelResult with collected items or error.
        """

    @abstractmethod
    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate config dict; return list of error strings (empty = valid)."""

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        """Fetch ONE batch under the thick contract. Default adapter: bridge to the
        legacy collect() — a channel that only implements collect() gets fetch()
        for free (one-shot, no cursor, no pagination). Channels migrate by
        overriding this and reading ctx.cursor / ctx.http / ctx.auth directly. The
        runner calls fetch(), never collect()."""
        result = await self.collect(ctx.config, ctx.params)
        if not result.success:
            raise ChannelFetchError(
                result.error or f"{self.channel_type} collect failed",
                error_type=result.error_type,
            )
        return FetchResult(items=result.items, metadata=result.metadata)

    def identity(self, item: dict[str, Any]) -> str | None:
        """Stable source-native id for an item — the dedup key. Default None → the
        normalizer falls back to its content hash (current behaviour). A channel
        with a native id (RSS entry.id, tweet id) overrides this to fix 'edit two
        chars in the title = a new item'."""
        return None

    async def health_check(
        self, config: dict[str, Any] | None = None, source_id: str | None = None
    ) -> bool:
        """Optional health check. Override to implement channel-specific check.
        ``config`` (the source's channel_config) lets an override do a real
        per-source probe instead of a generic liveness check; ``source_id``
        lets it resolve real credentials from the encrypted store (see
        ``ApiChannel._resolve_auth_headers``) for an authenticated probe."""
        return True
