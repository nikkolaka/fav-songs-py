"""Per-user play tracking.

Two independent mechanisms, deliberately kept separate:

1. A cursored sweep of `GET /me/player/recently-played` is the *only* source of play
   counts. Spotify keeps the last 50 plays server-side and stamps each with a stable
   `played_at`, which gives us both properties the old progress-polling loop lacked:
   plays that happened while this container was down still land on the next sweep, and
   `UNIQUE(user_id, track_id, played_at)` makes re-reading an overlapping window a no-op.

2. A poll of `GET /me/player` drives the live now-playing panel and discovery capture.
   It never increments a play count, so the two cannot double-count each other.

Consequence worth knowing: Spotify logs a track to recently-played on its own terms
(roughly 30 seconds in) and never reports how far through it got, so
`min_completion_ratio` cannot gate favourite counting. It applies to discovery capture,
which does see live progress.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

import spotipy

from . import playlists
from .db import Database, now_millis
from .discovery import Discovery
from .playlists import PlaylistCache
from .spotify import SpotifyAuthError, SpotifyService, retry_after_seconds

log = logging.getLogger(__name__)

RECENTLY_PLAYED_LIMIT = 50

# Re-read a few seconds behind the cursor. Any overlap is absorbed by the UNIQUE
# constraint, and it means a play stamped on a boundary can never fall through the gap.
CURSOR_OVERLAP_MS = 5_000

FAVORITES_DESCRIPTION = "Songs you keep coming back to. Maintained automatically."

# Back off when nothing is happening, so five idle users don't burn quota all day.
MAX_IDLE_MULTIPLIER = 4
IDLE_CYCLES_BEFORE_BACKOFF = 5


def parse_played_at(value: str) -> int:
    """'2026-07-26T12:34:56.789Z' -> epoch milliseconds."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def format_now_playing(playback: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not playback:
        return None
    track = playback.get("item")
    if not track or not track.get("id"):
        return None

    artists = track.get("artists") or []
    duration_ms = int(track.get("duration_ms") or 0)
    progress_ms = int(playback.get("progress_ms") or 0)
    return {
        "track_id": str(track["id"]),
        "name": str(track.get("name") or "Unknown Track"),
        "artist": str(
            (artists[0].get("name") if artists and artists[0] else None) or "Unknown Artist"
        ),
        "duration_ms": duration_ms,
        "progress_ms": progress_ms,
        "completion_ratio": (progress_ms / duration_ms) if duration_ms else 0.0,
        "is_playing": bool(playback.get("is_playing")),
    }


class UserTracker:
    def __init__(self, user_id: int, db: Database, spotify: SpotifyService):
        self.user_id = user_id
        self.db = db
        self.spotify = spotify
        self.cache = PlaylistCache()
        self.discovery = Discovery(db, self.cache)
        self.task: Optional[asyncio.Task] = None
        self.now_playing: Optional[dict[str, Any]] = None
        self.last_error: Optional[str] = None
        # Refreshed once per cycle so /api/state, which the browser polls, needs no
        # Spotify call of its own.
        self.favorites_snapshot: list[dict[str, str]] = []
        self.favorites_membership: set[str] = set()
        self._idle_cycles = 0
        self._last_capture_key: Optional[str] = None

    # ------------------------------------------------------------- favourites

    def _favorites_playlist(self, client: spotipy.Spotify, settings: dict[str, Any]) -> str:
        stored = settings.get("favorites_playlist_id")
        if stored:
            return str(stored)

        playlist_id = playlists.find_or_create(
            client,
            name=str(settings["playlist_name"]),
            public=bool(settings["playlist_public"]),
            description=FAVORITES_DESCRIPTION,
        )
        self.db.update_settings(self.user_id, {"favorites_playlist_id": playlist_id})
        return playlist_id

    def add_to_favorites(self, client: spotipy.Spotify, track_ids: list[str]) -> int:
        settings = self.db.settings(self.user_id)
        playlist_id = self._favorites_playlist(client, settings)
        # add_tracks skips anything already present, so repeat calls are free.
        return playlists.add_tracks(client, playlist_id, track_ids, self.cache, position=0)

    def remove_from_favorites(self, client: spotipy.Spotify, track_ids: list[str]) -> int:
        settings = self.db.settings(self.user_id)
        stored = settings.get("favorites_playlist_id")
        if not stored:
            return 0
        return playlists.remove_tracks(client, str(stored), track_ids, self.cache)

    def refresh_favorites(self, client: spotipy.Spotify) -> None:
        """Re-read the favourites playlist into memory.

        Only follows an id we already stored -- resolving by name walks every playlist
        the user has, which is too expensive to do on a timer. The id gets stored the
        first time `_favorites_playlist` creates or finds the playlist.
        """
        playlist_id = self.db.settings(self.user_id).get("favorites_playlist_id")
        if not playlist_id:
            self.favorites_snapshot = []
            self.favorites_membership = set()
            return

        entries = playlists.items(client, str(playlist_id), self.cache)
        self.favorites_snapshot = entries
        self.favorites_membership = {entry["track_id"] for entry in entries}

    def reconcile_favorites(self) -> int:
        """Add everything already over the threshold that isn't in the playlist yet.

        Covers the case where the user lowers the threshold or turns auto-add back on:
        without this, a track sitting at 4 plays under a new threshold of 3 would wait
        for its next play before being added.
        """
        settings = self.db.settings(self.user_id)
        if not settings["auto_add_enabled"]:
            return 0

        threshold = int(settings["favorite_threshold"])
        qualifying = [
            track_id
            for track_id, row in self.db.play_counts(self.user_id).items()
            if int(row["occurrences"]) >= threshold
        ]
        if not qualifying:
            return 0
        return self.add_to_favorites(self.spotify.client(self.user_id), qualifying)

    # ------------------------------------------------------------------ sweep

    def _sweep(self, client: spotipy.Spotify) -> int:
        """Fold new plays into the ledger. Returns how many were new."""
        after = self.db.cursor_after(self.user_id)
        response = client.current_user_recently_played(
            limit=RECENTLY_PLAYED_LIMIT,
            after=max(after - CURSOR_OVERLAP_MS, 0) if after else None,
        )
        items = (response or {}).get("items") or []
        if not items:
            return 0

        settings = self.db.settings(self.user_id)
        threshold = int(settings["favorite_threshold"])
        auto_add = bool(settings["auto_add_enabled"])

        newest = after
        promoted: list[str] = []
        new_plays = 0

        for item in items:
            track = (item or {}).get("track") or {}
            track_id = track.get("id")
            played_at_raw = item.get("played_at")
            if not track_id or not played_at_raw:
                continue

            played_at = parse_played_at(played_at_raw)
            newest = max(newest, played_at)

            artists = track.get("artists") or []
            occurrences = self.db.record_play(
                user_id=self.user_id,
                track_id=str(track_id),
                name=str(track.get("name") or "Unknown Track"),
                artist=str(
                    (artists[0].get("name") if artists and artists[0] else None)
                    or "Unknown Artist"
                ),
                played_at=played_at,
                context_uri=((item.get("context") or {}).get("uri")),
            )
            if occurrences is None:
                continue  # already counted on an earlier sweep
            new_plays += 1
            if auto_add and occurrences >= threshold:
                promoted.append(str(track_id))

        # Advance the cursor only after every row is committed, so a crash mid-sweep
        # replays the window instead of skipping it.
        if newest > after:
            self.db.set_cursor_after(self.user_id, newest)

        if promoted:
            try:
                added = self.add_to_favorites(client, promoted)
                if added:
                    log.info("Added %s track(s) to favourites for user %s", added, self.user_id)
            except spotipy.SpotifyException:
                raise
            except Exception as exc:
                log.warning("Could not add favourites for user %s: %s", self.user_id, exc)

        # Spotify only retains the last 50 plays, so there is nothing older to page to:
        # one request per sweep is always enough, and downtime beyond 50 tracks is
        # unrecoverable no matter how we ask.
        return new_plays

    def _live_poll(self, client: spotipy.Spotify) -> bool:
        """Refresh now-playing and run discovery capture. Returns True if playing."""
        playback = client.current_playback()
        self.now_playing = format_now_playing(playback)

        if not playback or not self.now_playing or not self.now_playing["is_playing"]:
            return False

        settings = self.db.settings(self.user_id)

        # One capture attempt per playback instance -- otherwise a track sitting above the
        # completion ratio would re-enter capture on every poll.
        started_at = int(playback.get("timestamp") or now_millis()) - int(
            playback.get("progress_ms") or 0
        )
        capture_key = f"{self.now_playing['track_id']}:{started_at // 1000}"
        if capture_key != self._last_capture_key:
            captured = self.discovery.capture(self.user_id, client, settings, playback)
            if captured:
                self._last_capture_key = capture_key

        return True

    # ------------------------------------------------------------------- loop

    async def _cycle(self) -> None:
        client = await asyncio.to_thread(self.spotify.client, self.user_id)
        new_plays = await asyncio.to_thread(self._sweep, client)
        playing = await asyncio.to_thread(self._live_poll, client)
        await asyncio.to_thread(self.refresh_favorites, client)
        self.last_error = None

        if playing or new_plays:
            self._idle_cycles = 0
        else:
            self._idle_cycles += 1

    def _sleep_seconds(self) -> int:
        base = max(int(self.db.settings(self.user_id)["poll_interval"]), 10)
        if self._idle_cycles < IDLE_CYCLES_BEFORE_BACKOFF:
            return base
        steps = self._idle_cycles - IDLE_CYCLES_BEFORE_BACKOFF + 2
        return base * min(steps, MAX_IDLE_MULTIPLIER)

    async def run(self) -> None:
        log.info("Tracker started for user %s", self.user_id)
        try:
            while True:
                delay = self._sleep_seconds()
                try:
                    await self._cycle()
                except SpotifyAuthError:
                    log.warning("User %s must reconnect Spotify; stopping tracker", self.user_id)
                    self.last_error = "Spotify access was revoked. Log in again to resume."
                    self.now_playing = None
                    return
                except spotipy.SpotifyException as exc:
                    wait = retry_after_seconds(exc)
                    if wait:
                        log.warning("Rate limited for user %s, waiting %ss", self.user_id, wait)
                        self.last_error = "Rate limited by Spotify; retrying shortly."
                        delay = wait
                    else:
                        log.warning("Spotify error for user %s: %s", self.user_id, exc)
                        self.last_error = f"Spotify error: {exc.msg or exc.http_status}"
                except Exception as exc:
                    log.warning("Tracker error for user %s: %s", self.user_id, exc)
                    self.last_error = str(exc)

                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.info("Tracker stopped for user %s", self.user_id)
            raise


class TrackerManager:
    """Owns one asyncio task per connected user."""

    def __init__(self, db: Database, spotify: SpotifyService):
        self.db = db
        self.spotify = spotify
        self.trackers: dict[int, UserTracker] = {}

    def get(self, user_id: int) -> UserTracker:
        tracker = self.trackers.get(user_id)
        if not tracker:
            tracker = UserTracker(user_id, self.db, self.spotify)
            self.trackers[user_id] = tracker
        return tracker

    def is_running(self, user_id: int) -> bool:
        tracker = self.trackers.get(user_id)
        return bool(tracker and tracker.task and not tracker.task.done())

    async def start(self, user_id: int) -> None:
        tracker = self.get(user_id)
        if tracker.task and not tracker.task.done():
            return
        self.db.update_settings(user_id, {"tracker_running": True})
        tracker.task = asyncio.create_task(tracker.run(), name=f"tracker-{user_id}")

    async def stop(self, user_id: int, persist: bool = True) -> None:
        # persist=False is for process shutdown: cancel the task without recording the
        # user as paused, or nobody's tracker would come back after a restart.
        if persist:
            self.db.update_settings(user_id, {"tracker_running": False})
        tracker = self.trackers.get(user_id)
        if not tracker or not tracker.task:
            return
        task, tracker.task = tracker.task, None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        tracker.now_playing = None

    async def start_all(self) -> None:
        for user_id in self.db.connected_user_ids():
            await self.start(user_id)

    async def stop_all(self) -> None:
        for user_id in list(self.trackers):
            await self.stop(user_id, persist=False)
