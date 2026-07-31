"""add runtimes to edge_nodes

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-03

The ws register handshake optionally advertises the agent-runtime types available on that edge node
(e.g. ["pi"]) via ``backend.agent_runtimes.registry.available_runtimes()``.
Persisted as a nullable JSON list so older nodes / handshakes that omit the
field leave the column NULL rather than forcing an empty-list default.
"""
import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edge_nodes", sa.Column("runtimes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("edge_nodes", "runtimes")
