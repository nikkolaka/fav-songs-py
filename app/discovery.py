"""Monthly discovery archive.

The Web API cannot read Discover Weekly: `GET /playlists/{id}/items` is
owner/collaborator-only and 403s otherwise, and since 2024-11-27 algorithmic and
Spotify-owned playlists are closed to any app that wasn't already in extended quota
mode. Confirmed against this account -- it returns 404.

Spotify's own public embed page for the same playlist does serve it. That's the page
that renders when you share a playlist link into Slack or a blog, it needs no auth, and
it carries the full track list. So the archive reads that, and falls back to capturing
whatever gets played from the playlist (`context.uri`, which stays visible even when the
playlist behind it doesn't) if the page's shape ever changes.

Tracks by artists on the live AI blocklist are filtered out before anything is written.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

import requests
import spotipy

from . import playlists
from .aiblocklist import AiBlocklist
from .db import Database
from .playlists import PlaylistCache

log = logging.getLogger(__name__)

EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
OEMBED_URL = "https://open.spotify.com/oembed"
EMBED_TIMEOUT = 25
EMBED_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)

PLAYLIST_DESCRIPTION = "Discoveries from this month. Maintained automatically."

# Playlists we register as discovery sources without asking, because they are exactly
# what this feature is for. Anything else needs one click from the user.
AUTO_SOURCE_TITLES = {"discover weekly"}


class EmbedUnavailable(Exception):
    """The embed page didn't parse. Callers fall back to play-based capture."""


def month_key(moment: Optional[datetime] = None) -> str:
    """Stable sort/group key, e.g. '2026-07'."""
    return (moment or datetime.now()).strftime("%Y-%m")


def month_playlist_name(key: str) -> str:
    """'2026-07' -> "July's Discover"."""
    return f"{datetime.strptime(key, '%Y-%m').strftime('%B')}'s Discover"


def playlist_id_from_context(context: Optional[dict[str, Any]]) -> Optional[str]:
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
        candidate = value.split("playlist/", 1)[-1].split("?", 1)[0].split("/", 1)[0]
        return candidate or None
    return value if value.isalnum() else None


def playlist_title(playlist_id: str) -> Optional[str]:
    """Playlist name via oEmbed -- a published link-preview endpoint, no auth needed.

    Used to show a real name instead of a raw id, so nobody has to identify a playlist
    by its 22-character hash.
    """
    try:
        response = requests.get(
            OEMBED_URL,
            params={"url": f"https://open.spotify.com/playlist/{playlist_id}"},
            timeout=EMBED_TIMEOUT,
            headers={"User-Agent": EMBED_UA},
        )
        response.raise_for_status()
        return str(response.json().get("title") or "") or None
    except Exception as exc:
        log.debug("oEmbed lookup failed for %s: %s", playlist_id, exc)
        return None


def read_playlist_embed(playlist_id: str) -> dict[str, Any]:
    """Full track list from the public embed page. Raises EmbedUnavailable on any problem."""
    try:
        response = requests.get(
            EMBED_URL.format(playlist_id=playlist_id),
            timeout=EMBED_TIMEOUT,
            headers={"User-Agent": EMBED_UA},
        )
        response.raise_for_status()
    except Exception as exc:
        raise EmbedUnavailable(f"fetch failed: {exc}") from exc

    match = NEXT_DATA_RE.search(response.text)
    if not match:
        raise EmbedUnavailable("no __NEXT_DATA__ block on the page")

    try:
        entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        raw_tracks = entity["trackList"]
    except (KeyError, TypeError, ValueError) as exc:
        raise EmbedUnavailable(f"unexpected page structure: {exc}") from exc

    tracks = []
    for item in raw_tracks:
        uri = str((item or {}).get("uri") or "")
        if not uri.startswith("spotify:track:"):
            continue  # local files and episodes have no track id to add
        tracks.append(
            {
                "track_id": uri.split(":")[-1],
                "name": str(item.get("title") or "Unknown Track"),
                "artist": str(item.get("subtitle") or "Unknown Artist"),
            }
        )

    if not tracks:
        raise EmbedUnavailable("page parsed but contained no tracks")

    return {"name": str(entity.get("name") or ""), "tracks": tracks}


class Discovery:
    def __init__(self, db: Database, cache: PlaylistCache, blocklist: AiBlocklist):
        self.db = db
        self.cache = cache
        self.blocklist = blocklist

    # ------------------------------------------------------------- playlists

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

    # -------------------------------------------------------------- blocking

    def _artist_ids(self, client: spotipy.Spotify, track_id: str) -> list[str]:
        """Artist ids for a track. The embed only gives names, and names aren't unique."""
        try:
            track = client.track(track_id)
        except Exception as exc:
            log.debug("Could not resolve artists for %s: %s", track_id, exc)
            return []
        return [a["id"] for a in (track.get("artists") or []) if a and a.get("id")]

    def _blocked_reason(
        self, client: spotipy.Spotify, track: dict[str, str]
    ) -> Optional[str]:
        """Why this track is blocked, or None. Fails open when the list isn't loaded."""
        if not self.blocklist.loaded:
            return None

        artist_ids = self._artist_ids(client, track["track_id"])
        if artist_ids:
            hit = self.blocklist.blocks_artist_ids(artist_ids)
            return f"AI artist {hit}" if hit else None

        # Couldn't resolve ids -- fall back to the looser name check rather than
        # letting an unresolvable track bypass the filter entirely.
        hit = self.blocklist.blocks_artist_name(track["artist"])
        return f"AI artist name {hit!r}" if hit else None

    # ----------------------------------------------------------------- sweep

    def sweep_sources(
        self, user_id: int, client: spotipy.Spotify, settings: dict[str, Any]
    ) -> dict[str, Any]:
        """Read Discover Weekly and archive everything new this month."""
        summary: dict[str, Any] = {"archived": 0, "blocked": 0, "errors": []}
        if not settings.get("discovery_enabled"):
            return summary

        self.blocklist.refresh()
        key = month_key()

        sources = [
            s for s in self.db.discovery_sources(user_id)
            if s["label"].casefold() in AUTO_SOURCE_TITLES
        ]
        for source in sources:
            playlist_id = source["playlist_id"]
            try:
                data = read_playlist_embed(playlist_id)
            except EmbedUnavailable as exc:
                # Not fatal: play-based capture still runs on every poll.
                log.warning("Embed read failed for %s: %s", playlist_id, exc)
                summary["errors"].append({"playlist_id": playlist_id, "error": str(exc)})
                self.db.set_source_degraded(user_id, playlist_id, str(exc))
                continue

            self.db.set_source_degraded(user_id, playlist_id, None)
            fresh: list[dict[str, str]] = []

            for track in data["tracks"]:
                if self.db.is_seen_this_month(user_id, key, track["track_id"]):
                    continue

                reason = self._blocked_reason(client, track)
                if reason:
                    self.db.record_blocked(
                        user_id, key, track["track_id"], track["name"], track["artist"], reason
                    )
                    summary["blocked"] += 1
                    log.info("Blocked %s - %s (%s)", track["artist"], track["name"], reason)
                    continue

                if self.db.archive_discovery(
                    user_id, key, track["track_id"], track["name"], track["artist"], playlist_id
                ):
                    fresh.append(track)

            if not fresh:
                continue

            try:
                month_id = self.month_playlist(
                    user_id, client, key, bool(settings["playlist_public"])
                )
                playlists.add_tracks(
                    client, month_id, [t["track_id"] for t in fresh], self.cache
                )
            except Exception:
                # Keep the ledger honest so the next sweep retries these.
                for track in fresh:
                    self.db.unarchive_discovery(user_id, key, track["track_id"])
                raise

            summary["archived"] += len(fresh)
            log.info(
                "Archived %s track(s) from %r to %s",
                len(fresh),
                source["label"],
                month_playlist_name(key),
            )

        return summary
