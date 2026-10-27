"""add selectable library allowlist

Revision ID: 20260921_16
Revises: 20260816_15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260921_16"
down_revision = "20260816_15"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("system_settings", sa.Column(
        "selectable_libraries", postgresql.JSONB(), server_default="[]", nullable=False
    ))


def downgrade():
    op.drop_column("system_settings", "selectable_libraries")
