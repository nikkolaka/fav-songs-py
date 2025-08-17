import spotipy
import os
import threading
import json
import sys
import signal
import time
from typing import Dict, Optional, Any
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth



load_dotenv()

class FavSongsTracker:
    def __init__(self):
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=os.getenv("CLIENT_ID"),
                                               client_secret=os.getenv("CLIENT_SECRET"),
                                               redirect_uri=os.getenv("REDIRECT_URI"),
                                               scope="user-read-playback-state,playlist-modify-public",
                                               cache_path="./data/.cache"))
        self.fav_songs_file = "/app/songs_data/fav_songs.json"
        self.fav_songs = self._load_fav_songs()
        self.playlist_id = None
        self.running = True
        self.last_track_id = None
        self.last_check_time = 0

        #configuration
        self.check_interval = 10  # seconds until next check
        self.favorite_threshold = 5  # number of plays until marked as favorite
        self.min_completion = 0.8  # minimum completion percentage to consider a song finished
        self.count_window = 300000  # time window that a played song can count towards occurence - 5 minutes in milliseconds

        #set up signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        #Handle shutdown signals gracefully.
        print(f"\nReceived signal {signum}. Shutting down gracefully...")
        self.running = False
        self._save_fav_songs()
        sys.exit(0)

    def _load_fav_songs(self) -> Dict[str, dict]:
        #Load favorite songs from JSON file.

        try:
            if os.path.exists(self.fav_songs_file):
                with open(self.fav_songs_file, "r") as f:
                    return json.load(f)
            return {}
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading favorite songs: {e}")
            return {}
        
    def _save_fav_songs(self):
        #Save favorite songs to JSON file.
        try:
            with open(self.fav_songs_file, "w") as f:
                json.dump(self.fav_songs, f, indent=2)
        except IOError as e:
            print(f"Error saving favorite songs: {e}")

    def get_current_playback(self) -> Optional[Dict[str, Any]]:
        #Get the current playback state from Spotify.
        try:
            results = self.sp.current_playback()
            if results is None or not results.get('is_playing', False):
                return None 
            
            track_info = results.get('item')
            if not track_info:
                return None

            
            return results

            
        except spotipy.SpotifyException as e:
            print(f"Error getting current playback: {e}")
            return None
        
    def _find_or_create_playlist(self) -> Optional[str]:
        #Find or create a playlist for favorite songs.
        try:
            user_id = self.sp.me()['id']
            playlists = self.sp.user_playlists(user_id)

            for playlist in playlists['items']:
                if '¿' in playlist['description'] or 'Whatsit' in playlist['name']:
                    print("Found existing playlist.")
                    return playlist['id']

            print("No existing playlist found, creating a new one.")
            playlist = self.sp.user_playlist_create(
                user_id,
                "Favourite Songs - Whatsit",
                public=True,
                description="Are these my favorites¿"
            )
            return playlist['id']
        
        except spotipy.SpotifyException as e:
            print(f"Error finding or creating playlist: {e}")
            return None

    def _is_song_completed(self, current_playback: Dict[str, Any]) -> bool:
        #Check if song has been played sufficiently to be considered completed.
        progress_ms = current_playback.get('progress_ms', 0)
        duration_ms = current_playback['item'].get('duration_ms', 0)
        if duration_ms == 0:
            return False

        completion_ratio = progress_ms / duration_ms
        return completion_ratio >= self.min_completion

    def _should_play_count(self, track_id: str, timestamp: int) -> bool:
        #Determine if this play should be counted
        if track_id not in self.fav_songs:
            return True
        last_played = self.fav_songs[track_id].get('last_played', 0)
        time_since_last = timestamp - last_played
        return time_since_last >= self.count_window  # convert to milliseconds

    def process_current_track(self):
        #Process the currently playing track and update favorite songs.
        current_playback = self.get_current_playback()
        if not current_playback:
            return
        
        track = current_playback['item']
        track_id = track['id']
        timestamp = current_playback['timestamp']

        # Skip if current track is the same as last processed
        if self.last_track_id == track_id and (time.time() - self.last_check_time) <= self.check_interval:
            return
            
        self.last_track_id = track_id
        self.last_check_time = time.time()
        

        # Check if song is completed
        if not self._is_song_completed(current_playback):
            return

        # Check if this play should be counted
        if not self._should_play_count(track_id, timestamp):
            print("Song played too recently, skipping count.")
            return
        
        print(f"Played: {track['name']} by {track['artists'][0]['name']}")
        
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
            print(f"You liked this song: {track['name']} by {track['artists'][0]['name']}!")
            self.sp.playlist_add_items(self.playlist_id, [track_id])
        else:
            remaining = self.favorite_threshold - occurrences
            print(f"{track['name']} is {remaining} plays away from being one of your favorites!")

    def _add_to_playlist(self, track_id: str):
        #Add the current track to the favorite playlist.
        if not self.playlist_id:
            self.playlist_id = self._find_or_create_playlist()
            if not self.playlist_id:
                print("Failed to find or create playlist.")
                return

        try:
            #Check if the track is already in the playlist
            playlist_tracks = self.sp.playlist_tracks(self.playlist_id)
            existing_track_ids = {track['track']['id'] for track in playlist_tracks['items']}

            if track_id not in existing_track_ids:
                self.sp.playlist_add_items(self.playlist_id, [track_id], position=0)
                print(f"Added {track_id} to playlist {self.playlist_id}.")
            else:
                print(f"{track_id} is already in the playlist {self.playlist_id}.")
        
        except spotipy.SpotifyException as e:
            print(f"Error adding track to playlist: {e}")
    
    def run(self):
        #Main loop
        print("Starting FavSongsTracker...")
        print(f"Checking every {self.check_interval} seconds for new favorite songs.")
        print(f"Songs will be marked as favorites after {self.favorite_threshold} plays.")
        print(f"Minimum completion percentage to consider a song finished: {self.min_completion * 100}%")

        #Initialize playlist
        self.playlist_id = self._find_or_create_playlist()
        if not self.playlist_id:
            print("Failed to find or create playlist. Exiting.")
            return
        
        print(f"Using playlist ID: {self.playlist_id}")
        print("Monitoring started...\nPress Ctrl+C to exit.")

        while self.running:
            try:
                self.process_current_track()
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(self.check_interval)
            
        print("Shutting down...")
        self._save_fav_songs()

def main():
    #Main Entry Point
    os.makedirs("data", exist_ok=True)  # Ensure data directory exists
    os.makedirs("/app/songs_data", exist_ok=True)
    
    tracker = FavSongsTracker()
    tracker.run()


if __name__ == "__main__":
    main()

