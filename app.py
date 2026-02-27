import html
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
PROFILE_URL_RE = re.compile(r"(?:https?://)?open\.spotify\.com/user/([A-Za-z0-9_]+)", re.IGNORECASE)
PROFILE_URI_RE = re.compile(r"spotify:user:([A-Za-z0-9_]+)", re.IGNORECASE)


def _now_seconds() -> int:
    return int(time.time())


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


@dataclass(frozen=True)
class AppConfig:
    client_id: str
    client_secret: str
    http_timeout_seconds: float
    token_skew_seconds: int
    max_lookup_candidates: int
    max_public_playlists: int
    max_liked_playlists: int
    max_tracks_per_liked_playlist: int
    default_playlist_track_limit: int
    liked_playlist_keywords: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "AppConfig":
        missing = [name for name in ("CLIENT_ID", "CLIENT_SECRET") if not os.getenv(name)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

        raw_keywords = os.getenv(
            "LIKED_PLAYLIST_KEYWORDS",
            "liked,favorite,favourites,favorites,saved,heart,hearts",
        )
        keywords = tuple(
            dict.fromkeys(k.strip().lower() for k in raw_keywords.split(",") if k.strip())
        )

        return cls(
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            http_timeout_seconds=float(os.getenv("SPOTIFY_HTTP_TIMEOUT_SECONDS", "20")),
            token_skew_seconds=max(0, int(os.getenv("TOKEN_SKEW_SECONDS", "60"))),
            max_lookup_candidates=max(3, int(os.getenv("MAX_LOOKUP_CANDIDATES", "12"))),
            max_public_playlists=max(1, int(os.getenv("MAX_PUBLIC_PLAYLISTS", "200"))),
            max_liked_playlists=max(1, int(os.getenv("MAX_LIKED_PLAYLISTS", "4"))),
            max_tracks_per_liked_playlist=max(
                1,
                int(os.getenv("MAX_TRACKS_PER_LIKED_PLAYLIST", "75")),
            ),
            default_playlist_track_limit=max(
                1,
                int(os.getenv("DEFAULT_PLAYLIST_TRACK_LIMIT", "100")),
            ),
            liked_playlist_keywords=keywords,
        )


class SpotifyPublicService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._token_lock = threading.Lock()
        self._cached_access_token: Optional[str] = None
        self._cached_token_expires_at = 0

    def _fetch_access_token(self) -> tuple[str, int]:
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.config.client_id, self.config.client_secret),
            timeout=self.config.http_timeout_seconds,
        )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify token request failed ({response.status_code}): {response.text}",
            )

        payload = response.json()
        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 3600))
        if not access_token:
            raise HTTPException(status_code=502, detail="Spotify token response missing access_token")

        return str(access_token), _now_seconds() + expires_in

    def _access_token(self) -> str:
        with self._token_lock:
            remaining = self._cached_token_expires_at - _now_seconds()
            if self._cached_access_token and remaining > self.config.token_skew_seconds:
                return self._cached_access_token

            token, expires_at = self._fetch_access_token()
            self._cached_access_token = token
            self._cached_token_expires_at = expires_at
            return token

    def _reset_cached_token(self) -> None:
        with self._token_lock:
            self._cached_access_token = None
            self._cached_token_expires_at = 0

    def _api_get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        retry_on_auth_error: bool = True,
    ) -> Dict[str, Any]:
        token = self._access_token()
        response = requests.get(
            f"{SPOTIFY_API_BASE}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=self.config.http_timeout_seconds,
        )

        if response.status_code == 401 and retry_on_auth_error:
            self._reset_cached_token()
            return self._api_get(path, params, retry_on_auth_error=False)

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Spotify resource not found")

        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify API request failed ({response.status_code}): {response.text}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Spotify API returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Unexpected Spotify API response format")

        return payload

    def _extract_user_id(self, query: str) -> Optional[str]:
        raw = query.strip()
        if not raw:
            return None

        match = PROFILE_URL_RE.search(raw)
        if match:
            return match.group(1)

        match = PROFILE_URI_RE.search(raw)
        if match:
            return match.group(1)

        if raw.startswith("@"):
            raw = raw[1:]

        if re.fullmatch(r"[A-Za-z0-9_]+", raw):
            return raw

        return None

    def _format_profile(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        images = payload.get("images") or []
        image_url = images[0].get("url") if images and isinstance(images[0], dict) else None
        external = payload.get("external_urls") or {}
        followers = payload.get("followers") or {}

        return {
            "id": payload.get("id"),
            "display_name": payload.get("display_name") or payload.get("id") or "Unknown",
            "external_url": external.get("spotify"),
            "followers_total": int(followers.get("total") or 0),
            "image_url": image_url,
        }

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        encoded_id = urllib.parse.quote(user_id, safe="")
        profile = self._api_get(f"users/{encoded_id}")
        return self._format_profile(profile)

    def _try_get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.get_profile(user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            raise

    def _score_candidate(self, query: str, profile: Dict[str, Any], source_weight: int) -> int:
        normalized_query = _normalize_text(query.lstrip("@").strip())
        normalized_id = _normalize_text(str(profile.get("id") or ""))
        normalized_name = _normalize_text(str(profile.get("display_name") or ""))
        score = source_weight

        if normalized_query and normalized_query == normalized_id:
            score += 100
        elif normalized_query and normalized_query in normalized_id:
            score += 45

        if normalized_query and normalized_query == normalized_name:
            score += 85
        elif normalized_query and normalized_query in normalized_name:
            score += 40

        return score

    def _search_owner_candidates(self, query: str, limit: int) -> list[str]:
        payload = self._api_get(
            "search",
            params={
                "q": query,
                "type": "playlist",
                "limit": max(1, min(50, limit)),
            },
        )
        playlists = (payload.get("playlists") or {}).get("items") or []

        owner_ids: list[str] = []
        seen_ids: set[str] = set()
        for playlist in playlists:
            if not isinstance(playlist, dict):
                continue
            owner = playlist.get("owner") or {}
            owner_id = owner.get("id")
            if not owner_id or owner_id in seen_ids:
                continue
            seen_ids.add(owner_id)
            owner_ids.append(str(owner_id))

        return owner_ids

    def resolve_user_candidates(self, query: str) -> list[Dict[str, Any]]:
        cleaned_query = query.strip()
        direct_user_id = self._extract_user_id(cleaned_query)

        candidate_ids: list[tuple[str, int, str]] = []
        if direct_user_id:
            candidate_ids.append((direct_user_id, 130, "direct"))

        for owner_id in self._search_owner_candidates(
            cleaned_query,
            self.config.max_lookup_candidates,
        ):
            candidate_ids.append((owner_id, 55, "playlist-search"))

        seen: set[str] = set()
        candidates: list[Dict[str, Any]] = []
        for user_id, source_weight, source in candidate_ids:
            if user_id in seen:
                continue
            seen.add(user_id)
            profile = self._try_get_profile(user_id)
            if not profile:
                continue
            score = self._score_candidate(cleaned_query, profile, source_weight)
            candidates.append(
                {
                    "source": source,
                    "score": score,
                    "profile": profile,
                }
            )

        candidates.sort(
            key=lambda item: (
                int(item["score"]),
                int(item["profile"].get("followers_total") or 0),
                str(item["profile"].get("id") or ""),
            ),
            reverse=True,
        )
        return candidates

    def _format_playlist(self, payload: Dict[str, Any], selected_user_id: str) -> Dict[str, Any]:
        owner = payload.get("owner") or {}
        tracks_ref = payload.get("tracks") or {}
        images = payload.get("images") or []
        image_url = images[0].get("url") if images and isinstance(images[0], dict) else None
        external = payload.get("external_urls") or {}
        description = html.unescape(str(payload.get("description") or "")).strip()

        owner_id = str(owner.get("id") or "")
        owner_display = owner.get("display_name") or owner_id or "Unknown"

        return {
            "id": payload.get("id"),
            "name": payload.get("name") or "Untitled playlist",
            "description": description,
            "tracks_total": int(tracks_ref.get("total") or 0),
            "public": bool(payload.get("public")) if payload.get("public") is not None else None,
            "collaborative": bool(payload.get("collaborative")),
            "external_url": external.get("spotify"),
            "image_url": image_url,
            "owner": {
                "id": owner_id,
                "display_name": owner_display,
            },
            "relationship": "owned" if owner_id == selected_user_id else "followed_public",
        }

    def get_public_playlists(self, user_id: str, max_playlists: int) -> list[Dict[str, Any]]:
        encoded_id = urllib.parse.quote(user_id, safe="")
        playlists: list[Dict[str, Any]] = []
        offset = 0

        while len(playlists) < max_playlists:
            page_size = min(50, max_playlists - len(playlists))
            payload = self._api_get(
                f"users/{encoded_id}/playlists",
                params={"limit": page_size, "offset": offset},
            )
            items = payload.get("items") or []
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                formatted = self._format_playlist(item, user_id)
                if formatted.get("id"):
                    playlists.append(formatted)
                if len(playlists) >= max_playlists:
                    break

            if not payload.get("next"):
                break
            offset += len(items)

        playlists.sort(
            key=lambda item: (
                item.get("relationship") != "owned",
                -int(item.get("tracks_total") or 0),
                str(item.get("name") or "").lower(),
            )
        )
        return playlists

    def get_playlist_tracks(self, playlist_id: str, limit: int) -> Dict[str, Any]:
        encoded_id = urllib.parse.quote(playlist_id, safe="")
        tracks: list[Dict[str, Any]] = []
        offset = 0
        total = 0

        while len(tracks) < limit:
            page_size = min(100, limit - len(tracks))
            payload = self._api_get(
                f"playlists/{encoded_id}/tracks",
                params={
                    "limit": page_size,
                    "offset": offset,
                    "fields": "items(track(id,name,duration_ms,explicit,external_urls,preview_url,artists(id,name),album(id,name,images))),next,total",
                },
            )

            total = int(payload.get("total") or total or 0)
            items = payload.get("items") or []
            if not items:
                break

            for entry in items:
                if not isinstance(entry, dict):
                    continue
                track = entry.get("track")
                if not isinstance(track, dict):
                    continue
                track_id = track.get("id")
                if not track_id:
                    continue

                artists = track.get("artists") or []
                artist_names = [artist.get("name") for artist in artists if isinstance(artist, dict) and artist.get("name")]
                album = track.get("album") or {}
                album_images = album.get("images") or []
                album_image_url = (
                    album_images[0].get("url") if album_images and isinstance(album_images[0], dict) else None
                )

                tracks.append(
                    {
                        "id": track_id,
                        "name": track.get("name") or "Unknown Track",
                        "artists": artist_names,
                        "duration_ms": int(track.get("duration_ms") or 0),
                        "explicit": bool(track.get("explicit")),
                        "album": {
                            "id": album.get("id"),
                            "name": album.get("name") or "Unknown Album",
                            "image_url": album_image_url,
                        },
                        "external_url": (track.get("external_urls") or {}).get("spotify"),
                        "preview_url": track.get("preview_url"),
                    }
                )

                if len(tracks) >= limit:
                    break

            if not payload.get("next"):
                break
            offset += len(items)

        return {
            "playlist_id": playlist_id,
            "total": total,
            "tracks": tracks,
        }

    def _infer_liked_playlists(self, playlists: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        liked: list[Dict[str, Any]] = []
        keywords = self.config.liked_playlist_keywords

        for playlist in playlists:
            name = str(playlist.get("name") or "").lower()
            description = str(playlist.get("description") or "").lower()
            matched = [kw for kw in keywords if kw and (kw in name or kw in description)]
            if not matched:
                continue
            enriched = dict(playlist)
            enriched["matched_keywords"] = matched
            liked.append(enriched)

        liked.sort(key=lambda item: int(item.get("tracks_total") or 0), reverse=True)
        return liked

    def _collect_derived_liked_tracks(self, liked_playlists: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        derived_tracks: list[Dict[str, Any]] = []
        seen_track_ids: set[str] = set()

        for playlist in liked_playlists[: self.config.max_liked_playlists]:
            playlist_id = playlist.get("id")
            if not playlist_id:
                continue
            payload = self.get_playlist_tracks(
                playlist_id,
                self.config.max_tracks_per_liked_playlist,
            )
            for track in payload.get("tracks", []):
                track_id = track.get("id")
                if not track_id or track_id in seen_track_ids:
                    continue
                seen_track_ids.add(track_id)
                derived_tracks.append(
                    {
                        **track,
                        "source_playlist": {
                            "id": playlist_id,
                            "name": playlist.get("name") or "Unknown playlist",
                        },
                    }
                )

        return derived_tracks

    def lookup_public_account(self, query: str) -> Dict[str, Any]:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise HTTPException(status_code=400, detail="Query is required")

        candidates = self.resolve_user_candidates(cleaned_query)
        if not candidates:
            raise HTTPException(
                status_code=404,
                detail="No public Spotify user could be resolved from that username/tag.",
            )

        selected = candidates[0]["profile"]
        selected_user_id = str(selected.get("id") or "")
        playlists = self.get_public_playlists(selected_user_id, self.config.max_public_playlists)

        owned_playlists = [p for p in playlists if p.get("relationship") == "owned"]
        followed_public_playlists = [p for p in playlists if p.get("relationship") == "followed_public"]
        inferred_liked_playlists = self._infer_liked_playlists(playlists)
        derived_liked_tracks = self._collect_derived_liked_tracks(inferred_liked_playlists)

        return {
            "query": cleaned_query,
            "selected_user": selected,
            "candidate_users": [
                {
                    "source": candidate["source"],
                    "score": candidate["score"],
                    "profile": candidate["profile"],
                }
                for candidate in candidates
            ],
            "playlists": {
                "all": playlists,
                "owned": owned_playlists,
                "followed_public": followed_public_playlists,
            },
            "liked_songs": {
                "available": False,
                "reason": "Spotify does not expose another user's Liked Songs via the public Web API.",
                "derived_from_public_playlists": inferred_liked_playlists,
                "derived_tracks": derived_liked_tracks,
            },
            "following": {
                "available": False,
                "reason": "Spotify does not expose another user's followed artists/users via the public Web API.",
                "public_followed_playlists": followed_public_playlists,
            },
            "stats": {
                "playlists_total": len(playlists),
                "owned_playlists_total": len(owned_playlists),
                "followed_public_playlists_total": len(followed_public_playlists),
                "inferred_liked_playlists_total": len(inferred_liked_playlists),
                "derived_liked_tracks_total": len(derived_liked_tracks),
            },
            "limitations": [
                "Spotify public API does not provide another user's private Liked Songs collection.",
                "Spotify public API does not provide another user's following graph (artists/users).",
                "Any 'liked songs' shown here are inferred from public playlists with liked/favorite-style naming.",
            ],
        }


config = AppConfig.from_env()
spotify_service = SpotifyPublicService(config)

app = FastAPI(
    title="FavSongs Public Explorer",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/api/health")
async def api_health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/public/lookup")
async def public_lookup(query: str = Query(..., min_length=2, max_length=200)) -> Dict[str, Any]:
    return spotify_service.lookup_public_account(query)


@app.get("/api/public/playlists/{playlist_id}/tracks")
async def public_playlist_tracks(
    playlist_id: str,
    limit: int = Query(default=config.default_playlist_track_limit, ge=1, le=500),
) -> Dict[str, Any]:
    return spotify_service.get_playlist_tracks(playlist_id, limit)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


ui_root = os.path.join("ui", "option-1")
ui_dist = os.path.join(ui_root, "dist")
ui_legacy = os.path.join(ui_root, "legacy")
if os.path.isdir(ui_dist):
    ui_dir = ui_dist
elif os.path.isdir(ui_legacy):
    ui_dir = ui_legacy
else:
    ui_dir = ui_root

app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
