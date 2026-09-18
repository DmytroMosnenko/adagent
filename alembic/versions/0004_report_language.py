"""reports: add report_language

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18 00:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "0004"
down_revision = "0003"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("report_language", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "report_language")
