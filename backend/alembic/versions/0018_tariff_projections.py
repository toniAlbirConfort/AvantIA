"""Yearly SA projections and hand-captured market prices.

Seeds the competitor prices of the Albir study of 13/11/2026 (report in 0013) so the
first projection can compare each SA group with the market.

Revision ID: 0018
Revises: 0017
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

ONE_BEDROOM = ("Albir Confort 1 dormitorio",)
TWO_BEDROOMS = ("ESTRELLA 2 dormitorios", "AVENIDA 2 dorm / Golf II -2112-")
THREE_BEDROOMS = ("AVENIDA 3 dormitorios",)
# (check-in, nights) of the three searches in the study.
SEARCHES = ((date(2026, 11, 27), 3), (date(2027, 5, 14), 3), (date(2027, 8, 9), 7))
# Lowest nightly price per competitor for each search; None means no availability.
STUDY_PRICES = (
    (ONE_BEDROOM, "Albir Experience", "1 dorm.", (77, 111, 198)),
    (ONE_BEDROOM, "Albir Palace", "1 dorm.", (80, None, 140)),
    (ONE_BEDROOM, "Albir Garden Resort", "1 dorm.", (80, None, 263)),
    (ONE_BEDROOM, "Alojamiento Life 1", "1 dorm.", (96, 132, 166)),
    (ONE_BEDROOM, "Delfin Natura (Goya RE)", "1 dorm.", (124, 232, 463)),
    (ONE_BEDROOM, "Alojamiento Life 2", "1 dorm.", (125, 146, 184)),
    (ONE_BEDROOM, "Albir Ibo Holiday", "1 dorm.", (159, None, 307)),
    (TWO_BEDROOMS, "Apartamentos BCL Playa Albir", "2 dorm.", (73, 114, 174)),
    (TWO_BEDROOMS, "Almar Albir", "2 dorm.", (129, None, None)),
    (TWO_BEDROOMS, "Apartamento Calalbir", "2 dorm.", (173, None, None)),
    (TWO_BEDROOMS, "Delfin Natura (Goya RE)", "2 dorm.", (177, 288, 444)),
    (THREE_BEDROOMS, "Delfin Natura (Goya RE)", "3 dorm.", (201, 352, 485)),
)


def _seed_rows() -> list[dict]:
    created_at = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    rows = []
    for groups, competitor, unit, prices in STUDY_PRICES:
        for (stay_date, nights), price in zip(SEARCHES, prices, strict=True):
            if price is None:
                continue
            for group in groups:
                rows.append({
                    "tariff_name": group,
                    "competitor": competitor,
                    "unit_label": unit,
                    "stay_date": stay_date,
                    "nights": nights,
                    "guests": 2,
                    "nightly_rate": Decimal(price),
                    "source": "Booking.com, consulta manual (estudio de competencia en Albir)",
                    "observed_on": date(2026, 11, 13),
                    "notes": "Precio más bajo por noche, impuestos incluidos",
                    "created_by_id": None,
                    "created_at": created_at,
                })
    return rows


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 0001 builds fresh databases from the current models, which already include these tables.
    if not inspector.has_table("tariff_market_observations"):
        op.create_table(
            "tariff_market_observations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tariff_name", sa.String(length=180), nullable=False),
            sa.Column("competitor", sa.String(length=180), nullable=False),
            sa.Column("unit_label", sa.String(length=120), nullable=True),
            sa.Column("stay_date", sa.Date(), nullable=False),
            sa.Column("nights", sa.Integer(), nullable=False),
            sa.Column("guests", sa.Integer(), nullable=False),
            sa.Column("nightly_rate", sa.Numeric(10, 2), nullable=False),
            sa.Column("source", sa.String(length=255), nullable=False),
            sa.Column("observed_on", sa.Date(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("nights >= 1", name="ck_tariff_market_observations_nights"),
            sa.CheckConstraint("guests >= 1", name="ck_tariff_market_observations_guests"),
            sa.CheckConstraint("nightly_rate > 0", name="ck_tariff_market_observations_rate"),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_tariff_market_observations_tariff_name",
            "tariff_market_observations",
            ["tariff_name"],
        )
        op.create_index(
            "ix_tariff_market_observations_stay_date",
            "tariff_market_observations",
            ["stay_date"],
        )
    if not inspector.has_table("tariff_projections"):
        op.create_table(
            "tariff_projections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("base_year", sa.Integer(), nullable=False),
            sa.Column("target_year", sa.Integer(), nullable=False),
            sa.Column("tariff_names", sa.JSON(), nullable=False),
            sa.Column("parameters", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("overrides", sa.JSON(), nullable=False),
            sa.Column("move_easter", sa.Boolean(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("algorithm_version", sa.String(length=20), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("applied_by_id", sa.Integer(), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("target_year > base_year", name="ck_tariff_projections_years"),
            sa.CheckConstraint(
                "status IN ('draft', 'applied')", name="ck_tariff_projections_status"
            ),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["applied_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tariff_projections_status", "tariff_projections", ["status"])
    observations = sa.table(
        "tariff_market_observations",
        sa.column("tariff_name", sa.String),
        sa.column("competitor", sa.String),
        sa.column("unit_label", sa.String),
        sa.column("stay_date", sa.Date),
        sa.column("nights", sa.Integer),
        sa.column("guests", sa.Integer),
        sa.column("nightly_rate", sa.Numeric),
        sa.column("source", sa.String),
        sa.column("observed_on", sa.Date),
        sa.column("notes", sa.Text),
        sa.column("created_by_id", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    existing = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM tariff_market_observations")
    ).scalar()
    if not existing:
        op.bulk_insert(observations, _seed_rows())


def downgrade() -> None:
    op.drop_index("ix_tariff_projections_status", table_name="tariff_projections")
    op.drop_table("tariff_projections")
    op.drop_index(
        "ix_tariff_market_observations_stay_date", table_name="tariff_market_observations"
    )
    op.drop_index(
        "ix_tariff_market_observations_tariff_name", table_name="tariff_market_observations"
    )
    op.drop_table("tariff_market_observations")
