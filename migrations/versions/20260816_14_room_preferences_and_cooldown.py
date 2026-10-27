"""Persist creator room choices and household cooldown policy.

Revision ID: 20260816_14
Revises: 20260816_13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260816_14"
down_revision = "20260816_13"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("household_settings", sa.Column(
        "reset_room_choices", sa.Boolean(), server_default="false", nullable=False
    ))
    op.add_column("household_settings", sa.Column(
        "matched_item_cooldown_days", sa.Integer(), server_default="7", nullable=False
    ))
    op.create_table(
        "room_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), server_default="movie", nullable=False),
        sa.Column("mode", sa.String(), server_default="random", nullable=False),
        sa.Column("filter_type", sa.String(), nullable=True),
        sa.Column("filter_value", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_room_preferences_household_id", "room_preferences", ["household_id"])


def downgrade():
    op.drop_index("ix_room_preferences_household_id", table_name="room_preferences")
    op.drop_table("room_preferences")
    op.drop_column("household_settings", "matched_item_cooldown_days")
    op.drop_column("household_settings", "reset_room_choices")
