"""Add tariff laboratory mappings, holidays, and saved studies.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_table(
        "tariff_holidays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("region", sa.String(length=150), nullable=True),
        sa.Column("municipality", sa.String(length=150), nullable=True),
        sa.Column("source", sa.String(length=255), server_default="manual", nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('national', 'regional', 'municipal')",
            name="ck_tariff_holidays_scope",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "holiday_date",
            "name",
            "scope",
            "region",
            "municipality",
            name="uq_tariff_holidays_identity",
        ),
    )
    op.create_index("ix_tariff_holidays_holiday_date", "tariff_holidays", ["holiday_date"])
    op.create_table(
        "tariff_studies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("guests", sa.Integer(), nullable=False),
        sa.Column("nights", sa.Integer(), nullable=False),
        sa.Column("tariff_names", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("algorithm_version", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("period_end >= period_start", name="ck_tariff_studies_valid_range"),
        sa.CheckConstraint("guests >= 1", name="ck_tariff_studies_valid_guests"),
        sa.CheckConstraint("nights >= 1", name="ck_tariff_studies_valid_nights"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed')",
            name="ck_tariff_studies_status",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tariff_studies_status", "tariff_studies", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tariff_studies_status", table_name="tariff_studies")
    op.drop_table("tariff_studies")
    op.drop_index("ix_tariff_holidays_holiday_date", table_name="tariff_holidays")
    op.drop_table("tariff_holidays")
    op.drop_index(
        "ix_tariff_group_assignments_tariff_name", table_name="tariff_group_assignments"
    )
    op.drop_table("tariff_group_assignments")
