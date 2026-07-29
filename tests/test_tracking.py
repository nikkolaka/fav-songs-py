"""The invariants the whole design rests on.

Nothing reaches a playlist unless enough of it was actually heard, and there is exactly
one way a play can enter the history: the live poll measured it.
"""

from datetime import timedelta

from conftest import play, playback

TRACK_MS = 200_000


def counts(db, user_id, track_id):
    row = db.play_counts(user_id).get(track_id) or {}
    return row.get("qualified_plays", 0), row.get("total_plays", 0)


# --------------------------------------------------- the threshold gates it


def test_a_full_listen_counts(player, db, user_id):
    player.listen("t1")
    assert counts(db, user_id, "t1") == (1, 1)


def test_a_skipped_track_is_recorded_but_does_not_count(player, db, user_id):
    """The behaviour this whole change exists for: playing 20 seconds of something is
    not liking it."""
    player.listen("t1", heard_ms=20_000)

    assert counts(db, user_id, "t1") == (0, 1)
    entry = db.history(user_id)["items"][0]
    assert entry["qualified"] is False
    assert entry["completion_ratio"] < 0.2


def test_auto_add_fires_after_enough_full_listens(player, spotify, db, user_id):
    db.update_settings(user_id, {"favorite_threshold": 3})

    for _ in range(3):
        player.listen("t1")

    playlist = next(iter(spotify.playlists.values()))
    assert playlist["name"] == "Favourite Songs"
    assert playlist["tracks"] == ["t1"]


def test_skipping_the_same_track_forever_never_adds_it(player, spotify, db, user_id):
    db.update_settings(user_id, {"favorite_threshold": 2})

    for _ in range(8):
        player.listen("t1", heard_ms=20_000)

    assert spotify.playlists == {}
    assert counts(db, user_id, "t1") == (0, 8)


def test_the_threshold_is_a_setting_not_a_constant(player, spotify, db, user_id):
    """Half a track counts for someone who sets it to half."""
    db.update_settings(user_id, {"favorite_threshold": 1, "min_completion_ratio": 0.5})

    player.listen("t1", heard_ms=120_000, duration_ms=TRACK_MS)

    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]


def test_a_partial_listen_stays_out_when_the_threshold_is_raised(player, spotify, db, user_id):
    db.update_settings(user_id, {"favorite_threshold": 1, "min_completion_ratio": 0.95})

    player.listen("t1", heard_ms=140_000, duration_ms=TRACK_MS)

    assert spotify.playlists == {}


def test_auto_add_off_writes_nothing(player, spotify, db, user_id):
    db.update_settings(user_id, {"favorite_threshold": 1, "auto_add_enabled": False})
    player.listen("t1")
    assert spotify.playlists == {}


def test_extra_listens_do_not_duplicate_the_playlist_entry(player, spotify, db, user_id):
    db.update_settings(user_id, {"favorite_threshold": 2})
    for _ in range(6):
        player.listen("t1")
    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]


def test_existing_playlist_is_reused_not_duplicated(player, spotify, db, user_id):
    """Needs playlist-read-private in the real world, or this silently creates a second one."""
    spotify.playlists["existing"] = {"name": "Favourite Songs", "public": False, "tracks": []}
    db.update_settings(user_id, {"favorite_threshold": 1})

    player.listen("t1")

    assert len(spotify.playlists) == 1
    assert spotify.playlists["existing"]["tracks"] == ["t1"]


def test_reconcile_catches_up_after_the_threshold_is_lowered(player, spotify, db, user_id):
    db.update_settings(user_id, {"favorite_threshold": 10})
    for _ in range(4):
        player.listen("t1")
    assert spotify.playlists == {}

    db.update_settings(user_id, {"favorite_threshold": 3})
    assert player.tracker.reconcile_favorites() == 1
    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]


# ------------------------------------------------------------- open listens


def test_pausing_and_resuming_is_one_listen(player, db, user_id, clock):
    player.poll(playback("t1", 0, duration_ms=TRACK_MS))
    player.poll(playback("t1", 30_000, duration_ms=TRACK_MS), after_ms=30_000)
    player.poll(playback("t1", 30_000, is_playing=False, duration_ms=TRACK_MS), after_ms=30_000)
    player.poll(playback("t1", 60_000, duration_ms=TRACK_MS), after_ms=120_000)

    assert db.history(user_id)["items"][0]["is_open"] is True
    assert len(db.history(user_id)["items"]) == 1


def test_an_interrupted_listen_keeps_what_was_measured(player, db, user_id):
    """A restart mid-track closes the row at what it had reached. 30 seconds of a
    3-minute track is 15%, which is short of the threshold."""
    player.poll(playback("t1", 30_000, duration_ms=TRACK_MS))

    db.close_orphaned_listens()

    assert counts(db, user_id, "t1") == (0, 1)
    assert db.history(user_id)["items"][0]["is_open"] is False


def test_an_interrupted_listen_still_counts_if_it_had_already_earned_it(player, db, user_id):
    """What was mirrored to the row is a floor on the audio heard, not a guess -- the
    rest simply went unobserved. A listen already past the threshold has earned it."""
    player.poll(playback("t1", 0, duration_ms=TRACK_MS))
    player.poll(playback("t1", 180_000, duration_ms=TRACK_MS), after_ms=180_000)

    db.close_orphaned_listens()

    assert counts(db, user_id, "t1") == (1, 1)
    assert db.history(user_id)["items"][0]["qualified"] is True


def test_stopping_the_tracker_closes_the_open_listen(player, db, user_id):
    player.poll(playback("t1", 0, duration_ms=TRACK_MS))
    player.poll(playback("t1", 190_000, duration_ms=TRACK_MS), after_ms=190_000)

    player.tracker.flush()

    assert counts(db, user_id, "t1") == (1, 1)
    assert db.history(user_id)["items"][0]["is_open"] is False


# ------------------------------------------------ one way in, and one only


def test_the_history_has_a_single_writer(player, spotify, db, user_id):
    """Every row comes from the live poll. Nothing else may add to `listens`."""
    player.listen("t1", heard_ms=20_000)
    player.listen("t2")

    rows = db.history(user_id)["items"]
    assert [row["track_id"] for row in rows] == ["t2", "t1"]
    # Measured, both of them -- there is no such thing as an unrated row any more.
    assert all(row["completion_ratio"] is not None for row in rows)
    assert [row["qualified"] for row in rows] == [True, False]


def test_spotifys_own_history_is_never_read(player, spotify, db, user_id, now):
    """The recently-played endpoint would report plays we cannot measure, so the tracker
    must not call it at all -- not even to fill gaps."""
    spotify.history = [play("t9", now + timedelta(minutes=i)) for i in range(5)]

    for _ in range(3):
        player.poll(playback("t1", 0), after_ms=5_000)

    assert "recently_played" not in spotify.calls
    assert {row["track_id"] for row in db.history(user_id)["items"]} == {"t1"}


def test_nothing_is_replayed_when_a_tracker_starts(player, spotify, db, user_id, now):
    spotify.history = [play("t9", now)]
    player.poll(None)
    assert db.history_summary(user_id)["listens"] == 0


# ------------------------------------------------------------ fixed cadence


def test_the_poll_interval_is_not_a_setting(db, user_id):
    """It is the measurement resolution; letting it be raised would quietly change what
    every percentage in the app means."""
    from app.db import SETTINGS_COLUMNS

    assert "poll_interval" not in SETTINGS_COLUMNS
    assert "poll_interval" not in db.settings(user_id)


def test_every_user_polls_at_five_seconds():
    from app.tracker import POLL_INTERVAL_SECONDS

    assert POLL_INTERVAL_SECONDS == 5


def test_five_second_steps_measure_the_tail(player, db, user_id):
    """At a 5s cadence the unobserved stretch at the end of a track is at most 5s."""
    player.listen("t1", duration_ms=200_000, step_ms=5_000)

    row = db.history(user_id)["items"][0]
    assert row["completion_ratio"] == 1.0
    assert row["qualified"] is True
