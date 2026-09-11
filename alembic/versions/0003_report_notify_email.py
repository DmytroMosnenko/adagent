"""reports: add notify_email flag

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-10 00:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "0003"
down_revision = "0002"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("reports", "notify_email")
