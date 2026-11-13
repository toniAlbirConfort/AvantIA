"""Reports catalogue, seeded with the Albir competitor price study of 13/11/2026.

Revision ID: 0013
Revises: 0012
"""

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
INITIAL_REPORTS = (
    {
        "title": "Precios de la competencia profesional en Albir",
        "description": (
            "Tarifas SA 2026-27 frente a los precios en Booking de 12 alojamientos "
            "profesionales de El Albir, en temporada baja, media y alta."
        ),
        "source": "claude",
        "file": "2026-11-13-competencia-albir.html",
        "original_url": None,
        "created_at": datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    },
)


def upgrade() -> None:
    reports = op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("original_url", sa.String(length=500), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('claude', 'app')", name="ck_reports_source"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_source", "reports", ["source"])
    op.create_index("ix_reports_created_at", "reports", ["created_at"])
    op.bulk_insert(
        reports,
        [
            {
                "title": item["title"],
                "description": item["description"],
                "source": item["source"],
                "html_body": (SEEDS / item["file"]).read_text(encoding="utf-8"),
                "original_url": item["original_url"],
                "created_by_id": None,
                "created_at": item["created_at"],
            }
            for item in INITIAL_REPORTS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_created_at", table_name="reports")
    op.drop_index("ix_reports_source", table_name="reports")
    op.drop_table("reports")
