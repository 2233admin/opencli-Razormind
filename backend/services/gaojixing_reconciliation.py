"""Server-only evidence for explicit Gaojixing checkpoint reconciliation."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SERVER_RECONCILIATIONS_KEY = "_serverGaojixingReconciliations"
RECONCILIATION_VERSION = "gaojixing.explicit-reconciliation.v1"
FORMAL_CHAT_URL = re.compile(r"^https://www\.doubao\.com/chat/\d+$")


def question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def read_managed_target_binding(
    project_root: Path,
    *,
    question_id: str,
    question: str,
) -> dict[str, str | None] | None:
    """Read the bounded v2 target journal without importing the runtime driver."""

    key = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    path = project_root / "logs" / "doubao-targets" / f"{key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 2
        or payload.get("questionId") != question_id
        or payload.get("questionSha256") != question_sha256(question)
        or payload.get("phase") not in {"target-owned", "submitted-formal"}
    ):
        return None
    target_id = str(payload.get("targetId") or "").strip()
    conversation_url = str(payload.get("conversationUrl") or "").strip() or None
    if not target_id:
        return None
    if payload["phase"] == "submitted-formal" and (
        conversation_url is None or not FORMAL_CHAT_URL.fullmatch(conversation_url)
    ):
        return None
    return {
        "target_id": target_id,
        "phase": str(payload["phase"]),
        "conversation_url": conversation_url,
    }


def build_reconciliation_record(
    *,
    governed_workspace_id: str,
    studio_workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    collection_run_id: str,
    question_id: str,
    question: str,
    target_id: str,
    expected_chat_url: str,
    actor_user_id: str,
    actor_subject: str,
    actor_auth_method: str,
) -> dict[str, Any]:
    return {
        "version": RECONCILIATION_VERSION,
        "source": "scoped-resume",
        "confirmedAt": datetime.now(UTC).isoformat(),
        "actor": {
            "userId": actor_user_id,
            "subject": actor_subject,
            "authMethod": actor_auth_method,
        },
        "scope": {
            "governedWorkspaceId": governed_workspace_id,
            "studioWorkspaceId": studio_workspace_id,
            "projectId": project_id,
            "workflowId": workflow_id,
        },
        "runId": run_id,
        "collectionRunId": collection_run_id,
        "questionId": question_id,
        "questionSha256": question_sha256(question),
        "targetId": target_id,
        "expectedChatUrl": expected_chat_url,
    }


def append_server_reconciliation(request: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(request)
    existing = updated.get(SERVER_RECONCILIATIONS_KEY, [])
    if not isinstance(existing, list) or any(not isinstance(item, dict) for item in existing):
        raise ValueError("Stored Gaojixing reconciliation audit is invalid")
    updated[SERVER_RECONCILIATIONS_KEY] = [*deepcopy(existing), deepcopy(record)]
    return updated


def preserve_server_reconciliations(
    existing_request: object,
    stored_request: dict[str, Any],
) -> dict[str, Any]:
    """Copy the server field only from the already-persisted database row."""

    result = dict(stored_request)
    result.pop(SERVER_RECONCILIATIONS_KEY, None)
    if isinstance(existing_request, dict) and SERVER_RECONCILIATIONS_KEY in existing_request:
        result[SERVER_RECONCILIATIONS_KEY] = deepcopy(existing_request[SERVER_RECONCILIATIONS_KEY])
    return result


def validated_driver_reconciliation(
    request: object,
    *,
    workflow_run_id: str,
    collection_run_id: str,
    question_id: str,
    question: str,
) -> dict[str, str] | None:
    """Return the latest exact server confirmation for the active checkpoint."""

    if not isinstance(request, dict):
        return None
    records = request.get(SERVER_RECONCILIATIONS_KEY)
    if not isinstance(records, list):
        return None
    expected_question_hash = question_sha256(question)
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        actor = record.get("actor")
        scope = record.get("scope")
        target_id = str(record.get("targetId") or "").strip()
        chat_url = str(record.get("expectedChatUrl") or "").strip()
        if (
            record.get("version") == RECONCILIATION_VERSION
            and record.get("source") == "scoped-resume"
            and isinstance(record.get("confirmedAt"), str)
            and bool(record["confirmedAt"].strip())
            and isinstance(actor, dict)
            and all(
                isinstance(actor.get(key), str) and bool(actor[key].strip())
                for key in ("userId", "subject", "authMethod")
            )
            and isinstance(scope, dict)
            and all(
                isinstance(scope.get(key), str) and bool(scope[key].strip())
                for key in (
                    "governedWorkspaceId",
                    "studioWorkspaceId",
                    "projectId",
                    "workflowId",
                )
            )
            and record.get("runId") == workflow_run_id
            and record.get("collectionRunId") == collection_run_id
            and record.get("questionId") == question_id
            and record.get("questionSha256") == expected_question_hash
            and bool(target_id)
            and FORMAL_CHAT_URL.fullmatch(chat_url)
        ):
            return {"target_id": target_id, "chat_url": chat_url}
    return None


__all__ = [
    "FORMAL_CHAT_URL",
    "SERVER_RECONCILIATIONS_KEY",
    "append_server_reconciliation",
    "build_reconciliation_record",
    "preserve_server_reconciliations",
    "read_managed_target_binding",
    "validated_driver_reconciliation",
]
