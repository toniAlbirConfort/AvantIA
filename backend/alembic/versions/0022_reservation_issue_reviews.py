"""Reviewed reservation incidents (double bookings, amounts, inactive apartments).

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already include this table.
    if sa.inspect(op.get_bind()).has_table("reservation_issue_reviews"):
        return
    op.create_table(
        "reservation_issue_reviews",
        sa.Column("issue_key", sa.String(length=320), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("issue_key"),
    )


def downgrade() -> None:
    op.drop_table("reservation_issue_reviews")
