"""The listening history: the one table meant to grow forever.

Everything here is about the queries staying cheap and exact as it does -- keyset paging
that doesn't walk what came before, and a search that doesn't scan.
"""

import sqlite3

import pytest

from app.db import SCHEMA_VERSION, Database, fts_query

FERNET_KEY = b"cGxhY2Vob2xkZXJfa2V5X2Zvcl90ZXN0c19vbmx5ISE="

BASE = 1_800_000_000_000
DAY = 86_400_000


def add(db, user_id, track_id, name, artist, played_at, qualified=True):
    row_id = db.open_listen(user_id, track_id, name, artist, played_at, 200_000, None)
    db.close_listen(
        row_id=row_id,
        user_id=user_id,
        track_id=track_id,
        name=name,
        artist=artist,
        played_at=played_at,
        duration_ms=200_000,
        listened_ms=200_000 if qualified else 20_000,
        completion_ratio=1.0 if qualified else 0.1,
        qualified=qualified,
    )
    return row_id


@pytest.fixture
def stocked(db, user_id):
    add(db, user_id, "t1", "Weird Fishes", "Radiohead", BASE)
    add(db, user_id, "t2", "Roygbiv", "Boards of Canada", BASE + DAY, qualified=False)
    add(db, user_id, "t3", "Svefn-g-englar", "Sigur Rós", BASE + 2 * DAY)
    return db


# ----------------------------------------------------------------- filtering


def test_history_is_newest_first(stocked, user_id):
    assert [row["name"] for row in stocked.history(user_id)["items"]] == [
        "Svefn-g-englar",
        "Roygbiv",
        "Weird Fishes",
    ]


def test_a_date_range_bounds_both_ends(stocked, user_id):
    page = stocked.history(user_id, start=BASE + DAY, end=BASE + DAY)
    assert [row["name"] for row in page["items"]] == ["Roygbiv"]


def test_filtering_by_whether_it_counted(stocked, user_id):
    counted = stocked.history(user_id, qualified=True)["items"]
    skipped = stocked.history(user_id, qualified=False)["items"]

    assert {row["name"] for row in counted} == {"Weird Fishes", "Svefn-g-englar"}
    assert [row["name"] for row in skipped] == ["Roygbiv"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("radio", {"Weird Fishes"}),  # artist, prefix
        ("fish", {"Weird Fishes"}),  # title, prefix
        ("boards canada", {"Roygbiv"}),  # several terms
        ("sigur ros", {"Svefn-g-englar"}),  # ó typed without the accent
        ("svefn", {"Svefn-g-englar"}),  # hyphenated title
        ("nothing here", set()),
    ],
)
def test_search_matches_title_and_artist(stocked, user_id, query, expected):
    assert {row["name"] for row in stocked.history(user_id, query=query)["items"]} == expected


def test_search_syntax_cannot_leak_out_of_the_query(stocked, user_id):
    """A search box takes what someone types, including FTS5 operators."""
    for hostile in ('" OR 1=1 --', "NEAR(a b)", "*", "^", "col:value", ""):
        assert stocked.history(user_id, query=hostile)["items"] is not None


def test_filters_combine(stocked, user_id):
    page = stocked.history(user_id, query="radio", qualified=True, start=BASE - DAY)
    assert [row["name"] for row in page["items"]] == ["Weird Fishes"]


# -------------------------------------------------------------------- paging


def test_paging_walks_every_row_exactly_once(db, user_id):
    for i in range(50):
        add(db, user_id, f"t{i}", f"Song {i}", "Artist", BASE + i * 1000)

    seen, cursor = [], None
    while True:
        page = db.history(user_id, limit=7, cursor=cursor)
        seen += [row["id"] for row in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
        cursor = tuple(int(part) for part in cursor.split("_"))

    assert len(seen) == 50
    assert len(set(seen)) == 50


def test_rows_sharing_a_timestamp_still_page_cleanly(db, user_id):
    """played_at alone isn't unique, so the cursor carries the row id as a tiebreak."""
    for i in range(6):
        add(db, user_id, f"t{i}", f"Song {i}", "Artist", BASE)

    page = db.history(user_id, limit=3)
    cursor = tuple(int(part) for part in page["next_cursor"].split("_"))
    rest = db.history(user_id, limit=10, cursor=cursor)

    ids = [row["id"] for row in page["items"]] + [row["id"] for row in rest["items"]]
    assert sorted(ids) == sorted(row["id"] for row in db.history(user_id, limit=100)["items"])
    assert len(ids) == 6


def test_the_last_page_has_no_cursor(stocked, user_id):
    assert stocked.history(user_id, limit=50)["next_cursor"] is None


def test_page_size_is_capped(db, user_id):
    for i in range(5):
        add(db, user_id, f"t{i}", f"Song {i}", "Artist", BASE + i)
    assert len(db.history(user_id, limit=10_000)["items"]) == 5


def test_a_page_does_not_scan_what_came_before_it(db, user_id):
    """Keyset paging, not OFFSET: the query plan has to be an index seek, or a year of
    history makes the last page cost the most."""
    add(db, user_id, "t1", "Song", "Artist", BASE)
    plan = db.conn.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT l.id FROM listens l
         WHERE l.user_id = ? AND (l.played_at < ? OR (l.played_at = ? AND l.id < ?))
         ORDER BY l.played_at DESC, l.id DESC LIMIT 50
        """,
        (user_id, BASE, BASE, 1),
    ).fetchall()
    detail = " ".join(row["detail"] for row in plan)

    assert "idx_listens_user_time" in detail
    assert "SCAN" not in detail
    assert "TEMP B-TREE" not in detail  # i.e. no sort


# ------------------------------------------------------------------ summary


def test_summary_counts_what_the_header_shows(stocked, user_id):
    assert stocked.history_summary(user_id) == {
        "listens": 3,
        "qualified": 2,
        "tracks": 3,
        "first_played": BASE,
        "last_played": BASE + 2 * DAY,
    }


def test_summary_of_an_empty_history(db, user_id):
    assert db.history_summary(user_id) == {
        "listens": 0,
        "qualified": 0,
        "tracks": 0,
        "first_played": None,
        "last_played": None,
    }


# ------------------------------------------------------------------ fts5


def test_fts_query_builds_prefix_terms():
    assert fts_query("boards canada") == '"boards"* "canada"*'
    assert fts_query('  " * ^ ') == ""


def test_search_still_works_without_fts5(db, user_id, monkeypatch):
    """Not every SQLite build ships FTS5. Losing search entirely would be worse than
    losing the index behind it."""
    add(db, user_id, "t1", "Weird Fishes", "Radiohead", BASE)
    monkeypatch.setattr(db, "fts", False)

    assert [row["name"] for row in db.history(user_id, query="Fish")["items"]] == ["Weird Fishes"]
    assert db.history(user_id, query="nope")["items"] == []


def test_deleting_a_user_leaves_no_orphans_in_the_index(db, user_id, tmp_path):
    add(db, user_id, "t1", "Weird Fishes", "Radiohead", BASE)
    db.delete_user(user_id)

    other = db.upsert_user("someone-else", "Other")
    assert db.history(other, query="fish")["items"] == []


# ----------------------------------------------------------------- migration


OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, spotify_user_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE settings (user_id INTEGER PRIMARY KEY, favorite_threshold INTEGER NOT NULL DEFAULT 5,
                    min_completion_ratio REAL NOT NULL DEFAULT 0.8, poll_interval INTEGER NOT NULL DEFAULT 30,
                    playlist_name TEXT NOT NULL, playlist_public INTEGER NOT NULL DEFAULT 0,
                    auto_add_enabled INTEGER NOT NULL DEFAULT 1, favorites_playlist_id TEXT,
                    discovery_enabled INTEGER NOT NULL DEFAULT 1, tracker_running INTEGER NOT NULL DEFAULT 1);
CREATE TABLE plays (user_id INTEGER NOT NULL, track_id TEXT NOT NULL, played_at INTEGER NOT NULL,
                    name TEXT NOT NULL, artist TEXT NOT NULL, context_uri TEXT,
                    UNIQUE(user_id, track_id, played_at));
CREATE TABLE play_counts (user_id INTEGER NOT NULL, track_id TEXT NOT NULL, name TEXT NOT NULL,
                    artist TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 0,
                    last_played INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, track_id));
CREATE TABLE cursors (user_id INTEGER PRIMARY KEY, recently_played_after INTEGER NOT NULL DEFAULT 0);
CREATE TABLE discovery_sources (user_id INTEGER NOT NULL, playlist_id TEXT NOT NULL, label TEXT NOT NULL,
                    created_at INTEGER NOT NULL, PRIMARY KEY(user_id, playlist_id));
CREATE TABLE seen_contexts (user_id INTEGER NOT NULL, playlist_id TEXT NOT NULL, last_seen INTEGER NOT NULL,
                    sample_track TEXT NOT NULL, play_count INTEGER NOT NULL DEFAULT 1,
                    dismissed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, playlist_id));
CREATE TABLE discovery_blocked (user_id INTEGER NOT NULL, month TEXT NOT NULL, track_id TEXT NOT NULL,
                    name TEXT NOT NULL, artist TEXT NOT NULL, reason TEXT NOT NULL,
                    blocked_at INTEGER NOT NULL, PRIMARY KEY(user_id, month, track_id));
CREATE TABLE discovery_archive (user_id INTEGER NOT NULL, month TEXT NOT NULL, track_id TEXT NOT NULL,
                    name TEXT NOT NULL, artist TEXT NOT NULL, source_id TEXT NOT NULL,
                    added_at INTEGER NOT NULL, PRIMARY KEY(user_id, month, track_id));
CREATE TABLE discovery_playlists (user_id INTEGER NOT NULL, month TEXT NOT NULL, playlist_id TEXT NOT NULL,
                    PRIMARY KEY(user_id, month));
"""


@pytest.fixture
def legacy_db_path(tmp_path):
    """A database as the previous version left it: a 30-day `plays` ledger and counts
    that meant every play, not every play that was heard."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO users VALUES (1, 'u1', 'Nick', 1000)")
    conn.execute("INSERT INTO settings (user_id, playlist_name) VALUES (1, 'Favourite Songs')")
    conn.execute("INSERT INTO cursors VALUES (1, ?)", (BASE + 99,))
    for i in range(9):
        conn.execute(
            "INSERT INTO plays VALUES (1, ?, ?, ?, ?, NULL)",
            (f"t{i % 3}", BASE + i * 60_000, f"Song {i % 3}", "Someone"),
        )
    conn.execute("INSERT INTO play_counts VALUES (1, 't0', 'Song 0', 'Someone', 4, ?)", (BASE,))
    conn.commit()
    conn.close()
    return path


def test_v1_ledger_is_dropped_rather_than_imported(legacy_db_path):
    """`plays` recorded that a track played, never how much was heard. Importing it
    would put rows in the history that no measurement stands behind, which is exactly
    the second source of truth this schema exists to remove."""
    db = Database(legacy_db_path, FERNET_KEY, "Favourite Songs")
    try:
        assert db.history_summary(1)["listens"] == 0
        assert not db._table_exists("plays")
    finally:
        db.close()


def test_v1_counts_are_rebuilt_from_the_history_not_inherited(legacy_db_path):
    """The tally is an aggregate of `listens`. Carrying over a count of unmeasured plays
    would leave the counter claiming plays no row in the history can show."""
    db = Database(legacy_db_path, FERNET_KEY, "Favourite Songs")
    try:
        assert db.play_counts(1) == {}
    finally:
        db.close()


def test_the_schema_version_is_recorded(legacy_db_path):
    db = Database(legacy_db_path, FERNET_KEY, "Favourite Songs")
    try:
        row = db.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(row["value"]) == SCHEMA_VERSION
    finally:
        db.close()


def test_migration_runs_once_and_is_safe_to_repeat(legacy_db_path):
    for _ in range(3):
        db = Database(legacy_db_path, FERNET_KEY, "Favourite Songs")
        try:
            assert db.history_summary(1)["listens"] == 0
            assert db.play_counts(1) == {}
        finally:
            db.close()


def test_a_fresh_database_needs_no_migration(db, user_id):
    row = db.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert int(row["value"]) == SCHEMA_VERSION
    assert not db._table_exists("plays")


# --- from the intermediate schema, which mixed measured and backfilled rows ---


@pytest.fixture
def v2_db_path(tmp_path):
    """A database from the build that recorded Spotify's history alongside measured
    listens, tagged by `source`."""
    path = str(tmp_path / "v2.db")
    db = Database(path, FERNET_KEY, "Favourite Songs")
    user_id = db.upsert_user("u1", "Nick")
    add(db, user_id, "t1", "Measured", "Artist", BASE)
    add(db, user_id, "t2", "Also Measured", "Artist", BASE + 1000, qualified=False)
    db.conn.execute("ALTER TABLE listens ADD COLUMN source TEXT NOT NULL DEFAULT 'live'")
    db.conn.execute("ALTER TABLE listens ADD COLUMN history_played_at INTEGER")
    db.conn.execute(
        """
        INSERT INTO listens (user_id, track_id, name, artist, played_at, duration_ms,
                             listened_ms, completion_ratio, qualified, is_open, source)
        VALUES (?, 't3', 'Backfilled', 'Artist', ?, 0, 0, 0, 0, 0, 'history')
        """,
        (user_id, BASE + 2000),
    )
    db.conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    db.conn.commit()
    db.close()
    return path


def test_v2_backfilled_rows_are_removed_and_measured_ones_kept(v2_db_path):
    db = Database(v2_db_path, FERNET_KEY, "Favourite Songs")
    try:
        names = {row["name"] for row in db.history(1, limit=50)["items"]}
        assert names == {"Measured", "Also Measured"}
        assert "source" not in db._columns("listens")
        assert "history_played_at" not in db._columns("listens")
    finally:
        db.close()


def test_v2_counts_match_the_surviving_history(v2_db_path):
    db = Database(v2_db_path, FERNET_KEY, "Favourite Songs")
    try:
        counts = db.play_counts(1)
        assert set(counts) == {"t1", "t2"}
        assert counts["t1"]["qualified_plays"] == 1
        assert counts["t2"]["qualified_plays"] == 0
        assert db.history_summary(1)["qualified"] == 1
    finally:
        db.close()
