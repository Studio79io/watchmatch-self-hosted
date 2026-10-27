import uuid

from sqlalchemy import inspect, select, text

from backend.database import connect, engine
from backend.models import Household, HouseholdSettings, Media, Participant, Room, RoomMedia, Swipe, SyncRun, SystemSettings
from backend.plex import exclude_libraries, extract_release_years, reconcile_unavailable

SUFFIX = uuid.uuid4().hex[:8]
MEDIA_ID = f"test-movie-{SUFFIX}"
ROOM_CODE = f"T{SUFFIX}".upper()
PLAYER_ID = f"test-player-{SUFFIX}"


def test_migrations_created_expected_schema():
    tables = set(inspect(engine).get_table_names())
    assert {"alembic_version", "media", "rooms", "participants", "swipes",
            "matches", "room_media", "sync_runs", "household_settings", "households",
            "users", "household_members", "household_invitations", "user_sessions",
            "plex_auth_pins", "second_chance_media", "room_preferences",
            "system_settings"} <= tables
    media_columns = {column["name"] for column in inspect(engine).get_columns("media")}
    assert {"version_count", "edition_title", "unwatched_count", "released_at"} <= media_columns
    with connect() as session:
        settings = session.get(HouseholdSettings, 1)
        assert settings.random_pool_size == 100
        assert settings.finite_card_limit >= 1
        assert session.scalar(select(Household).where(Household.slug == "default")) is not None
        assert session.get(SystemSettings, 1).sync_interval_minutes == 1440
    room_columns = {column["name"] for column in inspect(engine).get_columns("rooms")}
    assert {"allow_undo", "offer_another_match", "card_limit"} <= room_columns
    settings_columns = {column["name"] for column in inspect(engine).get_columns("household_settings")}
    assert {"reset_room_choices", "matched_item_cooldown_days"} <= settings_columns


def test_database_is_postgresql_18():
    with connect() as session:
        assert session.scalar(text("SHOW server_version")).startswith("18.")


def test_path_year_detection_ignores_years_that_belong_to_titles():
    assert extract_release_years(
        ["/Movies/1917 (2019)/1917 (2019).mkv"], "1917"
    ) == [2019]
    assert extract_release_years(
        ["/Movies/Blade Runner 2049 (2017)/movie.mkv"], "Blade Runner 2049"
    ) == [2017]
    assert extract_release_years(
        ["/Movies/2001 A Space Odyssey/movie.mkv"], "2001: A Space Odyssey"
    ) == []
    assert extract_release_years(
        ["/Movies/Hellboy 2019/Hellboy.mkv"], "Hellboy"
    ) == [2019]
    assert extract_release_years(
        [r"D:\Movies\Wonder Woman 1984 [2020]\movie.mkv"], "Wonder Woman 1984"
    ) == [2020]


def test_sync_run_history():
    with connect() as session:
        run = SyncRun(trigger="test", status="running")
        session.add(run)
        session.flush()
        run_id = run.id
    with connect() as session:
        assert session.get(SyncRun, run_id).trigger == "test"


def test_jsonb_catalog_and_room_relationships():
    with connect() as session:
        session.add(Media(
            id=MEDIA_ID, title="Test Movie", kind="movie", library="Movies",
            genres=["Comedy"], collections=["Holiday & Seasonal"],
            directors=["Test Director"], actors=["Test Actor"],
        ))
        session.add(Room(code=ROOM_CODE, household_id=1, kind="movie", library="Movies"))
        session.flush()
        session.add(Participant(id=PLAYER_ID, room_code=ROOM_CODE, name="Tester"))
        session.add(RoomMedia(room_code=ROOM_CODE, media_id=MEDIA_ID))
    with connect() as session:
        movie = session.scalar(select(Media).where(Media.id == MEDIA_ID, Media.genres.contains(["Comedy"])))
        assert movie.id == MEDIA_ID
        assert movie.collections == ["Holiday & Seasonal"]
        assert session.get(RoomMedia, (ROOM_CODE, MEDIA_ID)) is not None


def test_postgresql_upsert_for_swipes():
    from sqlalchemy.dialects.postgresql import insert
    with connect() as session:
        statement = insert(Swipe).values(
            participant_id=PLAYER_ID, room_code=ROOM_CODE,
            media_id=MEDIA_ID, direction="left",
        ).on_conflict_do_update(
            index_elements=[Swipe.participant_id, Swipe.media_id],
            set_={"direction": "right"},
        )
        session.execute(statement)
        session.execute(statement)
    with connect() as session:
        assert session.get(Swipe, (PLAYER_ID, MEDIA_ID)).direction == "right"


def test_missing_media_leaves_active_pool_but_keeps_history():
    with connect() as session:
        missing = reconcile_unavailable(session, {"Movies"}, set())
        assert MEDIA_ID in missing
    with connect() as session:
        movie = session.get(Media, MEDIA_ID)
        assert movie is not None
        assert movie.available is False
        assert movie.unavailable_at is not None
        assert session.get(RoomMedia, (ROOM_CODE, MEDIA_ID)) is None


def test_excluded_library_is_soft_hidden_and_removed_from_rooms():
    suffix = uuid.uuid4().hex[:8]
    media_id = f"private-{suffix}"
    room_code = f"P{suffix}".upper()
    with connect() as session:
        session.add(Media(id=media_id, title="Private Video", kind="movie",
                          library="Home Movies", available=True))
        session.add(Room(code=room_code, household_id=1, kind="movie", library="Home Movies"))
        session.flush()
        session.add(RoomMedia(room_code=room_code, media_id=media_id))
    with connect() as session:
        hidden = exclude_libraries(session, {"Home Movies"})
        assert media_id in hidden
    with connect() as session:
        assert session.get(Media, media_id).available is False
        assert session.get(RoomMedia, (room_code, media_id)) is None
