import os
import time
import signal
import sys
import sqlite3
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from typing import Dict, Optional, Any

# Load environment variables
load_dotenv()

class FavSongsTracker:
    def __init__(self):
        self._validate_env()

        self.data_dir = os.getenv("FAVSONGS_DATA_DIR", "data")
        os.makedirs(self.data_dir, exist_ok=True)

        self.db_path = os.getenv("FAVSONGS_DB_PATH", os.path.join(self.data_dir, "favsongs.db"))
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

        cache_path = os.path.join(self.data_dir, ".cache")
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=os.getenv("CLIENT_ID"),
            client_secret=os.getenv("CLIENT_SECRET"),
            redirect_uri=os.getenv("REDIRECT_URI"),
            scope="user-read-playback-state,playlist-modify-public",
            cache_path=cache_path
        ))

        self.playlist_id = None
        self.running = True

        self.current_track_id = None
        self.current_track_info = None
        self.current_track_progress_ms = 0
        self.current_track_duration_ms = 0
        self.current_track_timestamp_ms = 0
        self.current_track_play_instance_id = None

        self.last_progress_update = 0

        # Configuration
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "10"))
        self.favorite_threshold = int(os.getenv("FAVORITE_THRESHOLD", "5"))
        self.min_completion_ratio = float(os.getenv("MIN_COMPLETION_RATIO", "0.8"))
        self.min_play_gap_ms = int(os.getenv("MIN_PLAY_GAP_MS", "300000"))
        self.progress_update_interval = int(os.getenv("PROGRESS_UPDATE_INTERVAL", "5"))

        self.playlist_track_cache = set()
        self.cache_last_updated = 0
        self.cache_ttl = int(os.getenv("PLAYLIST_CACHE_TTL", "300"))

        # Set up signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _validate_env(self):
        missing = [key for key in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI") if not os.getenv(key)]
        if missing:
            missing_list = ", ".join(missing)
            raise ValueError(f"Missing required environment variables: {missing_list}")

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fav_songs (
                track_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                artist TEXT NOT NULL,
                occurrences INTEGER NOT NULL DEFAULT 0,
                last_played INTEGER NOT NULL DEFAULT 0,
                last_play_instance_id TEXT
            )
            """
        )
        self.conn.commit()

    def _close_db(self):
        if getattr(self, "conn", None):
            self.conn.close()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"\nReceived signal {signum}. Shutting down gracefully...")
        self.running = False
        self._close_db()
        sys.exit(0)

    def get_current_playback(self) -> Optional[Dict[str, Any]]:
        """Get current playback information"""
        try:
            results = self.sp.current_playback()
            if results is None:
                return None

            track_info = results.get("item")
            if not track_info:
                return None

            return results
        except Exception as e:
            print(f"Error getting current playback: {e}")
            return None

    def _find_or_create_playlist(self) -> Optional[str]:
        """Find existing playlist or create new one"""
        try:
            user_id = self.sp.me()["id"]
            playlists = self.sp.user_playlists(user_id, limit=50)

            for playlist in playlists["items"]:
                if playlist["name"] == "Favourite Songs - Whatsit":
                    print("[INFO] Found existing playlist")
                    return playlist["id"]

            print("[INFO] Creating new playlist...")
            playlist = self.sp.user_playlist_create(
                user_id,
                "Favourite Songs - Whatsit",
                public=True,
                description="Are these my favorites?"
            )
            print("[SUCCESS] Playlist created")
            return playlist["id"]

        except Exception as e:
            print(f"Error with playlist: {e}")
            return None

    def _is_song_completed_ms(self, progress_ms: int, duration_ms: int) -> bool:
        """Check if song has been played sufficiently"""
        if duration_ms <= 0:
            return False

        completion_ratio = progress_ms / duration_ms
        return completion_ratio >= self.min_completion_ratio

    def _derive_play_instance_id(self, track_id: str, timestamp_ms: int, progress_ms: int) -> Optional[str]:
        if not timestamp_ms:
            return None
        start_ms = timestamp_ms - progress_ms
        return f"{track_id}:{start_ms}"

    def _get_track_row(self, track_id: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.execute(
            "SELECT track_id, name, artist, occurrences, last_played, last_play_instance_id FROM fav_songs WHERE track_id = ?",
            (track_id,)
        )
        return cursor.fetchone()

    def _record_play(self, track: Dict[str, Any], timestamp_ms: int, play_instance_id: Optional[str]):
        track_id = track["id"]
        track_name = track["name"]
        artist_name = track["artists"][0]["name"]

        row = self._get_track_row(track_id)
        if row:
            if play_instance_id and row["last_play_instance_id"] == play_instance_id:
                return
            if (timestamp_ms - row["last_played"]) <= self.min_play_gap_ms:
                return
            occurrences = row["occurrences"] + 1
            self.conn.execute(
                """
                UPDATE fav_songs
                SET name = ?, artist = ?, occurrences = ?, last_played = ?, last_play_instance_id = ?
                WHERE track_id = ?
                """,
                (track_name, artist_name, occurrences, timestamp_ms, play_instance_id, track_id)
            )
        else:
            occurrences = 1
            self.conn.execute(
                """
                INSERT INTO fav_songs (track_id, name, artist, occurrences, last_played, last_play_instance_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (track_id, track_name, artist_name, occurrences, timestamp_ms, play_instance_id)
            )

        self.conn.commit()

        if occurrences == self.favorite_threshold:
            print(f"\n[FAVORITE] Adding to playlist after {occurrences} plays")
            self._add_to_playlist(track_id)
        else:
            remaining = self.favorite_threshold - occurrences
            print(f"\n[PROGRESS] Play count: {occurrences}/{self.favorite_threshold} ({remaining} more needed)")

    def _format_time(self, ms: int) -> str:
        """Convert milliseconds to MM:SS format"""
        seconds = ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _create_progress_bar(self, progress_ms: int, duration_ms: int, width: int = 30) -> str:
        """Create a text progress bar"""
        if duration_ms == 0:
            return "[" + "-" * width + "]"

        progress = progress_ms / duration_ms
        filled = int(progress * width)
        bar = "=" * filled + ">" if filled < width else "=" * width
        bar = bar.ljust(width, "-")
        return f"[{bar}]"

    def _log_track_start(self, track_info: Dict[str, Any]):
        """Log when a new track starts playing"""
        track_name = track_info["name"]
        artist_name = track_info["artists"][0]["name"]
        duration_str = self._format_time(track_info["duration_ms"])

        print(f"\n[STARTED] {artist_name} - {track_name}")
        print(f"Duration: {duration_str}")

    def _log_track_end(self, track_info: Dict[str, Any], was_completed: bool):
        """Log when a track ends"""
        track_name = track_info["name"]
        artist_name = track_info["artists"][0]["name"]

        status = "COMPLETED" if was_completed else "SKIPPED"
        print(f"[{status}] {artist_name} - {track_name}")

    def _log_track_progress(self, current_playback: Dict[str, Any]):
        """Log current track progress with progress bar"""
        track = current_playback["item"]
        progress_ms = current_playback.get("progress_ms", 0)
        duration_ms = track["duration_ms"]

        progress_time = self._format_time(progress_ms)
        duration_time = self._format_time(duration_ms)
        progress_bar = self._create_progress_bar(progress_ms, duration_ms)

        completion_pct = (progress_ms / duration_ms * 100) if duration_ms > 0 else 0

        print(f"\r{progress_bar} {progress_time}/{duration_time} ({completion_pct:.1f}%)", end="", flush=True)

    def _finalize_current_track(self):
        if not self.current_track_id or not self.current_track_info:
            return

        was_completed = self._is_song_completed_ms(self.current_track_progress_ms, self.current_track_duration_ms)
        self._log_track_end(self.current_track_info, was_completed)

        if was_completed:
            timestamp_ms = self.current_track_timestamp_ms or int(time.time() * 1000)
            self._record_play(self.current_track_info, timestamp_ms, self.current_track_play_instance_id)

    def _set_current_track(self, track: Dict[str, Any], progress_ms: int, duration_ms: int, timestamp_ms: int, play_instance_id: Optional[str]):
        self.current_track_id = track["id"]
        self.current_track_info = track
        self.current_track_progress_ms = progress_ms
        self.current_track_duration_ms = duration_ms
        self.current_track_timestamp_ms = timestamp_ms
        self.current_track_play_instance_id = play_instance_id
        self.last_progress_update = 0
        self._log_track_start(track)

    def process_current_track(self):
        """Process the currently playing track"""
        current_playback = self.get_current_playback()
        if not current_playback:
            return

        track = current_playback["item"]
        track_id = track["id"]
        progress_ms = current_playback.get("progress_ms", 0)
        duration_ms = track.get("duration_ms", 0)
        timestamp_ms = current_playback.get("timestamp", 0)
        is_playing = current_playback.get("is_playing", False)
        play_instance_id = self._derive_play_instance_id(track_id, timestamp_ms, progress_ms)

        if self.current_track_id is None:
            self._set_current_track(track, progress_ms, duration_ms, timestamp_ms, play_instance_id)
        elif track_id != self.current_track_id:
            self._finalize_current_track()
            self._set_current_track(track, progress_ms, duration_ms, timestamp_ms, play_instance_id)
        else:
            restarted = (
                self.current_track_play_instance_id
                and play_instance_id
                and play_instance_id != self.current_track_play_instance_id
                and (progress_ms + 5000) < self.current_track_progress_ms
            )

            if restarted:
                self._finalize_current_track()
                self._set_current_track(track, progress_ms, duration_ms, timestamp_ms, play_instance_id)
            else:
                if progress_ms > self.current_track_progress_ms:
                    self.current_track_progress_ms = progress_ms
                if duration_ms:
                    self.current_track_duration_ms = duration_ms
                if timestamp_ms:
                    self.current_track_timestamp_ms = timestamp_ms
                if play_instance_id:
                    self.current_track_play_instance_id = play_instance_id

        if is_playing:
            current_time = time.time()
            if current_time - self.last_progress_update > self.progress_update_interval:
                self._log_track_progress(current_playback)
                self.last_progress_update = current_time

    def _get_all_playlist_tracks(self, playlist_id: str) -> set:
        """Get all track IDs from playlist, handling pagination"""
        all_track_ids = set()

        try:
            results = self.sp.playlist_tracks(playlist_id, limit=100)

            while results:
                for item in results["items"]:
                    if item["track"] and item["track"]["id"]:
                        all_track_ids.add(item["track"]["id"])

                if results["next"]:
                    results = self.sp.next(results)
                else:
                    break

        except Exception as e:
            print(f"Error fetching playlist tracks: {e}")

        return all_track_ids

    def _get_playlist_tracks_cached(self, playlist_id: str) -> set:
        """Get playlist tracks with caching to reduce API calls"""
        current_time = time.time()

        if (current_time - self.cache_last_updated) < self.cache_ttl:
            return self.playlist_track_cache

        self.playlist_track_cache = self._get_all_playlist_tracks(playlist_id)
        self.cache_last_updated = current_time

        return self.playlist_track_cache

    def _add_to_playlist(self, track_id: str):
        """Add track to the favorites playlist"""
        if not self.playlist_id:
            return

        try:
            existing_track_ids = self._get_playlist_tracks_cached(self.playlist_id)

            if track_id not in existing_track_ids:
                self.sp.playlist_add_items(self.playlist_id, [track_id], position=0)
                print("[SUCCESS] Added to favorites playlist")
                self.playlist_track_cache.add(track_id)
            else:
                print("[DUPLICATE] Song already exists in playlist")

        except Exception as e:
            print(f"Error adding to playlist: {e}")
            self.cache_last_updated = 0

    def run(self):
        """Main run loop"""
        print("Spotify Favorite Songs Tracker")
        print("=" * 35)
        print(f"Monitor interval: {self.check_interval}s")
        print(f"Favorite threshold: {self.favorite_threshold} plays")
        print(f"Completion requirement: {self.min_completion_ratio:.0%}")
        print("=" * 35)
        print("Starting monitor... (Ctrl+C to stop)\n")

        self.playlist_id = self._find_or_create_playlist()
        if not self.playlist_id:
            print("[ERROR] Could not create/find playlist. Exiting.")
            return

        print(f"[INFO] Using playlist: {self.playlist_id[:10]}...")
        print("[INFO] Monitor active\n")

        while self.running:
            try:
                self.process_current_track()
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(self.check_interval)

        print("\n[SHUTDOWN] Monitoring stopped")
        self._close_db()


def main():
    """Main entry point"""
    tracker = FavSongsTracker()
    tracker.run()


if __name__ == "__main__":
    main()
