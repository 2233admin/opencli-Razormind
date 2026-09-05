"""Immutable Admin-owned authorization records for controlled delivery receivers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class DeliveryTarget(TimestampMixin):
    """Workspace-owned identity for one controlled receiver; it never stores a secret."""

    __tablename__ = "delivery_targets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "receiver_identity", name="uq_delivery_target_receiver"),
        Index("ix_delivery_targets_workspace", "workspace_id", "id"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("studio_workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    receiver_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    target_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="controlled-receiver-v1"
    )


class DeliveryTargetRevision(TimestampMixin):
    """One immutable, fully scoped configuration revision for a controlled receiver."""

    __tablename__ = "delivery_target_revisions"
    __table_args__ = (
        UniqueConstraint("target_id", "revision", name="uq_delivery_target_revision"),
        Index(
            "ix_delivery_target_revision_scope",
            "workspace_id",
            "project_id",
            "workflow_id",
            "run_id",
        ),
    )

    target_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("delivery_targets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_projects.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_workflows.id", ondelete="RESTRICT"), nullable=False
    )
    studio_workflow_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    endpoint_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    non_secret_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DeliveryAuthorizationDecisionV1(TimestampMixin):
    """Append-only authorization for exactly one delivery operation, never an execution result."""

    __tablename__ = "delivery_authorization_decisions"
    __table_args__ = (
        UniqueConstraint("decision_hash", name="uq_delivery_authorization_decision_hash"),
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "workflow_id",
            "studio_workflow_version_id",
            "run_id",
            "operation_id",
            name="uq_delivery_authorization_operation",
        ),
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "workflow_id",
            "studio_workflow_version_id",
            "run_id",
            "idempotency_key",
            name="uq_delivery_authorization_idempotency",
        ),
        Index(
            "ix_delivery_authorization_scope_created",
            "workspace_id",
            "project_id",
            "workflow_id",
            "run_id",
            "created_at",
            "id",
        ),
    )

    version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_projects.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_workflows.id", ondelete="RESTRICT"), nullable=False
    )
    studio_workflow_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("studio_workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    target_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("delivery_targets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_revision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("delivery_target_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    non_secret_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pin_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    research_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_claims: Mapped[list] = mapped_column(JSON, nullable=False)
    manifest_set: Mapped[list] = mapped_column(JSON, nullable=False)
    sanitized_payload_manifest: Mapped[dict] = mapped_column(JSON, nullable=False)

    payload_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redaction_profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approver_actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    approver_principal: Mapped[str] = mapped_column(String(255), nullable=False)
    approver_capability: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_evidence: Mapped[list] = mapped_column(JSON, nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decisioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

@event.listens_for(DeliveryTargetRevision, "before_update")
@event.listens_for(DeliveryTargetRevision, "before_delete")
@event.listens_for(DeliveryAuthorizationDecisionV1, "before_update")
@event.listens_for(DeliveryAuthorizationDecisionV1, "before_delete")
def _reject_immutable_mutation(*_: object) -> None:
    raise ValueError("Frozen delivery authorization records are append-only")
