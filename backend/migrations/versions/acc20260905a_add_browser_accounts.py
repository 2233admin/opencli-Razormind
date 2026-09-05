"""Add isolated browser login account metadata."""

import sqlalchemy as sa
from alembic import op

revision = "acc20260905a"
down_revision = "int20260905a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "browser_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column(
            "browser_instance_id",
            sa.String(36),
            sa.ForeignKey("browser_instances.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("profile_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_by", sa.String(255)),
    )
    if op.get_bind().dialect.name == "sqlite":
        # The legacy SQLite database does not globally enable foreign_keys.
        # Enforce just these ownership invariants even for concurrent writers.
        for operation in ("INSERT", "UPDATE"):
            op.execute(f"""CREATE TRIGGER account_instance_{operation.lower()}
                BEFORE {operation} ON browser_accounts
                WHEN NOT EXISTS (SELECT 1 FROM browser_instances
                    WHERE id = NEW.browser_instance_id AND profile_name = NEW.profile_name
                    AND profile_kind = 'authenticated')
                BEGIN SELECT RAISE(ABORT, 'account_profile_invalid'); END""")
        op.execute("""CREATE TRIGGER account_instance_delete
            BEFORE DELETE ON browser_instances
            WHEN EXISTS (SELECT 1 FROM browser_accounts WHERE browser_instance_id = OLD.id)
            BEGIN SELECT RAISE(ABORT, 'account_profile_reserved'); END""")
        op.execute("""CREATE TRIGGER account_instance_profile
            BEFORE UPDATE OF profile_name, profile_kind ON browser_instances
            WHEN (NEW.profile_name IS NOT OLD.profile_name
                OR NEW.profile_kind IS NOT OLD.profile_kind)
                AND EXISTS (SELECT 1 FROM browser_accounts WHERE browser_instance_id = OLD.id)
            BEGIN SELECT RAISE(ABORT, 'account_profile_reserved'); END""")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        for suffix in ("insert", "update", "delete", "profile"):
            op.execute(f"DROP TRIGGER IF EXISTS account_instance_{suffix}")
    op.drop_table("browser_accounts")
