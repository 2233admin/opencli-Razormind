from __future__ import annotations

from backend.models.record import CollectedRecord
from backend.models.source import DataSource
from backend.models.studio import StudioProject, StudioWorkflow
from backend.models.task import CollectionTask
from backend.services import record_service


async def test_record_run_filter_stays_inside_project_and_workflow(db_session):
    project_a = StudioProject(
        id="record-project-a",
        workspace_id="record-workspace-a",
        name="Project A",
        slug="record-project-a",
        created_by_user_id="record-user",
    )
    project_b = StudioProject(
        id="record-project-b",
        workspace_id="record-workspace-b",
        name="Project B",
        slug="record-project-b",
        created_by_user_id="record-user",
    )
    workflow_a = StudioWorkflow(
        id="record-workflow-a",
        project_id=project_a.id,
        name="Workflow A",
    )
    workflow_b = StudioWorkflow(
        id="record-workflow-b",
        project_id=project_b.id,
        name="Workflow B",
    )
    source = DataSource(
        id="record-source",
        name="Record source",
        channel_type="rss",
        channel_config={},
    )
    task = CollectionTask(
        id="record-task",
        source_id=source.id,
        trigger_type="manual",
        parameters={},
    )
    db_session.add_all([project_a, project_b, workflow_a, workflow_b, source, task])
    await db_session.flush()
    db_session.add_all(
        [
            CollectedRecord(
                id="record-a",
                task_id=task.id,
                source_id=source.id,
                workflow_id=workflow_a.id,
                workflow_run_id="run-a",
                raw_data={"title": "A"},
                normalized_data={"title": "A"},
                content_hash="a" * 64,
                status="normalized",
            ),
            CollectedRecord(
                id="record-b",
                task_id=task.id,
                source_id=source.id,
                workflow_id=workflow_b.id,
                workflow_run_id="run-b",
                raw_data={"title": "B"},
                normalized_data={"title": "B"},
                content_hash="b" * 64,
                status="normalized",
            ),
            CollectedRecord(
                id="record-a-other-run",
                task_id=task.id,
                source_id=source.id,
                workflow_id=workflow_a.id,
                workflow_run_id="run-a-other",
                raw_data={"title": "A other"},
                normalized_data={"title": "A other"},
                content_hash="c" * 64,
                status="normalized",
            ),
        ]
    )
    await db_session.commit()

    records, total = await record_service.list_records(
        db_session,
        project_id=project_a.id,
        workflow_id=workflow_a.id,
        workflow_run_id="run-a",
    )
    assert total == 1
    assert [record.id for record in records] == ["record-a"]

    records, total = await record_service.list_records(
        db_session,
        project_id=project_a.id,
        workflow_id=workflow_b.id,
        workflow_run_id="run-b",
    )
    assert records == []
    assert total == 0
    records, total = await record_service.list_records(
        db_session,
        project_id=project_a.id,
        workflow_run_id="run-b",
    )
    assert records == []
    assert total == 0
