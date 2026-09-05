"""add node_type to browser_instances

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-20 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: str | None = 'd4e5f6a7b8c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'browser_instances',
        sa.Column('node_type', sa.String(20), nullable=False, server_default='local'),
    )


def downgrade() -> None:
    op.drop_column('browser_instances', 'node_type')
