"""Last email the team sent to each auto-reply recipient (from the Sent folder).

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

COLUMNS = (
    ("staff_checked_at", sa.DateTime(timezone=True)),
    ("staff_last_sent_at", sa.DateTime(timezone=True)),
    ("staff_last_subject", sa.String(length=500)),
    ("staff_last_in_thread", sa.Boolean()),
)


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already include them.
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("auto_replies")}
    for name, kind in COLUMNS:
        if name not in existing:
            op.add_column("auto_replies", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("auto_replies", name)
