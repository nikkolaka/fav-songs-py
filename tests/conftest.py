"""A fake Spotify client, so the tracking invariants can be tested without credentials."""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import tracker as tracker_mod  # noqa: E402
from app.aiblocklist import AiBlocklist  # noqa: E402
from app.db import Database  # noqa: E402
from app.spotify import SpotifyService  # noqa: E402
from app.tracker import UserTracker  # noqa: E402

FERNET_KEY = b"cGxhY2Vob2xkZXJfa2V5X2Zvcl90ZXN0c19vbmx5ISE="


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def track(track_id: str, name: str = "Song", artist: str = "Artist", duration_ms: int = 200_000):
    return {
        "id": track_id,
        "name": name,
        "duration_ms": duration_ms,
        "artists": [{"name": artist}],
    }


def play(track_id: str, at: datetime, context_playlist: Optional[str] = None, **kwargs):
    return {
        "track": track(track_id, **kwargs),
        "played_at": iso(at),
        "context": {"uri": f"spotify:playlist:{context_playlist}"} if context_playlist else None,
    }


def playback(
    track_id: str,
    progress_ms: int,
    is_playing: bool = True,
    context_playlist: Optional[str] = None,
    **kwargs,
):
    """What `GET /me/player` returns mid-track."""
    return {
        "item": track(track_id, **kwargs),
        "progress_ms": progress_ms,
        "is_playing": is_playing,
        "context": {"uri": f"spotify:playlist:{context_playlist}"} if context_playlist else None,
    }


class Clock:
    """A hand-cranked wall clock, so listening can be simulated poll by poll."""

    def __init__(self, start_ms: int = 1_800_000_000_000) -> None:
        self.now = start_ms

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> int:
        self.now += ms
        return self.now


class Player:
    """Drives a tracker through a sequence of polls against a fake clock."""

    def __init__(self, tracker: UserTracker, spotify: "FakeSpotify", clock: Clock) -> None:
        self.tracker = tracker
        self.spotify = spotify
        self.clock = clock

    def poll(self, state: Optional[dict[str, Any]], after_ms: int = 0) -> None:
        if after_ms:
            self.clock.advance(after_ms)
        self.spotify.playback = state
        self.tracker._live_poll(self.spotify)

    def listen(
        self,
        track_id: str,
        duration_ms: int = 200_000,
        heard_ms: Optional[int] = None,
        step_ms: int = 30_000,
        context_playlist: Optional[str] = None,
    ) -> None:
        """Play a track from the start for `heard_ms`, then stop -- closing its session.

        Polls land on the step, not on the moment playback ends, exactly as a real
        30-second interval would: the last stretch is never directly observed.
        """
        heard_ms = duration_ms if heard_ms is None else heard_ms
        at = 0
        while at <= heard_ms:
            self.poll(
                playback(track_id, at, duration_ms=duration_ms, context_playlist=context_playlist),
                after_ms=0 if at == 0 else step_ms,
            )
            at += step_ms
        self.poll(None, after_ms=max(0, heard_ms - (at - step_ms)))


class FakeSpotify:
    """Implements only the handful of methods this app calls, with the post-Feb-2026 names."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.playlists: dict[str, dict[str, Any]] = {}
        self.playback: Optional[dict[str, Any]] = None
        self.calls: list[str] = []
        self._next_id = 0
        # track_id -> artist ids, for the AI blocklist lookup
        self.track_artists: dict[str, list[str]] = {}

    def track(self, track_id, market=None):
        self.calls.append("track")
        ids = self.track_artists.get(track_id, [f"artist-of-{track_id}"])
        return {"id": track_id, "artists": [{"id": i, "name": i} for i in ids]}

    # --- history

    def current_user_recently_played(self, limit=50, after=None, before=None):
        self.calls.append("recently_played")
        items = self.history
        if after is not None:
            cutoff = datetime.fromtimestamp(after / 1000, tz=timezone.utc)
            items = [
                item
                for item in items
                if datetime.fromisoformat(item["played_at"].replace("Z", "+00:00")) > cutoff
            ]
        items = sorted(items, key=lambda i: i["played_at"], reverse=True)[:limit]
        return {"items": items, "next": None, "cursors": {}}

    # --- playback

    def current_playback(self, market=None, additional_types=None):
        self.calls.append("current_playback")
        return self.playback

    # --- playlists

    def current_user_playlists(self, limit=50, offset=0):
        self.calls.append("current_user_playlists")
        items = [
            {"id": pid, "name": data["name"], "public": data["public"]}
            for pid, data in self.playlists.items()
        ]
        return {"items": items, "next": None}

    def current_user_playlist_create(self, name, public=True, collaborative=False, description=""):
        self.calls.append("create_playlist")
        self._next_id += 1
        pid = f"pl{self._next_id}"
        self.playlists[pid] = {"name": name, "public": public, "tracks": []}
        return {"id": pid, "name": name}

    def playlist_items(self, playlist_id, fields=None, limit=50, offset=0, market=None,
                       additional_types=("track", "episode")):
        self.calls.append("playlist_items")
        data = self.playlists[playlist_id]
        return {
            "items": [{"track": track(tid)} for tid in data["tracks"]],
            "next": None,
        }

    def playlist_add_items(self, playlist_id, items, position=None):
        self.calls.append("playlist_add_items")
        data = self.playlists[playlist_id]
        ids = [i.split(":")[-1] for i in items]
        data["tracks"] = (ids + data["tracks"]) if position == 0 else (data["tracks"] + ids)
        return {"snapshot_id": "x"}

    def playlist_remove_all_occurrences_of_items(self, playlist_id, items, snapshot_id=None):
        self.calls.append("playlist_remove")
        ids = {i.split(":")[-1] for i in items}
        data = self.playlists[playlist_id]
        data["tracks"] = [tid for tid in data["tracks"] if tid not in ids]
        return {"snapshot_id": "x"}

    def next(self, result):
        return None


class FakeService(SpotifyService):
    def __init__(self, fake: FakeSpotify) -> None:  # noqa: D107 -- deliberately no super()
        self.fake = fake

    def client(self, user_id: int):
        return self.fake


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "test.db"), FERNET_KEY, "Favourite Songs")
    yield database
    database.close()


@pytest.fixture
def user_id(db: Database) -> int:
    return db.upsert_user("tester", "Tester")


@pytest.fixture
def spotify() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def blocklist(tmp_path) -> AiBlocklist:
    """A loaded blocklist that never touches the network (refresh is a no-op here)."""
    csv_path = tmp_path / "ai-artists.csv"
    rows = "\n".join(f"Fake AI Act {i},aiartist{i}" for i in range(600))
    csv_path.write_text(f"artist,id\nSynthwave Ghost,aiartist-known\n{rows}\n", encoding="utf-8")
    instance = AiBlocklist(str(csv_path))
    instance.refresh = lambda force=False: False
    return instance


@pytest.fixture
def tracker(
    db: Database, user_id: int, spotify: FakeSpotify, blocklist: AiBlocklist
) -> UserTracker:
    return UserTracker(user_id, db, FakeService(spotify), blocklist)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """Freeze the tracker's clock so a listen can be played out in simulated time."""
    instance = Clock()
    monkeypatch.setattr(tracker_mod, "now_millis", instance)
    return instance


@pytest.fixture
def player(tracker: UserTracker, spotify: FakeSpotify, clock: Clock) -> Player:
    return Player(tracker, spotify, clock)


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=30)
