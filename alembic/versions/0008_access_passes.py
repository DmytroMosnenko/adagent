"""access_passes: one-time, time-boxed full access (48 h pass)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 00:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "0008"
down_revision = "0007"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "access_passes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("stripe_session_id", sa.String(255), nullable=False),
        sa.Column("stripe_payment_intent", sa.String(255), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_session_id"),
    )
    op.create_index("ix_access_passes_user_id", "access_passes", ["user_id"])
    op.create_index("ix_access_passes_stripe_payment_intent", "access_passes", ["stripe_payment_intent"])
    op.create_index("idx_pass_user_expires", "access_passes", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("idx_pass_user_expires", table_name="access_passes")
    op.drop_index("ix_access_passes_stripe_payment_intent", table_name="access_passes")
    op.drop_index("ix_access_passes_user_id", table_name="access_passes")
    op.drop_table("access_passes")
