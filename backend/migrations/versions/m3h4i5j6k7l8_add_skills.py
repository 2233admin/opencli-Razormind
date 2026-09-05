"""add skills

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
Create Date: 2026-06-30

"""
import sqlalchemy as sa
from alembic import op

revision = 'm3h4i5j6k7l8'
down_revision = 'l2g3h4i5j6k7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'skills',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('domain', sa.String(100), nullable=False),
        sa.Column('capability', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('scope', sa.Text(), nullable=True),
        sa.Column('skill_md', sa.Text(), nullable=False, server_default=''),
        sa.Column('elements', sa.JSON(), nullable=False),
        sa.Column('source_trace', sa.String(255), nullable=True),
        sa.Column('distill_model', sa.String(255), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='draft'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('domain', 'capability', name='uq_skill_domain_capability'),
    )


def downgrade() -> None:
    op.drop_table('skills')
