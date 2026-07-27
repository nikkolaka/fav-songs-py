"""SQLite persistence. One connection guarded by a lock, as before -- the write volume
here is a handful of rows per user per minute."""

import os
import sqlite3
import time
from threading import Lock
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

PLAYS_RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_user_id  TEXT NOT NULL UNIQUE,
    display_name     TEXT NOT NULL,
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token   TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    expires_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT PRIMARY KEY,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    user_id               INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    favorite_threshold    INTEGER NOT NULL DEFAULT 5,
    min_completion_ratio  REAL    NOT NULL DEFAULT 0.8,
    poll_interval         INTEGER NOT NULL DEFAULT 30,
    playlist_name         TEXT    NOT NULL,
    playlist_public       INTEGER NOT NULL DEFAULT 0,
    auto_add_enabled      INTEGER NOT NULL DEFAULT 1,
    favorites_playlist_id TEXT,
    discovery_enabled     INTEGER NOT NULL DEFAULT 1,
    tracker_running       INTEGER NOT NULL DEFAULT 1
);

-- Dedup ledger for the recently-played sweep. played_at is assigned by Spotify and is
-- stable across calls, so this UNIQUE constraint is what makes the sweep idempotent.
CREATE TABLE IF NOT EXISTS plays (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id     TEXT    NOT NULL,
    played_at    INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    artist       TEXT    NOT NULL,
    context_uri  TEXT,
    UNIQUE (user_id, track_id, played_at)
);

CREATE INDEX IF NOT EXISTS idx_plays_user_played
    ON plays (user_id, played_at DESC);

CREATE TABLE IF NOT EXISTS play_counts (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id     TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    artist       TEXT    NOT NULL,
    occurrences  INTEGER NOT NULL DEFAULT 0,
    last_played  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, track_id)
);

CREATE TABLE IF NOT EXISTS cursors (
    user_id                INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    recently_played_after  INTEGER NOT NULL DEFAULT 0
);

-- Playlists whose plays get filed into the monthly discovery archive.
CREATE TABLE IF NOT EXISTS discovery_sources (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id  TEXT    NOT NULL,
    label        TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, playlist_id)
);

-- Playlist contexts we have seen but cannot name, because Spotify-owned playlists are
-- unreadable. Surfaced in the UI so the user can label one as a discovery source.
CREATE TABLE IF NOT EXISTS seen_contexts (
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id   TEXT    NOT NULL,
    last_seen     INTEGER NOT NULL,
    sample_track  TEXT    NOT NULL,
    play_count    INTEGER NOT NULL DEFAULT 1,
    dismissed     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, playlist_id)
);

CREATE TABLE IF NOT EXISTS discovery_archive (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month      TEXT    NOT NULL,
    track_id   TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    artist     TEXT    NOT NULL,
    source_id  TEXT    NOT NULL,
    added_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, month, track_id)
);

CREATE TABLE IF NOT EXISTS discovery_playlists (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month        TEXT    NOT NULL,
    playlist_id  TEXT    NOT NULL,
    PRIMARY KEY (user_id, month)
);
"""

SETTINGS_COLUMNS = (
    "favorite_threshold",
    "min_completion_ratio",
    "poll_interval",
    "playlist_name",
    "playlist_public",
    "auto_add_enabled",
    "favorites_playlist_id",
    "discovery_enabled",
    "tracker_running",
)

BOOLEAN_SETTINGS = frozenset(
    {"playlist_public", "auto_add_enabled", "discovery_enabled", "tracker_running"}
)


def now_seconds() -> int:
    return int(time.time())


def now_millis() -> int:
    return int(time.time() * 1000)


class Database:
    def __init__(self, db_path: str, fernet_key: bytes, default_playlist_name: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.lock = Lock()
        self.fernet = Fernet(fernet_key)
        self.default_playlist_name = default_playlist_name
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ---------------------------------------------------------------- users

    def upsert_user(self, spotify_user_id: str, display_name: str) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT id FROM users WHERE spotify_user_id = ?", (spotify_user_id,)
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name, row["id"]),
                )
                self.conn.commit()
                return int(row["id"])

            cursor = self.conn.execute(
                "INSERT INTO users (spotify_user_id, display_name, created_at) VALUES (?, ?, ?)",
                (spotify_user_id, display_name, now_seconds()),
            )
            user_id = int(cursor.lastrowid)
            self.conn.execute(
                "INSERT INTO settings (user_id, playlist_name) VALUES (?, ?)",
                (user_id, self.default_playlist_name),
            )
            self.conn.execute(
                "INSERT INTO cursors (user_id, recently_played_after) VALUES (?, 0)",
                (user_id,),
            )
            self.conn.commit()
            return user_id

    def user(self, user_id: int) -> Optional[dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                "SELECT id, spotify_user_id, display_name FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def user_count(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def user_exists(self, spotify_user_id: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM users WHERE spotify_user_id = ?", (spotify_user_id,)
            ).fetchone()
        return row is not None

    def connected_user_ids(self) -> list[int]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT u.id FROM users u
                JOIN tokens t ON t.user_id = u.id
                JOIN settings s ON s.user_id = u.id
                WHERE s.tracker_running = 1
                """
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def delete_user(self, user_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.conn.commit()

    # --------------------------------------------------------------- tokens

    def save_tokens(
        self, user_id: int, access_token: str, refresh_token: str, expires_at: int
    ) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO tokens (user_id, access_token, refresh_token, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at
                """,
                (
                    user_id,
                    self.fernet.encrypt(access_token.encode()).decode(),
                    self.fernet.encrypt(refresh_token.encode()).decode(),
                    expires_at,
                ),
            )
            self.conn.commit()

    def tokens(self, user_id: int) -> Optional[dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                "SELECT access_token, refresh_token, expires_at FROM tokens WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return {
                "access_token": self.fernet.decrypt(row["access_token"].encode()).decode(),
                "refresh_token": self.fernet.decrypt(row["refresh_token"].encode()).decode(),
                "expires_at": int(row["expires_at"]),
            }
        except InvalidToken:
            # SESSION_SECRET changed out from under us; force a reconnect.
            self.clear_tokens(user_id)
            return None

    def clear_tokens(self, user_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
            self.conn.commit()

    # ------------------------------------------------------------- sessions

    def create_session(self, token: str, user_id: int, expires_at: int) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now_seconds(), expires_at),
            )
            self.conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?", (now_seconds(),)
            )
            self.conn.commit()

    def session_user_id(self, token: str) -> Optional[int]:
        with self.lock:
            row = self.conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
        if not row or int(row["expires_at"]) < now_seconds():
            return None
        return int(row["user_id"])

    def delete_session(self, token: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self.conn.commit()

    # ---------------------------------------------------------- oauth state

    def save_oauth_state(self, state: str, expires_at: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now_seconds(),))
            self.conn.execute(
                "INSERT INTO oauth_states (state, expires_at) VALUES (?, ?)",
                (state, expires_at),
            )
            self.conn.commit()

    def consume_oauth_state(self, state: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT expires_at FROM oauth_states WHERE state = ?", (state,)
            ).fetchone()
            self.conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            self.conn.commit()
        return bool(row) and int(row["expires_at"]) >= now_seconds()

    # -------------------------------------------------------------- settings

    def settings(self, user_id: int) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                f"SELECT {', '.join(SETTINGS_COLUMNS)} FROM settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"No settings for user {user_id}")
        data = dict(row)
        for key in BOOLEAN_SETTINGS:
            data[key] = bool(data[key])
        return data

    def update_settings(self, user_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        parts, values = [], []
        for key, value in updates.items():
            if key not in SETTINGS_COLUMNS:
                continue
            if key in BOOLEAN_SETTINGS:
                value = 1 if value else 0
            parts.append(f"{key} = ?")
            values.append(value)

        if parts:
            values.append(user_id)
            with self.lock:
                self.conn.execute(
                    f"UPDATE settings SET {', '.join(parts)} WHERE user_id = ?", tuple(values)
                )
                self.conn.commit()
        return self.settings(user_id)

    # ----------------------------------------------------------------- plays

    def record_play(
        self,
        user_id: int,
        track_id: str,
        name: str,
        artist: str,
        played_at: int,
        context_uri: Optional[str],
    ) -> Optional[int]:
        """Insert one play. Returns the new occurrence count, or None if already seen.

        The UNIQUE(user_id, track_id, played_at) constraint is what makes the sweep safe
        to re-run over overlapping windows.
        """
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO plays
                    (user_id, track_id, played_at, name, artist, context_uri)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, track_id, played_at, name, artist, context_uri),
            )
            if cursor.rowcount == 0:
                self.conn.commit()
                return None

            self.conn.execute(
                """
                INSERT INTO play_counts (user_id, track_id, name, artist, occurrences, last_played)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id, track_id) DO UPDATE SET
                    name        = excluded.name,
                    artist      = excluded.artist,
                    occurrences = play_counts.occurrences + 1,
                    last_played = MAX(play_counts.last_played, excluded.last_played)
                """,
                (user_id, track_id, name, artist, played_at),
            )
            row = self.conn.execute(
                "SELECT occurrences FROM play_counts WHERE user_id = ? AND track_id = ?",
                (user_id, track_id),
            ).fetchone()
            self.conn.commit()
        return int(row["occurrences"])

    def prune_plays(self) -> None:
        cutoff = now_millis() - PLAYS_RETENTION_DAYS * 86_400_000
        with self.lock:
            self.conn.execute("DELETE FROM plays WHERE played_at < ?", (cutoff,))
            self.conn.commit()

    def cursor_after(self, user_id: int) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT recently_played_after FROM cursors WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row["recently_played_after"]) if row else 0

    def set_cursor_after(self, user_id: int, value: int) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO cursors (user_id, recently_played_after) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    recently_played_after = MAX(cursors.recently_played_after, excluded.recently_played_after)
                """,
                (user_id, value),
            )
            self.conn.commit()

    def count_plays_since(self, user_id: int, since_ms: int) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM plays WHERE user_id = ? AND played_at >= ?",
                (user_id, since_ms),
            ).fetchone()
        return int(row["n"])

    def play_counts(self, user_id: int) -> dict[str, dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT track_id, name, artist, occurrences, last_played
                FROM play_counts WHERE user_id = ?
                """,
                (user_id,),
            ).fetchall()
        return {str(row["track_id"]): dict(row) for row in rows}

    def next_favorite_candidate(self, user_id: int, threshold: int) -> Optional[dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT track_id, name, artist, occurrences, last_played
                FROM play_counts
                WHERE user_id = ? AND occurrences < ?
                ORDER BY occurrences DESC, last_played DESC
                LIMIT 1
                """,
                (user_id, threshold),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------- discovery

    def discovery_sources(self, user_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT playlist_id, label FROM discovery_sources
                WHERE user_id = ? ORDER BY created_at
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_discovery_source(self, user_id: int, playlist_id: str, label: str) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO discovery_sources (user_id, playlist_id, label, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, playlist_id) DO UPDATE SET label = excluded.label
                """,
                (user_id, playlist_id, label, now_seconds()),
            )
            self.conn.execute(
                "DELETE FROM seen_contexts WHERE user_id = ? AND playlist_id = ?",
                (user_id, playlist_id),
            )
            self.conn.commit()

    def remove_discovery_source(self, user_id: int, playlist_id: str) -> None:
        with self.lock:
            self.conn.execute(
                "DELETE FROM discovery_sources WHERE user_id = ? AND playlist_id = ?",
                (user_id, playlist_id),
            )
            self.conn.commit()

    def note_seen_context(
        self, user_id: int, playlist_id: str, sample_track: str
    ) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO seen_contexts (user_id, playlist_id, last_seen, sample_track)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, playlist_id) DO UPDATE SET
                    last_seen    = excluded.last_seen,
                    sample_track = excluded.sample_track,
                    play_count   = seen_contexts.play_count + 1
                """,
                (user_id, playlist_id, now_seconds(), sample_track),
            )
            self.conn.commit()

    def unlabelled_contexts(self, user_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT playlist_id, sample_track, play_count, last_seen
                FROM seen_contexts
                WHERE user_id = ? AND dismissed = 0
                ORDER BY play_count DESC, last_seen DESC
                LIMIT 5
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def dismiss_context(self, user_id: int, playlist_id: str) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE seen_contexts SET dismissed = 1 WHERE user_id = ? AND playlist_id = ?",
                (user_id, playlist_id),
            )
            self.conn.commit()

    def archive_discovery(
        self,
        user_id: int,
        month: str,
        track_id: str,
        name: str,
        artist: str,
        source_id: str,
    ) -> bool:
        """Returns True only the first time a track lands in a given month."""
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO discovery_archive
                    (user_id, month, track_id, name, artist, source_id, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, month, track_id, name, artist, source_id, now_seconds()),
            )
            self.conn.commit()
        return cursor.rowcount == 1

    def unarchive_discovery(self, user_id: int, month: str, track_id: str) -> None:
        """Roll back an archive row when the Spotify write that followed it failed."""
        with self.lock:
            self.conn.execute(
                "DELETE FROM discovery_archive WHERE user_id = ? AND month = ? AND track_id = ?",
                (user_id, month, track_id),
            )
            self.conn.commit()

    def discovery_month(self, user_id: int, month: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT track_id, name, artist, added_at FROM discovery_archive
                WHERE user_id = ? AND month = ? ORDER BY added_at DESC
                """,
                (user_id, month),
            ).fetchall()
        return [dict(row) for row in rows]

    def discovery_months(self, user_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT month, COUNT(*) AS tracks FROM discovery_archive
                WHERE user_id = ? GROUP BY month ORDER BY month DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def discovery_playlist_id(self, user_id: int, month: str) -> Optional[str]:
        with self.lock:
            row = self.conn.execute(
                "SELECT playlist_id FROM discovery_playlists WHERE user_id = ? AND month = ?",
                (user_id, month),
            ).fetchone()
        return str(row["playlist_id"]) if row else None

    def set_discovery_playlist_id(self, user_id: int, month: str, playlist_id: str) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO discovery_playlists (user_id, month, playlist_id) VALUES (?, ?, ?)
                ON CONFLICT(user_id, month) DO UPDATE SET playlist_id = excluded.playlist_id
                """,
                (user_id, month, playlist_id),
            )
            self.conn.commit()
