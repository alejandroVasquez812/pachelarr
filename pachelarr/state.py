import logging
import os
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()

PACHELARR_LOG_LEVEL = os.getenv("PACHELARR_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, PACHELARR_LOG_LEVEL, logging.INFO))
logger = logging.getLogger("pachelarr")

PROWLARR_URL = os.getenv("PROWLARR_URL")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY")
TORBOX_API_KEY = os.getenv("TORBOX_API_KEY")
PACHELARR_API_KEY = os.getenv("PACHELARR_API_KEY")
PACHELARR_SEEDERS_BOOST = int(os.getenv("PACHELARR_SEEDERS_BOOST", "10000"))

TORBOX_CHECK_URL = os.getenv("TORBOX_CHECK_URL", "https://api.torbox.app/v1/api/torrents/checkcached")
_configured_chunk = int(os.getenv("TORBOX_CHUNK_SIZE", "100"))
TORBOX_CHUNK_SIZE = min(_configured_chunk, 100)
TORBOX_MAX_RETRIES = int(os.getenv("TORBOX_MAX_RETRIES", "3"))
TORBOX_RETRY_BACKOFF = float(os.getenv("TORBOX_RETRY_BACKOFF", "0.5"))
TRACKER_SCRAPE_ENABLED = os.getenv("TRACKER_SCRAPE_ENABLED", "false").lower() in ("1", "true", "yes")
TRACKER_SCRAPE_CONCURRENCY = int(os.getenv("TRACKER_SCRAPE_CONCURRENCY", "4"))
TRACKER_SCRAPE_TIMEOUT = float(os.getenv("TRACKER_SCRAPE_TIMEOUT", "5.0"))
TRACKER_SCRAPE_BATCH_SIZE = int(os.getenv("TRACKER_SCRAPE_BATCH_SIZE", "50"))
TRACKER_SCRAPE_CACHE_TTL = int(os.getenv("TRACKER_SCRAPE_CACHE_TTL", "300"))
TRACKER_SCRAPE_CACHE_MAX = int(os.getenv("TRACKER_SCRAPE_CACHE_MAX", "5000"))
PACHELARR_TEST_FALLBACK_QUERY = os.getenv("PACHELARR_TEST_FALLBACK_QUERY", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_TITLE_LOOKUP_ENABLED = os.getenv("TMDB_TITLE_LOOKUP_ENABLED", "false").lower() in ("1", "true", "yes")
TMDB_TITLE_LOOKUP_CACHE_TTL = int(os.getenv("TMDB_TITLE_LOOKUP_CACHE_TTL", "300"))
TMDB_TITLE_LOOKUP_CACHE_MAX = int(os.getenv("TMDB_TITLE_LOOKUP_CACHE_MAX", "5000"))
PROWLARR_INDEXERS_CACHE_TTL = int(os.getenv("PROWLARR_INDEXERS_CACHE_TTL", "300"))
PROWLARR_INDEXERS_CACHE_MAX = int(os.getenv("PROWLARR_INDEXERS_CACHE_MAX", "1"))
PROWLARR_PARALLEL_INDEXER_CONCURRENCY = int(os.getenv("PROWLARR_PARALLEL_INDEXER_CONCURRENCY", "8"))
PROWLARR_INDEXER_SEARCH_TIMEOUT = float(os.getenv("PROWLARR_INDEXER_SEARCH_TIMEOUT", "10.0"))

last_search_latency_ms = None
last_search_at = None
torbox_hits = 0
torbox_misses = 0

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

_SCRAPE_CACHE = OrderedDict()
_TMDB_TITLE_CACHE = OrderedDict()
_MAGNET_CACHE = OrderedDict()
_MAGNET_CACHE_MAX = TRACKER_SCRAPE_CACHE_MAX
_INDEXERS_CACHE = OrderedDict()
