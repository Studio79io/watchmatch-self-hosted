import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.database import connect
from backend.main import app, config, mark_room_exhausted_if_complete, populate_room_pool, token_hash
from backend.models import (Household, HouseholdMember, HouseholdSettings, Match,
                            Participant, Room, RoomMedia, RoomPreference,
                            SecondChanceMedia, Swipe,
                            User, UserSession)
from sqlalchemy import select
from backend.models import Media


def add_test_catalog():
    suffix = uuid.uuid4().hex[:8]
    media_id = f"api-movie-{suffix}"
    genre = f"API Test {suffix}"
    with connect() as session:
        session.add(Media(
            id=media_id, title=f"API Movie {suffix}", year=2026,
            kind="movie", library="Movies", genres=[genre],
            collections=[], directors=[], actors=[], available=True,
        ))
    return media_id, genre


def test_health_readiness_and_diagnostics():
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}
        ready = client.get("/api/ready")
        assert ready.status_code == 200
        assert ready.json()["ok"] is True


def test_complete_two_person_room_api_flow():
    media_id, genre = add_test_catalog()
    with TestClient(app) as client:
        created = client.post("/api/rooms", json={
            "kind": "movie", "mode": "filter",
            "filter_type": "genre", "filter_value": genre,
        })
        assert created.status_code == 200
        code = created.json()["code"]
        player_one = client.post(f"/api/rooms/{code}/join", json={"name": "Alex"})
        player_two = client.post(f"/api/rooms/{code}/join", json={"name": "Sam"})
        assert player_one.status_code == player_two.status_code == 200
        p1 = player_one.json()["participant_id"]
        p2 = player_two.json()["participant_id"]
        cards = client.get(f"/api/rooms/{code}/cards", params={"participant_id": p1})
        assert cards.status_code == 200
        assert [card["id"] for card in cards.json()] == [media_id]
        assert client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": p1, "media_id": media_id, "direction": "right",
        }).status_code == 200
        matched = client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": p2, "media_id": media_id, "direction": "right",
        })
        assert matched.status_code == 200
        assert matched.json()["status"] == "matched"
        assert matched.json()["match"]["id"] == media_id


def test_filter_options_contract():
    _, genre = add_test_catalog()
    with TestClient(app) as client:
        options = client.get("/api/filter-options", params={
            "kind": "movie", "filter_type": "genre", "q": "API",
        })
        assert options.status_code == 200
        assert genre in options.json()


def test_selectable_libraries_are_owner_managed_and_enforced(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    owner_id, raw = f"library-owner-{suffix}", f"library-session-{suffix}"
    movie_library, show_library = f"Movies {suffix}", f"Shows {suffix}"
    with connect() as session:
        household = session.scalar(select(Household).where(Household.slug == "default"))
        owner = User(plex_id=owner_id, username="Library Owner")
        session.add(owner); session.flush()
        session.add_all([
            Media(id=f"library-movie-{suffix}", title="Movie", kind="movie", library=movie_library,
                  genres=[], collections=[], directors=[], actors=[], available=True),
            Media(id=f"library-show-{suffix}", title="Show", kind="show", library=show_library,
                  genres=[], collections=[], directors=[], actors=[], available=True),
            UserSession(token_hash=token_hash(raw), user_id=owner.id, household_id=household.id,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        ])
    monkeypatch.setenv("SYSTEM_OWNER_PLEX_ID", owner_id)
    with TestClient(app) as client:
        assert client.get("/api/system/libraries").status_code == 401
        client.cookies.set("watchmatch_session", raw)
        assert client.put("/api/system/libraries", json={"libraries": ["missing"]}).status_code == 400
        saved = client.put("/api/system/libraries", json={"libraries": [movie_library]})
        assert saved.json() == {"selectable_libraries": [movie_library]}
        managed = client.get("/api/system/libraries").json()
        assert managed["selection_required"] is True
        movie_summary = next(item for item in managed["catalog"] if item["library"] == movie_library)
        assert movie_summary == {"library": movie_library, "count": 1,
                                 "kinds": {"movie": 1}, "selectable": True}
        assert client.get("/api/room-libraries", params={"kind": "movie"}).json()[0]["library"] == movie_library
        assert client.get("/api/room-libraries", params={"kind": "show"}).json() == []
        assert client.post("/api/rooms", json={"kind": "show", "library": show_library}).status_code == 400
        assert client.post("/api/rooms", json={"kind": "movie", "library": movie_library}).status_code == 200
        client.put("/api/system/libraries", json={"libraries": []})
        # Empty allowlist restores V1's all-synchronized-library behavior.
        assert any(row["library"] == show_library for row in client.get("/api/room-libraries").json())


def test_system_page_apis_require_the_configured_plex_owner(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    owner_plex_id = f"system-owner-{suffix}"
    owner_raw, outsider_raw = f"owner-session-{suffix}", f"outsider-session-{suffix}"
    recovery_code = "recovery-" + uuid.uuid4().hex + uuid.uuid4().hex
    monkeypatch.setenv("SYSTEM_OWNER_PLEX_ID", owner_plex_id)
    monkeypatch.setenv("SYSTEM_RECOVERY_CODE", recovery_code)
    with connect() as session:
        household = session.scalar(select(Household).where(Household.slug == "default"))
        owner = User(plex_id=owner_plex_id, username="System Owner")
        outsider = User(plex_id=f"not-owner-{suffix}", username="Household Admin")
        displaced = User(plex_id=f"displaced-{suffix}", username="Old Account")
        recovery_household = Household(slug=f"recover-{suffix}", name="Recover Me")
        session.add_all([owner, outsider, displaced, recovery_household]); session.flush()
        recovery_household_id, outsider_id, displaced_id = recovery_household.id, outsider.id, displaced.id
        session.add_all([
            HouseholdSettings(household_id=recovery_household.id),
            HouseholdMember(household_id=recovery_household.id, user_id=displaced.id, role="admin"),
            UserSession(token_hash=token_hash(owner_raw), user_id=owner.id,
                        household_id=household.id,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
            UserSession(token_hash=token_hash(outsider_raw), user_id=outsider.id,
                        household_id=household.id,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        ])
    protected_gets = ["/api/system/access", "/api/sync/status", "/api/libraries",
                      "/api/diagnostics/catalog"]
    with TestClient(app) as client:
        for path in protected_gets:
            assert client.get(path).status_code == 401
        client.cookies.set("watchmatch_session", outsider_raw)
        for path in protected_gets:
            assert client.get(path).status_code == 403
        assert client.post("/api/sync").status_code == 403
        assert client.put("/api/sync/settings", json={"interval_minutes": 720}).status_code == 403
        assert client.post("/api/diagnostics/cache/clear").status_code == 403
        client.cookies.set("watchmatch_session", owner_raw)
        assert client.get("/api/system/access").json()["authorized"] is True
        status = client.get("/api/sync/status")
        assert status.status_code == 200
        assert {"running", "interval_minutes", "latest", "history"} <= status.json().keys()
        changed_schedule = client.put("/api/sync/settings", json={"interval_minutes": 720})
        assert changed_schedule.json() == {"interval_minutes": 720}
        assert client.get("/api/sync/status").json()["interval_minutes"] == 720
        assert client.put("/api/sync/settings", json={"interval_minutes": 1}).status_code == 400
        client.put("/api/sync/settings", json={"interval_minutes": 1440})
        assert client.get("/api/libraries").status_code == 200
        assert "issues" in client.get("/api/diagnostics/catalog").json()
        assert client.post("/api/diagnostics/cache/clear").json()["ok"] is True
        accounts = client.get("/api/system/accounts")
        assert accounts.status_code == 200
        assert any(item["id"] == recovery_household_id for item in accounts.json()["households"])
        recovered = client.post(f"/api/system/households/{recovery_household_id}/recover",
                                json={"user_id": outsider_id})
        assert recovered.status_code == 200
        assert recovered.json()["sessions_revoked"] is True
        client.cookies.clear()
        assert client.post("/api/system/recovery/session", json={"code": "wrong"}).status_code == 401
        emergency = client.post("/api/system/recovery/session", json={"code": recovery_code})
        assert emergency.status_code == 200
        assert emergency.json()["expires_in"] == 900
        assert client.get("/api/system/access").status_code == 200
    with connect() as session:
        membership = session.scalar(select(HouseholdMember).where(
            HouseholdMember.household_id == recovery_household_id
        ))
        assert membership.user_id == outsider_id
        assert session.scalar(select(HouseholdMember).where(
            HouseholdMember.user_id == displaced_id
        )) is None
        assert session.scalar(select(UserSession).where(UserSession.user_id == outsider_id)) is None


def test_household_match_history_can_restore_a_title_and_clear_history():
    suffix = uuid.uuid4().hex[:8]
    raw_session = f"history-session-{suffix}"
    media_id, _ = add_test_catalog()
    with connect() as session:
        household = Household(slug=f"history-{suffix}", name="History House")
        user = User(plex_id=f"history-user-{suffix}", username="History Owner")
        session.add_all([household, user]); session.flush()
        session.add_all([
            HouseholdSettings(household_id=household.id),
            HouseholdMember(household_id=household.id, user_id=user.id, role="admin"),
            UserSession(token_hash=token_hash(raw_session), user_id=user.id,
                        household_id=household.id,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
            Match(household_id=household.id, room_code=f"A{suffix}".upper(), media_id=media_id),
            Match(household_id=household.id, room_code=f"B{suffix}".upper(), media_id=media_id),
        ])
        household_id = household.id
    with TestClient(app) as client:
        client.cookies.set("watchmatch_session", raw_session)
        history = client.get("/api/match-history")
        assert history.status_code == 200
        assert len(history.json()) == 2
        assert history.json()[0]["title"].startswith("API Movie")
        restored = client.post(f"/api/match-history/{history.json()[0]['id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["removed"] == 2
        assert client.get("/api/match-history").json() == []
        with connect() as session:
            session.add(Match(household_id=household_id,
                              room_code=f"C{suffix}".upper(), media_id=media_id))
        cleared = client.delete("/api/match-history")
        assert cleared.json() == {"ok": True, "removed": 1}
        assert client.get("/api/match-history").json() == []


def test_household_settings_persist_validate_and_enforce_room_choices():
    with TestClient(app) as client:
        original = client.get("/api/settings").json()
        changed = {**original, "shows_enabled": False, "filter_enabled": False,
                   "random_pool_size": 37, "allowed_content_ratings": ["PG", "PG-13"]}
        try:
            saved = client.put("/api/settings", json=changed)
            assert saved.status_code == 200
            assert client.get("/api/settings").json() == changed
            assert client.post("/api/rooms", json={
                "kind": "show", "mode": "random",
            }).status_code == 403
            assert client.post("/api/rooms", json={
                "kind": "movie", "mode": "filter",
                "filter_type": "genre", "filter_value": "Comedy",
            }).status_code == 403
            invalid = {**changed, "movies_enabled": False}
            assert client.put("/api/settings", json=invalid).status_code == 400
        finally:
            client.put("/api/settings", json=original)


def test_finite_room_undo_and_another_match_flow():
    suffix = uuid.uuid4().hex[:8]
    library = f"Controls {suffix}"
    media_ids = [f"controls-{suffix}-{index}" for index in range(3)]
    with connect() as session:
        session.add_all([
            Media(id=media_id, title=f"Controls Movie {index}", year=2020 + index,
                  kind="movie", library=library, genres=[], available=True)
            for index, media_id in enumerate(media_ids)
        ])
    with TestClient(app) as client:
        original = client.get("/api/settings").json()
        enabled = {**original, "undo_enabled": True, "another_match_enabled": True,
                   "finite_sessions_enabled": True, "finite_card_limit": 2,
                   "random_pool_size": 100}
        try:
            assert client.put("/api/settings", json=enabled).status_code == 200
            created = client.post("/api/rooms", json={
                "kind": "movie", "library": library, "mode": "random",
            })
            assert created.status_code == 200
            code = created.json()["code"]
            assert created.json()["allow_undo"] is True
            assert created.json()["offer_another_match"] is True
            assert created.json()["card_limit"] == 2
            p1 = client.post(f"/api/rooms/{code}/join", json={"name": "One"}).json()["participant_id"]
            p2 = client.post(f"/api/rooms/{code}/join", json={"name": "Two"}).json()["participant_id"]
            cards_one = client.get(f"/api/rooms/{code}/cards", params={"participant_id": p1}).json()
            cards_two = client.get(f"/api/rooms/{code}/cards", params={"participant_id": p2}).json()
            assert len(cards_one) == len(cards_two) == 2

            passed_id = cards_one[0]["id"]
            client.post(f"/api/rooms/{code}/swipe", json={
                "participant_id": p1, "media_id": passed_id, "direction": "left",
            })
            assert len(client.get(f"/api/rooms/{code}/cards", params={"participant_id": p1}).json()) == 1
            undone = client.post(f"/api/rooms/{code}/undo", json={"participant_id": p1})
            assert undone.status_code == 200
            assert undone.json()["undone_media_id"] == passed_id
            assert len(client.get(f"/api/rooms/{code}/cards", params={"participant_id": p1}).json()) == 2

            match_id = next(card["id"] for card in cards_one if card["id"] in {x["id"] for x in cards_two})
            client.post(f"/api/rooms/{code}/swipe", json={
                "participant_id": p1, "media_id": match_id, "direction": "right",
            })
            matched = client.post(f"/api/rooms/{code}/swipe", json={
                "participant_id": p2, "media_id": match_id, "direction": "right",
            })
            assert matched.json()["status"] == "matched"
            continued = client.post(f"/api/rooms/{code}/another-match")
            assert continued.status_code == 200
            assert continued.json()["status"] == "open"
            assert continued.json()["match"] is None
            with connect() as session:
                assert session.get(RoomMedia, (code, match_id)) is None
                assert session.scalar(select(Match).where(
                    Match.room_code == code, Match.media_id == match_id
                )) is not None
        finally:
            client.put("/api/settings", json=original)


def test_match_cooldown_is_isolated_by_household():
    suffix = uuid.uuid4().hex[:8]
    library = f"Tenant {suffix}"
    media_id = f"tenant-media-{suffix}"
    default_room = f"D{suffix}".upper()
    second_room = f"H{suffix}".upper()
    with connect() as session:
        default_household = session.scalar(select(Household).where(Household.slug == "default"))
        second = Household(slug=f"house-{suffix}", name="Second household")
        session.add(second)
        session.flush()
        session.add(HouseholdSettings(household_id=second.id))
        session.add(Media(id=media_id, title=f"Tenant Movie {suffix}", year=2026,
                          kind="movie", library=library, genres=[], available=True))
        session.add(Room(code=default_room, household_id=default_household.id,
                         kind="movie", library=library))
        session.add(Room(code=second_room, household_id=second.id,
                         kind="movie", library=library))
        session.add(Match(household_id=default_household.id,
                          room_code=default_room, media_id=media_id))
        session.flush()
        populate_room_pool(session, default_room, "movie", library)
        populate_room_pool(session, second_room, "movie", library)
    with connect() as session:
        assert session.get(RoomMedia, (default_room, media_id)) is None
        assert session.get(RoomMedia, (second_room, media_id)) is not None


def test_room_creation_requires_an_authenticated_household_session_when_enabled():
    _, genre = add_test_catalog()
    raw_session = f"test-session-{uuid.uuid4().hex}"
    with connect() as session:
        household = session.scalar(select(Household).where(Household.slug == "default"))
        user = User(plex_id=f"plex-{uuid.uuid4().hex}", username="Authorized User")
        session.add(user); session.flush()
        session.add(HouseholdMember(household_id=household.id, user_id=user.id, role="admin"))
        session.add(UserSession(token_hash=token_hash(raw_session), user_id=user.id,
                                household_id=household.id,
                                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    previous = config.get("auth", {}).get("required", False)
    config.setdefault("auth", {})["required"] = True
    try:
        with TestClient(app) as client:
            payload = {"kind": "movie", "mode": "filter",
                       "filter_type": "genre", "filter_value": genre}
            assert client.post("/api/rooms", json=payload).status_code == 401
            client.cookies.set("watchmatch_session", raw_session)
            created = client.post("/api/rooms", json=payload)
            assert created.status_code == 200
            assert "invite=" in created.json()["join_url"]
            assert client.post("/api/auth/logout").json() == {"ok": True}
            assert client.post("/api/rooms", json=payload).status_code == 401
    finally:
        config["auth"]["required"] = previous


def test_one_time_invitation_moves_user_from_empty_personal_household():
    suffix = uuid.uuid4().hex[:8]
    inviter_raw, invitee_raw = f"inviter-{suffix}", f"invitee-{suffix}"
    with connect() as session:
        target = session.scalar(select(Household).where(Household.slug == "default"))
        inviter = User(plex_id=f"inviter-{suffix}", username="Inviter")
        invitee = User(plex_id=f"invitee-{suffix}", username="Invitee")
        personal = Household(slug=f"plex-invitee-{suffix}", name="Invitee's household")
        session.add_all([inviter, invitee, personal]); session.flush()
        session.add_all([
            HouseholdMember(household_id=target.id, user_id=inviter.id, role="admin"),
            HouseholdMember(household_id=personal.id, user_id=invitee.id, role="admin"),
            HouseholdSettings(household_id=personal.id),
            UserSession(token_hash=token_hash(inviter_raw), user_id=inviter.id,
                        household_id=target.id, expires_at=datetime.now(timezone.utc)+timedelta(hours=1)),
            UserSession(token_hash=token_hash(invitee_raw), user_id=invitee.id,
                        household_id=personal.id, expires_at=datetime.now(timezone.utc)+timedelta(hours=1)),
        ])
        personal_id, invitee_id, target_id = personal.id, invitee.id, target.id
    with TestClient(app) as client:
        client.cookies.set("watchmatch_session", inviter_raw)
        created = client.post("/api/household/invitations")
        assert created.status_code == 200
        invite_url = created.json()["invite_url"]
        raw_token = invite_url.split("invite=", 1)[1]
        assert client.get(f"/api/household/invitations/{raw_token}").status_code == 200
        client.cookies.set("watchmatch_session", invitee_raw)
        accepted = client.post(f"/api/household/invitations/{raw_token}/accept")
        assert accepted.status_code == 200
        assert client.get(f"/api/household/invitations/{raw_token}").status_code == 410
    with connect() as session:
        assert session.get(HouseholdMember, (target_id, invitee_id)) is not None
        assert session.get(Household, personal_id) is None
        assert session.get(UserSession, token_hash(invitee_raw)).household_id == target_id


def test_creator_room_choices_persist_or_reset_from_household_setting():
    suffix = uuid.uuid4().hex[:8]
    raw_session = f"preference-session-{suffix}"
    _, genre = add_test_catalog()
    with connect() as session:
        household = session.scalar(select(Household).where(Household.slug == "default"))
        user = User(plex_id=f"preference-{suffix}", username="Preference Tester")
        session.add(user); session.flush()
        session.add(UserSession(token_hash=token_hash(raw_session), user_id=user.id,
                                household_id=household.id,
                                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        user_id = user.id
    with TestClient(app) as client:
        client.cookies.set("watchmatch_session", raw_session)
        created = client.post("/api/rooms", json={
            "kind": "movie", "mode": "filter", "filter_type": "genre",
            "filter_value": genre,
        })
        assert created.status_code == 200
        created_code = created.json()["code"]
        remembered = client.get("/api/room-preferences").json()
        assert remembered == {"kind": "movie", "mode": "filter", "filter_type": "genre",
                              "filter_value": genre, "remembered": True}

        settings = client.get("/api/settings").json()
        settings["reset_room_choices"] = True
        settings["matched_item_cooldown_days"] = 7
        assert client.put("/api/settings", json=settings).status_code == 200
        reset = client.get("/api/room-preferences").json()
        assert reset["remembered"] is False
        assert reset["mode"] == "random"
        with connect() as session:
            assert session.get(RoomPreference, user_id) is None

        settings["reset_room_choices"] = False
        client.put("/api/settings", json=settings)
    with connect() as session:
        session.delete(session.get(Room, created_code))
        session.flush()
        session.delete(session.get(User, user_id))


def test_household_matched_title_cooldown_is_configurable():
    suffix = uuid.uuid4().hex[:8]
    library = f"Cooldown {suffix}"
    media_id = f"cooldown-{suffix}"
    with connect() as session:
        household = session.scalar(select(Household).where(Household.slug == "default"))
        session.add(Media(id=media_id, title=f"Cooldown Movie {suffix}", kind="movie",
                          library=library, available=True))
        session.add(Match(household_id=household.id, room_code=f"H{suffix}".upper(),
                          media_id=media_id))
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        original = settings["matched_item_cooldown_days"]
        settings["matched_item_cooldown_days"] = 7
        client.put("/api/settings", json=settings)
        blocked = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "random",
        })
        assert blocked.status_code == 400

        settings["matched_item_cooldown_days"] = 0
        assert client.put("/api/settings", json=settings).status_code == 200
        eligible = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "random",
        })
        assert eligible.status_code == 200
        settings["matched_item_cooldown_days"] = original
        client.put("/api/settings", json=settings)


def test_room_exhaustion_is_reported_after_everyone_finishes():
    media_id, genre = add_test_catalog()
    with TestClient(app) as client:
        code = client.post("/api/rooms", json={
            "kind": "movie", "mode": "filter", "filter_type": "genre",
            "filter_value": genre,
        }).json()["code"]
        p1 = client.post(f"/api/rooms/{code}/join", json={"name": "Alex"}).json()["participant_id"]
        p2 = client.post(f"/api/rooms/{code}/join", json={"name": "Sam"}).json()["participant_id"]
        first = client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": p1, "media_id": media_id, "direction": "left",
        })
        assert first.json()["status"] == "open"
        finished = client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": p2, "media_id": media_id, "direction": "left",
        })
        assert finished.json()["status"] == "exhausted"


def test_second_chance_shows_only_the_other_participants_choices():
    suffix = uuid.uuid4().hex[:8]
    library = f"Second Chance {suffix}"
    media_ids = [f"second-{suffix}-a", f"second-{suffix}-b"]
    with connect() as session:
        session.add_all([
            Media(id=media_ids[0], title=f"Choice A {suffix}", kind="movie",
                  library=library, available=True),
            Media(id=media_ids[1], title=f"Choice B {suffix}", kind="movie",
                  library=library, available=True),
        ])
    with TestClient(app) as client:
        code = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "random",
        }).json()["code"]
        one = client.post(f"/api/rooms/{code}/join", json={"name": "One"}).json()["participant_id"]
        two = client.post(f"/api/rooms/{code}/join", json={"name": "Two"}).json()["participant_id"]
        for participant, directions in ((one, ("right", "left")), (two, ("left", "right"))):
            for media_id, direction in zip(media_ids, directions):
                state = client.post(f"/api/rooms/{code}/swipe", json={
                    "participant_id": participant, "media_id": media_id, "direction": direction,
                }).json()

        assert state["status"] == "open"
        assert state["phase"] == "second_chance"
        one_cards = client.get(f"/api/rooms/{code}/cards", params={"participant_id": one}).json()
        two_cards = client.get(f"/api/rooms/{code}/cards", params={"participant_id": two}).json()
        assert [card["id"] for card in one_cards] == [media_ids[1]]
        assert [card["id"] for card in two_cards] == [media_ids[0]]
        assert one_cards[0]["second_chance_prompt"] == "The other person chose this"

        matched = client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": one, "media_id": media_ids[1], "direction": "right",
        })
        assert matched.json()["status"] == "matched"
        assert matched.json()["match"]["id"] == media_ids[1]


def test_second_chance_three_person_prompt_hides_exact_vote_count():
    suffix = uuid.uuid4().hex[:8]
    library = f"Second Chance Group {suffix}"
    media_id = f"second-group-{suffix}"
    with connect() as session:
        session.add(Media(id=media_id, title=f"Group Choice {suffix}", kind="movie",
                          library=library, available=True))
    with TestClient(app) as client:
        code = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "random",
        }).json()["code"]
        one = client.post(f"/api/rooms/{code}/join", json={"name": "One"}).json()["participant_id"]
        two = client.post(f"/api/rooms/{code}/join", json={"name": "Two"}).json()["participant_id"]
        three = str(uuid.uuid4())
        with connect() as session:
            session.add(Participant(id=three, room_code=code, name="Three"))
            session.add_all([
                Swipe(participant_id=one, room_code=code, media_id=media_id, direction="left"),
                Swipe(participant_id=two, room_code=code, media_id=media_id, direction="right"),
                Swipe(participant_id=three, room_code=code, media_id=media_id, direction="left"),
            ])
            session.flush()
            room = session.get(Room, code)
            mark_room_exhausted_if_complete(session, room)
            assert room.phase == "second_chance"

        card = client.get(f"/api/rooms/{code}/cards", params={"participant_id": one}).json()[0]
        assert card["second_chance_prompt"] == "At least 1 other person chose this"


def test_each_participant_gets_a_stable_personal_card_shuffle():
    suffix = uuid.uuid4().hex[:8]
    library = f"Shuffle {suffix}"
    expected_ids = {f"shuffle-{suffix}-{index}" for index in range(12)}
    with connect() as session:
        session.add_all([
            Media(id=media_id, title=f"Shuffle Movie {index} {suffix}",
                  year=2000 + index, kind="movie", library=library,
                  genres=[], available=True)
            for index, media_id in enumerate(sorted(expected_ids))
        ])
    with TestClient(app) as client:
        created = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "random",
        })
        assert created.status_code == 200
        code = created.json()["code"]
        p1 = client.post(f"/api/rooms/{code}/join", json={"name": "Alex"}).json()["participant_id"]
        p2 = client.post(f"/api/rooms/{code}/join", json={"name": "Sam"}).json()["participant_id"]
        first_order = [card["id"] for card in client.get(
            f"/api/rooms/{code}/cards", params={"participant_id": p1}
        ).json()]
        second_order = [card["id"] for card in client.get(
            f"/api/rooms/{code}/cards", params={"participant_id": p2}
        ).json()]
        reloaded_order = [card["id"] for card in client.get(
            f"/api/rooms/{code}/cards", params={"participant_id": p1}
        ).json()]
        assert set(first_order) == set(second_order) == expected_ids
        assert first_order != second_order
        assert reloaded_order == first_order


def test_top_choice_allowance_promotion_and_matching():
    suffix = uuid.uuid4().hex[:8]
    library = f"Top Choice {suffix}"
    with connect() as session:
        session.add_all([
            Media(id=f"top-{suffix}-{index}", title=f"Top Candidate {suffix} {index}",
                  kind="movie", library=library, genres=[], collections=[], available=True)
            for index in range(16)
        ])

    with TestClient(app) as client:
        room = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "random",
        }).json()
        code = room["code"]
        one = client.post(f"/api/rooms/{code}/join", json={"name": "One"}).json()["participant_id"]
        two = client.post(f"/api/rooms/{code}/join", json={"name": "Two"}).json()["participant_id"]
        one_cards = client.get(f"/api/rooms/{code}/cards", params={"participant_id": one}).json()

        status = client.get(f"/api/rooms/{code}/top-choice-status", params={"participant_id": one})
        assert status.json() == {"limit": 2, "used": 0, "remaining": 2}
        selected = [one_cards[0]["id"], one_cards[1]["id"]]
        for media_id in selected:
            response = client.post(f"/api/rooms/{code}/swipe", json={
                "participant_id": one, "media_id": media_id, "direction": "top",
            })
            assert response.status_code == 200

        exhausted = client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": one, "media_id": one_cards[2]["id"], "direction": "top",
        })
        assert exhausted.status_code == 409
        assert client.get(f"/api/rooms/{code}/top-choice-status", params={
            "participant_id": one,
        }).json()["remaining"] == 0

        two_cards = client.get(f"/api/rooms/{code}/cards", params={"participant_id": two}).json()
        positions = [next(index for index, card in enumerate(two_cards) if card["id"] == media_id)
                     for media_id in selected]
        assert max(positions) <= 5
        assert all(two_cards[position]["top_choice"] for position in positions)

        matched = client.post(f"/api/rooms/{code}/swipe", json={
            "participant_id": two, "media_id": selected[0], "direction": "right",
        })
        assert matched.json()["match"]["id"] == selected[0]


def test_runtime_filter_rating_gate_and_duplicate_collapsing():
    suffix = uuid.uuid4().hex[:8]
    library = f"Runtime {suffix}"
    with connect() as session:
        session.add_all([
            Media(id=f"short-a-{suffix}", title=f"Same Film {suffix}", year=2020,
                  kind="movie", library=library, duration=80 * 60, rating="PG",
                  edition_title="Theatrical", available=True),
            Media(id=f"short-b-{suffix}", title=f"Same Film {suffix}", year=2020,
                  kind="movie", library=library, duration=80 * 60, rating="PG",
                  edition_title="Director's Cut", available=True),
            Media(id=f"blocked-{suffix}", title=f"Blocked {suffix}", year=2021,
                  kind="movie", library=library, duration=80 * 60, rating="R",
                  available=True),
            Media(id=f"long-{suffix}", title=f"Long {suffix}", year=2022,
                  kind="movie", library=library, duration=170 * 60, rating="PG",
                  available=True),
        ])
    with connect() as session:
        settings = session.get(HouseholdSettings, 1)
        previous = list(settings.allowed_content_ratings)
        settings.allowed_content_ratings = ["PG"]
    try:
        with TestClient(app) as client:
            created = client.post("/api/rooms", json={
                "kind": "movie", "library": library, "mode": "filter",
                "filter_type": "runtime", "filter_value": "under_90",
            })
            assert created.status_code == 200
            code = created.json()["code"]
            p1 = client.post(f"/api/rooms/{code}/join", json={"name": "One"}).json()["participant_id"]
            client.post(f"/api/rooms/{code}/join", json={"name": "Two"})
            cards = client.get(f"/api/rooms/{code}/cards", params={"participant_id": p1}).json()
            assert len(cards) == 1
            assert cards[0]["rating"] == "PG"
            assert cards[0]["edition_title"] in {"Theatrical", "Director's Cut"}
    finally:
        with connect() as session:
            session.get(HouseholdSettings, 1).allowed_content_ratings = previous


def test_collection_and_recency_filters():
    suffix = uuid.uuid4().hex[:8]
    library = f"Discovery {suffix}"
    collection = f"Curated {suffix}"
    now = datetime.now(timezone.utc)
    with connect() as session:
        session.add_all([
            Media(id=f"released-{suffix}", title=f"New Release {suffix}", kind="movie",
                  library=library, collections=[collection], released_at=date.today() - timedelta(days=30),
                  added_at=now - timedelta(days=300), watched=False, available=True),
            Media(id=f"added-{suffix}", title=f"New Addition {suffix}", kind="movie",
                  library=library, collections=[], released_at=date.today() - timedelta(days=1000),
                  added_at=now - timedelta(days=20), watched=False, available=True),
            Media(id=f"watched-{suffix}", title=f"Watched Addition {suffix}", kind="movie",
                  library=library, collections=[], released_at=date.today() - timedelta(days=1000),
                  added_at=now - timedelta(days=20), watched=True, available=True),
            Media(id=f"old-{suffix}", title=f"Old {suffix}", kind="movie",
                  library=library, collections=[], released_at=date.today() - timedelta(days=1000),
                  added_at=now - timedelta(days=300), watched=False, available=True),
        ])

    with TestClient(app) as client:
        collection_room = client.post("/api/rooms", json={
            "kind": "movie", "library": library, "mode": "filter",
            "filter_type": "collection", "filter_value": collection,
        })
        assert collection_room.status_code == 200
        with connect() as session:
            assert set(session.scalars(select(RoomMedia.media_id).where(
                RoomMedia.room_code == collection_room.json()["code"]
            ))) == {f"released-{suffix}"}

        for value, expected in (
            ("released:any", {f"released-{suffix}"}),
            ("added:unwatched", {f"added-{suffix}"}),
            ("both:any", {f"released-{suffix}", f"added-{suffix}", f"watched-{suffix}"}),
        ):
            created = client.post("/api/rooms", json={
                "kind": "movie", "library": library, "mode": "filter",
                "filter_type": "recency", "filter_value": value,
            })
            assert created.status_code == 200
            with connect() as session:
                ids = set(session.scalars(select(RoomMedia.media_id).where(
                    RoomMedia.room_code == created.json()["code"]
                )))
            assert ids == expected



def test_plex_player_discovery_has_offline_safe_contract():
    with TestClient(app) as client:
        response = client.get("/api/plex/players")
        assert response.status_code == 200
        assert {"configured", "reachable", "players", "error"} <= response.json().keys()


def test_tv_pool_prefers_shows_with_unwatched_episodes():
    suffix = uuid.uuid4().hex[:8]
    library = f"Shows {suffix}"
    room_code = f"S{suffix}".upper()
    with connect() as session:
        session.add_all([
            Media(id=f"complete-{suffix}", title="Completed Show", kind="show",
                  library=library, unwatched_count=0, available=True),
            Media(id=f"unwatched-{suffix}", title="Unwatched Show", kind="show",
                  library=library, unwatched_count=8, available=True),
        ])
    with connect() as session:
        settings = session.get(HouseholdSettings, 1)
        previous_size = settings.random_pool_size
        settings.random_pool_size = 1
    try:
        with connect() as session:
            from backend.models import Room
            session.add(Room(code=room_code, household_id=1, kind="show", library=library))
            session.flush()
            populate_room_pool(session, room_code, "show", library)
        with connect() as session:
            selected = session.scalar(select(RoomMedia.media_id).where(
                RoomMedia.room_code == room_code
            ))
            assert selected == f"unwatched-{suffix}"
    finally:
        with connect() as session:
            session.get(HouseholdSettings, 1).random_pool_size = previous_size
