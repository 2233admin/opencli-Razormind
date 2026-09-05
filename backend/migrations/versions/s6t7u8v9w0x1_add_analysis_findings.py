"""add durable analysis findings

Revision ID: s6t7u8v9w0x1
Revises: r5s6t7u8v9w0
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "s6t7u8v9w0x1"
down_revision = "r5s6t7u8v9w0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_findings",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("studio_workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_receipt_id", sa.String(length=36), nullable=False),
        sa.Column("author_user_id", sa.String(length=100), nullable=False),
        sa.Column("source_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("selector_kind", sa.String(length=32), nullable=False),
        sa.Column("selector_key", sa.String(length=255), nullable=True),
        sa.Column("evidence_metric", sa.String(length=32), nullable=False),
        sa.Column("evidence_value", sa.Float(), nullable=False),
        sa.Column("evidence_unit", sa.String(length=32), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(observation)) > 0",
            name="ck_analysis_findings_observation_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(interpretation)) > 0",
            name="ck_analysis_findings_interpretation_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(recommendation)) > 0",
            name="ck_analysis_findings_recommendation_nonempty",
        ),
        sa.CheckConstraint(
            "source_start_at < source_end_at",
            name="ck_analysis_findings_source_range",
        ),
        sa.CheckConstraint(
            "((selector_kind IN ('event_type', 'node')) "
            "AND selector_key IS NOT NULL AND length(trim(selector_key)) > 0) "
            "OR ((selector_kind IN ('overall_throughput', 'latency', 'failure_rate')) "
            "AND selector_key IS NULL)",
            name="ck_analysis_findings_selector_key",
        ),
        sa.CheckConstraint(
            "(selector_kind = 'overall_throughput' "
            "AND evidence_metric = 'events_per_minute' "
            "AND evidence_unit = 'events_per_minute') "
            "OR (selector_kind = 'latency' AND evidence_metric = 'p95_ms' "
            "AND evidence_unit = 'milliseconds') "
            "OR (selector_kind = 'failure_rate' AND evidence_metric = 'failure_rate' "
            "AND evidence_unit = 'ratio') "
            "OR (selector_kind IN ('event_type', 'node') "
            "AND evidence_metric = 'event_count' AND evidence_unit = 'count')",
            name="ck_analysis_findings_evidence_mapping",
        ),
        sa.CheckConstraint(
            "evidence_value >= 0 "
            "AND (evidence_metric != 'failure_rate' OR evidence_value <= 1)",
            name="ck_analysis_findings_evidence_value",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_receipt_id"],
            ["analysis_snapshot_receipts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_findings_scope_created",
        "analysis_findings",
        ["workspace_id", "project_id", "workflow_id", "run_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_findings_snapshot_receipt",
        "analysis_findings",
        ["snapshot_receipt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_analysis_findings_snapshot_receipt",
        table_name="analysis_findings",
    )
    op.drop_index(
        "ix_analysis_findings_scope_created",
        table_name="analysis_findings",
    )
    op.drop_table("analysis_findings")
