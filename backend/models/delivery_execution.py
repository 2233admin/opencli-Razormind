"""Durable execution and controlled-receiver evidence for frozen delivery decisions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class DeliveryExecution(TimestampMixin):
    """Mutable claim state; immutable authorization and result evidence live elsewhere."""

    __tablename__ = "delivery_executions"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_delivery_execution_decision"),
        UniqueConstraint("execution_binding_hash", name="uq_delivery_execution_binding"),
        Index(
            "ix_delivery_execution_scope",
            "workspace_id",
            "project_id",
            "workflow_id",
            "run_id",
            "id",
        ),
    )

    decision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("delivery_authorization_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_revision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("delivery_target_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    studio_workflow_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    send_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reserved_attempt_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    final_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    final_reconciliation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class DeliveryExecutionResult(TimestampMixin):
    """Append-only observation for one reserved outbound network attempt."""

    __tablename__ = "delivery_execution_results"
    __table_args__ = (
        UniqueConstraint(
            "execution_id", "attempt_number", name="uq_delivery_execution_result_attempt"
        ),
        Index("ix_delivery_execution_result_execution", "execution_id", "attempt_number"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("delivery_executions.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    transport_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    receipt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)



class DeliveryExecutionReconciliation(TimestampMixin):
    """Append-only verified receiver-status evidence; never a send attempt."""

    __tablename__ = "delivery_execution_reconciliations"
    __table_args__ = (
        Index("ix_delivery_execution_reconciliation_execution", "execution_id", "observed_at"),
    )

    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("delivery_executions.id", ondelete="RESTRICT"), nullable=False
    )
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlledReceiverDelivery(TimestampMixin):
    """Durable receiver decision keyed by operation and frozen decision hash."""

    __tablename__ = "controlled_receiver_deliveries"
    __table_args__ = (
        UniqueConstraint("operation_id", "decision_hash", name="uq_controlled_receiver_delivery"),
        Index("ix_controlled_receiver_delivery_receiver", "receiver_identity", "created_at", "id"),
    )

    receiver_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    durable_status: Mapped[str] = mapped_column(String(16), nullable=False)
    receipt_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    receipt_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_signature: Mapped[str] = mapped_column(String(128), nullable=False)


class ControlledReceiverNonce(TimestampMixin):
    """Restart-safe nonce replay evidence, separate from the durable delivery key."""

    __tablename__ = "controlled_receiver_nonces"
    __table_args__ = (
        UniqueConstraint(
            "receiver_identity", "key_id", "nonce", name="uq_controlled_receiver_nonce"
        ),
    )

    receiver_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(DeliveryExecutionResult, "before_update")
@event.listens_for(DeliveryExecutionResult, "before_delete")
@event.listens_for(DeliveryExecutionReconciliation, "before_update")
@event.listens_for(DeliveryExecutionReconciliation, "before_delete")
@event.listens_for(ControlledReceiverDelivery, "before_update")
@event.listens_for(ControlledReceiverDelivery, "before_delete")
@event.listens_for(ControlledReceiverNonce, "before_update")
@event.listens_for(ControlledReceiverNonce, "before_delete")
def _reject_immutable_mutation(*_: object) -> None:
    raise ValueError("Delivery result and controlled receiver evidence are append-only")
