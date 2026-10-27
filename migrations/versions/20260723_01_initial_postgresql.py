"""Initial PostgreSQL schema.

Revision ID: 20260723_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260723_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("media",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("genres", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("duration", sa.Integer()),
        sa.Column("rating", sa.String()),
        sa.Column("thumb", sa.String()),
        sa.Column("library", sa.String()),
        sa.Column("watched", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True)),
        sa.Column("directors", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("actors", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("studio", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table("rooms",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), server_default="open", nullable=False),
        sa.Column("kind", sa.String(), server_default="movie", nullable=False),
        sa.Column("library", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("matched_media_id", sa.String()),
        sa.Column("mode", sa.String(), server_default="random", nullable=False),
        sa.Column("phase", sa.String(), server_default="movies", nullable=False),
        sa.Column("filter_type", sa.String()),
        sa.Column("filter_value", sa.String()),
    )
    op.create_table("participants",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("room_code", sa.String(), sa.ForeignKey("rooms.code", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_participants_room_code", "participants", ["room_code"])
    op.create_table("matches",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("room_code", sa.String(), nullable=False),
        sa.Column("media_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_matches_room_code", "matches", ["room_code"])
    op.create_index("ix_matches_media_id", "matches", ["media_id"])
    op.create_table("room_media",
        sa.Column("room_code", sa.String(), sa.ForeignKey("rooms.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("media_id", sa.String(), sa.ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table("swipes",
        sa.Column("participant_id", sa.String(), sa.ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("media_id", sa.String(), primary_key=True),
        sa.Column("room_code", sa.String(), sa.ForeignKey("rooms.code", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_swipes_room_code", "swipes", ["room_code"])


def downgrade():
    op.drop_table("swipes")
    op.drop_table("room_media")
    op.drop_table("matches")
    op.drop_table("participants")
    op.drop_table("rooms")
    op.drop_table("media")
