"""Spotify OAuth and per-user API clients.

Everything goes through spotipy 2.26.0, which is the first release that targets the
February 2026 endpoint layout (`/playlists/{id}/items`, `POST /me/playlists`). Do not
downgrade -- 2.25.x calls paths that no longer exist.
"""

import logging
import urllib.parse
from typing import Any, Optional

import requests
import spotipy

from .config import AppConfig
from .db import Database, now_seconds

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

# Refresh a little early so a long request can't start with a token that expires mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS = 60


class SpotifyAuthError(Exception):
    """The user's grant is gone -- they have to reconnect."""


class SpotifyService:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def auth_url(self, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": self.config.scope,
                "state": state,
                # Forces the account chooser, so a second family member on a shared
                # browser doesn't silently land back on the first person's account.
                "show_dialog": "true",
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _token_request(self, payload: dict[str, str]) -> dict[str, Any]:
        response = requests.post(
            TOKEN_URL,
            data=payload,
            auth=(self.config.client_id, self.config.client_secret),
            timeout=20,
        )
        if response.status_code >= 400:
            body = response.text[:400]
            if "invalid_grant" in body:
                raise SpotifyAuthError("Spotify rejected the grant")
            raise RuntimeError(f"Spotify token request failed ({response.status_code}): {body}")
        return response.json()

    def exchange_code(self, code: str) -> dict[str, Any]:
        """Swap an authorization code for tokens and return the caller's profile."""
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            }
        )

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not access_token or not refresh_token:
            raise RuntimeError("Spotify OAuth response was incomplete")

        profile = spotipy.Spotify(auth=access_token, requests_timeout=20).me()
        if not profile.get("id"):
            raise RuntimeError("Spotify profile did not include a user id")

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": now_seconds() + int(payload.get("expires_in", 3600)),
            "spotify_user_id": profile["id"],
            "display_name": profile.get("display_name") or profile["id"],
        }

    def access_token(self, user_id: int) -> str:
        tokens = self.db.tokens(user_id)
        if not tokens:
            raise SpotifyAuthError("No stored Spotify tokens")

        if int(tokens["expires_at"]) > now_seconds() + TOKEN_REFRESH_MARGIN_SECONDS:
            return str(tokens["access_token"])

        try:
            refreshed = self._token_request(
                {"grant_type": "refresh_token", "refresh_token": str(tokens["refresh_token"])}
            )
        except SpotifyAuthError:
            # Revoked from the Spotify account page, or the app allowlist changed.
            self.db.clear_tokens(user_id)
            raise

        access_token = refreshed.get("access_token")
        if not access_token:
            raise RuntimeError("Spotify refresh response was incomplete")

        # Spotify only sometimes rotates the refresh token; keep the old one otherwise.
        self.db.save_tokens(
            user_id,
            str(access_token),
            str(refreshed.get("refresh_token") or tokens["refresh_token"]),
            now_seconds() + int(refreshed.get("expires_in", 3600)),
        )
        return str(access_token)

    def client(self, user_id: int) -> spotipy.Spotify:
        return spotipy.Spotify(auth=self.access_token(user_id), requests_timeout=20, retries=0)


def retry_after_seconds(error: spotipy.SpotifyException) -> Optional[int]:
    """Seconds to wait from a 429, per the Retry-After header.

    Development Mode groups endpoints into quota buckets and answers with
    429 + `"reason": "QUOTA_EXCEEDED"` when one is drained.
    """
    if error.http_status != 429:
        return None
    headers = getattr(error, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 60
