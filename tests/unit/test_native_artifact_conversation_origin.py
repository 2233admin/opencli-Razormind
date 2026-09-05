import pytest
from sqlalchemy import select

from backend.models.intelligence import IntelligenceArtifact
from backend.workflow.intelligence.research import build_research_artifact
from backend.workflow.intelligence_store import (
    IntelligenceArtifactInvariantError,
    IntelligenceStore,
)


def _artifact(*, conversation_id=None):
    artifact = build_research_artifact(
        session_id="origin-unit-session",
        input_items=[{"title": "Fixture", "url": "https://example.com"}],
        params={"now": "2026-09-06T00:00:00Z"},
        seed=7,
    )
    if conversation_id is None:
        return artifact
    return artifact.model_copy(
        update={
            "provenance": artifact.provenance.model_copy(
                update={"conversation_id": conversation_id}
            )
        }
    )


@pytest.mark.asyncio
async def test_central_writer_injects_only_server_conversation_context(db_session):
    store = IntelligenceStore(
        db_session,
        run_context={"conversation_id": "trusted-conversation"},
    )
    artifact = _artifact()

    await store._append_artifacts([artifact])
    row = await db_session.scalar(select(IntelligenceArtifact))

    assert row is not None
    assert artifact.provenance.conversation_id is None
    assert row.provenance["conversation_id"] == "trusted-conversation"


@pytest.mark.asyncio
async def test_central_writer_rejects_producer_declared_conversation(db_session):
    store = IntelligenceStore(db_session)

    with pytest.raises(IntelligenceArtifactInvariantError):
        await store._append_artifacts([_artifact(conversation_id="forged-conversation")])
