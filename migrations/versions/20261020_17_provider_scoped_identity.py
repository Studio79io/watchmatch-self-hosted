"""Add provider-scoped user and catalog identifiers.

Revision ID: 20261020_17
Revises: 20260921_16
"""
from alembic import op
import sqlalchemy as sa

revision = "20261020_17"
down_revision = "20260921_16"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("provider", sa.String(), nullable=False, server_default="plex"))
    op.add_column("users", sa.Column("provider_external_id", sa.String(), nullable=True))
    op.execute("UPDATE users SET provider = 'plex', provider_external_id = plex_id")
    op.add_column("media", sa.Column("catalog_provider", sa.String(), nullable=False, server_default="plex"))
    op.add_column("media", sa.Column("provider_external_id", sa.String(), nullable=True))
    op.execute("UPDATE media SET catalog_provider = 'plex', provider_external_id = id")


def downgrade():
    op.drop_column("media", "provider_external_id")
    op.drop_column("media", "catalog_provider")
    op.drop_column("users", "provider_external_id")
    op.drop_column("users", "provider")
