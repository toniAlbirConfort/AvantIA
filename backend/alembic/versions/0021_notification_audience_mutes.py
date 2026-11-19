"""Personal notifications (user audience) and per-user muted notification kinds.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("notifications")}
    if "user_id" not in columns:
        with op.batch_alter_table("notifications") as table:
            table.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
            table.create_foreign_key("fk_notifications_user_id_users", "users", ["user_id"], ["id"])
            table.create_index("ix_notifications_user_id", ["user_id"])
    # 0001 builds fresh databases from the current models, which already include this table.
    if not inspector.has_table("notification_mutes"):
        op.create_table(
            "notification_mutes",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=60), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("user_id", "kind"),
        )


def downgrade() -> None:
    op.drop_table("notification_mutes")
    with op.batch_alter_table("notifications") as table:
        table.drop_index("ix_notifications_user_id")
        table.drop_constraint("fk_notifications_user_id_users", type_="foreignkey")
        table.drop_column("user_id")
