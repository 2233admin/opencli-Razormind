"""Unit tests for ai_processor pipeline step."""

import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.pipeline.ai_processor import process_with_ai
from backend.processors.base import ProcessingResult


@pytest.mark.asyncio
async def test_process_with_ai_no_config():
    records = [MagicMock()]
    result = await process_with_ai(records, None)
    # Should do nothing
    assert result == 0


@pytest.mark.asyncio
async def test_process_with_ai_no_records():
    result = await process_with_ai([], {"processor_type": "claude"})
    # Should do nothing
    assert result == 0


@pytest.mark.asyncio
async def test_process_with_ai_unknown_processor(caplog):
    records = [MagicMock()]
    # Should silently skip unknown processor (AUDIT C3: "silently" only in
    # the sense of not raising — it must now warn and report 0 enriched).
    with caplog.at_level(logging.WARNING):
        result = await process_with_ai(records, {"processor_type": "unknown_processor_xyz"})

    assert result == 0
    assert _logged(caplog, "unknown_processor_xyz")


@pytest.mark.asyncio
async def test_process_with_ai_enriches_records():
    records = [MagicMock(), MagicMock()]
    for r in records:
        r.ai_enrichment = None
        r.status = "normalized"

    enrichments = [{"summary": "Summary 1"}, {"summary": "Summary 2"}]
    mock_result = ProcessingResult(success=True, enrichments=enrichments)
    mock_processor = AsyncMock()
    mock_processor.process = AsyncMock(return_value=mock_result)

    with patch("backend.pipeline.ai_processor.get_processor", return_value=mock_processor):
        result = await process_with_ai(
            records, {"processor_type": "claude", "prompt_template": "Summarize: {{content}}"}
        )

    assert records[0].ai_enrichment == {"summary": "Summary 1"}
    assert records[1].ai_enrichment == {"summary": "Summary 2"}
    assert records[0].status == "ai_processed"
    assert result == 2


# ─── model-provider runtime PR-F (decision #9): DataSource.ai_config <-> ModelProvider ─────
# soft dual-track convergence at the ai_config -> processor-config seam.


def _session_cm(session):
    """Wrap an already-open (test-fixture) AsyncSession in the async context
    manager shape ``backend.database.AsyncSessionLocal()`` normally returns,
    so code under test that does ``async with AsyncSessionLocal() as
    session:`` transparently reuses the real ``db_session`` fixture instead
    of hitting the module-level production engine. Same pattern as
    ``tests/unit/worker/test_redbeat_sync.py``'s ``_session_cm`` helper."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _logged(caplog, substring: str) -> bool:
    return any(substring in r.getMessage() for r in caplog.records)


def _make_record():
    record = MagicMock()
    record.ai_enrichment = None
    record.status = "normalized"
    return record


def _mock_processor(enrichment: dict | None = None):
    mock_result = ProcessingResult(success=True, enrichments=[enrichment or {"summary": "ok"}])
    processor = AsyncMock()
    processor.process = AsyncMock(return_value=mock_result)
    return processor


@pytest.mark.asyncio
async def test_process_with_ai_provider_id_resolves(db_session):
    """ai_config.provider_id resolves to a real ModelProvider row: the
    resulting processor config carries the provider's
    api_key/base_url/model/provider_type, not whatever was inline."""
    from backend.models.provider import ModelProvider

    provider = ModelProvider(
        name="Governed Provider",
        provider_type="openai",
        base_url="https://provider.example.com/v1",
        api_key="sk-provider-secret",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    db_session.add(provider)
    await db_session.flush()

    records = [_make_record()]
    mock_processor = _mock_processor()

    ai_config = {
        "provider_id": provider.id,
        "processor_type": "claude",  # must be overridden by provider.provider_type
        "prompt_template": "Summarize: {{content}}",
    }

    with (
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
        patch("backend.pipeline.ai_processor.get_processor", return_value=mock_processor) as mock_get,
    ):
        await process_with_ai(records, ai_config, source_id="src-provider")

    mock_get.assert_called_once_with("openai")
    passed_config = mock_processor.process.call_args.kwargs["config"]
    assert passed_config["api_key"] == "sk-provider-secret"
    assert passed_config["base_url"] == "https://provider.example.com/v1"
    assert passed_config["model"] == "gpt-4o-mini"
    assert passed_config["processor_type"] == "openai"
    assert records[0].status == "ai_processed"


@pytest.mark.asyncio
async def test_process_with_ai_inline_only_is_byte_identical_and_warns(caplog):
    """No provider_id at all: the resolved processor config is untouched
    (byte-identical to pre-PR-F behavior) but a deprecation warning fires."""
    records = [_make_record()]
    mock_processor = _mock_processor()

    ai_config = {
        "processor_type": "openai",
        "api_key": "sk-inline-secret",
        "base_url": "https://inline.example.com/v1",
        "model": "gpt-4o-mini",
        "prompt_template": "Summarize: {{content}}",
    }
    original = dict(ai_config)

    with (
        patch("backend.pipeline.ai_processor.get_processor", return_value=mock_processor),
        caplog.at_level(logging.WARNING),
    ):
        await process_with_ai(records, ai_config, source_id="src-inline")

    passed_config = mock_processor.process.call_args.kwargs["config"]
    assert passed_config == original
    assert passed_config is ai_config  # same object — nothing copied/rebuilt
    assert _logged(caplog, "deprecated")


@pytest.mark.asyncio
async def test_process_with_ai_both_supplied_provider_id_wins(db_session, caplog):
    """provider_id AND inline api_key/base_url both present: provider_id
    wins outright, the inline fields are ignored, and a warning is logged."""
    from backend.models.provider import ModelProvider

    provider = ModelProvider(
        name="Winning Provider",
        provider_type="claude",
        base_url=None,
        api_key="sk-provider-wins",
        default_model="claude-sonnet-5",
        enabled=True,
    )
    db_session.add(provider)
    await db_session.flush()

    records = [_make_record()]
    mock_processor = _mock_processor()

    ai_config = {
        "provider_id": provider.id,
        "processor_type": "openai",
        "api_key": "sk-inline-loses",
        "base_url": "https://inline-loses.example.com",
        "model": "gpt-4o-mini",
        "prompt_template": "Summarize: {{content}}",
    }

    with (
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
        patch("backend.pipeline.ai_processor.get_processor", return_value=mock_processor),
        caplog.at_level(logging.WARNING),
    ):
        await process_with_ai(records, ai_config, source_id="src-both")

    passed_config = mock_processor.process.call_args.kwargs["config"]
    assert passed_config["api_key"] == "sk-provider-wins"
    assert passed_config["base_url"] is None
    assert passed_config["model"] == "claude-sonnet-5"
    assert passed_config["processor_type"] == "claude"
    assert _logged(caplog, "precedence")


@pytest.mark.asyncio
async def test_process_with_ai_provider_id_not_found_falls_back(db_session, caplog):
    """provider_id set but no such ModelProvider exists (deleted/bad id):
    warn, fall back to ai_config unchanged, never crash the pipeline."""
    records = [_make_record()]
    mock_processor = _mock_processor()

    ai_config = {
        "provider_id": "does-not-exist",
        "processor_type": "claude",
        "prompt_template": "Summarize: {{content}}",
    }
    original = dict(ai_config)

    with (
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
        patch("backend.pipeline.ai_processor.get_processor", return_value=mock_processor) as mock_get,
        caplog.at_level(logging.WARNING),
    ):
        await process_with_ai(records, ai_config, source_id="src-missing")

    mock_get.assert_called_once_with("claude")
    passed_config = mock_processor.process.call_args.kwargs["config"]
    assert passed_config == original
    assert _logged(caplog, "does not resolve")
    assert records[0].status == "ai_processed"


@pytest.mark.asyncio
async def test_process_with_ai_resolve_provider_false_skips_resolution(caplog):
    """resolve_provider=False (the agent_config path from
    backend.pipeline.runner's own ai_agents.provider_id merge) bypasses
    decision #9 entirely: no DB lookup, no deprecation warning, even though
    the dict carries inline api_key/base_url with no provider_id key — the
    exact shape that merge produces."""
    records = [_make_record()]
    mock_processor = _mock_processor()

    ai_config = {
        "processor_type": "claude",
        "api_key": "sk-agent-provider-key",
        "base_url": "https://agent.example.com",
        "prompt_template": "Summarize: {{content}}",
    }
    original = dict(ai_config)

    with (
        patch("backend.pipeline.ai_processor.get_processor", return_value=mock_processor),
        caplog.at_level(logging.WARNING),
    ):
        await process_with_ai(records, ai_config, source_id="src-agent", resolve_provider=False)

    passed_config = mock_processor.process.call_args.kwargs["config"]
    assert passed_config == original
    assert not _logged(caplog, "deprecated")
