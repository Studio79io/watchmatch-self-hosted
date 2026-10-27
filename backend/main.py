import asyncio
import logging
import hashlib
import os
import random
import secrets
import string
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from .config import load_config
from .database import connect, initialize
from .models import (Household, HouseholdInvitation, HouseholdMember,
                     HouseholdSettings, Match, Media, Participant, PlexAuthPin,
                     Room, RoomMedia, RoomPreference, SecondChanceMedia, Swipe,
                     SyncRun, SystemSettings, User, UserSession)
from .catalog import sync_catalog
from .auth import selected_authorization_provider


config = load_config()
connections: dict[str, set[WebSocket]] = {}
active_sync_task: asyncio.Task | None = None
scheduler_task: asyncio.Task | None = None
scheduler_wakeup: asyncio.Event | None = None
logger = logging.getLogger("watchmatch")
filter_cache_last_cleared_at: datetime | None = None
PLEX_TV = "https://plex.tv/api/v2"


def token_hash(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def auth_required():
    value = os.getenv("AUTH_REQUIRED", config.get("auth", {}).get("required", False))
    return value is True or str(value).lower() in {"1", "true", "yes", "on"}


def authenticated_context(request: Request, session, required=True):
    raw = request.cookies.get("watchmatch_session")
    record = session.get(UserSession, token_hash(raw)) if raw else None
    if not record or record.expires_at <= datetime.now(timezone.utc):
        if required:
            raise HTTPException(401, "Sign in with Plex to create a room")
        return None
    return record


def system_owner_context(request: Request, session):
    """Require the explicitly configured Plex identity for platform operations."""
    context = authenticated_context(request, session, required=False)
    if not context:
        raise HTTPException(401, "Sign in with Plex to access the system console")
    owner_plex_id = os.getenv("SYSTEM_OWNER_PLEX_ID", "").strip()
    if not owner_plex_id:
        raise HTTPException(503, "System owner access has not been configured")
    user = session.get(User, context.user_id)
    if not user or not secrets.compare_digest(user.plex_id, owner_plex_id):
        raise HTTPException(403, "This Plex account is not the WatchMatch system owner")
    return context


def serialize_media(media: Media):
    result = {
        column.name: getattr(media, column.name)
        for column in Media.__table__.columns
    }
    for key in ("added_at", "released_at", "updated_at", "unavailable_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if result.get("thumb") and result.get("available", True):
        result["poster_url"] = f"/api/media/{result['id']}/poster"
    return result


def room_state(code):
    with connect() as session:
        room = session.get(Room, code)
        if not room:
            raise HTTPException(404, "Room not found")
        people = session.scalars(
            select(Participant).where(Participant.room_code == code).order_by(Participant.joined_at)
        ).all()
        media = session.get(Media, room.matched_media_id) if room.matched_media_id else None
        return {
            "code": code, "status": room.status, "kind": room.kind,
            "mode": room.mode, "phase": room.phase,
            "filter_type": room.filter_type, "filter_value": room.filter_value,
            "allow_undo": room.allow_undo,
            "offer_another_match": room.offer_another_match,
            "card_limit": room.card_limit,
            "top_choice_count": session.scalar(
                select(func.count()).select_from(Swipe).where(
                    Swipe.room_code == code, Swipe.direction == "top"
                )
            ) or 0,
            "participants": [{"id": p.id, "name": p.name} for p in people],
            "match": serialize_media(media) if media else None,
        }


async def broadcast(code):
    payload = {"type": "room", "data": room_state(code)}
    dead = []
    for ws in connections.get(code, set()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections[code].discard(ws)


def sync_run_payload(run: SyncRun | None):
    if not run:
        return None
    return {
        "id": run.id, "trigger": run.trigger, "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "item_count": run.item_count, "added_count": run.added_count,
        "updated_count": run.updated_count, "removed_count": run.removed_count,
        "error": run.error,
    }


async def run_catalog_sync(trigger: str):
    with connect() as session:
        run = SyncRun(trigger=trigger, status="running")
        session.add(run)
        session.flush()
        run_id = run.id
    logger.info("Plex synchronization started", extra={
        "event": "plex_sync_started", "sync_trigger": trigger, "sync_run_id": run_id,
    })
    try:
        result = await asyncio.to_thread(sync_catalog, config)
        with connect() as session:
            run = session.get(SyncRun, run_id)
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.item_count = result.get("count", 0)
            run.added_count = result.get("added", 0)
            run.updated_count = result.get("updated", 0)
            run.removed_count = result.get("removed", 0)
        get_filter_candidates.cache_clear()
        get_filter_candidate_stats.cache_clear()
        global filter_cache_last_cleared_at
        filter_cache_last_cleared_at = datetime.now(timezone.utc)
        logger.info("Plex synchronization completed", extra={
            "event": "plex_sync_completed", "sync_trigger": trigger,
            "sync_run_id": run_id, "item_count": result.get("count", 0),
            "added_count": result.get("added", 0),
            "updated_count": result.get("updated", 0),
            "removed_count": result.get("removed", 0),
        })
        return result
    except Exception as exc:
        with connect() as session:
            run = session.get(SyncRun, run_id)
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error = str(exc)[:2000]
        logger.exception("Plex synchronization failed", extra={
            "event": "plex_sync_failed", "sync_trigger": trigger, "sync_run_id": run_id,
        })


def queue_catalog_sync(trigger: str):
    global active_sync_task
    if active_sync_task and not active_sync_task.done():
        return False
    active_sync_task = asyncio.create_task(run_catalog_sync(trigger))
    return True


def system_settings(session):
    settings = session.get(SystemSettings, 1)
    if not settings:
        settings = SystemSettings(id=1, sync_interval_minutes=max(
            15, int(config.get("plex", {}).get("sync_interval_minutes", 1440))
        ))
        session.add(settings)
        session.flush()
    return settings


def catalog_libraries(session):
    rows = session.execute(
        select(Media.library, Media.kind, func.count().label("count"))
        .where(Media.available.is_(True))
        .group_by(Media.library, Media.kind).order_by(Media.library, Media.kind)
    ).all()
    return [dict(row._mapping) for row in rows]


def selectable_library_names(session):
    configured = list(system_settings(session).selectable_libraries or [])
    if configured:
        return set(configured)
    excluded = set(config.get("catalog", {}).get("excluded_libraries", []))
    return {row["library"] for row in catalog_libraries(session) if row["library"] not in excluded}


def room_library_options(session, kind: str | None = None):
    allowed = selectable_library_names(session)
    return [row for row in catalog_libraries(session)
            if row["library"] in allowed and (kind is None or row["kind"] == kind)]


def library_management_summary(session):
    selected = set(system_settings(session).selectable_libraries or [])
    grouped = {}
    for row in catalog_libraries(session):
        item = grouped.setdefault(row["library"], {"library": row["library"], "count": 0,
                                                     "kinds": {}, "selectable": row["library"] in selected})
        item["count"] += row["count"]
        item["kinds"][row["kind"]] = row["count"]
    return list(grouped.values())


async def sync_scheduler():
    while True:
        with connect() as session:
            interval = system_settings(session).sync_interval_minutes * 60
        try:
            await asyncio.wait_for(scheduler_wakeup.wait(), timeout=interval)
            scheduler_wakeup.clear()
        except asyncio.TimeoutError:
            if queue_catalog_sync("scheduled") and active_sync_task:
                await active_sync_task


@asynccontextmanager
async def lifespan(app):
    global scheduler_task, scheduler_wakeup
    initialize()
    with connect() as session:
        session.execute(update(SyncRun).where(SyncRun.status == "running").values(
            status="failed", completed_at=datetime.now(timezone.utc),
            error="Interrupted by application restart",
        ))
    logger.info("WatchMatch application starting", extra={"event": "app_starting"})
    queue_catalog_sync("startup")
    scheduler_wakeup = asyncio.Event()
    scheduler_task = asyncio.create_task(sync_scheduler())
    try:
        yield
    finally:
        logger.info("WatchMatch application stopping", extra={"event": "app_stopping"})
        if scheduler_task:
            scheduler_task.cancel()
        if active_sync_task and not active_sync_task.done():
            active_sync_task.cancel()


app = FastAPI(title="WatchMatch", lifespan=lifespan)


@app.middleware("http")
async def structured_request_log(request, call_next):
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled API error", extra={
            "event": "api_error", "method": request.method, "path": request.url.path,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        })
        raise
    logger.info("API request completed", extra={
        "event": "api_request", "method": request.method, "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    })
    return response


class RoomRequest(BaseModel):
    kind: str = "movie"
    library: str | None = None
    mode: str = "random"
    filter_type: str | None = None
    filter_value: str | None = None


class SelectableLibrariesRequest(BaseModel):
    libraries: list[str]


class JoinRequest(BaseModel):
    name: str


class SwipeRequest(BaseModel):
    participant_id: str
    media_id: str
    direction: str


class ParticipantRequest(BaseModel):
    participant_id: str


class HouseholdNameRequest(BaseModel):
    name: str


class SystemRecoveryCodeRequest(BaseModel):
    code: str


class HouseholdRecoveryRequest(BaseModel):
    user_id: int


class SyncSettingsRequest(BaseModel):
    interval_minutes: int


class HouseholdSettingsRequest(BaseModel):
    movies_enabled: bool
    shows_enabled: bool
    random_enabled: bool
    filter_enabled: bool
    genre_match_enabled: bool
    random_pool_size: int
    allowed_content_ratings: list[str]
    undo_enabled: bool
    another_match_enabled: bool
    finite_sessions_enabled: bool
    finite_card_limit: int
    reset_room_choices: bool = False
    matched_item_cooldown_days: int = 7


def settings_payload(settings: HouseholdSettings):
    return {
        "movies_enabled": settings.movies_enabled,
        "shows_enabled": settings.shows_enabled,
        "random_enabled": settings.random_enabled,
        "filter_enabled": settings.filter_enabled,
        "genre_match_enabled": settings.genre_match_enabled,
        "random_pool_size": settings.random_pool_size,
        "allowed_content_ratings": settings.allowed_content_ratings,
        "undo_enabled": settings.undo_enabled,
        "another_match_enabled": settings.another_match_enabled,
        "finite_sessions_enabled": settings.finite_sessions_enabled,
        "finite_card_limit": settings.finite_card_limit,
        "reset_room_choices": settings.reset_room_choices,
        "matched_item_cooldown_days": settings.matched_item_cooldown_days,
    }


def current_household(session):
    slug = config.get("app", {}).get("default_household_slug", "default")
    household = session.scalar(select(Household).where(Household.slug == slug))
    if not household:
        household = Household(slug=slug, name="Default household")
        session.add(household)
        session.flush()
    return household


def household_settings(session, household_id: int | None = None):
    household_id = household_id or current_household(session).id
    settings = session.get(HouseholdSettings, household_id)
    if not settings:
        settings = HouseholdSettings(household_id=household_id)
        session.add(settings)
        session.flush()
    return settings


def mark_room_exhausted_if_complete(session, room: Room):
    """Finish only after every participant has voted on every remaining title."""
    if room.status != "open":
        return False
    if room.phase == "second_chance":
        remaining = session.scalar(
            select(func.count()).select_from(SecondChanceMedia).where(
                SecondChanceMedia.room_code == room.code,
                SecondChanceMedia.direction.is_(None),
            )
        ) or 0
        if remaining == 0:
            room.status = "exhausted"
            logger.info("Second Chance exhausted without a match", extra={
                "event": "room_exhausted", "room_code": room.code,
            })
            return True
        return False
    if room.phase != "movies":
        return False
    pool_count = session.scalar(
        select(func.count()).select_from(RoomMedia).where(RoomMedia.room_code == room.code)
    ) or 0
    people = session.scalar(
        select(func.count()).select_from(Participant).where(Participant.room_code == room.code)
    ) or 0
    if not pool_count or people < 2:
        return False
    vote_count = session.scalar(
        select(func.count()).select_from(Swipe).where(
            Swipe.room_code == room.code,
            Swipe.media_id.in_(select(RoomMedia.media_id).where(RoomMedia.room_code == room.code)),
        )
    ) or 0
    if vote_count >= pool_count * people:
        votes = session.scalars(select(Swipe).where(
            Swipe.room_code == room.code,
            Swipe.media_id.not_like("genre:%"),
        )).all()
        approvals: dict[str, set[str]] = {}
        top_choices: dict[str, set[str]] = {}
        for vote in votes:
            if vote.direction in ("right", "top"):
                approvals.setdefault(vote.media_id, set()).add(vote.participant_id)
            if vote.direction == "top":
                top_choices.setdefault(vote.media_id, set()).add(vote.participant_id)
        snapshots = []
        for vote in votes:
            other_approvals = approvals.get(vote.media_id, set()) - {vote.participant_id}
            if vote.direction == "left" and other_approvals:
                snapshots.append(SecondChanceMedia(
                    participant_id=vote.participant_id,
                    media_id=vote.media_id,
                    room_code=room.code,
                    source_top_choice=bool(top_choices.get(vote.media_id, set()) - {vote.participant_id}),
                ))
        if snapshots:
            session.add_all(snapshots)
            room.phase = "second_chance"
            logger.info("Room entered Second Chance", extra={
                "event": "second_chance_started", "room_code": room.code,
                "item_count": len(snapshots),
            })
        else:
            room.status = "exhausted"
            logger.info("Room exhausted without a match", extra={
                "event": "room_exhausted", "room_code": room.code,
            })
        return True
    return False


def top_choice_limit(pool_count: int):
    if pool_count <= 15:
        return 1
    if pool_count <= 50:
        return 2
    if pool_count <= 100:
        return 3
    if pool_count <= 150:
        return 4
    return 5


def top_choice_status(session, room_code: str, participant_id: str):
    pool_count = session.scalar(
        select(func.count()).select_from(RoomMedia).where(RoomMedia.room_code == room_code)
    ) or 0
    used = session.scalar(
        select(func.count()).select_from(Swipe).where(
            Swipe.room_code == room_code,
            Swipe.participant_id == participant_id,
            Swipe.direction == "top",
        )
    ) or 0
    limit = top_choice_limit(pool_count)
    return {"limit": limit, "used": used, "remaining": max(0, limit - used)}


def populate_room_pool(session, code: str, kind: str, library: str | None,
                       filter_type: str | None = None, filter_value: str | None = None):
    catalog_cfg = config.get("catalog", {})
    room = session.get(Room, code)
    household_id = room.household_id if room else current_household(session).id
    settings = household_settings(session, household_id)
    pool_size = settings.random_pool_size
    if room and room.card_limit:
        pool_size = min(pool_size, room.card_limit) if pool_size > 0 else room.card_limit
    cooldown = settings.matched_item_cooldown_days
    query = select(Media.id).where(Media.kind == kind, Media.available.is_(True))
    if library:
        query = query.where(Media.library == library)
    if not catalog_cfg.get("include_watched", True):
        query = query.where(Media.watched.is_(False))
    elif not catalog_cfg.get("include_unwatched", True):
        query = query.where(Media.watched.is_(True))
    allowed_ratings = [value for value in settings.allowed_content_ratings if value]
    if allowed_ratings:
        query = query.where(Media.rating.in_(allowed_ratings))
    if filter_type and filter_value:
        if filter_type in ("genre", "director", "actor"):
            column = {"genre": Media.genres, "director": Media.directors, "actor": Media.actors}[filter_type]
            if filter_type == "genre" and filter_value in catalog_cfg.get("genre_collections", []):
                query = query.where(or_(Media.genres.contains([filter_value]),
                                        Media.collections.contains([filter_value])))
            else:
                query = query.where(column.contains([filter_value]))
        elif filter_type == "studio":
            query = query.where(func.lower(Media.studio) == filter_value.lower())
        elif filter_type == "runtime":
            runtime_ranges = {
                "under_90": (None, 90), "90_120": (90, 120),
                "120_150": (120, 150), "over_150": (150, None),
            }
            if filter_value not in runtime_ranges:
                raise HTTPException(400, "Unknown runtime range")
            minimum, maximum = runtime_ranges[filter_value]
            if minimum is not None:
                query = query.where(Media.duration >= minimum * 60)
            if maximum is not None:
                query = query.where(Media.duration < maximum * 60)
        elif filter_type == "collection":
            query = query.where(Media.collections.contains([filter_value]))
        elif filter_type == "recency":
            try:
                basis, watched_gate = filter_value.split(":", 1)
            except ValueError:
                raise HTTPException(400, "Unknown recency filter")
            now = datetime.now(timezone.utc)
            released_after = date.today() - timedelta(days=365)
            added_after = now - timedelta(days=90)
            recency_filters = {
                "released": Media.released_at >= released_after,
                "added": Media.added_at >= added_after,
                "both": or_(Media.released_at >= released_after, Media.added_at >= added_after),
            }
            if basis not in recency_filters or watched_gate not in ("any", "unwatched"):
                raise HTTPException(400, "Unknown recency filter")
            query = query.where(recency_filters[basis])
            if watched_gate == "unwatched":
                query = query.where(Media.watched.is_(False))
    if cooldown > 0:
        recent_matches = select(Match.media_id).where(
            Match.household_id == household_id,
            Match.created_at >= datetime.now(timezone.utc) - timedelta(days=cooldown)
        )
        query = query.where(Media.id.not_in(recent_matches))
    ranked = query.add_columns(
        Media.unwatched_count.label("unwatched_count"),
        func.row_number().over(
            partition_by=(func.lower(Media.title), Media.year),
            order_by=(Media.edition_title.is_(None), Media.id),
        ).label("duplicate_rank"),
    ).subquery()
    pool_query = select(ranked.c.id).where(ranked.c.duplicate_rank == 1)
    if kind == "show":
        pool_query = pool_query.order_by(ranked.c.unwatched_count.desc(), func.random())
    else:
        pool_query = pool_query.order_by(func.random())
    if pool_size > 0:
        pool_query = pool_query.limit(pool_size)
    session.execute(delete(RoomMedia).where(RoomMedia.room_code == code))
    session.add_all(RoomMedia(room_code=code, media_id=media_id)
                    for media_id in session.scalars(pool_query))


@app.get("/api/health")
def health():
    """Liveness: the process can answer requests."""
    return {"ok": True}


def migration_revisions(session):
    current = MigrationContext.configure(session.connection()).get_current_revision()
    script = ScriptDirectory.from_config(AlembicConfig("alembic.ini"))
    return current, script.get_current_head()


@app.get("/api/ready")
def readiness():
    """Readiness: PostgreSQL is reachable and its schema is current."""
    with connect() as session:
        session.execute(select(1))
        current, expected = migration_revisions(session)
        media_count = session.scalar(select(func.count()).select_from(Media).where(Media.available.is_(True)))
    ready = current == expected
    return {
        "ok": ready, "database": "ok", "migration_current": current,
        "migration_expected": expected, "catalog_items": media_count,
        "sync_running": bool(active_sync_task and not active_sync_task.done()),
    }


@app.get("/api/settings")
def get_household_settings(request: Request):
    with connect() as session:
        context = authenticated_context(request, session, required=False)
        household_id = context.household_id if context else current_household(session).id
        return settings_payload(household_settings(session, household_id))


def default_room_choices(settings: HouseholdSettings):
    kind = "movie" if settings.movies_enabled else "show"
    mode = "random" if settings.random_enabled else (
        "filter" if settings.filter_enabled else "genre_match"
    )
    return {"kind": kind, "mode": mode, "filter_type": None, "filter_value": None,
            "remembered": False}


@app.get("/api/room-preferences")
def get_room_preferences(request: Request):
    with connect() as session:
        context = authenticated_context(request, session, required=False)
        household_id = context.household_id if context else current_household(session).id
        settings = household_settings(session, household_id)
        fallback = default_room_choices(settings)
        if not context or settings.reset_room_choices:
            return fallback
        preference = session.get(RoomPreference, context.user_id)
        if not preference or preference.household_id != household_id:
            return fallback
        kind_enabled = settings.movies_enabled if preference.kind == "movie" else settings.shows_enabled
        mode_enabled = {
            "random": settings.random_enabled,
            "filter": settings.filter_enabled,
            "genre_match": settings.genre_match_enabled,
        }.get(preference.mode, False)
        if not kind_enabled or not mode_enabled:
            return fallback
        return {"kind": preference.kind, "mode": preference.mode,
                "filter_type": preference.filter_type,
                "filter_value": preference.filter_value, "remembered": True}


def plex_headers(token: str | None = None):
    auth_cfg = config.get("auth", {})
    headers = {
        "Accept": "application/json", "X-Plex-Product": "WatchMatch",
        "X-Plex-Client-Identifier": auth_cfg.get("plex_client_identifier", "watchmatch-web"),
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


def configured_machine_identifier():
    configured = os.getenv("PLEX_MACHINE_IDENTIFIER") or config.get("auth", {}).get("plex_machine_identifier")
    if configured:
        return configured
    from plexapi.server import PlexServer
    return PlexServer(config["plex"]["url"], config["plex"]["token"]).machineIdentifier


@app.post("/api/auth/start")
@app.post("/api/auth/plex/start", include_in_schema=False)
def start_authorization():
    try:
        result = httpx.post(f"{PLEX_TV}/pins", headers=plex_headers(),
                            data={"strong": "true"}, timeout=10).raise_for_status().json()
    except Exception as exc:
        raise HTTPException(502, "Plex authentication is unavailable") from exc
    state = secrets.token_urlsafe(32)
    with connect() as session:
        session.add(PlexAuthPin(state=state, pin_id=result["id"], code=result["code"],
                                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30)))
    client_id = config.get("auth", {}).get("plex_client_identifier", "watchmatch-web")
    auth_url = ("https://app.plex.tv/auth#?clientID=" + client_id + "&code=" + result["code"] +
                "&context%5Bdevice%5D%5Bproduct%5D=WatchMatch")
    return {"state": state, "auth_url": auth_url, "expires_in": 1800}


@app.get("/api/auth/poll/{state}")
@app.get("/api/auth/plex/poll/{state}", include_in_schema=False)
def poll_authorization(state: str, response: Response):
    with connect() as session:
        pin = session.get(PlexAuthPin, state)
        if not pin or pin.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(410, "Plex sign-in expired")
        pin_id, code = pin.pin_id, pin.code
    try:
        plex_response = httpx.get(f"{PLEX_TV}/pins/{pin_id}", headers=plex_headers(),
                                  params={"code": code}, timeout=10)
        if plex_response.status_code == 429:
            try:
                retry_after = int(plex_response.headers.get("Retry-After", "5"))
            except ValueError:
                retry_after = 5
            return {"status": "pending", "retry_after": max(3, min(retry_after, 30))}
        result = plex_response.raise_for_status().json()
    except Exception as exc:
        raise HTTPException(502, "Could not check Plex sign-in") from exc
    plex_token = result.get("authToken")
    if not plex_token:
        return {"status": "pending"}
    try:
        user_data = httpx.get(f"{PLEX_TV}/user", headers=plex_headers(plex_token),
                              timeout=10).raise_for_status().json()
        resources = httpx.get(f"{PLEX_TV}/resources", headers=plex_headers(plex_token),
                              params={"includeHttps": 1, "includeRelay": 1},
                              timeout=10).raise_for_status().json()
        machine_id = configured_machine_identifier()
        authorization_provider = selected_authorization_provider(config)
        provider_identity = authorization_provider.identity(user_data)
    except Exception as exc:
        raise HTTPException(502, "Could not verify Plex server access") from exc
    if not authorization_provider.has_configured_server_access(resources, machine_id):
        raise HTTPException(403, "This Plex account does not have access to the configured Plex server")
    plex_id = provider_identity.external_id
    with connect() as session:
        user = session.scalar(select(User).where(
            User.provider == provider_identity.provider,
            User.provider_external_id == provider_identity.external_id,
        ))
        if not user:
            user = User(plex_id=plex_id, provider=provider_identity.provider,
                        provider_external_id=provider_identity.external_id,
                        username=user_data.get("username") or user_data.get("title") or "Plex user",
                        email=user_data.get("email"))
            session.add(user); session.flush()
        membership = session.scalar(select(HouseholdMember).where(HouseholdMember.user_id == user.id))
        if not membership:
            any_member = session.scalar(select(HouseholdMember).limit(1))
            if not any_member:
                household = current_household(session)
            else:
                household = Household(slug=f"plex-{user.plex_id.lower()}", name=f"{user.username}'s household")
                session.add(household); session.flush()
                session.add(HouseholdSettings(household_id=household.id))
            membership = HouseholdMember(household_id=household.id, user_id=user.id, role="admin")
            session.add(membership)
        raw_session = secrets.token_urlsafe(48)
        hours = int(config.get("auth", {}).get("session_hours", 24))
        session.add(UserSession(token_hash=token_hash(raw_session), user_id=user.id,
                                household_id=membership.household_id,
                                expires_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
        session.delete(session.get(PlexAuthPin, state))
    response.set_cookie("watchmatch_session", raw_session, httponly=True,
                        secure=config["app"]["public_url"].startswith("https://"),
                        samesite="lax", max_age=hours * 3600)
    return {"status": "authenticated", "username": user.username}


@app.get("/api/auth/me")
def auth_me(request: Request):
    raw = request.cookies.get("watchmatch_session")
    if not raw:
        return {"authenticated": False, "required": auth_required()}
    with connect() as session:
        identity = session.execute(
            select(User.username, Household.slug, Household.name)
            .select_from(UserSession)
            .join(User, User.id == UserSession.user_id)
            .join(Household, Household.id == UserSession.household_id)
            .where(UserSession.token_hash == token_hash(raw),
                   UserSession.expires_at > datetime.now(timezone.utc))
        ).one_or_none()
        if not identity:
            return {"authenticated": False, "required": auth_required()}
        return {"authenticated": True, "required": auth_required(),
                "username": identity.username,
                "household": {"slug": identity.slug, "name": identity.name}}


@app.get("/api/system/access")
def system_access(request: Request):
    with connect() as session:
        context = system_owner_context(request, session)
        user = session.get(User, context.user_id)
        return {"authorized": True, "username": user.username}


@app.post("/api/system/recovery/session")
def system_recovery_session(payload: SystemRecoveryCodeRequest, response: Response):
    configured_code = os.getenv("SYSTEM_RECOVERY_CODE", "").strip()
    supplied_code = payload.code.strip()
    if len(configured_code) < 32:
        raise HTTPException(503, "Emergency system recovery is not configured")
    if not supplied_code or not secrets.compare_digest(supplied_code, configured_code):
        raise HTTPException(401, "Invalid recovery code")
    owner_plex_id = os.getenv("SYSTEM_OWNER_PLEX_ID", "").strip()
    with connect() as session:
        owner = session.scalar(select(User).where(User.plex_id == owner_plex_id))
        if not owner:
            raise HTTPException(409, "The configured owner must sign in with Plex at least once")
        membership = session.scalar(select(HouseholdMember).where(HouseholdMember.user_id == owner.id))
        household_id = membership.household_id if membership else current_household(session).id
        raw_session = secrets.token_urlsafe(48)
        session.add(UserSession(token_hash=token_hash(raw_session), user_id=owner.id,
                                household_id=household_id,
                                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)))
    response.set_cookie("watchmatch_session", raw_session, httponly=True,
                        secure=config["app"]["public_url"].startswith("https://"),
                        samesite="strict", max_age=15 * 60)
    logger.warning("Emergency system recovery session created", extra={
        "event": "system_recovery_session_created", "owner_user_id": owner.id,
    })
    return {"ok": True, "expires_in": 15 * 60}


@app.get("/api/system/accounts")
def system_accounts(request: Request):
    with connect() as session:
        system_owner_context(request, session)
        users = session.scalars(select(User).order_by(User.username, User.id)).all()
        households = session.scalars(select(Household).order_by(Household.name, Household.id)).all()
        memberships = session.scalars(select(HouseholdMember)).all()
        user_by_household = {membership.household_id: membership.user_id for membership in memberships}
        room_counts = dict(session.execute(
            select(Room.household_id, func.count()).group_by(Room.household_id)
        ).all())
        match_counts = dict(session.execute(
            select(Match.household_id, func.count()).group_by(Match.household_id)
        ).all())
        return {
            "users": [{"id": user.id, "username": user.username,
                       "plex_id": user.plex_id} for user in users],
            "households": [{"id": household.id, "name": household.name,
                            "slug": household.slug,
                            "user_id": user_by_household.get(household.id),
                            "room_count": room_counts.get(household.id, 0),
                            "match_count": match_counts.get(household.id, 0)}
                           for household in households],
        }


@app.post("/api/system/households/{household_id}/recover")
def recover_household_account(household_id: int, payload: HouseholdRecoveryRequest,
                              request: Request):
    with connect() as session:
        context = system_owner_context(request, session)
        household = session.get(Household, household_id)
        target = session.get(User, payload.user_id)
        if not household or not target:
            raise HTTPException(404, "Household or Plex account not found")
        destination_members = session.scalars(select(HouseholdMember).where(
            HouseholdMember.household_id == household_id
        )).all()
        affected_user_ids = {member.user_id for member in destination_members} | {target.id}
        session.execute(delete(UserSession).where(UserSession.user_id.in_(affected_user_ids)))
        session.execute(delete(HouseholdMember).where(
            or_(HouseholdMember.household_id == household_id,
                HouseholdMember.user_id == target.id)
        ))
        session.add(HouseholdMember(household_id=household_id, user_id=target.id, role="admin"))
        logger.warning("Household Plex account recovered", extra={
            "event": "household_account_recovered", "actor_user_id": context.user_id,
            "household_id": household_id, "assigned_user_id": target.id,
            "revoked_session_user_count": len(affected_user_ids),
        })
        return {"ok": True, "household": {"id": household.id, "name": household.name},
                "user": {"id": target.id, "username": target.username},
                "sessions_revoked": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    raw = request.cookies.get("watchmatch_session")
    if raw:
        with connect() as session:
            record = session.get(UserSession, token_hash(raw))
            if record:
                session.delete(record)
    response.delete_cookie("watchmatch_session", httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/household")
def get_current_household(request: Request):
    with connect() as session:
        context = authenticated_context(request, session, required=False)
        household = session.get(Household, context.household_id) if context else current_household(session)
        return {"slug": household.slug, "name": household.name}


@app.put("/api/household")
def rename_household(payload: HouseholdNameRequest, request: Request):
    name = payload.name.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, "Household name must be between 1 and 80 characters")
    with connect() as session:
        context = authenticated_context(request, session)
        membership = session.get(HouseholdMember, (context.household_id, context.user_id))
        if not membership or membership.role != "admin":
            raise HTTPException(403, "Household administrator access required")
        household = session.get(Household, context.household_id)
        household.name = name
        return {"slug": household.slug, "name": household.name}


@app.post("/api/household/invitations")
def create_household_invitation(request: Request):
    with connect() as session:
        context = authenticated_context(request, session)
        membership = session.get(HouseholdMember, (context.household_id, context.user_id))
        if not membership or membership.role != "admin":
            raise HTTPException(403, "Household administrator access required")
        raw = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        session.add(HouseholdInvitation(token_hash=token_hash(raw), household_id=context.household_id,
                                        created_by_user_id=context.user_id, expires_at=expires_at))
        household = session.get(Household, context.household_id)
    url = f"{config['app']['public_url'].rstrip('/')}/household/join?invite={raw}"
    return {"invite_url": url, "household_name": household.name,
            "expires_at": expires_at.isoformat()}


@app.get("/api/household/invitations/{raw_token}")
def inspect_household_invitation(raw_token: str):
    with connect() as session:
        invitation = session.get(HouseholdInvitation, token_hash(raw_token))
        if not invitation or invitation.accepted_at or invitation.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(410, "This household invitation is invalid or expired")
        household = session.get(Household, invitation.household_id)
        return {"household_name": household.name, "expires_at": invitation.expires_at.isoformat()}


@app.post("/api/household/invitations/{raw_token}/accept")
def accept_household_invitation(raw_token: str, request: Request):
    with connect() as session:
        context = authenticated_context(request, session)
        invitation = session.get(HouseholdInvitation, token_hash(raw_token))
        if not invitation or invitation.accepted_at or invitation.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(410, "This household invitation is invalid or expired")
        if context.household_id != invitation.household_id:
            current = session.get(Household, context.household_id)
            room_count = session.scalar(select(func.count()).select_from(Room).where(
                Room.household_id == context.household_id)) or 0
            match_count = session.scalar(select(func.count()).select_from(Match).where(
                Match.household_id == context.household_id)) or 0
            if room_count or match_count:
                raise HTTPException(409, "Your current household has activity and must be merged manually")
            old_membership = session.get(HouseholdMember, (context.household_id, context.user_id))
            if old_membership:
                session.delete(old_membership); session.flush()
            context.household_id = invitation.household_id
            session.add(HouseholdMember(household_id=invitation.household_id,
                                        user_id=context.user_id, role="admin"))
            if current.slug == f"plex-{session.get(User, context.user_id).plex_id.lower()}":
                session.delete(current)
        invitation.accepted_by_user_id = context.user_id
        invitation.accepted_at = datetime.now(timezone.utc)
        household = session.get(Household, invitation.household_id)
        return {"joined": True, "household": {"slug": household.slug, "name": household.name}}


@app.put("/api/settings")
def update_household_settings(request: HouseholdSettingsRequest, http_request: Request):
    if not request.movies_enabled and not request.shows_enabled:
        raise HTTPException(400, "Enable at least one media type")
    if not request.random_enabled and not request.filter_enabled and not request.genre_match_enabled:
        raise HTTPException(400, "Enable at least one search type")
    if request.random_pool_size < 0:
        raise HTTPException(400, "Random pool size cannot be negative")
    if request.finite_card_limit < 1:
        raise HTTPException(400, "Finite-session card limit must be at least one")
    if not 0 <= request.matched_item_cooldown_days <= 3650:
        raise HTTPException(400, "Matched-title cooldown must be between 0 and 3650 days")
    allowed_rating_values = {"G", "PG", "PG-13", "R", "NC-17", "NR"}
    ratings = list(dict.fromkeys(request.allowed_content_ratings))
    if any(rating not in allowed_rating_values for rating in ratings):
        raise HTTPException(400, "Unknown content rating")
    with connect() as session:
        context = authenticated_context(http_request, session, required=auth_required())
        household = session.get(Household, context.household_id) if context else current_household(session)
        settings = household_settings(session, household.id)
        for field in ("movies_enabled", "shows_enabled", "random_enabled", "filter_enabled",
                      "genre_match_enabled", "random_pool_size", "undo_enabled",
                      "another_match_enabled", "finite_sessions_enabled", "finite_card_limit",
                      "reset_room_choices", "matched_item_cooldown_days"):
            setattr(settings, field, getattr(request, field))
        settings.allowed_content_ratings = ratings
        if settings.reset_room_choices:
            session.execute(delete(RoomPreference).where(
                RoomPreference.household_id == household.id
            ))
        session.flush()
        return settings_payload(settings)


def request_household(request: Request, session):
    context = authenticated_context(request, session, required=auth_required())
    return session.get(Household, context.household_id) if context else current_household(session)


@app.get("/api/match-history")
def match_history(request: Request):
    with connect() as session:
        household = request_household(request, session)
        settings = household_settings(session, household.id)
        rows = session.execute(
            select(Match, Media).outerjoin(Media, Media.id == Match.media_id)
            .where(Match.household_id == household.id)
            .order_by(Match.created_at.desc(), Match.id.desc()).limit(100)
        ).all()
        now = datetime.now(timezone.utc)
        result = []
        for match, media in rows:
            eligible_at = match.created_at + timedelta(days=settings.matched_item_cooldown_days)
            result.append({
                "id": match.id, "room_code": match.room_code,
                "media_id": match.media_id,
                "title": media.title if media else "Unavailable title",
                "year": media.year if media else None,
                "kind": media.kind if media else None,
                "available": media.available if media else False,
                "matched_at": match.created_at.isoformat(),
                "eligible_at": eligible_at.isoformat(),
                "eligible": settings.matched_item_cooldown_days == 0 or eligible_at <= now,
            })
        return result


@app.post("/api/match-history/{match_id}/restore")
def restore_match_history(match_id: int, request: Request):
    with connect() as session:
        household = request_household(request, session)
        match = session.get(Match, match_id)
        if not match or match.household_id != household.id:
            raise HTTPException(404, "Match not found")
        media_id = match.media_id
        removed = session.execute(delete(Match).where(
            Match.household_id == household.id, Match.media_id == media_id
        )).rowcount
        return {"ok": True, "removed": removed, "media_id": media_id}


@app.delete("/api/match-history")
def clear_match_history(request: Request):
    with connect() as session:
        household = request_household(request, session)
        removed = session.execute(delete(Match).where(
            Match.household_id == household.id
        )).rowcount
        return {"ok": True, "removed": removed}


@app.post("/api/sync")
async def request_sync(request: Request):
    with connect() as session:
        system_owner_context(request, session)
    accepted = queue_catalog_sync("manual")
    return {"accepted": accepted, "status": "queued" if accepted else "already_running"}


@app.get("/api/sync/status")
def sync_status(request: Request):
    with connect() as session:
        system_owner_context(request, session)
        latest = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
        history = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(10)).all()
        interval = system_settings(session).sync_interval_minutes
    return {
        "running": bool(active_sync_task and not active_sync_task.done()),
        "interval_minutes": interval,
        "latest": sync_run_payload(latest),
        "history": [sync_run_payload(run) for run in history],
    }


@app.put("/api/sync/settings")
def update_sync_settings(payload: SyncSettingsRequest, request: Request):
    if not 15 <= payload.interval_minutes <= 10080:
        raise HTTPException(400, "Synchronization interval must be between 15 and 10080 minutes")
    with connect() as session:
        system_owner_context(request, session)
        settings = system_settings(session)
        settings.sync_interval_minutes = payload.interval_minutes
    if scheduler_wakeup:
        scheduler_wakeup.set()
    return {"interval_minutes": payload.interval_minutes}


@app.get("/api/libraries")
def libraries(request: Request):
    with connect() as session:
        system_owner_context(request, session)
        return catalog_libraries(session)


@app.get("/api/system/libraries")
def system_libraries(request: Request):
    with connect() as session:
        system_owner_context(request, session)
        settings = system_settings(session)
        return {"selectable_libraries": list(settings.selectable_libraries or []),
                "selection_required": bool(settings.selectable_libraries or []),
                "catalog": library_management_summary(session)}


@app.put("/api/system/libraries")
def update_system_libraries(payload: SelectableLibrariesRequest, request: Request):
    names = [name.strip() for name in payload.libraries if name.strip()]
    if len(names) != len(set(names)):
        raise HTTPException(400, "Each selectable library may be listed only once")
    with connect() as session:
        system_owner_context(request, session)
        available = {row["library"] for row in catalog_libraries(session)}
        excluded = set(config.get("catalog", {}).get("excluded_libraries", []))
        unknown, blocked = sorted(set(names) - available), sorted(set(names) & excluded)
        if unknown:
            raise HTTPException(400, f"Unknown catalog libraries: {', '.join(unknown)}")
        if blocked:
            raise HTTPException(400, f"Excluded libraries cannot be selectable: {', '.join(blocked)}")
        system_settings(session).selectable_libraries = names
    get_filter_candidates.cache_clear()
    get_filter_candidate_stats.cache_clear()
    return {"selectable_libraries": names}


@app.get("/api/room-libraries")
def room_libraries(kind: str | None = None):
    if kind is not None and kind not in ("movie", "show"):
        raise HTTPException(400, "Unknown media type")
    with connect() as session:
        return room_library_options(session, kind)


@app.get("/api/room-library-policy")
def room_library_policy(kind: str = "movie"):
    """Expose only whether a room must explicitly select a library."""
    with connect() as session:
        if kind not in ("movie", "show"):
            raise HTTPException(400, "Unknown media type")
        return {"selection_required": bool(system_settings(session).selectable_libraries or []),
                "default_library": config.get("catalog", {}).get("default_libraries", {}).get(kind)}


@app.get("/api/plex/players")
def plex_players():
    """Discover Plex playback targets without failing when every player is offline."""
    plex_cfg = config.get("plex", {})
    if not plex_cfg.get("token"):
        return {"configured": False, "reachable": False, "players": [],
                "error": "Plex is not configured"}
    try:
        from plexapi.server import PlexServer
        clients = PlexServer(plex_cfg["url"], plex_cfg["token"]).clients()
        return {
            "configured": True, "reachable": True, "error": None,
            "players": [{
                "name": getattr(client, "title", None) or getattr(client, "name", "Plex player"),
                "product": getattr(client, "product", None),
                "platform": getattr(client, "platform", None),
                "machine_identifier": getattr(client, "machineIdentifier", None),
            } for client in clients],
        }
    except Exception as exc:
        logger.warning("Plex player discovery unavailable", extra={
            "event": "plex_players_unavailable", "error": str(exc)[:500],
        })
        return {"configured": True, "reachable": False, "players": [],
                "error": "No Plex players are currently reachable"}


@app.get("/api/diagnostics/catalog")
def catalog_diagnostics(request: Request):
    """Metadata integrity summary without exposing filesystem paths."""
    with connect() as session:
        system_owner_context(request, session)
        available_filter = Media.available.is_(True)
        counts = dict(session.execute(
            select(Media.kind, func.count()).where(available_filter).group_by(Media.kind)
        ).all())
        unavailable = session.scalar(
            select(func.count()).select_from(Media).where(Media.available.is_(False))
        )
        missing_posters = session.execute(
            select(Media.id, Media.title, Media.year).where(
                available_filter, or_(Media.thumb.is_(None), Media.thumb == "")
            ).order_by(Media.title).limit(100)
        ).all()
        multiple_files = session.execute(
            select(Media.id, Media.title, Media.year, Media.file_count,
                   Media.version_count, Media.edition_title).where(
                available_filter, or_(Media.file_count > 1, Media.version_count > 1,
                                      Media.edition_title.is_not(None))
            ).order_by(Media.version_count.desc(), Media.file_count.desc(), Media.title).limit(100)
        ).all()
        duplicate_groups = session.execute(
            select(Media.title, Media.year, func.count().label("count")).where(available_filter)
            .group_by(Media.title, Media.year).having(func.count() > 1)
            .order_by(func.count().desc(), Media.title).limit(100)
        ).all()
        path_year_rows = session.execute(
            select(Media.id, Media.title, Media.year, Media.path_years).where(
                available_filter, Media.year.is_not(None), Media.path_years != []
            )
        ).all()
    year_mismatches = [
        {"id": row.id, "title": row.title, "metadata_year": row.year,
         "years_found_in_paths": row.path_years}
        for row in path_year_rows if any(year != row.year for year in row.path_years)
    ][:100]
    cache = get_filter_candidate_stats.cache_info()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available_counts": counts, "unavailable_count": unavailable,
        "filter_cache": {
            "entries": cache.currsize, "hits": cache.hits, "misses": cache.misses,
            "max_entries": cache.maxsize,
            "last_cleared_at": filter_cache_last_cleared_at.isoformat()
            if filter_cache_last_cleared_at else None,
        },
        "issues": {
            "missing_posters": [dict(row._mapping) for row in missing_posters],
            "multiple_files": [dict(row._mapping) for row in multiple_files],
            "duplicate_title_year": [dict(row._mapping) for row in duplicate_groups],
            "path_year_mismatches": year_mismatches,
        },
    }


@app.post("/api/diagnostics/cache/clear")
def clear_metadata_cache(request: Request):
    with connect() as session:
        system_owner_context(request, session)
    global filter_cache_last_cleared_at
    get_filter_candidates.cache_clear()
    get_filter_candidate_stats.cache_clear()
    filter_cache_last_cleared_at = datetime.now(timezone.utc)
    logger.info("Metadata filter cache cleared", extra={"event": "metadata_cache_cleared"})
    return {"ok": True, "cleared_at": filter_cache_last_cleared_at.isoformat()}


@app.get("/api/filter-options")
def filter_options(kind: str = "movie", library: str | None = None,
                   filter_type: str = "genre", q: str = ""):
    if kind not in ("movie", "show"):
        raise HTTPException(400, "Unknown media type")
    with connect() as session:
        permitted = {row["library"] for row in room_library_options(session, kind)}
    library = library or config.get("catalog", {}).get("default_libraries", {}).get(kind)
    if library not in permitted:
        raise HTTPException(400, "That Plex library is not selectable for rooms")
    limit = 200 if filter_type == "genre" else 20
    candidates = get_filter_candidate_stats(kind, library, filter_type)
    query = q.strip().casefold()
    if not query:
        return [label for label, _ in candidates[:limit]] if filter_type == "genre" else []

    def relevance(label: str):
        value = label.casefold()
        if value == query:
            return 5, 1.0
        if value.startswith(query):
            return 4, 1.0
        if any(word.startswith(query) for word in value.replace("-", " ").split()):
            return 3.5, 1.0
        if query in value:
            return 3, 1.0
        # Avoid comparing every query with every cast member. A shared initial or
        # character pair keeps typo tolerance while sharply reducing the work.
        if abs(len(value) - len(query)) > max(4, len(query) // 2):
            return 0, 0.0
        pairs = {query[index:index + 2] for index in range(max(0, len(query) - 1))}
        if value[:1] != query[:1] and not any(pair in value for pair in pairs):
            return 0, 0.0
        words = value.replace("-", " ").split()
        fuzzy = max([SequenceMatcher(None, query, value).ratio(),
                     *[SequenceMatcher(None, query, word).ratio() for word in words]])
        return (2, fuzzy) if fuzzy >= 0.60 else (0, 0.0)

    ranked = sorted(((*relevance(label), frequency, label) for label, frequency in candidates),
                    key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [label for tier, score, frequency, label in ranked if tier > 0][:limit]


@lru_cache(maxsize=16)
def get_filter_candidates(kind: str, library: str | None, filter_type: str):
    return tuple(label for label, _ in get_filter_candidate_stats(kind, library, filter_type))


@lru_cache(maxsize=16)
def get_filter_candidate_stats(kind: str, library: str | None, filter_type: str):
    with connect() as session:
        if filter_type in ("genre", "director", "actor"):
            column = {"genre": "genres", "director": "directors", "actor": "actors"}[filter_type]
            rows = session.execute(text(
                f"SELECT item.value AS label, count(*)::int AS frequency FROM media "
                f"CROSS JOIN LATERAL jsonb_array_elements_text({column}) AS item(value) "
                "WHERE kind=:kind AND library=:library AND available=true "
                "GROUP BY item.value ORDER BY item.value"
            ), {"kind": kind, "library": library}).all()
        elif filter_type == "studio":
            rows = session.execute(
                select(Media.studio.label("label"), func.count().label("frequency")).where(
                    Media.kind == kind, Media.library == library,
                    Media.available.is_(True), Media.studio.is_not(None)
                ).group_by(Media.studio).order_by("label")
            ).all()
        elif filter_type == "collection":
            rows = session.execute(text(
                "SELECT item.value AS label, count(*)::int AS frequency FROM media "
                "CROSS JOIN LATERAL jsonb_array_elements_text(collections) AS item(value) "
                "WHERE kind=:kind AND library=:library AND available=true "
                "GROUP BY item.value ORDER BY item.value"
            ), {"kind": kind, "library": library}).all()
        else:
            raise HTTPException(400, "Unknown filter type")
        frequencies = {row.label: int(row.frequency) for row in rows}
        if filter_type == "genre":
            configured = config.get("catalog", {}).get("genre_collections", [])
            for collection in configured:
                exists = session.scalar(select(Media.id).where(
                    Media.kind == kind, Media.library == library,
                    Media.available.is_(True),
                    Media.collections.contains([collection]),
                ).limit(1))
                if exists:
                    frequencies.setdefault(collection, 1)
        return tuple(sorted(frequencies.items()))


@app.post("/api/rooms")
def create_room(request: RoomRequest, http_request: Request):
    if request.mode not in ("random", "filter", "genre_match"):
        raise HTTPException(400, "Unknown room mode")
    if request.mode == "filter" and (not request.filter_type or not request.filter_value):
        raise HTTPException(400, "Choose a filter")
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    code = "".join(random.choices(alphabet, k=6))
    with connect() as session:
        context = authenticated_context(http_request, session, required=auth_required())
        household = session.get(Household, context.household_id) if context else current_household(session)
        settings = household_settings(session, household.id)
        kind_enabled = settings.movies_enabled if request.kind == "movie" else settings.shows_enabled
        mode_enabled = {
            "random": settings.random_enabled,
            "filter": settings.filter_enabled,
            "genre_match": settings.genre_match_enabled,
        }[request.mode]
        if request.kind not in ("movie", "show") or not kind_enabled:
            raise HTTPException(403, "That media type is disabled by the household administrator")
        if not mode_enabled:
            raise HTTPException(403, "That search type is disabled by the household administrator")
        selectable = room_library_options(session, request.kind)
        permitted = {row["library"] for row in selectable}
        configured = list(system_settings(session).selectable_libraries or [])
        library = request.library or config.get("catalog", {}).get("default_libraries", {}).get(request.kind)
        if configured and not request.library:
            raise HTTPException(400, "Choose a Plex library for this room")
        if library not in permitted:
            raise HTTPException(400, "That Plex library is not selectable for rooms")
        phase = "genres" if request.mode == "genre_match" else "movies"
        join_token = secrets.token_urlsafe(32)
        session.add(Room(code=code, household_id=household.id,
                         creator_user_id=context.user_id if context else None,
                         join_token_hash=token_hash(join_token),
                         kind=request.kind, library=library, mode=request.mode,
                         phase=phase, filter_type=request.filter_type,
                         filter_value=request.filter_value, allow_undo=settings.undo_enabled,
                         offer_another_match=settings.another_match_enabled,
                         card_limit=settings.finite_card_limit if settings.finite_sessions_enabled else None))
        session.flush()
        if context:
            if settings.reset_room_choices:
                session.execute(delete(RoomPreference).where(
                    RoomPreference.user_id == context.user_id
                ))
            else:
                session.execute(insert(RoomPreference).values(
                    user_id=context.user_id, household_id=household.id,
                    kind=request.kind, mode=request.mode,
                    filter_type=request.filter_type if request.mode == "filter" else None,
                    filter_value=request.filter_value if request.mode == "filter" else None,
                ).on_conflict_do_update(
                    index_elements=[RoomPreference.user_id],
                    set_={"household_id": household.id, "kind": request.kind,
                          "mode": request.mode,
                          "filter_type": request.filter_type if request.mode == "filter" else None,
                          "filter_value": request.filter_value if request.mode == "filter" else None,
                          "updated_at": func.now()},
                ))
        if phase == "movies":
            populate_room_pool(session, code, request.kind, library,
                               request.filter_type, request.filter_value)
            session.flush()
            pool_count = session.scalar(
                select(func.count()).select_from(RoomMedia).where(RoomMedia.room_code == code)
            )
            if pool_count == 0:
                raise HTTPException(400, "No titles matched that filter")
    state = room_state(code)
    logger.info("Room created", extra={"event": "room_created", "room_code": code})
    state["join_url"] = f"{config['app']['public_url'].rstrip('/')}/join/{code}?invite={join_token}"
    return state


@app.get("/api/rooms/{code}")
def get_room(code: str):
    return room_state(code.upper())


@app.post("/api/rooms/{code}/join")
async def join(code: str, request: JoinRequest):
    code = code.upper()
    with connect() as session:
        room = session.get(Room, code)
        count = session.scalar(
            select(func.count()).select_from(Participant).where(Participant.room_code == code)
        )
        if not room:
            raise HTTPException(404, "Room not found")
        if room.status != "open" or count >= 2:
            raise HTTPException(409, "Room is full or finished")
        participant_id = str(uuid.uuid4())
        session.add(Participant(id=participant_id, room_code=code,
                                name=request.name.strip() or f"Player {count + 1}"))
    await broadcast(code)
    logger.info("Participant joined room", extra={
        "event": "participant_joined", "room_code": code,
    })
    return {"participant_id": participant_id, "room": room_state(code)}


@app.get("/api/rooms/{code}/cards")
def cards(code: str, participant_id: str):
    code = code.upper()
    with connect() as session:
        room = session.get(Room, code)
        person = session.get(Participant, participant_id)
        if not room or not person or person.room_code != code:
            raise HTTPException(404, "Room or participant not found")
        participant_count = session.scalar(
            select(func.count()).select_from(Participant).where(Participant.room_code == code)
        )
        if participant_count < 2:
            raise HTTPException(409, "Waiting for the other player")
        if room.phase not in ("movies", "second_chance"):
            raise HTTPException(409, "Match on a genre first")
        swiped = select(Swipe.media_id).where(Swipe.participant_id == participant_id)
        if room.phase == "second_chance":
            query = (
                select(Media).join(SecondChanceMedia, SecondChanceMedia.media_id == Media.id)
                .where(SecondChanceMedia.room_code == code,
                       SecondChanceMedia.participant_id == participant_id,
                       SecondChanceMedia.direction.is_(None),
                       Media.available.is_(True))
            )
        else:
            query = (
                select(Media).join(RoomMedia, RoomMedia.media_id == Media.id)
                .where(RoomMedia.room_code == code, Media.kind == room.kind,
                       Media.available.is_(True), Media.id.not_in(swiped))
            )
        if room.library:
            query = query.where(Media.library == room.library)
        # Everyone votes on the same room pool, but each participant gets a
        # stable personal shuffle. Reloading does not reshuffle their stack,
        # while two devices no longer see an identical sequence.
        participant_order = func.md5(func.concat(participant_id, ":", Media.id))
        rows = session.scalars(query.order_by(participant_order)).all()
        if room.phase == "second_chance":
            promoted_ids = set(session.scalars(select(SecondChanceMedia.media_id).where(
                SecondChanceMedia.room_code == code,
                SecondChanceMedia.participant_id == participant_id,
                SecondChanceMedia.direction.is_(None),
                SecondChanceMedia.source_top_choice.is_(True),
            )))
        else:
            promoted_ids = set(session.scalars(
                select(Swipe.media_id).where(
                    Swipe.room_code == code,
                    Swipe.participant_id != participant_id,
                    Swipe.direction == "top",
                    Swipe.media_id.not_in(swiped),
                )
            ))
        promoted = [row for row in rows if row.id in promoted_ids]
        ordered = [row for row in rows if row.id not in promoted_ids]
        for offset, row in enumerate(promoted):
            position = offset if room.phase == "second_chance" else 2 + offset * 3
            ordered.insert(min(position, len(ordered)), row)
        result = []
        for row in ordered:
            payload = serialize_media(row)
            payload["top_choice"] = row.id in promoted_ids
            if room.phase == "second_chance":
                payload["second_chance"] = True
                payload["second_chance_prompt"] = (
                    "The other person chose this" if participant_count == 2
                    else "At least 1 other person chose this"
                )
            result.append(payload)
        return result


@app.get("/api/rooms/{code}/top-choice-status")
def get_top_choice_status(code: str, participant_id: str):
    code = code.upper()
    with connect() as session:
        room = session.get(Room, code)
        person = session.get(Participant, participant_id)
        if not room or not person or person.room_code != code:
            raise HTTPException(404, "Room or participant not found")
        return top_choice_status(session, code, participant_id)


@app.get("/api/rooms/{code}/genres")
def genres(code: str, participant_id: str):
    code = code.upper()
    with connect() as session:
        room = session.get(Room, code)
        person = session.get(Participant, participant_id)
        people = session.scalar(
            select(func.count()).select_from(Participant).where(Participant.room_code == code)
        )
        if not room or not person or person.room_code != code:
            raise HTTPException(404, "Room or participant not found")
        if people < 2:
            raise HTTPException(409, "Waiting for the other player")
        if room.phase != "genres":
            return []
        values = list(get_filter_candidates(room.kind, room.library, "genre"))
        swiped = set(session.scalars(
            select(Swipe.media_id).where(
                Swipe.participant_id == participant_id, Swipe.media_id.like("genre:%")
            )
        ))
    values = [value for value in values if f"genre:{value}" not in swiped]
    random.shuffle(values)
    return values


@app.post("/api/rooms/{code}/swipe")
async def swipe(code: str, request: SwipeRequest):
    code = code.upper()
    if request.direction not in ("left", "right", "top"):
        raise HTTPException(400, "Direction must be left, right, or top")
    with connect() as session:
        room = session.get(Room, code)
        person = session.get(Participant, request.participant_id)
        if not room or not person or person.room_code != code:
            raise HTTPException(404, "Room or participant not found")
        if room.status != "open":
            return room_state(code)
        if request.direction == "top":
            if room.phase != "movies" or request.media_id.startswith("genre:"):
                raise HTTPException(400, "Top Choice is only available for titles")
            status = top_choice_status(session, code, request.participant_id)
            previous = session.get(Swipe, (request.participant_id, request.media_id))
            if status["remaining"] <= 0 and (not previous or previous.direction != "top"):
                raise HTTPException(409, "No Top Choices remaining")
        review = None
        if room.phase == "second_chance":
            if request.direction == "top":
                raise HTTPException(400, "Top Choice is unavailable during Second Chance")
            review = session.get(SecondChanceMedia, (request.participant_id, request.media_id))
            if not review or review.room_code != code or review.direction is not None:
                raise HTTPException(409, "That title is not awaiting a Second Chance vote")
        statement = insert(Swipe).values(
            participant_id=request.participant_id, room_code=code,
            media_id=request.media_id, direction=request.direction,
        ).on_conflict_do_update(
            index_elements=[Swipe.participant_id, Swipe.media_id],
            set_={"direction": request.direction, "created_at": func.now()},
        )
        session.execute(statement)
        if review:
            review.direction = request.direction
        if request.direction in ("right", "top"):
            mutual = session.scalar(
                select(func.count(func.distinct(Swipe.participant_id))).where(
                    Swipe.room_code == code, Swipe.media_id == request.media_id,
                    Swipe.direction.in_(("right", "top")),
                )
            )
            people = session.scalar(
                select(func.count()).select_from(Participant).where(Participant.room_code == code)
            )
            if people == 2 and mutual == 2:
                if request.media_id.startswith("genre:") and room.phase == "genres":
                    genre = request.media_id.removeprefix("genre:")
                    room.phase, room.filter_type, room.filter_value = "movies", "genre", genre
                    populate_room_pool(session, code, room.kind, room.library, "genre", genre)
                elif room.phase in ("movies", "second_chance"):
                    room.status, room.matched_media_id = "matched", request.media_id
                    session.add(Match(household_id=room.household_id, room_code=code,
                                      media_id=request.media_id))
                    logger.info("Room matched a title", extra={
                        "event": "room_matched", "room_code": code,
                    })
        mark_room_exhausted_if_complete(session, room)
    await broadcast(code)
    return room_state(code)


@app.post("/api/rooms/{code}/undo")
async def undo_swipe(code: str, request: ParticipantRequest):
    code = code.upper()
    with connect() as session:
        room = session.get(Room, code)
        person = session.get(Participant, request.participant_id)
        if not room or not person or person.room_code != code:
            raise HTTPException(404, "Room or participant not found")
        if not room.allow_undo:
            raise HTTPException(403, "Undo is disabled for this room")
        if room.status != "open" or room.phase != "movies":
            raise HTTPException(409, "A swipe cannot be undone right now")
        latest = session.scalar(
            select(Swipe).where(
                Swipe.room_code == code,
                Swipe.participant_id == request.participant_id,
                Swipe.media_id.not_like("genre:%"),
            ).order_by(Swipe.created_at.desc()).limit(1)
        )
        if not latest:
            raise HTTPException(409, "There is no swipe to undo")
        undone_media_id = latest.media_id
        session.delete(latest)
    await broadcast(code)
    state = room_state(code)
    state["undone_media_id"] = undone_media_id
    return state


@app.post("/api/rooms/{code}/another-match")
async def another_match(code: str):
    code = code.upper()
    with connect() as session:
        room = session.get(Room, code)
        if not room:
            raise HTTPException(404, "Room not found")
        if not room.offer_another_match:
            raise HTTPException(403, "Another match is disabled for this room")
        if room.status != "matched" or not room.matched_media_id:
            raise HTTPException(409, "This room does not have a match to continue from")
        matched_media_id = room.matched_media_id
        session.execute(delete(Swipe).where(
            Swipe.room_code == code, Swipe.media_id == matched_media_id,
        ))
        session.execute(delete(RoomMedia).where(
            RoomMedia.room_code == code, RoomMedia.media_id == matched_media_id,
        ))
        session.execute(delete(SecondChanceMedia).where(
            SecondChanceMedia.room_code == code,
            SecondChanceMedia.media_id == matched_media_id,
        ))
        room.matched_media_id = None
        if room.phase == "second_chance":
            remaining = session.scalar(
                select(func.count()).select_from(SecondChanceMedia).where(
                    SecondChanceMedia.room_code == code,
                    SecondChanceMedia.direction.is_(None),
                )
            ) or 0
        else:
            remaining = session.scalar(
                select(func.count()).select_from(RoomMedia).where(RoomMedia.room_code == code)
            ) or 0
        room.status = "open" if remaining else "exhausted"
    await broadcast(code)
    return room_state(code)


@app.websocket("/ws/rooms/{code}")
async def room_socket(ws: WebSocket, code: str):
    code = code.upper()
    await ws.accept()
    connections.setdefault(code, set()).add(ws)
    await ws.send_json({"type": "room", "data": room_state(code)})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        connections[code].discard(ws)


@app.get("/api/media/{media_id}/poster")
def poster(media_id: str):
    from fastapi.responses import Response
    from plexapi.server import PlexServer
    with connect() as session:
        media = session.get(Media, media_id)
        thumb = media.thumb if media else None
    if not thumb or not config["plex"].get("token"):
        raise HTTPException(404)
    plex = PlexServer(config["plex"]["url"], config["plex"]["token"])
    return Response(plex._session.get(plex.url(thumb, includeToken=True)).content, media_type="image/jpeg")


DIST = Path("frontend/dist")
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        requested = (DIST / path).resolve()
        if requested.is_relative_to(DIST.resolve()) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(DIST / "index.html")
