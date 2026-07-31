"""add provider_models and model_defaults tables

Revision ID: d8e9f0a1b2c3
Revises: a7v8w9x0y1z2
Create Date: 2026-07-09

model-provider runtime PR-A (model-provider-mgmt, decisions #3/#4): the model catalog
(``provider_models`` — one row per model a provider exposes, sourced from
discovery sync or manual entry) and system default candidates per
consumption role (``model_defaults``). Adds ONLY these two tables — no
adapters/factory/resolver (PR-B/D), no API routes (PR-C).
"""

import sqlalchemy as sa
from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "a7v8w9x0y1z2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("model_type", sa.String(length=50), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_provider_models_provider_model"),
    )
    op.create_index("ix_provider_models_provider_id", "provider_models", ["provider_id"])

    op.create_table(
        "model_defaults",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role", name="uq_model_defaults_role"),
    )


def downgrade() -> None:
    op.drop_table("model_defaults")
    op.drop_index("ix_provider_models_provider_id", table_name="provider_models")
    op.drop_table("provider_models")
