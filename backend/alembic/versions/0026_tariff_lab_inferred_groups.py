"""Infer tariff groups from reservations and record official holiday sources.

The tariff laboratory no longer stores manual property-to-group assignments: they
are inferred from the catalog and reservation prices. Holidays come from official
sources, so each row keeps its reference and link, and manual rows are removed.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already match this state.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("tariff_group_assignments"):
        op.drop_index(
            "ix_tariff_group_assignments_tariff_name", table_name="tariff_group_assignments"
        )
        op.drop_table("tariff_group_assignments")
    columns = {column["name"] for column in inspector.get_columns("tariff_holidays")}
    if "source_reference" not in columns:
        op.add_column(
            "tariff_holidays", sa.Column("source_reference", sa.String(length=120), nullable=True)
        )
    if "source_url" not in columns:
        op.add_column(
            "tariff_holidays", sa.Column("source_url", sa.String(length=500), nullable=True)
        )
    op.execute("DELETE FROM tariff_holidays WHERE source = 'manual'")


def downgrade() -> None:
    op.drop_column("tariff_holidays", "source_url")
    op.drop_column("tariff_holidays", "source_reference")
    op.create_table(
        "tariff_group_assignments",
        sa.Column("property_id", sa.String(length=100), nullable=False),
        sa.Column("tariff_name", sa.String(length=180), nullable=False),
        sa.Column("municipality", sa.String(length=150), nullable=False),
        sa.Column("province", sa.String(length=150), nullable=True),
        sa.Column("autonomous_community", sa.String(length=150), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("property_id"),
    )
    op.create_index(
        "ix_tariff_group_assignments_tariff_name",
        "tariff_group_assignments",
        ["tariff_name"],
    )
