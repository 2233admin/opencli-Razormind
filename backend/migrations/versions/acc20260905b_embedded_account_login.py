"""Website-driven embedded login and retained Profile reservations."""

import sqlalchemy as sa
from alembic import op

revision = "acc20260905b"
down_revision = "acc20260905a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("browser_accounts", sa.Column("site_url", sa.String(2048)))
    op.add_column("browser_accounts", sa.Column("login_target_id", sa.String(100)))
    op.add_column(
        "browser_instances",
        sa.Column("login_reserved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("browser_accounts", "platform", type_=sa.String(255))


def downgrade():
    op.drop_column("browser_accounts", "login_target_id")
    op.drop_column("browser_accounts", "site_url")
    op.drop_column("browser_instances", "login_reserved")
