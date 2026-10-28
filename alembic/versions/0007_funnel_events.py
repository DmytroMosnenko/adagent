"""funnel_events: anonymous landing-page funnel tracking

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28 00:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "0007"
down_revision = "0006"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "funnel_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sid", sa.String(32), nullable=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("has_gclid", sa.Boolean(), nullable=True),
        sa.Column("device", sa.String(8), nullable=False, server_default="other"),
        sa.Column("ui_lang", sa.String(4), nullable=True),
        sa.Column("utm_source", sa.String(64), nullable=True),
        sa.Column("utm_campaign", sa.String(128), nullable=True),
        sa.Column("ref_host", sa.String(128), nullable=True),
        sa.Column("url_host", sa.String(16), nullable=True),
        sa.Column("url_label", sa.String(48), nullable=True),
        sa.Column("detail", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_funnel_created_event", "funnel_events", ["created_at", "event"])
    op.create_index("ix_funnel_sid", "funnel_events", ["sid"])
    op.create_index("ix_funnel_event_detail", "funnel_events", ["event", "detail"])


def downgrade() -> None:
    op.drop_index("ix_funnel_event_detail", table_name="funnel_events")
    op.drop_index("ix_funnel_sid", table_name="funnel_events")
    op.drop_index("ix_funnel_created_event", table_name="funnel_events")
    op.drop_table("funnel_events")
