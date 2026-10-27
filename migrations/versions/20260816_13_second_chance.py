"""Snapshot participant-specific Second Chance pools.

Revision ID: 20260816_13
Revises: 20260816_12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260816_13"
down_revision = "20260816_12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "second_chance_media",
        sa.Column("participant_id", sa.String(), nullable=False),
        sa.Column("media_id", sa.String(), nullable=False),
        sa.Column("room_code", sa.String(), nullable=False),
        sa.Column("source_top_choice", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_code"], ["rooms.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("participant_id", "media_id"),
    )
    op.create_index("ix_second_chance_media_room_code", "second_chance_media", ["room_code"])


def downgrade():
    op.drop_index("ix_second_chance_media_room_code", table_name="second_chance_media")
    op.drop_table("second_chance_media")
