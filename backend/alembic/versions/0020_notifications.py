"""In-app notifications and per-user read marks.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 0001 builds fresh databases from the current models, which already include these tables.
    if not inspector.has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=60), nullable=False),
            sa.Column("severity", sa.String(length=10), nullable=False),
            sa.Column("audience", sa.String(length=10), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("body", sa.String(length=500), nullable=True),
            sa.Column("link", sa.String(length=300), nullable=True),
            sa.Column("dedupe_key", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("occurrences", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dismissed_by_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["dismissed_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_notifications_kind", "notifications", ["kind"])
        op.create_index("ix_notifications_dedupe_key", "notifications", ["dedupe_key"])
        op.create_index("ix_notifications_status", "notifications", ["status"])
        op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    if not inspector.has_table("notification_reads"):
        op.create_table(
            "notification_reads",
            sa.Column("notification_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("notification_id", "user_id"),
        )


def downgrade() -> None:
    op.drop_table("notification_reads")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index("ix_notifications_dedupe_key", table_name="notifications")
    op.drop_index("ix_notifications_kind", table_name="notifications")
    op.drop_table("notifications")
