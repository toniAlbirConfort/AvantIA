"""Add customers imported from the Avantio customer export.

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
    text = {
        "first_name": 120, "last_name": 120, "second_last_name": 120, "sex": 20,
        "nationality": 80, "street": 200, "street_number": 40, "door": 40,
        "postal_code": 40, "city": 120, "province": 120, "country": 80, "region": 120,
        "document_number": 60, "email": 200, "email_alt": 200, "phone": 80,
        "phone_alt1": 80, "phone_alt2": 80, "language": 40, "customer_class": 40,
        "customer_type": 40, "bank_account_masked": 40, "tax_code": 60,
        "accounting_account": 60, "stay_period": 120,
    }
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("avantio_id", sa.String(length=20), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("sort_name", sa.String(length=255), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        *(sa.Column(name, sa.String(length=size), nullable=True) for name, size in text.items()),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("registered_on", sa.Date(), nullable=True),
        sa.Column("email_opt_in", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("avantio_id"),
    )
    op.create_index("ix_customers_batch_id", "customers", ["batch_id"])
    op.create_index("ix_customers_sort_name", "customers", ["sort_name"])


def downgrade() -> None:
    op.drop_index("ix_customers_sort_name", table_name="customers")
    op.drop_index("ix_customers_batch_id", table_name="customers")
    op.drop_table("customers")
