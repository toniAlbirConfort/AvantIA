"""Rich-text reply drafts and per-operator email signatures.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("html_body", sa.Text(), nullable=True))
    op.add_column("drafts", sa.Column("subject", sa.String(length=500), nullable=True))
    op.add_column("drafts", sa.Column("cc", sa.String(length=1000), nullable=True))
    op.create_table(
        "email_signatures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mailbox", sa.String(length=255), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "mailbox"),
    )
    op.create_index("ix_email_signatures_user_id", "email_signatures", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_email_signatures_user_id", table_name="email_signatures")
    op.drop_table("email_signatures")
    op.drop_column("drafts", "cc")
    op.drop_column("drafts", "subject")
    op.drop_column("drafts", "html_body")
