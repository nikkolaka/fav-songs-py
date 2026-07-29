"""Live AI-artist blocklist, from github.com/CennoxX/spotify-ai-blocker.

The CSV ships Spotify artist *ids*, not just names, so matching is exact -- a real band
that happens to share a name with an AI act is never blocked. It's updated most days, so
it's fetched live rather than vendored, cached on disk, and falls back to the last good
copy when GitHub is unreachable.

Fails open on purpose: if the list can't be loaded, tracks are archived rather than
dropped. Silently losing music you wanted is worse than archiving one you didn't.
"""

import csv
import io
import logging
import os
import time
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

CSV_URL = "https://raw.githubusercontent.com/CennoxX/spotify-ai-blocker/main/SpotifyAiArtists.csv"
REFRESH_SECONDS = 86_400
FETCH_TIMEOUT = 30

# The list is ~7.5k rows / ~260KB. Anything wildly outside that is a bad download
# (a GitHub error page, a truncated response) and must not replace a good cache.
MIN_PLAUSIBLE_ROWS = 500


class AiBlocklist:
    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self._ids: set[str] = set()
        self._names: set[str] = set()
        self._fetched_at: float = 0.0
        self._error: Optional[str] = None
        self._load_cache()

    # ------------------------------------------------------------------ state

    @property
    def loaded(self) -> bool:
        return bool(self._ids)

    def status(self) -> dict[str, Any]:
        return {
            "artists": len(self._ids),
            "fetched_at": int(self._fetched_at) or None,
            "stale": self._is_stale(),
            "error": self._error,
        }

    def _is_stale(self) -> bool:
        return (time.time() - self._fetched_at) > REFRESH_SECONDS

    # ------------------------------------------------------------------- data

    def _parse(self, text: str) -> tuple[set[str], set[str]]:
        ids: set[str] = set()
        names: set[str] = set()
        for row in csv.DictReader(io.StringIO(text)):
            artist_id = (row.get("id") or "").strip()
            name = (row.get("artist") or "").strip()
            if artist_id:
                ids.add(artist_id)
            if name:
                names.add(name.casefold())
        return ids, names

    def _load_cache(self) -> None:
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                self._ids, self._names = self._parse(handle.read())
            self._fetched_at = os.path.getmtime(self.cache_path)
            log.info("Loaded %s AI artists from cache", len(self._ids))
        except Exception as exc:
            log.warning("Could not read blocklist cache: %s", exc)

    def refresh(self, force: bool = False) -> bool:
        """Re-fetch if stale. Returns True if the in-memory list changed."""
        if not force and not self._is_stale():
            return False

        try:
            response = requests.get(CSV_URL, timeout=FETCH_TIMEOUT)
            response.raise_for_status()
            ids, names = self._parse(response.text)
        except Exception as exc:
            self._error = str(exc)
            log.warning("Blocklist fetch failed, keeping %s cached: %s", len(self._ids), exc)
            return False

        if len(ids) < MIN_PLAUSIBLE_ROWS:
            self._error = f"Refusing a suspiciously small blocklist ({len(ids)} rows)"
            log.warning("%s", self._error)
            return False

        changed = ids != self._ids
        self._ids, self._names = ids, names
        self._fetched_at = time.time()
        self._error = None

        try:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                handle.write(response.text)
        except Exception as exc:
            log.warning("Could not write blocklist cache: %s", exc)

        log.info("Blocklist refreshed: %s AI artists%s", len(ids), " (changed)" if changed else "")
        return changed

    # ---------------------------------------------------------------- matching

    def blocks_artist_ids(self, artist_ids: list[str]) -> Optional[str]:
        """Return the matching artist id, or None. Exact -- this is the real check."""
        for artist_id in artist_ids:
            if artist_id in self._ids:
                return artist_id
        return None

    def blocks_artist_name(self, name: str) -> Optional[str]:
        """Name fallback for when a track's artist ids can't be resolved.

        Looser than the id check and only used as a backstop, since names are not unique.
        """
        for part in str(name or "").split(","):
            candidate = part.strip().casefold()
            if candidate and candidate in self._names:
                return part.strip()
        return None
