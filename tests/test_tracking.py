"""The invariants the whole design rests on: no play missed, no play counted twice."""

from datetime import timedelta

from app import discovery as discovery_mod
from app.tracker import parse_played_at

from conftest import play


def test_sweep_counts_each_play_once(tracker, spotify, db, user_id, now):
    spotify.history = [play("t1", now), play("t2", now + timedelta(minutes=1))]

    assert tracker._sweep(spotify) == 2
    counts = db.play_counts(user_id)
    assert counts["t1"]["occurrences"] == 1
    assert counts["t2"]["occurrences"] == 1


def test_resweeping_the_same_window_adds_nothing(tracker, spotify, db, user_id, now):
    """The cursor deliberately rewinds 5s each sweep; the UNIQUE constraint absorbs it."""
    spotify.history = [play("t1", now)]
    tracker._sweep(spotify)

    for _ in range(5):
        assert tracker._sweep(spotify) == 0

    assert db.play_counts(user_id)["t1"]["occurrences"] == 1


def test_same_track_at_different_times_counts_twice(tracker, spotify, db, user_id, now):
    spotify.history = [play("t1", now), play("t1", now + timedelta(minutes=4))]
    tracker._sweep(spotify)
    assert db.play_counts(user_id)["t1"]["occurrences"] == 2


def test_plays_during_downtime_land_on_the_next_sweep(tracker, spotify, db, user_id, now):
    """The case progress-polling could never recover: the app was simply not running."""
    spotify.history = [play("t1", now)]
    tracker._sweep(spotify)

    # ... container down here; three tracks played ...
    spotify.history += [
        play("t2", now + timedelta(minutes=3)),
        play("t3", now + timedelta(minutes=6)),
        play("t4", now + timedelta(minutes=9)),
    ]

    assert tracker._sweep(spotify) == 3
    assert set(db.play_counts(user_id)) == {"t1", "t2", "t3", "t4"}


def test_cursor_only_moves_forward(tracker, spotify, db, user_id, now):
    spotify.history = [play("t1", now + timedelta(minutes=10))]
    tracker._sweep(spotify)
    high_water = db.cursor_after(user_id)

    db.set_cursor_after(user_id, high_water - 60_000)
    assert db.cursor_after(user_id) == high_water


def test_auto_add_fires_at_the_threshold(tracker, spotify, db, user_id, now):
    db.update_settings(user_id, {"favorite_threshold": 3})
    spotify.history = [play("t1", now + timedelta(minutes=i)) for i in range(3)]

    tracker._sweep(spotify)

    playlist = next(iter(spotify.playlists.values()))
    assert playlist["name"] == "Favourite Songs"
    assert playlist["tracks"] == ["t1"]


def test_below_the_threshold_nothing_is_written(tracker, spotify, db, user_id, now):
    db.update_settings(user_id, {"favorite_threshold": 3})
    spotify.history = [play("t1", now), play("t1", now + timedelta(minutes=4))]

    tracker._sweep(spotify)
    assert spotify.playlists == {}


def test_extra_plays_do_not_duplicate_the_playlist_entry(tracker, spotify, db, user_id, now):
    db.update_settings(user_id, {"favorite_threshold": 2})
    spotify.history = [play("t1", now + timedelta(minutes=i)) for i in range(6)]

    tracker._sweep(spotify)

    playlist = next(iter(spotify.playlists.values()))
    assert playlist["tracks"] == ["t1"]


def test_auto_add_off_writes_nothing(tracker, spotify, db, user_id, now):
    db.update_settings(user_id, {"favorite_threshold": 2, "auto_add_enabled": False})
    spotify.history = [play("t1", now), play("t1", now + timedelta(minutes=4))]

    tracker._sweep(spotify)
    assert spotify.playlists == {}


def test_existing_playlist_is_reused_not_duplicated(tracker, spotify, db, user_id, now):
    """Needs playlist-read-private in the real world, or this silently creates a second one."""
    spotify.playlists["existing"] = {"name": "Favourite Songs", "public": False, "tracks": []}
    db.update_settings(user_id, {"favorite_threshold": 1})
    spotify.history = [play("t1", now)]

    tracker._sweep(spotify)

    assert len(spotify.playlists) == 1
    assert spotify.playlists["existing"]["tracks"] == ["t1"]


def test_reconcile_catches_up_after_the_threshold_is_lowered(tracker, spotify, db, user_id, now):
    db.update_settings(user_id, {"favorite_threshold": 10})
    spotify.history = [play("t1", now + timedelta(minutes=i)) for i in range(4)]
    tracker._sweep(spotify)
    assert spotify.playlists == {}

    db.update_settings(user_id, {"favorite_threshold": 3})
    assert tracker.reconcile_favorites() == 1
    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]


def test_parse_played_at_round_trips_utc():
    assert parse_played_at("2026-01-15T08:30:00.000Z") == 1768465800000
    assert parse_played_at("2026-01-15T08:30:00.500Z") == 1768465800500
