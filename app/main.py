"""HTTP surface: OAuth, session cookies, and one state endpoint that drives the UI."""

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import discovery as discovery_mod
from . import demo as demo_mod
from .aiblocklist import AiBlocklist
from .config import MAX_USERS, OAUTH_STATE_TTL_SECONDS, SESSION_TTL_SECONDS, AppConfig
from .db import Database, now_millis, now_seconds
from .spotify import SpotifyAuthError, SpotifyService
from .tracker import TrackerManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("favsongs")

SESSION_COOKIE = "favsongs_session"
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")

# In production (Docker), the React build lives at this path.
# In dev, Vite dev server handles the frontend.
USE_REACT = os.path.isdir(FRONTEND_DIST)

config = AppConfig.from_env()
database = Database(config.db_path, config.fernet_key, config.default_playlist_name)
spotify_service = SpotifyService(config, database)
blocklist = AiBlocklist(
    os.path.join(os.path.dirname(config.db_path) or ".", "ai-artists.csv")
)
trackers = TrackerManager(database, spotify_service, blocklist)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.close_orphaned_listens()
    # Don't let a GitHub outage delay startup; the cached copy carries us until the
    # tracker's next sweep refreshes it.
    await asyncio.to_thread(blocklist.refresh)
    await trackers.start_all()
    yield
    await trackers.stop_all()
    database.close()


app = FastAPI(
    title="FavSongs", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


# --------------------------------------------------------- security middleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Simple in-memory rate limiter: allow at most `limit` requests per `window` seconds
# to paths with a given prefix. Shared across users, least-effort flood protection.
_rate_state: dict[str, list[float]] = {}
_RATE_WINDOW = 60
_RATE_LIMIT = 30


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        for prefix, limit, window in [("/api/auth/", _RATE_LIMIT, _RATE_WINDOW)]:
            if request.url.path.startswith(prefix):
                key = f"rate:{prefix}"
                now = time.time()
                _rate_state.setdefault(key, [])
                _rate_state[key] = [t for t in _rate_state[key] if now - t < window]
                if len(_rate_state[key]) >= limit:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many requests. Slow down."},
                    )
                _rate_state[key].append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware)


# ------------------------------------------------------------------ session


def current_user_id(
    favsongs_session: Optional[str] = Cookie(default=None),
) -> int:
    user_id = database.session_user_id(favsongs_session) if favsongs_session else None
    if user_id is None and config.dev_mode:
        # Dev mode allows local development without Spotify. Only active when
        # FAVSONGS_DEV_MODE is explicitly set — must never reach production.
        return 0
    if user_id is None:
        raise HTTPException(status_code=401, detail="Log in with Spotify first")
    return user_id


def optional_user_id(favsongs_session: Optional[str] = Cookie(default=None)) -> Optional[int]:
    user_id = database.session_user_id(favsongs_session) if favsongs_session else None
    if user_id is None and config.dev_mode:
        return 0
    return user_id


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
    # Now gates favourites as well as discovery, so it reaches lower than the 0.5 that
    # made sense when it only decided what got archived. There is no poll_interval here
    # on purpose: it is the measurement resolution, not a preference.
    min_completion_ratio: Optional[float] = Field(default=None, ge=0.25, le=1.0)
    playlist_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    playlist_public: Optional[bool] = None
    auto_add_enabled: Optional[bool] = None
    discovery_enabled: Optional[bool] = None
    pinned_stats: Optional[list[str]] = None


class TrackSelection(BaseModel):
    track_ids: list[str] = Field(min_length=1, max_length=100)


class DiscoverySourceInput(BaseModel):
    playlist: str = Field(min_length=1, max_length=200)
    label: str = Field(default="Discover Weekly", min_length=1, max_length=60)


class ContextLabel(BaseModel):
    label: str = Field(min_length=1, max_length=60)


class TogglePinRequest(BaseModel):
    stat_id: str = Field(min_length=1, max_length=64)


# -------------------------------------------------------------------- state


def build_state(user_id: Optional[int]) -> dict[str, Any]:
    if user_id is None:
        return {"connected": False}
    if user_id == 0:
        return demo_mod.demo_state()

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
        if int(row["qualified_plays"]) < threshold and track_id not in membership:
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
            "qualified_plays": int(counts.get(track_id, {}).get("qualified_plays", 0)),
            "total_plays": int(counts.get(track_id, {}).get("total_plays", 0)),
            "last_played": int(counts.get(track_id, {}).get("last_played", 0)),
            "in_playlist": True,
        }

    favorites = sorted(
        rows.values(),
        key=lambda item: (
            0 if item["in_playlist"] else 1,
            -int(item["qualified_plays"]),
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
            "last_24h": database.count_listens_since(user_id, now_millis() - 86_400_000),
            "tracked_tracks": len(counts),
            "next_favorite": database.next_favorite_candidate(user_id, threshold),
        },
        "favorites": favorites,
        "favorite_track_ids": list(tracker.favorites_membership),
        "discovery": {
            "month": month,
            "month_name": discovery_mod.month_playlist_name(month),
            "tracks": database.discovery_month(user_id, month),
            "blocked": database.blocked_month(user_id, month),
            "months": months,
            "sources": database.discovery_sources(user_id),
            "unlabelled": database.unlabelled_contexts(user_id),
            "last_sweep": tracker.last_sweep,
            "blocklist": blocklist.status(),
        },
    }


# ------------------------------------------------------------------- routes


if USE_REACT:
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/", response_class=FileResponse)
    async def index() -> FileResponse:
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(
            os.path.join(FRONTEND_DIST, "favicon.svg"),
            media_type="image/svg+xml",
        )

    @app.get("/icons.svg")
    async def icons() -> FileResponse:
        return FileResponse(
            os.path.join(FRONTEND_DIST, "icons.svg"),
            media_type="image/svg+xml",
        )
else:
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


@app.get("/api/history")
async def api_history(
    q: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    qualified: Optional[bool] = None,
    favorites_only: Optional[bool] = None,
    sort: str = "time",
    cursor: Optional[str] = None,
    limit: int = 50,
    user_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """One page of listening history, newest first.

    `start`/`end` are epoch milliseconds -- the browser converts the dates someone picks
    from its own timezone, so a day means their day rather than UTC's.
    """
    if user_id == 0:
        return await asyncio.to_thread(
            demo_mod.demo_history,
            query=q, start=start, end=end, qualified=qualified,
            cursor=None, limit=limit,
        )

    favorite_ids = None
    if favorites_only:
        tracker = trackers.get(user_id)
        favorite_ids = tracker.favorites_membership

    return await asyncio.to_thread(
        database.history,
        user_id,
        query=q,
        start=start,
        end=end,
        qualified=qualified,
        favorites_only=favorites_only,
        favorite_track_ids=favorite_ids,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@app.get("/api/history/summary")
async def api_history_summary(user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    if user_id == 0:
        return demo_mod.demo_history_summary()
    return await asyncio.to_thread(database.history_summary, user_id)


@app.get("/api/stats")
async def api_stats(user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    if user_id == 0:
        return {"stats": [], "pinned_stats": []}
    settings = database.settings(user_id)
    threshold = int(settings["favorite_threshold"])
    return {
        "stats": await asyncio.to_thread(database.get_all_stats, user_id, threshold),
        "pinned_stats": settings.get("pinned_stats", []),
    }


@app.post("/api/stats/toggle-pin")
async def api_stats_toggle_pin(
    payload: TogglePinRequest,
    user_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    settings = database.settings(user_id)
    pinned: list[str] = list(settings.get("pinned_stats", []))
    stat_id = payload.stat_id
    if stat_id in pinned:
        pinned.remove(stat_id)
    else:
        pinned.append(stat_id)
    database.update_settings(user_id, {"pinned_stats": pinned})
    return {"pinned_stats": pinned}


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

    # Nothing is replayed on sign-in: tracking starts from the first thing measured, so
    # logging in can never trigger a surprise batch of playlist additions.
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


@app.post("/api/discovery/sweep")
async def sweep_now(user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    """Read every source immediately instead of waiting for the next scheduled sweep."""
    if user_id == 0:
        # In dev mode, return a mock result to keep the demo flow working.
        return {"archived": 0, "blocked": 0, "errors": []}
    tracker = trackers.get(user_id)

    def work() -> dict[str, Any]:
        return tracker.sweep_sources(spotify_service.client(user_id))

    try:
        return await asyncio.to_thread(work)
    except Exception as exc:
        log.error("Sweep failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=502, detail="Sweep failed; check the logs.") from exc


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
