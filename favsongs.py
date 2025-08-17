import spotipy
import os
import json
import time
import signal
import sys
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth
from typing import Dict, Optional, Any

# Load environment variables
load_dotenv()

class FavSongsTracker:
    def __init__(self):
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=os.getenv("CLIENT_ID"),
            client_secret=os.getenv("CLIENT_SECRET"),
            redirect_uri=os.getenv("REDIRECT_URI"),
            scope="user-read-playback-state,playlist-modify-public",
            cache_path="./data/.cache"
        ))
        
        self.fav_songs_file = "fav_songs.json"  # Back to project root
        self.fav_songs = self._load_fav_songs()
        self.playlist_id = None
        self.running = True
        self.last_track_id = None
        self.last_check_time = 0
        self.current_track_start_time = None
        self.current_track_info = None
        self.track_was_completed = False
        self.last_progress_update = 0
        self.progress_update_interval = 5  # Update progress every 5 seconds
        
        # Configuration
        self.check_interval = 10  # seconds
        self.favorite_threshold = 5
        self.min_completion_ratio = 0.8  # 80% completion before counting as "played"
        
        self.playlist_track_cache = set()  # Cache to avoid repeated API calls
        self.cache_last_updated = 0
        self.cache_ttl = 300  # Cache for 5 minutes
        
        # Set up signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"\nReceived signal {signum}. Shutting down gracefully...")
        self.running = False
        self._save_fav_songs()
        sys.exit(0)
    
    def _load_fav_songs(self) -> Dict[str, Any]:
        """Load favorite songs from JSON file"""
        try:
            if os.path.exists(self.fav_songs_file):
                with open(self.fav_songs_file, "r") as f:
                    return json.load(f)
            return {}
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading fav_songs.json: {e}")
            return {}
    
    def _save_fav_songs(self):
        """Save favorite songs to JSON file"""
        try:
            with open(self.fav_songs_file, "w") as f:
                json.dump(self.fav_songs, f, indent=2)
        except IOError as e:
            print(f"Error saving fav_songs.json: {e}")
    
    def get_current_playback(self) -> Optional[Dict[str, Any]]:
        """Get current playback information"""
        try:
            results = self.sp.current_playback()
            if results is None or not results.get('is_playing', False):
                return None
            
            track_info = results.get('item')
            if not track_info:
                return None
                
            return results
        except Exception as e:
            print(f"Error getting current playback: {e}")
            return None
    
    def _find_or_create_playlist(self) -> Optional[str]:
        """Find existing playlist or create new one"""
        try:
            user_id = self.sp.me()['id']
            playlists = self.sp.user_playlists(user_id, limit=50)
            
            # Search for existing playlist
            for playlist in playlists['items']:
                if playlist['name'] == "Favourite Songs - Whatsit":
                    print("[INFO] Found existing playlist")
                    return playlist['id']
            
            # Create new playlist
            print("[INFO] Creating new playlist...")
            playlist = self.sp.user_playlist_create(
                user_id, 
                "Favourite Songs - Whatsit", 
                public=True, 
                description="Are these my favorites¿"
            )
            print("[SUCCESS] Playlist created")
            return playlist['id']
            
        except Exception as e:
            print(f"Error with playlist: {e}")
            return None
    
    def _is_song_completed(self, current_playback: Dict[str, Any]) -> bool:
        """Check if song has been played sufficiently"""
        progress_ms = current_playback.get('progress_ms', 0)
        duration_ms = current_playback['item'].get('duration_ms', 0)
        
        if duration_ms == 0:
            return False
            
        completion_ratio = progress_ms / duration_ms
        return completion_ratio >= self.min_completion_ratio
    
    def _should_count_play(self, track_id: str, timestamp: int) -> bool:
        """Determine if this play should be counted"""
        if track_id not in self.fav_songs:
            return True
            
        last_played = self.fav_songs[track_id].get('last_played', 0)
        time_since_last = timestamp - last_played
        
        # Only count if it's been more than 5 minutes since last play
        return time_since_last > 300000  # 5 minutes in milliseconds
    
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
        track_name = track_info['name']
        artist_name = track_info['artists'][0]['name']
        duration_str = self._format_time(track_info['duration_ms'])
        
        print(f"\n[STARTED] {artist_name} - {track_name}")
        print(f"Duration: {duration_str}")
        
    def _log_track_end(self, track_info: Dict[str, Any], was_completed: bool):
        """Log when a track ends"""
        track_name = track_info['name']
        artist_name = track_info['artists'][0]['name']
        
        status = "COMPLETED" if was_completed else "SKIPPED"
        print(f"[{status}] {artist_name} - {track_name}")
    
    def _log_track_progress(self, current_playback: Dict[str, Any]):
        """Log current track progress with progress bar"""
        track = current_playback['item']
        progress_ms = current_playback.get('progress_ms', 0)
        duration_ms = track['duration_ms']
        
        progress_time = self._format_time(progress_ms)
        duration_time = self._format_time(duration_ms)
        progress_bar = self._create_progress_bar(progress_ms, duration_ms)
        
        completion_pct = (progress_ms / duration_ms * 100) if duration_ms > 0 else 0
        
        print(f"\r{progress_bar} {progress_time}/{duration_time} ({completion_pct:.1f}%)", end="", flush=True)
    
    def _handle_track_change(self, current_playback: Optional[Dict[str, Any]]):
        """Handle when tracks change or stop"""
        current_track_id = current_playback['item']['id'] if current_playback else None
        
        # Handle track ending/changing
        if self.last_track_id and self.last_track_id != current_track_id:
            if self.current_track_info:
                self._log_track_end(self.current_track_info, self.track_was_completed)
        
        # Handle new track starting
        if current_track_id and current_track_id != self.last_track_id:
            self.current_track_info = current_playback['item']
            self.current_track_start_time = time.time()
            self.track_was_completed = False
            self.last_progress_update = 0  # Reset progress timer
            self._log_track_start(self.current_track_info)
        
        # Handle playback stopping
        elif not current_playback and self.last_track_id:
            if self.current_track_info:
                self._log_track_end(self.current_track_info, self.track_was_completed)
            self.current_track_info = None
        
        self.last_track_id = current_track_id
    def process_current_track(self):
        """Process the currently playing track"""
        current_playback = self.get_current_playback()
        
        # Handle track changes first (start/end logging)
        self._handle_track_change(current_playback)
        
        if not current_playback:
            return
        
        # Show progress bar periodically
        current_time = time.time()
        if current_time - self.last_progress_update > self.progress_update_interval:
            self._log_track_progress(current_playback)
            self.last_progress_update = current_time
        
        track = current_playback['item']
        track_id = track['id']
        timestamp = current_playback['timestamp']
        
        # Skip if same track as last check and not enough time passed
        if track_id == self.last_track_id and (time.time() - self.last_check_time) < 30:
            return
            
        self.last_check_time = time.time()
        
        # Check if song is completed enough to count
        if self._is_song_completed(current_playback):
            self.track_was_completed = True
            
            # Check if we should count this play
            if not self._should_count_play(track_id, timestamp):
                return
            
            # Update song data
            if track_id not in self.fav_songs:
                self.fav_songs[track_id] = {
                    "name": track['name'],
                    "artist": track['artists'][0]['name'],
                    "occurrences": 0,
                    "last_played": 0
                }
            
            self.fav_songs[track_id]['occurrences'] += 1
            self.fav_songs[track_id]['last_played'] = timestamp
            
            occurrences = self.fav_songs[track_id]['occurrences']
            
            if occurrences == self.favorite_threshold:
                print(f"\n[FAVORITE] Adding to playlist after {occurrences} plays")
                self._add_to_playlist(track_id)
            else:
                remaining = self.favorite_threshold - occurrences
                print(f"\n[PROGRESS] Play count: {occurrences}/{self.favorite_threshold} ({remaining} more needed)")
            
            # Save after each update
            self._save_fav_songs()
    
    def _get_all_playlist_tracks(self, playlist_id: str) -> set:
        """Get all track IDs from playlist, handling pagination"""
        all_track_ids = set()
        
        try:
            results = self.sp.playlist_tracks(playlist_id, limit=100)
            
            while results:
                # Add current batch of tracks
                for item in results['items']:
                    if item['track'] and item['track']['id']:
                        all_track_ids.add(item['track']['id'])
                
                # Check if there are more tracks to fetch
                if results['next']:
                    results = self.sp.next(results)
                else:
                    break
                    
        except Exception as e:
            print(f"Error fetching playlist tracks: {e}")
            
        return all_track_ids

    def _get_playlist_tracks_cached(self, playlist_id: str) -> set:
        """Get playlist tracks with caching to reduce API calls"""
        current_time = time.time()
        
        # If cache is still valid, use it
        if (current_time - self.cache_last_updated) < self.cache_ttl:
            return self.playlist_track_cache
        
        # Refresh cache
        self.playlist_track_cache = self._get_all_playlist_tracks(playlist_id)
        self.cache_last_updated = current_time
        
        return self.playlist_track_cache

    def _add_to_playlist(self, track_id: str):
        """Add track to the favorites playlist"""
        if not self.playlist_id:
            return
            
        try:
            # Use cached check first for performance
            existing_track_ids = self._get_playlist_tracks_cached(self.playlist_id)
            
            if track_id not in existing_track_ids:
                self.sp.playlist_add_items(self.playlist_id, [track_id], position=0)
                print("[SUCCESS] Added to favorites playlist")
                
                # Update cache with new track
                self.playlist_track_cache.add(track_id)
                
            else:
                print("[DUPLICATE] Song already exists in playlist")
                
        except Exception as e:
            print(f"Error adding to playlist: {e}")
            # Reset cache on error to force refresh next time
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
        
        # Initialize playlist
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
        self._save_fav_songs()

def main():
    """Main entry point"""
    # Ensure data directory exists
    os.makedirs("data", exist_ok=True)
    
    tracker = FavSongsTracker()
    tracker.run()

if __name__ == "__main__":
    main()