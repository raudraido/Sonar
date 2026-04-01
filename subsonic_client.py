import requests
import hashlib
import random
import string
import time
import os
import sys
import json
import threading

from collections import OrderedDict

def get_local_dir(subfolder=""):
    """
    Forces the cache to stay strictly inside your project folder.
    Creates an 'app_data' folder right next to this script.
    """
    # Get the exact folder where this Python file lives
    if getattr(sys, 'frozen', False):
        # If you ever compile this into an .exe or Linux binary
        base_dir = os.path.dirname(sys.executable)
    else:
        # Standard Python script execution
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Put everything in a neat 'app_data' folder so your root stays clean
    master_dir = os.path.join(base_dir, "app_data")
    final_path = os.path.join(master_dir, subfolder)
    
    os.makedirs(final_path, exist_ok=True)
    return final_path

class LRUCache:
    def __init__(self, max_size=50):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key, default=None):
        if key not in self.cache:
            return default
        self.cache.move_to_end(key) # Mark as recently used
        return self.cache[key]

    def set(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False) # Delete the oldest item

    def keys(self):
        return self.cache.keys()
    
    def __contains__(self, key):
        return key in self.cache
    
    def delete(self, key):
        if key in self.cache:
            del self.cache[key]

class SubsonicClient:
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.api_version = "1.30.0"
        self.client_name = "ProFeishin"
        self._api_cache = LRUCache(max_size=20)
        self._artists_cache = None
        self._scan_status_cache = None

        self._auth_lock = threading.Lock()

        import requests
        from requests.adapters import HTTPAdapter
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=10)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ------------------------------------------------------------------
    # Disk cache helpers
    # All JSON files live in app_data/json_data/ next to the script.
    # Every entry is tagged with scan_status; if the server is rescanned
    # (new music added) every cached file is automatically stale.
    # ------------------------------------------------------------------

    def test_connection(self):
        """
        Tests the connection and returns a tuple: (success: bool, error_message: str)
        """
        import requests
        try:
            r = requests.get(f"{self.base_url}/rest/ping", params=self._get_auth_params(), timeout=5)
            
            # The server was reached successfully
            if r.status_code == 200:
                data = r.json()
                response = data.get('subsonic-response', {})
                if response.get('status') == 'ok':
                    return True, ""
                else:
                    # Subsonic API often returns HTTP 200 but sets status to 'failed' for bad passwords
                    return False, "Wrong username or password."
            elif r.status_code in (401, 403):
                return False, "Wrong username or password."
            else:
                return False, f"Connected to server, but received an unexpected response (HTTP {r.status_code})."
                
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return False, "Could not connect to the server. Please check the URL and your network."
        except Exception as e:
            return False, f"An unexpected error occurred: {str(e)}"
    
    def _get_scan_status_cached(self):
        """Return scan status, hitting the network at most once per process lifetime."""
        if self._scan_status_cache is None:
            self._scan_status_cache = self.get_server_scan_status()
        return self._scan_status_cache

    def _disk_cache_path(self, key):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return os.path.join(get_local_dir("json_data"), f"{safe}.json")

    def _disk_cache_get(self, key):
        """Return cached data if it exists and matches current scan_status, else None."""
        path = self._disk_cache_path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
            if entry.get("scan_status") == self._get_scan_status_cached():
                return entry.get("data")
        except Exception:
            pass
        return None

    def _disk_cache_set(self, key, data):
        """Write data to disk under key, tagged with current scan_status."""
        path = self._disk_cache_path(key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"scan_status": self._get_scan_status_cached(), "data": data}, f)
        except Exception as e:
            print(f"[DiskCache] Write failed for {key}: {e}")

    def authenticate_native(self):
        """Log into Navidrome's native API to get the JWT token."""
        
        with self._auth_lock:
            
            
            if hasattr(self, 'native_jwt') and self.native_jwt:
                return True
                
            print("\n[DEBUG API] Attempting to fetch Navidrome Native JWT token...")
            try:
                import requests
                r = requests.post(f"{self.base_url}/auth/login", json={
                    "username": self.username,
                    "password": self.password
                }, timeout=5)
                
                print(f"[DEBUG API] Native Auth Response Code: {r.status_code}")
                
                if r.status_code == 200:
                    self.native_jwt = r.json().get('token')
                    print(f"[DEBUG API] SUCCESS! Obtained JWT Token: {self.native_jwt[:15]}...")
                    return True
                else:
                    print(f"[DEBUG API] FAILED to get Native Token. Server said: {r.text}")
            except Exception as e:
                print(f"[DEBUG API] Native auth crashed: {e}")
            return False
   
    def get_playlists(self):
        """Fetches all playlists using standard requests.get"""
        try:
            params = self._get_auth_params()
            r = requests.get(f"{self.base_url}/rest/getPlaylists", params=params, timeout=10)
            data = r.json()
            
            if 'subsonic-response' in data and 'playlists' in data['subsonic-response']:
                playlists_data = data['subsonic-response']['playlists'].get('playlist', [])
                return playlists_data if isinstance(playlists_data, list) else [playlists_data]
        except Exception as e: 
            print(f"Error getting playlists: {e}")
        return []

    def get_playlist_tracks(self, playlist_id):
        """Fetches tracks for a playlist and parses them for the UI"""
        try:
            params = self._get_auth_params()
            params['id'] = playlist_id
            r = requests.get(f"{self.base_url}/rest/getPlaylist", params=params, timeout=10)
            data = r.json()
            
            if 'subsonic-response' in data and 'playlist' in data['subsonic-response']:
                raw_tracks = data['subsonic-response']['playlist'].get('entry', [])
                
                
                if isinstance(raw_tracks, dict): 
                    raw_tracks = [raw_tracks]
                
                
                return [self._parse_song_data(t) for t in raw_tracks]
        except Exception as e: 
            print(f"Error getting playlist tracks: {e}")
        return []
    
    def create_playlist(self, name, public=False):
        """Creates a playlist and optionally makes it public."""
        params = self._get_auth_params()
        params['name'] = name
        
        try:
            # 1. Create the base playlist
            r = requests.get(f"{self.base_url}/rest/createPlaylist", params=params, timeout=10)
            data = r.json()
            
            # Extract the newly created Playlist ID from Navidrome
            new_id = None
            if 'subsonic-response' in data and 'playlist' in data['subsonic-response']:
                new_id = data['subsonic-response']['playlist'].get('id')
                
            
            if public and new_id:
                def force_public():
                    import time
                    # Pause for 2 seconds to let add_tracks_to_playlist finish completely
                    time.sleep(2) 
                    
                    update_params = self._get_auth_params()
                    update_params['playlistId'] = new_id
                    update_params['public'] = 'true'
                    
                    # Force the public flag update AFTER the playlist is populated
                    requests.get(f"{self.base_url}/rest/updatePlaylist", params=update_params, timeout=10)
                
                # Spin up a tiny background thread so your UI doesn't freeze while waiting
                import threading
                threading.Thread(target=force_public, daemon=True).start()
                
            return new_id
            
        except Exception as e:
            print(f"Error creating playlist: {e}")
            return None

    def add_tracks_to_playlist(self, playlist_id, track_ids):
        """Appends one or more tracks to an existing playlist.
        Uses songIdToAdd only — does NOT touch existing entries."""
        if not track_ids:
            return True

        params = self._get_auth_params()
        query_items = list(params.items())
        query_items.append(('playlistId', str(playlist_id)))
        for tid in track_ids:
            query_items.append(('songIdToAdd', str(tid)))

        r = requests.post(f"{self.base_url}/rest/updatePlaylist",
                          data=query_items, timeout=15)
        r.raise_for_status()

        response = r.json().get('subsonic-response', {})
        if response.get('status') != 'ok':
            raise Exception(response.get('error', {}).get('message', 'Unknown API Error'))
        return True
    
    def reset_caches(self):
        """Call this when new content is known to have landed on the server."""
        self._scan_status_cache = None   # forces re-fetch of scan timestamp
        self._artists_cache = None       # forces re-fetch of artist list
    
    def delete_playlist(self, playlist_id):
        """Deletes a playlist from the Navidrome server."""
        params = self._get_auth_params()
        params['id'] = playlist_id
        
        r = requests.get(f"{self.base_url}/rest/deletePlaylist", params=params, timeout=10)
        r.raise_for_status()
        
        response = r.json().get('subsonic-response', {})
        if response.get('status') != 'ok':
            raise Exception(response.get('error', {}).get('message', 'Unknown API Error'))
        return True

    def rename_playlist(self, playlist_id, new_name):
        """Renames an existing playlist on the server."""
        params = self._get_auth_params()
        params['playlistId'] = playlist_id  # Note: updatePlaylist uses 'playlistId', not 'id'
        params['name'] = new_name
        
        r = requests.get(f"{self.base_url}/rest/updatePlaylist", params=params, timeout=10)
        r.raise_for_status()
        
        response = r.json().get('subsonic-response', {})
        if response.get('status') != 'ok':
            raise Exception(response.get('error', {}).get('message', 'Unknown API Error'))
        return True
       
    def update_playlist_tracks(self, playlist_id, current_length, new_track_ids):
        """Replaces the entire playlist content with a new track order."""
        params = self._get_auth_params()
        params['playlistId'] = playlist_id
        
        query_items = list(params.items())
        
        # Subsonic API requires removing old indices and adding the new ones.
        # We remove from highest index to lowest to avoid shifting bugs on older servers.
        for i in range(current_length - 1, -1, -1):
            query_items.append(('songIndexToRemove', str(i)))
            
        for tid in new_track_ids:
            query_items.append(('songIdToAdd', str(tid)))
            
        # Use POST for the data payload to avoid URL length limits on massive playlists
        r = requests.post(f"{self.base_url}/rest/updatePlaylist", data=query_items, timeout=15)
        r.raise_for_status()
        
        response = r.json().get('subsonic-response', {})
        if response.get('status') != 'ok':
            raise Exception(response.get('error', {}).get('message', 'Unknown API Error'))
        return True
    
    def get_artists_native_page(self, sort_by="name", order="ASC", start=0, end=50, query=""):
        """Fetches a specific page of artists using Navidrome's native API."""
        import requests
        if not hasattr(self, 'native_jwt') or not self.native_jwt:
            self.authenticate_native()
            
        
        headers = {
            "x-nd-authorization": f"Bearer {self.native_jwt}"
        }
            
        params = {
            "_start": start,
            "_end": end,
            "_sort": sort_by,
            "_order": order,
        }
        if query:
            params["_q"] = query 
            
        print(f"\n[DEBUG API] --------------------------------------------------")
        print(f"[DEBUG API] REQUESTING: /api/artist")
        print(f"[DEBUG API] PARAMS: {params}")
            
        try:
            r = requests.get(f"{self.base_url}/api/artist", params=params, headers=headers, timeout=10)
            
            # If still 401, token might be stale; refresh and try one more time
            if r.status_code == 401: 
                print("[DEBUG API] 401 Error - Refreshing token...")
                if self.authenticate_native():
                    headers["x-nd-authorization"] = f"Bearer {self.native_jwt}"
                    r = requests.get(f"{self.base_url}/api/artist", params=params, headers=headers, timeout=10)
            
           
            data = r.json()
            if not isinstance(data, list):
                print(f"[DEBUG API] ERROR: Server returned {type(data)} instead of list. Content: {data}")
                return [], 0

            total_count = int(r.headers.get('X-Total-Count', len(data)))
            
            print(f"[DEBUG API] RESPONSE STATUS: {r.status_code}")
            print(f"[DEBUG API] X-TOTAL-COUNT: {total_count}")
            print(f"[DEBUG API] TOP 3 RAW ARTISTS FROM SERVER:")
            for i, item in enumerate(data[:3]):
                print(f"   {i+1}. {item.get('name')} | Albums: {item.get('albumCount')} | Plays: {item.get('playCount')}")
            print(f"[DEBUG API] --------------------------------------------------\n")
            
            clean_artists = []
            for item in data:
                clean_artists.append({
                    'id': item.get('id'),
                    'name': item.get('name', 'Unknown Artist'),
                    'coverArt': item.get('id'), 
                    'albumCount': item.get('albumCount', 0),
                    'playCount': item.get('playCount', 0)
                })
            return clean_artists, total_count
        except Exception as e:
            print(f"[DEBUG API] Native API fetch failed: {e}")
            return [], 0
    
    def get_tracks_native_page(self, sort_by="title", order="ASC", start=0, end=50, query=""):
        """Fetches a specific page of tracks using Navidrome's native API for true server-side sorting."""
        import requests
        if not hasattr(self, 'native_jwt') or not self.native_jwt:
            self.authenticate_native()
            
        headers = {
            "x-nd-authorization": f"Bearer {self.native_jwt}"
        }
            
        params = {
            "_start": start,
            "_end": end,
            "_sort": sort_by,
            "_order": order,
        }
        if query:
            params["_q"] = query 
            
        try:
            r = requests.get(f"{self.base_url}/api/song", params=params, headers=headers, timeout=10)
            
            if r.status_code == 401: 
                if self.authenticate_native():
                    headers["x-nd-authorization"] = f"Bearer {self.native_jwt}"
                    r = requests.get(f"{self.base_url}/api/song", params=params, headers=headers, timeout=10)
            
            data = r.json()
            if not isinstance(data, list):
                return [], 0

            total_count = int(r.headers.get('X-Total-Count', len(data)))
            
            # Navidrome's native API returns items that map perfectly to our UI parser
            clean_tracks = []
            for item in data:
                # Add a fallback for cover_id since native uses 'coverArt'
                if 'coverArt' in item and 'cover_id' not in item:
                    item['cover_id'] = item['coverArt']
                clean_tracks.append(self._parse_song_data(item))
                
            return clean_tracks, total_count
        except Exception as e:
            print(f"[Client] Native track fetch failed: {e}")
            return [], 0
       
    def get_albums_native_page(self, sort_by="name", order="ASC", start=0, end=50, query=""):
        """Fetches a specific page of albums using Navidrome's native API for true server-side sorting."""
        import requests
        if not hasattr(self, 'native_jwt') or not self.native_jwt:
            if not self.authenticate_native():
                return [], 0
            
        headers = {
            "x-nd-authorization": f"Bearer {self.native_jwt}"
        }
            
        params = {
            "_start": start,
            "_end": end,
            "_sort": sort_by,
            "_order": order,
        }
        if query:
            params["_q"] = query 
            
        try:
            r = requests.get(f"{self.base_url}/api/album", params=params, headers=headers, timeout=10)
            
            if r.status_code == 401: 
                if self.authenticate_native():
                    headers["x-nd-authorization"] = f"Bearer {self.native_jwt}"
                    r = requests.get(f"{self.base_url}/api/album", params=params, headers=headers, timeout=10)
            
            data = r.json()
            if not isinstance(data, list):
                return [], 0

            total_count = int(r.headers.get('X-Total-Count', len(data)))
            
            # The native API returns items that are mostly compatible.
            # We just need to ensure the keys match what the UI expects.
            return data, total_count
        except Exception as e:
            print(f"[Client] Native album fetch failed: {e}")
            return [], 0
       
    def get_artists_live(self, force_refresh=False):
        """Fetches ALL artists (including track artists) using OpenSubsonic empty-query search."""
        if not force_refresh and self._artists_cache is not None:
            return self._artists_cache

        # Try disk cache first
        if not force_refresh:
            cached = self._disk_cache_get("artists_live")
            if cached is not None:
                print(f"[Client] Loaded {len(cached)} artists from disk cache.")
                self._artists_cache = cached
                return cached

        print("[Client] Fetching ALL artists (including track artists) via search3...")
        params = self._get_auth_params()
        params.update({
            'query': '',
            'artistCount': 100000,
            'albumCount': 0,
            'songCount': 0
        })
        clean_artists = []
        try:
            r = requests.get(f"{self.base_url}/rest/search3", params=params, timeout=10)
            data = r.json()
            if 'subsonic-response' in data and 'searchResult3' in data['subsonic-response']:
                raw_artists = data['subsonic-response']['searchResult3'].get('artist', [])
                if isinstance(raw_artists, dict): raw_artists = [raw_artists]
                for artist in raw_artists:
                    clean_artists.append({
                        'id': artist.get('id'),
                        'name': artist.get('name', 'Unknown Artist'),
                        'coverArt': artist.get('coverArt'),
                        'albumCount': artist.get('albumCount', 0),
                        'playCount': artist.get('playCount', 0)
                    })
            print(f"[Client] Found {len(clean_artists)} total artists. Saving to disk cache.")
            self._artists_cache = clean_artists
            self._disk_cache_set("artists_live", clean_artists)
            return clean_artists
        except Exception as e:
            print(f"[Client] Live artist fetch failed: {e}")
            return self.get_all_artists_index()
    
    def get_tracks_live(self, query="", size=50, offset=0, force_refresh=False):
        """Fetches tracks directly from the server using OpenSubsonic empty-query search."""
        
        if query:
            force_refresh = True
        cache_key = f"tracks_{query}_{size}_{offset}"
        
        if not force_refresh and self._api_cache.get(cache_key) is not None:
            return self._api_cache.get(cache_key)

        params = self._get_auth_params()
        params.update({
            'query': query,       
            'songCount': size, 
            'songOffset': offset,
            'artistCount': 0,     
            'albumCount': 0       
        })
        
        try:
            r = requests.get(f"{self.base_url}/rest/search3", params=params, timeout=5)
            data = r.json()
            
            raw_tracks = data.get('subsonic-response', {}).get('searchResult3', {}).get('song', [])
            
            # Navidrome returns a dict instead of a list if there's only 1 result
            if isinstance(raw_tracks, dict): 
                raw_tracks = [raw_tracks]
                
           
            parsed_tracks = [self._parse_song_data(s) for s in raw_tracks]
            
            self._api_cache.set(cache_key, parsed_tracks)
            return parsed_tracks
            
        except Exception as e:
            print(f"[Client] Live track fetch failed: {e}")
            return []
      
    def get_albums_live(self, sort_type="newest", size=50, offset=0, force_refresh=False):
        """Fetches albums directly from the server with LRUCache-safe access."""
        cache_key = f"albums_{sort_type}_{size}_{offset}"
        
        # 1. Use .get() for LRUCache instead of 'in self._api_cache'
        cached_result = self._api_cache.get(cache_key)
        if not force_refresh and cached_result:
            return cached_result

        # 2. Otherwise, fetch from the server
        params = self._get_auth_params()
        params.update({'type': sort_type, 'size': size, 'offset': offset})
        
        try:
            r = requests.get(f"{self.base_url}/rest/getAlbumList2", params=params, timeout=5)
            data = r.json()
            
            # Extract albums and normalize to a list
            res_data = data.get('subsonic-response', {}).get('albumList2', {}).get('album', [])
            albums = [res_data] if isinstance(res_data, dict) else res_data
            
            
            total_count = r.headers.get('X-Total-Count')
            total_count = int(total_count) if total_count else None
            
            # 3. Use .set() for LRUCache instead of bracket assignment
            result = (albums, total_count)
            self._api_cache.set(cache_key, result)
            
            return result
        except Exception as e:
            # This is where your 'LRUCache object does not support item assignment' error happened
            print(f"[Client] Live fetch failed: {e}")
            return ([], None)
     
    def get_fast_album_count(self):
        """
        Lightning-fast album count using Navidrome's X-Total-Count header trick.
        """
        params = self._get_auth_params()
        params['type'] = 'newest'
        params['size'] = 1  # Request only 1 item to make the server response instant
        params['offset'] = 0
        
        try:
            r = requests.get(f"{self.base_url}/rest/getAlbumList2", params=params, timeout=5)
            
            # Extract the magic header (requests headers are case-insensitive)
            header_count = r.headers.get('X-Total-Count')
            
            if header_count:
                return int(header_count)
            else:
                print("[Client] X-Total-Count header missing. Is this a legacy server?")
                return None
        except Exception as e:
            print(f"[Client] Error getting fast album count: {e}")
            return None
    
    def _get_auth_params(self):
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        token = hashlib.md5((self.password + salt).encode('utf-8')).hexdigest()
        return {
            'u': self.username,
            't': token,
            's': salt,
            'v': self.api_version,
            'c': self.client_name,
            'f': 'json'
        }

    def get_total_track_count(self):
        """Fetches the exact total number of tracks from the server's database."""
        try:
            params = self._get_auth_params()
            r = requests.get(f"{self.base_url}/rest/getScanStatus", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'scanStatus' in data['subsonic-response']:
                return int(data['subsonic-response']['scanStatus'].get('count', 0))
        except Exception as e:
            print(f"Error getting total track count: {e}")
        return 0
    
    def ping(self):
        try:
            r = requests.get(f"{self.base_url}/rest/ping", params=self._get_auth_params(), timeout=5)
            return r.status_code == 200 and r.json()['subsonic-response']['status'] == 'ok'
        except:
            return False

    def set_favorite(self, item_id, active):
        """Toggles the star on the server and checks for errors."""
        endpoint = "star" if active else "unstar"
        params = self._get_auth_params()
        params['id'] = item_id
        
        print(f"[Client] Attempting to {endpoint} item: {item_id}")
        
        try:
            r = requests.get(f"{self.base_url}/rest/{endpoint}", params=params)
            r.raise_for_status() # Raise error for 404/500
            
            data = r.json()
            response = data.get('subsonic-response', {})
            
            if response.get('status') == 'ok':
                print(f"[Client] Success: Item {item_id} {endpoint}red.")
            else:
                error = response.get('error', {})
                print(f"[Client] API Error: {error.get('code')} - {error.get('message')}")

        except Exception as e:
            print(f"[Client] Network Error setting favorite: {e}")

    def get_last_scan_time(self):
        """Standardized helper to get scan status as an integer timestamp."""
        try:
            params = self._get_auth_params()
            r = requests.get(f"{self.base_url}/rest/getScanStatus", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'scanStatus' in data['subsonic-response']:
                status = data['subsonic-response']['scanStatus']
                
                # 1. Try 'count' (Standard Subsonic - Integer revision)
                if 'count' in status:
                    return int(status['count'])
                
                # 2. Try 'lastScan' (Navidrome - ISO String)
                if 'lastScan' in status:
                    val = status['lastScan']
                    if isinstance(val, int): return val
                    if isinstance(val, str) and val.isdigit(): return int(val)
                    
                    try:
                        import datetime
                        # Remove milliseconds (.123456Z) to be safe
                        val = val.split('.')[0].replace('Z', '')
                        # Parse ISO string to timestamp number
                        dt = datetime.datetime.fromisoformat(val)
                        return int(dt.timestamp())
                    except Exception as e:
                        print(f"[Client] Timestamp parse error: {e}")
        except Exception as e:
            print(f"Error checking scan status: {e}")
        return 0
    
    def get_random_songs(self, count=20):
        params = self._get_auth_params()
        params['size'] = count
        try:
            r = requests.get(f"{self.base_url}/rest/getRandomSongs", params=params)
            data = r.json()
            songs = data['subsonic-response']['randomSongs'].get('song', [])
            if isinstance(songs, dict): songs = [songs]
            
            cleaned = []
            for s in songs:
                cleaned.append(self._parse_song_data(s))
            return cleaned
        except Exception as e:
            print(f"Error fetching random songs: {e}")
            return []

    def _parse_song_data(self, s):
        """Helper to normalize song data structure"""
        
        raw_sec = s.get('duration', 0)
        try:
            sec = int(float(raw_sec)) if raw_sec else 0
        except Exception:
            sec = 0
            
        dur_str = f"{sec // 60}:{sec % 60:02d}"
        
        # 1. Handle Artist(s) for DISPLAY
        raw_artist = s.get('artist', 'Unknown Artist')
        display_artist = "Unknown Artist"
        
        if isinstance(raw_artist, list):
            display_artist = " • ".join(str(a) for a in raw_artist)
        elif isinstance(raw_artist, str):
            display_artist = raw_artist
        
        artist_id = s.get('artistId') or s.get('artist_id')
        
        # 2. Handle Genre(s) - similar to artists
        # Try 'genres' (plural) first, then fall back to 'genre' (singular)
        raw_genre = s.get('genres') or s.get('genre', '')
        display_genre = ""
        
        
        if isinstance(raw_genre, list):
            # If it's a list, check if items are dicts with 'name' field or just strings
            genre_names = []
            for g in raw_genre:
                if isinstance(g, dict) and 'name' in g:
                    # Genre is an object like {'name': 'Pop'}
                    genre_names.append(str(g['name']))
                elif g:
                    # Genre is a plain string
                    genre_names.append(str(g))
            display_genre = " • ".join(genre_names) if genre_names else ""
        elif isinstance(raw_genre, str) and raw_genre:
            # If it's a string, keep as-is (might already have delimiters)
            display_genre = raw_genre

        return {
            'id': s.get('id'),
            'title': s.get('title', 'Unknown Title'),
            'artist': display_artist,
            'artist_id': artist_id,
            'album': s.get('album', 'Unknown Album'),
            'albumId': s.get('albumId'), 
            'created': s.get('created'), 
            'album_artist': s.get('albumArtist') or s.get('album_artist'), 
            'trackNumber': int(s.get('track', 0) or 0),
            'discNumber': int(s.get('discNumber', 1) or 1),
            'duration': dur_str,
            'duration_ms': sec * 1000,
            'stream_url': self._build_stream_url(s.get('id')),
            'cover_id': s.get('coverArt') or s.get('cover_id'),
            'starred': 'starred' in s or 'favorite' in s,
            'path': s.get('path'),
            'genre': display_genre,
            'year': str(s.get('year', ''))[:4],
            'play_count': s.get('playCount', 0),
            'bitRate': s.get('bitRate', 0)
        }

    def _build_stream_url(self, song_id):
        if not song_id: return ""
        params = self._get_auth_params()
        params['id'] = song_id
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{self.base_url}/rest/stream?{query}"

    def get_cover_art(self, cover_id, size=500):
        if not cover_id: return None
        params = self._get_auth_params()
        params['id'] = cover_id
        params['size'] = size
        try:
            r = self.session.get(f"{self.base_url}/rest/getCoverArt", params=params, timeout=15)
            if r.status_code == 200:
                return r.content
        except:
            pass
        return None

    def get_artists(self):
        try:
            r = requests.get(f"{self.base_url}/rest/getArtists", params=self._get_auth_params())
            data = r.json()
            if 'subsonic-response' in data and 'artists' in data['subsonic-response']:
                artists = []
                indices = data['subsonic-response']['artists'].get('index', [])
                if isinstance(indices, dict): indices = [indices]
                for letter in indices:
                    letter_artists = letter.get('artist', [])
                    if isinstance(letter_artists, dict): letter_artists = [letter_artists]
                    for artist in letter_artists:
                        artists.append({
                            'id': artist.get('id'),
                            'name': artist.get('name', 'Unknown Artist'),
                            'coverArt': artist.get('coverArt')
                        })
                return artists
        except: pass 
        return []

    def get_artist(self, artist_id):
        key = f"artist_{artist_id}"
        cached = self._disk_cache_get(key)
        if cached is not None:
            return cached
        params = self._get_auth_params()
        params['id'] = artist_id
        try:
            r = requests.get(f"{self.base_url}/rest/getArtist", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'artist' in data['subsonic-response']:
                result = data['subsonic-response']['artist']
                self._disk_cache_set(key, result)
                return result
        except Exception as e:
            print(f"Error getting artist info: {e}")
        return None

    def get_artist_info2(self, artist_id):
        """Calls getArtistInfo2 to get Last.fm biography, similar artists, etc."""
        params = self._get_auth_params()
        params['id'] = artist_id
        try:
            r = requests.get(f"{self.base_url}/rest/getArtistInfo2", params=params)
            data = r.json()
            sr = data.get('subsonic-response', {})
            info = sr.get('artistInfo2') or sr.get('artistInfo') or {}
            return info
        except Exception as e:
            print(f"Error getting artist info2: {e}")
        return {}

    def get_album_tracks(self, album_id, force_refresh=False):
        key = f"album_tracks_{album_id}"
        
        
        if not force_refresh:
            cached = self._disk_cache_get(key)
            if cached is not None:
                return cached
                
        params = self._get_auth_params()
        params['id'] = album_id
        try:
            r = requests.get(f"{self.base_url}/rest/getAlbum", params=params)
            data = r.json()
            tracks = []
            if 'album' not in data['subsonic-response']: return []
            album_info = data['subsonic-response']['album']
            album_artist = album_info.get('artist')
            raw_tracks = album_info.get('song', [])
            if isinstance(raw_tracks, dict): raw_tracks = [raw_tracks]
            for s in raw_tracks:
                track = self._parse_song_data(s)
                if not track.get('album_artist') and album_artist:
                    track['album_artist'] = album_artist
                    track['albumArtist'] = album_artist
                tracks.append(track)
                
            
            self._disk_cache_set(key, tracks)
            
            return tracks
        except Exception as e:
            print(f"Error getting tracks: {e}")
            return []
        
    def get_album_list_sorted(self, sort_type="newest", size=50, offset=0):
        params = self._get_auth_params()
        params['type'] = sort_type
        params['size'] = size
        params['offset'] = offset
        try:
            r = requests.get(f"{self.base_url}/rest/getAlbumList2", params=params)
            data = r.json()
            albums = []
            if 'subsonic-response' in data and 'albumList2' in data['subsonic-response']:
                raw = data['subsonic-response']['albumList2'].get('album', [])
                if isinstance(raw, dict): raw = [raw]
                for alb in raw:
                    title = alb.get('title') or alb.get('name') or "Unknown Album"
                    artist = alb.get('artist', 'Unknown Artist')
                    year = alb.get('year')
                    if not year and 'created' in alb:
                        try: year = str(alb['created'])[:4]
                        except: pass
                    albums.append({
                        'id': alb.get('id'),
                        'title': title,
                        'artist': artist,
                        'year': year if year else "",
                        'cover_id': alb.get('coverArt'),
                        'starred': 'starred' in alb or 'favorite' in alb
                    })
            return albums
        except Exception as e:
            print(f"Error fetching sorted albums: {e}")
            return []

    def get_all_artists_flat(self):
        print("Using 'search3' strategy to find all artists...")
        params = self._get_auth_params()
        params['query'] = '' 
        params['artistCount'] = 100000 
        params['albumCount'] = 0
        params['songCount'] = 0
        try:
            r = requests.get(f"{self.base_url}/rest/search3", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'searchResult3' in data['subsonic-response']:
                raw_artists = data['subsonic-response']['searchResult3'].get('artist', [])
                if isinstance(raw_artists, dict): raw_artists = [raw_artists]
                print(f"Found {len(raw_artists)} artists via Search.")
                cleaned_artists = []
                for artist in raw_artists:
                    cleaned_artists.append({
                        'id': artist.get('id'),
                        'name': artist.get('name', 'Unknown Artist'),
                        'coverArt': artist.get('coverArt')
                    })
                cleaned_artists.sort(key=lambda x: x['name'].lower())
                return cleaned_artists
        except Exception as e:
            print(f"Search strategy failed: {e}")
        print("Falling back to standard getArtists...")
        return self.get_artists()

    def get_all_artists_index(self, force_refresh=False):
        """Fetches the Artist Index, utilizing a self-updating portable disk cache."""
        
    
        cache_dir = get_local_dir("json_data")
        cache_file = os.path.join(cache_dir, "artists_index.json")
        
        # 1. Ask Navidrome for its current database timestamp/revision
        current_scan_status = self.get_server_scan_status()
        
        # 2. Check if we have a valid cache on disk
        if not force_refresh and os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    
                # Does the cache's timestamp match the server's current timestamp?
                if cached_data.get("scan_status") == current_scan_status:
                    print("[Client] Loading full artist index from portable disk cache!")
                    return cached_data.get("artists", [])
                else:
                    print("[Client] Server has new music! Disk cache is invalidated.")
            except Exception as e:
                print(f"Cache read error: {e}")

        # 3. If missing or old, fetch fresh data from the server
        print("[Client] Fetching fresh full artist index from server...")
        params = self._get_auth_params()
        clean_artists = []
        
        try:
            r = requests.get(f"{self.base_url}/rest/getArtists", params=params)
            data = r.json()
            
            if 'subsonic-response' in data and 'artists' in data['subsonic-response']:
                index_list = data['subsonic-response']['artists'].get('index', [])
                if isinstance(index_list, dict): index_list = [index_list]
                
                for letter_group in index_list:
                    artists = letter_group.get('artist', [])
                    if isinstance(artists, dict): artists = [artists]
                    
                    for artist in artists:
                        clean_artists.append({
                            'id': artist.get('id'),
                            'name': artist.get('name'),
                            'coverArt': artist.get('coverArt'),
                            'albumCount': artist.get('albumCount', 0),
                            'playCount': artist.get('playCount', 0)
                        })
        except Exception as e:
            print(f"Error fetching artist index: {e}")
            return []

        # 4. Save the fresh data to your portable app_data folder!
        if clean_artists:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "scan_status": current_scan_status, 
                        "artists": clean_artists
                    }, f)
            except Exception as e:
                print(f"Error saving cache: {e}")
                
        return clean_artists
      
    def search3(self, query, size=500, offset=0, artist_count=5, album_count=5):
        """
        Searches for songs, albums, and artists. 
        If query is empty, it acts as a fetch-all for tracks.
        """
        params = self._get_auth_params()
        params['query'] = query
        params['songCount'] = size
        params['songOffset'] = offset
        params['artistCount'] = artist_count
        params['albumCount'] = album_count
        
        try:
            r = requests.get(f"{self.base_url}/rest/search3", params=params, timeout=10)
            data = r.json()
            
            if 'subsonic-response' in data and 'searchResult3' in data['subsonic-response']:
                result = data['subsonic-response']['searchResult3']
                
                # 1. Parse Songs using your standard cleaner
                if 'song' in result:
                    raw_songs = result['song']
                    if isinstance(raw_songs, dict): raw_songs = [raw_songs]
                    result['song'] = [self._parse_song_data(s) for s in raw_songs]
                else:
                    result['song'] = []

                # 2. Ensure Artist/Album lists are lists (not single dicts)
                for key in ['artist', 'album']:
                    if key in result and isinstance(result[key], dict):
                        result[key] = [result[key]]
                    elif key not in result:
                        result[key] = []
                    
                return result
                    
            return {'song': [], 'album': [], 'artist': []}
        except Exception as e:
            print(f"Error in search3: {e}")
            return {'song': [], 'album': [], 'artist': []}
    
    def search_albums(self, query, count=50, offset=0):
        """
        Searches for albums by title or artist using the Subsonic search3 endpoint.
        Returns (list_of_albums, total_count_or_None).
        """
        params = self._get_auth_params()
        params['query'] = query
        params['albumCount'] = count
        params['albumOffset'] = offset
        params['songCount'] = 0
        params['artistCount'] = 0
        try:
            r = requests.get(f"{self.base_url}/rest/search3", params=params, timeout=10)
            data = r.json()
            raw = data.get('subsonic-response', {}).get('searchResult3', {}).get('album', [])
            if isinstance(raw, dict): raw = [raw]
            albums = []
            for a in raw:
                albums.append({
                    'id': a.get('id'),
                    'title': a.get('title') or a.get('name') or 'Unknown Album',
                    'artist': a.get('artist') or a.get('albumArtist') or 'Unknown Artist',
                    'year': str(a.get('year', '')),
                    'cover_id': a.get('coverArt'),
                    'coverArt': a.get('coverArt'),
                    'starred': 'starred' in a or 'favorite' in a,
                })
            # Navidrome doesn't expose total for album search — return None so UI hides pagination
            return albums, None
        except Exception as e:
            print(f"[Client] search_albums error: {e}")
            return [], None

    def get_starred_songs(self):
        """Fetches the full list of starred songs from the server."""
        print("[Client] Fetching starred songs...")
        params = self._get_auth_params()
        try:
            r = requests.get(f"{self.base_url}/rest/getStarred", params=params)
            data = r.json()
            
            parsed_songs = []
            
            if 'subsonic-response' in data and 'starred' in data['subsonic-response']:
                starred_data = data['subsonic-response']['starred']
                
                # The 'starred' endpoint returns songs, albums, and artists.
                # We only want 'song' entries for the tracks table.
                raw_songs = starred_data.get('song', [])
                
                # Handle case where single result is a dict, not a list
                if isinstance(raw_songs, dict): 
                    raw_songs = [raw_songs]
                
                for s in raw_songs:
                    # Use our helper to ensure format matches search results
                    parsed_songs.append(self._parse_song_data(s))
                    
            print(f"[Client] Found {len(parsed_songs)} starred songs.")
            return parsed_songs
            
        except Exception as e:
            print(f"Error fetching starred songs: {e}")
            return []
    
    def get_starred_ids(self):
        """Fetches list of all starred song IDs from the server."""
        params = self._get_auth_params()
        try:
            print("[Client] Fetching starred items list...")
            r = requests.get(f"{self.base_url}/rest/getStarred", params=params)
            data = r.json()
            ids = []
            
            if 'subsonic-response' in data and 'starred' in data['subsonic-response']:
                starred_data = data['subsonic-response']['starred']
                # Extract Song IDs
                if 'song' in starred_data:
                    songs = starred_data['song']
                    if isinstance(songs, dict): songs = [songs]
                    for s in songs:
                        if 'id' in s: ids.append(s['id'])
                        
            print(f"[Client] Got {len(ids)} starred IDs from server.")
            return ids
        except Exception as e:
            print(f"[Client] Error fetching starred items: {e}")
            return []
    
    def get_top_songs(self, artist_name, count=10):
        key = f"top_songs_{artist_name}_{count}"
        cached = self._disk_cache_get(key)
        if cached is not None:
            return cached
        params = self._get_auth_params()
        params['artist'] = artist_name
        params['count'] = count
        try:
            r = requests.get(f"{self.base_url}/rest/getTopSongs", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'topSongs' in data['subsonic-response']:
                raw_songs = data['subsonic-response']['topSongs'].get('song', [])
                if isinstance(raw_songs, dict): raw_songs = [raw_songs]
                cleaned = [self._parse_song_data(s) for s in raw_songs]
                self._disk_cache_set(key, cleaned)
                return cleaned
        except Exception as e:
            print(f"Error getting top songs: {e}")
        return []

    def search_artist_tracks(self, artist_name):
        """Searches for ALL tracks matching the artist name (includes compilations/features)."""
        key = f"search_artist_tracks_{artist_name}"
        cached = self._disk_cache_get(key)
        if cached is not None:
            return cached
        params = self._get_auth_params()
        params['query'] = artist_name
        params['songCount'] = 2000
        params['albumCount'] = 0
        params['artistCount'] = 0
        try:
            r = requests.get(f"{self.base_url}/rest/search3", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'searchResult3' in data['subsonic-response']:
                raw_songs = data['subsonic-response']['searchResult3'].get('song', [])
                if isinstance(raw_songs, dict): raw_songs = [raw_songs]
                cleaned = [self._parse_song_data(s) for s in raw_songs]
                self._disk_cache_set(key, cleaned)
                return cleaned
        except Exception as e:
            print(f"Error searching artist tracks: {e}")
        return []
    
    def get_server_scan_status(self):
        """Returns the server's last scan revision/timestamp as an integer."""
        params = self._get_auth_params()
        try:
            r = requests.get(f"{self.base_url}/rest/getScanStatus", params=params)
            data = r.json()
            if 'subsonic-response' in data and 'scanStatus' in data['subsonic-response']:
                status = data['subsonic-response']['scanStatus']
                
                # 1. Try 'count' (Standard Subsonic - Integer revision)
                if 'count' in status:
                    return int(status['count'])
                
                # 2. Try 'lastScan' (Navidrome/others - ISO Date String)
                if 'lastScan' in status:
                    val = status['lastScan']
                    # If it's already an int (some servers), return it
                    if isinstance(val, int): return val
                    if isinstance(val, str) and val.isdigit(): return int(val)
                    
                    # If it's a string like '2026-02-11T...', convert to timestamp
                    try:
                        import datetime
                        # Handle 'Z' manually for older Python versions
                        val = val.replace('Z', '+00:00')
                        dt = datetime.datetime.fromisoformat(val)
                        return int(dt.timestamp())
                    except:
                        pass
                        
        except Exception as e:
            print(f"Error checking scan status: {e}")
        return 0
    
def get_fast_album_count(self):
        """
        Lightning-fast album count using Navidrome's X-Total-Count header trick.
        """
        params = self._get_auth_params()
        params['type'] = 'newest'
        params['size'] = 1 
        params['offset'] = 0
        
        try:
            r = requests.get(f"{self.base_url}/rest/getAlbumList2", params=params, timeout=5)
            
            
            header_count = r.headers.get('X-Total-Count')
            
            if header_count:
                return int(header_count)
            else:
                print("[Client] X-Total-Count header missing. Is this a legacy server?")
                return None
        except Exception as e:
            print(f"[Client] Error getting fast album count: {e}")
            return None