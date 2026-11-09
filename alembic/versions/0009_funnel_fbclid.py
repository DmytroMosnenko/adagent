"""funnel_events: has_fbclid (Meta ads attribution)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09 00:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "0009"
down_revision = "0008"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column("funnel_events", sa.Column("has_fbclid", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("funnel_events", "has_fbclid")
