# cache_handler.py

from spotipy.cache_handler import CacheHandler
from django.core.cache import cache

class ServerCacheHandler(CacheHandler):
    def __init__(self):
        self.token_key = "spotify_token_info"

    def get_cached_token(self):
        return cache.get(self.token_key)

    def save_token_to_cache(self, token_info):
        #print(f"Saving token {token_info} to cache with key {self.state}")
        cache.set(self.token_key, token_info, timeout=3600)  # Tokens expire after an hour
    
    def delete_cached_token(self):
        cache.delete(self.token_key)
        
class SessionCacheHandler(CacheHandler):
    def __init__(self, session):
        self.session = session
        self.token_key = "spotify_token_info"
        
    def get_cached_token(self):
        return self.session.get(self.token_key)
    
    def save_token_to_cache(self, token_info):
        self.session[self.token_key] = token_info
        
    def delete_cached_token(self):
        del self.session[self.token_key]
        