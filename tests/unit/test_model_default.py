"""Unit tests for ModelDefault (model-provider runtime PR-A, decision #4): the per-role
system default candidates table (role UNIQUE) and the Pydantic schemas'
closed-set validation for role.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.model_default import ModelDefault
from backend.schemas.model_default import ModelDefaultPut, ModelDefaultRead


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# role uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_role_raises_integrity_error(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        session.add(ModelDefault(role="chat", candidates=[{"provider_id": "p1", "model_id": "m1"}]))
        await session.commit()

    async with sm() as session:
        session.add(ModelDefault(role="chat", candidates=[{"provider_id": "p2", "model_id": "m2"}]))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_distinct_roles_are_fine(db_engine):
    sm = _sessionmaker(db_engine)
    async with sm() as session:
        session.add(ModelDefault(role="chat", candidates=[]))
        session.add(ModelDefault(role="executor", candidates=[]))
        session.add(ModelDefault(role="enrichment", candidates=[]))
        await session.commit()

    async with sm() as session:
        rows = (await session.execute(select(ModelDefault))).scalars().all()
    assert {r.role for r in rows} == {"chat", "executor", "enrichment"}


@pytest.mark.asyncio
async def test_candidates_ordering_round_trips(db_engine):
    """candidates[0] is the primary pick, the rest are failover order — the
    JSON column must preserve list order through a round trip."""
    sm = _sessionmaker(db_engine)
    ordered = [
        {"provider_id": "primary-provider", "model_id": "primary-model"},
        {"provider_id": "backup-provider", "model_id": "backup-model"},
    ]
    async with sm() as session:
        md = ModelDefault(role="chat", candidates=ordered)
        session.add(md)
        await session.commit()
        md_id = md.id

    async with sm() as session:
        loaded = (await session.execute(
            select(ModelDefault).where(ModelDefault.id == md_id)
        )).scalar_one()
    assert loaded.candidates == ordered


# ---------------------------------------------------------------------------
# Pydantic schema closed-set validation (role)
# ---------------------------------------------------------------------------


def test_model_default_put_accepts_each_valid_role():
    for role in ("chat", "executor", "enrichment"):
        payload = ModelDefaultPut(role=role, candidates=[])
        assert payload.role == role


def test_model_default_put_accepts_candidates_list():
    payload = ModelDefaultPut(
        role="chat",
        candidates=[
            {"provider_id": "p1", "model_id": "m1"},
            {"provider_id": "p2", "model_id": "m2"},
        ],
    )
    assert len(payload.candidates) == 2
    assert payload.candidates[0].provider_id == "p1"


def test_model_default_put_rejects_invalid_role():
    with pytest.raises(ValidationError):
        ModelDefaultPut(role="summarizer", candidates=[])


def test_model_default_read_from_attributes():
    from datetime import datetime, timezone

    class _Row:
        id = "md-1"
        role = "chat"
        candidates = [{"provider_id": "p1", "model_id": "m1"}]
        created_at = datetime.now(timezone.utc)
        updated_at = datetime.now(timezone.utc)

    read = ModelDefaultRead.model_validate(_Row())
    assert read.role == "chat"
    assert read.candidates == [{"provider_id": "p1", "model_id": "m1"}]
