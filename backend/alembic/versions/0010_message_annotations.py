"""Add manual message annotations used as ground truth for the classifier.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_annotations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("intent", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=24), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("split", sa.String(length=12), nullable=False),
        sa.Column("predicted_label", sa.String(length=24), nullable=True),
        sa.Column("predicted_confidence", sa.String(length=12), nullable=True),
        sa.Column("predicted_data", sa.JSON(), nullable=True),
        sa.Column("predicted_model", sa.String(length=100), nullable=True),
        sa.Column("annotated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["annotated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index("ix_message_annotations_intent", "message_annotations", ["intent"])
    op.create_index("ix_message_annotations_split", "message_annotations", ["split"])


def downgrade() -> None:
    op.drop_index("ix_message_annotations_split", table_name="message_annotations")
    op.drop_index("ix_message_annotations_intent", table_name="message_annotations")
    op.drop_table("message_annotations")
