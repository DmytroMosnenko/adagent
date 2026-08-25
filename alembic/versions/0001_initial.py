"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id",                 sa.Integer(),      primary_key=True, autoincrement=True),
        sa.Column("email",              sa.String(255),    nullable=False),
        sa.Column("stripe_customer_id", sa.String(255),    nullable=True),
        sa.Column("created_at",         sa.DateTime(),     server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("stripe_customer_id"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "sessions",
        sa.Column("token",      sa.String(64),  primary_key=True),
        sa.Column("user_id",    sa.Integer(),   sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(),  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(),  nullable=False),
    )
    op.create_index("idx_session_user", "sessions", ["user_id"])

    op.create_table(
        "magic_links",
        sa.Column("token",      sa.String(64),  primary_key=True),
        sa.Column("email",      sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(),  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(),  nullable=False),
        sa.Column("used",       sa.Boolean(),   nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_magic_links_email", "magic_links", ["email"])

    op.create_table(
        "subscriptions",
        sa.Column("id",                     sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("user_id",                sa.Integer(),     sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_customer_id",     sa.String(255),   nullable=False),
        sa.Column("stripe_subscription_id", sa.String(255),   nullable=False),
        sa.Column("stripe_price_id",        sa.String(255),   nullable=False),
        sa.Column("plan_period",            sa.String(20),    nullable=False, server_default="monthly"),
        sa.Column("status",                 sa.String(50),    nullable=False),
        sa.Column("current_period_end",     sa.DateTime(),    nullable=False),
        sa.Column("created_at",             sa.DateTime(),    server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",             sa.DateTime(),    server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("stripe_subscription_id"),
    )
    op.create_index("ix_subscriptions_stripe_customer", "subscriptions", ["stripe_customer_id"])

    op.create_table(
        "reports",
        sa.Column("id",                    sa.String(32),    primary_key=True),
        sa.Column("user_id",               sa.Integer(),     sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filter_url",            sa.String(2048),  nullable=False),
        sa.Column("prompt_preset",         sa.String(64),    nullable=True),
        sa.Column("custom_ad_prompt",      sa.Text(),        nullable=True),
        sa.Column("custom_summary_prompt", sa.Text(),        nullable=True),
        sa.Column("status",                sa.Enum("pending", "running", "done", "failed"), nullable=False, server_default="pending"),
        sa.Column("ads_found",             sa.Integer(),     nullable=True),
        sa.Column("ads_analyzed",          sa.Integer(),     nullable=True),
        sa.Column("is_limited",            sa.Boolean(),     nullable=False, server_default=sa.text("1")),
        sa.Column("report_path",           sa.String(512),   nullable=True),
        sa.Column("result_json",           sa.Text(),        nullable=True),
        sa.Column("created_at",            sa.DateTime(),    server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at",           sa.DateTime(),    nullable=True),
        sa.Column("error_message",         sa.Text(),        nullable=True),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("subscriptions")
    op.drop_table("magic_links")
    op.drop_table("sessions")
    op.drop_table("users")
