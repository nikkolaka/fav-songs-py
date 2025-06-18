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
                                               scope="user-read-playback-state,playlist-modify-public"))







fav_songs = {}

if not os.path.exists("fav_songs.json"):
    with open("fav_songs.json", "w") as f:
        json.dump({}, f)




def check_fav(playlist_id):
    threading.Timer(10.0, check_fav, [playlist_id]).start()


    with open("fav_songs.json", "r") as f:
        fav_songs = json.load(f)

    results = current_playback()
    if results is None:
        return
    
    current_track_id = str(results['item']['id'])
    last_play_ms = 0
    

    if fav_songs.get(results['item']['id'], None) is not None:
        last_play_ms = time.time()*1000.0 - fav_songs[results['item']['id']]['last_played']
        print("Last played " + str(last_play_ms/1000.0) + " seconds ago\n")
        if last_play_ms < results['item']['duration_ms']:
            print("This song isn't finished!\n")
            return


    clean_results = {current_track_id : { 
                    "name":results['item']['name'], 
                    "artist":results['item']['artists'][0]['name'],
                    "occurrences":fav_songs.get(results['item']['id'], {}).get("occurrences", 0) + 1,
                    "last_played":results['timestamp'],
                    }}    
    
    fav_songs.update(clean_results)
    
    if fav_songs[current_track_id]['occurrences'] == 5:
        print("You liked this song~!\n")
        sp.playlist_add_items(playlist_id, [current_track_id], 0)
    else:
        print(clean_results[current_track_id]['name']+" is " + str(5 - clean_results[current_track_id]['occurrences']) + " plays away from being one of your favourites!\n")
    
    with open("fav_songs.json", "w") as f:
        json.dump(fav_songs, f)

def current_playback():
    results = sp.current_playback()
    if results is None or results['is_playing'] is False:
        print("No song is currently playing.\n")
        return
    print("Currently playing: " + results['item']['name'] + " by " + results['item']['artists'][0]['name'] + "\n")
    return results



def create_playlist():
    playlist_name = "Favourite Songs - Whatsit"
    user_id = sp.me()['id']
    
    try:
        playlist = sp.user_playlist_create(user_id, playlist_name, public=True, description="Are these my favorites¿")
        print(f"Playlist '{playlist_name}' created successfully.")
    except Exception as e:
        print(f"Error creating playlist: {e}")
        return None
    
    return playlist['id']

def search_playlist():
    user_id = sp.me()['id']
    playlists = sp.user_playlists(user_id)
    
    for playlist in playlists['items']:
        if '¿' in playlist['description'] or 'Whatsit' in playlist['name']:
            print("Found existing playlist.")
            return playlist['id']
    
    print("No existing playlist found, creating a new one.")
    return create_playlist()

playlist_id = search_playlist()
check_fav(playlist_id)
