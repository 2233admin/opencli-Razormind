"""add agent_protocol to browser_instances

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-03-20 03:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'h8c9d0e1f2a3'
down_revision: str | None = 'g7b8c9d0e1f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'browser_instances',
        sa.Column('agent_protocol', sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('browser_instances', 'agent_protocol')
