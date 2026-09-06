import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.gaojixing_collection import (
    GaojixingCollectionRun,
    GaojixingQuestionCheckpoint,
)
from backend.services.gaojixing_collection_service import ensure_collection, resume_collection
from backend.workflow.gaojixing_collection_runner import run_collection_job
from backend.workflow.gaojixing_doubao_driver import DoubaoDriverUnavailableError
from backend.workflow.managed_gaojixing_question_batches import stage_managed_question_batch
from tests.unit.test_gaojixing_collection_service import _workflow_run


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (DoubaoDriverUnavailableError("doubao-ask-failed"), "doubao-ask-failed"),
    (RuntimeError("private-browser-output-secret"), "doubao-capture-failed"),
    (DoubaoDriverUnavailableError("private-browser-output-secret"), "doubao-capture-failed"),
])
async def test_capture_failure_is_durable_sanitized_and_recovery_never_resubmits(
    db_engine, tmp_path, error, expected,
):
    run_id = "diagnostic-run"
    signing_key = "diagnostic-test-signing-key"
    staged = stage_managed_question_batch(
        json.dumps({
            "phase1": [{"id": "G0001", "question": "One question"}], "phase2": [],
        }).encode(),
        filename="questions.json", run_id=run_id,
        storage_root=tmp_path, signing_key=signing_key,
    )
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    calls = {"collect": 0, "inspect": 0}

    class FailedDriver:
        def __init__(self, _root):
            pass

        async def preflight(self):
            return None

        async def collect(self, **_kwargs):
            calls["collect"] += 1
            raise error

        async def inspect_current(self, **_kwargs):
            calls["inspect"] += 1
            return None

    async with sessions() as db:
        db.add(_workflow_run(run_id))
        job = await ensure_collection(
            db, workflow_run_id=run_id, node_id="batch::tool",
            question_batch_ref=staged.question_batch_ref,
            storage_root=tmp_path, signing_key=signing_key, dispatch=lambda _id: None,
        )
        job_id = job.id
        await db.commit()

    async def execute():
        return await run_collection_job(
            job_id, session_factory=sessions, driver_factory=FailedDriver,
            schedule_resume=lambda _id: None,
            storage_root=tmp_path, signing_key=signing_key,
        )

    assert await execute() == "waiting_reconciliation"
    async with sessions() as db:
        stored = await db.get(GaojixingCollectionRun, job_id)
        checkpoint = await db.scalar(select(GaojixingQuestionCheckpoint).where(
            GaojixingQuestionCheckpoint.collection_run_id == job_id,
        ))
        assert stored.failure == checkpoint.failure == {"code": expected}
        assert stored.status == checkpoint.status == "waiting_reconciliation"
        assert checkpoint.attempt == 1
        await resume_collection(db, job_id=job_id, dispatch=lambda _id: None)
        await db.commit()

    assert await execute() == "waiting_reconciliation"
    assert calls == {"collect": 1, "inspect": 1}
    async with sessions() as db:
        stored = await db.get(GaojixingCollectionRun, job_id)
        assert stored.failure == {"code": "doubao-answer-not-proven"}
