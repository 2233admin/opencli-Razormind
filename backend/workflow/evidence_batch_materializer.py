"""Immutable, scoped ODP evidence-batch materialization.

This module is deliberately the only Admin path that joins the durable III
attempt ledger to ODP reconciliation.  It never receives an ODP predicate from
a browser and never treats ingress acceptance or a page scan as finality.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.iii_collection import (
    EvidenceBatchMaterializationEventV1,
    EvidenceBatchMaterializationManifestV1,
    IIICollectionAttemptV1,
    IIICollectionCommandV1,
    IIICollectionExpectedKeyReportV1,
    IIICollectionIngressReceiptV1,
)
from backend.odp.query_client import (
    OdpQueryError,
    OdpRecordKey,
    build_attempt_page_request,
    build_dlq_request,
    build_exact_request,
    post_reconciliation_query,
)
from backend.schemas.evidence_manifest import (
    ResearchGraphV2ItemKey,
    ResearchGraphV2ManifestRef,
    ResearchGraphV2RecordRef,
    record_ref_set_hash,
)
from backend.schemas.iii_collection import (
    EvidenceBatchMaterializationReadV1,
    EvidenceBatchMaterializationSummaryV1,
    EvidenceBatchRecordReferenceV1,
)
from backend.workflow.evidence_batch_materialization_facts import (
    delegation,
    matches_scope,
    receipt_outcomes,
    report_keys,
)
from backend.workflow.iii_collection_store import CollectionScope, IIICollectionNotFoundError
from backend.workflow.workflow_run_events import lock_scoped_workflow_run

_MAX_QUERY_KEYS = 100
_MAX_RECORD_REFERENCES = 1000


def _canonical_hash(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _batch_id(command: IIICollectionCommandV1, attempt: IIICollectionAttemptV1) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            (
                f"opencli-admin/workflow/{command.workflow_id}/run/{command.run_id}/"
                f"batch/{attempt.task_id}"
            ),
        )
    )


def _legacy_status(materialization_status: str) -> str:
    return {
        "awaiting_final_report": "running",
        "reconciling": "running",
        "completed": "completed",
        "completed_empty": "completed",
        "partial": "partial",
        "failed_definitive": "failed",
        "indeterminate": "blocked",
    }[materialization_status]


def _recovery_action(status: str) -> str:
    return (
        "none"
        if status in {"completed", "completed_empty", "partial", "failed_definitive"}
        else "reconcile_evidence_batch"
    )


def _reference(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": value["source_id"],
        "event_id": value["event_id"],
        "odp_record_id": value["odp_record_id"],
        "committed_at": value["committed_at"],
    }


def _research_graph_manifest_ref(
    manifest: EvidenceBatchMaterializationManifestV1,
    report: IIICollectionExpectedKeyReportV1 | None,
) -> ResearchGraphV2ManifestRef | None:
    if (
        getattr(manifest, "materialization_status", None)
        not in {"completed", "completed_empty", "partial"}
        or getattr(manifest, "version", None) != "v1"
        or getattr(manifest, "derivation", None) != "dispatch-task-v1"
        or getattr(manifest, "expected_key_set_hash", None) is None
        or report is None
        or report.report_id != getattr(manifest, "report_id", None)
        or report.command_id != getattr(manifest, "command_id", None)
        or report.attempt_id != getattr(manifest, "attempt_id", None)
        or report.key_set_sha256 != getattr(manifest, "expected_key_set_hash", None)
    ):
        return None
    expected_key_set_hash = manifest.expected_key_set_hash
    if expected_key_set_hash is None:
        return None
    record_refs = [
        ResearchGraphV2RecordRef(
            source_id=str(item.get("source_id", item.get("sourceId", ""))),
            event_id=str(item.get("event_id", item.get("eventId", ""))),
            odp_record_id=int(item.get("odp_record_id", item.get("odpRecordId", 0))),
        )
        for item in manifest.record_references
    ]
    materialization_status = cast(
        Literal["completed", "completed_empty", "partial"], manifest.materialization_status
    )
    excluded_item_keys: list[ResearchGraphV2ItemKey] = []
    if materialization_status == "partial":
        present = {(ref.source_id, ref.event_id) for ref in record_refs}
        expected_item_keys = {
            (
                str(item.get("source_id", item.get("sourceId", ""))),
                str(item.get("event_id", item.get("eventId", ""))),
            )
            for item in report.expected_keys
        }
        excluded_item_keys = [
            ResearchGraphV2ItemKey(source_id=source_id, event_id=event_id)
            for source_id, event_id in sorted(expected_item_keys - present)
        ]
    return ResearchGraphV2ManifestRef(
        batch_id=manifest.batch_id,
        derivation=cast(Literal["dispatch-task-v1"], manifest.derivation),
        reconciliation_revision=manifest.reconciliation_revision,
        manifest_schema_version=cast(Literal["v1"], manifest.version),
        manifest_hash=manifest.manifest_hash,
        expected_record_key_set_hash=expected_key_set_hash,
        record_ref_set_hash=record_ref_set_hash(manifest.record_references),
        materialization_status=materialization_status,
        record_refs=record_refs,
        excluded_item_keys=excluded_item_keys,
    )


async def _read(
    db: AsyncSession,
    manifest: EvidenceBatchMaterializationManifestV1,
    report: IIICollectionExpectedKeyReportV1 | None = None,
) -> EvidenceBatchMaterializationReadV1:
    status = manifest.materialization_status
    if (
        report is None
        and status in {"completed", "completed_empty", "partial"}
        and manifest.report_id is not None
    ):
        report = (
            await db.execute(
                select(IIICollectionExpectedKeyReportV1).where(
                    IIICollectionExpectedKeyReportV1.report_id == manifest.report_id
                )
            )
        ).scalar_one_or_none()
    return EvidenceBatchMaterializationReadV1(
        batch_id=manifest.batch_id,
        reconciliation_revision=manifest.reconciliation_revision,
        materialization_status=status,
        legacy_status=_legacy_status(status),
        item_count=manifest.item_count,
        record_count=int(manifest.counts.get("record_present", 0)),
        manifest_hash=manifest.manifest_hash,
        counts={name: int(manifest.counts.get(name, 0)) for name in _COUNT_NAMES},
        record_references=[
            EvidenceBatchRecordReferenceV1(**value) for value in manifest.record_references
        ],
        blocker=None if status in _TERMINAL else manifest.finalization_reason,
        recovery_action=_recovery_action(status),
        query_fingerprint=manifest.query_fingerprint,
        page_snapshot_as_of=manifest.page_snapshot_as_of,
        redaction_profile_version=manifest.redaction_profile_version,
        finalized_at=manifest.finalized_at,
        research_graph_manifest_ref=_research_graph_manifest_ref(manifest, report),
    )


_COUNT_NAMES = (
    "expected",
    "record_present",
    "inserted",
    "duplicate_existing",
    "rejected",
    "dlq",
    "unknown",
)
_TERMINAL = {"completed", "completed_empty", "partial", "failed_definitive"}


async def get_materialization(
    db: AsyncSession, *, scope: CollectionScope, command_id: str
) -> EvidenceBatchMaterializationReadV1 | None:
    """Return only the latest immutable projection for a fully scoped command."""
    manifest = (
        await db.execute(
            select(EvidenceBatchMaterializationManifestV1)
            .where(
                EvidenceBatchMaterializationManifestV1.command_id == command_id,
                EvidenceBatchMaterializationManifestV1.workspace_id == scope.workspace_id,
                EvidenceBatchMaterializationManifestV1.project_id == scope.project_id,
                EvidenceBatchMaterializationManifestV1.workflow_id == scope.workflow_id,
                EvidenceBatchMaterializationManifestV1.studio_workflow_version_id
                == scope.studio_workflow_version_id,
                EvidenceBatchMaterializationManifestV1.run_id == scope.run_id,
            )
            .order_by(EvidenceBatchMaterializationManifestV1.reconciliation_revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return await _read(db, manifest) if manifest is not None else None


def _scope_filters(scope: CollectionScope) -> tuple[Any, ...]:
    manifest = EvidenceBatchMaterializationManifestV1
    return (
        manifest.workspace_id == scope.workspace_id,
        manifest.project_id == scope.project_id,
        manifest.workflow_id == scope.workflow_id,
        manifest.studio_workflow_version_id == scope.studio_workflow_version_id,
        manifest.run_id == scope.run_id,
    )


def _summary(row: Any) -> EvidenceBatchMaterializationSummaryV1:
    status = row.materialization_status
    return EvidenceBatchMaterializationSummaryV1(
        batch_id=row.batch_id,
        reconciliation_revision=row.reconciliation_revision,
        materialization_status=status,
        legacy_status=_legacy_status(status),
        item_count=row.item_count,
        record_count=int(row.counts.get("record_present", 0)),
        counts={name: int(row.counts.get(name, 0)) for name in _COUNT_NAMES},
        blocker=None if status in _TERMINAL else row.finalization_reason,
        recovery_action=_recovery_action(status),
        query_fingerprint=row.query_fingerprint,
        page_snapshot_as_of=row.page_snapshot_as_of,
        redaction_profile_version=row.redaction_profile_version,
        finalized_at=row.finalized_at,
    )


async def list_materializations(
    db: AsyncSession,
    *,
    scope: CollectionScope,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[EvidenceBatchMaterializationSummaryV1], str | None]:
    """Page latest scoped revisions in SQL without loading record-reference JSON."""
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if cursor is not None and (not cursor or len(cursor) > 36):
        raise ValueError("cursor must be a bounded batch id")
    manifest = EvidenceBatchMaterializationManifestV1
    latest = (
        select(
            manifest.id.label("id"),
            func.row_number()
            .over(
                partition_by=manifest.batch_id,
                order_by=manifest.reconciliation_revision.desc(),
            )
            .label("revision_rank"),
        )
        .where(*_scope_filters(scope))
        .subquery()
    )
    statement = (
        select(
            manifest.batch_id,
            manifest.reconciliation_revision,
            manifest.materialization_status,
            manifest.item_count,
            manifest.counts,
            manifest.finalization_reason,
            manifest.query_fingerprint,
            manifest.page_snapshot_as_of,
            manifest.redaction_profile_version,
            manifest.finalized_at,
        )
        .join(latest, manifest.id == latest.c.id)
        .where(latest.c.revision_rank == 1)
        .order_by(manifest.batch_id)
        .limit(limit + 1)
    )
    if cursor is not None:
        statement = statement.where(manifest.batch_id > cursor)
    rows = list((await db.execute(statement)).all())
    page = rows[:limit]
    next_cursor = page[-1].batch_id if len(rows) > limit else None
    return [_summary(row) for row in page], next_cursor


async def get_materialization_by_batch(
    db: AsyncSession, *, scope: CollectionScope, batch_id: str
) -> EvidenceBatchMaterializationReadV1 | None:
    """Return a scoped batch's latest revision without exposing retained raw facts."""
    manifest = (
        await db.execute(
            select(EvidenceBatchMaterializationManifestV1)
            .where(
                *_scope_filters(scope),
                EvidenceBatchMaterializationManifestV1.batch_id == batch_id,
            )
            .order_by(EvidenceBatchMaterializationManifestV1.reconciliation_revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return await _read(db, manifest) if manifest is not None else None


async def materialize_evidence_batch(
    db: AsyncSession,
    *,
    scope: CollectionScope,
    command_id: str,
    _race_retries_remaining: int = 1,
    _force_reconcile: bool = False,
) -> EvidenceBatchMaterializationReadV1:
    """Materialize once; terminal revisions replay without recomputing ODP facts."""
    # Match V2 mutation and delivery authorization lock order before reading
    # mutable materialization inputs or appending a manifest revision.
    run = await lock_scoped_workflow_run(
        db,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
    )
    if run is None:
        raise IIICollectionNotFoundError("Scoped workflow run not found")
    command = await db.get(IIICollectionCommandV1, command_id)
    if command is None or not matches_scope(
        command,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
    ):
        raise IIICollectionNotFoundError("Collection command not found")
    attempt = (
        await db.execute(
            select(IIICollectionAttemptV1)
            .where(IIICollectionAttemptV1.command_id == command.id)
            .order_by(IIICollectionAttemptV1.attempt_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise IIICollectionNotFoundError("Collection attempt not found")
    attempt_id = attempt.id
    report = (
        await db.execute(
            select(IIICollectionExpectedKeyReportV1).where(
                IIICollectionExpectedKeyReportV1.attempt_id == attempt.id
            )
        )
    ).scalar_one_or_none()
    receipts = list(
        (
            await db.execute(
                select(IIICollectionIngressReceiptV1)
                .where(IIICollectionIngressReceiptV1.attempt_id == attempt.id)
                .order_by(IIICollectionIngressReceiptV1.issued_at, IIICollectionIngressReceiptV1.id)
            )
        ).scalars()
    )
    terminal_inputs = _terminal_inputs(report, receipts)
    latest = await _latest_manifest(db, command_id, attempt_id)
    if (
        not _force_reconcile
        and latest is not None
        and latest.materialization_status in _TERMINAL
        and _same_terminal_inputs(latest, terminal_inputs)
    ):
        return await _read(db, latest, report)
    facts = await _reconcile(command, attempt, report, receipts)
    latest = await _latest_manifest(db, command_id, attempt_id)
    if latest is not None and (
        _same_revision(latest, facts)
        or (not _force_reconcile and _same_terminal_inputs(latest, terminal_inputs))
    ):
        return await _read(db, latest, report)
    revision = 1 if latest is None else latest.reconciliation_revision + 1
    manifest_payload = {"revision": revision, **facts}
    manifest = EvidenceBatchMaterializationManifestV1(
        version="v1",
        reconciliation_revision=revision,
        command_id=command.id,
        attempt_id=attempt.id,
        task_id=attempt.task_id,
        trace_id=attempt.trace_id,
        workspace_id=command.workspace_id,
        project_id=command.project_id,
        workflow_id=command.workflow_id,
        studio_workflow_version_id=command.studio_workflow_version_id,
        run_id=command.run_id,
        node_id=command.node_id,
        source_binding_id=command.source_binding_id,
        source_binding_revision_id=command.source_binding_revision_id,
        manifest_hash=_canonical_hash(manifest_payload),
        **facts,
    )
    db.add(manifest)
    try:
        await db.flush()
        db.add(
            EvidenceBatchMaterializationEventV1(
                manifest_id=manifest.id,
                command_id=command.id,
                attempt_id=attempt.id,
                reconciliation_revision=revision,
                materialization_status=facts["materialization_status"],
                event_hash=_canonical_hash({"event": "reconciled", **manifest_payload}),
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await _latest_manifest(db, command_id, attempt_id)
        if winner is not None and (
            _same_revision(winner, facts)
            or (not _force_reconcile and _same_terminal_inputs(winner, terminal_inputs))
        ):
            return await _read(db, winner, report)
        if _race_retries_remaining:
            return await materialize_evidence_batch(
                db,
                scope=scope,
                command_id=command_id,
                _race_retries_remaining=_race_retries_remaining - 1,
                _force_reconcile=_force_reconcile,
            )
        raise
    return await _read(db, manifest, report)


async def recover_evidence_batch(
    db: AsyncSession, *, scope: CollectionScope, command_id: str
) -> EvidenceBatchMaterializationReadV1:
    """Explicitly refresh bounded ODP facts without rewriting prior revisions."""
    return await materialize_evidence_batch(
        db, scope=scope, command_id=command_id, _force_reconcile=True
    )


def _terminal_inputs(
    report: IIICollectionExpectedKeyReportV1 | None,
    receipts: list[IIICollectionIngressReceiptV1],
) -> tuple[str, str, str, int, tuple[str, ...]] | None:
    if report is None:
        return None
    return (
        report.report_id,
        report.report_hash,
        report.key_set_sha256,
        report.item_count,
        tuple(receipt.receipt_hash for receipt in receipts),
    )


def _same_terminal_inputs(
    manifest: EvidenceBatchMaterializationManifestV1,
    inputs: tuple[str, str, str, int, tuple[str, ...]] | None,
) -> bool:
    return (
        inputs is not None
        and manifest.materialization_status in _TERMINAL
        and (
            manifest.report_id,
            manifest.report_hash,
            manifest.expected_key_set_hash,
            manifest.item_count,
            tuple(manifest.receipt_hashes),
        )
        == inputs
    )


async def _reconcile(
    command: IIICollectionCommandV1,
    attempt: IIICollectionAttemptV1,
    report: IIICollectionExpectedKeyReportV1 | None,
    receipts: list[IIICollectionIngressReceiptV1],
) -> dict[str, Any]:
    counts = {name: 0 for name in _COUNT_NAMES}
    common: dict[str, Any] = {
        "batch_id": _batch_id(command, attempt),
        "derivation": "dispatch-task-v1",
        "report_id": report.report_id if report else None,
        "report_hash": report.report_hash if report else None,
        "expected_key_set_hash": report.key_set_sha256 if report else None,
        "receipt_hashes": [receipt.receipt_hash for receipt in receipts],
        "query_fingerprint": None,
        "page_snapshot_as_of": None,
        "redaction_profile_version": None,
        "item_count": report.item_count if report else 0,
        "counts": counts,
        "record_references": [],
        "retention_state": "unknown",
        "finalized_at": None,
    }
    if report is None:
        return _outcome(common, "indeterminate", "collector_final_expected_key_report_missing")
    keys, invalid_keys = report_keys(report, command)
    counts["expected"] = len(keys)
    if invalid_keys:
        counts["unknown"] = len(keys)
        return _outcome(common, "indeterminate", "expected_key_scope_conflict")
    if not keys:
        if report.zero_count != 1 or report.rejected_count:
            return _outcome(common, "indeterminate", "invalid_zero_report")
        common["retention_state"] = "not_applicable"
        return _outcome(common, "completed_empty", "declared_successful_zero")
    outcomes = receipt_outcomes(receipts, keys)
    if outcomes is None:
        counts["unknown"] = len(keys)
        return _outcome(common, "indeterminate", "signed_outcome_receipt_missing_or_conflicting")

    rejected_keys = {key for key, outcome in outcomes.items() if outcome == "rejected"}
    duplicate_keys = {key for key, outcome in outcomes.items() if outcome == "duplicate"}
    counts["duplicate_existing"] = len(duplicate_keys)
    exact_keys = [key for key in keys if key not in rejected_keys]
    present_keys: set[OdpRecordKey] = set()
    dlq_keys: set[OdpRecordKey] = set()
    unresolved = set(exact_keys)

    def finish(status: str, reason: str) -> dict[str, Any]:
        counts["record_present"] = len(present_keys)
        counts["rejected"] = len(rejected_keys)
        counts["dlq"] = len(dlq_keys)
        counts["unknown"] = len(unresolved)
        return _outcome(common, status, reason)

    def accept_present(key: OdpRecordKey, result: dict[str, Any]) -> None:
        if key in present_keys or len(common["record_references"]) >= _MAX_RECORD_REFERENCES:
            return
        record = result.get("record")
        if not isinstance(record, dict):
            return
        present_keys.add(key)
        unresolved.discard(key)
        common["record_references"].append(_reference(record))

    try:
        delegation_request = delegation(command, attempt, common["batch_id"])
        for offset in range(0, len(exact_keys), _MAX_QUERY_KEYS):
            requested_keys = exact_keys[offset : offset + _MAX_QUERY_KEYS]
            exact = await post_reconciliation_query(
                build_exact_request(delegation_request, requested_keys)
            )
            if (
                common["query_fingerprint"] is not None
                and common["query_fingerprint"] != exact["query_fingerprint"]
            ):
                return finish("indeterminate", "query_fingerprint_conflict")
            common["query_fingerprint"] = exact["query_fingerprint"]
            common["redaction_profile_version"] = exact["redaction_profile_version"]
            common["retention_state"] = exact["retention_state"]
            result_by_key = {
                (result["key"]["source_id"], result["key"]["event_id"]): result
                for result in exact["results"]
            }
            for key in requested_keys:
                result = result_by_key.get((str(key.source_id), key.event_id))
                if result is not None and result["classification"] == "present":
                    accept_present(key, result)

        for offset in range(0, len(exact_keys), _MAX_QUERY_KEYS):
            requested_keys = [
                key for key in exact_keys[offset : offset + _MAX_QUERY_KEYS] if key in unresolved
            ]
            if not requested_keys:
                continue
            dlq = await post_reconciliation_query(
                build_dlq_request(delegation_request, requested_keys)
            )
            if common["query_fingerprint"] != dlq["query_fingerprint"]:
                return finish("indeterminate", "query_fingerprint_conflict")
            common["redaction_profile_version"] = dlq["redaction_profile_version"]
            common["retention_state"] = dlq["retention_state"]
            result_by_key = {
                (result["key"]["source_id"], result["key"]["event_id"]): result
                for result in dlq["results"]
            }
            for key in requested_keys:
                result = result_by_key.get((str(key.source_id), key.event_id))
                if result is not None and result["classification"] == "present":
                    accept_present(key, result)
                elif (
                    result is not None
                    and result["classification"] == "dlq"
                    and result["retention_state"] == "retained"
                ):
                    dlq_keys.add(key)
                    unresolved.discard(key)

        page = await post_reconciliation_query(
            build_attempt_page_request(delegation_request, page_size=_MAX_QUERY_KEYS)
        )
        if common["query_fingerprint"] is None:
            common["query_fingerprint"] = page["query_fingerprint"]
        elif common["query_fingerprint"] != page["query_fingerprint"]:
            return finish("indeterminate", "query_fingerprint_conflict")
        common["page_snapshot_as_of"] = page.get("as_of")
        common["redaction_profile_version"] = page["redaction_profile_version"]
        common["retention_state"] = page["retention_state"]
    except (OdpQueryError, ValueError, TypeError, KeyError):
        return finish("indeterminate", "odp_reconciliation_unavailable_or_invalid")
    if unresolved:
        return finish("indeterminate", "exact_reconciliation_unknown")
    if rejected_keys or dlq_keys:
        return finish("partial", "explicit_retained_rejection_or_dlq")
    if len(present_keys) != len(keys):
        return finish("indeterminate", "incomplete_exact_reconciliation")
    return finish("completed", "exact_presence_reconciled")


def _outcome(common: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    common["materialization_status"] = status
    common["finalization_reason"] = reason
    if status in _TERMINAL:
        common["finalized_at"] = datetime.now(UTC)
    return common


async def _latest_manifest(
    db: AsyncSession, command_id: str, attempt_id: str
) -> EvidenceBatchMaterializationManifestV1 | None:
    return (
        await db.execute(
            select(EvidenceBatchMaterializationManifestV1)
            .where(
                EvidenceBatchMaterializationManifestV1.command_id == command_id,
                EvidenceBatchMaterializationManifestV1.attempt_id == attempt_id,
            )
            .order_by(EvidenceBatchMaterializationManifestV1.reconciliation_revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _same_revision(manifest: EvidenceBatchMaterializationManifestV1, facts: dict[str, Any]) -> bool:
    return all(
        getattr(manifest, name) == facts[name]
        for name in (
            "batch_id",
            "derivation",
            "report_id",
            "report_hash",
            "expected_key_set_hash",
            "receipt_hashes",
            "query_fingerprint",
            "redaction_profile_version",
            "item_count",
            "counts",
            "materialization_status",
            "record_references",
            "retention_state",
            "finalization_reason",
        )
    )
