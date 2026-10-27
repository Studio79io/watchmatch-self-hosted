from plexapi.server import PlexServer
import re
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from .database import connect
from .models import Media, RoomMedia


MOCK_MEDIA = [
    dict(id="mock-1", title="Arrival", year=2016, kind="movie", summary="A linguist works to communicate with mysterious visitors.", genres=["Drama", "Science Fiction"], duration=6960, rating="PG-13", thumb=None, library="Movies", watched=False),
    dict(id="mock-2", title="Knives Out", year=2019, kind="movie", summary="A detective investigates a wonderfully dysfunctional family.", genres=["Mystery", "Comedy"], duration=7860, rating="PG-13", thumb=None, library="Movies", watched=False),
    dict(id="mock-3", title="The Grand Budapest Hotel", year=2014, kind="movie", summary="A concierge and his lobby boy become unlikely friends.", genres=["Comedy", "Adventure"], duration=6000, rating="R", thumb=None, library="Movies", watched=False),
    dict(id="mock-4", title="Severance", year=2022, kind="show", summary="Office workers undergo a procedure separating work and home memories.", genres=["Drama", "Mystery"], duration=0, rating="TV-MA", thumb=None, library="TV Shows", watched=False),
    dict(id="mock-5", title="The Bear", year=2022, kind="show", summary="A chef returns home to run his family's sandwich shop.", genres=["Drama", "Comedy"], duration=0, rating="TV-MA", thumb=None, library="TV Shows", watched=False),
]

YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
EXPLICIT_YEAR_PATTERN = re.compile(r"[\[(]((?:19|20)\d{2})[\])]" )


def extract_release_years(files, title):
    """Extract likely release years without mistaking title numbers for years."""
    title_years = {int(value) for value in YEAR_PATTERN.findall(title or "")}
    detected = set()
    for raw_path in files:
        parts = [part for part in raw_path.replace("\\", "/").split("/") if part]
        filename = parts[-1] if parts else ""
        parent = parts[-2] if len(parts) > 1 else ""
        # Explicit Movie Name (2020) / Movie Name [2020] conventions are the
        # strongest signal and override other year-like numbers in the title.
        explicit = EXPLICIT_YEAR_PATTERN.findall(parent) or EXPLICIT_YEAR_PATTERN.findall(filename)
        if explicit:
            detected.update(int(value) for value in explicit)
            continue
        parent_years = {int(value) for value in YEAR_PATTERN.findall(parent)} - title_years
        if parent_years:
            detected.update(parent_years)
            continue
        detected.update({int(value) for value in YEAR_PATTERN.findall(filename)} - title_years)
    return sorted(detected)


def upsert_media(session, rows):
    if not rows:
        return
    # PostgreSQL caps one statement at 65,535 bind parameters. A full Plex
    # catalog easily exceeds that once each title carries rich metadata.
    for offset in range(0, len(rows), 1000):
        _upsert_media_batch(session, rows[offset:offset + 1000])


def _upsert_media_batch(session, rows):
    rows = [{**row, "catalog_provider": row.get("catalog_provider", "plex"),
             "provider_external_id": row.get("provider_external_id", row["id"]),
             "available": True, "unavailable_at": None} for row in rows]
    statement = insert(Media).values(rows)
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[Media.id],
        set_={
            "catalog_provider": excluded.catalog_provider,
            "provider_external_id": excluded.provider_external_id,
            "title": excluded.title, "year": excluded.year, "kind": excluded.kind,
            "summary": excluded.summary, "genres": excluded.genres,
            "collections": excluded.collections,
            "file_count": excluded.file_count, "path_years": excluded.path_years,
            "version_count": excluded.version_count,
            "edition_title": excluded.edition_title,
            "unwatched_count": excluded.unwatched_count,
            "duration": excluded.duration, "rating": excluded.rating,
            "thumb": excluded.thumb, "library": excluded.library,
            "watched": excluded.watched, "added_at": excluded.added_at,
            "released_at": excluded.released_at,
            "available": True, "unavailable_at": None,
            "directors": excluded.directors, "actors": excluded.actors,
            "studio": excluded.studio,
            "updated_at": func.now(),
        },
    )
    session.execute(statement)


def seed_mock():
    with connect() as session:
        upsert_media(session, MOCK_MEDIA)


def reconcile_unavailable(session, synced_libraries, incoming_ids):
    if not synced_libraries:
        return set()
    missing = set(session.scalars(select(Media.id).where(
        Media.library.in_(synced_libraries), Media.available.is_(True),
        Media.id.not_in(incoming_ids),
    )))
    if not missing:
        return missing
    session.execute(update(Media).where(Media.id.in_(missing)).values(
        available=False, unavailable_at=func.now(), updated_at=func.now(),
    ))
    # Active rooms stop offering deleted titles. Historical matches keep the
    # soft-deleted metadata so their result screen remains meaningful.
    session.execute(delete(RoomMedia).where(RoomMedia.media_id.in_(missing)))
    return missing


def exclude_libraries(session, library_names):
    """Soft-hide previously synchronized libraries that are now excluded."""
    if not library_names:
        return set()
    excluded_ids = set(session.scalars(select(Media.id).where(
        Media.library.in_(library_names), Media.available.is_(True),
    )))
    if excluded_ids:
        session.execute(update(Media).where(Media.id.in_(excluded_ids)).values(
            available=False, unavailable_at=func.now(), updated_at=func.now(),
        ))
        session.execute(delete(RoomMedia).where(RoomMedia.media_id.in_(excluded_ids)))
    return excluded_ids


def sync_plex(config):
    plex_cfg = config["plex"]
    if not plex_cfg.get("token"):
        if config["app"].get("mock_catalog_when_disconnected", True):
            seed_mock()
            return {"mode": "demo", "count": len(MOCK_MEDIA),
                    "added": 0, "updated": len(MOCK_MEDIA), "removed": 0}
        raise RuntimeError("PLEX_TOKEN is not configured")
    plex = PlexServer(plex_cfg["url"], plex_cfg["token"])
    allowed = set(config["catalog"].get("libraries") or [])
    excluded = set(config["catalog"].get("excluded_libraries") or [])
    rows = []
    synced_libraries = set()
    for section in plex.library.sections():
        if section.title in excluded or (allowed and section.title not in allowed):
            continue
        if section.type not in ("movie", "show"):
            continue
        synced_libraries.add(section.title)
        for item in section.all():
            media_versions = getattr(item, "media", [])
            files = [part.file for media in media_versions
                     for part in getattr(media, "parts", []) if getattr(part, "file", None)]
            path_years = extract_release_years(files, item.title)
            rows.append({
                "id": str(item.ratingKey), "catalog_provider": "plex",
                "provider_external_id": str(item.ratingKey), "title": item.title,
                "year": getattr(item, "year", None), "kind": section.type,
                "summary": getattr(item, "summary", ""),
                "genres": [g.tag for g in getattr(item, "genres", [])],
                "collections": [c.tag for c in getattr(item, "collections", [])],
                "file_count": len(files), "path_years": path_years,
                "version_count": len(media_versions),
                "edition_title": getattr(item, "editionTitle", None),
                "unwatched_count": max(0, int(getattr(item, "leafCount", 0) or 0)
                                         - int(getattr(item, "viewedLeafCount", 0) or 0))
                if section.type == "show" else 0,
                "duration": (getattr(item, "duration", None) or 0) // 1000,
                "rating": getattr(item, "contentRating", None),
                "thumb": getattr(item, "thumb", None), "library": section.title,
                "watched": bool(getattr(item, "isWatched", False)),
                "added_at": getattr(item, "addedAt", None),
                "released_at": getattr(item, "originallyAvailableAt", None),
                "directors": [d.tag for d in getattr(item, "directors", [])],
                "actors": [r.tag for r in getattr(item, "roles", [])],
                "studio": getattr(item, "studio", None),
            })
    with connect() as session:
        existing = set(session.scalars(
            select(Media.id).where(Media.library.in_(synced_libraries))
        )) if synced_libraries else set()
        upsert_media(session, rows)
        removed = reconcile_unavailable(
            session, synced_libraries, {row["id"] for row in rows}
        )
        removed |= exclude_libraries(session, excluded)
    added = sum(row["id"] not in existing for row in rows)
    return {"mode": "plex", "count": len(rows), "added": added,
            "updated": len(rows) - added, "removed": len(removed)}
