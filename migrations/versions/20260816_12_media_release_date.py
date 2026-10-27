"""Store the original Plex release date for exact recency filtering.

Revision ID: 20260816_12
Revises: 20260809_11
"""

from alembic import op
import sqlalchemy as sa

revision = "20260816_12"
down_revision = "20260809_11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column("released_at", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("media", "released_at")
