"""Extra columns of the Avantio reservation export, one row per reservation.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(precision=12, scale=2)


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already include this table.
    if sa.inspect(op.get_bind()).has_table("reservation_details"):
        return
    op.create_table(
        "reservation_details",
        sa.Column("locator", sa.String(length=150), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("rent_total", MONEY, nullable=True),
        sa.Column("rent_net", MONEY, nullable=True),
        sa.Column("rent_tax", MONEY, nullable=True),
        sa.Column("extras_total", MONEY, nullable=True),
        sa.Column("extras_net", MONEY, nullable=True),
        sa.Column("extras_tax", MONEY, nullable=True),
        sa.Column("commission", MONEY, nullable=True),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("invoice_total", MONEY, nullable=True),
        sa.Column("agent_locator", sa.String(length=150), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("customer_avantio_id", sa.String(length=40), nullable=True),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("occupant_email", sa.String(length=255), nullable=True),
        sa.Column("handled_by", sa.String(length=150), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"]),
        sa.PrimaryKeyConstraint("locator"),
    )
    for column in ("customer_avantio_id", "customer_email", "occupant_email"):
        op.create_index(f"ix_reservation_details_{column}", "reservation_details", [column])


def downgrade() -> None:
    for column in ("customer_avantio_id", "customer_email", "occupant_email"):
        op.drop_index(f"ix_reservation_details_{column}", table_name="reservation_details")
    op.drop_table("reservation_details")
