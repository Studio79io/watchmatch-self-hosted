"""Track Plex availability and reconciliation counts.

Revision ID: 20260726_04
Revises: 20260726_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260726_04"
down_revision = "20260726_03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column(
        "available", sa.Boolean(), server_default=sa.true(), nullable=False,
    ))
    op.add_column("media", sa.Column(
        "unavailable_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("sync_runs", sa.Column(
        "removed_count", sa.Integer(), server_default="0", nullable=False,
    ))


def downgrade():
    op.drop_column("sync_runs", "removed_count")
    op.drop_column("media", "unavailable_at")
    op.drop_column("media", "available")
