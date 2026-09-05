"""Migration-installed SQLite guards for controlled delivery evidence."""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from backend.config import get_settings
from backend.models.delivery_authorization import (
    DeliveryAuthorizationDecisionV1,
    DeliveryTarget,
    DeliveryTargetRevision,
)
from backend.models.identity import Workspace
from backend.security import controlled_receiver as receiver
from backend.workflow import delivery_execution
from backend.workflow.delivery_authorization import DeliveryAuthorizationScope
from tests.integration.iii_collection_test_support import create_scoped_run


async def _stored_frozen_decision(
    db_session, *, operation_id: str = "delivery-op", claim_id: str = "claim-1"
) -> tuple[DeliveryAuthorizationScope, str]:
    scoped = await create_scoped_run(db_session)
    workspace_id = scoped["workspace"].id
    await db_session.merge(
        Workspace(id=workspace_id, name="Receiver delivery", slug="receiver-delivery")
    )
    scope = DeliveryAuthorizationScope(
        workspace_id=workspace_id,
        project_id=scoped["project"].id,
        workflow_id=scoped["workflow"].id,
        studio_workflow_version_id=scoped["version"].id,
        run_id=scoped["run"].id,
    )
    endpoint = receiver.resolve_endpoint("receiver-primary", "credential-a")
    policy_version, policy_snapshot, policy_hash = delivery_execution._current_policy()
    target_id, revision_id, decision_id = (str(uuid.uuid4()) for _ in range(3))
    now = datetime.now(UTC).replace(tzinfo=None)
    claims = [{"claimId": claim_id, "contentHash": "a" * 64}]
    manifests = [{"manifestHash": "b" * 64}]
    payload = {
        "schemaVersion": "delivery-claim-manifest-v1",
        "claims": claims,
        "manifestHashes": ["b" * 64],
    }
    target = DeliveryTarget(
        id=target_id, workspace_id=workspace_id, receiver_identity=endpoint.receiver_identity
    )
    revision = DeliveryTargetRevision(
        id=revision_id,
        target_id=target_id,
        revision=1,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
        endpoint_identity=endpoint.identity,
        non_secret_config_hash=receiver.endpoint_config_hash(endpoint),
        credential_reference=endpoint.credential_reference,
        policy_version=policy_version,
        policy_snapshot=policy_snapshot,
        policy_hash=policy_hash,
    )
    decision = DeliveryAuthorizationDecisionV1(
        id=decision_id,
        version="v1",
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
        node_id="delivery-node",
        operation_id=operation_id,
        idempotency_key="delivery-idempotency",
        target_id=target_id,
        target_revision_id=revision_id,
        target_revision=1,
        endpoint_identity=endpoint.identity,
        non_secret_config_hash=receiver.endpoint_config_hash(endpoint),
        policy_version=policy_version,
        policy_snapshot=policy_snapshot,
        policy_hash=policy_hash,
        pin_sequence=1,
        research_revision_id="research-1",
        manifest_set_hash="b" * 64,
        selected_claims=claims,
        manifest_set=manifests,
        sanitized_payload_manifest={
            "payloadSchemaVersion": "delivery-claim-manifest-v1",
            "payloadReference": "frozen-claim-manifest",
            "payloadHash": receiver.canonical_hash(payload),
            "sanctionedReferenceHashes": ["a" * 64, "b" * 64],
            "redactionProfileVersion": "delivery-authorization-redaction-v1",
        },
        payload_schema_version="delivery-claim-manifest-v1",
        payload_reference="frozen-claim-manifest",
        payload_hash=receiver.canonical_hash(payload),
        redaction_profile_version="delivery-authorization-redaction-v1",
        approver_actor_id="approver-1",
        approver_actor_type="user",
        approver_principal="approver-1",
        approver_capability="actions.approve",
        approval_policy_version="workspace-rbac-v1",
        approved_at=now,
        approval_evidence=[],
        binding_hash="",
        decision_hash="",
        decisioned_at=now,
    )
    decision.approval_evidence = [delivery_execution._approval(decision, scope)]
    binding = delivery_execution._decision_binding(decision, scope)
    decision.binding_hash = receiver.canonical_hash(binding)
    decision.decision_hash = receiver.canonical_hash(
        {
            "binding": binding,
            "approvalEvidence": decision.approval_evidence,
            "decisionedAt": now.isoformat(),
        }
    )
    db_session.add_all((target, revision, decision))
    await db_session.commit()
    return scope, decision_id


def _insert_sqlite_row(
    connection: sqlite3.Connection, table: str, values: dict[str, object]
) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def _seed_delivery_guard_graph(connection: sqlite3.Connection) -> dict[str, str]:
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "workspace",
            "project",
            "workflow",
            "validation",
            "version",
            "run",
            "target",
            "revision",
            "decision_a",
            "decision_b",
            "execution_a",
            "execution_b",
            "result_b",
            "reconciliation_b",
            "receiver_delivery",
            "receiver_nonce",
        )
    }
    now = "2026-08-30 00:00:00"
    _insert_sqlite_row(
        connection,
        "studio_workspaces",
        {
            "id": ids["workspace"],
            "created_at": now,
            "updated_at": now,
            "name": "guards",
            "slug": "guards",
            "active": 1,
        },
    )
    _insert_sqlite_row(
        connection,
        "studio_projects",
        {
            "id": ids["project"],
            "created_at": now,
            "updated_at": now,
            "workspace_id": ids["workspace"],
            "name": "guards",
            "slug": "guards",
            "description": None,
            "app_type": "workflow",
            "primary_workflow_id": None,
            "created_by_user_id": "guard-user",
            "archived": 0,
        },
    )
    _insert_sqlite_row(
        connection,
        "studio_workflows",
        {
            "id": ids["workflow"],
            "created_at": now,
            "updated_at": now,
            "project_id": ids["project"],
            "name": "guards",
            "description": None,
            "current_published_version": 1,
            "archived": 0,
        },
    )
    _insert_sqlite_row(
        connection,
        "studio_workflow_validation_runs",
        {
            "id": ids["validation"],
            "created_at": now,
            "updated_at": now,
            "workflow_id": ids["workflow"],
            "draft_revision": 1,
            "status": "valid",
            "valid": 1,
            "errors": "[]",
            "warnings": "[]",
            "compile_version": "v1",
            "resolved_graph": "{}",
        },
    )
    _insert_sqlite_row(
        connection,
        "studio_workflow_versions",
        {
            "id": ids["version"],
            "created_at": now,
            "updated_at": now,
            "workflow_id": ids["workflow"],
            "version": 1,
            "draft_revision": 1,
            "graph": "{}",
            "compile_version": "v1",
            "validation_run_id": ids["validation"],
            "published_by_user_id": "guard-user",
            "reason": "guard test",
        },
    )
    _insert_sqlite_row(
        connection,
        "workflow_runs",
        {
            "id": ids["run"],
            "created_at": now,
            "updated_at": now,
            "workflow_id": ids["workflow"],
            "workflow_version_id": None,
            "studio_workflow_version_id": ids["version"],
            "trace_id": "guard-trace",
            "status": "completed",
            "valid": 1,
            "package_node_id": None,
            "request": "{}",
            "projection": "{}",
            "next_event_sequence": 1,
        },
    )
    _insert_sqlite_row(
        connection,
        "delivery_targets",
        {
            "id": ids["target"],
            "created_at": now,
            "updated_at": now,
            "workspace_id": ids["workspace"],
            "receiver_identity": "receiver-a",
            "target_kind": "controlled-receiver",
        },
    )
    _insert_sqlite_row(
        connection,
        "delivery_target_revisions",
        {
            "id": ids["revision"],
            "created_at": now,
            "updated_at": now,
            "target_id": ids["target"],
            "revision": 1,
            "workspace_id": ids["workspace"],
            "project_id": ids["project"],
            "workflow_id": ids["workflow"],
            "studio_workflow_version_id": ids["version"],
            "run_id": ids["run"],
            "endpoint_identity": "receiver-primary",
            "non_secret_config_hash": "a" * 64,
            "credential_reference": "credential-a",
            "policy_version": "controlled-receiver-policy-v2",
            "policy_snapshot": "{}",
            "policy_hash": "b" * 64,
        },
    )
    for suffix in ("a", "b"):
        _insert_sqlite_row(
            connection,
            "delivery_authorization_decisions",
            {
                "id": ids[f"decision_{suffix}"],
                "created_at": now,
                "updated_at": now,
                "version": "v1",
                "workspace_id": ids["workspace"],
                "project_id": ids["project"],
                "workflow_id": ids["workflow"],
                "studio_workflow_version_id": ids["version"],
                "run_id": ids["run"],
                "node_id": f"node-{suffix}",
                "operation_id": f"operation-{suffix}",
                "idempotency_key": f"idempotency-{suffix}",
                "target_id": ids["target"],
                "target_revision_id": ids["revision"],
                "target_revision": 1,
                "endpoint_identity": "receiver-primary",
                "non_secret_config_hash": "a" * 64,
                "policy_version": "controlled-receiver-policy-v2",
                "policy_snapshot": "{}",
                "policy_hash": "b" * 64,
                "pin_sequence": 1,
                "research_revision_id": "research-1",
                "manifest_set_hash": "c" * 64,
                "selected_claims": "[]",
                "manifest_set": "[]",
                "sanitized_payload_manifest": "{}",
                "payload_schema_version": "delivery-claim-manifest-v1",
                "payload_reference": "frozen",
                "payload_hash": "d" * 64,
                "redaction_profile_version": "delivery-authorization-redaction-v1",
                "approver_actor_id": "guard-user",
                "approver_actor_type": "user",
                "approver_principal": "guard-user",
                "approver_capability": "actions.approve",
                "approval_policy_version": "workspace-rbac-v1",
                "approved_at": now,
                "approval_evidence": "[]",
                "binding_hash": f"{suffix}" * 64,
                "decision_hash": f"{suffix}" * 64,
                "decisioned_at": now,
            },
        )
    return ids


def _delivery_execution_values(
    ids: dict[str, str],
    *,
    execution_id: str,
    decision_id: str,
    binding_hash: str,
    final_result_id: str | None = None,
    final_reconciliation_id: str | None = None,
) -> dict[str, object]:
    now = "2026-08-30 00:00:00"
    return {
        "id": execution_id,
        "created_at": now,
        "updated_at": now,
        "decision_id": decision_id,
        "target_revision_id": ids["revision"],
        "workspace_id": ids["workspace"],
        "project_id": ids["project"],
        "workflow_id": ids["workflow"],
        "studio_workflow_version_id": ids["version"],
        "run_id": ids["run"],
        "operation_id": f"execution-{execution_id}",
        "decision_hash": "e" * 64,
        "payload_hash": "f" * 64,
        "execution_binding_hash": binding_hash,
        "state": "pending",
        "final_result_id": final_result_id,
        "final_reconciliation_id": final_reconciliation_id,
    }


def test_migration_installed_sqlite_evidence_guards_reject_raw_mutation_and_invalid_links(
    tmp_path: Path, monkeypatch
):
    database = tmp_path / "delivery-guards.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "backend" / "migrations")
    )
    try:
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        ids = _seed_delivery_guard_graph(connection)
        _insert_sqlite_row(
            connection,
            "delivery_executions",
            _delivery_execution_values(
                ids,
                execution_id=ids["execution_a"],
                decision_id=ids["decision_a"],
                binding_hash="1" * 64,
            ),
        )
        _insert_sqlite_row(
            connection,
            "delivery_executions",
            _delivery_execution_values(
                ids,
                execution_id=ids["execution_b"],
                decision_id=ids["decision_b"],
                binding_hash="2" * 64,
            ),
        )
        now = "2026-08-30 00:00:00"
        _insert_sqlite_row(
            connection,
            "delivery_execution_results",
            {
                "id": ids["result_b"],
                "created_at": now,
                "updated_at": now,
                "execution_id": ids["execution_b"],
                "attempt_number": 1,
                "transport_classification": "http-success",
                "http_status": 200,
                "receipt_classification": "verified",
                "protocol_classification": "v2",
                "outcome": "accepted",
                "receipt_id": None,
                "receipt_hash": None,
                "observed_at": now,
            },
        )
        _insert_sqlite_row(
            connection,
            "delivery_execution_reconciliations",
            {
                "id": ids["reconciliation_b"],
                "created_at": now,
                "updated_at": now,
                "execution_id": ids["execution_b"],
                "receipt_hash": "3" * 64,
                "outcome": "accepted",
                "observed_at": now,
            },
        )
        _insert_sqlite_row(
            connection,
            "controlled_receiver_deliveries",
            {
                "id": ids["receiver_delivery"],
                "created_at": now,
                "updated_at": now,
                "receiver_identity": "receiver-a",
                "operation_id": "receiver-operation",
                "decision_hash": "4" * 64,
                "payload_hash": "5" * 64,
                "request_hash": "6" * 64,
                "durable_status": "accepted",
                "receipt_id": "receipt-a",
                "receipt_timestamp": now,
                "receipt_key_id": "receipt-a",
                "receipt_signature": "signature",
            },
        )
        _insert_sqlite_row(
            connection,
            "controlled_receiver_nonces",
            {
                "id": ids["receiver_nonce"],
                "created_at": now,
                "updated_at": now,
                "receiver_identity": "receiver-a",
                "key_id": "request-a",
                "nonce": "nonce-a",
                "request_hash": "7" * 64,
            },
        )
        connection.commit()
        trigger_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        assert {
            "trg_delivery_execution_results_append_only_update",
            "trg_delivery_execution_results_append_only_delete",
            "trg_delivery_execution_reconciliations_append_only_update",
            "trg_delivery_execution_reconciliations_append_only_delete",
            "trg_controlled_receiver_deliveries_append_only_update",
            "trg_controlled_receiver_deliveries_append_only_delete",
            "trg_controlled_receiver_nonces_append_only_update",
            "trg_controlled_receiver_nonces_append_only_delete",
            "trg_delivery_execution_final_links_insert",
            "trg_delivery_execution_final_links_update",
        } <= trigger_names

        def rejected(operation) -> None:
            with pytest.raises(sqlite3.IntegrityError):
                operation()
            connection.rollback()

        for attempt, outcome in ((0, "unknown"), (4, "unknown"), (1, "invalid")):
            rejected(
                lambda attempt=attempt, outcome=outcome: _insert_sqlite_row(
                    connection,
                    "delivery_execution_results",
                    {
                        "id": str(uuid.uuid4()),
                        "created_at": now,
                        "updated_at": now,
                        "execution_id": ids["execution_a"],
                        "attempt_number": attempt,
                        "transport_classification": "test",
                        "http_status": None,
                        "receipt_classification": "missing",
                        "protocol_classification": "unknown",
                        "outcome": outcome,
                        "receipt_id": None,
                        "receipt_hash": None,
                        "observed_at": now,
                    },
                )
            )
        rejected(
            lambda: _insert_sqlite_row(
                connection,
                "delivery_execution_reconciliations",
                {
                    "id": str(uuid.uuid4()),
                    "created_at": now,
                    "updated_at": now,
                    "execution_id": ids["execution_a"],
                    "receipt_hash": "8" * 64,
                    "outcome": "unknown",
                    "observed_at": now,
                },
            )
        )
        rejected(
            lambda: _insert_sqlite_row(
                connection,
                "controlled_receiver_deliveries",
                {
                    "id": str(uuid.uuid4()),
                    "created_at": now,
                    "updated_at": now,
                    "receiver_identity": "receiver-a",
                    "operation_id": "invalid-status",
                    "decision_hash": "9" * 64,
                    "payload_hash": "a" * 64,
                    "request_hash": "b" * 64,
                    "durable_status": "unknown",
                    "receipt_id": "receipt-invalid",
                    "receipt_timestamp": now,
                    "receipt_key_id": "receipt-a",
                    "receipt_signature": "signature",
                },
            )
        )
        for table, row_id in (
            ("delivery_execution_results", ids["result_b"]),
            ("delivery_execution_reconciliations", ids["reconciliation_b"]),
            ("controlled_receiver_deliveries", ids["receiver_delivery"]),
            ("controlled_receiver_nonces", ids["receiver_nonce"]),
        ):
            rejected(
                lambda table=table: connection.execute(
                    f"UPDATE {table} SET updated_at = updated_at"
                )
            )
            rejected(
                lambda table=table, row_id=row_id: connection.execute(
                    f"DELETE FROM {table} WHERE id = ?", (row_id,)
                )
            )
        for column, evidence_id in (
            ("final_result_id", str(uuid.uuid4())),
            ("final_reconciliation_id", str(uuid.uuid4())),
        ):
            rejected(
                lambda column=column, evidence_id=evidence_id: _insert_sqlite_row(
                    connection,
                    "delivery_executions",
                    _delivery_execution_values(
                        ids,
                        execution_id=str(uuid.uuid4()),
                        decision_id=ids["decision_a"],
                        binding_hash="c" * 64,
                        **{column: evidence_id},
                    ),
                )
            )
        for column, evidence_id in (
            ("final_result_id", str(uuid.uuid4())),
            ("final_reconciliation_id", str(uuid.uuid4())),
            ("final_result_id", ids["result_b"]),
            ("final_reconciliation_id", ids["reconciliation_b"]),
        ):
            rejected(
                lambda column=column, evidence_id=evidence_id: connection.execute(
                    f"UPDATE delivery_executions SET {column} = ? WHERE id = ?",
                    (evidence_id, ids["execution_a"]),
                )
            )
    finally:
        connection.close()
