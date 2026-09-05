"""add control measurement tables (source_measurements, odp_system_measurements)

Revision ID: v2q3r4s5t6u7
Revises: u1p2q3r4s5t6
Create Date: 2026-07-02

Control-plane sensor time series (cybernetics observe stage — see
docs/CONTROL_THEORY_ARCHITECTURE.md).

- source_measurements: one row per run (success or failure), written by
  backend/control/recorder.py — the per-source "sensor reading" a future
  controller (PR-Control-3+) reads instead of guessing. Mirrors the pure
  Pydantic contract in backend/control/measurements.py.
- odp_system_measurements: optional point-in-time snapshot of the ODP
  SYSTEM-level state (Redis stream lag/pending, DLQ counts) exposed live by
  GET /api/v1/control/odp-state. The endpoint collects on demand and does NOT
  depend on this table; it exists only so a future periodic job can persist
  history. store/outbox columns are nullable — they have no producer today
  (odp-store has no heartbeat, there is no odp_outbox table) and are kept so a
  future Rust-side producer can populate them without another migration.

Both tables are new/empty, so the NOT NULL columns need no backfill.
"""
import sqlalchemy as sa
from alembic import op

revision = "v2q3r4s5t6u7"
down_revision = "u1p2q3r4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=False),
        sa.Column("duplicate_rate", sa.Float(), nullable=False),
        sa.Column("error_kinds", sa.JSON(), nullable=False),
        sa.Column("fetch_latency_ms", sa.Integer(), nullable=True),
        sa.Column("ingest_latency_ms", sa.Integer(), nullable=True),
        sa.Column("store_latency_ms", sa.Integer(), nullable=True),
        sa.Column("cursor_advanced", sa.Boolean(), nullable=False),
        sa.Column("newest_source_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_lag_seconds", sa.Integer(), nullable=True),
        sa.Column("source_ts_quality", sa.String(length=32), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_measurements_source_id", "source_measurements", ["source_id"])
    op.create_index("ix_source_measurements_run_id", "source_measurements", ["run_id"])

    op.create_table(
        "odp_system_measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingest_available", sa.Boolean(), nullable=False),
        sa.Column("ingest_healthy", sa.Boolean(), nullable=True),
        sa.Column("stream_available", sa.Boolean(), nullable=False),
        sa.Column("stream_name", sa.String(length=255), nullable=True),
        sa.Column("stream_group", sa.String(length=255), nullable=True),
        sa.Column("stream_lag", sa.Integer(), nullable=True),
        sa.Column("stream_pending", sa.Integer(), nullable=True),
        sa.Column("stream_oldest_pending_idle_ms", sa.Integer(), nullable=True),
        sa.Column("dlq_available", sa.Boolean(), nullable=False),
        sa.Column("dlq_total", sa.Integer(), nullable=True),
        sa.Column("dlq_last_24h", sa.Integer(), nullable=True),
        sa.Column("store_available", sa.Boolean(), nullable=False),
        sa.Column("store_healthy", sa.Boolean(), nullable=True),
        sa.Column("outbox_available", sa.Boolean(), nullable=False),
        sa.Column("outbox_unpublished", sa.Integer(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_odp_system_measurements_observed_at",
        "odp_system_measurements",
        ["observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_odp_system_measurements_observed_at", table_name="odp_system_measurements")
    op.drop_table("odp_system_measurements")
    op.drop_index("ix_source_measurements_run_id", table_name="source_measurements")
    op.drop_index("ix_source_measurements_source_id", table_name="source_measurements")
    op.drop_table("source_measurements")
