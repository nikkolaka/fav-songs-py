import spotipy
import os
import threading
import json
import time
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth


load_dotenv()



sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=os.getenv("CLIENT_ID"),
                                               client_secret=os.getenv("CLIENT_SECRET"),
                                               redirect_uri=os.getenv("REDIRECT_URI"),
                                               scope="user-read-playback-state"))







fav_songs = {}

if not os.path.exists("fav_songs.json"):
    with open("fav_songs.json", "w") as f:
        json.dump({}, f)

def check_fav():
    threading.Timer(10.0, check_fav).start()


    with open("fav_songs.json", "r") as f:
        fav_songs = json.load(f)

    results = sp.current_playback()
    track_id = str(results['item']['id'])
    last_play_ms = 0
    

    if fav_songs.get(results['item']['id'], None) is not None:
        print("This song has already been added to your listened!\n")
        last_play_ms = fav_songs[results['item']['id']]['last_played']
    
        if last_play_ms < results['item']['duration_ms']:
            print("This song isn't finished!\n")
            return


    clean_results = {track_id : { 
                    "name":results['item']['name'], 
                    "artist":results['item']['artists'][0]['name'],
                    "occurrences":fav_songs.get(results['item']['id'], {}).get("occurrences", 0) + 1,
                    "last_played":results['timestamp'],
                    }}    
    
    fav_songs.update(clean_results)
    
    if fav_songs[track_id]['occurrences'] > 5:
        print("This is one of your favourite songs!\n")
    else:
        print("This song is " + str(5 - clean_results[track_id]['occurrences']) + " plays away from being one of your favourites!\n")
    
    with open("fav_songs.json", "w") as f:
        json.dump(fav_songs, f)

check_fav()
