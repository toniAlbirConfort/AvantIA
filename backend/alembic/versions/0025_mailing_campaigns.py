"""Win-back email campaigns: campaigns, their frozen audience and the suppression list.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds fresh databases from the current models, which already include these tables.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("mailing_campaigns"):
        op.create_table(
            "mailing_campaigns",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=150), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("filters", sa.JSON(), nullable=False),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("offer", sa.JSON(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("audience_built_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not inspector.has_table("mailing_recipients"):
        op.create_table(
            "mailing_recipients",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("guest_key", sa.String(length=255), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("first_name", sa.String(length=120), nullable=True),
            sa.Column("avantio_id", sa.String(length=20), nullable=True),
            sa.Column("email", sa.String(length=200), nullable=True),
            sa.Column("language", sa.String(length=10), nullable=False),
            sa.Column("language_raw", sa.String(length=40), nullable=True),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("exclusion_reason", sa.String(length=30), nullable=True),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(
                ["campaign_id"], ["mailing_campaigns.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("campaign_id", "guest_key"),
            sa.UniqueConstraint("token"),
        )
        op.create_index(
            "ix_mailing_recipients_campaign_id", "mailing_recipients", ["campaign_id"]
        )
    if not inspector.has_table("mailing_suppressions"):
        op.create_table(
            "mailing_suppressions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(length=200), nullable=False),
            sa.Column("reason", sa.String(length=30), nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["campaign_id"], ["mailing_campaigns.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
        )


def downgrade() -> None:
    op.drop_table("mailing_suppressions")
    op.drop_index("ix_mailing_recipients_campaign_id", table_name="mailing_recipients")
    op.drop_table("mailing_recipients")
    op.drop_table("mailing_campaigns")
