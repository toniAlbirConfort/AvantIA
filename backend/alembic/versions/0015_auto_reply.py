"""Email Date header and out-of-hours acknowledgements.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("email_date", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "auto_replies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index("ix_auto_replies_conversation_id", "auto_replies", ["conversation_id"])
    op.create_index("ix_auto_replies_recipient", "auto_replies", ["recipient"])
    op.create_index("ix_auto_replies_mode", "auto_replies", ["mode"])


def downgrade() -> None:
    op.drop_index("ix_auto_replies_mode", table_name="auto_replies")
    op.drop_index("ix_auto_replies_recipient", table_name="auto_replies")
    op.drop_index("ix_auto_replies_conversation_id", table_name="auto_replies")
    op.drop_table("auto_replies")
    op.drop_column("messages", "email_date")
