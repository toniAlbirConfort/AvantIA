"""Pending becomes total minus paid; the export's own figure is kept as a reference.

Avantio's "Pendiente" column follows the payment plan and can disagree with the booking
card, which shows total - paid. Snapshots are rewritten with total - paid.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("reservation_details")}
    if "export_pending" not in columns:
        op.add_column(
            "reservation_details",
            sa.Column("export_pending", sa.Numeric(precision=12, scale=2), nullable=True),
        )
    op.execute(
        """
        UPDATE reservation_details SET export_pending = (
            SELECT s.pending FROM reservation_snapshots s
            WHERE s.batch_id = reservation_details.batch_id
              AND s.locator = reservation_details.locator
        )
        WHERE export_pending IS NULL
        """
    )
    op.execute("UPDATE reservation_snapshots SET pending = total - paid")


def downgrade() -> None:
    # The original pending of older snapshots is not recoverable; only the column goes.
    op.drop_column("reservation_details", "export_pending")
