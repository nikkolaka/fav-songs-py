"""Discovery archiving.

The Web API can't read Discover Weekly (404 for this app tier), so the archive reads
Spotify's public embed page and falls back to capturing what actually gets played.
"""

import json

import pytest

from app import discovery as discovery_mod
from app.discovery import (
    EmbedUnavailable,
    month_key,
    month_playlist_name,
    playlist_id_from_context,
    playlist_id_from_link,
    read_playlist_embed,
)


def embed_html(name="Discover Weekly", tracks=(("t1", "Song One", "Band One"),)):
    payload = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": name,
                            "type": "playlist",
                            "trackList": [
                                {
                                    "uri": f"spotify:track:{tid}",
                                    "title": title,
                                    "subtitle": artist,
                                }
                                for tid, title, artist in tracks
                            ],
                        }
                    }
                }
            }
        }
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></body></html>"
    )


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ------------------------------------------------------------------- naming


def test_month_playlist_name_uses_the_requested_convention():
    assert month_playlist_name("2026-07") == "July's Discover"
    assert month_playlist_name("2026-01") == "January's Discover"
    assert month_playlist_name("2026-12") == "December's Discover"


def test_month_key_is_sortable_and_year_aware():
    assert sorted(["2026-02", "2025-12", "2026-01"]) == ["2025-12", "2026-01", "2026-02"]
    assert len(month_key()) == 7


@pytest.mark.parametrize(
    "value,expected",
    [
        ("spotify:playlist:37i9dQ", "37i9dQ"),
        ("https://open.spotify.com/playlist/37i9dQ?si=abc123", "37i9dQ"),
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


# -------------------------------------------------------------- embed read


def test_read_playlist_embed_extracts_name_and_tracks(monkeypatch):
    html = embed_html(tracks=[("t1", "Song One", "Band One"), ("t2", "Song Two", "Band Two")])
    monkeypatch.setattr(discovery_mod.requests, "get", lambda *a, **k: FakeResponse(html))

    data = read_playlist_embed("dw123")
    assert data["name"] == "Discover Weekly"
    assert [t["track_id"] for t in data["tracks"]] == ["t1", "t2"]
    assert data["tracks"][0] == {"track_id": "t1", "name": "Song One", "artist": "Band One"}


def test_read_playlist_embed_skips_non_track_entries(monkeypatch):
    html = embed_html(tracks=[("t1", "Song", "Band")])
    html = html.replace("spotify:track:t1", "spotify:episode:e1")
    monkeypatch.setattr(discovery_mod.requests, "get", lambda *a, **k: FakeResponse(html))

    with pytest.raises(EmbedUnavailable, match="no tracks"):
        read_playlist_embed("dw123")


@pytest.mark.parametrize(
    "body,match",
    [
        ("<html>no next data here</html>", "no __NEXT_DATA__"),
        ('<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>', "structure"),
    ],
)
def test_read_playlist_embed_raises_when_the_page_changes(monkeypatch, body, match):
    """Spotify can change this page any time; we must fail loudly, not silently."""
    monkeypatch.setattr(discovery_mod.requests, "get", lambda *a, **k: FakeResponse(body))
    with pytest.raises(EmbedUnavailable, match=match):
        read_playlist_embed("dw123")


# ------------------------------------------------------------------- sweep


def test_sweep_archives_every_track_from_the_source(tracker, spotify, db, user_id, monkeypatch):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    monkeypatch.setattr(
        discovery_mod,
        "read_playlist_embed",
        lambda pid: {
            "name": "Discover Weekly",
            "tracks": [
                {"track_id": f"t{i}", "name": f"Song {i}", "artist": f"Band {i}"}
                for i in range(30)
            ],
        },
    )

    summary = tracker.sweep_sources(spotify)

    assert summary == {"archived": 30, "blocked": 0, "errors": []}
    playlist = next(iter(spotify.playlists.values()))
    assert playlist["name"] == month_playlist_name(month_key())
    assert len(playlist["tracks"]) == 30


def test_sweep_is_idempotent(tracker, spotify, db, user_id, monkeypatch):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    monkeypatch.setattr(
        discovery_mod,
        "read_playlist_embed",
        lambda pid: {
            "name": "Discover Weekly",
            "tracks": [{"track_id": "t1", "name": "Song", "artist": "Band"}],
        },
    )

    assert tracker.sweep_sources(spotify)["archived"] == 1
    for _ in range(3):
        assert tracker.sweep_sources(spotify)["archived"] == 0
    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]


def test_sweep_filters_ai_artists_by_id(tracker, spotify, db, user_id, monkeypatch):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    spotify.track_artists = {
        "good": ["legit-band"],
        "bot": ["aiartist-known"],
    }
    monkeypatch.setattr(
        discovery_mod,
        "read_playlist_embed",
        lambda pid: {
            "name": "Discover Weekly",
            "tracks": [
                {"track_id": "good", "name": "Real Song", "artist": "Legit Band"},
                {"track_id": "bot", "name": "Slop", "artist": "Synthwave Ghost"},
            ],
        },
    )

    summary = tracker.sweep_sources(spotify)

    assert summary["archived"] == 1 and summary["blocked"] == 1
    assert next(iter(spotify.playlists.values()))["tracks"] == ["good"]

    blocked = db.blocked_month(user_id, month_key())
    assert len(blocked) == 1
    assert blocked[0]["track_id"] == "bot"
    assert "aiartist-known" in blocked[0]["reason"]


def test_a_blocked_track_is_not_re_checked_next_sweep(tracker, spotify, db, user_id, monkeypatch):
    """Blocked tracks cost one API lookup, not one per sweep for the rest of the month."""
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    spotify.track_artists = {"bot": ["aiartist-known"]}
    monkeypatch.setattr(
        discovery_mod,
        "read_playlist_embed",
        lambda pid: {
            "name": "Discover Weekly",
            "tracks": [{"track_id": "bot", "name": "Slop", "artist": "Synthwave Ghost"}],
        },
    )

    tracker.sweep_sources(spotify)
    lookups = spotify.calls.count("track")
    tracker.sweep_sources(spotify)
    assert spotify.calls.count("track") == lookups


def test_name_match_catches_tracks_whose_artists_cannot_be_resolved(
    tracker, spotify, db, user_id, monkeypatch
):
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")

    def unresolvable(track_id, market=None):
        raise RuntimeError("track lookup failed")

    monkeypatch.setattr(spotify, "track", unresolvable)
    monkeypatch.setattr(
        discovery_mod,
        "read_playlist_embed",
        lambda pid: {
            "name": "Discover Weekly",
            "tracks": [{"track_id": "bot", "name": "Slop", "artist": "Synthwave Ghost"}],
        },
    )

    assert tracker.sweep_sources(spotify)["blocked"] == 1


def test_a_broken_embed_records_the_error_without_killing_the_sweep(
    tracker, spotify, db, user_id, monkeypatch
):
    db.add_discovery_source(user_id, "broken", "Discover Weekly")
    db.add_discovery_source(user_id, "working", "Discover Weekly")

    def reader(playlist_id):
        if playlist_id == "broken":
            raise EmbedUnavailable("unexpected page structure")
        return {
            "name": "Release Radar",
            "tracks": [{"track_id": "t1", "name": "Song", "artist": "Band"}],
        }

    monkeypatch.setattr(discovery_mod, "read_playlist_embed", reader)
    summary = tracker.sweep_sources(spotify)

    assert summary["archived"] == 1
    assert len(summary["errors"]) == 1
    assert summary["errors"][0]["playlist_id"] == "broken"

    sources = {s["playlist_id"]: s for s in db.discovery_sources(user_id)}
    assert sources["broken"]["degraded"] == "unexpected page structure"
    assert sources["working"]["degraded"] is None


def test_failed_playlist_write_is_rolled_back(tracker, spotify, db, user_id, monkeypatch):
    """The archive row must not survive a failed Spotify write, or the track is skipped
    forever on later sweeps."""
    db.add_discovery_source(user_id, "dw123", "Discover Weekly")
    monkeypatch.setattr(
        discovery_mod,
        "read_playlist_embed",
        lambda pid: {
            "name": "Discover Weekly",
            "tracks": [{"track_id": "t1", "name": "Song", "artist": "Band"}],
        },
    )

    real_add = discovery_mod.playlists.add_tracks
    attempts = {"n": 0}

    def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("Spotify said no")
        return real_add(*args, **kwargs)

    monkeypatch.setattr(discovery_mod.playlists, "add_tracks", flaky)

    with pytest.raises(RuntimeError):
        tracker.sweep_sources(spotify)
    assert db.discovery_month(user_id, month_key()) == []

    # The next sweep picks it up again rather than skipping it forever.
    assert tracker.sweep_sources(spotify)["archived"] == 1
    assert next(iter(spotify.playlists.values()))["tracks"] == ["t1"]
