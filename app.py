import asyncio
import os
import secrets
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional

import requests
import spotipy
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

load_dotenv()

DEFAULT_PLAYLIST_NAME = "Favourite Songs - Whatsit"
DEFAULT_SCOPE = (
    "user-read-playback-state,"
    "user-read-currently-playing,"
    "playlist-modify-public,"
    "playlist-modify-private"
)
STATE_TTL_SECONDS = 600
PLAYLIST_CACHE_TTL_SECONDS = 300


def _now_seconds() -> int:
    return int(time.time())


def _now_millis() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class AppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    db_path: str
    default_playlist_name: str
    scope: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        missing = [
            name
            for name in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI")
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        data_dir = os.getenv("FAVSONGS_DATA_DIR", "data")
        db_path = os.getenv("FAVSONGS_DB_PATH", os.path.join(data_dir, "favsongs.db"))

        return cls(
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            redirect_uri=os.environ["REDIRECT_URI"],
            db_path=db_path,
            default_playlist_name=os.getenv(
                "DEFAULT_PLAYLIST_NAME", DEFAULT_PLAYLIST_NAME
            ),
            scope=os.getenv("SPOTIFY_SCOPE", DEFAULT_SCOPE),
        )


class Database:
    def __init__(self, db_path: str, default_playlist_name: str):
        db_dir = os.path.dirname(db_path) or "."
        os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = Lock()
        self.default_playlist_name = default_playlist_name.strip() or DEFAULT_PLAYLIST_NAME
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    favorite_threshold INTEGER NOT NULL DEFAULT 5,
                    min_completion_ratio REAL NOT NULL DEFAULT 0.8,
                    check_interval INTEGER NOT NULL DEFAULT 10,
                    min_play_gap_ms INTEGER NOT NULL DEFAULT 300000,
                    playlist_name TEXT NOT NULL DEFAULT 'Favourite Songs - Whatsit',
                    playlist_public INTEGER NOT NULL DEFAULT 1,
                    auto_add_enabled INTEGER NOT NULL DEFAULT 1,
                    playlist_id TEXT
                );

                CREATE TABLE IF NOT EXISTS plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    play_instance_id TEXT NOT NULL UNIQUE,
                    played_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_plays_played_at
                ON plays (played_at DESC);

                CREATE TABLE IF NOT EXISTS favorites (
                    track_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 0,
                    last_played INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self.conn.execute(
                """
                INSERT INTO settings (id, playlist_name)
                VALUES (1, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (self.default_playlist_name,),
            )
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _get_state(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def _set_state(self, key: str, value: Optional[str]) -> None:
        if value is None:
            self.conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
            return
        self.conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def account(self) -> Optional[dict[str, str]]:
        with self.lock:
            spotify_user_id = self._get_state("spotify_user_id")
            display_name = self._get_state("display_name")
        if not spotify_user_id:
            return None
        return {
            "spotify_user_id": spotify_user_id,
            "display_name": display_name or spotify_user_id,
        }

    def token_data(self) -> Optional[dict[str, Any]]:
        with self.lock:
            access_token = self._get_state("access_token")
            refresh_token = self._get_state("refresh_token")
            expires_at = self._get_state("expires_at")
        if not access_token or not refresh_token or not expires_at:
            return None
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(expires_at),
        }

    def is_connected(self) -> bool:
        return self.account() is not None and self.token_data() is not None

    def save_connection(
        self,
        spotify_user_id: str,
        display_name: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
    ) -> None:
        with self.lock:
            self._set_state("spotify_user_id", spotify_user_id)
            self._set_state("display_name", display_name)
            self._set_state("access_token", access_token)
            self._set_state("refresh_token", refresh_token)
            self._set_state("expires_at", str(expires_at))
            self.conn.commit()

    def update_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_at: int,
    ) -> None:
        with self.lock:
            self._set_state("access_token", access_token)
            self._set_state("refresh_token", refresh_token)
            self._set_state("expires_at", str(expires_at))
            self.conn.commit()

    def save_oauth_state(self, state: str, expires_at: int) -> None:
        with self.lock:
            self._set_state("oauth_state", state)
            self._set_state("oauth_state_expires_at", str(expires_at))
            self.conn.commit()

    def consume_oauth_state(self, state: str) -> bool:
        with self.lock:
            saved_state = self._get_state("oauth_state")
            expires_at = self._get_state("oauth_state_expires_at")
            self._set_state("oauth_state", None)
            self._set_state("oauth_state_expires_at", None)
            self.conn.commit()

        if not saved_state or not expires_at:
            return False
        if not secrets.compare_digest(saved_state, state):
            return False
        return int(expires_at) >= _now_seconds()

    def tracker_running(self) -> bool:
        with self.lock:
            return self._get_state("tracker_running") == "1"

    def set_tracker_running(self, running: bool) -> None:
        with self.lock:
            self._set_state("tracker_running", "1" if running else "0")
            self.conn.commit()

    def settings(self) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT favorite_threshold, min_completion_ratio, check_interval,
                       playlist_name, playlist_public, auto_add_enabled, playlist_id
                FROM settings
                WHERE id = 1
                """
            ).fetchone()
        if not row:
            raise RuntimeError("Could not load settings")
        data = dict(row)
        data["playlist_public"] = bool(data["playlist_public"])
        data["auto_add_enabled"] = bool(data["auto_add_enabled"])
        return data

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "favorite_threshold",
            "min_completion_ratio",
            "check_interval",
            "playlist_name",
            "playlist_public",
            "auto_add_enabled",
            "playlist_id",
        }
        parts: list[str] = []
        values: list[Any] = []

        for key, value in updates.items():
            if key not in allowed:
                continue
            if key in {"playlist_public", "auto_add_enabled"}:
                value = 1 if bool(value) else 0
            parts.append(f"{key} = ?")
            values.append(value)

        if not parts:
            return self.settings()

        values.append(1)
        with self.lock:
            self.conn.execute(
                f"UPDATE settings SET {', '.join(parts)} WHERE id = ?",
                tuple(values),
            )
            self.conn.commit()
        return self.settings()

    def record_completed_play(
        self,
        track_id: str,
        name: str,
        artist: str,
        played_at: int,
        play_instance_id: Optional[str],
        min_gap_ms: int,
    ) -> Optional[int]:
        play_id = play_instance_id or f"{track_id}:{played_at}"

        with self.lock:
            row = self.conn.execute(
                """
                SELECT occurrences, last_played
                FROM favorites
                WHERE track_id = ?
                """,
                (track_id,),
            ).fetchone()

            if row and (played_at - int(row["last_played"])) <= min_gap_ms:
                return None

            try:
                self.conn.execute(
                    """
                    INSERT INTO plays (track_id, name, artist, play_instance_id, played_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (track_id, name, artist, play_id, played_at),
                )
            except sqlite3.IntegrityError:
                self.conn.commit()
                return None

            if row:
                occurrences = int(row["occurrences"]) + 1
                self.conn.execute(
                    """
                    UPDATE favorites
                    SET name = ?, artist = ?, occurrences = ?, last_played = ?
                    WHERE track_id = ?
                    """,
                    (name, artist, occurrences, played_at, track_id),
                )
            else:
                occurrences = 1
                self.conn.execute(
                    """
                    INSERT INTO favorites (track_id, name, artist, occurrences, last_played)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (track_id, name, artist, occurrences, played_at),
                )

            self.conn.commit()
            return occurrences

    def count_plays_since(self, since_ms: int) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM plays WHERE played_at >= ?",
                (since_ms,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def max_occurrence(self) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(occurrences), 0) AS value FROM favorites"
            ).fetchone()
        return int(row["value"] if row else 0)

    def recent_plays(self, limit: int = 8) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT track_id, name, artist,
                       occurrences AS plays,
                       last_played AS played_at
                FROM favorites
                ORDER BY last_played DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def favorites(self, minimum_occurrences: int = 1, limit: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT track_id, name, artist, occurrences, last_played
                FROM favorites
                WHERE occurrences >= ?
                ORDER BY occurrences DESC, last_played DESC
                LIMIT ?
                """,
                (minimum_occurrences, limit),
            ).fetchall()
        return [dict(row) for row in rows]


class SpotifyService:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def auth_url(self, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": self.config.scope,
                "state": state,
                "show_dialog": "true",
            }
        )
        return f"https://accounts.spotify.com/authorize?{query}"

    def _token_request(self, payload: dict[str, str]) -> dict[str, Any]:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data=payload,
            auth=(self.config.client_id, self.config.client_secret),
            timeout=20,
        )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=400,
                detail=f"Spotify token request failed ({response.status_code}): {response.text}",
            )
        return response.json()

    def connect(self, code: str) -> None:
        token_data = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            }
        )

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = int(token_data.get("expires_in", 3600))

        if not access_token or not refresh_token:
            raise HTTPException(status_code=400, detail="Spotify OAuth response was incomplete")

        profile = spotipy.Spotify(auth=access_token, requests_timeout=20).me()
        spotify_user_id = profile.get("id")
        display_name = profile.get("display_name") or spotify_user_id

        if not spotify_user_id:
            raise HTTPException(status_code=400, detail="Spotify profile did not include a user id")

        self.db.save_connection(
            spotify_user_id=spotify_user_id,
            display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=_now_seconds() + expires_in,
        )

    def access_token(self) -> str:
        token_data = self.db.token_data()
        if not token_data:
            raise HTTPException(status_code=400, detail="Spotify is not connected")

        if int(token_data["expires_at"]) > (_now_seconds() + 60):
            return str(token_data["access_token"])

        refreshed = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": str(token_data["refresh_token"]),
            }
        )

        access_token = refreshed.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Spotify refresh response was incomplete")

        refresh_token = str(refreshed.get("refresh_token") or token_data["refresh_token"])
        expires_at = _now_seconds() + int(refreshed.get("expires_in", 3600))
        self.db.update_tokens(str(access_token), refresh_token, expires_at)
        return str(access_token)

    def client(self) -> spotipy.Spotify:
        return spotipy.Spotify(auth=self.access_token(), requests_timeout=20)


@dataclass
class RuntimeState:
    track_id: Optional[str] = None
    track_name: Optional[str] = None
    track_artist: Optional[str] = None
    progress_ms: int = 0
    duration_ms: int = 0
    timestamp_ms: int = 0
    play_instance_id: Optional[str] = None
    play_recorded: bool = False


class Tracker:
    def __init__(self, db: Database, spotify: SpotifyService):
        self.db = db
        self.spotify = spotify
        self.task: Optional[asyncio.Task] = None
        self.state = RuntimeState()
        self.playlist_cache: Optional[dict[str, Any]] = None

    async def startup(self) -> None:
        if self.db.tracker_running() and self.db.is_connected():
            await self.start()

    async def start(self) -> None:
        if not self.db.is_connected():
            raise HTTPException(status_code=400, detail="Connect Spotify first")
        if self.task and not self.task.done():
            self.db.set_tracker_running(True)
            return
        self.state = RuntimeState()
        self.db.set_tracker_running(True)
        self.task = asyncio.create_task(self._run_loop(), name="favsongs-tracker")

    async def stop(self) -> None:
        self.db.set_tracker_running(False)
        task = self.task
        self.task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        await self.stop()

    def _play_id(self, track_id: str, timestamp_ms: int, progress_ms: int) -> Optional[str]:
        if not track_id or not timestamp_ms:
            return None
        return f"{track_id}:{max(timestamp_ms - progress_ms, 0)}"

    def _dynamic_min_gap_ms(self, duration_ms: int) -> int:
        if duration_ms <= 0:
            return 1000
        return max(1000, min(duration_ms // 2, 300000))

    def _is_complete(self, progress_ms: int, duration_ms: int, ratio: float) -> bool:
        return duration_ms > 0 and (progress_ms / duration_ms) >= ratio

    def _playlist_id(self, settings: dict[str, Any], client: spotipy.Spotify) -> Optional[str]:
        saved = settings.get("playlist_id")
        if saved:
            return str(saved)

        profile = client.me()
        spotify_user_id = profile.get("id")
        if not spotify_user_id:
            return None

        name = str(settings.get("playlist_name") or self.db.default_playlist_name)
        public = bool(settings.get("playlist_public", True))

        results = client.user_playlists(spotify_user_id, limit=50)
        while results:
            for playlist in results.get("items", []):
                if playlist and playlist.get("name") == name and playlist.get("id"):
                    playlist_id = str(playlist["id"])
                    self.db.update_settings({"playlist_id": playlist_id})
                    return playlist_id
            if results.get("next"):
                results = client.next(results)
            else:
                break

        created = client.user_playlist_create(
            spotify_user_id,
            name,
            public=public,
            description="Auto-managed by FavSongs",
        )
        created_id = created.get("id")
        if created_id:
            playlist_id = str(created_id)
            self.db.update_settings({"playlist_id": playlist_id})
            return playlist_id
        return None

    def _playlist_tracks(self, playlist_id: str, client: spotipy.Spotify) -> set[str]:
        now = time.time()
        if (
            self.playlist_cache
            and self.playlist_cache.get("playlist_id") == playlist_id
            and (now - float(self.playlist_cache.get("cached_at", 0))) < PLAYLIST_CACHE_TTL_SECONDS
        ):
            return self.playlist_cache["track_ids"]

        track_ids: set[str] = set()
        results = client.playlist_tracks(playlist_id, limit=100)
        while results:
            for item in results.get("items", []):
                track = item.get("track") if item else None
                track_id = track.get("id") if track else None
                if track_id:
                    track_ids.add(track_id)
            if results.get("next"):
                results = client.next(results)
            else:
                break

        self.playlist_cache = {
            "playlist_id": playlist_id,
            "track_ids": track_ids,
            "cached_at": now,
        }
        return track_ids

    def add_track(self, track_id: str, allow_duplicate: bool = False) -> bool:
        settings = self.db.settings()
        client = self.spotify.client()
        playlist_id = self._playlist_id(settings, client)
        if not playlist_id:
            return False

        existing = self._playlist_tracks(playlist_id, client)
        if not allow_duplicate and track_id in existing:
            return False

        client.playlist_add_items(playlist_id, [track_id], position=0)
        existing.add(track_id)
        return True

    def _set_track(
        self,
        track_id: str,
        name: str,
        artist: str,
        progress_ms: int,
        duration_ms: int,
        timestamp_ms: int,
        play_instance_id: Optional[str],
    ) -> None:
        self.state.track_id = track_id
        self.state.track_name = name
        self.state.track_artist = artist
        self.state.progress_ms = progress_ms
        self.state.duration_ms = duration_ms
        self.state.timestamp_ms = timestamp_ms
        self.state.play_instance_id = play_instance_id
        self.state.play_recorded = False

    def _finalize_track(self) -> None:
        if self.state.play_recorded:
            return

        if not self.state.track_id or not self.state.track_name or not self.state.track_artist:
            return

        settings = self.db.settings()
        if not self._is_complete(
            self.state.progress_ms,
            self.state.duration_ms,
            float(settings["min_completion_ratio"]),
        ):
            return

        played_at = (self.state.timestamp_ms or _now_millis()) + max(
            self.state.duration_ms - self.state.progress_ms,
            0,
        )
        occurrences = self.db.record_completed_play(
            track_id=self.state.track_id,
            name=self.state.track_name,
            artist=self.state.track_artist,
            played_at=played_at,
            play_instance_id=self.state.play_instance_id,
            min_gap_ms=self._dynamic_min_gap_ms(self.state.duration_ms),
        )
        self.state.play_recorded = True

        if not occurrences:
            return

        if bool(settings["auto_add_enabled"]) and occurrences >= int(
            settings["favorite_threshold"]
        ):
            try:
                self.add_track(self.state.track_id, allow_duplicate=False)
            except Exception as exc:
                print(f"[WARN] Could not auto-add track: {exc}")

    def _tick(self) -> None:
        playback = self.spotify.client().current_playback()
        if not playback:
            return

        track = playback.get("item")
        if not track or not track.get("id"):
            return

        track_id = str(track["id"])
        track_name = str(track.get("name") or "Unknown Track")
        artists = track.get("artists") or []
        track_artist = artists[0].get("name") if artists and artists[0] else "Unknown Artist"

        progress_ms = int(playback.get("progress_ms") or 0)
        duration_ms = int(track.get("duration_ms") or 0)
        timestamp_ms = int(playback.get("timestamp") or _now_millis())
        play_instance_id = self._play_id(track_id, timestamp_ms, progress_ms)

        if self.state.track_id is None:
            self._set_track(
                track_id,
                track_name,
                track_artist,
                progress_ms,
                duration_ms,
                timestamp_ms,
                play_instance_id,
            )
            return

        if track_id != self.state.track_id:
            self._finalize_track()
            self._set_track(
                track_id,
                track_name,
                track_artist,
                progress_ms,
                duration_ms,
                timestamp_ms,
                play_instance_id,
            )
            return

        restarted = (
            self.state.play_instance_id
            and play_instance_id
            and play_instance_id != self.state.play_instance_id
            and (progress_ms + 5000) < self.state.progress_ms
        )
        if restarted:
            self._finalize_track()
            self._set_track(
                track_id,
                track_name,
                track_artist,
                progress_ms,
                duration_ms,
                timestamp_ms,
                play_instance_id,
            )
            return

        if progress_ms > self.state.progress_ms:
            self.state.progress_ms = progress_ms
        if duration_ms:
            self.state.duration_ms = duration_ms
        if timestamp_ms:
            self.state.timestamp_ms = timestamp_ms
        if play_instance_id:
            self.state.play_instance_id = play_instance_id

        self._finalize_track()

    async def _run_loop(self) -> None:
        try:
            while self.db.tracker_running():
                try:
                    self._tick()
                except Exception as exc:
                    print(f"[WARN] Tracker loop error: {exc}")
                await asyncio.sleep(max(int(self.db.settings()["check_interval"]), 3))
        except asyncio.CancelledError:
            self._finalize_track()
            raise
        finally:
            self.task = None


class SettingsUpdate(BaseModel):
    favorite_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    min_completion_ratio: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    check_interval: Optional[int] = Field(default=None, ge=3, le=300)
    playlist_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    playlist_public: Optional[bool] = None
    auto_add_enabled: Optional[bool] = None


def _format_now_playing(playback: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not playback:
        return None

    track = playback.get("item")
    if not track:
        return None

    artists = track.get("artists") or []
    artist = artists[0].get("name") if artists and artists[0] else "Unknown Artist"
    duration_ms = int(track.get("duration_ms") or 0)
    progress_ms = int(playback.get("progress_ms") or 0)

    return {
        "track_id": track.get("id"),
        "name": track.get("name"),
        "artist": artist,
        "duration_ms": duration_ms,
        "progress_ms": progress_ms,
        "completion_ratio": (progress_ms / duration_ms) if duration_ms > 0 else 0,
        "is_playing": bool(playback.get("is_playing")),
    }


def _safe_now_playing() -> Optional[dict[str, Any]]:
    if not database.is_connected():
        return None
    try:
        return _format_now_playing(spotify_service.client().current_playback())
    except Exception as exc:
        return {"error": str(exc)}


def _payload() -> dict[str, Any]:
    settings = database.settings()
    threshold = int(settings["favorite_threshold"])
    return {
        "connected": database.is_connected(),
        "account": database.account(),
        "tracker_running": database.tracker_running(),
        "now_playing": _safe_now_playing(),
        "stats": {
            "tracks_counted_24h": database.count_plays_since(_now_millis() - 86_400_000),
            "next_favorite": max(threshold - database.max_occurrence(), 0),
        },
        "settings": settings,
        "recent_plays": database.recent_plays(),
        "favorites": database.favorites(minimum_occurrences=threshold),
    }


INDEX_FILE = os.path.join(os.path.dirname(__file__), "index.html")


config = AppConfig.from_env()
database = Database(config.db_path, config.default_playlist_name)
spotify_service = SpotifyService(config, database)
tracker = Tracker(database, spotify_service)

app = FastAPI(title="FavSongs", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
async def startup_event() -> None:
    await tracker.startup()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await tracker.shutdown()
    database.close()


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE)


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    return _payload()


@app.post("/api/auth/start")
async def auth_start() -> dict[str, str]:
    state = secrets.token_urlsafe(32)
    database.save_oauth_state(state, _now_seconds() + STATE_TTL_SECONDS)
    return {"auth_url": spotify_service.auth_url(state)}


@app.get("/api/auth/callback")
async def auth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse(url="/?oauth=error")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing Spotify OAuth code or state")
    if not database.consume_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    spotify_service.connect(code)
    return RedirectResponse(url="/?oauth=connected")


@app.post("/api/settings")
async def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    if "playlist_name" in updates:
        updates["playlist_id"] = None
    return {"settings": database.update_settings(updates)}


@app.post("/api/tracker/start")
async def start_tracker() -> dict[str, bool]:
    await tracker.start()
    return {"running": True}


@app.post("/api/tracker/stop")
async def stop_tracker() -> dict[str, bool]:
    await tracker.stop()
    return {"running": False}


@app.post("/api/favorites/{track_id}/add")
async def add_favorite(track_id: str) -> dict[str, bool]:
    if not database.is_connected():
        raise HTTPException(status_code=400, detail="Connect Spotify first")
    if not tracker.add_track(track_id, allow_duplicate=True):
        raise HTTPException(status_code=400, detail="Could not add that track to the playlist")
    return {"queued": True}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Any, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
