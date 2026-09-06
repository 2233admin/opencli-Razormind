import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import get_settings
from backend.main import app
from backend.models.gaojixing_collection import (
    GaojixingCollectionRunStatus,
    GaojixingQuestionCheckpoint,
    GaojixingQuestionStatus,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.studio import StudioProject, StudioWorkflow, StudioWorkspace
from backend.models.workflow_run import WorkflowRun
from backend.schemas.workflow import WorkflowRunStartRequest
from backend.schemas.workflow_runtime import WorkflowRunProjection
from backend.security.identity import RequestIdentity
from backend.services.gaojixing_collection_service import (
    GaojixingReconciliationAuthorization,
    ensure_collection,
    resume_collection,
)
from backend.services.gaojixing_reconciliation import (
    SERVER_RECONCILIATIONS_KEY,
    build_reconciliation_record,
    question_sha256,
    validated_driver_reconciliation,
)
from backend.workflow.managed_gaojixing_question_batches import stage_managed_question_batch
from tests.fixtures.workflow_conformance import workflow_conformance_project

QUESTION_ID = "G0001"
QUESTION = "第一道非品牌题"
CHAT_URL = "https://www.doubao.com/chat/38440436182332418"


def _one_question_bank() -> bytes:
    return json.dumps(
        {"phase1": [{"id": QUESTION_ID, "question": QUESTION}], "phase2": []},
        ensure_ascii=False,
    ).encode()


def _projection(run_id: str, workflow_id: str) -> dict:
    return WorkflowRunProjection(
        workflowId=workflow_id,
        runId=run_id,
        traceId=f"trace-{run_id}",
        valid=True,
        status="waiting",
        startedAt="2026-09-06T00:00:00Z",
        updatedAt="2026-09-06T00:00:01Z",
        eventCount=0,
    ).model_dump(mode="json")


def _write_target_journal(project_root, *, target_id: str = "target-1") -> None:
    key = hashlib.sha256(QUESTION_ID.encode()).hexdigest()
    path = project_root / "logs" / "doubao-targets" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "questionId": QUESTION_ID,
                "questionSha256": question_sha256(QUESTION),
                "targetId": target_id,
                "phase": "target-owned",
                "conversationUrl": None,
            }
        ),
        encoding="utf-8",
    )


async def _seed_waiting_collection(
    session: AsyncSession,
    tmp_path,
    monkeypatch,
    *,
    run_id: str,
    role: WorkspaceRole = WorkspaceRole.OPERATOR,
):
    signing_key = "reconciliation-test-key"
    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    monkeypatch.setattr(get_settings(), "secret_key", signing_key)
    staged = stage_managed_question_batch(
        _one_question_bank(),
        filename="questions.json",
        run_id=run_id,
        storage_root=tmp_path,
        signing_key=signing_key,
    )
    actor = User(id=f"user-{run_id}", subject=f"{run_id}@example.test")
    governed = Workspace(id=f"governed-{run_id}", name="Governed", slug=f"gov-{run_id}")
    studio = StudioWorkspace(id=f"studio-{run_id}", name="Studio", slug=f"studio-{run_id}")
    project = StudioProject(
        id=f"project-{run_id}",
        workspace_id=studio.id,
        name="Project",
        slug=f"project-{run_id}",
        app_type="workflow",
        created_by_user_id=actor.id,
    )
    workflow = StudioWorkflow(
        id=f"workflow-{run_id}",
        project_id=project.id,
        name="Workflow",
    )
    workflow_run = WorkflowRun(
        id=run_id,
        workflow_id=workflow.id,
        trace_id=f"trace-{run_id}",
        status="waiting",
        valid=True,
        request={
            "project": workflow_conformance_project(),
            "input": {"payload": {"questionBatchRef": staged.question_batch_ref}},
        },
        projection=_projection(run_id, workflow.id),
    )
    session.add_all([actor, governed, studio, project, workflow, workflow_run])
    await session.flush()
    session.add(WorkspaceMembership(workspace_id=governed.id, user_id=actor.id, role=role))
    job = await ensure_collection(
        session,
        workflow_run_id=run_id,
        node_id="batch::tool",
        question_batch_ref=staged.question_batch_ref,
        storage_root=tmp_path,
        signing_key=signing_key,
        dispatch=lambda _job_id: None,
    )
    checkpoint = await session.scalar(
        select(GaojixingQuestionCheckpoint).where(
            GaojixingQuestionCheckpoint.collection_run_id == job.id
        )
    )
    assert checkpoint is not None
    job.status = GaojixingCollectionRunStatus.WAITING_RECONCILIATION.value
    job.current_question_id = QUESTION_ID
    job.waiting_kind = "reconciliation"
    checkpoint.status = GaojixingQuestionStatus.WAITING_RECONCILIATION.value
    _write_target_journal(tmp_path / "runs" / run_id)
    await session.flush()
    return {
        "actor": actor,
        "governed": governed,
        "studio": studio,
        "project": project,
        "workflow": workflow,
        "workflow_run": workflow_run,
        "job": job,
        "checkpoint": checkpoint,
        "signing_key": signing_key,
    }


def _authorization(seeded) -> GaojixingReconciliationAuthorization:
    return GaojixingReconciliationAuthorization(
        expected_chat_url=CHAT_URL,
        governed_workspace_id=seeded["governed"].id,
        studio_workspace_id=seeded["studio"].id,
        project_id=seeded["project"].id,
        workflow_id=seeded["workflow"].id,
        actor_user_id=seeded["actor"].id,
        actor_subject=seeded["actor"].subject,
        actor_auth_method="local",
    )


def _resume_path(seeded) -> str:
    return (
        f"/api/v1/workspaces/{seeded['studio'].id}/projects/{seeded['project'].id}"
        f"/workflows/{seeded['workflow'].id}/runs/{seeded['workflow_run'].id}"
        "/gaojixing/resume"
    )


@pytest.mark.asyncio
async def test_actual_scoped_resume_route_records_authenticated_operator_confirmation(
    client, db_session, tmp_path, monkeypatch
):
    from backend.api.v1 import studio_workflows

    seeded = await _seed_waiting_collection(
        db_session, tmp_path, monkeypatch, run_id="run-api-reconcile"
    )
    identity = RequestIdentity(
        subject=seeded["actor"].subject,
        is_platform_admin=True,
        auth_method="local",
    )
    app.dependency_overrides[studio_workflows._optional_request_identity] = lambda: identity

    response = await client.post(
        _resume_path(seeded),
        json={"expectedChatUrl": CHAT_URL},
    )

    assert response.status_code == 202, response.text
    await db_session.refresh(seeded["workflow_run"])
    record = seeded["workflow_run"].request[SERVER_RECONCILIATIONS_KEY][-1]
    assert record["actor"]["subject"] == seeded["actor"].subject
    assert record["expectedChatUrl"] == CHAT_URL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_kind", "role", "expected_status"),
    [
        ("missing", WorkspaceRole.OPERATOR, 401),
        ("viewer", WorkspaceRole.VIEWER, 403),
    ],
)
async def test_actual_scoped_resume_route_rejects_unauthenticated_or_unprivileged_confirmation(
    client,
    db_session,
    tmp_path,
    monkeypatch,
    identity_kind,
    role,
    expected_status,
):
    from backend.api.v1 import studio_workflows

    seeded = await _seed_waiting_collection(
        db_session,
        tmp_path,
        monkeypatch,
        run_id=f"run-api-{identity_kind}",
        role=role,
    )
    identity = (
        None
        if identity_kind == "missing"
        else RequestIdentity(
            subject=seeded["actor"].subject,
            is_platform_admin=True,
            auth_method="local",
        )
    )
    app.dependency_overrides[studio_workflows._optional_request_identity] = lambda: identity

    response = await client.post(
        _resume_path(seeded),
        json={"expectedChatUrl": CHAT_URL},
    )

    assert response.status_code == expected_status, response.text
    await db_session.refresh(seeded["workflow_run"])
    assert SERVER_RECONCILIATIONS_KEY not in seeded["workflow_run"].request
    assert seeded["job"].status == GaojixingCollectionRunStatus.WAITING_RECONCILIATION.value


@pytest.mark.asyncio
async def test_actual_scoped_resume_route_rejects_cross_scope_and_client_audit_fields(
    client, db_session, tmp_path, monkeypatch
):
    from backend.api.v1 import studio_workflows

    seeded = await _seed_waiting_collection(
        db_session, tmp_path, monkeypatch, run_id="run-api-scope"
    )
    identity = RequestIdentity(
        subject=seeded["actor"].subject,
        is_platform_admin=True,
        auth_method="local",
    )
    app.dependency_overrides[studio_workflows._optional_request_identity] = lambda: identity
    other_studio = StudioWorkspace(
        id="studio-other-api-scope", name="Other Studio", slug="other-api-scope"
    )
    db_session.add(other_studio)
    await db_session.flush()

    cross_scope_path = _resume_path(seeded).replace(seeded["studio"].id, other_studio.id, 1)
    cross_scope = await client.post(
        cross_scope_path,
        json={"expectedChatUrl": CHAT_URL},
    )
    injected_audit = await client.post(
        _resume_path(seeded),
        json={
            "expectedChatUrl": CHAT_URL,
            "actor": {"subject": "attacker@example.test"},
        },
    )

    assert cross_scope.status_code == 404, cross_scope.text
    assert injected_audit.status_code == 422, injected_audit.text
    await db_session.refresh(seeded["workflow_run"])
    assert SERVER_RECONCILIATIONS_KEY not in seeded["workflow_run"].request


class _ReconciliationDriver:
    def __init__(self, project_root, *, capture: bool):
        self.project_root = project_root
        self.capture = capture
        self.collected: list[str] = []
        self.reconciliations: list[dict[str, str] | None] = []

    async def preflight(self) -> None:
        return None

    async def collect(self, *, question_id: str, question: str) -> dict:
        self.collected.append(question_id)
        raise AssertionError("reconciliation must never submit the question again")

    async def inspect_current(
        self,
        *,
        question_id: str,
        question: str,
        reconciliation: dict[str, str] | None = None,
    ) -> dict | None:
        self.reconciliations.append(reconciliation)
        if not self.capture:
            return None
        screenshot_files = []
        for suffix in ("01_顶部", "02_正文", "03_底部"):
            relative = f"screenshots/{question_id}_{suffix}.png"
            path = self.project_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(suffix.encode())
            screenshot_files.append(relative)
        return {
            "id": question_id,
            "question": question,
            "has_brand": False,
            "status": "completed",
            "chat_url": CHAT_URL,
            "answer": f"完整回答：{question}",
            "collected_at": "2026-09-06T00:00:00Z",
            "page_modules": {
                "keywords": "页面未显示",
                "ref_links": "页面未显示",
                "product_links": "页面未显示",
                "video_links": "页面未显示",
                "followups": ["还有哪些注意事项？"],
            },
            "brand_observation": {
                "target": "高吉星",
                "appeared": False,
                "positions": [],
                "natural_recommendation": False,
                "basis": "页面回答和已显示模块未出现高吉星",
            },
            "page_evidence": {
                "screenshot_files": screenshot_files,
                "share_link": {
                    "displayed": True,
                    "copy_control_displayed": True,
                    "capture_method": "share-copy-control",
                    "url": "https://www.doubao.com/thread/reconciliation-proof",
                },
                "module_expectations": {
                    name: {
                        "displayed": name == "followups",
                        "expected_count": 1 if name == "followups" else 0,
                    }
                    for name in (
                        "keywords",
                        "ref_links",
                        "product_links",
                        "video_links",
                        "followups",
                    )
                },
                "screenshot_coverage": {"top": True, "answer": True, "bottom": True},
            },
            "required_missing": [],
        }


@pytest.mark.asyncio
async def test_explicit_resume_records_server_audit_and_worker_only_inspects_confirmed_url(
    db_engine, tmp_path, monkeypatch
):
    from backend.workflow.gaojixing_collection_runner import run_collection_job

    run_id = "run-reconcile-worker"
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        seeded = await _seed_waiting_collection(session, tmp_path, monkeypatch, run_id=run_id)
        await resume_collection(
            session,
            job_id=seeded["job"].id,
            reconciliation=_authorization(seeded),
            dispatch=lambda _job_id: None,
        )
        job_id = seeded["job"].id
        await session.commit()
        await session.refresh(seeded["workflow_run"])
        record = seeded["workflow_run"].request[SERVER_RECONCILIATIONS_KEY][-1]
        assert record["actor"] == {
            "userId": seeded["actor"].id,
            "subject": seeded["actor"].subject,
            "authMethod": "local",
        }
        assert record["scope"] == {
            "governedWorkspaceId": seeded["governed"].id,
            "studioWorkspaceId": seeded["studio"].id,
            "projectId": seeded["project"].id,
            "workflowId": seeded["workflow"].id,
        }
        assert record["runId"] == run_id
        assert record["collectionRunId"] == job_id
        assert record["questionId"] == QUESTION_ID
        assert record["questionSha256"] == question_sha256(QUESTION)
        assert record["targetId"] == "target-1"
        assert record["expectedChatUrl"] == CHAT_URL

    drivers = []

    def driver_factory(attempt_root):
        driver = _ReconciliationDriver(attempt_root, capture=True)
        drivers.append(driver)
        return driver

    resumed = []
    outcome = await run_collection_job(
        job_id,
        session_factory=sessions,
        driver_factory=driver_factory,
        schedule_resume=resumed.append,
        storage_root=tmp_path,
        signing_key=seeded["signing_key"],
    )

    assert outcome == "workflow_resume_scheduled"
    assert drivers[0].collected == []
    assert drivers[0].reconciliations == [{"target_id": "target-1", "chat_url": CHAT_URL}]
    assert resumed == [run_id]


@pytest.mark.asyncio
async def test_bodyless_resume_does_not_claim_matching_historical_target(
    db_engine, tmp_path, monkeypatch
):
    from backend.workflow.gaojixing_collection_runner import run_collection_job

    run_id = "run-reconcile-legacy"
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        seeded = await _seed_waiting_collection(session, tmp_path, monkeypatch, run_id=run_id)
        await resume_collection(
            session,
            job_id=seeded["job"].id,
            dispatch=lambda _job_id: None,
        )
        job_id = seeded["job"].id
        await session.commit()

    drivers = []

    def driver_factory(attempt_root):
        driver = _ReconciliationDriver(attempt_root, capture=False)
        drivers.append(driver)
        return driver

    outcome = await run_collection_job(
        job_id,
        session_factory=sessions,
        driver_factory=driver_factory,
        schedule_resume=lambda _run_id: None,
        storage_root=tmp_path,
        signing_key=seeded["signing_key"],
    )

    assert outcome == "waiting_reconciliation"
    assert drivers[0].collected == []
    assert drivers[0].reconciliations == [None]


def test_worker_rejects_confirmation_for_another_run_or_question():
    record = build_reconciliation_record(
        governed_workspace_id="workspace-1",
        studio_workspace_id="studio-1",
        project_id="project-1",
        workflow_id="workflow-1",
        run_id="run-1",
        collection_run_id="collection-1",
        question_id=QUESTION_ID,
        question=QUESTION,
        target_id="target-1",
        expected_chat_url=CHAT_URL,
        actor_user_id="user-1",
        actor_subject="operator@example.test",
        actor_auth_method="oidc",
    )
    request = {SERVER_RECONCILIATIONS_KEY: [record]}

    assert (
        validated_driver_reconciliation(
            request,
            workflow_run_id="run-2",
            collection_run_id="collection-1",
            question_id=QUESTION_ID,
            question=QUESTION,
        )
        is None
    )
    assert (
        validated_driver_reconciliation(
            request,
            workflow_run_id="run-1",
            collection_run_id="collection-1",
            question_id=QUESTION_ID,
            question="同题编号但文本已变化",
        )
        is None
    )


@pytest.mark.asyncio
async def test_store_workflow_run_preserves_only_existing_server_confirmation(db_session):
    from backend.workflow.opencli_hda_tracer import _store_workflow_run

    run_id = "run-preserve-reconciliation"
    original = {"actor": {"subject": "trusted-original"}}
    row = WorkflowRun(
        id=run_id,
        workflow_id="workflow-preserve",
        trace_id=f"trace-{run_id}",
        status="waiting",
        valid=True,
        request={SERVER_RECONCILIATIONS_KEY: [original]},
        projection=_projection(run_id, "workflow-preserve"),
    )
    db_session.add(row)
    await db_session.flush()
    incoming = WorkflowRunStartRequest.model_validate(
        {
            "project": workflow_conformance_project(),
            SERVER_RECONCILIATIONS_KEY: [{"actor": {"subject": "attacker"}}],
        }
    )
    projection = WorkflowRunProjection.model_validate(_projection(run_id, "workflow-preserve"))

    await _store_workflow_run(
        run_id,
        request=incoming,
        projection=projection,
        events=[],
        session=db_session,
    )
    await db_session.flush()
    await db_session.refresh(row)

    assert row.request[SERVER_RECONCILIATIONS_KEY] == [original]
    assert "attacker" not in json.dumps(row.request)
