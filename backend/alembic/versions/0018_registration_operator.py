"""Operator outcome of an unregistered-consultation alert (who resolved it and why).

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("registration_checks")
    }
    with op.batch_alter_table("registration_checks") as table:
        if "resolved_by_id" not in columns:
            table.add_column(sa.Column("resolved_by_id", sa.Integer(), nullable=True))
            table.create_foreign_key(
                "fk_registration_checks_resolved_by_id_users", "users", ["resolved_by_id"], ["id"]
            )
        if "note" not in columns:
            table.add_column(sa.Column("note", sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("registration_checks") as table:
        table.drop_constraint("fk_registration_checks_resolved_by_id_users", type_="foreignkey")
        table.drop_column("note")
        table.drop_column("resolved_by_id")
