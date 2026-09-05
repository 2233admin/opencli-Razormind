"""add collector expected keys and signed ingress receipts

Revision ID: a9b0c1d2e3f4
Revises: a8b9c0d1e2f3
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "a9b0c1d2e3f4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iii_collection_expected_key_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("report_id", sa.String(length=128), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("report_sequence", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("key_set_sha256", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("zero_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("expected_keys", sa.JSON(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["iii_collection_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["iii_collection_commands.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", name="uq_iii_collection_expected_key_report_id"),
        sa.UniqueConstraint(
            "command_id",
            "attempt_id",
            "report_sequence",
            name="uq_iii_collection_expected_key_report_replay",
        ),
    )
    op.create_index(
        "ix_iii_collection_expected_key_report_command_id",
        "iii_collection_expected_key_reports",
        ["command_id"],
    )
    op.create_index(
        "ix_iii_collection_expected_key_report_attempt_id",
        "iii_collection_expected_key_reports",
        ["attempt_id"],
    )
    op.create_index(
        "ix_iii_collection_expected_key_report_attempt",
        "iii_collection_expected_key_reports",
        ["attempt_id", "report_sequence"],
    )

    op.create_table(
        "iii_collection_ingress_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("receipt_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("producer_id", sa.String(length=255), nullable=False),
        sa.Column("producer_key_id", sa.String(length=255), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_key_set_sha256", sa.String(length=64), nullable=False),
        sa.Column("outcomes", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_hash", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["iii_collection_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["iii_collection_commands.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_id", name="uq_iii_collection_ingress_receipt_id"),
        sa.UniqueConstraint(
            "producer_id", "idempotency_key", name="uq_iii_collection_ingress_receipt_replay"
        ),
    )
    op.create_index(
        "ix_iii_collection_ingress_receipt_command_id",
        "iii_collection_ingress_receipts",
        ["command_id"],
    )
    op.create_index(
        "ix_iii_collection_ingress_receipt_attempt_id",
        "iii_collection_ingress_receipts",
        ["attempt_id"],
    )
    op.create_index(
        "ix_iii_collection_ingress_receipt_attempt",
        "iii_collection_ingress_receipts",
        ["attempt_id", "issued_at"],
    )


def downgrade() -> None:
    op.drop_table("iii_collection_ingress_receipts")
    op.drop_table("iii_collection_expected_key_reports")
