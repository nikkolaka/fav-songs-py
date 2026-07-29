"""Per-user play tracking.

One mechanism writes the listening history, and only one: a poll of `GET /me/player`
every five seconds, measuring playback as it happens. See `app/listens.py` for how a
session accumulates into a completion ratio. A listen that clears
`min_completion_ratio` counts towards the favourites threshold; one that doesn't is
still recorded, just not counted. Both end up in the history either way.

Spotify's own history (`GET /me/player/recently-played`) is deliberately not read. It
reports that a track played and never how much of it was heard, so anything from there
would be a second, unmeasurable way for rows to appear -- a play that happened while
this app was down simply isn't recorded, rather than being recorded as a guess.

The poll interval is fixed rather than configurable because it *is* the measurement
resolution: at five seconds the unobserved stretch at the end of a track is at most five
seconds, and the completion figure is honest to within that. Letting it be raised would
quietly make everyone's percentages mean something different.
"""

import asyncio
import logging
from typing import Any, Optional

import spotipy

from . import listens, playlists
from .aiblocklist import AiBlocklist
from .db import Database, now_millis, now_seconds
from .discovery import Discovery
from .listens import Observation, Session
from .playlists import PlaylistCache
from .spotify import SpotifyAuthError, SpotifyService, retry_after_seconds

log = logging.getLogger(__name__)

# Fixed for every user. Five seconds bounds the measurement error on a track's tail; it
# is not a preference, and there is no backoff, because a session that goes unobserved
# for a minute is a session measured a minute wrong.
POLL_INTERVAL_SECONDS = 5

FAVORITES_DESCRIPTION = "Songs you keep coming back to. Maintained automatically."

# Discover Weekly refreshes on Mondays, so six hours catches every edition with room to
# spare while costing one page fetch per source per sweep.
SOURCE_SWEEP_INTERVAL_SECONDS = 6 * 3600
SOURCE_SWEEP_RETRY_SECONDS = 900


def format_now_playing(
    obs: Optional[Observation], session: Optional[Session], threshold: float
) -> Optional[dict[str, Any]]:
    """What the live panel shows: position in the track, and how much was actually heard.

    The two differ whenever someone seeks, which is the whole point -- `progress_ms`
    says where the needle is, `heard_ratio` says what would count.
    """
    if not obs:
        return None

    heard_ratio = session.heard_ratio if session else 0.0
    return {
        "track_id": obs.track_id,
        "name": obs.name,
        "artist": obs.artist,
        "duration_ms": obs.duration_ms,
        "progress_ms": obs.progress_ms,
        "completion_ratio": (obs.progress_ms / obs.duration_ms) if obs.duration_ms else 0.0,
        "heard_ms": session.listened_ms if session else 0,
        "heard_ratio": heard_ratio,
        "counts": heard_ratio >= threshold,
        "is_playing": obs.is_playing,
    }


class UserTracker:
    def __init__(
        self, user_id: int, db: Database, spotify: SpotifyService, blocklist: AiBlocklist
    ):
        self.user_id = user_id
        self.db = db
        self.spotify = spotify
        self.cache = PlaylistCache()
        self.discovery = Discovery(db, self.cache, blocklist)
        self.last_sweep: Optional[dict[str, Any]] = None
        self.task: Optional[asyncio.Task] = None
        self.now_playing: Optional[dict[str, Any]] = None
        self.last_error: Optional[str] = None
        # Refreshed once per cycle so /api/state, which the browser polls, needs no
        # Spotify call of its own.
        self.favorites_snapshot: list[dict[str, str]] = []
        self.favorites_membership: set[str] = set()
        self._last_capture_key: Optional[str] = None
        # The playback currently being measured. Mirrored to an open row in `listens`,
        # so a restart keeps whatever was heard before it rather than dropping it.
        self.session: Optional[Session] = None
        # Set when a playlist write failed, so a track that earned its place at the
        # exact moment Spotify was unavailable isn't left waiting for its next play.
        self._needs_reconcile = False

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
            if int(row["qualified_plays"]) >= threshold
        ]
        if not qualifying:
            return 0
        return self.add_to_favorites(self.spotify.client(self.user_id), qualifying)

    # --------------------------------------------------------------- measuring

    def _close_session(
        self,
        ended_at: int,
        settings: dict[str, Any],
        client: Optional[spotipy.Spotify] = None,
    ) -> None:
        """Finish the open listen, record what was heard, and promote if it earned it."""
        session = self.session
        self.session = None
        if not session or session.row_id is None:
            return

        result = listens.finalize(session, max(ended_at, session.last_observed_at))
        threshold_ratio = float(settings["min_completion_ratio"])
        qualified = result["completion_ratio"] >= threshold_ratio

        count = self.db.close_listen(
            row_id=session.row_id,
            user_id=self.user_id,
            track_id=result["track_id"],
            name=result["name"],
            artist=result["artist"],
            played_at=result["played_at"],
            duration_ms=result["duration_ms"],
            listened_ms=result["listened_ms"],
            completion_ratio=result["completion_ratio"],
            qualified=qualified,
        )
        log.info(
            "Listen: %s - %s, heard %.0f%% (%s), %s qualified play(s)",
            result["artist"],
            result["name"],
            result["completion_ratio"] * 100,
            "counts" if qualified else f"below {threshold_ratio:.0%}",
            count,
        )

        if not qualified:
            return

        try:
            client = client or self.spotify.client(self.user_id)
            # A track whose final poll landed short of the threshold only clears it once
            # the tail is credited, so the archive gets its last look here.
            self._try_capture(client, settings, session, result["completion_ratio"])
            if settings["auto_add_enabled"] and count >= int(settings["favorite_threshold"]):
                added = self.add_to_favorites(client, [result["track_id"]])
                if added:
                    log.info("Added %r to favourites for user %s", result["name"], self.user_id)
        except spotipy.SpotifyException:
            # The listen is already recorded; let the run loop see a 429 and back off.
            # The reconcile on the next cycle is what actually gets the track filed.
            self._needs_reconcile = True
            raise
        except Exception as exc:
            self._needs_reconcile = True
            log.warning("Could not file %r for user %s: %s", result["name"], self.user_id, exc)

    def _measure(
        self,
        obs: Optional[Observation],
        settings: dict[str, Any],
        client: Optional[spotipy.Spotify] = None,
    ) -> None:
        """Fold one poll into the open session, opening and closing sessions as needed."""
        if self.session and (obs is None or not listens.continues(self.session, obs)):
            # When another track is already playing we know precisely when this one
            # stopped: the moment that one started. Otherwise all we have is now.
            ended_at = (obs.at - obs.progress_ms) if obs else now_millis()
            self._close_session(ended_at, settings, client)

        if obs is None:
            return

        if self.session is None:
            self.session = listens.start_session(obs)
            self.session.row_id = self.db.open_listen(
                user_id=self.user_id,
                track_id=obs.track_id,
                name=obs.name,
                artist=obs.artist,
                played_at=self.session.started_at,
                duration_ms=obs.duration_ms,
                context_uri=obs.context_uri,
            )
        else:
            listens.observe(self.session, obs)
            if self.session.row_id is not None:
                self.db.update_open_listen(
                    self.session.row_id,
                    self.session.listened_ms,
                    self.session.heard_ratio,
                    self.session.duration_ms,
                )

    def sweep_sources(self, client: spotipy.Spotify) -> dict[str, Any]:
        """Read every discovery source now, regardless of schedule."""
        settings = self.db.settings(self.user_id)
        summary = self.discovery.sweep_sources(self.user_id, client, settings)
        self.db.set_last_source_sweep(self.user_id, now_seconds())
        self.last_sweep = {**summary, "at": now_seconds()}
        return summary

    def _maybe_sweep_sources(self, client: spotipy.Spotify) -> None:
        due = self.db.last_source_sweep(self.user_id) + SOURCE_SWEEP_INTERVAL_SECONDS
        if now_seconds() < due:
            return
        try:
            self.sweep_sources(client)
        except Exception as exc:
            # Come back in minutes rather than hours, but don't retry every cycle.
            self.db.set_last_source_sweep(
                self.user_id,
                now_seconds() - SOURCE_SWEEP_INTERVAL_SECONDS + SOURCE_SWEEP_RETRY_SECONDS,
            )
            log.warning("Source sweep failed for user %s: %s", self.user_id, exc)

    def _live_poll(self, client: spotipy.Spotify) -> bool:
        """Measure playback, refresh now-playing, run discovery capture.

        Returns True if something is playing.
        """
        playback = client.current_playback()
        settings = self.db.settings(self.user_id)
        obs = listens.observation_from_playback(playback, now_millis())

        self._measure(obs, settings, client)
        self.now_playing = format_now_playing(
            obs, self.session, float(settings["min_completion_ratio"])
        )

        if not obs or not obs.is_playing or not self.session:
            return False

        self._try_capture(client, settings, self.session, self.session.heard_ratio)
        return True

    def _try_capture(
        self,
        client: spotipy.Spotify,
        settings: dict[str, Any],
        session: Session,
        heard_ratio: float,
    ) -> None:
        """Offer a session to the discovery archive, at most once per playback."""
        key = f"{session.track_id}:{session.started_at}"
        if key == self._last_capture_key:
            return
        try:
            captured = self.discovery.capture(
                self.user_id,
                client,
                settings,
                track_id=session.track_id,
                name=session.name,
                artist=session.artist,
                context_uri=session.context_uri,
                heard_ratio=heard_ratio,
            )
        except spotipy.SpotifyException:
            raise
        except Exception as exc:
            log.warning("Discovery capture failed for user %s: %s", self.user_id, exc)
            return
        if captured:
            self._last_capture_key = key

    def flush(self) -> None:
        """Close the open listen on the way out, so pausing doesn't discard it."""
        if not self.session:
            return
        try:
            self._close_session(now_millis(), self.db.settings(self.user_id))
        except Exception as exc:
            log.warning("Could not close the open listen for user %s: %s", self.user_id, exc)
            self.session = None

    # ------------------------------------------------------------------- loop

    async def _cycle(self) -> None:
        client = await asyncio.to_thread(self.spotify.client, self.user_id)
        await asyncio.to_thread(self._live_poll, client)
        # One Spotify read per cycle at most: the playlist membership behind this is
        # cached for 30s, so a 5-second poll doesn't turn into a 5-second playlist read.
        await asyncio.to_thread(self.refresh_favorites, client)
        await asyncio.to_thread(self._maybe_sweep_sources, client)

        if self._needs_reconcile:
            await asyncio.to_thread(self.reconcile_favorites)
            self._needs_reconcile = False

        self.last_error = None

    async def run(self) -> None:
        log.info("Tracker started for user %s", self.user_id)
        try:
            while True:
                # Fixed, except when Spotify itself tells us to slow down below.
                delay = POLL_INTERVAL_SECONDS
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

    def __init__(self, db: Database, spotify: SpotifyService, blocklist: AiBlocklist):
        self.db = db
        self.spotify = spotify
        self.blocklist = blocklist
        self.trackers: dict[int, UserTracker] = {}

    def get(self, user_id: int) -> UserTracker:
        tracker = self.trackers.get(user_id)
        if not tracker:
            tracker = UserTracker(user_id, self.db, self.spotify, self.blocklist)
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
        await asyncio.to_thread(tracker.flush)
        tracker.now_playing = None

    async def start_all(self) -> None:
        for user_id in self.db.connected_user_ids():
            await self.start(user_id)

    async def stop_all(self) -> None:
        for user_id in list(self.trackers):
            await self.stop(user_id, persist=False)
