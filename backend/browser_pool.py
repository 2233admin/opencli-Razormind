"""CDP browser endpoint pool.

Distributes opencli collection tasks across multiple Chrome instances so tasks
can run concurrently without competing for a single browser.

Two implementations:
  LocalBrowserPool  — per-endpoint asyncio.Queue slots, for TASK_EXECUTOR=local.
  RedisBrowserPool  — Redis BLPOP/RPUSH, for TASK_EXECUTOR=celery (distributed
                      workers across processes / machines).

Routing:
  acquire(endpoint=None)         — any available instance (round-robin / first-free)
  acquire(endpoint="http://...")  — wait specifically for that Chrome instance

Use routing to pin certain data sources to a Chrome instance that is logged into
a specific site (e.g. only chrome-2 is logged into Twitter).
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

logger = logging.getLogger(__name__)


async def _account_reservations(pool) -> set[str]:
    if not getattr(pool, "enforce_account_reservations", False):
        return set()
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.browser import BrowserInstance
    from backend.models.browser_account import BrowserAccount

    # Read durable ownership on each allocation, including in Celery workers.
    # A failed lookup must fail closed instead of handing out a login Profile.
    async with AsyncSessionLocal() as db:
        return set(
            await db.scalars(
                select(BrowserInstance.endpoint).outerjoin(
                    BrowserAccount, BrowserAccount.browser_instance_id == BrowserInstance.id
                ).where(BrowserAccount.id.is_not(None) | BrowserInstance.login_reserved.is_(True))
            )
        )


class NoCleanProfileError(RuntimeError):
    """No explicitly anonymous browser profile is available for acquisition."""

    code = "no_clean_profile"

    def __init__(self) -> None:
        super().__init__(self.code)


class NoReadyBrowserSlotError(RuntimeError):
    """No slot has reported an exact desired/loaded runtime match and self-check."""

    code = "no_ready_browser_slot"

    def __init__(self) -> None:
        super().__init__(self.code)


class LocalBrowserPool:
    """In-process pool backed by per-endpoint asyncio.Queue slots.

    Each slot holds exactly one token (the endpoint URL string).  Acquiring a
    slot removes the token; releasing puts it back.  This ensures at most one
    concurrent task per Chrome instance.

    Unrouted acquire() races all slots and takes whichever becomes available
    first, mimicking the previous single-queue round-robin behaviour.
    """

    def __init__(self, endpoints: list[str]) -> None:
        # One single-element queue per endpoint acting as a semaphore
        self._slots: dict[str, asyncio.Queue[str]] = {}
        for ep in endpoints:
            q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
            q.put_nowait(ep)
            self._slots[ep] = q
        self._total = len(endpoints)
        # mode per endpoint: "bridge" (opencli 1.0.0 daemon) or "cdp" (opencli 0.9.6 Playwright)
        self._modes: dict[str, str] = {ep: "bridge" for ep in endpoints}
        # agent_url per endpoint: HTTP base URL of the agent server (COLLECTION_MODE=agent only)
        self._agent_urls: dict[str, str | None] = {ep: None for ep in endpoints}
        # agent_protocol per endpoint: "http" (LAN/proxy) or "ws" (NAT reverse channel, Phase 2)
        self._agent_protocols: dict[str, str | None] = {ep: None for ep in endpoints}
        # node_type per endpoint: "docker" (started in container) | "shell" (native process)
        self._node_types: dict[str, str] = {ep: "docker" for ep in endpoints}
        # Fail closed: an endpoint is potentially personalized until an agent
        # or operator explicitly registers it as a dedicated anonymous profile.
        self._profile_kinds: dict[str, str] = {ep: "authenticated" for ep in endpoints}
        # Legacy slots have no runtime bundle and retain their historic
        # admission behaviour. A slot assigned a bundle is immediately set to
        # DEGRADED until a loaded-fact report proves it READY.
        self._runtime_states: dict[str, str] = {ep: "LEGACY" for ep in endpoints}
        # Profile write leases are independent of endpoint leases. They defend
        # against accidental duplicate profile assignment across Chromium slots.
        self._profile_names: dict[str, str] = {ep: ep for ep in endpoints}
        self._profile_locks: dict[str, asyncio.Lock] = {ep: asyncio.Lock() for ep in endpoints}
        logger.info(
            "BrowserPool (local): %d Chrome instance(s): %s",
            self._total,
            list(endpoints),
        )

    @asynccontextmanager
    async def acquire(
        self,
        endpoint: str | None = None,
        *,
        required_profile_kind: str | None = None,
    ) -> AsyncIterator[str]:
        if endpoint in await _account_reservations(self):
            raise NoReadyBrowserSlotError()
        if required_profile_kind and (
            endpoint is None
            or endpoint not in self._slots
            or self.get_profile_kind(endpoint) != required_profile_kind
        ):
            raise NoCleanProfileError()
        if endpoint:
            if endpoint not in self._slots:
                logger.warning(
                    "Requested Chrome endpoint %r not in pool; falling back to any instance.",
                    endpoint,
                )
                ep = await self._acquire_any()
            else:
                if not self.is_ready(endpoint):
                    raise NoReadyBrowserSlotError()
                ep = await self._slots[endpoint].get()
                logger.debug("Chrome acquired (routed): %s", ep)
        else:
            ep = await self._acquire_any()

        if required_profile_kind and self.get_profile_kind(ep) != required_profile_kind:
            self._slots[ep].put_nowait(ep)
            raise NoCleanProfileError()

        profile_lock = self._profile_locks.setdefault(self.get_profile_name(ep), asyncio.Lock())
        await profile_lock.acquire()
        try:
            if ep in await _account_reservations(self):
                raise NoReadyBrowserSlotError()
            yield ep
        finally:
            profile_lock.release()
            self._slots[ep].put_nowait(ep)
            logger.debug("Chrome released: %s", ep)

    @asynccontextmanager
    async def acquire_anonymous(self) -> AsyncIterator[str]:
        endpoint = self.select_anonymous_endpoint()
        async with self.acquire(endpoint, required_profile_kind="anonymous") as acquired_endpoint:
            yield acquired_endpoint

    def anonymous_endpoints(self) -> list[str]:
        candidates = [
            endpoint
            for endpoint in self.endpoints
            if self.get_profile_kind(endpoint) == "anonymous"
        ]
        if not candidates:
            raise NoCleanProfileError()
        ready_candidates = [endpoint for endpoint in candidates if self.is_ready(endpoint)]
        if not ready_candidates:
            raise NoReadyBrowserSlotError()
        return ready_candidates

    def select_anonymous_endpoint(self) -> str:
        candidates = self.anonymous_endpoints()
        return next(
            (endpoint for endpoint in candidates if self.available_for(endpoint)),
            candidates[0],
        )

    async def _acquire_any(self) -> str:
        """Wait for whichever READY endpoint slot becomes free first."""
        reserved = await _account_reservations(self)
        ready_slots = {
            ep: slot for ep, slot in self._slots.items() if self.is_ready(ep) and ep not in reserved
        }
        if not ready_slots:
            raise NoReadyBrowserSlotError()
        tasks: dict[asyncio.Task[str], str] = {
            asyncio.get_event_loop().create_task(slot.get()): ep for ep, slot in ready_slots.items()
        }
        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        ep = tasks[next(iter(done))]
        logger.debug(
            "Chrome acquired (any): %s (available: %d/%d)",
            ep,
            self.available,
            self._total,
        )
        return ep

    @property
    def total(self) -> int:
        return self._total

    @property
    def available(self) -> int:
        return sum(1 for q in self._slots.values() if not q.empty())

    @property
    def endpoints(self) -> list[str]:
        return list(self._slots.keys())

    def available_for(self, endpoint: str) -> bool:
        q = self._slots.get(endpoint)
        return q is not None and not q.empty()

    def get_mode(self, endpoint: str) -> str:
        """Return the connection mode for the given endpoint ("bridge" or "cdp")."""
        return self._modes.get(endpoint, "bridge")

    def set_mode(self, endpoint: str, mode: str) -> None:
        """Update the connection mode for an endpoint at runtime."""
        self._modes[endpoint] = mode
        logger.info("BrowserPool: endpoint %s mode set to %s", endpoint, mode)

    def get_agent_url(self, endpoint: str) -> str | None:
        """Return the agent HTTP base URL for the given endpoint."""
        return self._agent_urls.get(endpoint)

    def set_agent_url(self, endpoint: str, agent_url: str | None) -> None:
        """Update the agent URL for an endpoint at runtime."""
        self._agent_urls[endpoint] = agent_url
        logger.info("BrowserPool: endpoint %s agent_url set to %s", endpoint, agent_url)

    def get_agent_protocol(self, endpoint: str) -> str | None:
        """Return the agent protocol for the given endpoint ("http", "ws", or None)."""
        return self._agent_protocols.get(endpoint)

    def set_agent_protocol(self, endpoint: str, protocol: str | None) -> None:
        """Update the agent protocol for an endpoint at runtime."""
        self._agent_protocols[endpoint] = protocol
        logger.info("BrowserPool: endpoint %s agent_protocol set to %s", endpoint, protocol)

    def get_node_type(self, endpoint: str) -> str:
        """Return deployment type: 'docker' (started in container) or 'shell' (native process)."""
        return self._node_types.get(endpoint, "docker")

    def set_node_type(self, endpoint: str, node_type: str) -> None:
        """Update the node deployment type for an endpoint."""
        self._node_types[endpoint] = node_type
        logger.info("BrowserPool: endpoint %s node_type set to %s", endpoint, node_type)

    def get_profile_kind(self, endpoint: str) -> str:
        return self._profile_kinds.get(endpoint, "authenticated")

    def set_profile_kind(self, endpoint: str, profile_kind: str) -> None:
        if profile_kind not in {"anonymous", "authenticated"}:
            raise ValueError("profile_kind must be 'anonymous' or 'authenticated'")
        self._profile_kinds[endpoint] = profile_kind
        logger.info(
            "BrowserPool: endpoint %s profile_kind set to %s",
            endpoint,
            profile_kind,
        )

    def get_profile_name(self, endpoint: str) -> str:
        return self._profile_names.get(endpoint, endpoint)

    def set_profile_name(self, endpoint: str, profile_name: str) -> None:
        if not profile_name:
            raise ValueError("profile_name must not be empty")
        self._profile_names[endpoint] = profile_name
        self._profile_locks.setdefault(profile_name, asyncio.Lock())

    def runtime_status(self, endpoint: str) -> str:
        return self._runtime_states.get(endpoint, "LEGACY")

    def is_ready(self, endpoint: str) -> bool:
        return self.runtime_status(endpoint) in {"READY", "LEGACY"}

    def set_runtime_status(self, endpoint: str, state: str) -> None:
        self._runtime_states[endpoint] = state
        logger.info("BrowserPool: endpoint %s runtime status set to %s", endpoint, state)

    def add_endpoint(self, endpoint: str) -> None:
        """Hot-add a new Chrome instance to the pool without restarting."""
        if endpoint in self._slots:
            return
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        q.put_nowait(endpoint)
        self._slots[endpoint] = q
        self._modes.setdefault(endpoint, "bridge")
        self._agent_urls.setdefault(endpoint, None)
        self._agent_protocols.setdefault(endpoint, None)
        self._node_types.setdefault(endpoint, "docker")
        self._runtime_states.setdefault(endpoint, "LEGACY")
        self._profile_names.setdefault(endpoint, endpoint)
        self._profile_locks.setdefault(self._profile_names[endpoint], asyncio.Lock())
        self._profile_kinds.setdefault(endpoint, "authenticated")
        self._total += 1
        logger.info("BrowserPool: added endpoint %s (total: %d)", endpoint, self._total)

    def remove_endpoint(self, endpoint: str) -> None:
        """Remove a Chrome instance from the pool (best-effort; waits for slot to be free)."""
        if endpoint not in self._slots:
            return
        self._slots.pop(endpoint)
        self._modes.pop(endpoint, None)
        self._agent_urls.pop(endpoint, None)
        self._agent_protocols.pop(endpoint, None)
        self._node_types.pop(endpoint, None)
        self._runtime_states.pop(endpoint, None)
        self._profile_names.pop(endpoint, None)
        self._profile_kinds.pop(endpoint, None)
        self._total -= 1
        logger.info("BrowserPool: removed endpoint %s (total: %d)", endpoint, self._total)


class RedisBrowserPool:
    """Distributed pool backed by Redis lists (BLPOP/RPUSH).

    Safe across multiple Celery worker processes and machines.

    Routing support:
      - Unrouted acquire() uses the shared pool list (existing behaviour).
      - Routed acquire(endpoint=...) uses a per-endpoint list key so only
        that specific Chrome instance is used, without consuming a slot from
        the shared pool.  This lets you pin data sources to Chrome instances
        that are logged into specific sites.

    Initialisation is idempotent (SET NX lock) so multiple API/worker
    replicas don't double-push endpoints.
    """

    _POOL_KEY = "browser_pool:endpoints"
    _LOCK_KEY = "browser_pool:initialized"
    _REGISTRY_KEY = "browser_pool:registered"
    _LEASE_TTL_MS = 30_000
    _LEASE_RENEW_SECONDS = 10
    _ACQUIRE_TIMEOUT_SECONDS = 300
    _RETRY_SECONDS = 0.1

    @staticmethod
    def _ep_key(endpoint: str) -> str:
        """Per-endpoint Redis list key (safe characters only)."""
        safe = endpoint.replace("://", "_").replace(":", "_").replace("/", "_")
        return f"browser_pool:ep:{safe}"

    @classmethod
    def _lease_key(cls, endpoint: str) -> str:
        return f"{cls._ep_key(endpoint)}:lease"

    @classmethod
    def _fence_key(cls, endpoint: str) -> str:
        return f"{cls._ep_key(endpoint)}:fence"

    def __init__(self, endpoints: list[str], redis_url: str) -> None:
        self._endpoints = list(endpoints)
        self._redis_url = redis_url
        self._total = len(endpoints)
        self._modes: dict[str, str] = {ep: "bridge" for ep in endpoints}
        self._profile_kinds: dict[str, str] = {ep: "authenticated" for ep in endpoints}
        self._runtime_states: dict[str, str] = {ep: "LEGACY" for ep in endpoints}
        self._profile_names: dict[str, str] = {ep: ep for ep in endpoints}

    def _client(self):
        import redis.asyncio as aioredis  # type: ignore[import]

        return aioredis.from_url(self._redis_url, decode_responses=True)

    async def initialize(self) -> None:
        """Populate the Redis pool and per-endpoint lists (idempotent)."""
        async with self._client() as r:
            acquired = await r.set(self._LOCK_KEY, "1", nx=True)
            if not acquired:
                logger.info("BrowserPool (Redis): pool already initialized by another replica")
                return

            # Shared pool list (for unrouted acquire)
            await r.delete(self._POOL_KEY)
            if self._endpoints:
                await r.rpush(self._POOL_KEY, *self._endpoints)
                await r.sadd(self._REGISTRY_KEY, *self._endpoints)

            # Per-endpoint lists (for routed acquire)
            for ep in self._endpoints:
                key = self._ep_key(ep)
                await r.delete(key)
                await r.rpush(key, ep)

            logger.info(
                "BrowserPool (Redis): %d Chrome instance(s) initialised",
                self._total,
            )

    async def register_endpoint(self, endpoint: str) -> None:
        """Add a DB-discovered endpoint to every worker and Redis exactly once."""
        if endpoint not in self._endpoints:
            self._endpoints.append(endpoint)
            self._total += 1
            self._modes.setdefault(endpoint, "bridge")
            self._profile_kinds.setdefault(endpoint, "authenticated")

        async with self._client() as r:
            self._runtime_states.setdefault(endpoint, "LEGACY")
            self._profile_names.setdefault(endpoint, endpoint)
            added = await r.sadd(self._REGISTRY_KEY, endpoint)
            if not added:
                return
            await r.rpush(self._POOL_KEY, endpoint)
            await r.rpush(self._ep_key(endpoint), endpoint)

    @asynccontextmanager
    async def acquire(
        self,
        endpoint: str | None = None,
        *,
        required_profile_kind: str | None = None,
    ) -> AsyncIterator[str]:
        if required_profile_kind and (
            endpoint is None
            or endpoint not in self._endpoints
            or self.get_profile_kind(endpoint) != required_profile_kind
        ):
            raise NoCleanProfileError()
        if endpoint is not None and not self.is_ready(endpoint):
            raise NoReadyBrowserSlotError()
        reserved = await _account_reservations(self)
        candidates = [
            candidate
            for candidate in ([endpoint] if endpoint else list(self._endpoints))
            if candidate is not None and self.is_ready(candidate) and candidate not in reserved
        ]
        if not candidates:
            raise NoReadyBrowserSlotError()
        deadline = time.monotonic() + self._ACQUIRE_TIMEOUT_SECONDS
        ep = None
        owner = None
        while time.monotonic() < deadline and ep is None:
            for candidate in candidates:
                async with self._client() as r:
                    fence = await r.incr(self._fence_key(candidate))
                    candidate_owner = f"{fence}:{uuid4()}"
                    acquired = await r.set(
                        self._lease_key(candidate),
                        candidate_owner,
                        nx=True,
                        px=self._LEASE_TTL_MS,
                    )
                if acquired:
                    ep = candidate
                    owner = candidate_owner
                    break
            if ep is None:
                await asyncio.sleep(self._RETRY_SECONDS)
        if ep is None or owner is None:
            raise TimeoutError("No Chrome endpoint lease became available in time")

        owning_task = asyncio.current_task()
        stop_renewal = asyncio.Event()

        async def renew() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop_renewal.wait(), timeout=self._LEASE_RENEW_SECONDS)
                    return
                except TimeoutError:
                    pass
                async with self._client() as r:
                    renewed = await r.eval(
                        "if redis.call('get',KEYS[1]) == ARGV[1] then "
                        "return redis.call('pexpire',KEYS[1],ARGV[2]) else return 0 end",
                        1,
                        self._lease_key(ep),
                        owner,
                        self._LEASE_TTL_MS,
                    )
                if not renewed:
                    if owning_task is not None:
                        owning_task.cancel()
                    return

        renewal_task = asyncio.create_task(renew())

        try:
            if ep in await _account_reservations(self):
                raise NoReadyBrowserSlotError()
            yield ep
        finally:
            stop_renewal.set()
            await renewal_task
            async with self._client() as r:
                await r.eval(
                    "if redis.call('get',KEYS[1]) == ARGV[1] then "
                    "return redis.call('del',KEYS[1]) else return 0 end",
                    1,
                    self._lease_key(ep),
                    owner,
                )
            logger.debug("Chrome lease released (Redis): %s", ep)

    @property
    def total(self) -> int:
        return self._total

    @property
    def available(self) -> int:
        return -1  # unknown without a synchronous Redis call

    @property
    def endpoints(self) -> list[str]:
        return list(self._endpoints)

    def available_for(self, endpoint: str) -> bool:
        return endpoint in self._endpoints  # approximate; real check would need Redis

    def get_mode(self, endpoint: str) -> str:
        return self._modes.get(endpoint, "bridge")

    def set_mode(self, endpoint: str, mode: str) -> None:
        self._modes[endpoint] = mode

    def get_profile_kind(self, endpoint: str) -> str:
        return self._profile_kinds.get(endpoint, "authenticated")

    def set_profile_kind(self, endpoint: str, profile_kind: str) -> None:
        if profile_kind not in {"anonymous", "authenticated"}:
            raise ValueError("profile_kind must be 'anonymous' or 'authenticated'")
        self._profile_kinds[endpoint] = profile_kind

    def get_profile_name(self, endpoint: str) -> str:
        return self._profile_names.get(endpoint, endpoint)

    def set_profile_name(self, endpoint: str, profile_name: str) -> None:
        if not profile_name:
            raise ValueError("profile_name must not be empty")
        self._profile_names[endpoint] = profile_name

    def runtime_status(self, endpoint: str) -> str:
        return self._runtime_states.get(endpoint, "LEGACY")

    def is_ready(self, endpoint: str) -> bool:
        return self.runtime_status(endpoint) in {"READY", "LEGACY"}

    def set_runtime_status(self, endpoint: str, state: str) -> None:
        self._runtime_states[endpoint] = state

    def anonymous_endpoints(self) -> list[str]:
        candidates = [
            endpoint
            for endpoint in self.endpoints
            if self.get_profile_kind(endpoint) == "anonymous" and self.is_ready(endpoint)
        ]
        if not candidates:
            raise NoCleanProfileError()
        return candidates

    def select_anonymous_endpoint(self) -> str:
        return self.anonymous_endpoints()[0]


# ── Module-level singleton ────────────────────────────────────────────────────

_pool: LocalBrowserPool | RedisBrowserPool | None = None


def init_pool(
    endpoints: list[str],
    use_redis: bool = False,
    redis_url: str = "",
) -> LocalBrowserPool | RedisBrowserPool:
    global _pool
    if use_redis and redis_url:
        _pool = RedisBrowserPool(endpoints, redis_url)
    else:
        _pool = LocalBrowserPool(endpoints)
    _pool.enforce_account_reservations = True
    return _pool


async def ensure_ready() -> None:
    """Async post-init step (populates Redis lists if applicable)."""
    if isinstance(_pool, RedisBrowserPool):
        await _pool.initialize()


def get_pool() -> LocalBrowserPool | RedisBrowserPool:
    if _pool is None:
        raise RuntimeError("BrowserPool not initialized — call init_pool() first")
    return _pool
