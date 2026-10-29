"""Persist message classifications and conversation queue state.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("active_classification", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "active_classification_message_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("classification_open", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "conversations",
        sa.Column("classification_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("classification_resolved_by_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_classification_resolved_by_id_users",
        "conversations",
        "users",
        ["classification_resolved_by_id"],
        ["id"],
    )
    op.create_index(
        "ix_conversations_active_classification",
        "conversations",
        ["active_classification"],
    )

    op.add_column(
        "messages",
        sa.Column("classification_label", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("classification_confidence", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("classification_reason", sa.String(length=300), nullable=True),
    )
    op.add_column("messages", sa.Column("classification_data", sa.JSON(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("classification_override", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("classification_model", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("classification_reviewed_by_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("classification_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_classification_reviewed_by_id_users",
        "messages",
        "users",
        ["classification_reviewed_by_id"],
        ["id"],
    )
    op.create_index("ix_messages_classification_label", "messages", ["classification_label"])


def downgrade() -> None:
    op.drop_index("ix_messages_classification_label", table_name="messages")
    op.drop_constraint(
        "fk_messages_classification_reviewed_by_id_users", "messages", type_="foreignkey"
    )
    for column in (
        "classification_reviewed_at",
        "classification_reviewed_by_id",
        "classification_model",
        "classification_override",
        "classification_data",
        "classification_reason",
        "classification_confidence",
        "classification_label",
    ):
        op.drop_column("messages", column)

    op.drop_index("ix_conversations_active_classification", table_name="conversations")
    op.drop_constraint(
        "fk_conversations_classification_resolved_by_id_users",
        "conversations",
        type_="foreignkey",
    )
    for column in (
        "classification_resolved_by_id",
        "classification_resolved_at",
        "classification_open",
        "active_classification_message_id",
        "active_classification",
    ):
        op.drop_column("conversations", column)
