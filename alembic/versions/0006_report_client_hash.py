"""reports: add client_hash (free-tier abuse guard)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24 00:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "0006"
down_revision = "0005"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("client_hash", sa.String(64), nullable=True))
    op.create_index("ix_reports_client_hash", "reports", ["client_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reports_client_hash", table_name="reports")
    op.drop_column("reports", "client_hash")
