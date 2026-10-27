"""Track background Plex synchronization runs.

Revision ID: 20260726_03
Revises: 20260725_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260726_03"
down_revision = "20260725_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("added_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])


def downgrade():
    op.drop_table("sync_runs")
