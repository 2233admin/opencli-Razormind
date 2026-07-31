"""Service-layer logic for model-provider runtime PR-C: provider model catalog (sync + CRUD)
and model_defaults (get/put), plus provider-delete catalog cleanup.

Kept out of ``backend/api/v1/providers.py`` / ``backend/api/v1/model_defaults.py``
(thin-endpoint convention — see ``backend/services/source_service.py`` and
friends) so the sync-upsert/manual-preservation logic (decision #3) and the
defaults validation logic are independently unit-testable without spinning up
the ASGI app, and so tests have one obvious seam (``get_adapter`` in this
module) to patch instead of reaching into the router.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.llm import is_valid_role
from backend.llm.factory import get_adapter
from backend.models.model_default import ModelDefault
from backend.models.provider import ModelProvider
from backend.models.provider_model import ProviderModel


class ModelDefaultsValidationError(ValueError):
    """Raised by :func:`put_default` for a bad role or a candidate that
    doesn't resolve to a real provider/catalog entry.

    The message only ever names role/provider_id/model_id — safe to surface
    verbatim as an HTTP 4xx ``detail`` (never touches ``api_key``).
    """


class SyncResult(TypedDict):
    added: int
    updated: int
    kept_manual: int
    pruned: int


# ---------------------------------------------------------------------------
# Providers (lookup helper shared by every endpoint below)
# ---------------------------------------------------------------------------


async def get_provider(db: AsyncSession, provider_id: str) -> Optional[ModelProvider]:
    return await db.get(ModelProvider, provider_id)


# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------


async def list_models(db: AsyncSession, provider_id: str) -> list[ProviderModel]:
    result = await db.execute(
        select(ProviderModel)
        .where(ProviderModel.provider_id == provider_id)
        .order_by(ProviderModel.model_id)
    )
    return list(result.scalars().all())


async def get_model(db: AsyncSession, model_row_id: str) -> Optional[ProviderModel]:
    return await db.get(ProviderModel, model_row_id)


async def add_manual_model(db: AsyncSession, provider_id: str, body: Any) -> ProviderModel:
    """Insert a hand-entered catalog row.

    ``source`` is always forced to ``"manual"`` regardless of what ``body``
    carries (the request schema, ``ProviderModelManualCreate``, doesn't even
    expose a ``source`` field) — decision #3's manual/discovered boundary is
    enforced structurally: this is the ONLY function that ever writes
    ``source="manual"``, :func:`sync_models` is the only one that writes
    ``source="discovered"``.
    """
    row = ProviderModel(
        provider_id=provider_id,
        model_id=body.model_id,
        model_type=body.model_type,
        capabilities=body.capabilities,
        source="manual",
        enabled=body.enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_model(
    db: AsyncSession, model_row_id: str, body: Any
) -> Optional[ProviderModel]:
    """Partial-update a catalog row (``enabled``/``capabilities``/``model_type``).

    Returns ``None`` if ``model_row_id`` doesn't exist — callers decide
    whether that's a 404 (the router checks ownership against ``provider_id``
    *before* calling this, so this function itself never needs the parent
    provider id).
    """
    row = await db.get(ProviderModel, model_row_id)
    if row is None:
        return None
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_model(db: AsyncSession, model_row_id: str) -> bool:
    """Delete one catalog row. Returns ``False`` if it didn't exist."""
    row = await db.get(ProviderModel, model_row_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def delete_provider_models(db: AsyncSession, provider_id: str) -> int:
    """Wipe a provider's whole catalog. Returns the number of rows deleted.

    Called from the providers router's ``DELETE /providers/{id}`` BEFORE the
    provider row itself is deleted (model-provider runtime PR-A note, decision #3): this
    repo's runtime engine (``backend/database.py``) never issues ``PRAGMA
    foreign_keys=ON``, so ``provider_models.provider_id``'s ``ondelete=
    CASCADE`` clause never actually fires against the production sqlite file
    — without this explicit cleanup, deleting a provider would silently
    orphan its ``provider_models`` rows instead of cascading them away.
    """
    result = await db.execute(delete(ProviderModel).where(ProviderModel.provider_id == provider_id))
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Discovery sync (decision #3)
# ---------------------------------------------------------------------------


async def sync_models(db: AsyncSession, provider_id: str) -> Optional[SyncResult]:
    """Discover a provider's models via its adapter and upsert them into the
    catalog as ``source="discovered"`` rows.

    Returns ``None`` if ``provider_id`` doesn't exist (router 404s). Raises
    whatever :class:`~backend.llm.base.LlmAdapterError` the adapter's
    ``list_models()`` raises on a genuine discovery failure (connection
    error, bad credentials, ...) — this mirrors ``list_models``'s own
    contract (non-raising probes belong to ``test_connection``, not sync);
    the router converts that into a 502 rather than a raw 500.

    Upsert rules (decision #3 + this PR's stale-row policy):

      * a discovered ``model_id`` with no existing row -> inserted
        (``added``);
      * a discovered ``model_id`` that already has a ``source="discovered"``
        row -> left alone, counted as ``updated`` (there is nothing richer
        to write yet — ``list_models()`` only returns bare ids — but this
        keeps the row from being re-inserted, which would violate the
        ``(provider_id, model_id)`` unique constraint, and gives a future
        richer discovery payload somewhere to land without reshaping this
        function);
      * a discovered ``model_id`` that already has a ``source="manual"``
        row -> left COMPLETELY untouched, counted as ``kept_manual``
        (decision #3, hard requirement: sync must never overwrite or delete
        a manually-entered row, even one that happens to share a model_id
        the provider also reports);
      * an existing ``source="discovered"`` row whose ``model_id`` was NOT
        in this sync's results (the provider no longer serves it) ->
        deleted, counted as ``pruned``. This half is this PR's own design
        choice (decision #3 only pins "manual survives", it doesn't mandate
        stale-discovered pruning) — without it, re-running sync against a
        provider that removed a model would leave a phantom catalog entry
        forever, which defeats sync being idempotent/re-runnable as the
        source of truth for "what does this provider serve right now".
        Manual rows are NEVER subject to this prune, regardless of whether
        their model_id appears in the fresh discovery results.
    """
    provider = await get_provider(db, provider_id)
    if provider is None:
        return None

    adapter = get_adapter(provider)
    discovered_ids = await adapter.list_models()
    discovered_set = set(discovered_ids)

    existing = await list_models(db, provider_id)
    existing_by_model_id = {row.model_id: row for row in existing}

    added = updated = kept_manual = pruned = 0

    for model_id in discovered_ids:
        row = existing_by_model_id.get(model_id)
        if row is None:
            db.add(
                ProviderModel(
                    provider_id=provider_id,
                    model_id=model_id,
                    source="discovered",
                )
            )
            added += 1
        elif row.source == "manual":
            kept_manual += 1
        else:
            updated += 1

    for row in existing:
        if row.source == "discovered" and row.model_id not in discovered_set:
            await db.delete(row)
            pruned += 1

    await db.commit()
    return SyncResult(added=added, updated=updated, kept_manual=kept_manual, pruned=pruned)


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------


async def test_connection(db: AsyncSession, provider_id: str) -> Optional[dict]:
    """Probe a provider via its adapter. Returns ``None`` if ``provider_id``
    doesn't exist (router 404s); otherwise the
    :class:`~backend.llm.base.ConnectionTestResult` dict as-is —
    ``test_connection()`` never raises and never leaks ``api_key`` (enforced
    in the adapters themselves, see ``backend.llm.base.redact_secret``)."""
    provider = await get_provider(db, provider_id)
    if provider is None:
        return None
    adapter = get_adapter(provider)
    return dict(await adapter.test_connection())


# ---------------------------------------------------------------------------
# model_defaults
# ---------------------------------------------------------------------------


async def get_defaults(db: AsyncSession) -> list[ModelDefault]:
    result = await db.execute(select(ModelDefault).order_by(ModelDefault.role))
    return list(result.scalars().all())


async def put_default(db: AsyncSession, role: str, candidates: list[Any]) -> ModelDefault:
    """Validate + upsert the ``model_defaults`` row for ``role``.

    Validation (decision #10):

      * ``role`` must be in the closed set (``backend.llm.is_valid_role`` —
        the router's Pydantic path/body layer also checks this; this is
        defense in depth for callers that hit this function directly, e.g.
        tests);
      * every candidate's ``(provider_id, model_id)`` pair must reference a
        provider that exists AND a catalog row that exists for that exact
        pair. A candidate naming a real provider but a ``model_id`` never
        synced/registered into that provider's catalog is rejected outright
        — otherwise the resolver (PR-D) would silently try to route to a
        model nobody ever confirmed that provider actually serves.

    Raises :class:`ModelDefaultsValidationError` (subclass of ``ValueError``)
    naming the first bad role/candidate found; the router maps that to a
    4xx with the message as ``detail``.
    """
    if not is_valid_role(role):
        raise ModelDefaultsValidationError(f"invalid role: {role!r}")

    for candidate in candidates:
        provider_id = candidate.provider_id
        model_id = candidate.model_id
        provider = await get_provider(db, provider_id)
        if provider is None:
            raise ModelDefaultsValidationError(
                f"candidate references nonexistent provider_id={provider_id!r}"
            )
        result = await db.execute(
            select(ProviderModel).where(
                ProviderModel.provider_id == provider_id,
                ProviderModel.model_id == model_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ModelDefaultsValidationError(
                f"candidate model_id={model_id!r} is not in provider "
                f"{provider_id!r}'s catalog (sync or register it first)"
            )

    candidates_json = [c.model_dump() for c in candidates]

    result = await db.execute(select(ModelDefault).where(ModelDefault.role == role))
    row = result.scalar_one_or_none()
    if row is None:
        row = ModelDefault(role=role, candidates=candidates_json)
        db.add(row)
    else:
        row.candidates = candidates_json

    await db.commit()
    await db.refresh(row)
    return row
