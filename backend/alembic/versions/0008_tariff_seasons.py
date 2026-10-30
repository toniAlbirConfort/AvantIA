"""Store the 2026–2027 read-only seasonal tariffs.

Revision ID: 0008
Revises: 0007
"""

from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

PERIODS = (
    (date(2026, 9, 1), date(2026, 11, 22)),
    (date(2026, 11, 23), date(2026, 12, 20)),
    (date(2026, 12, 21), date(2026, 12, 31)),
    (date(2027, 1, 1), date(2027, 1, 10)),
    (date(2027, 1, 11), date(2027, 1, 31)),
    (date(2027, 2, 1), date(2027, 3, 18)),
    (date(2027, 3, 19), date(2027, 3, 31)),
    (date(2027, 4, 1), date(2027, 4, 30)),
    (date(2027, 5, 1), date(2027, 6, 30)),
    (date(2027, 7, 1), date(2027, 8, 31)),
    (date(2027, 9, 1), date(2027, 9, 12)),
    (date(2027, 9, 13), date(2027, 11, 30)),
    (date(2027, 12, 1), date(2027, 12, 19)),
    (date(2027, 12, 20), date(2027, 12, 31)),
)

GROUP_RATES = (
    (
        "Albir Confort 1 dormitorio",
        (149, 109, 119, 129, 119, 129, 139, 119, 139, 189, 169, 159, 119, 129),
    ),
    (
        "AVENIDA 2 dorm / Golf II -2112-",
        (189, 149, 139, 149, 139, 149, 169, 149, 179, 259, 249, 209, 159, 149),
    ),
    (
        "AVENIDA 3 dormitorios",
        (219, 169, 159, 179, 169, 179, 199, 179, 239, 299, 289, 239, 189, 179),
    ),
    (
        "ESTRELLA 2 dormitorios",
        (169, 119, 129, 139, 129, 139, 159, 139, 199, 219, 209, 189, 129, 139),
    ),
)

SEED_ROWS = [
    {
        "tariff_name": tariff_name,
        "rate_plan": "SA",
        "start_date": start_date,
        "end_date": end_date,
        "nightly_rate": Decimal(rate).quantize(Decimal("0.01")),
    }
    for tariff_name, rates in GROUP_RATES
    for (start_date, end_date), rate in zip(PERIODS, rates, strict=True)
]


def upgrade() -> None:
    tariff_seasons = op.create_table(
        "tariff_seasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tariff_name", sa.String(length=180), nullable=False),
        sa.Column("rate_plan", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("nightly_rate", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_tariff_seasons_valid_range"),
        sa.CheckConstraint("nightly_rate >= 0", name="ck_tariff_seasons_nonnegative_rate"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tariff_name", "rate_plan", "start_date", name="uq_tariff_seasons_name_plan_start"
        ),
    )
    op.create_index(
        "ix_tariff_seasons_tariff_name", "tariff_seasons", ["tariff_name"], unique=False
    )
    op.bulk_insert(tariff_seasons, SEED_ROWS)


def downgrade() -> None:
    op.drop_index("ix_tariff_seasons_tariff_name", table_name="tariff_seasons")
    op.drop_table("tariff_seasons")
