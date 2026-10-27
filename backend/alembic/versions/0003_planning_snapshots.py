"""Versioned property and reservation imports for the planning view.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("import_batches")}
    with op.batch_alter_table("import_batches") as batch:
        if "dataset" not in columns:
            batch.add_column(
                sa.Column("dataset", sa.String(20), nullable=False, server_default="availability")
            )
        if "actor_user_id" not in columns:
            batch.add_column(sa.Column("actor_user_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_import_batches_actor", "users", ["actor_user_id"], ["id"])
        if "parser_version" not in columns:
            batch.add_column(sa.Column("parser_version", sa.String(20), nullable=True))
        if "warnings" not in columns:
            batch.add_column(sa.Column("warnings", sa.JSON(), nullable=True))
        if "changes" not in columns:
            batch.add_column(sa.Column("changes", sa.JSON(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("import_batches")}
    if "ix_import_batches_dataset" not in indexes:
        op.create_index("ix_import_batches_dataset", "import_batches", ["dataset"])
    if not inspector.has_table("property_snapshots"):
        op.create_table(
            "property_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("import_batches.id"), nullable=False),
            sa.Column("source_row", sa.Integer(), nullable=False),
            sa.Column("row_hash", sa.String(64), nullable=False),
            sa.Column("property_id", sa.String(100), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("status_original", sa.String(100), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("category", sa.String(100), nullable=False),
            sa.Column("capacity", sa.Integer(), nullable=False),
            sa.Column("building", sa.String(255)),
            sa.Column("city", sa.String(150)),
            sa.Column("address", sa.String(255)),
            sa.UniqueConstraint("batch_id", "property_id"),
        )
    property_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("property_snapshots")
    }
    if "ix_property_snapshots_batch_id" not in property_indexes:
        op.create_index("ix_property_snapshots_batch_id", "property_snapshots", ["batch_id"])
    if "ix_property_snapshots_property_id" not in property_indexes:
        op.create_index("ix_property_snapshots_property_id", "property_snapshots", ["property_id"])
    if not inspector.has_table("reservation_snapshots"):
        op.create_table(
            "reservation_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("import_batches.id"), nullable=False),
            sa.Column("source_row", sa.Integer(), nullable=False),
            sa.Column("row_hash", sa.String(64), nullable=False),
            sa.Column("locator", sa.String(150), nullable=False),
            sa.Column("property_id", sa.String(100), nullable=False),
            sa.Column("status_original", sa.String(100), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("created_on", sa.Date()),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("nights", sa.Integer(), nullable=False),
            sa.Column("adults", sa.Integer(), nullable=False),
            sa.Column("children", sa.Integer(), nullable=False),
            sa.Column("infants", sa.Integer(), nullable=False),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("portal", sa.String(100)),
            sa.Column("total", sa.Numeric(12, 2), nullable=False),
            sa.Column("net", sa.Numeric(12, 2), nullable=False),
            sa.Column("tax", sa.Numeric(12, 2), nullable=False),
            sa.Column("paid", sa.Numeric(12, 2), nullable=False),
            sa.Column("pending", sa.Numeric(12, 2), nullable=False),
            sa.UniqueConstraint("batch_id", "locator"),
        )
    reservation_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("reservation_snapshots")
    }
    if "ix_reservation_snapshots_batch_id" not in reservation_indexes:
        op.create_index("ix_reservation_snapshots_batch_id", "reservation_snapshots", ["batch_id"])
    if "ix_reservation_snapshots_locator" not in reservation_indexes:
        op.create_index("ix_reservation_snapshots_locator", "reservation_snapshots", ["locator"])
    if "ix_reservation_snapshots_property_id" not in reservation_indexes:
        op.create_index(
            "ix_reservation_snapshots_property_id", "reservation_snapshots", ["property_id"]
        )


def downgrade() -> None:
    op.drop_table("reservation_snapshots")
    op.drop_table("property_snapshots")
    op.drop_constraint("fk_import_batches_actor", "import_batches", type_="foreignkey")
    op.drop_index("ix_import_batches_dataset", table_name="import_batches")
    for column in ("changes", "warnings", "parser_version", "actor_user_id", "dataset"):
        op.drop_column("import_batches", column)
