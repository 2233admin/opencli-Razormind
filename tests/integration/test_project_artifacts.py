from __future__ import annotations

from fastapi import HTTPException

from backend.api.v1 import project_artifacts as artifact_api
from backend.models.intelligence import IntelligenceArtifact, IntelligenceSession
from backend.models.workflow_run import WorkflowRun
from backend.security.identity import RequestIdentity
from backend.workflow.native_intelligence_state import IntelligenceState
from tests.integration.iii_collection_test_support import create_scoped_run


def _artifact(
    *, session_id: str, artifact_id: str, payload: dict, run_id: str
) -> IntelligenceArtifact:
    return IntelligenceArtifact(
        id=f"row-{artifact_id}",
        session_id=session_id,
        artifact_id=artifact_id,
        schema_version="intelligence.artifact.v1",
        kind="report",
        payload=payload,
        simulated=True,
        provenance={
            "source": "test-persisted-output",
            "evidence_artifact_ids": [],
            "run_id": run_id,
        },
        algorithm_version="test-v1",
        seed=7,
        content_hash=(artifact_id.encode().hex() + "0" * 64)[:64],
    )


async def _persist_artifact(
    db_session, *, run_id: str, session_id: str, artifact_id: str, payload: dict
):
    db_session.add(
        IntelligenceSession(
            id=session_id,
            created_by_run_id=run_id,
            state=IntelligenceState.CREATED,
            workflow_projection={},
        )
    )
    row = _artifact(
        session_id=session_id,
        artifact_id=artifact_id,
        payload=payload,
        run_id=run_id,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_project_artifact_reads_are_run_scoped_and_authorized(db_session):
    scope = await create_scoped_run(db_session)
    first = await _persist_artifact(
        db_session,
        run_id=scope["run"].id,
        session_id="artifact-session-a",
        artifact_id="artifact-a",
        payload={"schema": "intelligence.report.v1", "title": "Run A", "answer": "persisted"},
    )

    second_run = WorkflowRun(
        id="iii-run-b",
        workflow_id=scope["workflow"].id,
        studio_workflow_version_id=scope["version"].id,
        trace_id="iii-trace-b",
        status="completed",
        request={},
        projection={},
    )
    db_session.add(second_run)
    await db_session.commit()
    await _persist_artifact(
        db_session,
        run_id=second_run.id,
        session_id="artifact-session-b",
        artifact_id="artifact-b",
        payload={"schema": "intelligence.report.v1", "title": "Run B", "answer": "other"},
    )

    identity = RequestIdentity(subject="iii-test-operator")
    base = {
        "db": db_session,
        "workspace_id": scope["workspace"].id,
        "project_id": scope["project"].id,
        "workflow_id": None,
        "identity": identity,
    }
    listed = await artifact_api.list_project_artifacts(
        **base,
        run_id=scope["run"].id,
        offset=0,
        limit=100,
    )
    assert [item.artifact_id for item in listed.data] == [first.artifact_id]
    assert listed.data[0].run_id == scope["run"].id

    detail = await artifact_api.read_project_artifact(
        **base,
        artifact_id=first.artifact_id,
        run_id=scope["run"].id,
    )
    assert detail.data.content["answer"] == "persisted"
    assert detail.data.payload == detail.data.content
    assert detail.data.content_hash == first.content_hash
    assert detail.data.provenance["source"] == "test-persisted-output"
    assert detail.data.conversation_id is None

    try:
        await artifact_api.read_project_artifact(
            **base,
            artifact_id=first.artifact_id,
            run_id=second_run.id,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
    else:  # pragma: no cover
        raise AssertionError("an artifact from another run must stay unreadable")


async def test_project_artifact_rejects_unauthorized_and_missing_or_oversized_reads(
    db_session,
):
    scope = await create_scoped_run(db_session)
    await _persist_artifact(
        db_session,
        run_id=scope["run"].id,
        session_id="artifact-session-secure",
        artifact_id="artifact-secure",
        payload={"title": "secure", "body": "x"},
    )
    base = {
        "db": db_session,
        "workspace_id": scope["workspace"].id,
        "project_id": scope["project"].id,
        "workflow_id": None,
    }

    try:
        await artifact_api.list_project_artifacts(
            **base,
            offset=0,
            limit=100,
            identity=RequestIdentity(subject="outside-workspace"),
        )
    except HTTPException as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover - assertion makes a failed auth an explicit test failure
        raise AssertionError("artifact listing must require workspace membership")

    try:
        await artifact_api.read_project_artifact(
            **base,
            artifact_id="does-not-exist",
            run_id=None,
            identity=RequestIdentity(subject="iii-test-operator"),
        )
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "artifact_not_found"
    else:  # pragma: no cover
        raise AssertionError("missing artifact must be a scoped not-found")

    large = await _persist_artifact(
        db_session,
        run_id=scope["run"].id,
        session_id="artifact-session-large",
        artifact_id="artifact-large",
        payload={"body": "x" * (1_048_576 + 1)},
    )
    try:
        await artifact_api.read_project_artifact(
            **base,
            artifact_id=large.artifact_id,
            run_id=scope["run"].id,
            identity=RequestIdentity(subject="iii-test-operator"),
        )
    except HTTPException as exc:
        assert exc.status_code == 413
        assert exc.detail == "artifact_content_too_large"
    else:  # pragma: no cover
        raise AssertionError("oversized artifact bodies must be rejected")
