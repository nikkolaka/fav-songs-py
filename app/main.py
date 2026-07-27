"""HTTP surface: OAuth, session cookies, and one state endpoint that drives the UI."""

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import discovery as discovery_mod
from .config import MAX_USERS, OAUTH_STATE_TTL_SECONDS, SESSION_TTL_SECONDS, AppConfig
from .db import Database, now_millis, now_seconds
from .spotify import SpotifyAuthError, SpotifyService
from .tracker import TrackerManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("favsongs")

SESSION_COOKIE = "favsongs_session"
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

config = AppConfig.from_env()
database = Database(config.db_path, config.fernet_key, config.default_playlist_name)
spotify_service = SpotifyService(config, database)
trackers = TrackerManager(database, spotify_service)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.prune_plays()
    await trackers.start_all()
    yield
    await trackers.stop_all()
    database.close()


app = FastAPI(
    title="FavSongs", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


# ------------------------------------------------------------------ session


def current_user_id(
    favsongs_session: Optional[str] = Cookie(default=None),
) -> int:
    user_id = database.session_user_id(favsongs_session) if favsongs_session else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Log in with Spotify first")
    return user_id


def optional_user_id(favsongs_session: Optional[str] = Cookie(default=None)) -> Optional[int]:
    return database.session_user_id(favsongs_session) if favsongs_session else None


def set_session_cookie(response: Any, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


# ------------------------------------------------------------------- models


class SettingsUpdate(BaseModel):
    favorite_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    min_completion_ratio: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    poll_interval: Optional[int] = Field(default=None, ge=10, le=300)
    playlist_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    playlist_public: Optional[bool] = None
    auto_add_enabled: Optional[bool] = None
    discovery_enabled: Optional[bool] = None


class TrackSelection(BaseModel):
    track_ids: list[str] = Field(min_length=1, max_length=100)


class DiscoverySourceInput(BaseModel):
    playlist: str = Field(min_length=1, max_length=200)
    label: str = Field(default="Discover Weekly", min_length=1, max_length=60)


class ContextLabel(BaseModel):
    label: str = Field(min_length=1, max_length=60)


# -------------------------------------------------------------------- state


def build_state(user_id: Optional[int]) -> dict[str, Any]:
    if user_id is None:
        return {"connected": False}

    user = database.user(user_id)
    if not user:
        return {"connected": False}

    tracker = trackers.get(user_id)
    settings = database.settings(user_id)
    threshold = int(settings["favorite_threshold"])
    counts = database.play_counts(user_id)
    membership = tracker.favorites_membership

    rows: dict[str, dict[str, Any]] = {}
    for track_id, row in counts.items():
        if int(row["occurrences"]) < threshold and track_id not in membership:
            continue
        rows[track_id] = {**row, "in_playlist": track_id in membership}

    # Tracks somebody added to the playlist by hand still belong in the list.
    for entry in tracker.favorites_snapshot:
        track_id = entry["track_id"]
        if track_id in rows:
            continue
        rows[track_id] = {
            "track_id": track_id,
            "name": entry["name"],
            "artist": entry["artist"],
            "occurrences": int(counts.get(track_id, {}).get("occurrences", 0)),
            "last_played": int(counts.get(track_id, {}).get("last_played", 0)),
            "in_playlist": True,
        }

    favorites = sorted(
        rows.values(),
        key=lambda item: (
            0 if item["in_playlist"] else 1,
            -int(item["occurrences"]),
            -int(item["last_played"]),
            str(item["artist"]).lower(),
        ),
    )

    month = discovery_mod.month_key()
    months = [
        {**row, "name": discovery_mod.month_playlist_name(row["month"])}
        for row in database.discovery_months(user_id)
    ]

    return {
        "connected": True,
        "user": {
            "display_name": user["display_name"],
            "spotify_user_id": user["spotify_user_id"],
        },
        "tracker_running": trackers.is_running(user_id),
        "last_error": tracker.last_error,
        "now_playing": tracker.now_playing,
        "settings": settings,
        "stats": {
            "plays_24h": database.count_plays_since(user_id, now_millis() - 86_400_000),
            "tracked_tracks": len(counts),
            "next_favorite": database.next_favorite_candidate(user_id, threshold),
        },
        "favorites": favorites,
        "discovery": {
            "month": month,
            "month_name": discovery_mod.month_playlist_name(month),
            "tracks": database.discovery_month(user_id, month),
            "months": months,
            "sources": database.discovery_sources(user_id),
            "unlabelled": database.unlabelled_contexts(user_id),
        },
    }


# ------------------------------------------------------------------- routes


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/app.js")
async def script() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "app.js"), media_type="text/javascript")


@app.get("/pico.min.css")
async def pico() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "pico.min.css"), media_type="text/css")


@app.get("/overrides.css")
async def overrides() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "overrides.css"), media_type="text/css")


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/api/state")
async def api_state(user_id: Optional[int] = Depends(optional_user_id)) -> dict[str, Any]:
    # Deliberately 200 even when logged out: the front-end polls this from the login
    # page, and a stream of 401s would trip the fail2ban caddy-auth jail.
    return build_state(user_id)


@app.post("/api/auth/start")
async def auth_start() -> dict[str, str]:
    state = secrets.token_urlsafe(32)
    database.save_oauth_state(state, now_seconds() + OAUTH_STATE_TTL_SECONDS)
    return {"auth_url": spotify_service.auth_url(state)}


@app.get("/api/auth/callback")
async def auth_callback(
    code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None
) -> RedirectResponse:
    if error:
        return RedirectResponse(url=f"/?login={error}", status_code=303)
    if not code or not state or not database.consume_oauth_state(state):
        return RedirectResponse(url="/?login=invalid_state", status_code=303)

    try:
        result = await asyncio.to_thread(spotify_service.exchange_code, code)
    except Exception as exc:
        log.warning("OAuth exchange failed: %s", exc)
        return RedirectResponse(url="/?login=exchange_failed", status_code=303)

    known = database.user_exists(result["spotify_user_id"])
    if not known and database.user_count() >= MAX_USERS:
        # Spotify enforces its own allowlist; this just gives a comprehensible message.
        return RedirectResponse(url="/?login=full", status_code=303)

    user_id = database.upsert_user(result["spotify_user_id"], result["display_name"])
    database.save_tokens(
        user_id, result["access_token"], result["refresh_token"], result["expires_at"]
    )

    if not known:
        # Start counting from now rather than replaying the 50 plays already in Spotify's
        # history, so signing in never triggers a surprise batch of playlist additions.
        database.set_cursor_after(user_id, now_millis())

    token = secrets.token_urlsafe(32)
    database.create_session(token, user_id, now_seconds() + SESSION_TTL_SECONDS)
    await trackers.start(user_id)

    response = RedirectResponse(url="/", status_code=303)
    set_session_cookie(response, token)
    return response


@app.post("/api/auth/logout")
async def auth_logout(favsongs_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    if favsongs_session:
        database.delete_session(favsongs_session)
    response = JSONResponse({"connected": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/api/auth/disconnect")
async def auth_disconnect(
    user_id: int = Depends(current_user_id),
    favsongs_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Forget this account entirely -- tokens, counts, archive."""
    await trackers.stop(user_id)
    trackers.trackers.pop(user_id, None)
    database.delete_user(user_id)
    if favsongs_session:
        database.delete_session(favsongs_session)
    response = JSONResponse({"connected": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/api/settings")
async def update_settings(
    payload: SettingsUpdate, user_id: int = Depends(current_user_id)
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    before = database.settings(user_id)

    if "playlist_name" in updates and updates["playlist_name"] != before["playlist_name"]:
        # Point at whatever playlist now carries that name, creating it on next write.
        updates["favorites_playlist_id"] = None

    settings = database.update_settings(user_id, updates)

    lowered = int(settings["favorite_threshold"]) < int(before["favorite_threshold"])
    enabled = settings["auto_add_enabled"] and not before["auto_add_enabled"]
    if lowered or enabled:
        try:
            await asyncio.to_thread(trackers.get(user_id).reconcile_favorites)
        except Exception as exc:
            log.warning("Reconcile after settings change failed: %s", exc)

    return {"settings": settings}


@app.post("/api/tracker/start")
async def tracker_start(user_id: int = Depends(current_user_id)) -> dict[str, bool]:
    await trackers.start(user_id)
    return {"running": True}


@app.post("/api/tracker/stop")
async def tracker_stop(user_id: int = Depends(current_user_id)) -> dict[str, bool]:
    await trackers.stop(user_id)
    return {"running": False}


@app.post("/api/favorites/add")
async def favorites_add(
    payload: TrackSelection, user_id: int = Depends(current_user_id)
) -> dict[str, int]:
    tracker = trackers.get(user_id)

    def work() -> int:
        client = spotify_service.client(user_id)
        added = tracker.add_to_favorites(client, payload.track_ids)
        tracker.refresh_favorites(client)
        return added

    added = await asyncio.to_thread(work)
    if not added:
        raise HTTPException(status_code=400, detail="Already in the playlist")
    return {"added": added}


@app.post("/api/favorites/remove")
async def favorites_remove(
    payload: TrackSelection, user_id: int = Depends(current_user_id)
) -> dict[str, int]:
    tracker = trackers.get(user_id)

    def work() -> int:
        client = spotify_service.client(user_id)
        removed = tracker.remove_from_favorites(client, payload.track_ids)
        tracker.refresh_favorites(client)
        return removed

    removed = await asyncio.to_thread(work)
    if not removed:
        raise HTTPException(status_code=400, detail="None of those are in the playlist")
    return {"removed": removed}


@app.post("/api/discovery/sources")
async def add_discovery_source(
    payload: DiscoverySourceInput, user_id: int = Depends(current_user_id)
) -> dict[str, Any]:
    playlist_id = discovery_mod.playlist_id_from_link(payload.playlist)
    if not playlist_id:
        raise HTTPException(
            status_code=400, detail="That doesn't look like a Spotify playlist link or id"
        )
    database.add_discovery_source(user_id, playlist_id, payload.label.strip())
    return {"sources": database.discovery_sources(user_id)}


@app.delete("/api/discovery/sources/{playlist_id}")
async def remove_discovery_source(
    playlist_id: str, user_id: int = Depends(current_user_id)
) -> dict[str, Any]:
    database.remove_discovery_source(user_id, playlist_id)
    return {"sources": database.discovery_sources(user_id)}


@app.post("/api/discovery/contexts/{playlist_id}")
async def label_context(
    playlist_id: str, payload: ContextLabel, user_id: int = Depends(current_user_id)
) -> dict[str, Any]:
    """Promote a playlist we've seen in playback but can't name into a discovery source."""
    database.add_discovery_source(user_id, playlist_id, payload.label.strip())
    return {"sources": database.discovery_sources(user_id)}


@app.delete("/api/discovery/contexts/{playlist_id}")
async def dismiss_context(
    playlist_id: str, user_id: int = Depends(current_user_id)
) -> dict[str, Any]:
    database.dismiss_context(user_id, playlist_id)
    return {"unlabelled": database.unlabelled_contexts(user_id)}


@app.exception_handler(SpotifyAuthError)
async def spotify_auth_handler(_: Any, __: SpotifyAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "Spotify access was revoked. Log in again."},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Any, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
