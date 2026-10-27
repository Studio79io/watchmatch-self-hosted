from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Identity, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HouseholdSettings(Base):
    __tablename__ = "household_settings"

    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    movies_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    shows_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    random_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    filter_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    genre_match_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    random_pool_size: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    allowed_content_ratings: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    undo_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    another_match_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    finite_sessions_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    finite_card_limit: Mapped[int] = mapped_column(Integer, default=25, server_default="25")
    reset_room_choices: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    matched_item_cooldown_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    plex_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String, default="plex", server_default="plex")
    provider_external_id: Mapped[str | None] = mapped_column(String)
    username: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoomPreference(Base):
    __tablename__ = "room_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String, default="movie", server_default="movie")
    mode: Mapped[str] = mapped_column(String, default="random", server_default="random")
    filter_type: Mapped[str | None]
    filter_value: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HouseholdMember(Base):
    __tablename__ = "household_members"
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, unique=True
    )
    role: Mapped[str] = mapped_column(String, default="admin", server_default="admin")


class HouseholdInvitation(Base):
    __tablename__ = "household_invitations"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlexAuthPin(Base):
    __tablename__ = "plex_auth_pins"
    state: Mapped[str] = mapped_column(String, primary_key=True)
    pin_id: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Media(Base):
    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    catalog_provider: Mapped[str] = mapped_column(String, default="plex", server_default="plex")
    provider_external_id: Mapped[str | None] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int | None]
    kind: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    genres: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    collections: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    file_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    version_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    path_years: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    edition_title: Mapped[str | None]
    unwatched_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration: Mapped[int | None]
    rating: Mapped[str | None]
    thumb: Mapped[str | None]
    library: Mapped[str | None]
    watched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    unavailable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[date | None] = mapped_column(Date)
    directors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    actors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    studio: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Room(Base):
    __tablename__ = "rooms"

    code: Mapped[str] = mapped_column(String, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    creator_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    join_token_hash: Mapped[str | None] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="open", server_default="open")
    kind: Mapped[str] = mapped_column(String, default="movie", server_default="movie")
    library: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    matched_media_id: Mapped[str | None] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String, default="random", server_default="random")
    phase: Mapped[str] = mapped_column(String, default="movies", server_default="movies")
    filter_type: Mapped[str | None]
    filter_value: Mapped[str | None]
    allow_undo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    offer_another_match: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    card_limit: Mapped[int | None]


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    room_code: Mapped[str] = mapped_column(ForeignKey("rooms.code", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Swipe(Base):
    __tablename__ = "swipes"

    participant_id: Mapped[str] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    media_id: Mapped[str] = mapped_column(String, primary_key=True)
    room_code: Mapped[str] = mapped_column(ForeignKey("rooms.code", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    room_code: Mapped[str] = mapped_column(String, nullable=False, index=True)
    media_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoomMedia(Base):
    __tablename__ = "room_media"

    room_code: Mapped[str] = mapped_column(
        ForeignKey("rooms.code", ondelete="CASCADE"), primary_key=True
    )
    media_id: Mapped[str] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )


class SecondChanceMedia(Base):
    __tablename__ = "second_chance_media"

    participant_id: Mapped[str] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    media_id: Mapped[str] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )
    room_code: Mapped[str] = mapped_column(
        ForeignKey("rooms.code", ondelete="CASCADE"), index=True
    )
    source_top_choice: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    direction: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    item_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    added_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    removed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, server_default="1")
    sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=1440, server_default="1440"
    )
    # Empty preserves V1: all synchronized, non-excluded libraries are selectable.
    selectable_libraries: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
