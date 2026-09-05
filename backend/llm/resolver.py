"""Provider resolver + failover (model-provider runtime PR-D, decision #7).

Reads a role's ``model_defaults.candidates`` (PR-A) — an ordered list of
``{"provider_id", "model_id"}`` dicts — and dispatches through
:func:`backend.llm.factory.get_adapter` (PR-B) to try providers in that
order. Only a *connection-level* failure (see
:func:`backend.llm.base.classify_retryable`: connect error, timeout, 5xx)
degrades to the next candidate; a *business* failure (4xx — bad key,
malformed request) is a configuration problem no other candidate would fix,
so it is re-raised immediately instead of being silently masked behind a
fallback model (decision #7).

Cooldown is an in-process ``dict`` keyed by ``provider_id`` — no Redis
(decision #7): this app runs as a single process, so a plain dict plus an
injectable clock is enough, and it keeps this PR pure logic with no new
infra dependency.

This module is pure logic + the module-level ``resolver`` singleton — it is
NOT wired into any consumption point yet (chat.py / skill_channel /
processors). That wiring is PR-E's job.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.llm.base import LlmAdapterError, ProviderAdapter
from backend.llm.factory import get_adapter
from backend.models.model_default import ModelDefault
from backend.models.provider import ModelProvider

T = TypeVar("T")

#: async callable invoked per live candidate: operation(adapter, model_id) -> T
Operation = Callable[[ProviderAdapter, str], Awaitable[T]]


class ResolverError(Exception):
    """Raised by :meth:`ProviderResolver.resolve_with_fallback` when no live
    candidate exists for a role — either ``model_defaults`` has no row (or an
    empty ``candidates`` list) for it, or every configured candidate is
    currently unavailable (cooled down and/or just retryable-failed).

    The message only ever names the role and tried/cooled counts — never a
    provider's ``api_key`` (nothing here ever touches a raw key, only ids).
    """


@dataclass
class ResolvedModel:
    """One resolved ``(provider, model_id, adapter)`` triple — what
    :meth:`ProviderResolver.resolve` returns for a role's primary candidate.
    """

    provider: ModelProvider
    model_id: str
    adapter: ProviderAdapter


class ProviderResolver:
    """Resolves a consumption ``role`` (PR-A: ``chat``/``executor``/
    ``enrichment``) to a live provider, trying ``model_defaults.candidates``
    in order and skipping/cooling down ones that just failed.

    ``now`` is an injectable monotonic clock (defaults to
    ``time.monotonic``) so unit tests can fast-forward a cooldown window
    without a real sleep. Cooldown state (``self._cooldown_until``) is a
    plain in-process ``dict`` — no Redis (decision #7) — so it is
    per-process only; that matches this app's current single-process
    deployment.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float = 60.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._now = now
        self._cooldown_until: dict[str, float] = {}

    def _is_cooled(self, provider_id: str) -> bool:
        until = self._cooldown_until.get(provider_id)
        return until is not None and self._now() < until

    def _set_cooldown(self, provider_id: str) -> None:
        # Single synchronous read-then-write, no `await` in between — safe
        # under concurrent asyncio callers sharing this resolver (nothing
        # can interleave inside one un-awaited statement).
        self._cooldown_until[provider_id] = self._now() + self.cooldown_seconds

    async def _candidates_for(self, db: AsyncSession, role: str) -> list[dict[str, Any]]:
        result = await db.execute(select(ModelDefault).where(ModelDefault.role == role))
        row = result.scalar_one_or_none()
        if row is None:
            return []
        return list(row.candidates or [])

    async def has_candidates(self, db: AsyncSession, role: str) -> bool:
        """``True`` iff a non-empty ``model_defaults.candidates`` list exists
        for ``role``.

        A cheap pre-check for callers that want to keep a legacy fallback
        path when the role has no failover order configured (e.g.
        ``chat.py``: candidates configured → failover runtime; no candidates
        → the pre-failover single-provider lookup, so existing installs
        keep working unchanged).
        """
        return bool(await self._candidates_for(db, role))

    async def resolve(self, db: AsyncSession, role: str) -> ResolvedModel | None:
        """Return the primary (first) candidate configured for ``role``.

        Returns ``None`` — rather than raising — when no ``model_defaults``
        row exists for ``role``, its ``candidates`` list is empty, the
        first candidate's ``provider_id`` no longer resolves to a real
        provider row, or that provider is currently disabled: this is a
        direct "what's configured AND usable" lookup for callers that want
        the single default, not a "try until one works" search (that's
        :meth:`resolve_with_fallback`) — there is nothing to fail over to
        here, so a clean ``None`` is more useful to a caller than an
        exception for what is often just "nothing configured yet".
        """
        candidates = await self._candidates_for(db, role)
        if not candidates:
            return None
        candidate = candidates[0]
        provider = await db.get(ModelProvider, candidate["provider_id"])
        if provider is None or not provider.enabled:
            return None
        return ResolvedModel(
            provider=provider,
            model_id=candidate["model_id"],
            adapter=get_adapter(provider),
        )

    async def resolve_with_fallback(
        self, db: AsyncSession, role: str, operation: Operation[T]
    ) -> T:
        """Try ``role``'s candidates in order, calling ``await
        operation(adapter, model_id)`` for the first live one.

        - A candidate whose ``provider_id`` is currently cooled down is
          skipped WITHOUT building its adapter (a cooled provider is
          assumed still broken; no point re-probing it every call before its
          window expires).
        - A candidate whose ``provider_id`` no longer resolves to a real
          provider row (deleted since the default was configured) is
          skipped the same way, without being put in cooldown (there is no
          "it" to cool down). A candidate whose provider row is currently
          ``enabled=False`` is skipped identically — disabling a provider
          in governance is an operator choice, not a liveness failure, so
          it must not be used and must not go into cooldown either.
        - ``operation`` succeeding returns that result immediately — no
          further candidates are tried.
        - ``operation`` raising :class:`~backend.llm.base.LlmAdapterError`
          with ``retryable=True`` (connection-level) puts that
          ``provider_id`` in cooldown and moves on to the next candidate.
        - ``operation`` raising ``LlmAdapterError`` with ``retryable=False``
          (business/4xx) is re-raised IMMEDIATELY: no cooldown is set, no
          further candidate is tried (decision #7 — this is a config error,
          not a liveness problem, and failing over would mask it).
        - once every candidate has been skipped (cooled/missing) or has
          retryable-failed, raises :class:`ResolverError` naming ``role``
          and how many candidates were tried vs. skipped-as-cooled.
        """
        candidates = await self._candidates_for(db, role)
        if not candidates:
            raise ResolverError(f"no model_defaults candidates configured for role={role!r}")

        tried = 0
        cooled = 0
        for candidate in candidates:
            provider_id = candidate["provider_id"]
            if self._is_cooled(provider_id):
                cooled += 1
                continue
            provider = await db.get(ModelProvider, provider_id)
            if provider is None:
                # Candidate references a since-deleted provider — dead, but
                # not "this provider just failed", so no cooldown to set.
                continue
            if not provider.enabled:
                # Disabled in governance — an operator choice, not a
                # liveness failure: skip without cooldown, same as a
                # deleted provider.
                continue
            adapter = get_adapter(provider)
            tried += 1
            try:
                return await operation(adapter, candidate["model_id"])
            except LlmAdapterError as exc:
                if not exc.retryable:
                    raise
                self._set_cooldown(provider_id)
                continue

        raise ResolverError(
            f"role={role!r}: no live provider candidate "
            f"(tried={tried}, cooled={cooled}, total={len(candidates)})"
        )


#: Module-level default instance — production code imports this singleton;
#: tests build their own ``ProviderResolver(now=fake_clock)`` (decision #7).
resolver = ProviderResolver()
