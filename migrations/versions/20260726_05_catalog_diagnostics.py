"""Add privacy-safe catalog integrity metadata.

Revision ID: 20260726_05
Revises: 20260726_04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260726_05"
down_revision = "20260726_04"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column(
        "file_count", sa.Integer(), server_default="0", nullable=False,
    ))
    op.add_column("media", sa.Column(
        "path_years", postgresql.JSONB(), server_default="[]", nullable=False,
    ))


def downgrade():
    op.drop_column("media", "path_years")
    op.drop_column("media", "file_count")
