"""Validated, immutable fact joins for EvidenceBatch materialization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.models.iii_collection import (
    IIICollectionAttemptV1,
    IIICollectionCommandV1,
    IIICollectionExpectedKeyReportV1,
    IIICollectionIngressReceiptV1,
)
from backend.odp.query_client import OdpReconciliationDelegation, OdpRecordKey

_DELEGATION_TTL = timedelta(minutes=5)


def matches_scope(
    command: IIICollectionCommandV1,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    studio_workflow_version_id: str,
    run_id: str,
) -> bool:
    return (
        command.workspace_id == workspace_id
        and command.project_id == project_id
        and command.workflow_id == workflow_id
        and command.studio_workflow_version_id == studio_workflow_version_id
        and command.run_id == run_id
    )


def report_keys(
    report: IIICollectionExpectedKeyReportV1, command: IIICollectionCommandV1
) -> tuple[list[OdpRecordKey], bool]:
    try:
        expected = [
            OdpRecordKey(UUID(key["source_id"]), key["event_id"]) for key in report.expected_keys
        ]
        command_source = UUID(command.odp_source_id)
    except (KeyError, TypeError, ValueError):
        return [], True
    return expected, len(set(expected)) != len(expected) or any(
        key.source_id != command_source for key in expected
    )


def receipt_outcomes(
    receipts: list[IIICollectionIngressReceiptV1], keys: list[OdpRecordKey]
) -> dict[OdpRecordKey, str] | None:
    """Join complete signed receipts, preserving an observed duplicate outcome."""
    if not receipts:
        return None
    expected = {(str(key.source_id), key.event_id) for key in keys}
    result: dict[OdpRecordKey, str] = {}
    for receipt in receipts:
        outcomes = receipt.outcomes
        observed = {
            (item.get("source_id"), item.get("event_id"))
            for item in outcomes
            if isinstance(item, dict)
        }
        if len(outcomes) != len(expected) or observed != expected:
            return None
        for key in keys:
            outcome = next(
                (
                    item.get("outcome")
                    for item in outcomes
                    if (
                        item.get("source_id") == str(key.source_id)
                        and item.get("event_id") == key.event_id
                    )
                ),
                None,
            )
            if outcome not in {"accepted", "duplicate", "rejected"}:
                return None
            previous = result.get(key)
            if previous is not None and ("rejected" in {previous, outcome} and previous != outcome):
                return None
            result[key] = (
                "rejected"
                if outcome == "rejected"
                else "duplicate"
                if "duplicate" in {previous, outcome}
                else "accepted"
            )
    return result if len(result) == len(expected) else None


def delegation(
    command: IIICollectionCommandV1, attempt: IIICollectionAttemptV1, batch_id: str
) -> OdpReconciliationDelegation:
    return OdpReconciliationDelegation(
        workspace_id=command.workspace_id,
        project_id=command.project_id,
        workflow_id=command.workflow_id,
        run_id=command.run_id,
        batch_id=batch_id,
        attempt_id=attempt.id,
        task_id=UUID(attempt.task_id),
        trace_id=UUID(attempt.trace_id),
        allowed_source_ids=(UUID(command.odp_source_id),),
        allowed_modes=("exact", "attempt_page", "dlq"),
        expires_at=datetime.now(UTC) + _DELEGATION_TTL,
    )
