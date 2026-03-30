"""
cover_cache.py — Unified album art cache for the entire application.

Two size tiers stored on disk with separate pointer files:
  covers_cache/{cover_id}_t.link  →  thumb-size image hash (300 px)
  covers_cache/{cover_id}_f.link  →  full-size image hash  (800 px)
  covers_cache/{md5_hash}.jpg     →  actual image bytes (shared pool)

All workers import CoverCache.instance() and call its methods.
No worker should directly touch the covers_cache directory anymore.
"""

import os
import hashlib
import threading
from collections import OrderedDict

CACHE_DIR   = "covers_cache"
THUMB_SIZE  = 300   # used for grid cards, playlist rows, artist tiles
FULL_SIZE   = 800   # used for the main now-playing artwork display


class CoverCache:
    """
    Thread-safe singleton.
    Get the shared instance via  CoverCache.instance()
    """

    _instance = None
    _lock      = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Singleton access                                                    #
    # ------------------------------------------------------------------ #
    @classmethod
    def instance(cls) -> "CoverCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = CoverCache()
        return cls._instance

    # ------------------------------------------------------------------ #
    #  Init                                                                #
    # ------------------------------------------------------------------ #
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        
        self._mem_thumb = OrderedDict()   
        self._mem_full = OrderedDict()    
        self._mem_lock = threading.Lock()
        
        
        self.MAX_THUMBS = 150
        self.MAX_FULL = 5

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #
    def _link_path(self, cover_id: str, full: bool) -> str:
        suffix = "_f" if full else "_t"
        return os.path.join(CACHE_DIR, f"{cover_id}{suffix}.link")

    def _img_path(self, img_hash: str) -> str:
        return os.path.join(CACHE_DIR, f"{img_hash}.jpg")

    def _legacy_link_path(self, cover_id: str) -> str:
        """Old single-tier .link files written by previous workers."""
        return os.path.join(CACHE_DIR, f"{cover_id}.link")

    def _read_link(self, link_path: str) -> bytes | None:
        """Follow a .link file → hash → image bytes, or None."""
        try:
            with open(link_path, "r") as f:
                img_hash = f.read().strip()
            img_path = self._img_path(img_hash)
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    return f.read()
        except Exception:
            pass
        return None

    def _write(self, cover_id: str, data: bytes, full: bool):
        """Persist image data and update the appropriate .link file."""
        try:
            img_hash = hashlib.md5(data).hexdigest()
            img_path = self._img_path(img_hash)
            if not os.path.exists(img_path):
                with open(img_path, "wb") as f:
                    f.write(data)
            with open(self._link_path(cover_id, full), "w") as f:
                f.write(img_hash)
        except Exception as e:
            print(f"[CoverCache] Write error for {cover_id}: {e}")

    # ------------------------------------------------------------------ #
    #  Public read API                                                     #
    # ------------------------------------------------------------------ #
    def get_thumb(self, cover_id: str) -> bytes | None:
        """
        Return thumb bytes from memory → new disk tier → legacy disk tier.
        Does NOT do any network I/O.
        """
        cid = str(cover_id)

        with self._mem_lock:
            if cid in self._mem_thumb:
                self._mem_thumb.move_to_end(cid)
                return self._mem_thumb[cid]

        # New-style _t.link
        data = self._read_link(self._link_path(cid, full=False))

        # Fall back to legacy .link (written by old workers)
        if data is None:
            data = self._read_link(self._legacy_link_path(cid))

        if data is not None:
            with self._mem_lock:
                self._mem_thumb[cid] = data
                self._mem_thumb.move_to_end(cid)
                if len(self._mem_thumb) > self.MAX_THUMBS:
                    self._mem_thumb.popitem(last=False)
        return data

    def get_full(self, cover_id: str) -> bytes | None:
        """
        Return full-size bytes from memory → new disk tier.
        Does NOT fall back to thumb (a 200 px image is not acceptable for
        the main now-playing display).
        Does NOT do any network I/O.
        """
        cid = str(cover_id)

        with self._mem_lock:
            if cid in self._mem_full:
                self._mem_full.move_to_end(cid)
                return self._mem_full[cid]

        data = self._read_link(self._link_path(cid, full=True))

        if data is not None:
            with self._mem_lock:
                self._mem_full[cid] = data
                self._mem_full.move_to_end(cid)
                if len(self._mem_full) > self.MAX_FULL:
                    self._mem_full.popitem(last=False)
        return data

    # ------------------------------------------------------------------ #
    #  Public write API                                                    #
    # ------------------------------------------------------------------ #
    def save_thumb(self, cover_id: str, data: bytes):
        """Persist thumb bytes and store in memory cache."""
        cid = str(cover_id)
        self._write(cid, data, full=False)
        with self._mem_lock:
            self._mem_thumb[cid] = data
            self._mem_thumb.move_to_end(cid)
            if len(self._mem_thumb) > self.MAX_THUMBS:
                self._mem_thumb.popitem(last=False)

    def save_full(self, cover_id: str, data: bytes):
        """Persist full-size bytes and store in memory cache."""
        cid = str(cover_id)
        self._write(cid, data, full=True)
        with self._mem_lock:
            self._mem_full[cid] = data
            self._mem_full.move_to_end(cid)
            if len(self._mem_full) > self.MAX_FULL:
                self._mem_full.popitem(last=False)

    # ------------------------------------------------------------------ #
    #  Existence checks (fast — no file read)                             #
    # ------------------------------------------------------------------ #
    def has_thumb(self, cover_id: str) -> bool:
        cid = str(cover_id)
        with self._mem_lock:
            if cid in self._mem_thumb:
                return True
        return (os.path.exists(self._link_path(cid, False))
                or os.path.exists(self._legacy_link_path(cid)))

    def has_full(self, cover_id: str) -> bool:
        cid = str(cover_id)
        with self._mem_lock:
            if cid in self._mem_full:
                return True
        return os.path.exists(self._link_path(cid, True))