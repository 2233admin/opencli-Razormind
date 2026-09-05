"""Seed a small, real SQLite OpenAlice workspace for browser acceptance.

The fixture intentionally persists the same entities the product reads: Studio
projects and runs, workflow evidence projections, native intelligence artifacts,
records, durable conversations, and one Inbox proposal.  It contains no model
credentials or external service configuration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

WORKSPACE_ID = "openalice-e2e-workspace"
USER_ID = "openalice-e2e-user"
# The bootstrap identity is the deterministic local administrator accepted by
# the existing auth boundary; membership is still checked by Workspace RBAC.
SUBJECT = "bootstrap-admin"

PROJECT_ALPHA = "openalice-project-alpha"
PROJECT_BETA = "openalice-project-beta"
WORKFLOW_ALPHA = "openalice-workflow-alpha"
WORKFLOW_BETA = "openalice-workflow-beta"
RUN_ALPHA_ONE = "openalice-run-alpha-1"
RUN_ALPHA_TWO = "openalice-run-alpha-2"
RUN_BETA_ONE = "openalice-run-beta-1"

SESSION_ALPHA_ONE = "openalice-session-alpha-1"
SESSION_ALPHA_TWO = "openalice-session-alpha-2"
SESSION_BETA_ONE = "openalice-session-beta-1"
ARTIFACT_ALPHA_ONE = "report-alpha-one"
ARTIFACT_ALPHA_TWO = "report-alpha-two"
ARTIFACT_BETA_ONE = "report-beta-one"
CONVERSATION_ALPHA_ACTIVE = "openalice-conversation-active"
CONVERSATION_ALPHA_CLOSED = "openalice-conversation-closed"
PROPOSAL_ID = "openalice-proposal-alpha"

BASE_TIME = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)


def artifact_public_id(session_id: str, artifact_id: str) -> str:
    return f"{session_id}:{artifact_id}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _projection(*, workflow_id: str, run_id: str, batch_id: str, updated_at: datetime) -> dict[str, Any]:
    timestamp = updated_at.isoformat()
    return {
        "workflowId": workflow_id,
        "runId": run_id,
        "traceId": f"trace:{run_id}",
        "valid": True,
        "status": "completed",
        "startedAt": timestamp,
        "updatedAt": timestamp,
        "eventCount": 1,
        "nodeStates": [
            {
                "nodeId": "persisted-output",
                "status": "completed",
                "sourceGroups": ["openalice-fixture"],
                "eventCount": 1,
                "batches": [
                    {
                        "batchId": batch_id,
                        "itemCount": 1,
                        "recordCount": 1,
                        "sourceGroup": "openalice-fixture",
                        "manifestUri": f"fixture://manifest/{batch_id}",
                        "odpRef": f"fixture://odp/{batch_id}",
                    }
                ],
            }
        ],
        "errors": [],
    }


async def seed_database(db_path: str | Path) -> dict[str, str]:
    """Create tables and persist the fixed workspace into *db_path*.

    Imports are delayed so the standalone backend runner can set DATABASE_URL,
    bootstrap auth, and a stable SECRET_KEY before any backend settings snapshot.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.database import Base
    from backend.models.agent_conversation import (
        AgentConversation,
        AgentConversationStatus,
        AgentConversationTurn,
        AgentConversationTurnStatus,
    )
    from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
    from backend.models.intelligence import (
        IntelligenceArtifact,
        IntelligenceSession,
        IntelligenceTransition,
    )
    from backend.models.operations_work_item import (
        OperationsWorkItem,
        Priority,
        Severity,
        WorkItemStatus,
        WorkItemType,
    )
    from backend.models.record import CollectedRecord
    from backend.models.source import DataSource
    from backend.models.studio import (
        StudioProject,
        StudioWorkflow,
        StudioWorkflowValidationRun,
        StudioWorkflowVersion,
        StudioWorkspace,
    )
    from backend.models.task import CollectionTask
    from backend.models.workflow_run import WorkflowRun
    from backend.workflow.native_intelligence_state import IntelligenceState

    db_file = Path(db_path).resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    engine = create_async_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        governed_workspace = Workspace(
            id=WORKSPACE_ID,
            name="OpenAlice E2E Workspace",
            slug="openalice-e2e-workspace",
        )
        studio_workspace = StudioWorkspace(
            id=WORKSPACE_ID,
            name="OpenAlice E2E Workspace",
            slug="openalice-e2e-studio",
        )
        user = User(
            id=USER_ID,
            subject=SUBJECT,
            email="openalice-e2e@example.test",
            display_name="OpenAlice E2E Admin",
        )
        db.add_all(
            [
                governed_workspace,
                studio_workspace,
                user,
                WorkspaceMembership(
                    workspace_id=WORKSPACE_ID,
                    user_id=USER_ID,
                    role=WorkspaceRole.ADMIN,
                ),
            ]
        )

        project_alpha = StudioProject(
            id=PROJECT_ALPHA,
            workspace_id=WORKSPACE_ID,
            name="Alpha persisted project",
            slug="openalice-alpha",
            description="Two real persisted runs for scoping checks.",
            app_type="agent",
            created_by_user_id=USER_ID,
        )
        project_beta = StudioProject(
            id=PROJECT_BETA,
            workspace_id=WORKSPACE_ID,
            name="Beta isolated project",
            slug="openalice-beta",
            description="A second project used to prove project scoping.",
            app_type="agent",
            created_by_user_id=USER_ID,
        )
        workflow_alpha = StudioWorkflow(
            id=WORKFLOW_ALPHA,
            project_id=PROJECT_ALPHA,
            name="Alpha workflow",
            description="Alpha workflow with two persisted runs.",
        )
        workflow_beta = StudioWorkflow(
            id=WORKFLOW_BETA,
            project_id=PROJECT_BETA,
            name="Beta workflow",
            description="Beta workflow owned by a different project.",
        )
        db.add_all([project_alpha, project_beta, workflow_alpha, workflow_beta])
        await db.flush()
        project_alpha.primary_workflow_id = WORKFLOW_ALPHA
        project_beta.primary_workflow_id = WORKFLOW_BETA

        validations = [
            StudioWorkflowValidationRun(
                id="openalice-validation-alpha",
                workflow_id=WORKFLOW_ALPHA,
                draft_revision=1,
                status="valid",
                valid=True,
                errors=[],
                warnings=[],
                compile_version="openalice-e2e-v1",
                resolved_graph={"nodes": [{"id": "persisted-output"}]},
            ),
            StudioWorkflowValidationRun(
                id="openalice-validation-beta",
                workflow_id=WORKFLOW_BETA,
                draft_revision=1,
                status="valid",
                valid=True,
                errors=[],
                warnings=[],
                compile_version="openalice-e2e-v1",
                resolved_graph={"nodes": [{"id": "persisted-output"}]},
            ),
        ]
        db.add_all(validations)
        await db.flush()
        versions = [
            StudioWorkflowVersion(
                id="openalice-version-alpha",
                workflow_id=WORKFLOW_ALPHA,
                version=1,
                draft_revision=1,
                graph={"nodes": [{"id": "persisted-output"}]},
                compile_version="openalice-e2e-v1",
                validation_run_id=validations[0].id,
                published_by_user_id=USER_ID,
                reason="OpenAlice E2E fixture",
            ),
            StudioWorkflowVersion(
                id="openalice-version-beta",
                workflow_id=WORKFLOW_BETA,
                version=1,
                draft_revision=1,
                graph={"nodes": [{"id": "persisted-output"}]},
                compile_version="openalice-e2e-v1",
                validation_run_id=validations[1].id,
                published_by_user_id=USER_ID,
                reason="OpenAlice E2E fixture",
            ),
        ]
        db.add_all(versions)
        await db.flush()
        workflow_alpha.current_published_version = 1
        workflow_beta.current_published_version = 1

        run_alpha_one = WorkflowRun(
            id=RUN_ALPHA_ONE,
            workflow_id=WORKFLOW_ALPHA,
            studio_workflow_version_id=versions[0].id,
            trace_id="openalice-trace-alpha-1",
            status="completed",
            request={"trigger": {"kind": "manual"}},
            projection=_projection(
                workflow_id=WORKFLOW_ALPHA,
                run_id=RUN_ALPHA_ONE,
                batch_id="batch-alpha-1",
                updated_at=BASE_TIME,
            ),
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
        run_alpha_two = WorkflowRun(
            id=RUN_ALPHA_TWO,
            workflow_id=WORKFLOW_ALPHA,
            studio_workflow_version_id=versions[0].id,
            trace_id="openalice-trace-alpha-2",
            status="completed",
            request={"trigger": {"kind": "manual"}},
            projection=_projection(
                workflow_id=WORKFLOW_ALPHA,
                run_id=RUN_ALPHA_TWO,
                batch_id="batch-alpha-2",
                updated_at=BASE_TIME + timedelta(minutes=2),
            ),
            created_at=BASE_TIME + timedelta(minutes=2),
            updated_at=BASE_TIME + timedelta(minutes=2),
        )
        run_beta_one = WorkflowRun(
            id=RUN_BETA_ONE,
            workflow_id=WORKFLOW_BETA,
            studio_workflow_version_id=versions[1].id,
            trace_id="openalice-trace-beta-1",
            status="completed",
            request={"trigger": {"kind": "manual"}},
            projection=_projection(
                workflow_id=WORKFLOW_BETA,
                run_id=RUN_BETA_ONE,
                batch_id="batch-beta-1",
                updated_at=BASE_TIME + timedelta(minutes=4),
            ),
            created_at=BASE_TIME + timedelta(minutes=4),
            updated_at=BASE_TIME + timedelta(minutes=4),
        )
        db.add_all([run_alpha_one, run_alpha_two, run_beta_one])
        await db.flush()

        artifact_rows = [
            (
                SESSION_ALPHA_ONE,
                ARTIFACT_ALPHA_ONE,
                RUN_ALPHA_ONE,
                {
                    "title": "Alpha run one report",
                    "media_type": "text/markdown",
                    "content": "# Alpha run one\n\nAlpha report body",
                    "summary": "Persisted Alpha run one output",
                },
                {"source": "openalice-e2e", "run_id": RUN_ALPHA_ONE, "conversation_id": CONVERSATION_ALPHA_ACTIVE},
                BASE_TIME,
            ),
            (
                SESSION_ALPHA_TWO,
                ARTIFACT_ALPHA_TWO,
                RUN_ALPHA_TWO,
                {
                    "title": "Alpha run two report",
                    "media_type": "text/markdown",
                    "content": "# Alpha run two\n\nClosed conversation output",
                },
                {"source": "openalice-e2e", "run_id": RUN_ALPHA_TWO, "conversation_id": CONVERSATION_ALPHA_CLOSED},
                BASE_TIME + timedelta(minutes=2),
            ),
            (
                SESSION_BETA_ONE,
                ARTIFACT_BETA_ONE,
                RUN_BETA_ONE,
                {
                    "title": "Beta isolated report",
                    "media_type": "text/markdown",
                    "content": "# Beta only\n\nBeta project output",
                },
                {"source": "openalice-e2e", "run_id": RUN_BETA_ONE},
                BASE_TIME + timedelta(minutes=4),
            ),
        ]
        for session_id, artifact_id, run_id, payload, provenance, created_at in artifact_rows:
            db.add(
                IntelligenceSession(
                    id=session_id,
                    created_by_run_id=run_id,
                    state=IntelligenceState.CREATED,
                    workflow_projection={},
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            db.add(
                IntelligenceArtifact(
                    id=f"row-{artifact_id}",
                    session_id=session_id,
                    artifact_id=artifact_id,
                    schema_version="intelligence.artifact.v1",
                    kind="report",
                    payload=payload,
                    simulated=False,
                    provenance=provenance,
                    algorithm_version="openalice-e2e-v1",
                    seed=7,
                    content_hash=_hash(json.dumps(payload, sort_keys=True)),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            db.add(
                IntelligenceTransition(
                    id=f"transition-{artifact_id}",
                    session_id=session_id,
                    sequence=1,
                    event_id=f"event-{artifact_id}",
                    command="research_complete",
                    from_state=IntelligenceState.CREATED.value,
                    to_state=IntelligenceState.CREATED.value,
                    request_hash=_hash(f"request:{artifact_id}"),
                    run_id=run_id,
                    node_id="persisted-output",
                    metadata_json={
                        "schema_version": "intelligence.transition.v1",
                        "artifact_ids": [artifact_id],
                    },
                    created_at=created_at,
                    updated_at=created_at,
                )
            )

        conversation_rows = [
            (
                CONVERSATION_ALPHA_ACTIVE,
                "Alpha active source conversation",
                AgentConversationStatus.ACTIVE.value,
                PROJECT_ALPHA,
                WORKFLOW_ALPHA,
                RUN_ALPHA_ONE,
                BASE_TIME,
                "仍在 Alpha run one 上处理",
            ),
            (
                CONVERSATION_ALPHA_CLOSED,
                "Alpha closed source conversation",
                AgentConversationStatus.CLOSED.value,
                PROJECT_ALPHA,
                WORKFLOW_ALPHA,
                RUN_ALPHA_TWO,
                BASE_TIME + timedelta(minutes=2),
                "Alpha run two 已关闭",
            ),
        ]
        for conversation_id, title, status, project_id, workflow_id, run_id, created_at, reply in conversation_rows:
            binding = {
                "studio_workspace_id": WORKSPACE_ID,
                "project_id": project_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "surface": "studio",
            }
            db.add(
                AgentConversation(
                    id=conversation_id,
                    workspace_id=WORKSPACE_ID,
                    title=title,
                    status=status,
                    created_by_user_id=USER_ID,
                    context_binding=binding,
                    revision=1,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            db.add(
                AgentConversationTurn(
                    id=f"turn-{conversation_id}",
                    conversation_id=conversation_id,
                    workspace_id=WORKSPACE_ID,
                    sequence=1,
                    request_id=f"request-{conversation_id}",
                    user_content="请解释这个运行结果。",
                    response={"type": "message", "content": reply},
                    context_binding=binding,
                    tool_trace=[],
                    status=AgentConversationTurnStatus.COMPLETED.value,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )

        db.add(
            OperationsWorkItem(
                id=PROPOSAL_ID,
                workspace_id=WORKSPACE_ID,
                type=WorkItemType.CHANGE_PROPOSAL.value,
                status=WorkItemStatus.OPEN.value,
                severity=Severity.LOW.value,
                priority=Priority.NORMAL.value,
                author_actor_type="agent",
                author_actor_id="openalice-deterministic-agent",
                evidence={
                    "title": "Alpha run one review proposal",
                    "conversation_id": CONVERSATION_ALPHA_ACTIVE,
                    "project_id": PROJECT_ALPHA,
                    "workflow_id": WORKFLOW_ALPHA,
                    "run_id": RUN_ALPHA_ONE,
                    "proposal_version": "openalice-e2e/v1",
                },
                reason="请确认 Alpha run one 的持久化分析结果。",
                proposal_id=PROPOSAL_ID,
                created_at=BASE_TIME,
                updated_at=BASE_TIME,
            )
        )

        sources = [
            DataSource(
                id="openalice-source-alpha",
                name="Alpha fixture source",
                channel_type="api",
                channel_config={"fixture": True},
                enabled=True,
            ),
            DataSource(
                id="openalice-source-beta",
                name="Beta fixture source",
                channel_type="api",
                channel_config={"fixture": True},
                enabled=True,
            ),
        ]
        db.add_all(sources)
        tasks = [
            CollectionTask(
                id="openalice-task-alpha-1",
                source_id=sources[0].id,
                trigger_type="manual",
                parameters={},
                status="completed",
            ),
            CollectionTask(
                id="openalice-task-alpha-2",
                source_id=sources[0].id,
                trigger_type="manual",
                parameters={},
                status="completed",
            ),
            CollectionTask(
                id="openalice-task-beta-1",
                source_id=sources[1].id,
                trigger_type="manual",
                parameters={},
                status="completed",
            ),
        ]
        db.add_all(tasks)
        db.add_all(
            [
                CollectedRecord(
                    id="openalice-record-alpha-1",
                    task_id=tasks[0].id,
                    source_id=sources[0].id,
                    workflow_id=WORKFLOW_ALPHA,
                    workflow_run_id=RUN_ALPHA_ONE,
                    lineage={"fixture": "openalice", "run_id": RUN_ALPHA_ONE},
                    raw_data={"title": "Alpha run one record", "scope": "alpha-1"},
                    normalized_data={"title": "Alpha run one record", "scope": "alpha-1"},
                    ai_enrichment={"summary": "Alpha one"},
                    content_hash=_hash("record:alpha:1"),
                    status="normalized",
                ),
                CollectedRecord(
                    id="openalice-record-alpha-2",
                    task_id=tasks[1].id,
                    source_id=sources[0].id,
                    workflow_id=WORKFLOW_ALPHA,
                    workflow_run_id=RUN_ALPHA_TWO,
                    lineage={"fixture": "openalice", "run_id": RUN_ALPHA_TWO},
                    raw_data={"title": "Alpha run two record", "scope": "alpha-2"},
                    normalized_data={"title": "Alpha run two record", "scope": "alpha-2"},
                    ai_enrichment={"summary": "Alpha two"},
                    content_hash=_hash("record:alpha:2"),
                    status="normalized",
                ),
                CollectedRecord(
                    id="openalice-record-beta-1",
                    task_id=tasks[2].id,
                    source_id=sources[1].id,
                    workflow_id=WORKFLOW_BETA,
                    workflow_run_id=RUN_BETA_ONE,
                    lineage={"fixture": "openalice", "run_id": RUN_BETA_ONE},
                    raw_data={"title": "Beta isolated record", "scope": "beta-1"},
                    normalized_data={"title": "Beta isolated record", "scope": "beta-1"},
                    ai_enrichment={"summary": "Beta"},
                    content_hash=_hash("record:beta:1"),
                    status="normalized",
                ),
            ]
        )
        await db.commit()

    await engine.dispose()
    return {
        "workspace_id": WORKSPACE_ID,
        "project_alpha": PROJECT_ALPHA,
        "project_beta": PROJECT_BETA,
        "workflow_alpha": WORKFLOW_ALPHA,
        "workflow_beta": WORKFLOW_BETA,
        "run_alpha_one": RUN_ALPHA_ONE,
        "run_alpha_two": RUN_ALPHA_TWO,
        "run_beta_one": RUN_BETA_ONE,
        "artifact_alpha_one": artifact_public_id(SESSION_ALPHA_ONE, ARTIFACT_ALPHA_ONE),
        "artifact_alpha_two": artifact_public_id(SESSION_ALPHA_TWO, ARTIFACT_ALPHA_TWO),
        "artifact_beta_one": artifact_public_id(SESSION_BETA_ONE, ARTIFACT_BETA_ONE),
        "conversation_active": CONVERSATION_ALPHA_ACTIVE,
        "conversation_closed": CONVERSATION_ALPHA_CLOSED,
        "proposal_id": PROPOSAL_ID,
    }


def _configure_environment(db_path: str | Path) -> None:
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{Path(db_path).resolve().as_posix()}",
            "API_AUTH_TOKEN": "",
            "BOOTSTRAP_ADMIN_TOKEN": "openalice-e2e-token",
            "SECRET_KEY": "openalice-e2e-secret-key-012345678901234567890123",
            "CREDENTIAL_ENCRYPTION_KEY": "",
            "WORKFLOW_PLUGINS": "",
            "TASK_EXECUTOR": "local",
        }
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tests.fixtures.openalice_workspace.seed DB_PATH")
    _configure_environment(sys.argv[1])
    result = asyncio.run(seed_database(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
