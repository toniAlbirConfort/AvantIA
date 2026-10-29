"""Expose the active Windows-host reservation sync interval.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_sync_states",
        sa.Column("interval_hours", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reservation_sync_states", "interval_hours")
