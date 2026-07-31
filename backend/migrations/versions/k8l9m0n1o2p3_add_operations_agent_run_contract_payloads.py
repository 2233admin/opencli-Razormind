"""add Operations Agent run contract payloads

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import context, op

revision = "k8l9m0n1o2p3"
down_revision = "j7k8l9m0n1o2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if (
        not context.is_offline_mode()
        and "operations_agent_runs" not in sa.inspect(op.get_bind()).get_table_names()
    ):
        return

    with op.batch_alter_table("operations_agent_runs") as batch:
        batch.add_column(
            sa.Column(
                "input_payload",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "state_payload",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(sa.Column("output_payload", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    if (
        not context.is_offline_mode()
        and "operations_agent_runs" not in sa.inspect(op.get_bind()).get_table_names()
    ):
        return

    with op.batch_alter_table("operations_agent_runs") as batch:
        batch.drop_column("error_message")
        batch.drop_column("output_payload")
        batch.drop_column("state_payload")
        batch.drop_column("input_payload")
