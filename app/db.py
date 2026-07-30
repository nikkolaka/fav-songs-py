"""SQLite persistence. One connection guarded by a lock, as before -- the write volume
here is a handful of rows per user per minute.

`listens` is the one table meant to grow without bound: it is the permanent history and
is never pruned. Everything that reads it does so through an index -- keyset pagination
rather than OFFSET, FTS5 rather than LIKE -- so a decade of plays costs the same per
page as a week of them.
"""

import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
from threading import Lock
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

HISTORY_PAGE_LIMIT = 200

# Bumped whenever the shape of the data changes. 1 = the recently-played ledger,
# 2 = measured listens alongside backfilled ones, 3 = measured listens only.
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
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

-- The permanent listening history. Exactly one thing writes here: the live playback
-- poll in app/tracker.py. Every row is therefore a measured listen with a real
-- `completion_ratio`, and there is no second path that could add a play we cannot
-- judge -- if it isn't in here, it wasn't heard while the app was watching.
CREATE TABLE IF NOT EXISTS listens (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id           TEXT    NOT NULL,
    name               TEXT    NOT NULL,
    artist             TEXT    NOT NULL,
    played_at          INTEGER NOT NULL,
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    listened_ms        INTEGER NOT NULL DEFAULT 0,
    completion_ratio   REAL    NOT NULL DEFAULT 0,
    qualified          INTEGER NOT NULL DEFAULT 0,
    context_uri        TEXT,
    is_open            INTEGER NOT NULL DEFAULT 0
);

-- Every history page is ordered by (played_at DESC, id DESC) and paged by keyset, so
-- this index alone answers a page without a sort or a scan of everything before it.
CREATE INDEX IF NOT EXISTS idx_listens_user_time
    ON listens (user_id, played_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_listens_user_qualified_time
    ON listens (user_id, qualified, played_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_listens_user_track
    ON listens (user_id, track_id, played_at DESC);

{play_counts}

-- Only the discovery embed sweep is scheduled now; listens have no cursor because
-- nothing is ever read back from Spotify's history.
CREATE TABLE IF NOT EXISTS cursors (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_source_sweep  INTEGER NOT NULL DEFAULT 0
);

-- Playlists whose contents get filed into the monthly discovery archive.
-- `degraded` holds the last embed-read failure, so a silently broken source is visible.
CREATE TABLE IF NOT EXISTS discovery_sources (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id  TEXT    NOT NULL,
    label        TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    degraded     TEXT,
    PRIMARY KEY (user_id, playlist_id)
);

-- Playlist contexts seen in playback that aren't registered sources. `title` is the
-- oEmbed name when we could resolve one, so the UI shows "Release Radar" rather than a
-- 22-character hash.
CREATE TABLE IF NOT EXISTS seen_contexts (
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id   TEXT    NOT NULL,
    last_seen     INTEGER NOT NULL,
    sample_track  TEXT    NOT NULL,
    title         TEXT,
    play_count    INTEGER NOT NULL DEFAULT 1,
    dismissed     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, playlist_id)
);

-- Tracks filtered out by the AI blocklist. Kept rather than discarded so the filter is
-- auditable -- you can see nothing legitimate was dropped -- and so a blocked track
-- isn't re-resolved against the API on every sweep.
CREATE TABLE IF NOT EXISTS discovery_blocked (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month       TEXT    NOT NULL,
    track_id    TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    artist      TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    blocked_at  INTEGER NOT NULL,
    PRIMARY KEY (user_id, month, track_id)
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

# Kept separate because a migration recreates this table from scratch: it is a
# materialised aggregate of `listens`, so rebuilding it is always the correct repair.
PLAY_COUNTS_DDL = """
CREATE TABLE IF NOT EXISTS play_counts (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id        TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    artist          TEXT    NOT NULL,
    qualified_plays INTEGER NOT NULL DEFAULT 0,
    total_plays     INTEGER NOT NULL DEFAULT 0,
    last_played     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, track_id)
);
"""

SCHEMA = SCHEMA.format(play_counts=PLAY_COUNTS_DDL.strip())

# Full-text search over the history, as an external-content table: the index stores the
# terms, the rows stay in `listens`, and the triggers keep the two in step. `prefix`
# makes "rad" match "Radiohead" without a leading-wildcard scan.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS listens_fts USING fts5(
    name,
    artist,
    content='listens',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2",
    prefix='2 3 4'
);

CREATE TRIGGER IF NOT EXISTS listens_fts_insert AFTER INSERT ON listens BEGIN
    INSERT INTO listens_fts (rowid, name, artist) VALUES (new.id, new.name, new.artist);
END;

CREATE TRIGGER IF NOT EXISTS listens_fts_delete AFTER DELETE ON listens BEGIN
    INSERT INTO listens_fts (listens_fts, rowid, name, artist)
    VALUES ('delete', old.id, old.name, old.artist);
END;

-- Open rows are rewritten on every poll; restricting the trigger to the indexed columns
-- keeps that from churning the FTS index once per tick.
CREATE TRIGGER IF NOT EXISTS listens_fts_update AFTER UPDATE OF name, artist ON listens BEGIN
    INSERT INTO listens_fts (listens_fts, rowid, name, artist)
    VALUES ('delete', old.id, old.name, old.artist);
    INSERT INTO listens_fts (rowid, name, artist) VALUES (new.id, new.name, new.artist);
END;
"""

# FTS5 treats these as syntax; a search box should treat them as nothing.
FTS_STRIP_RE = re.compile(r'["*(){}\[\]:^~-]+')


def fts_query(text: str) -> str:
    """Turn what someone typed into an FTS5 prefix query, e.g. `rad ok` -> `"rad"* "ok"*`."""
    tokens = [token for token in FTS_STRIP_RE.sub(" ", text).split() if token]
    return " ".join(f'"{token}"*' for token in tokens)


# `poll_interval` is deliberately absent: the polling rate is what makes the measurement
# accurate, so it is fixed in app/tracker.py rather than being anyone's to loosen. The
# column stays in the table so an older build could still read the database.
SETTINGS_COLUMNS = (
    "favorite_threshold",
    "min_completion_ratio",
    "playlist_name",
    "playlist_public",
    "auto_add_enabled",
    "favorites_playlist_id",
    "discovery_enabled",
    "tracker_running",
    "pinned_stats",
)

BOOLEAN_SETTINGS = frozenset(
    {"playlist_public", "auto_add_enabled", "discovery_enabled", "tracker_running"}
)

JSON_SETTINGS = frozenset({"pinned_stats"})


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
            self.fts = self._enable_fts()
            self._migrate()
            self.conn.commit()

    def _enable_fts(self) -> bool:
        """Build the search index, reporting whether this SQLite has FTS5 at all.

        Debian's and Alpine's builds both ship it, but a stray interpreter without it
        should degrade to LIKE rather than refuse to start.
        """
        try:
            self.conn.executescript(FTS_SCHEMA)
            return True
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 unavailable (%s); history search falls back to LIKE", exc)
            return False

    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def _table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        return row is not None

    def _detect_version(self) -> int:
        """Which schema an existing database is on, for one that predates `meta`."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row:
            return int(row["value"])
        if self._table_exists("plays"):
            return 1  # the recently-played ledger
        if "source" in self._columns("listens"):
            return 2  # measured listens, with backfilled ones alongside
        # No marker and no older shape: either brand new, or already current.
        return SCHEMA_VERSION if self._columns("listens") else 0

    def _migrate(self) -> None:
        """Bring a database created by an earlier version up to the current schema.

        Each step is written to be safe to re-run: a crash part-way through leaves a
        database the next start can finish migrating rather than one that needs a human.
        """
        additions = (
            ("discovery_sources", "degraded", "TEXT"),
            ("seen_contexts", "title", "TEXT"),
            ("cursors", "last_source_sweep", "INTEGER NOT NULL DEFAULT 0"),
            ("settings", "pinned_stats", "TEXT NOT NULL DEFAULT '[]'"),
        )
        for table, column, ddl in additions:
            if column not in self._columns(table):
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

        version = self._detect_version()

        if version == 1:
            # `plays` held what Spotify's history reported: that a track played, with no
            # measure of how much was heard. Nothing here can be judged against the
            # listening threshold, and the history now has a single source, so the
            # ledger is dropped rather than imported.
            self.conn.execute("DROP TABLE IF EXISTS plays")
            log.info("Dropped the recently-played ledger; history is measured listens only")

        if version == 2:
            # Same reasoning, applied to rows an earlier build backfilled.
            self.conn.execute("DELETE FROM listens WHERE source <> 'live'")
            for column in ("source", "history_played_at"):
                if column in self._columns("listens"):
                    self.conn.execute(f"ALTER TABLE listens DROP COLUMN {column}")
            self.conn.execute("DROP INDEX IF EXISTS idx_listens_history_played")

        if 0 < version < SCHEMA_VERSION:
            self._rebuild_play_counts()
            self.conn.execute("UPDATE listens SET completion_ratio = 0 WHERE completion_ratio IS NULL")

        # `recently_played_after` tracked how far the sweep had read. There is no sweep.
        if "recently_played_after" in self._columns("cursors"):
            try:
                self.conn.execute("ALTER TABLE cursors DROP COLUMN recently_played_after")
            except sqlite3.OperationalError as exc:  # pragma: no cover - old SQLite
                log.debug("Left recently_played_after in place: %s", exc)

        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def _rebuild_play_counts(self) -> None:
        """Recompute the per-track tally from the history it summarises.

        The counter and the history have to tell the same story -- a tally inherited
        from a schema that counted different things would disagree with every row a user
        can actually see.
        """
        # Dropped rather than emptied: an older schema had different columns here.
        self.conn.execute("DROP TABLE IF EXISTS play_counts")
        self.conn.executescript(PLAY_COUNTS_DDL)
        self.conn.execute(
            """
            INSERT INTO play_counts
                (user_id, track_id, name, artist, qualified_plays, total_plays, last_played)
            SELECT user_id, track_id, name, artist,
                   SUM(qualified), COUNT(*), MAX(played_at)
              FROM listens
             WHERE is_open = 0
             GROUP BY user_id, track_id
            """
        )
        log.info("Rebuilt play counts from the listen history")

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
                "INSERT INTO cursors (user_id, last_source_sweep) VALUES (?, 0)",
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
        for key in JSON_SETTINGS:
            try:
                data[key] = json.loads(data[key])
            except (json.JSONDecodeError, TypeError):
                data[key] = []
        return data

    def update_settings(self, user_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        parts, values = [], []
        for key, value in updates.items():
            if key not in SETTINGS_COLUMNS:
                continue
            if key in BOOLEAN_SETTINGS:
                value = 1 if value else 0
            elif key in JSON_SETTINGS:
                value = json.dumps(value)
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

    # --------------------------------------------------------------- listens

    def _bump_counts(
        self,
        user_id: int,
        track_id: str,
        name: str,
        artist: str,
        played_at: int,
        qualified: bool,
    ) -> int:
        """Fold one listen into the per-track tally. Caller holds the lock.

        `play_counts` is a materialised aggregate of `listens`. It could be a GROUP BY,
        but the browser polls the state endpoint every few seconds and the history is
        the one table designed to grow forever -- so the tally is kept, not recomputed.
        """
        self.conn.execute(
            """
            INSERT INTO play_counts
                (user_id, track_id, name, artist, qualified_plays, total_plays, last_played)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id, track_id) DO UPDATE SET
                name            = excluded.name,
                artist          = excluded.artist,
                qualified_plays = play_counts.qualified_plays + excluded.qualified_plays,
                total_plays     = play_counts.total_plays + 1,
                last_played     = MAX(play_counts.last_played, excluded.last_played)
            """,
            (user_id, track_id, name, artist, 1 if qualified else 0, played_at),
        )
        row = self.conn.execute(
            "SELECT qualified_plays FROM play_counts WHERE user_id = ? AND track_id = ?",
            (user_id, track_id),
        ).fetchone()
        return int(row["qualified_plays"])

    def open_listen(
        self,
        user_id: int,
        track_id: str,
        name: str,
        artist: str,
        played_at: int,
        duration_ms: int,
        context_uri: Optional[str],
    ) -> int:
        """Start a row for a playback in progress, so a restart doesn't lose it."""
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT INTO listens
                    (user_id, track_id, name, artist, played_at, duration_ms, listened_ms,
                     completion_ratio, qualified, context_uri, is_open)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, 1)
                """,
                (user_id, track_id, name, artist, played_at, duration_ms, context_uri),
            )
            self.conn.commit()
        return int(cursor.lastrowid)

    def update_open_listen(
        self, row_id: int, listened_ms: int, completion_ratio: float, duration_ms: int
    ) -> None:
        with self.lock:
            self.conn.execute(
                """
                UPDATE listens
                   SET listened_ms = ?, completion_ratio = ?, duration_ms = ?
                 WHERE id = ? AND is_open = 1
                """,
                (listened_ms, completion_ratio, duration_ms, row_id),
            )
            self.conn.commit()

    def close_listen(
        self,
        row_id: int,
        user_id: int,
        track_id: str,
        name: str,
        artist: str,
        played_at: int,
        duration_ms: int,
        listened_ms: int,
        completion_ratio: float,
        qualified: bool,
    ) -> int:
        """Finish a measured listen and return the track's qualified-play count.

        Both writes happen under one lock and one commit, so a listen can never be
        recorded without its tally moving, or the other way round.
        """
        with self.lock:
            self.conn.execute(
                """
                UPDATE listens
                   SET name = ?, artist = ?, played_at = ?, duration_ms = ?, listened_ms = ?,
                       completion_ratio = ?, qualified = ?, is_open = 0
                 WHERE id = ?
                """,
                (
                    name,
                    artist,
                    played_at,
                    duration_ms,
                    listened_ms,
                    completion_ratio,
                    1 if qualified else 0,
                    row_id,
                ),
            )
            count = self._bump_counts(user_id, track_id, name, artist, played_at, qualified)
            self.conn.commit()
        return count

    def close_orphaned_listens(self) -> None:
        """Close rows left open by a process that stopped mid-track.

        What was mirrored to the row before the lights went out is a real measurement of
        audio heard -- a floor, not a guess, since the rest simply went unobserved. So a
        listen that had already cleared the threshold still counts; one that hadn't is
        recorded at what it reached.
        """
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT l.id, l.user_id, l.track_id, l.name, l.artist, l.played_at,
                       l.completion_ratio, s.min_completion_ratio AS threshold
                  FROM listens l
                  JOIN settings s ON s.user_id = l.user_id
                 WHERE l.is_open = 1
                """
            ).fetchall()
            for row in rows:
                qualified = float(row["completion_ratio"] or 0) >= float(row["threshold"])
                self.conn.execute(
                    "UPDATE listens SET is_open = 0, qualified = ? WHERE id = ?",
                    (1 if qualified else 0, row["id"]),
                )
                self._bump_counts(
                    int(row["user_id"]),
                    str(row["track_id"]),
                    str(row["name"]),
                    str(row["artist"]),
                    int(row["played_at"]),
                    qualified=qualified,
                )
            self.conn.commit()
        if rows:
            log.info("Closed %s listen(s) interrupted by a restart", len(rows))

    # --------------------------------------------------------------- history

    def history(
        self,
        user_id: int,
        *,
        query: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        qualified: Optional[bool] = None,
        favorites_only: Optional[bool] = None,
        favorite_track_ids: Optional[set[str]] = None,
        sort: str = "time",
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """One page of listening history.

        Sorted by the chosen column (default played_at). Paged by keyset: the cursor
        carries the last row's sort-column value plus its id.
        """
        limit = max(1, min(int(limit), HISTORY_PAGE_LIMIT))

        sort_cols: dict[str, tuple[str, str]] = {
            "time": ("l.played_at", "played_at"),
            "name": ("l.name", "name"),
            "artist": ("l.artist", "artist"),
            "length": ("l.duration_ms", "duration_ms"),
            "completion": ("l.completion_ratio", "completion_ratio"),
        }
        col_sql, col_name = sort_cols.get(sort, sort_cols["time"])

        joins = ""
        extra_cols = ""
        where = ["l.user_id = ?"]
        params: list[Any] = [user_id]

        if extra_cols or True:
            # Always join play_counts for the per-track tally shown in the UI.
            joins += " LEFT JOIN play_counts pc ON pc.user_id = l.user_id AND pc.track_id = l.track_id"
            extra_cols += ", COALESCE(pc.qualified_plays, 0) AS play_count"

        if query and query.strip():
            if self.fts:
                match = fts_query(query)
                if match:
                    joins += " JOIN listens_fts ON listens_fts.rowid = l.id"
                    where.append("listens_fts MATCH ?")
                    params.append(match)
            else:
                where.append("(l.name LIKE ? OR l.artist LIKE ?)")
                params += [f"%{query.strip()}%"] * 2
        if start is not None:
            where.append("l.played_at >= ?")
            params.append(int(start))
        if end is not None:
            where.append("l.played_at <= ?")
            params.append(int(end))
        if qualified is not None:
            where.append("l.qualified = ?")
            params.append(1 if qualified else 0)
        if favorites_only and favorite_track_ids:
            placeholders = ",".join("?" * len(favorite_track_ids))
            where.append(f"l.track_id IN ({placeholders})")
            params.extend(favorite_track_ids)
        if cursor is not None:
            parts = cursor.split("|", 2)
            if len(parts) == 3 and parts[0] == sort:
                cur_val, cur_id = parts[1], int(parts[2])
                if sort == "time" or sort == "length" or sort == "completion":
                    cur_val = float(cur_val)
                else:
                    cur_val = urllib.parse.unquote(cur_val)
                where.append(f"({col_sql} < ? OR ({col_sql} = ? AND l.id < ?))")
                params += [cur_val, cur_val, cur_id]

        sql = f"""
            SELECT l.id, l.track_id, l.name, l.artist, l.played_at, l.duration_ms,
                   l.listened_ms, l.completion_ratio, l.qualified, l.is_open{extra_cols}
              FROM listens l {joins}
             WHERE {' AND '.join(where)}
             ORDER BY {col_sql} DESC, l.id DESC
             LIMIT ?
        """
        with self.lock:
            rows = self.conn.execute(sql, (*params, limit + 1)).fetchall()

        items = [dict(row) for row in rows[:limit]]
        for item in items:
            item["qualified"] = bool(item["qualified"])
            item["is_open"] = bool(item["is_open"])

        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            raw = last[col_name]
            encoded = (
                urllib.parse.quote(str(raw), safe="")
                if isinstance(raw, str)
                else str(raw)
            )
            next_cursor = f"{sort}|{encoded}|{last['id']}"
        return {"items": items, "next_cursor": next_cursor}

    def history_summary(self, user_id: int) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS listens,
                       COALESCE(SUM(qualified), 0) AS qualified,
                       MIN(played_at) AS first_played,
                       MAX(played_at) AS last_played
                  FROM listens WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            tracks = self.conn.execute(
                "SELECT COUNT(*) AS n FROM play_counts WHERE user_id = ?", (user_id,)
            ).fetchone()
        return {
            "listens": int(row["listens"]),
            "qualified": int(row["qualified"]),
            "tracks": int(tracks["n"]),
            "first_played": row["first_played"],
            "last_played": row["last_played"],
        }

    def count_listens_since(self, user_id: int, since_ms: int) -> dict[str, int]:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(qualified), 0) AS qualified
                  FROM listens WHERE user_id = ? AND played_at >= ?
                """,
                (user_id, since_ms),
            ).fetchone()
        return {"total": int(row["total"]), "qualified": int(row["qualified"])}

    def play_counts(self, user_id: int) -> dict[str, dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT track_id, name, artist, qualified_plays, total_plays, last_played
                FROM play_counts WHERE user_id = ?
                """,
                (user_id,),
            ).fetchall()
        return {str(row["track_id"]): dict(row) for row in rows}

    def next_favorite_candidate(self, user_id: int, threshold: int) -> Optional[dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT track_id, name, artist, qualified_plays, total_plays, last_played
                FROM play_counts
                WHERE user_id = ? AND qualified_plays < ?
                ORDER BY qualified_plays DESC, last_played DESC
                LIMIT 1
                """,
                (user_id, threshold),
            ).fetchone()
        return dict(row) if row else None

    def _distinct_days(self, user_id: int) -> list[str]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT DISTINCT DATE(played_at / 1000, 'unixepoch', 'localtime') AS day
                  FROM listens WHERE user_id = ? AND is_open = 0
                 ORDER BY day DESC
                """,
                (user_id,),
            ).fetchall()
        return [row["day"] for row in rows]

    @staticmethod
    def _compute_streaks(days: list[str]) -> tuple[int, int]:
        if not days:
            return 0, 0

        from datetime import date, timedelta

        today = date.today().isoformat()
        dates = {date.fromisoformat(d) for d in days}
        min_date = min(dates)

        current = 0
        d = date.today()
        while d >= min_date:
            if d in dates:
                current += 1
                d -= timedelta(days=1)
            else:
                break

        longest = 0
        run = 0
        all_dates = sorted(dates, reverse=True)
        prev: Optional[date] = None
        for d in all_dates:
            if prev is not None and (prev - d).days == 1:
                run += 1
            else:
                run = 1
            longest = max(longest, run)
            prev = d

        return current, longest

    def get_all_stats(self, user_id: int, threshold: int) -> dict[str, Any]:
        with self.lock:
            # Counted in 24h
            row_24h = self.conn.execute(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(qualified), 0) AS qualified
                  FROM listens WHERE user_id = ? AND played_at >= ?
                """,
                (user_id, now_millis() - 86_400_000),
            ).fetchone()

            # Total listens + listening time + avg completion
            row_listens = self.conn.execute(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(listened_ms), 0) AS total_ms,
                       COALESCE(AVG(completion_ratio), 0) AS avg_completion
                  FROM listens WHERE user_id = ? AND is_open = 0
                """,
                (user_id,),
            ).fetchone()

            # Tracks seen
            row_tracks = self.conn.execute(
                "SELECT COUNT(*) AS n FROM play_counts WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            # Favorites count
            row_favs = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM play_counts
                 WHERE user_id = ? AND qualified_plays >= ?
                """,
                (user_id, threshold),
            ).fetchone()

            # Top artist
            row_artist = self.conn.execute(
                """
                SELECT artist, SUM(qualified_plays) AS plays
                  FROM play_counts WHERE user_id = ?
                 GROUP BY artist ORDER BY plays DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            # Next favorite
            row_next = self.conn.execute(
                """
                SELECT track_id, name, artist, qualified_plays
                  FROM play_counts
                 WHERE user_id = ? AND qualified_plays < ?
                 ORDER BY qualified_plays DESC, last_played DESC
                 LIMIT 1
                """,
                (user_id, threshold),
            ).fetchone()

            # Peak hour
            row_hour = self.conn.execute(
                """
                SELECT CAST(STRFTIME('%H', played_at / 1000, 'unixepoch', 'localtime') AS INTEGER) AS h,
                       COUNT(*) AS n
                  FROM listens WHERE user_id = ?
                 GROUP BY h ORDER BY n DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            # Avg daily hours, last 7 days
            row_avg = self.conn.execute(
                """
                SELECT COALESCE(SUM(listened_ms) / 7.0 / 3600000.0, 0) AS avg_hrs
                  FROM listens WHERE user_id = ? AND is_open = 0
                   AND played_at >= ?
                """,
                (user_id, now_millis() - 7 * 86_400_000),
            ).fetchone()

        # Streaks (outside lock b/c it uses distinct_days which acquires its own)
        days = self._distinct_days(user_id)
        current_streak, longest_streak = self._compute_streaks(days)

        total_ms = int(row_listens["total_ms"]) if row_listens else 0
        hours = total_ms // 3_600_000
        mins = (total_ms % 3_600_000) // 60_000

        result: list[dict[str, Any]] = []

        total_24h = int(row_24h["total"])
        qualified_24h = int(row_24h["qualified"])
        result.append({
            "id": "counted_24h",
            "label": "Counted Today",
            "value": str(qualified_24h),
            "subtitle": f"of {total_24h} played in last 24h",
        })

        total_listens_n = int(row_listens["total"]) if row_listens else 0

        if row_next:
            result.append({
                "id": "next_favorite",
                "label": "Next Favorite",
                "value": str(row_next["name"]),
                "subtitle": f"{threshold - int(row_next['qualified_plays'])} plays to go · {row_next['artist']}",
            })

        result.append({
            "id": "total_listens",
            "label": "All-Time Plays",
            "value": f"{total_listens_n:,}",
            "subtitle": "",
        })

        result.append({
            "id": "listening_time",
            "label": "Listening Time",
            "value": f"{hours}h {mins}m",
            "subtitle": "total tracked",
        })

        result.append({
            "id": "favorites_count",
            "label": "Favorites",
            "value": str(int(row_favs["n"])),
            "subtitle": "tracks over threshold",
        })

        if row_artist:
            result.append({
                "id": "top_artist",
                "label": "Top Artist",
                "value": str(row_artist["artist"]),
                "subtitle": f"{int(row_artist['plays'])} counted plays",
            })

        result.append({
            "id": "current_streak",
            "label": "Current Streak",
            "value": f"{current_streak} day{'s' if current_streak != 1 else ''}",
            "subtitle": "consecutive days with a listen",
        })

        result.append({
            "id": "longest_streak",
            "label": "Longest Streak",
            "value": f"{longest_streak} day{'s' if longest_streak != 1 else ''}",
            "subtitle": "all-time best",
        })

        if row_hour:
            result.append({
                "id": "peak_hour",
                "label": "Peak Hour",
                "value": f"{int(row_hour['h'])}:00",
                "subtitle": "most active hour",
            })

        avg_pct = round((float(row_listens["avg_completion"]) if row_listens else 0) * 100)
        result.append({
            "id": "avg_completion",
            "label": "Avg Completion",
            "value": f"{avg_pct}%",
            "subtitle": "mean listen-through",
        })

        avg_hrs = round(float(row_avg["avg_hrs"]) if row_avg else 0, 1)
        result.append({
            "id": "avg_daily_last_week",
            "label": "Daily Avg (7d)",
            "value": f"{avg_hrs}h",
            "subtitle": "average daily listening, last week",
        })

        return result

    # ------------------------------------------------------------- discovery

    def discovery_sources(self, user_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT playlist_id, label, degraded FROM discovery_sources
                WHERE user_id = ? ORDER BY created_at
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_source_degraded(
        self, user_id: int, playlist_id: str, error: Optional[str]
    ) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE discovery_sources SET degraded = ? WHERE user_id = ? AND playlist_id = ?",
                (error, user_id, playlist_id),
            )
            self.conn.commit()

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
        self,
        user_id: int,
        playlist_id: str,
        sample_track: str,
        title: Optional[str] = None,
    ) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO seen_contexts
                    (user_id, playlist_id, last_seen, sample_track, title)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, playlist_id) DO UPDATE SET
                    last_seen    = excluded.last_seen,
                    sample_track = excluded.sample_track,
                    title        = COALESCE(excluded.title, seen_contexts.title),
                    play_count   = seen_contexts.play_count + 1
                """,
                (user_id, playlist_id, now_seconds(), sample_track, title),
            )
            self.conn.commit()

    def unlabelled_contexts(self, user_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT playlist_id, sample_track, title, play_count, last_seen
                FROM seen_contexts
                WHERE user_id = ? AND dismissed = 0
                ORDER BY play_count DESC, last_seen DESC
                LIMIT 5
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_source_sweep(self, user_id: int) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT last_source_sweep FROM cursors WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row["last_source_sweep"]) if row else 0

    def set_last_source_sweep(self, user_id: int, value: int) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO cursors (user_id, last_source_sweep) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_source_sweep = excluded.last_source_sweep
                """,
                (user_id, value),
            )
            self.conn.commit()

    # ------------------------------------------------------- AI blocklist

    def record_blocked(
        self,
        user_id: int,
        month: str,
        track_id: str,
        name: str,
        artist: str,
        reason: str,
    ) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO discovery_blocked
                    (user_id, month, track_id, name, artist, reason, blocked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, month, track_id, name, artist, reason, now_seconds()),
            )
            self.conn.commit()

    def is_seen_this_month(self, user_id: int, month: str, track_id: str) -> bool:
        """Already archived or already blocked -- either way, don't reprocess it.

        Blocked tracks count here so a filtered track doesn't cost an API lookup on
        every single sweep for the rest of the month.
        """
        with self.lock:
            row = self.conn.execute(
                """
                SELECT 1 FROM discovery_archive
                 WHERE user_id = ? AND month = ? AND track_id = ?
                UNION ALL
                SELECT 1 FROM discovery_blocked
                 WHERE user_id = ? AND month = ? AND track_id = ?
                 LIMIT 1
                """,
                (user_id, month, track_id, user_id, month, track_id),
            ).fetchone()
        return row is not None

    def blocked_month(self, user_id: int, month: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT track_id, name, artist, reason, blocked_at FROM discovery_blocked
                WHERE user_id = ? AND month = ? ORDER BY blocked_at DESC
                """,
                (user_id, month),
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
