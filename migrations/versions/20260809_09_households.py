"""Introduce household tenant boundaries.

Revision ID: 20260809_09
Revises: 20260809_08
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_09"
down_revision = "20260809_08"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "households",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("INSERT INTO households (slug, name) VALUES ('default', 'Default household')")

    op.drop_constraint("ck_household_settings_singleton", "household_settings", type_="check")
    op.alter_column("household_settings", "id", new_column_name="household_id")
    op.create_foreign_key(
        "fk_household_settings_household", "household_settings", "households",
        ["household_id"], ["id"], ondelete="CASCADE",
    )

    op.add_column("rooms", sa.Column("household_id", sa.Integer(), nullable=True))
    op.execute("UPDATE rooms SET household_id = (SELECT id FROM households WHERE slug = 'default')")
    op.alter_column("rooms", "household_id", nullable=False)
    op.create_foreign_key("fk_rooms_household", "rooms", "households", ["household_id"], ["id"])
    op.create_index("ix_rooms_household_id", "rooms", ["household_id"])

    op.add_column("matches", sa.Column("household_id", sa.Integer(), nullable=True))
    op.execute("UPDATE matches SET household_id = (SELECT id FROM households WHERE slug = 'default')")
    op.alter_column("matches", "household_id", nullable=False)
    op.create_foreign_key("fk_matches_household", "matches", "households", ["household_id"], ["id"])
    op.create_index("ix_matches_household_id", "matches", ["household_id"])


def downgrade():
    op.drop_index("ix_matches_household_id", table_name="matches")
    op.drop_constraint("fk_matches_household", "matches", type_="foreignkey")
    op.drop_column("matches", "household_id")
    op.drop_index("ix_rooms_household_id", table_name="rooms")
    op.drop_constraint("fk_rooms_household", "rooms", type_="foreignkey")
    op.drop_column("rooms", "household_id")
    op.drop_constraint("fk_household_settings_household", "household_settings", type_="foreignkey")
    op.alter_column("household_settings", "household_id", new_column_name="id")
    op.create_check_constraint("ck_household_settings_singleton", "household_settings", "id = 1")
    op.drop_table("households")
