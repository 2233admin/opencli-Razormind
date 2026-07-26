"""add workspace sources and project source bindings

Revision ID: f3g4h5i6j7k8
Revises: 1901f6da7138
Create Date: 2026-07-26

ADR-0041: a Workspace-owned Source (identity + mutable operational/health
status) gets immutable SourceRevisions for semantic adapter/endpoint/config
edits. A Project narrows a Source into a SourceBinding (authorization +
collection scope, also mutable for immediate credential/health/revocation
changes), which gets immutable SourceBindingRevisions that each pin an exact
SourceRevision explicitly — never the latest one implicitly. Adds only these
four tables; the legacy unscoped `data_sources` table (and its /sources API)
is untouched and remains the compatibility path for existing callers.
"""

import sqlalchemy as sa
from alembic import op

revision = "f3g4h5i6j7k8"
down_revision = "1901f6da7138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("adapter_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", "revoked", name="source_lifecycle_status"),
            nullable=False,
        ),
        sa.Column("current_revision_number", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("workspace_id", "slug"),
    )
    op.create_index("ix_sources_workspace_id", "sources", ["workspace_id"])

    op.create_table(
        "source_revisions",
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("adapter_config", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_id", "revision_number"),
    )
    op.create_index("ix_source_revisions_source_id", "source_revisions", ["source_id"])

    op.create_table(
        "source_bindings",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", "revoked", name="source_binding_lifecycle_status"),
            nullable=False,
        ),
        sa.Column("current_revision_number", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "slug"),
    )
    op.create_index("ix_source_bindings_project_id", "source_bindings", ["project_id"])
    op.create_index("ix_source_bindings_source_id", "source_bindings", ["source_id"])

    op.create_table(
        "source_binding_revisions",
        sa.Column("source_binding_id", sa.String(36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("pinned_source_revision_id", sa.String(36), nullable=False),
        sa.Column("scope_config", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_binding_id"], ["source_bindings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pinned_source_revision_id"], ["source_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_binding_id", "revision_number"),
    )
    op.create_index(
        "ix_source_binding_revisions_source_binding_id",
        "source_binding_revisions",
        ["source_binding_id"],
    )


def downgrade() -> None:
    op.drop_table("source_binding_revisions")
    op.drop_table("source_bindings")
    op.drop_table("source_revisions")
    op.drop_table("sources")
