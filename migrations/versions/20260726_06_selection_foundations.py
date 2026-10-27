"""Add selection and Plex-version metadata.

Revision ID: 20260726_06
Revises: 20260726_05
"""
from alembic import op
import sqlalchemy as sa

revision = "20260726_06"
down_revision = "20260726_05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column("version_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("media", sa.Column("edition_title", sa.String(), nullable=True))
    op.add_column("media", sa.Column("unwatched_count", sa.Integer(), server_default="0", nullable=False))


def downgrade():
    op.drop_column("media", "unwatched_count")
    op.drop_column("media", "edition_title")
    op.drop_column("media", "version_count")
