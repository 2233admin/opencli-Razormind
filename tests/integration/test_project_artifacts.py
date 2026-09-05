from __future__ import annotations

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from backend.api.v1 import project_artifacts as artifact_api
from backend.database import get_db
from backend.models.agent_conversation import AgentConversation
from backend.models.intelligence import (
    IntelligenceArtifact,
    IntelligenceSession,
    IntelligenceTransition,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas.workflow_runtime import WorkflowRunProjection
from backend.security.identity import RequestIdentity, get_request_identity
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
    db_session.add(
        IntelligenceTransition(
            session_id=session_id,
            sequence=1,
            event_id=f"transition-event-{artifact_id}",
            command="research_complete",
            from_state=IntelligenceState.CREATED.value,
            to_state=IntelligenceState.CREATED.value,
            request_hash="0" * 64,
            run_id=run_id,
            node_id="test-node",
            metadata_json={
                "schema_version": "intelligence.transition.v1",
                "artifact_ids": [artifact_id],
            },
        )
    )
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
    assert listed.data[0].id == f"{first.session_id}:{first.artifact_id}"
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


async def test_project_artifact_uses_transition_run_for_reused_sessions(db_session):
    scope = await create_scoped_run(db_session)
    await _persist_artifact(
        db_session,
        run_id=scope["run"].id,
        session_id="reused-artifact-session",
        artifact_id="artifact-original-run",
        payload={"title": "original"},
    )
    later_run = WorkflowRun(
        id="iii-reused-later-run",
        workflow_id=scope["workflow"].id,
        studio_workflow_version_id=scope["version"].id,
        trace_id="iii-reused-later-trace",
        status="completed",
        request={},
        projection={},
    )
    db_session.add(later_run)
    await db_session.flush()
    later_artifact = _artifact(
        session_id="reused-artifact-session",
        artifact_id="artifact-later-run",
        payload={"title": "later"},
        run_id=later_run.id,
    )
    db_session.add_all(
        [
            later_artifact,
            IntelligenceTransition(
                session_id="reused-artifact-session",
                sequence=2,
                event_id="transition-event-artifact-later-run",
                command="research_complete",
                from_state=IntelligenceState.CREATED.value,
                to_state=IntelligenceState.CREATED.value,
                request_hash="1" * 64,
                run_id=later_run.id,
                node_id="test-node",
                metadata_json={
                    "schema_version": "intelligence.transition.v1",
                    "artifact_ids": [later_artifact.artifact_id],
                },
            ),
        ]
    )
    await db_session.commit()

    listed = await artifact_api.list_project_artifacts(
        db=db_session,
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=None,
        run_id=later_run.id,
        offset=0,
        limit=100,
        identity=RequestIdentity(subject="iii-test-operator"),
    )
    assert [item.artifact_id for item in listed.data] == [later_artifact.artifact_id]
    assert listed.data[0].run_id == later_run.id


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


async def test_project_artifact_lists_ordinary_workflow_evidence_batches(db_session):
    scope = await create_scoped_run(db_session)
    evidence_run = WorkflowRun(
        id="iii-evidence-run",
        workflow_id=scope["workflow"].id,
        studio_workflow_version_id=scope["version"].id,
        trace_id="iii-evidence-trace",
        status="completed",
        request={},
        projection=WorkflowRunProjection(
            workflowId=scope["workflow"].id,
            runId="iii-evidence-run",
            traceId="iii-evidence-trace",
            valid=True,
            status="completed",
            startedAt="2026-09-06T00:00:00+00:00",
            updatedAt="2026-09-06T00:00:01+00:00",
            eventCount=1,
            nodeStates=[
                {
                    "nodeId": "evidence-node",
                    "status": "completed",
                    "batches": [
                        {
                            "batchId": "ordinary-batch",
                            "itemCount": 2,
                            "recordCount": 2,
                            "sourceGroup": "ordinary-source",
                            "manifestUri": "odp://manifest/ordinary-batch",
                            "odpRef": "odp://ordinary-batch",
                        }
                    ],
                }
            ],
        ).model_dump(by_alias=True),
    )
    db_session.add(evidence_run)
    await db_session.commit()

    identity = RequestIdentity(subject="iii-test-operator")
    base = {
        "db": db_session,
        "workspace_id": scope["workspace"].id,
        "project_id": scope["project"].id,
        "workflow_id": scope["workflow"].id,
        "identity": identity,
    }
    listed = await artifact_api.list_project_artifacts(
        **base,
        run_id=evidence_run.id,
        offset=0,
        limit=100,
    )
    assert [item.id for item in listed.data] == ["evidence-batch:ordinary-batch"]
    assert listed.data[0].source == "workflow_run_projection"
    assert listed.data[0].session_id is None

    detail = await artifact_api.read_project_artifact(
        **base,
        artifact_id="evidence-batch:ordinary-batch",
        run_id=evidence_run.id,
    )
    assert detail.data.content["batch"]["batchId"] == "ordinary-batch"
    assert detail.data.provenance["run_id"] == evidence_run.id


async def test_project_artifact_conversation_provenance_is_run_scoped_and_fail_closed(
    db_session,
):
    scope = await create_scoped_run(db_session)
    artifact = await _persist_artifact(
        db_session,
        run_id=scope["run"].id,
        session_id="artifact-session-conversation",
        artifact_id="artifact-conversation",
        payload={"title": "conversation scoped"},
    )
    other_workflow = scope["workflow"].__class__(
        id="iii-other-workflow",
        project_id=scope["project"].id,
        name="Other workflow",
    )
    artifact.provenance = {
        "source": "test-persisted-output",
        "run_id": scope["run"].id,
        "conversation_id": "conversation-bound-to-other-workflow",
    }
    db_session.add_all(
        [
            other_workflow,
            AgentConversation(
                id="conversation-bound-to-other-workflow",
                workspace_id=scope["workspace"].id,
                created_by_user_id="iii-test-operator",
                context_binding={
                    "studio_workspace_id": scope["workspace"].id,
                    "project_id": scope["project"].id,
                    "workflow_id": other_workflow.id,
                },
            ),
        ]
    )
    await db_session.commit()

    listed = await artifact_api.list_project_artifacts(
        db=db_session,
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=None,
        run_id=None,
        offset=0,
        limit=100,
        identity=RequestIdentity(subject="iii-test-operator"),
    )
    row = next(item for item in listed.data if item.artifact_id == artifact.artifact_id)
    assert row.conversation_id is None

    run_mismatch = await _persist_artifact(
        db_session,
        run_id=scope["run"].id,
        session_id="artifact-session-run-mismatch",
        artifact_id="artifact-run-mismatch",
        payload={"title": "run scoped"},
    )
    run_mismatch.provenance = {
        "source": "test-persisted-output",
        "run_id": scope["run"].id,
        "conversation_id": "conversation-bound-to-other-run",
    }
    db_session.add(
        AgentConversation(
            id="conversation-bound-to-other-run",
            workspace_id=scope["workspace"].id,
            created_by_user_id="iii-test-operator",
            context_binding={
                "studio_workspace_id": scope["workspace"].id,
                "project_id": scope["project"].id,
                "workflow_id": scope["workflow"].id,
                "run_id": "iii-other-run",
            },
        )
    )
    await db_session.commit()
    listed = await artifact_api.list_project_artifacts(
        db=db_session,
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=None,
        run_id=None,
        offset=0,
        limit=100,
        identity=RequestIdentity(subject="iii-test-operator"),
    )
    row = next(item for item in listed.data if item.artifact_id == run_mismatch.artifact_id)
    assert row.conversation_id is None

    scope["run"].request = {}
    artifact.provenance = {
        "source": "test-persisted-output",
        "run_id": scope["run"].id,
        "conversation_id": "conversation-a",
        "conversationId": "conversation-b",
    }
    db_session.add_all(
        [
            AgentConversation(
                id="conversation-a",
                workspace_id=scope["workspace"].id,
                created_by_user_id="iii-test-operator",
                context_binding={
                    "studio_workspace_id": scope["workspace"].id,
                    "project_id": scope["project"].id,
                    "workflow_id": scope["workflow"].id,
                    "run_id": scope["run"].id,
                },
            ),
            AgentConversation(
                id="conversation-b",
                workspace_id=scope["workspace"].id,
                created_by_user_id="iii-test-operator",
                context_binding={
                    "studio_workspace_id": scope["workspace"].id,
                    "project_id": scope["project"].id,
                    "workflow_id": scope["workflow"].id,
                    "run_id": scope["run"].id,
                },
            ),
        ]
    )
    await db_session.commit()
    listed = await artifact_api.list_project_artifacts(
        db=db_session,
        workspace_id=scope["workspace"].id,
        project_id=scope["project"].id,
        workflow_id=scope["workflow"].id,
        run_id=scope["run"].id,
        offset=0,
        limit=100,
        identity=RequestIdentity(subject="iii-test-operator"),
    )
    row = next(item for item in listed.data if item.artifact_id == artifact.artifact_id)
    assert row.conversation_id is None


async def test_project_artifact_http_route_rejects_unauthorized_identity(db_session):
    scope = await create_scoped_run(db_session)
    test_app = FastAPI()
    test_app.include_router(artifact_api.router, prefix="/api/v1")

    async def override_db():
        yield db_session

    async def override_identity() -> RequestIdentity:
        return RequestIdentity(subject="outside-workspace")

    test_app.dependency_overrides[get_db] = override_db
    test_app.dependency_overrides[get_request_identity] = override_identity
    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}/artifacts"
            )
        assert response.status_code == 403
    finally:
        test_app.dependency_overrides.clear()
