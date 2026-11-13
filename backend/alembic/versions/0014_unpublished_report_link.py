"""Drop the link to the unpublished claude.ai copy of the Albir competitor report.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UNPUBLISHED_URL = "https://claude.ai/artifact/JSBP7TDSBHiqQpQR7rhyAU"


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE reports SET original_url = NULL WHERE original_url = :url").bindparams(
            url=UNPUBLISHED_URL
        )
    )


def downgrade() -> None:
    # The claude.ai copy no longer exists, so there is no link to restore.
    pass
