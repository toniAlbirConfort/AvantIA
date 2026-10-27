"""Store sanitized HTML email bodies for readable rendering.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "html_body" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("messages")
    }:
        op.add_column("messages", sa.Column("html_body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "html_body")
