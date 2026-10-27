"""Add persistent household settings.

Revision ID: 20260731_07
Revises: 20260726_06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260731_07"
down_revision = "20260726_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "household_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movies_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("shows_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("random_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("filter_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("genre_match_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("random_pool_size", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("allowed_content_ratings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_household_settings_singleton"),
        sa.CheckConstraint("random_pool_size >= 0", name="ck_household_settings_pool_size"),
    )
    op.execute("INSERT INTO household_settings (id) VALUES (1)")


def downgrade():
    op.drop_table("household_settings")
