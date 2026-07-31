"""Unit tests for ProviderModel (model-provider runtime PR-A, decision #3): the model catalog
table, its real FK to ModelProvider (ondelete CASCADE — unlike
AIAgent.provider_id, which stays a loose string per decision #9), the
(provider_id, model_id) uniqueness constraint, and the Pydantic schemas'
closed-set validation for model_type/source.

Follows the DB-fixture pattern from tests/unit/security/test_provider_key_*
(db_engine -> own sessionmaker -> `async with sm() as session`).
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.provider import ModelProvider
from backend.models.provider_model import ProviderModel
from backend.schemas.provider_model import ProviderModelCreate, ProviderModelRead


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_provider(session, name="Test Provider") -> ModelProvider:
    provider = ModelProvider(name=name, provider_type="openai", enabled=True)
    session.add(provider)
    await session.flush()
    return provider


# ---------------------------------------------------------------------------
# Unique constraint: (provider_id, model_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_provider_id_model_id_pair_raises_integrity_error(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        provider = await _make_provider(session)
        session.add(ProviderModel(
            provider_id=provider.id, model_id="gpt-4o", source="manual",
        ))
        await session.commit()

    async with sm() as session:
        provider = (await session.execute(
            select(ModelProvider)
        )).scalars().first()
        session.add(ProviderModel(
            provider_id=provider.id, model_id="gpt-4o", source="manual",
        ))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_same_provider_different_model_id_is_fine(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        provider = await _make_provider(session)
        session.add(ProviderModel(
            provider_id=provider.id, model_id="gpt-4o", source="manual",
        ))
        session.add(ProviderModel(
            provider_id=provider.id, model_id="gpt-4o-mini", source="manual",
        ))
        await session.commit()
        provider_id = provider.id

    async with sm() as session:
        rows = (await session.execute(
            select(ProviderModel).where(ProviderModel.provider_id == provider_id)
        )).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_same_model_id_different_provider_is_fine(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        p1 = await _make_provider(session, name="Provider A")
        p2 = await _make_provider(session, name="Provider B")
        session.add(ProviderModel(provider_id=p1.id, model_id="shared-model", source="manual"))
        session.add(ProviderModel(provider_id=p2.id, model_id="shared-model", source="manual"))
        await session.commit()

    async with sm() as session:
        rows = (await session.execute(
            select(ProviderModel).where(ProviderModel.model_id == "shared-model")
        )).scalars().all()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# Defaults / column shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defaults_model_type_llm_source_manual_enabled_true(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        provider = await _make_provider(session)
        pm = ProviderModel(provider_id=provider.id, model_id="gpt-4o", source="manual")
        session.add(pm)
        await session.commit()
        pm_id = pm.id

    async with sm() as session:
        loaded = (await session.execute(
            select(ProviderModel).where(ProviderModel.id == pm_id)
        )).scalar_one()
    assert loaded.model_type == "llm"
    assert loaded.enabled is True
    assert loaded.capabilities is None


@pytest.mark.asyncio
async def test_capabilities_json_round_trips(db_engine):
    sm = _sessionmaker(db_engine)
    caps = {"tools": True, "vision": False, "context_window": 200000}
    async with sm() as session:
        provider = await _make_provider(session)
        pm = ProviderModel(
            provider_id=provider.id, model_id="claude-sonnet-5", source="discovered",
            capabilities=caps,
        )
        session.add(pm)
        await session.commit()
        pm_id = pm.id

    async with sm() as session:
        loaded = (await session.execute(
            select(ProviderModel).where(ProviderModel.id == pm_id)
        )).scalar_one()
    assert loaded.capabilities == caps


# ---------------------------------------------------------------------------
# FK cascade: deleting a ModelProvider cascade-deletes its provider_models
# rows (real FK, ondelete=CASCADE — decision #3). SQLite does not enforce
# FK constraints by default (matches this repo's production database.py,
# which never issues `PRAGMA foreign_keys=ON`), so this test explicitly
# turns enforcement on for its own session/connection to prove the
# ondelete=CASCADE clause baked into the migration/model actually works at
# the DB level when enforcement is active. Documented limitation: outside
# tests, this repo's runtime engine does not enable the pragma, so today
# nothing relies on DB-level cascade actually firing in production sqlite —
# PR-C/E service code must not assume it (delete provider_models explicitly
# if that ever matters), same caveat table.py's own FK columns are under.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_provider_cascade_deletes_provider_models(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        provider = await _make_provider(session)
        provider_id = provider.id
        session.add(ProviderModel(provider_id=provider_id, model_id="gpt-4o", source="manual"))
        session.add(ProviderModel(provider_id=provider_id, model_id="gpt-4o-mini", source="manual"))
        await session.commit()

        rows_before = (await session.execute(
            select(ProviderModel).where(ProviderModel.provider_id == provider_id)
        )).scalars().all()
        assert len(rows_before) == 2

        await session.delete(provider)
        await session.commit()

        rows_after = (await session.execute(
            select(ProviderModel).where(ProviderModel.provider_id == provider_id)
        )).scalars().all()
    assert rows_after == []


# ---------------------------------------------------------------------------
# Pydantic schema closed-set validation (model_type / source)
# ---------------------------------------------------------------------------


def test_provider_model_create_accepts_valid_model_type_and_source():
    payload = ProviderModelCreate(
        provider_id="p1", model_id="gpt-4o", model_type="llm", source="manual",
    )
    assert payload.model_type == "llm"
    assert payload.source == "manual"


def test_provider_model_create_accepts_discovered_source():
    payload = ProviderModelCreate(
        provider_id="p1", model_id="gpt-4o", source="discovered",
    )
    assert payload.source == "discovered"


def test_provider_model_create_rejects_invalid_model_type():
    with pytest.raises(ValidationError):
        ProviderModelCreate(provider_id="p1", model_id="gpt-4o", model_type="embedding")


def test_provider_model_create_rejects_invalid_source():
    with pytest.raises(ValidationError):
        ProviderModelCreate(provider_id="p1", model_id="gpt-4o", source="synced")


def test_provider_model_read_from_attributes(db_engine):
    # ProviderModelRead just needs from_attributes wiring; exercised via a
    # plain namespace rather than a DB round-trip (that's covered above).
    from datetime import datetime, timezone

    class _Row:
        id = "pm-1"
        provider_id = "p-1"
        model_id = "gpt-4o"
        model_type = "llm"
        capabilities = {"tools": True}
        source = "manual"
        enabled = True
        created_at = datetime.now(timezone.utc)

    read = ProviderModelRead.model_validate(_Row())
    assert read.id == "pm-1"
    assert read.model_type == "llm"
