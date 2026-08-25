"""report id: String(32) → BINARY(16) UUID v7

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-02 00:00:00
"""
import sqlalchemy as sa
from sqlalchemy import BINARY
from alembic import op

revision    = "0002"
down_revision = "0001"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # Drop and recreate reports table with BINARY(16) primary key.
    # Safe because this migration runs before any production data exists.
    op.drop_table("reports")
    op.create_table(
        "reports",
        sa.Column("id",                    BINARY(16),   primary_key=True),
        sa.Column("user_id",               sa.Integer(),  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filter_url",            sa.String(2048), nullable=False),
        sa.Column("prompt_preset",         sa.String(64),   nullable=True),
        sa.Column("custom_ad_prompt",      sa.Text(),       nullable=True),
        sa.Column("custom_summary_prompt", sa.Text(),       nullable=True),
        sa.Column("status",                sa.Enum("pending", "running", "done", "failed"),
                                           nullable=False, server_default="pending"),
        sa.Column("ads_found",             sa.Integer(),    nullable=True),
        sa.Column("ads_analyzed",          sa.Integer(),    nullable=True),
        sa.Column("is_limited",            sa.Boolean(),    nullable=False, server_default=sa.text("1")),
        sa.Column("report_path",           sa.String(512),  nullable=True),
        sa.Column("result_json",           sa.Text(),       nullable=True),
        sa.Column("created_at",            sa.DateTime(),   server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at",           sa.DateTime(),   nullable=True),
        sa.Column("error_message",         sa.Text(),       nullable=True),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])


def downgrade() -> None:
    op.drop_table("reports")
    op.create_table(
        "reports",
        sa.Column("id",                    sa.String(32),  primary_key=True),
        sa.Column("user_id",               sa.Integer(),   sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filter_url",            sa.String(2048), nullable=False),
        sa.Column("prompt_preset",         sa.String(64),   nullable=True),
        sa.Column("custom_ad_prompt",      sa.Text(),       nullable=True),
        sa.Column("custom_summary_prompt", sa.Text(),       nullable=True),
        sa.Column("status",                sa.Enum("pending", "running", "done", "failed"),
                                           nullable=False, server_default="pending"),
        sa.Column("ads_found",             sa.Integer(),    nullable=True),
        sa.Column("ads_analyzed",          sa.Integer(),    nullable=True),
        sa.Column("is_limited",            sa.Boolean(),    nullable=False, server_default=sa.text("1")),
        sa.Column("report_path",           sa.String(512),  nullable=True),
        sa.Column("result_json",           sa.Text(),       nullable=True),
        sa.Column("created_at",            sa.DateTime(),   server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at",           sa.DateTime(),   nullable=True),
        sa.Column("error_message",         sa.Text(),       nullable=True),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])
