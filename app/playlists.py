"""Find-or-create playlists by name, and cached membership lookups.

All calls use the post-February-2026 spotipy methods:
  current_user_playlists()        -> GET  /me/playlists
  current_user_playlist_create()  -> POST /me/playlists
  playlist_items()                -> GET  /playlists/{id}/items
The `user_*` equivalents hit `/users/{id}/...`, which Spotify removed.
"""

import logging
import time
from typing import Any, Optional

import spotipy

log = logging.getLogger(__name__)

MEMBERSHIP_TTL_SECONDS = 30
PAGE_LIMIT = 50
ADD_BATCH = 100


class PlaylistCache:
    """Track ids per playlist, so repeated auto-adds don't re-read the whole playlist."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def get(self, playlist_id: str) -> Optional[list[dict[str, str]]]:
        entry = self._entries.get(playlist_id)
        if not entry:
            return None
        if time.time() - entry["cached_at"] > MEMBERSHIP_TTL_SECONDS:
            return None
        return list(entry["tracks"])

    def put(self, playlist_id: str, tracks: list[dict[str, str]]) -> None:
        self._entries[playlist_id] = {"tracks": tracks, "cached_at": time.time()}

    def invalidate(self, playlist_id: Optional[str] = None) -> None:
        if playlist_id is None:
            self._entries.clear()
        else:
            self._entries.pop(playlist_id, None)


def find_playlist_by_name(client: spotipy.Spotify, name: str) -> Optional[str]:
    """Look for a playlist the user owns or follows with this exact name.

    Needs the playlist-read-private scope, or private playlists are invisible here and
    we would create a duplicate on every startup.
    """
    results = client.current_user_playlists(limit=PAGE_LIMIT)
    while results:
        for playlist in results.get("items", []):
            if playlist and playlist.get("name") == name and playlist.get("id"):
                return str(playlist["id"])
        results = client.next(results) if results.get("next") else None
    return None


def find_or_create(
    client: spotipy.Spotify, name: str, public: bool, description: str
) -> str:
    existing = find_playlist_by_name(client, name)
    if existing:
        return existing

    created = client.current_user_playlist_create(
        name=name, public=public, description=description
    )
    playlist_id = created.get("id")
    if not playlist_id:
        raise RuntimeError(f"Spotify did not return an id when creating {name!r}")
    log.info("Created playlist %r (%s)", name, playlist_id)
    return str(playlist_id)


def items(
    client: spotipy.Spotify, playlist_id: str, cache: Optional[PlaylistCache] = None
) -> list[dict[str, str]]:
    if cache:
        cached = cache.get(playlist_id)
        if cached is not None:
            return cached

    tracks: list[dict[str, str]] = []
    seen: set[str] = set()
    results = client.playlist_items(
        playlist_id,
        limit=PAGE_LIMIT,
        additional_types=("track",),
        fields="next,items(track(id,name,artists(name)))",
    )
    while results:
        for item in results.get("items", []):
            track = (item or {}).get("track") or {}
            track_id = track.get("id")
            if not track_id or track_id in seen:
                continue
            seen.add(str(track_id))
            artists = track.get("artists") or []
            tracks.append(
                {
                    "track_id": str(track_id),
                    "name": str(track.get("name") or "Unknown Track"),
                    "artist": str(
                        (artists[0].get("name") if artists and artists[0] else None)
                        or "Unknown Artist"
                    ),
                }
            )
        results = client.next(results) if results.get("next") else None

    if cache:
        cache.put(playlist_id, tracks)
    return tracks


def track_ids(
    client: spotipy.Spotify, playlist_id: str, cache: Optional[PlaylistCache] = None
) -> set[str]:
    return {entry["track_id"] for entry in items(client, playlist_id, cache)}


def add_tracks(
    client: spotipy.Spotify,
    playlist_id: str,
    ids: list[str],
    cache: Optional[PlaylistCache] = None,
    position: Optional[int] = None,
) -> int:
    """Add tracks that aren't already present. Returns how many were actually added."""
    if not ids:
        return 0

    existing = track_ids(client, playlist_id, cache)
    fresh = [tid for tid in dict.fromkeys(ids) if tid not in existing]
    if not fresh:
        return 0

    for start in range(0, len(fresh), ADD_BATCH):
        client.playlist_add_items(playlist_id, fresh[start : start + ADD_BATCH], position=position)
    if cache:
        cache.invalidate(playlist_id)
    return len(fresh)


def remove_tracks(
    client: spotipy.Spotify,
    playlist_id: str,
    ids: list[str],
    cache: Optional[PlaylistCache] = None,
) -> int:
    existing = track_ids(client, playlist_id, cache)
    removable = [tid for tid in dict.fromkeys(ids) if tid in existing]
    if not removable:
        return 0

    for start in range(0, len(removable), ADD_BATCH):
        client.playlist_remove_all_occurrences_of_items(
            playlist_id, removable[start : start + ADD_BATCH]
        )
    if cache:
        cache.invalidate(playlist_id)
    return len(removable)
