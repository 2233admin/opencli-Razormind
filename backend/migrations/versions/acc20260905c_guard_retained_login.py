"""Guard retained login profiles even after their account record is deleted."""

import sqlalchemy as sa
from alembic import op

revision = "acc20260905c"
down_revision = "acc20260905b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("browser_accounts", sa.Column("operation_token", sa.String(36)))
    op.add_column("browser_accounts", sa.Column("operation_until", sa.DateTime(timezone=True)))
    op.add_column("browser_accounts", sa.Column("deletion_clear", sa.Boolean()))
    if op.get_bind().dialect.name == "sqlite":
        op.execute("""CREATE TRIGGER retained_login_delete
            BEFORE DELETE ON browser_instances WHEN OLD.login_reserved = 1
            BEGIN SELECT RAISE(ABORT, 'login_profile_reserved'); END""")
        op.execute("""CREATE TRIGGER retained_login_profile
            BEFORE UPDATE OF profile_name, profile_kind ON browser_instances
            WHEN OLD.login_reserved = 1 AND (NEW.profile_name IS NOT OLD.profile_name
                OR NEW.profile_kind IS NOT OLD.profile_kind)
            BEGIN SELECT RAISE(ABORT, 'login_profile_reserved'); END""")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS retained_login_delete")
        op.execute("DROP TRIGGER IF EXISTS retained_login_profile")
    op.drop_column("browser_accounts", "deletion_clear")
    op.drop_column("browser_accounts", "operation_until")
    op.drop_column("browser_accounts", "operation_token")
