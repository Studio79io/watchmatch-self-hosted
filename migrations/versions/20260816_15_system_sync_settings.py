"""persist system synchronization settings

Revision ID: 20260816_15
Revises: 20260816_14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260816_15"
down_revision = "20260816_14"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), server_default="1440", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_system_settings_singleton"),
        sa.CheckConstraint(
            "sync_interval_minutes BETWEEN 15 AND 10080",
            name="ck_system_settings_sync_interval",
        ),
    )
    op.execute("INSERT INTO system_settings (id, sync_interval_minutes) VALUES (1, 1440)")


def downgrade():
    op.drop_table("system_settings")
