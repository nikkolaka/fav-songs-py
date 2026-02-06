import asyncio
import base64
import os
import secrets
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional

import requests
import spotipy
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

load_dotenv()


def _now_seconds() -> int:
    return int(time.time())


def _now_millis() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class AppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    site_username: str
    site_password: str
    db_path: str
    data_dir: str
    oauth_scope: str
    oauth_state_ttl_seconds: int
    playlist_cache_ttl: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        missing = [
            name
            for name in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "SITE_PASSWORD")
            if not os.getenv(name)
        ]
        if missing:
            missing_list = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {missing_list}")

        data_dir = os.getenv("FAVSONGS_DATA_DIR", "data")
        db_path = os.getenv("FAVSONGS_DB_PATH", os.path.join(data_dir, "favsongs.db"))

        return cls(
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            redirect_uri=os.environ["REDIRECT_URI"],
            site_username=os.getenv("SITE_USERNAME", "friend"),
            site_password=os.environ["SITE_PASSWORD"],
            db_path=db_path,
            data_dir=data_dir,
            oauth_scope=os.getenv(
                "SPOTIFY_SCOPE",
                "user-read-playback-state,user-read-currently-playing,playlist-modify-public,playlist-modify-private",
            ),
            oauth_state_ttl_seconds=int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600")),
            playlist_cache_ttl=int(os.getenv("PLAYLIST_CACHE_TTL", "300")),
        )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, username: str, password: str, public_paths: Optional[set[str]] = None):
        super().__init__(app)
        self.username = username
        self.password = password
        self.public_paths = public_paths or set()

    def _authorized(self, auth_header: Optional[str]) -> bool:
        if not auth_header:
            return False

        try:
            scheme, encoded = auth_header.split(" ", 1)
            if scheme.lower() != "basic":
                return False

            raw = base64.b64decode(encoded.strip()).decode("utf-8")
            username, password = raw.split(":", 1)
            return secrets.compare_digest(username, self.username) and secrets.compare_digest(
                password, self.password
            )
        except Exception:
            return False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self.public_paths:
            return await call_next(request)

        if not self._authorized(request.headers.get("Authorization")):
            return PlainTextResponse(
                "Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="FavSongs"'},
            )

        return await call_next(request)


class Database:
    def __init__(self, db_path: str):
        db_dir = os.path.dirname(db_path) or "."
        os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    spotify_user_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tokens (
                    user_id TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    favorite_threshold INTEGER NOT NULL DEFAULT 5,
                    min_completion_ratio REAL NOT NULL DEFAULT 0.8,
                    check_interval INTEGER NOT NULL DEFAULT 10,
                    min_play_gap_ms INTEGER NOT NULL DEFAULT 300000,
                    playlist_name TEXT NOT NULL DEFAULT 'Favourite Songs - Whatsit',
                    playlist_public INTEGER NOT NULL DEFAULT 1,
                    auto_add_enabled INTEGER NOT NULL DEFAULT 1,
                    playlist_id TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tracker_state (
                    user_id TEXT PRIMARY KEY,
                    running INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tracks (
                    track_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    artist TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    play_instance_id TEXT,
                    played_at INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
                    UNIQUE(user_id, play_instance_id)
                );

                CREATE INDEX IF NOT EXISTS idx_plays_user_played_at ON plays (user_id, played_at DESC);

                CREATE TABLE IF NOT EXISTS favorites (
                    user_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 0,
                    last_played INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, track_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                """
            )
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _row_to_dict(self, row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row else None

    def ensure_user_defaults(self, user_id: str) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO user_settings (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id,),
            )
            self.conn.execute(
                """
                INSERT INTO tracker_state (user_id, running)
                VALUES (?, 0)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id,),
            )
            self.conn.commit()

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                "SELECT id, spotify_user_id, display_name, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._row_to_dict(row)

    def upsert_user(self, spotify_user_id: str, display_name: str) -> Dict[str, Any]:
        with self.lock:
            existing = self.conn.execute(
                "SELECT id FROM users WHERE spotify_user_id = ?",
                (spotify_user_id,),
            ).fetchone()

            now = _now_seconds()
            if existing:
                user_id = existing["id"]
                self.conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name, user_id),
                )
            else:
                user_id = secrets.token_urlsafe(16)
                self.conn.execute(
                    """
                    INSERT INTO users (id, spotify_user_id, display_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, spotify_user_id, display_name, now),
                )
            self.conn.commit()

        self.ensure_user_defaults(user_id)
        user = self.get_user(user_id)
        if not user:
            raise RuntimeError("Could not upsert user")
        return user

    def list_accounts(self) -> list[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT u.id, u.spotify_user_id, u.display_name, u.created_at,
                       COALESCE(ts.running, 0) AS tracker_running
                FROM users u
                LEFT JOIN tracker_state ts ON ts.user_id = u.id
                ORDER BY u.created_at ASC
                """
            ).fetchall()
        accounts = []
        for row in rows:
            item = dict(row)
            item["tracker_running"] = bool(item["tracker_running"])
            accounts.append(item)
        return accounts

    def save_tokens(self, user_id: str, access_token: str, refresh_token: str, expires_at: int) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO tokens (user_id, access_token, refresh_token, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (user_id, access_token, refresh_token, expires_at, _now_seconds()),
            )
            self.conn.commit()

    def get_tokens(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                "SELECT user_id, access_token, refresh_token, expires_at FROM tokens WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._row_to_dict(row)

    def get_settings(self, user_id: str) -> Dict[str, Any]:
        self.ensure_user_defaults(user_id)
        with self.lock:
            row = self.conn.execute(
                """
                SELECT user_id, favorite_threshold, min_completion_ratio, check_interval,
                       min_play_gap_ms, playlist_name, playlist_public,
                       auto_add_enabled, playlist_id
                FROM user_settings
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            raise RuntimeError("Could not read settings")
        settings = dict(row)
        settings["playlist_public"] = bool(settings["playlist_public"])
        settings["auto_add_enabled"] = bool(settings["auto_add_enabled"])
        return settings

    def update_settings(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_user_defaults(user_id)
        if not updates:
            return self.get_settings(user_id)

        allowed_fields = {
            "favorite_threshold",
            "min_completion_ratio",
            "check_interval",
            "min_play_gap_ms",
            "playlist_name",
            "playlist_public",
            "auto_add_enabled",
            "playlist_id",
        }

        set_parts: list[str] = []
        values: list[Any] = []

        for key, value in updates.items():
            if key not in allowed_fields:
                continue
            if key in {"playlist_public", "auto_add_enabled"}:
                value = 1 if bool(value) else 0
            set_parts.append(f"{key} = ?")
            values.append(value)

        if not set_parts:
            return self.get_settings(user_id)

        values.append(user_id)
        with self.lock:
            self.conn.execute(
                f"UPDATE user_settings SET {', '.join(set_parts)} WHERE user_id = ?",
                tuple(values),
            )
            self.conn.commit()

        return self.get_settings(user_id)

    def create_oauth_state(self, state: str, expires_at: int) -> None:
        with self.lock:
            self.conn.execute(
                "DELETE FROM oauth_states WHERE expires_at < ?",
                (_now_seconds(),),
            )
            self.conn.execute(
                "INSERT INTO oauth_states (state, expires_at) VALUES (?, ?)",
                (state, expires_at),
            )
            self.conn.commit()

    def consume_oauth_state(self, state: str) -> bool:
        now = _now_seconds()
        with self.lock:
            row = self.conn.execute(
                "SELECT state, expires_at FROM oauth_states WHERE state = ?",
                (state,),
            ).fetchone()
            if not row:
                return False

            self.conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            self.conn.commit()

        return row["expires_at"] >= now

    def list_running_user_ids(self) -> list[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT user_id FROM tracker_state WHERE running = 1"
            ).fetchall()
        return [row["user_id"] for row in rows]

    def set_tracker_running(self, user_id: str, running: bool) -> None:
        self.ensure_user_defaults(user_id)
        with self.lock:
            self.conn.execute(
                "UPDATE tracker_state SET running = ? WHERE user_id = ?",
                (1 if running else 0, user_id),
            )
            self.conn.commit()

    def is_tracker_running(self, user_id: str) -> bool:
        self.ensure_user_defaults(user_id)
        with self.lock:
            row = self.conn.execute(
                "SELECT running FROM tracker_state WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return bool(row and row["running"])

    def _upsert_track(self, track_id: str, name: str, artist: str) -> None:
        self.conn.execute(
            """
            INSERT INTO tracks (track_id, name, artist)
            VALUES (?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                name = excluded.name,
                artist = excluded.artist
            """,
            (track_id, name, artist),
        )

    def record_completed_play(
        self,
        user_id: str,
        track_id: str,
        name: str,
        artist: str,
        played_at: int,
        play_instance_id: Optional[str],
        min_play_gap_ms: int,
    ) -> Optional[int]:
        with self.lock:
            self._upsert_track(track_id, name, artist)

            if not play_instance_id:
                play_instance_id = f"{track_id}:{played_at}"

            existing_play = self.conn.execute(
                "SELECT 1 FROM plays WHERE user_id = ? AND play_instance_id = ?",
                (user_id, play_instance_id),
            ).fetchone()
            if existing_play:
                self.conn.commit()
                return None

            favorite_row = self.conn.execute(
                """
                SELECT occurrences, last_played
                FROM favorites
                WHERE user_id = ? AND track_id = ?
                """,
                (user_id, track_id),
            ).fetchone()

            if favorite_row and (played_at - favorite_row["last_played"]) <= min_play_gap_ms:
                self.conn.commit()
                return None

            try:
                self.conn.execute(
                    """
                    INSERT INTO plays (user_id, track_id, play_instance_id, played_at, completed)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (user_id, track_id, play_instance_id, played_at),
                )
            except sqlite3.IntegrityError:
                self.conn.commit()
                return None

            if favorite_row:
                occurrences = int(favorite_row["occurrences"]) + 1
                self.conn.execute(
                    """
                    UPDATE favorites
                    SET occurrences = ?, last_played = ?
                    WHERE user_id = ? AND track_id = ?
                    """,
                    (occurrences, played_at, user_id, track_id),
                )
            else:
                occurrences = 1
                self.conn.execute(
                    """
                    INSERT INTO favorites (user_id, track_id, occurrences, last_played)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, track_id, occurrences, played_at),
                )

            self.conn.commit()
            return occurrences

    def count_plays_since(self, user_id: str, since_ms: int) -> int:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM plays
                WHERE user_id = ? AND played_at >= ?
                """,
                (user_id, since_ms),
            ).fetchone()
        return int(row["cnt"] if row else 0)

    def max_occurrence(self, user_id: str) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(occurrences), 0) AS max_occ FROM favorites WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["max_occ"] if row else 0)

    def recent_plays(self, user_id: str, limit: int = 10) -> list[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT p.track_id, t.name, t.artist, p.played_at
                FROM plays p
                JOIN tracks t ON t.track_id = p.track_id
                WHERE p.user_id = ?
                ORDER BY p.played_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def favorites(self, user_id: str, limit: int = 200) -> list[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT f.track_id, t.name, t.artist, f.occurrences, f.last_played
                FROM favorites f
                JOIN tracks t ON t.track_id = f.track_id
                WHERE f.user_id = ?
                ORDER BY f.occurrences DESC, f.last_played DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


class SpotifyService:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def _token_request(self, payload: Dict[str, str]) -> Dict[str, Any]:
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

    def exchange_code(self, code: str) -> Dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        }
        return self._token_request(payload)

    def refresh_access_token(self, user_id: str) -> Dict[str, Any]:
        tokens = self.db.get_tokens(user_id)
        if not tokens:
            raise HTTPException(status_code=400, detail="Spotify account is not connected for this user")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        }
        token_data = self._token_request(payload)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", tokens["refresh_token"])
        expires_at = _now_seconds() + int(token_data.get("expires_in", 3600))

        self.db.save_tokens(user_id, access_token, refresh_token, expires_at)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }

    def get_valid_access_token(self, user_id: str) -> str:
        tokens = self.db.get_tokens(user_id)
        if not tokens:
            raise HTTPException(status_code=400, detail="Spotify account is not connected for this user")

        if int(tokens["expires_at"]) > (_now_seconds() + 60):
            return str(tokens["access_token"])

        refreshed = self.refresh_access_token(user_id)
        return str(refreshed["access_token"])

    def spotify_client(self, user_id: str) -> spotipy.Spotify:
        access_token = self.get_valid_access_token(user_id)
        return spotipy.Spotify(auth=access_token, requests_timeout=20)

    def spotify_profile(self, access_token: str) -> Dict[str, Any]:
        response = requests.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=400,
                detail=f"Could not load Spotify profile ({response.status_code}): {response.text}",
            )
        return response.json()

    def build_auth_url(self, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": self.config.oauth_scope,
                "state": state,
                "show_dialog": "true",
            }
        )
        return f"https://accounts.spotify.com/authorize?{query}"


@dataclass
class RuntimeTrackState:
    current_track_id: Optional[str] = None
    current_track_name: Optional[str] = None
    current_track_artist: Optional[str] = None
    current_track_progress_ms: int = 0
    current_track_duration_ms: int = 0
    current_track_timestamp_ms: int = 0
    current_play_instance_id: Optional[str] = None


class TrackerManager:
    def __init__(self, db: Database, spotify_service: SpotifyService, playlist_cache_ttl: int):
        self.db = db
        self.spotify_service = spotify_service
        self.playlist_cache_ttl = playlist_cache_ttl
        self.tasks: Dict[str, asyncio.Task] = {}
        self.runtime: Dict[str, RuntimeTrackState] = {}
        self.playlist_cache: Dict[str, Dict[str, Any]] = {}

    async def start_saved_trackers(self) -> None:
        for user_id in self.db.list_running_user_ids():
            await self.start(user_id)

    async def start(self, user_id: str) -> None:
        if user_id in self.tasks and not self.tasks[user_id].done():
            self.db.set_tracker_running(user_id, True)
            return

        if not self.db.get_user(user_id):
            raise HTTPException(status_code=404, detail="Unknown account")

        self.db.set_tracker_running(user_id, True)
        task = asyncio.create_task(self._run_loop(user_id), name=f"tracker-{user_id}")
        self.tasks[user_id] = task

    async def stop(self, user_id: str) -> None:
        self.db.set_tracker_running(user_id, False)
        task = self.tasks.pop(user_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        for user_id in list(self.tasks.keys()):
            await self.stop(user_id)

    def _derive_play_instance_id(self, track_id: str, timestamp_ms: int, progress_ms: int) -> Optional[str]:
        if not track_id or not timestamp_ms:
            return None
        start_ms = max(timestamp_ms - progress_ms, 0)
        return f"{track_id}:{start_ms}"

    def _is_completed(self, progress_ms: int, duration_ms: int, min_completion_ratio: float) -> bool:
        if duration_ms <= 0:
            return False
        return (progress_ms / duration_ms) >= min_completion_ratio

    def _resolve_playlist_id(self, user_id: str, sp: spotipy.Spotify, settings: Dict[str, Any]) -> Optional[str]:
        if settings.get("playlist_id"):
            return str(settings["playlist_id"])

        profile = sp.me()
        spotify_user_id = profile.get("id")
        if not spotify_user_id:
            return None

        playlist_name = str(settings.get("playlist_name") or "Favourite Songs - Whatsit")
        playlist_public = bool(settings.get("playlist_public", True))

        results = sp.user_playlists(spotify_user_id, limit=50)
        while results:
            for playlist in results.get("items", []):
                if playlist and playlist.get("name") == playlist_name:
                    playlist_id = playlist.get("id")
                    if playlist_id:
                        self.db.update_settings(user_id, {"playlist_id": playlist_id})
                        return str(playlist_id)
            if results.get("next"):
                results = sp.next(results)
            else:
                break

        created = sp.user_playlist_create(
            spotify_user_id,
            playlist_name,
            public=playlist_public,
            description="Auto-managed by FavSongs multi-account tracker",
        )
        playlist_id = created.get("id")
        if playlist_id:
            self.db.update_settings(user_id, {"playlist_id": playlist_id})
            return str(playlist_id)
        return None

    def _get_playlist_tracks_cached(self, user_id: str, playlist_id: str, sp: spotipy.Spotify) -> set[str]:
        cached = self.playlist_cache.get(user_id)
        now = time.time()
        if (
            cached
            and cached.get("playlist_id") == playlist_id
            and (now - float(cached.get("cached_at", 0))) < self.playlist_cache_ttl
        ):
            return cached["track_ids"]

        track_ids: set[str] = set()
        results = sp.playlist_tracks(playlist_id, limit=100)
        while results:
            for item in results.get("items", []):
                track = item.get("track") if item else None
                track_id = track.get("id") if track else None
                if track_id:
                    track_ids.add(track_id)
            if results.get("next"):
                results = sp.next(results)
            else:
                break

        self.playlist_cache[user_id] = {
            "playlist_id": playlist_id,
            "track_ids": track_ids,
            "cached_at": now,
        }
        return track_ids

    def add_track_to_playlist(self, user_id: str, track_id: str, allow_duplicate: bool = False) -> bool:
        settings = self.db.get_settings(user_id)
        sp = self.spotify_service.spotify_client(user_id)
        playlist_id = self._resolve_playlist_id(user_id, sp, settings)
        if not playlist_id:
            return False

        existing_ids = self._get_playlist_tracks_cached(user_id, playlist_id, sp)
        if not allow_duplicate and track_id in existing_ids:
            return False

        sp.playlist_add_items(playlist_id, [track_id], position=0)
        existing_ids.add(track_id)
        return True

    def _finalize_current_track(self, user_id: str, state: RuntimeTrackState) -> None:
        if not state.current_track_id or not state.current_track_name or not state.current_track_artist:
            return

        settings = self.db.get_settings(user_id)
        completed = self._is_completed(
            state.current_track_progress_ms,
            state.current_track_duration_ms,
            float(settings["min_completion_ratio"]),
        )
        if not completed:
            return

        played_at = state.current_track_timestamp_ms or _now_millis()
        occurrences = self.db.record_completed_play(
            user_id=user_id,
            track_id=state.current_track_id,
            name=state.current_track_name,
            artist=state.current_track_artist,
            played_at=played_at,
            play_instance_id=state.current_play_instance_id,
            min_play_gap_ms=int(settings["min_play_gap_ms"]),
        )

        if not occurrences:
            return

        should_auto_add = bool(settings["auto_add_enabled"]) and occurrences >= int(settings["favorite_threshold"])
        if should_auto_add:
            try:
                self.add_track_to_playlist(user_id, state.current_track_id, allow_duplicate=False)
            except Exception as exc:
                print(f"[WARN] Could not auto-add track to playlist for {user_id}: {exc}")

    def _set_current_track(
        self,
        state: RuntimeTrackState,
        track_id: str,
        track_name: str,
        track_artist: str,
        progress_ms: int,
        duration_ms: int,
        timestamp_ms: int,
        play_instance_id: Optional[str],
    ) -> None:
        state.current_track_id = track_id
        state.current_track_name = track_name
        state.current_track_artist = track_artist
        state.current_track_progress_ms = progress_ms
        state.current_track_duration_ms = duration_ms
        state.current_track_timestamp_ms = timestamp_ms
        state.current_play_instance_id = play_instance_id

    def _process_current_track(self, user_id: str, state: RuntimeTrackState) -> None:
        sp = self.spotify_service.spotify_client(user_id)
        playback = sp.current_playback()
        if not playback:
            return

        track = playback.get("item")
        if not track:
            return

        track_id = track.get("id")
        if not track_id:
            return

        track_name = track.get("name") or "Unknown Track"
        artists = track.get("artists") or []
        first_artist = artists[0].get("name") if artists and artists[0] else "Unknown Artist"

        progress_ms = int(playback.get("progress_ms") or 0)
        duration_ms = int(track.get("duration_ms") or 0)
        timestamp_ms = int(playback.get("timestamp") or _now_millis())
        play_instance_id = self._derive_play_instance_id(track_id, timestamp_ms, progress_ms)

        if state.current_track_id is None:
            self._set_current_track(
                state,
                track_id,
                track_name,
                first_artist,
                progress_ms,
                duration_ms,
                timestamp_ms,
                play_instance_id,
            )
            return

        if track_id != state.current_track_id:
            self._finalize_current_track(user_id, state)
            self._set_current_track(
                state,
                track_id,
                track_name,
                first_artist,
                progress_ms,
                duration_ms,
                timestamp_ms,
                play_instance_id,
            )
            return

        restarted = (
            state.current_play_instance_id
            and play_instance_id
            and play_instance_id != state.current_play_instance_id
            and (progress_ms + 5000) < state.current_track_progress_ms
        )
        if restarted:
            self._finalize_current_track(user_id, state)
            self._set_current_track(
                state,
                track_id,
                track_name,
                first_artist,
                progress_ms,
                duration_ms,
                timestamp_ms,
                play_instance_id,
            )
            return

        if progress_ms > state.current_track_progress_ms:
            state.current_track_progress_ms = progress_ms
        if duration_ms:
            state.current_track_duration_ms = duration_ms
        if timestamp_ms:
            state.current_track_timestamp_ms = timestamp_ms
        if play_instance_id:
            state.current_play_instance_id = play_instance_id

    async def _run_loop(self, user_id: str) -> None:
        state = self.runtime.setdefault(user_id, RuntimeTrackState())

        try:
            while self.db.is_tracker_running(user_id):
                try:
                    self._process_current_track(user_id, state)
                except Exception as exc:
                    print(f"[WARN] Tracker loop error for {user_id}: {exc}")

                settings = self.db.get_settings(user_id)
                check_interval = max(int(settings["check_interval"]), 3)
                await asyncio.sleep(check_interval)
        except asyncio.CancelledError:
            self._finalize_current_track(user_id, state)
            raise
        finally:
            self.tasks.pop(user_id, None)


class SettingsUpdate(BaseModel):
    favorite_threshold: Optional[int] = Field(default=None, ge=1, le=20)
    min_completion_ratio: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    check_interval: Optional[int] = Field(default=None, ge=3, le=300)
    min_play_gap_ms: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    playlist_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    playlist_public: Optional[bool] = None
    auto_add_enabled: Optional[bool] = None


def _format_now_playing(playback: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not playback:
        return None

    track = playback.get("item")
    if not track:
        return None

    artists = track.get("artists") or []
    artist_name = artists[0].get("name") if artists and artists[0] else "Unknown Artist"

    duration_ms = int(track.get("duration_ms") or 0)
    progress_ms = int(playback.get("progress_ms") or 0)

    return {
        "track_id": track.get("id"),
        "name": track.get("name"),
        "artist": artist_name,
        "duration_ms": duration_ms,
        "progress_ms": progress_ms,
        "is_playing": bool(playback.get("is_playing")),
        "completion_ratio": (progress_ms / duration_ms) if duration_ms > 0 else 0,
    }


config = AppConfig.from_env()
database = Database(config.db_path)
spotify_service = SpotifyService(config, database)
tracker_manager = TrackerManager(database, spotify_service, config.playlist_cache_ttl)

middleware = [
    Middleware(
        BasicAuthMiddleware,
        username=config.site_username,
        password=config.site_password,
        public_paths={"/healthz"},
    )
]

app = FastAPI(
    title="FavSongs",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    middleware=middleware,
)


@app.on_event("startup")
async def startup_event() -> None:
    await tracker_manager.start_saved_trackers()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await tracker_manager.shutdown()
    database.close()


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/api/health")
async def api_health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/accounts")
async def list_accounts() -> Dict[str, Any]:
    accounts = database.list_accounts()
    return {"accounts": accounts}


@app.post("/api/auth/spotify/start")
@app.post("/auth/spotify/start")
async def auth_spotify_start() -> Dict[str, str]:
    state = secrets.token_urlsafe(32)
    database.create_oauth_state(state, _now_seconds() + config.oauth_state_ttl_seconds)
    return {"auth_url": spotify_service.build_auth_url(state)}


@app.get("/api/auth/spotify/callback")
@app.get("/auth/spotify/callback")
async def auth_spotify_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        return RedirectResponse(url="/?oauth=error")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")

    if not database.consume_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    token_data = spotify_service.exchange_code(code)
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in", 3600))

    if not access_token or not refresh_token:
        raise HTTPException(status_code=400, detail="Spotify OAuth response missing token data")

    profile = spotify_service.spotify_profile(access_token)
    spotify_user_id = profile.get("id")
    display_name = profile.get("display_name") or spotify_user_id

    if not spotify_user_id:
        raise HTTPException(status_code=400, detail="Spotify profile missing user id")

    user = database.upsert_user(spotify_user_id=spotify_user_id, display_name=display_name)
    database.save_tokens(
        user_id=user["id"],
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=_now_seconds() + expires_in,
    )

    return RedirectResponse(url="/?oauth=connected")


def _require_account(user_id: str) -> Dict[str, Any]:
    account = database.get_user(user_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@app.get("/api/accounts/{user_id}/dashboard")
async def account_dashboard(user_id: str) -> Dict[str, Any]:
    account = _require_account(user_id)
    settings = database.get_settings(user_id)

    now_playing = None
    try:
        sp = spotify_service.spotify_client(user_id)
        now_playing = _format_now_playing(sp.current_playback())
    except Exception as exc:
        now_playing = {"error": str(exc)}

    plays_24h = database.count_plays_since(user_id, _now_millis() - 86_400_000)
    max_occurrences = database.max_occurrence(user_id)
    threshold = int(settings["favorite_threshold"])
    next_favorite = max(threshold - max_occurrences, 0)

    recent = [
        {
            "track_id": item["track_id"],
            "name": item["name"],
            "artist": item["artist"],
            "played_at": item["played_at"],
        }
        for item in database.recent_plays(user_id, limit=8)
    ]

    return {
        "account": {
            "id": account["id"],
            "spotify_user_id": account["spotify_user_id"],
            "display_name": account["display_name"],
            "tracker_running": database.is_tracker_running(user_id),
        },
        "now_playing": now_playing,
        "stats": {
            "tracks_counted_24h": plays_24h,
            "completion_threshold": float(settings["min_completion_ratio"]),
            "next_favorite": next_favorite,
        },
        "recent_plays": recent,
    }


@app.get("/api/accounts/{user_id}/favorites")
async def account_favorites(user_id: str) -> Dict[str, Any]:
    _require_account(user_id)
    return {"favorites": database.favorites(user_id, limit=200)}


@app.post("/api/accounts/{user_id}/favorites/{track_id}/force-add")
async def force_add_favorite(user_id: str, track_id: str) -> Dict[str, Any]:
    _require_account(user_id)
    added = tracker_manager.add_track_to_playlist(user_id, track_id, allow_duplicate=True)
    return {"queued": bool(added)}


@app.get("/api/accounts/{user_id}/settings")
async def account_settings(user_id: str) -> Dict[str, Any]:
    _require_account(user_id)
    return database.get_settings(user_id)


@app.post("/api/accounts/{user_id}/settings")
async def update_account_settings(user_id: str, payload: SettingsUpdate) -> Dict[str, Any]:
    _require_account(user_id)
    updates = payload.model_dump(exclude_none=True)

    # Changing the playlist name should force playlist resolution from scratch.
    if "playlist_name" in updates:
        updates["playlist_id"] = None

    updated = database.update_settings(user_id, updates)
    return {"settings": updated}


@app.post("/api/accounts/{user_id}/tracker/start")
async def start_tracker(user_id: str) -> Dict[str, bool]:
    _require_account(user_id)
    await tracker_manager.start(user_id)
    return {"running": True}


@app.post("/api/accounts/{user_id}/tracker/stop")
async def stop_tracker(user_id: str) -> Dict[str, bool]:
    _require_account(user_id)
    await tracker_manager.stop(user_id)
    return {"running": False}


@app.get("/me")
async def me(user_id: str) -> Dict[str, Any]:
    account = _require_account(user_id)
    return {
        "id": account["id"],
        "spotify_user_id": account["spotify_user_id"],
        "display_name": account["display_name"],
        "tracker_running": database.is_tracker_running(user_id),
    }


@app.get("/me/now-playing")
async def me_now_playing(user_id: str) -> Dict[str, Any]:
    _require_account(user_id)
    sp = spotify_service.spotify_client(user_id)
    return {"now_playing": _format_now_playing(sp.current_playback())}


@app.get("/me/favorites")
async def me_favorites(user_id: str) -> Dict[str, Any]:
    _require_account(user_id)
    return {"favorites": database.favorites(user_id, limit=200)}


@app.post("/me/settings")
async def me_settings(user_id: str, payload: SettingsUpdate) -> Dict[str, Any]:
    return await update_account_settings(user_id, payload)


@app.post("/me/tracker/start")
async def me_start_tracker(user_id: str) -> Dict[str, bool]:
    return await start_tracker(user_id)


@app.post("/me/tracker/stop")
async def me_stop_tracker(user_id: str) -> Dict[str, bool]:
    return await stop_tracker(user_id)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.mount("/", StaticFiles(directory="ui/option-1", html=True), name="ui")
