"""Email consultations checked against Avantio reservations (unregistered-inquiry alert).

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already include this table.
    if sa.inspect(op.get_bind()).has_table("registration_checks"):
        return
    op.create_table(
        "registration_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=60), nullable=True),
        sa.Column("locator", sa.String(length=150), nullable=True),
        sa.Column("mode", sa.String(length=12), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index(
        "ix_registration_checks_customer_email", "registration_checks", ["customer_email"]
    )
    op.create_index("ix_registration_checks_due_at", "registration_checks", ["due_at"])
    op.create_index("ix_registration_checks_status", "registration_checks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_registration_checks_status", table_name="registration_checks")
    op.drop_index("ix_registration_checks_due_at", table_name="registration_checks")
    op.drop_index("ix_registration_checks_customer_email", table_name="registration_checks")
    op.drop_table("registration_checks")
