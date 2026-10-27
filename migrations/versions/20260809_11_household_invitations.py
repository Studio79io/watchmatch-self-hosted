"""Add household invitations and enforce one household per user.

Revision ID: 20260809_11
Revises: 20260809_10
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_11"
down_revision = "20260809_10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_household_members_user", "household_members", ["user_id"])
    op.create_table("household_invitations",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_household_invitations_household_id", "household_invitations", ["household_id"])


def downgrade():
    op.drop_index("ix_household_invitations_household_id", table_name="household_invitations")
    op.drop_table("household_invitations")
    op.drop_constraint("uq_household_members_user", "household_members", type_="unique")
