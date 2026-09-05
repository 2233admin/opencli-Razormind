"""converge materialization status index names

Revision ID: h6i7j8k9l0m
Revises: g5b6c7d8e9f0
Create Date: 2026-08-30
"""

from alembic import op

revision = "h6i7j8k9l0m"
down_revision = "g5b6c7d8e9f0"
branch_labels = None
depends_on = None


_LEGACY_TRUNCATED_INDEX = "ix_evidence_batch_materialization_manifests_materialization_sta"
_CANONICAL_INDEX = "ix_evidence_batch_materialization_status"


def upgrade() -> None:
    """Repair PostgreSQL's truncated identifier after the original migration ran."""

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "DO $$ BEGIN "
        f"IF to_regclass('{_LEGACY_TRUNCATED_INDEX}') IS NOT NULL "
        f"AND to_regclass('{_CANONICAL_INDEX}') IS NULL THEN "
        f"ALTER INDEX {_LEGACY_TRUNCATED_INDEX} RENAME TO {_CANONICAL_INDEX}; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    # The canonical short name is valid on every supported dialect; do not regress it.
    return
