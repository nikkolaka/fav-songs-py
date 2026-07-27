"""Discovery archiving, which works off playback context because Spotify blocks reading
algorithmic playlists directly."""

import pytest

from app import discovery as discovery_mod
from app.discovery import (
    month_key,
    month_playlist_name,
    playlist_id_from_context,
    playlist_id_from_link,
)

from conftest import track


def playing(track_id, context_playlist=None, progress_ratio=0.9, duration_ms=200_000):
    return {
        "item": track(track_id, duration_ms=duration_ms),
        "progress_ms": int(duration_ms * progress_ratio),
        "is_playing": True,
        "timestamp": 1_800_000_000_000,
        "context": {"uri": f"spotify:playlist:{context_playlist}", "type": "playlist"}
        if context_playlist
        else None,
    }


def test_month_playlist_name():
    assert month_playlist_name("2026-01") == "January '26 Discovery"
    assert month_playlist_name("2026-12") == "December '26 Discovery"


def test_month_key_is_sortable():
    keys = sorted(["2026-02", "2025-12", "2026-01"])
    assert keys == ["2025-12", "2026-01", "2026-02"]
    assert len(month_key()) == 7


@pytest.mark.parametrize(
    "value,expected",
    [
        ("spotify:playlist:37i9dQ", "37i9dQ"),
        ("https://open.spotify.com/playlist/37i9dQ?si=abc123", "37i9dQ"),
        ("https://open.spotify.com/playlist/37i9dQ", "37i9dQ"),
        ("37i9dQ", "37i9dQ"),
        ("", None),
        ("not a link!", None),
    ],
)
def test_playlist_id_from_link(value, expected):
    assert playlist_id_from_link(value) == expected


def test_album_context_is_ignored():
    assert playlist_id_from_context({"uri": "spotify:album:abc"}) is None
    assert playlist_id_from_context(None) is None


def test_unknown_context_is_recorded_not_archived(tracker, spotify, db, user_id):
    """We can't read the playlist's name, so we surface it for the user to label."""
    settings = db.settings(user_id)
    captured = tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123"))

    assert captured is None
    assert spotify.playlists == {}

    pending = db.unlabelled_contexts(user_id)
    assert len(pending) == 1
    assert pending[0]["playlist_id"] == "dw123"
    assert pending[0]["sample_track"] == "Artist - Song"


def test_labelled_source_archives_to_the_month_playlist(tracker, spotify, db, user_id):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    settings = db.settings(user_id)

    captured = tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123"))

    assert captured["track_id"] == "t1"
    playlist = next(iter(spotify.playlists.values()))
    assert playlist["name"] == month_playlist_name(month_key())
    assert playlist["tracks"] == ["t1"]


def test_replaying_the_same_discovery_does_not_duplicate(tracker, spotify, db, user_id):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    settings = db.settings(user_id)

    assert tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123"))
    for _ in range(4):
        assert tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123")) is None

    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]


def test_partial_listen_is_not_archived(tracker, spotify, db, user_id):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    settings = db.settings(user_id)  # min_completion_ratio defaults to 0.8

    assert (
        tracker.discovery.capture(
            user_id, spotify, settings, playing("t1", "dw123", progress_ratio=0.3)
        )
        is None
    )
    assert spotify.playlists == {}


def test_playback_outside_a_source_is_ignored(tracker, spotify, db, user_id):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    settings = db.settings(user_id)

    assert tracker.discovery.capture(user_id, spotify, settings, playing("t1")) is None
    assert db.unlabelled_contexts(user_id) == []


def test_discovery_disabled_captures_nothing(tracker, spotify, db, user_id):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    settings = db.update_settings(user_id, {"discovery_enabled": False})

    assert tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123")) is None
    assert spotify.playlists == {}


def test_labelling_a_context_clears_the_prompt(tracker, spotify, db, user_id):
    settings = db.settings(user_id)
    tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123"))
    assert db.unlabelled_contexts(user_id)

    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    assert db.unlabelled_contexts(user_id) == []


def test_failed_playlist_write_is_rolled_back(tracker, spotify, db, user_id, monkeypatch):
    """The archive row must not survive a failed Spotify write, or the track would be
    skipped forever on later plays."""
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    settings = db.settings(user_id)

    def boom(*args, **kwargs):
        raise RuntimeError("Spotify said no")

    monkeypatch.setattr(discovery_mod.playlists, "add_tracks", boom)
    with pytest.raises(RuntimeError):
        tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123"))

    assert db.discovery_month(user_id, month_key()) == []

    # And a later play succeeds normally.
    monkeypatch.undo()
    assert tracker.discovery.capture(user_id, spotify, settings, playing("t1", "dw123"))
