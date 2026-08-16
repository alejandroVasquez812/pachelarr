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
from collections import OrderedDict, deque

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

# Per-indexer stats keyed by indexer id (int). Each value:
# {requests, errors, total_latency_ms, last_latency_ms, cached, uncached}.
# Populated only when the PER_INDEXER granularity is enabled.
_INDEXER_STATS = {}

# Recent search history: a capped ring of per-search record dicts.
_SEARCH_HISTORY = deque()


def record_indexer_stat(idx_id, latency_ms, error=False):
    """Record a single per-indexer request outcome (success or error).

    Latency is milliseconds. No-op when the PER_INDEXER granularity is
    disabled so the hot path pays nothing when stats are off.
    """
    from pachelarr import settings
    if not settings.stats_granularity_enabled("PER_INDEXER"):
        return
    entry = _INDEXER_STATS.setdefault(idx_id, {
        "requests": 0, "errors": 0, "total_latency_ms": 0.0,
        "last_latency_ms": None, "cached": 0, "uncached": 0,
    })
    entry["requests"] += 1
    if error:
        entry["errors"] += 1
    entry["total_latency_ms"] += float(latency_ms or 0)
    entry["last_latency_ms"] = float(latency_ms or 0)


def record_indexer_cache_attribution(idx_id, cached_n, uncached_n):
    """Accumulate torbox cached/uncached attribution for an indexer."""
    from pachelarr import settings
    if not settings.stats_granularity_enabled("PER_INDEXER"):
        return
    entry = _INDEXER_STATS.setdefault(idx_id, {
        "requests": 0, "errors": 0, "total_latency_ms": 0.0,
        "last_latency_ms": None, "cached": 0, "uncached": 0,
    })
    entry["cached"] += int(cached_n or 0)
    entry["uncached"] += int(uncached_n or 0)


def record_search(record):
    """Append a search record to the history ring, evicting the oldest when
    over the configured cap. No-op when the PER_SEARCH granularity is off."""
    from pachelarr import settings
    if not settings.stats_granularity_enabled("PER_SEARCH"):
        return
    cap = max(settings.get_int("STATS_PER_SEARCH_MAX", 100), 1)
    _SEARCH_HISTORY.append(record)
    while len(_SEARCH_HISTORY) > cap:
        _SEARCH_HISTORY.popleft()
    # Write-through to SQLite (best-effort) so history survives restarts.
    try:
        from pachelarr import db
        db.insert_search(record)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"record_search DB write failed: {e}", exc_info=True)


# --------------------------------------------------------------------------- #
# Param overrides (global + per-indexer Torznab query param overrides)
# --------------------------------------------------------------------------- #

# Dict keyed by scope: "global" or "indexer:<id>". Each value is a params dict.
# Rebuilt from SQLite on startup via db.load_param_overrides_into_state().
_PARAM_OVERRIDES = {}


def get_param_overrides(indexer_id=None) -> dict:
    """Return merged param overrides for a given indexer.

    Global overrides are applied first as defaults; per-indexer overrides
    (when ``indexer_id`` is provided) take precedence. Returns a new dict so
    callers can safely mutate it.
    """
    merged = {}
    global_params = _PARAM_OVERRIDES.get("global")
    if global_params:
        merged.update(global_params)
    if indexer_id is not None:
        per_indexer = _PARAM_OVERRIDES.get(f"indexer:{indexer_id}")
        if per_indexer:
            merged.update(per_indexer)
    return merged


def set_param_overrides(scope: str, params: dict) -> None:
    """Store param overrides for a scope in-memory and write-through to SQLite."""
    if not params:
        _PARAM_OVERRIDES.pop(scope, None)
        from pachelarr import db
        db.delete_param_overrides(scope)
        return
    _PARAM_OVERRIDES[scope] = dict(params)
    from pachelarr import db
    db.upsert_param_overrides(scope, params)


def clear_param_overrides(scope: str) -> None:
    """Remove param overrides for a scope from memory and SQLite."""
    _PARAM_OVERRIDES.pop(scope, None)
    from pachelarr import db
    db.delete_param_overrides(scope)


def all_param_overrides() -> dict:
    """Return a snapshot of all param overrides (for the REST GET endpoint)."""
    return {scope: dict(params) for scope, params in _PARAM_OVERRIDES.items()}


# --------------------------------------------------------------------------- #
# Cache invalidation + stats reset helpers
# --------------------------------------------------------------------------- #

def invalidate_indexers_cache() -> None:
    """Clear the in-memory indexer listing cache and the SQLite cache row."""
    _INDEXERS_CACHE.clear()
    from pachelarr import db
    db.delete_indexers_cache_row()
    logger.info("Indexer listing cache invalidated")


def reset_indexer_stats(indexer_id=None) -> None:
    """Reset per-indexer stats — all, or one indexer if ``indexer_id`` is given."""
    if indexer_id is not None:
        _INDEXER_STATS.pop(indexer_id, None)
    else:
        _INDEXER_STATS.clear()
    from pachelarr import db
    db.delete_indexer_stats(indexer_id)
    logger.info(f"Per-indexer stats reset (indexer_id={indexer_id})")


def reset_search_history() -> None:
    """Clear in-memory search history and the SQLite search rows."""
    _SEARCH_HISTORY.clear()
    from pachelarr import db
    db.delete_searches()
    logger.info("Search history reset")

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
_TORBOX_CACHE = OrderedDict()

# TVDB v4 JWT token cache (in-memory only; re-acquired on restart).
# Populated by pachelarr.tvdb._login; read by _ensure_token.
_TVDB_TOKEN = {"token": None, "expires_at": 0.0}


def magnet_cache_max() -> int:
    """Live magnet cache cap (defaults to TRACKER_SCRAPE_CACHE_MAX)."""
    from pachelarr import settings
    return settings.get_int("TRACKER_SCRAPE_CACHE_MAX", 5000)


def torbox_cache_max() -> int:
    """Live Torbox known-cached infohash cache cap (defaults to TORBOX_CACHE_MAX)."""
    from pachelarr import settings
    return settings.get_int("TORBOX_CACHE_MAX", 5000)
