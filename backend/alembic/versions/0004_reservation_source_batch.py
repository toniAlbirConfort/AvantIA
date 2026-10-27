"""Track the CSV batch that last supplied each consolidated reservation.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("reservation_snapshots")}
    if "source_batch_id" not in columns:
        op.add_column(
            "reservation_snapshots", sa.Column("source_batch_id", sa.Integer(), nullable=True)
        )
    if op.get_bind().dialect.name != "sqlite":
        foreign_keys = {
            key["name"] for key in inspector.get_foreign_keys("reservation_snapshots")
        }
        if "fk_reservation_snapshots_source_batch" not in foreign_keys:
            op.create_foreign_key(
                "fk_reservation_snapshots_source_batch",
                "reservation_snapshots",
                "import_batches",
                ["source_batch_id"],
                ["id"],
            )
    op.execute(sa.text(
        "UPDATE reservation_snapshots SET source_batch_id = batch_id "
        "WHERE source_batch_id IS NULL"
    ))


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_reservation_snapshots_source_batch", "reservation_snapshots", type_="foreignkey"
        )
    op.drop_column("reservation_snapshots", "source_batch_id")
