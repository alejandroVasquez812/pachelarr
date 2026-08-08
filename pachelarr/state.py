"""Module-level mutable state for Pachelarr.

Holds only:
- ``logger`` (configured at import from ``PACHELARR_LOG_LEVEL`` env)
- the four in-memory LRU caches (``OrderedDict``), still the hot read path
- stats counters (in-memory; periodically flushed to SQLite)
- constant mappings shared across modules

All settings (URLs, API keys, tunables) live in :mod:`pachelarr.settings` and
are read via live getters, NOT as module globals here. The caches remain
``OrderedDict`` so existing tests that assert ``isinstance(m._X, OrderedDict)``
and call ``.clear()`` / mutate ``['expires']`` keep working.
"""
import logging
import os
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()

PACHELARR_LOG_LEVEL = os.getenv("PACHELARR_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, PACHELARR_LOG_LEVEL, logging.INFO))
logger = logging.getLogger("pachelarr")

# --------------------------------------------------------------------------- #
# Stats counters (in-memory; periodically flushed to SQLite)
# --------------------------------------------------------------------------- #
last_search_latency_ms = None
last_search_at = None
torbox_hits = 0
torbox_misses = 0

# --------------------------------------------------------------------------- #
# Constant mappings (not settings)
# --------------------------------------------------------------------------- #
FOREIGN_LANGUAGE_TAGS = frozenset({
    'KR', 'JP', 'RU', 'CN', 'TW', 'HK', 'TH', 'ID', 'MY', 'SG', 'PH', 'VN',
    'IN', 'TR', 'AR', 'MX', 'ES', 'FR', 'DE', 'IT', 'GB', 'US', 'CA', 'AU',
    'BR', 'PT', 'NL', 'SE', 'NO', 'DK', 'FI', 'PL', 'CZ', 'HU', 'RO', 'GR',
    'IL', 'EG', 'SA', 'AE', 'NG', 'ZA', 'KZ', 'UA', 'BY',
})

_SEARCH_PARAMS_FIELD = {
    'movie': 'movieSearchParams',
    'tvsearch': 'tvSearchParams',
    'search': 'searchParams',
    'music': 'musicSearchParams',
    'book': 'bookSearchParams',
}

_PROWLARR_ENUM_TO_OUR_NAME = {
    'q': 'q',
    'season': 'season',
    'ep': 'ep',
    'year': 'year',
    'genre': 'genre',
    'imdbId': 'imdbid',
    'tmdbId': 'tmdbid',
    'tvdbId': 'tvdbid',
}

_TORZNAB_ID_PARAMS = ('imdbid', 'tvdbid', 'tmdbid', 'season', 'ep')

_TORZNAB_NS = 'http://torznab.com/schemas/2015/feed'

# --------------------------------------------------------------------------- #
# In-memory LRU caches (hot read path; persisted to SQLite write-through)
# --------------------------------------------------------------------------- #
_SCRAPE_CACHE = OrderedDict()
_TMDB_TITLE_CACHE = OrderedDict()
_MAGNET_CACHE = OrderedDict()
_INDEXERS_CACHE = OrderedDict()


def magnet_cache_max() -> int:
    """Live magnet cache cap (defaults to TRACKER_SCRAPE_CACHE_MAX)."""
    from pachelarr import settings
    return settings.get_int("TRACKER_SCRAPE_CACHE_MAX", 5000)
