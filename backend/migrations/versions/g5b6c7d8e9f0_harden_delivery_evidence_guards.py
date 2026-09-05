"""harden controlled delivery evidence guards

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "g5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None

_EVIDENCE_TABLES = (
    "delivery_execution_results",
    "delivery_execution_reconciliations",
    "controlled_receiver_deliveries",
    "controlled_receiver_nonces",
)


def _sqlite_guards() -> None:
    for table in _EVIDENCE_TABLES:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table.lower()}_append_only_{action.lower()} "
                f"BEFORE {action} ON {table} BEGIN "
                f"SELECT RAISE(ABORT, '{table} is append-only'); END"
            )
    for event in ("INSERT", "UPDATE"):
        columns = "" if event == "INSERT" else " OF final_result_id, final_reconciliation_id"
        op.execute(
            f"CREATE TRIGGER trg_delivery_execution_final_links_{event.lower()} "
            f"BEFORE {event}{columns} ON delivery_executions "
            "BEGIN "
            "SELECT CASE WHEN NEW.final_result_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM delivery_execution_results WHERE id = "
            "NEW.final_result_id AND execution_id = NEW.id) "
            "THEN RAISE(ABORT, 'final result must belong to execution') END; "
            "SELECT CASE WHEN NEW.final_reconciliation_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM delivery_execution_reconciliations WHERE id = "
            "NEW.final_reconciliation_id AND execution_id = NEW.id) "
            "THEN RAISE(ABORT, 'final reconciliation must belong to execution') END; END"
        )


def _postgres_guards() -> None:
    op.execute(
        "CREATE FUNCTION delivery_evidence_append_only() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'delivery evidence is append-only'; END; $$"
    )
    for table in _EVIDENCE_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table.lower()}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION delivery_evidence_append_only()"
        )
    op.execute(
        "CREATE FUNCTION delivery_execution_final_links_guard() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN "
        "IF NEW.final_result_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM delivery_execution_results WHERE id = "
        "NEW.final_result_id AND execution_id = NEW.id) THEN "
        "RAISE EXCEPTION 'final result must belong to execution'; END IF; "
        "IF NEW.final_reconciliation_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM delivery_execution_reconciliations WHERE id = "
        "NEW.final_reconciliation_id AND execution_id = NEW.id) THEN "
        "RAISE EXCEPTION 'final reconciliation must belong to execution'; "
        "END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER trg_delivery_execution_final_links BEFORE INSERT OR UPDATE OF "
        "final_result_id, final_reconciliation_id ON delivery_executions FOR EACH ROW "
        "EXECUTE FUNCTION delivery_execution_final_links_guard()"
    )


def upgrade() -> None:
    # Legacy plugin databases may not carry the retired Studio parent tables
    # referenced by these delivery FKs. Avoid resolving unrelated targets
    # while rebuilding the local SQLite tables.
    reflect_kwargs = {"resolve_fks": False}
    with op.batch_alter_table("delivery_executions", reflect_kwargs=reflect_kwargs) as batch:
        batch.add_column(sa.Column("send_started_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("delivery_execution_results", reflect_kwargs=reflect_kwargs) as batch:
        batch.create_check_constraint(
            "ck_delivery_result_attempt_range",
            "attempt_number BETWEEN 1 AND 3",
        )
        batch.create_check_constraint(
            "ck_delivery_result_outcome",
            "outcome IN ('accepted', 'rejected', 'unknown')",
        )
    with op.batch_alter_table(
        "delivery_execution_reconciliations", reflect_kwargs=reflect_kwargs
    ) as batch:
        batch.create_check_constraint(
            "ck_delivery_reconciliation_outcome",
            "outcome IN ('accepted', 'rejected')",
        )
    with op.batch_alter_table(
        "controlled_receiver_deliveries", reflect_kwargs=reflect_kwargs
    ) as batch:
        batch.create_check_constraint(
            "ck_receiver_delivery_status",
            "durable_status IN ('accepted', 'rejected')",
        )
    if op.get_bind().dialect.name == "sqlite":
        _sqlite_guards()
    else:
        _postgres_guards()


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_delivery_execution_final_links")
        op.execute("DROP TRIGGER IF EXISTS trg_delivery_execution_final_links_update")
        op.execute("DROP TRIGGER IF EXISTS trg_delivery_execution_final_links_insert")
        for table in _EVIDENCE_TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"DROP TRIGGER trg_{table.lower()}_append_only_{action.lower()}")
    else:
        op.execute("DROP TRIGGER trg_delivery_execution_final_links ON delivery_executions")
        for table in _EVIDENCE_TABLES:
            op.execute(f"DROP TRIGGER trg_{table.lower()}_append_only ON {table}")
        op.execute("DROP FUNCTION delivery_execution_final_links_guard()")
        op.execute("DROP FUNCTION delivery_evidence_append_only()")
    reflect_kwargs = {"resolve_fks": False}
    with op.batch_alter_table(
        "controlled_receiver_deliveries", reflect_kwargs=reflect_kwargs
    ) as batch:
        batch.drop_constraint("ck_receiver_delivery_status", type_="check")
    with op.batch_alter_table(
        "delivery_execution_reconciliations", reflect_kwargs=reflect_kwargs
    ) as batch:
        batch.drop_constraint("ck_delivery_reconciliation_outcome", type_="check")
    with op.batch_alter_table("delivery_execution_results", reflect_kwargs=reflect_kwargs) as batch:
        batch.drop_constraint("ck_delivery_result_outcome", type_="check")
        batch.drop_constraint("ck_delivery_result_attempt_range", type_="check")
    with op.batch_alter_table("delivery_executions", reflect_kwargs=reflect_kwargs) as batch:
        batch.drop_column("send_started_at")
