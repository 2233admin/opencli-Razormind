"""Govern one real Gaojixing question from an Agent-confirmed proposal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models.studio import StudioWorkflowVersion
from backend.models.workflow_run import WorkflowRun
from backend.schemas import workflow as workflow_schemas
from backend.security.identity import RequestIdentity
from backend.services.studio_workflow_runtime import (
    get_published_workflow_version,
    published_run_id,
    start_published_version_run,
)
from backend.workflow.gaojixing_doubao_driver import (
    DoubaoDriverUnavailableError,
    OpenCLIDoubaoEvidenceDriver,
    _default_command_runner,
)
from backend.workflow.managed_gaojixing_question_batches import (
    accepts_managed_question_batch,
    cleanup_managed_question_batch,
    stage_managed_question_batch,
)
from backend.workflow.opencli_hda_tracer import start_workflow_run
from backend.workflow.plugin_registry import build_workflow_plugin_registry


def _validated_question(question: object) -> str:
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Question is required")
    normalized = question.replace("\u00a0", " ").strip()
    if len(normalized) > 1_000:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Question exceeds the 1000-character limit",
        )
    return normalized


def _single_question_bank(question: str) -> bytes:
    if "高吉星" in question:
        document = {"phase1": [], "phase2": [{"id": "B001", "question": question}]}
    else:
        document = {"phase1": [{"id": "G0001", "question": question}], "phase2": []}
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _login_state(rows: list[dict[str, Any]]) -> str:
    """Classify the observed OpenCLI Login field without inferring from its URL."""

    states = [row["Login"] for row in rows if "Login" in row]
    if any(value is True for value in states):
        return "ready"
    normalized = {str(value).strip().lower() for value in states}
    if normalized & {"false", "logged out", "not logged in", "login required"}:
        return "not_ready"
    if normalized & {"unknown", "indeterminate"}:
        return "unknown"
    return "missing"


async def preflight_managed_doubao_runtime() -> None:
    """Probe session availability while preserving an indeterminate login state."""

    settings = get_settings()
    captured_status: list[dict[str, Any]] = []

    async def capture_status(command: list[str], endpoint: str):
        result = await _default_command_runner(command, endpoint)
        if len(command) > 1 and command[1] == "status":
            captured_status.extend(result[1])
        return result

    storage_parent = Path(settings.gaojixing_run_storage_path).resolve().parent
    if not storage_parent.is_dir():
        raise DoubaoDriverUnavailableError("gaojixing-storage-parent-unavailable")
    driver = OpenCLIDoubaoEvidenceDriver(
        project_root=storage_parent,
        command_runner=capture_status,
    )
    await driver.preflight()
    # OpenCLI 1.8.7 reports ``Unknown`` for its new authenticated DOM.  The
    # driver has already proved a usable Doubao session in that case; retain
    # the uncertainty without turning a URL alone into a login assertion.
    if _login_state(captured_status) not in {"ready", "unknown"}:
        raise DoubaoDriverUnavailableError("doubao-login-required")


async def require_managed_doubao_version(
    db: AsyncSession,
    *,
    studio_workspace_id: str,
    project_id: str,
    workflow_id: str,
    expected_published_version: int,
    check_runtime: bool = True,
) -> StudioWorkflowVersion:
    """Bind one action to the current immutable, permitted Gaojixing graph."""

    version = await get_published_workflow_version(
        db,
        workspace_id=studio_workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    if version.version != expected_published_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published workflow version changed")
    project = workflow_schemas.WorkflowProject.model_validate(version.graph)
    if not accepts_managed_question_batch(project):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Managed Doubao questions require the governed Gaojixing workflow packages",
        )
    permissions = project.agentPermissions
    if not permissions.canFetchNetwork or not bool(
        getattr(permissions, "canMutateExternalSites", False)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Managed Doubao collection requires network and external-site permissions",
        )
    if permissions.canSendNotifications:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Managed Doubao chat runs must disable notifications",
        )
    if check_runtime:
        try:
            await preflight_managed_doubao_runtime()
        except DoubaoDriverUnavailableError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Managed Doubao runtime is not ready: {exc.code}",
            ) from exc
    return version


async def start_managed_doubao_question(
    db: AsyncSession,
    *,
    governed_workspace_id: str,
    studio_workspace_id: str,
    project_id: str,
    workflow_id: str,
    expected_published_version: int,
    question: str,
    identity: RequestIdentity,
    actor_user_id: str,
    conversation_id: str,
    action_execution_id: str,
) -> tuple[WorkflowRun, object]:
    """Stage and start exactly one question with trusted conversation provenance."""

    # Imported at execution time because the stored Studio resolver delegates
    # governed-workspace lookup back to Agent Control.
    from backend.services.workflow_conversation_origin import (
        resolve_workflow_conversation_origin,
    )

    normalized_question = _validated_question(question)
    version = await require_managed_doubao_version(
        db,
        studio_workspace_id=studio_workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        expected_published_version=expected_published_version,
        check_runtime=True,
    )
    origin = await resolve_workflow_conversation_origin(
        db,
        identity,
        conversation_id=conversation_id,
        studio_workspace_id=studio_workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        governed_workspace_id=governed_workspace_id,
    )
    idempotency_key = f"agent-control:{action_execution_id}"
    run_id = published_run_id(
        workspace_id=studio_workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        version_id=version.id,
        idempotency_key=idempotency_key,
    )
    assert run_id is not None
    staged = stage_managed_question_batch(
        _single_question_bank(normalized_question),
        filename="managed-one-question.json",
        run_id=run_id,
    )
    try:
        response = await start_published_version_run(
            db=db,
            workspace_id=studio_workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            version=version,
            run_input=workflow_schemas.WorkflowRunInput(
                payload={"questionBatchRef": staged.question_batch_ref},
                source="agent",
                sourceId=actor_user_id,
            ),
            user=actor_user_id,
            request_id=action_execution_id,
            response_mode="async",
            idempotency_key=idempotency_key,
            run_id=run_id,
            conversation_origin=origin,
            plugins=build_workflow_plugin_registry(get_settings()),
            start_runner=start_workflow_run,
        )
    except Exception:
        if staged.created and await db.get(WorkflowRun, run_id) is None:
            cleanup_managed_question_batch(
                staged.question_batch_ref,
                expected_run_id=run_id,
            )
        raise
    row = await db.get(WorkflowRun, run_id)
    if row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Managed Doubao workflow run was not persisted",
        )
    return row, response.data


__all__ = [
    "preflight_managed_doubao_runtime",
    "require_managed_doubao_version",
    "start_managed_doubao_question",
]
