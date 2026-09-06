"""Bind persistent conversations to durable Agent runs."""

import sqlalchemy as sa
from alembic import op

revision = "ac126run1"
down_revision = "cr125p2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_conversations") as batch:
        batch.add_column(
            sa.Column(
                "execution_binding",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("agent_session_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_agent_conversations_agent_session",
            "agent_sessions",
            ["agent_session_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_agent_conversations_agent_session", ["agent_session_id"])
        batch.create_index("ix_agent_conversations_agent_session_id", ["agent_session_id"])

    with op.batch_alter_table("agent_conversation_turns") as batch:
        batch.drop_constraint("ck_agent_conversation_turns_status", type_="check")
        batch.add_column(sa.Column("active_slot", sa.String(36), nullable=True))
        batch.add_column(sa.Column("agent_run_id", sa.String(36), nullable=True))
        batch.create_check_constraint(
            "ck_agent_conversation_turns_status",
            "status IN ('queued', 'running', 'completed', 'proposal', 'failed', 'interrupted')",
        )
        batch.create_foreign_key(
            "fk_agent_conversation_turns_agent_run",
            "agent_runs",
            ["agent_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_agent_conversation_turns_active_slot", ["active_slot"])
        batch.create_unique_constraint("uq_agent_conversation_turns_agent_run", ["agent_run_id"])
        batch.create_index("ix_agent_conversation_turns_agent_run_id", ["agent_run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    incompatible_turns = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM agent_conversation_turns "
            "WHERE status IN ('queued', 'interrupted')"
        )
    ).scalar_one()
    if incompatible_turns:
        raise RuntimeError(
            "cannot downgrade ac126run1 while queued or interrupted conversation turns exist"
        )

    with op.batch_alter_table("agent_conversation_turns") as batch:
        batch.drop_index("ix_agent_conversation_turns_agent_run_id")
        batch.drop_constraint("uq_agent_conversation_turns_agent_run", type_="unique")
        batch.drop_constraint("uq_agent_conversation_turns_active_slot", type_="unique")
        batch.drop_constraint("fk_agent_conversation_turns_agent_run", type_="foreignkey")
        batch.drop_constraint("ck_agent_conversation_turns_status", type_="check")
        batch.create_check_constraint(
            "ck_agent_conversation_turns_status",
            "status IN ('running', 'completed', 'proposal', 'failed')",
        )
        batch.drop_column("agent_run_id")
        batch.drop_column("active_slot")

    with op.batch_alter_table("agent_conversations") as batch:
        batch.drop_index("ix_agent_conversations_agent_session_id")
        batch.drop_constraint("uq_agent_conversations_agent_session", type_="unique")
        batch.drop_constraint("fk_agent_conversations_agent_session", type_="foreignkey")
        batch.drop_column("agent_session_id")
        batch.drop_column("execution_binding")
