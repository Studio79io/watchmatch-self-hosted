"""Add Plex users, memberships, sessions, PINs, and room invitations.

Revision ID: 20260809_10
Revises: 20260809_09
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_10"
down_revision = "20260809_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("plex_id", sa.String(), nullable=False, unique=True),
        sa.Column("username", sa.String(), nullable=False), sa.Column("email", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("household_members",
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(), nullable=False, server_default="admin"))
    op.create_table("user_sessions",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_household_id", "user_sessions", ["household_id"])
    op.create_table("plex_auth_pins",
        sa.Column("state", sa.String(), primary_key=True), sa.Column("pin_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column("rooms", sa.Column("creator_user_id", sa.Integer(), nullable=True))
    op.add_column("rooms", sa.Column("join_token_hash", sa.String(), nullable=True))
    op.create_foreign_key("fk_rooms_creator", "rooms", "users", ["creator_user_id"], ["id"])
    op.create_index("ix_rooms_join_token_hash", "rooms", ["join_token_hash"])


def downgrade():
    op.drop_index("ix_rooms_join_token_hash", table_name="rooms")
    op.drop_constraint("fk_rooms_creator", "rooms", type_="foreignkey")
    op.drop_column("rooms", "join_token_hash"); op.drop_column("rooms", "creator_user_id")
    op.drop_table("plex_auth_pins")
    op.drop_index("ix_user_sessions_household_id", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions"); op.drop_table("household_members"); op.drop_table("users")
