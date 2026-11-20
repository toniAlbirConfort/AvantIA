"""Team reply from the Sent folder and model suggestion on each registration check.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

COLUMNS = (
    ("staff_checked_at", sa.DateTime(timezone=True)),
    ("staff_reply_at", sa.DateTime(timezone=True)),
    ("staff_reply_subject", sa.String(length=500)),
    ("staff_reply_sender", sa.String(length=255)),
    ("staff_reply_in_thread", sa.Boolean()),
    ("suggestion", sa.String(length=30)),
    ("suggestion_reason", sa.String(length=300)),
    ("suggestion_checked_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already include them.
    existing = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("registration_checks")
    }
    for name, kind in COLUMNS:
        if name not in existing:
            op.add_column("registration_checks", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("registration_checks", name)
