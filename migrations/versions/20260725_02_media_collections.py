"""Store Plex collection memberships.

Revision ID: 20260725_02
Revises: 20260723_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260725_02"
down_revision = "20260723_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column(
        "collections", postgresql.JSONB(), server_default="[]", nullable=False,
    ))


def downgrade():
    op.drop_column("media", "collections")
