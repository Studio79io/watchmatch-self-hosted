"""Add configurable room controls.

Revision ID: 20260809_08
Revises: 20260731_07
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_08"
down_revision = "20260731_07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("household_settings", sa.Column("undo_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("household_settings", sa.Column("another_match_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("household_settings", sa.Column("finite_sessions_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("household_settings", sa.Column("finite_card_limit", sa.Integer(), nullable=False, server_default="25"))
    op.create_check_constraint("ck_household_settings_card_limit", "household_settings", "finite_card_limit >= 1")
    op.add_column("rooms", sa.Column("allow_undo", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("rooms", sa.Column("offer_another_match", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("rooms", sa.Column("card_limit", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_rooms_card_limit", "rooms", "card_limit IS NULL OR card_limit >= 1")


def downgrade():
    op.drop_constraint("ck_rooms_card_limit", "rooms", type_="check")
    op.drop_column("rooms", "card_limit")
    op.drop_column("rooms", "offer_another_match")
    op.drop_column("rooms", "allow_undo")
    op.drop_constraint("ck_household_settings_card_limit", "household_settings", type_="check")
    op.drop_column("household_settings", "finite_card_limit")
    op.drop_column("household_settings", "finite_sessions_enabled")
    op.drop_column("household_settings", "another_match_enabled")
    op.drop_column("household_settings", "undo_enabled")
