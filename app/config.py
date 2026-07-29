"""Environment-derived configuration."""

import base64
import hashlib
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PLAYLIST_NAME = "Favourite Songs"

# No user-read-recently-played: Spotify's own history is never read, because it can't
# say how much of a track was heard. playlist-read-private is what lets us find an
# existing private favourites playlist instead of creating a second one.
SCOPES = (
    "user-read-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-modify-public",
    "playlist-modify-private",
)

# Spotify Development Mode allows at most five authorised users per app.
MAX_USERS = 5

SESSION_TTL_SECONDS = 30 * 24 * 3600
OAUTH_STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class AppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    db_path: str
    default_playlist_name: str
    session_secret: str
    cookie_secure: bool

    @property
    def scope(self) -> str:
        return " ".join(SCOPES)

    @property
    def fernet_key(self) -> bytes:
        """Key for encrypting refresh tokens at rest, derived from the session secret."""
        digest = hashlib.sha256(self.session_secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @classmethod
    def from_env(cls) -> "AppConfig":
        required = ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "SESSION_SECRET")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        db_path = os.getenv("FAVSONGS_DB_PATH", os.path.join("data", "favsongs.db"))

        return cls(
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            redirect_uri=os.environ["REDIRECT_URI"],
            db_path=db_path,
            default_playlist_name=(
                os.getenv("DEFAULT_PLAYLIST_NAME") or DEFAULT_PLAYLIST_NAME
            ),
            session_secret=os.environ["SESSION_SECRET"],
            cookie_secure=os.getenv("COOKIE_SECURE", "0").strip().lower()
            in {"1", "true", "yes"},
        )
