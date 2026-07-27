"""Monthly discovery archive.

Spotify blocks reading algorithmic playlists: `GET /playlists/{id}/items` is
owner/collaborator-only (403 otherwise), and since 2024-11-27 Spotify-owned and
algorithmic playlists are off limits to any app that wasn't already in extended quota
mode. So we cannot enumerate Discover Weekly's 30 tracks.

What we *can* see is `context.uri` on the playback object, which names the playlist a
track is being played from even when that playlist's contents are unreadable. So the
archive is built from what actually gets listened to.

The trade-off, accepted deliberately: a week you don't listen archives nothing.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import spotipy

from . import playlists
from .db import Database
from .playlists import PlaylistCache

log = logging.getLogger(__name__)

PLAYLIST_DESCRIPTION = "Tracks discovered this month. Maintained automatically."


def month_key(moment: Optional[datetime] = None) -> str:
    """Stable sort/group key, e.g. '2026-01'."""
    moment = moment or datetime.now()
    return moment.strftime("%Y-%m")


def month_playlist_name(key: str) -> str:
    """Display name for a month key, e.g. '2026-01' -> "January '26 Discovery"."""
    parsed = datetime.strptime(key, "%Y-%m")
    return f"{parsed.strftime('%B')} '{parsed.strftime('%y')} Discovery"


def playlist_id_from_context(context: Optional[dict[str, Any]]) -> Optional[str]:
    """Pull the playlist id out of a playback context, ignoring albums and artists."""
    uri = (context or {}).get("uri") or ""
    parts = uri.split(":")
    if len(parts) == 3 and parts[1] == "playlist" and parts[2]:
        return parts[2]
    return None


def playlist_id_from_link(value: str) -> Optional[str]:
    """Accept a playlist id, a spotify: URI, or an open.spotify.com share link."""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("spotify:"):
        return playlist_id_from_context({"uri": value})
    if "open.spotify.com" in value:
        tail = value.split("playlist/", 1)[-1]
        candidate = tail.split("?", 1)[0].split("/", 1)[0]
        return candidate or None
    return value if value.isalnum() else None


class Discovery:
    def __init__(self, db: Database, cache: PlaylistCache):
        self.db = db
        self.cache = cache

    def month_playlist(
        self, user_id: int, client: spotipy.Spotify, key: str, public: bool
    ) -> str:
        cached = self.db.discovery_playlist_id(user_id, key)
        if cached:
            return cached

        playlist_id = playlists.find_or_create(
            client,
            name=month_playlist_name(key),
            public=public,
            description=PLAYLIST_DESCRIPTION,
        )
        self.db.set_discovery_playlist_id(user_id, key, playlist_id)
        return playlist_id

    def capture(
        self,
        user_id: int,
        client: spotipy.Spotify,
        settings: dict[str, Any],
        playback: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Archive the currently-playing track if it came from a discovery source.

        Returns the archived track, or None. Assumes the caller has already ensured this
        playback instance hasn't been handled yet.
        """
        if not settings.get("discovery_enabled"):
            return None

        source_id = playlist_id_from_context(playback.get("context"))
        if not source_id:
            return None

        track = playback.get("item") or {}
        track_id = track.get("id")
        if not track_id:
            return None

        duration_ms = int(track.get("duration_ms") or 0)
        progress_ms = int(playback.get("progress_ms") or 0)
        if duration_ms <= 0:
            return None
        if (progress_ms / duration_ms) < float(settings["min_completion_ratio"]):
            return None

        artists = track.get("artists") or []
        name = str(track.get("name") or "Unknown Track")
        artist = str(
            (artists[0].get("name") if artists and artists[0] else None) or "Unknown Artist"
        )

        known = {source["playlist_id"] for source in self.db.discovery_sources(user_id)}
        if source_id not in known:
            # We can't read the playlist's name, but we can show the user which track
            # they just heard from it and let them label it in one click.
            self.db.note_seen_context(user_id, source_id, f"{artist} - {name}")
            return None

        key = month_key()
        if not self.db.archive_discovery(user_id, key, str(track_id), name, artist, source_id):
            return None

        try:
            playlist_id = self.month_playlist(
                user_id, client, key, bool(settings["playlist_public"])
            )
            playlists.add_tracks(client, playlist_id, [str(track_id)], self.cache)
        except Exception:
            # Keep the ledger honest: if Spotify rejected the write, forget the row so
            # the next play of this track retries instead of silently skipping forever.
            self.db.unarchive_discovery(user_id, key, str(track_id))
            raise

        log.info(
            "Archived %s - %s to %s (user %s)", artist, name, month_playlist_name(key), user_id
        )
        return {"track_id": str(track_id), "name": name, "artist": artist, "month": key}
