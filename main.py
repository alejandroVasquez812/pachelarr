import os
import time
import socket
from collections import OrderedDict
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timezone
import logging
from fastapi import FastAPI, Request, Response
import aiohttp
from lxml import etree as ET
from urllib.parse import urljoin, parse_qs, unquote

load_dotenv()
PACHELARR_LOG_LEVEL = os.getenv("PACHELARR_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, PACHELARR_LOG_LEVEL, logging.INFO))
logger = logging.getLogger("pachelarr")

PROWLARR_URL = os.getenv("PROWLARR_URL")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY")
TORBOX_API_KEY = os.getenv("TORBOX_API_KEY")
PACHELARR_API_KEY = os.getenv("PACHELARR_API_KEY")
# Seed count used to boost cached items (default: 10000)
PACHELARR_SEEDERS_BOOST = int(os.getenv("PACHELARR_SEEDERS_BOOST", "10000"))

TORBOX_CHECK_URL = os.getenv("TORBOX_CHECK_URL", "https://api.torbox.app/v1/api/torrents/checkcached")
_configured_chunk = int(os.getenv("TORBOX_CHUNK_SIZE", "100"))
# Torbox supports up to 100 per request; enforce a cap.
TORBOX_CHUNK_SIZE = min(_configured_chunk, 100)
TORBOX_MAX_RETRIES = int(os.getenv("TORBOX_MAX_RETRIES", "3"))
TORBOX_RETRY_BACKOFF = float(os.getenv("TORBOX_RETRY_BACKOFF", "0.5"))
TRACKER_SCRAPE_ENABLED = os.getenv("TRACKER_SCRAPE_ENABLED", "false").lower() in ("1", "true", "yes")
TRACKER_SCRAPE_CONCURRENCY = int(os.getenv("TRACKER_SCRAPE_CONCURRENCY", "4"))
TRACKER_SCRAPE_TIMEOUT = float(os.getenv("TRACKER_SCRAPE_TIMEOUT", "5.0"))
TRACKER_SCRAPE_BATCH_SIZE = int(os.getenv("TRACKER_SCRAPE_BATCH_SIZE", "50"))
TRACKER_SCRAPE_CACHE_TTL = int(os.getenv("TRACKER_SCRAPE_CACHE_TTL", "300"))
TRACKER_SCRAPE_CACHE_MAX = int(os.getenv("TRACKER_SCRAPE_CACHE_MAX", "5000"))
# Optional query fallback used when an incoming search contains categories but no
# query. Useful to improve Sonarr's "Test" indexer behavior where Sonarr sends a
# 0-query category-only search to verify indexer connectivity.
PACHELARR_TEST_FALLBACK_QUERY = os.getenv("PACHELARR_TEST_FALLBACK_QUERY", "")
# TMDB API key for looking up movie/TV titles from IMDb/TVDB/TMDB IDs
# Get a free key at: https://www.themoviedb.org/settings/api
# This is REQUIRED for ID-based searches to work with indexers that don't support IDs
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
# Opt-in: resolve IDs (imdbid/tvdbid) from title queries via TMDB so the
# Prowlarr ID-token path can enrich title-only movie/tv searches. Adds latency.
# Requires TMDB_API_KEY to be set as well.
TMDB_TITLE_LOOKUP_ENABLED = os.getenv("TMDB_TITLE_LOOKUP_ENABLED", "false").lower() in ("1", "true", "yes")
TMDB_TITLE_LOOKUP_CACHE_TTL = int(os.getenv("TMDB_TITLE_LOOKUP_CACHE_TTL", "300"))
TMDB_TITLE_LOOKUP_CACHE_MAX = int(os.getenv("TMDB_TITLE_LOOKUP_CACHE_MAX", "5000"))
# Per-indexer search strategy: cache the full IndexerResource[] list so we can
# customize each Prowlarr /api/v1/search call by each indexer's capabilities.
# TTL in seconds (default 300), max cached listings (default 1: a single
# listing object holds the whole indexer list), and the max number of
# per-indexer search requests executed in parallel via asyncio.Semaphore.
PROWLARR_INDEXERS_CACHE_TTL = int(os.getenv("PROWLARR_INDEXERS_CACHE_TTL", "300"))
PROWLARR_INDEXERS_CACHE_MAX = int(os.getenv("PROWLARR_INDEXERS_CACHE_MAX", "1"))
PROWLARR_PARALLEL_INDEXER_CONCURRENCY = int(os.getenv("PROWLARR_PARALLEL_INDEXER_CONCURRENCY", "8"))
PROWLARR_INDEXER_SEARCH_TIMEOUT = float(os.getenv("PROWLARR_INDEXER_SEARCH_TIMEOUT", "10.0"))


@asynccontextmanager
async def lifespan(app):
    _required = {
        "PROWLARR_URL": PROWLARR_URL,
        "PROWLARR_API_KEY": PROWLARR_API_KEY,
        "TORBOX_API_KEY": TORBOX_API_KEY,
    }
    _missing = [name for name, val in _required.items() if not (val and str(val).strip())]
    if _missing:
        msg = f"Missing required environment variables: {', '.join(_missing)}. Set them and restart."
        logger.error(msg)
        raise RuntimeError(msg)
    connector_limit = max(PROWLARR_PARALLEL_INDEXER_CONCURRENCY * 2, 16)
    try:
        connector = aiohttp.TCPConnector(limit=connector_limit, limit_per_host=0)
    except Exception:
        connector = aiohttp.TCPConnector()
    app.state.session = aiohttp.ClientSession(connector=connector)
    try:
        yield
    finally:
        await app.state.session.close()


app = FastAPI(lifespan=lifespan)

last_search_latency_ms = None
last_search_at = None
torbox_hits = 0
torbox_misses = 0


async def lookup_title_from_id(session, imdbid=None, tmdbid=None, tvdbid=None, rid=None, search_type='movie'):
    """Look up movie/TV title from external IDs using TMDB API.
    
    TMDB supports:
    - IMDb IDs (movies and TV shows)
    - TVDB IDs (TV shows)
    - TVRage IDs (TV shows, deprecated)
    - Direct TMDB IDs (movies and TV shows)
    
    Requires TMDB_API_KEY environment variable.
    Get a free API key at: https://www.themoviedb.org/settings/api
    """
    if not TMDB_API_KEY:
        logger.debug("TMDB_API_KEY not configured, skipping title lookup. Set TMDB_API_KEY env var to enable ID-based search support.")
        return None
    
    try:
        # Try IMDb ID lookup (works for both movies and TV)
        if imdbid:
            url = f"https://api.themoviedb.org/3/find/tt{imdbid}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    # Check movie results first
                    if data.get('movie_results') and len(data['movie_results']) > 0:
                        movie = data['movie_results'][0]
                        title = movie.get('title', '')
                        release_date = movie.get('release_date', '')
                        year = release_date.split('-')[0] if release_date else ''
                        if title and year:
                            logger.info(f"Successfully looked up movie via TMDB (IMDb): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up movie via TMDB (IMDb): {title}")
                            return title
                    # Check TV results
                    if data.get('tv_results') and len(data['tv_results']) > 0:
                        show = data['tv_results'][0]
                        title = show.get('name', '')
                        first_air = show.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (IMDb): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (IMDb): {title}")
                            return title
        
        # Try TVDB ID lookup (TV shows only)
        if tvdbid:
            url = f"https://api.themoviedb.org/3/find/{tvdbid}?api_key={TMDB_API_KEY}&external_source=tvdb_id"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('tv_results') and len(data['tv_results']) > 0:
                        show = data['tv_results'][0]
                        title = show.get('name', '')
                        first_air = show.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (TVDB): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (TVDB): {title}")
                            return title
        
        # Try TVRage ID lookup (deprecated but still supported by TMDB)
        if rid:
            url = f"https://api.themoviedb.org/3/find/{rid}?api_key={TMDB_API_KEY}&external_source=tvrage_id"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('tv_results') and len(data['tv_results']) > 0:
                        show = data['tv_results'][0]
                        title = show.get('name', '')
                        first_air = show.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (TVRage): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (TVRage): {title}")
                            return title
        
        # Direct TMDB ID lookup
        if tmdbid:
            # Determine if it's a movie or TV show based on search type
            if search_type in ('movie', 'search'):
                url = f"https://api.themoviedb.org/3/movie/{tmdbid}?api_key={TMDB_API_KEY}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                    if response.status == 200:
                        data = await response.json()
                        title = data.get('title', '')
                        release_date = data.get('release_date', '')
                        year = release_date.split('-')[0] if release_date else ''
                        if title and year:
                            logger.info(f"Successfully looked up movie via TMDB (TMDB ID): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up movie via TMDB (TMDB ID): {title}")
                            return title
            else:
                # Try as TV show
                url = f"https://api.themoviedb.org/3/tv/{tmdbid}?api_key={TMDB_API_KEY}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                    if response.status == 200:
                        data = await response.json()
                        title = data.get('name', '')
                        first_air = data.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (TMDB ID): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (TMDB ID): {title}")
                            return title
        
        logger.debug(f"Could not lookup title for imdbid={imdbid} tmdbid={tmdbid} tvdbid={tvdbid} rid={rid}")
        return None
    except Exception as e:
        logger.warning(f"Error looking up title from ID: {e}")
        return None

async def lookup_identifier_from_query(session, query, search_type='movie'):
    """Look up TMDB/IMDb/TVDB IDs from a title query via TMDB search.

    Reverse of lookup_title_from_id: given a title (optionally with a trailing
    year), resolve imdbid (movies+TV) and tvdbid (TV) so search_prowlarr can
    emit {imdbid:..}/{tvdbid:..} tokens for ID-only indexers.

    Returns a dict like {'tmdbid':..,'imdbid':..,'tvdbid':..} (only keys found),
    or None on failure/empty. imdbid is stored WITHOUT the 'tt' prefix to match
    codebase convention. Requires TMDB_API_KEY AND TMDB_TITLE_LOOKUP_ENABLED.
    """
    if not TMDB_API_KEY or not TMDB_TITLE_LOOKUP_ENABLED or not query:
        logger.debug("Title->ID lookup disabled or missing query/TMDB_API_KEY; skipping.")
        return None

    # Parse a trailing 4-digit year as a TMDB hint. Keep the outgoing query
    # unchanged (year stays in the title text sent to Prowlarr).
    year = None
    stripped = query.strip()
    if ' ' in stripped:
        last_token = stripped.rsplit(' ', 1)[-1]
        if last_token.isdigit() and len(last_token) == 4:
            year = last_token
            # Drop the year so a trailing foreign-language tag (now exposed as
            # the new last token) can be stripped next without re-matching.
            stripped = stripped.rsplit(' ', 1)[0].rstrip()

    # Strip a trailing foreign-language origin tag (e.g. "Boys Over Flowers KR"
    # -> "Boys Over Flowers") so TMDB search matches the clean title.
    stripped = strip_foreign_language_tag(stripped)

    cache_key = (stripped.lower(), year, search_type)
    cached = _tmdb_title_cache_get(cache_key)
    if cached is not None:
        logger.debug(f"Title->ID lookup cache hit for {cache_key!r}: {cached}")
        return cached

    try:
        tmdb_id = None
        if search_type == 'movie':
            url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={stripped}"
            if year:
                url += f"&year={year}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    if results:
                        tmdb_id = results[0].get('id')
            if tmdb_id:
                ext_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids?api_key={TMDB_API_KEY}"
                async with session.get(ext_url, timeout=aiohttp.ClientTimeout(total=3)) as ext_response:
                    if ext_response.status == 200:
                        ext_data = await ext_response.json()
                        imdb_raw = ext_data.get('imdb_id') or ''
                        imdbid = imdb_raw[2:] if imdb_raw.startswith('tt') else imdb_raw
                        ids = {'tmdbid': str(tmdb_id)}
                        if imdbid:
                            ids['imdbid'] = imdbid
                        logger.info(f"Successfully looked up movie IDs from title: {ids}")
                        _tmdb_title_cache_put(cache_key, ids)
                        return ids
        elif search_type == 'tvsearch':
            url = f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={stripped}"
            if year:
                url += f"&first_air_date_year={year}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    if results:
                        tmdb_id = results[0].get('id')
            if tmdb_id:
                ext_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids?api_key={TMDB_API_KEY}"
                async with session.get(ext_url, timeout=aiohttp.ClientTimeout(total=3)) as ext_response:
                    if ext_response.status == 200:
                        ext_data = await ext_response.json()
                        imdb_raw = ext_data.get('imdb_id') or ''
                        imdbid = imdb_raw[2:] if imdb_raw.startswith('tt') else imdb_raw
                        tvdb_raw = ext_data.get('tvdb_id')
                        ids = {'tmdbid': str(tmdb_id)}
                        if imdbid:
                            ids['imdbid'] = imdbid
                        if tvdb_raw:
                            ids['tvdbid'] = str(tvdb_raw)
                        logger.info(f"Successfully looked up TV IDs from title: {ids}")
                        _tmdb_title_cache_put(cache_key, ids)
                        return ids

        logger.debug(f"Could not lookup IDs from title: query={query!r} search_type={search_type}")
        return None
    except Exception as e:
        logger.warning(f"Error looking up IDs from title: {e}")
        return None

# Common foreign-language origin tags that clients/users append to titles to
# disambiguate a release's country of origin (e.g. "Boys Over Flowers KR").
# Only stripped when present as the trailing whitespace-separated token, so
# genuine titles containing these substrings mid-string are untouched.
FOREIGN_LANGUAGE_TAGS = frozenset({
    'KR', 'JP', 'RU', 'CN', 'TW', 'HK', 'TH', 'ID', 'MY', 'SG', 'PH', 'VN',
    'IN', 'TR', 'AR', 'MX', 'ES', 'FR', 'DE', 'IT', 'GB', 'US', 'CA', 'AU',
    'BR', 'PT', 'NL', 'SE', 'NO', 'DK', 'FI', 'PL', 'CZ', 'HU', 'RO', 'GR',
    'IL', 'EG', 'SA', 'AE', 'NG', 'ZA', 'KZ', 'UA', 'BY',
})

def strip_foreign_language_tag(query):
    """Remove a trailing foreign-language origin tag (e.g. "KR", "JP") from a
    title query. Only matched when it stands alone as the final whitespace-
    separated token. Returns the cleaned query (unchanged if no match).
    """
    if not query:
        return query
    stripped = query.strip()
    if ' ' not in stripped:
        return query
    last_token = stripped.rsplit(' ', 1)[-1]
    if last_token.isalpha() and len(last_token) == 2 and last_token.upper() in FOREIGN_LANGUAGE_TAGS:
        cleaned = stripped.rsplit(' ', 1)[0].rstrip()
        return cleaned if cleaned else query
    return query

def _mask_prowlarr_key(k):
    """Mask a Prowlarr API key for safe debug logging."""
    if not k:
        return None
    if len(k) <= 8:
        return "****"
    return k[:4] + "*" * (len(k) - 8) + k[-4:]


def _normalize_indexer_list(raw):
    """Normalize a Prowlarr /api/v1/indexer response to a list of indexer dicts.

    Prowlarr can return a list or a dict with keys like records/results/indexers/
    items/data; defensive normalization keeps both shapes working.
    """
    indexers = []
    if isinstance(raw, list):
        indexers = raw
    elif isinstance(raw, dict):
        for key in ('records', 'results', 'indexers', 'items', 'data'):
            if key in raw and isinstance(raw[key], list):
                indexers = raw[key]
                break
        if not indexers:
            # If keys are numeric or it's a single indexer dict, try aggregating
            if all(isinstance(v, dict) for v in raw.values()):
                indexers = [v for v in raw.values()]
            else:
                # If it's a single indexer returned as dict
                indexers = [raw]
    return indexers


def _indexer_is_enabled(idx):
    """Return True if an indexer dict should be considered enabled.

    The Prowlarr IndexerResource uses ``enable`` (bool) as the primary flag, but
    older/variant responses may use enabled/isEnabled/enabledByDefault/disabled.
    The first present key wins (disabled is inverted).
    """
    idx_id = idx.get('id') or idx.get('indexerId') or idx.get('IndexerId')
    enabled = True
    for key in ('enable', 'enabled', 'isEnabled', 'enabledByDefault', 'disabled'):
        if key in idx:
            val = idx.get(key)
            if key == 'disabled':
                enabled = not bool(val)
            else:
                enabled = bool(val)
            break
    return bool(idx_id and enabled)


def isTorrentIndexer(indexer):
    if not isinstance(indexer, dict):
        return False
    return str(indexer.get('protocol', '')).lower() == 'torrent'
async def get_prowlarr_indexers_cached(session):
    """Fetch the full list of Prowlarr indexers (IndexerResource[]) with caching.

    Returns the FULL indexer dicts (not just ids) so the per-indexer search
    strategy can read each indexer's ``capabilities``. Serves from the
    in-memory cache when fresh (< PROWLARR_INDEXERS_CACHE_TTL); otherwise
    refetches. On aiohttp.ClientError: return the last-good cached list if
    present (logged as a warning), else an empty list.
    """
    cached = _indexers_cache_get()
    if cached is not None:
        logger.debug(f"Prowlarr indexers cache hit: {len(cached)} indexers")
        return cached
    try:
        url = urljoin(PROWLARR_URL, "/api/v1/indexer")
        headers = {"X-Api-Key": PROWLARR_API_KEY}
        logger.debug(
            f"Prowlarr indexers request: GET {url} headers={{'X-Api-Key': '{_mask_prowlarr_key(PROWLARR_API_KEY)}'}}"
        )
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            raw = await response.json()
            indexers = _normalize_indexer_list(raw)
            indexers = [idx for idx in indexers if isTorrentIndexer(idx)]
        _indexers_cache_put(indexers)
        enabled_ids = [idx.get('id') for idx in indexers if _indexer_is_enabled(idx)]
        logger.info(f'Prowlarr: found {len(indexers)} indexers ({len(enabled_ids)} enabled): {enabled_ids}')
        return indexers
    except aiohttp.ClientError as e:
        logger.warning(f"Error fetching Prowlarr indexers: {e}; attempting last-good cache fallback")
        last_good = _INDEXERS_CACHE.get('listing')
        if last_good is not None:
            # Serve the stale listing rather than failing the whole search.
            stale = last_good.get('indexers', [])
            logger.warning(f"Prowlarr indexers: serving {len(stale)} stale cached indexers after fetch error")
            return stale
        logger.warning("Prowlarr indexers fetch failed and no cache available; returning empty list")
        return []


async def get_all_prowlarr_indexers(session):
    """Backward-compat shim: return enabled indexer IDs as ints.

    Prefer :func:`get_prowlarr_indexers_cached` for new code; this preserves
    the historical contract of returning a flat list of enabled ids.
    """
    indexers = await get_prowlarr_indexers_cached(session)
    return [idx.get('id') for idx in indexers if _indexer_is_enabled(idx)]


# Map a search type to the capabilities field that lists its supported params.
_SEARCH_PARAMS_FIELD = {
    'movie': 'movieSearchParams',
    'tvsearch': 'tvSearchParams',
    'search': 'searchParams',
    'music': 'musicSearchParams',
    'book': 'bookSearchParams',
}

# Prowlarr OpenAPI enum values (camelCase) -> our search_kwargs param names
# (lowercase). season/ep/year/genre/q pass through unchanged.
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

# Standard Torznab ID parameters forwarded as distinct query params when the
# indexer supports them. Sent as-is (no {key:val} token embedding, no name
# remapping). ep is the Torznab param for episode.
_TORZNAB_ID_PARAMS = ('imdbid', 'tvdbid', 'tmdbid', 'season', 'ep')


def _collect_indexer_category_ids(categories):
    """Flatten an indexer's capabilities.categories tree into a set of ids.

    Walks the nested subCategories list recursively, collecting every ``id``
    (top-level + all nested subs). IDs are normalized to int when possible.
    """
    ids = set()
    if not categories or not isinstance(categories, list):
        return ids
    stack = list(categories)
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        cid = node.get('id')
        if cid is not None:
            try:
                ids.add(int(cid))
            except (TypeError, ValueError):
                ids.add(cid)
        subs = node.get('subCategories')
        if isinstance(subs, list):
            stack.extend(subs)
    return ids


def select_indexers_for_query(indexers, search_type, categories, has_ids):
    """Select indexers eligible to answer this query.

    Filters:
      * ``enable`` is True (defensive key loop via _indexer_is_enabled).
      * ``supportsSearch`` is True.
      * Lenient category match: if the client requested categories, keep the
        indexer iff its declared category ids (incl. nested subCategories)
        intersect the requested set. If the client sent no categories, keep
        all (subject to the filters above).

    ``has_ids`` is informational and currently does not gate selection here:
    q-only indexers are still called (using the title fallback query). The
    decision to skip a q-only indexer with no usable query is made later in
    build_per_indexer_params.
    """
    requested = set()
    for c in (categories or []):
        try:
            requested.add(int(c))
        except (TypeError, ValueError):
            requested.add(c)
    selected = []
    for idx in indexers:
        if not isinstance(idx, dict):
            continue
        if not _indexer_is_enabled(idx):
            continue
        if not bool(idx.get('supportsSearch', True)):
            continue
        if requested:
            caps = idx.get('capabilities') or {}
            declared = _collect_indexer_category_ids(caps.get('categories'))
            if not (declared & requested):
                continue
        selected.append(idx)
    logger.debug(
        f"select_indexers_for_query: {len(indexers)} total -> {len(selected)} selected "
        f"(type={search_type!r} cats={list(requested)} has_ids={has_ids})"
    )
    return selected


def _indexer_supported_our_names(indexer, search_type):
    """Return the set of OUR param names an indexer supports for a search type.

    Reads capabilities.<field> (movieSearchParams/tvSearchParams/searchParams/
    musicSearchParams/bookSearchParams), normalizes each Prowlarr camelCase enum
    to our lowercase param name, and returns the set. Always includes 'q'
    implicitly (every search type supports q) for convenience in callers.
    """
    caps = (indexer.get('capabilities') or {}) if isinstance(indexer, dict) else {}
    field = _SEARCH_PARAMS_FIELD.get(search_type)
    supported_ours = {'q'}
    if field:
        enums = caps.get(field) or []
        if isinstance(enums, list):
            for e in enums:
                if not isinstance(e, str):
                    continue
                ours = _PROWLARR_ENUM_TO_OUR_NAME.get(e)
                if ours:
                    supported_ours.add(ours)
                elif e and e.islower():
                    # Unknown but already-lowercase param (e.g. album/artist);
                    # keep as-is so future token maps can extend behavior.
                    supported_ours.add(e)
    return supported_ours


def build_per_indexer_params(indexer, search_kwargs):
    """Build per-indexer Torznab GET params, filtered by indexer capabilities.

    Returns a params dict (our-names) for the per-indexer ``/<indexerId>/api``
    Torznab passthrough call, or None to signal "skip this indexer".

    IDs (imdbid/tvdbid/tmdbid/season/ep) are sent as standard Torznab query
    params (not {key:val} tokens) only when the indexer's ``*SearchParams``
    capabilities list them. ``params['query']`` is just the base query title.

    The returned dict uses keys: ``query``, ``type``, ``categories`` (list when
    present), any of the 5 ID params, plus ``limit``/``offset``. It does NOT
    include ``indexerIds`` (scoping is via the ``/<indexerId>/api`` URL path).
    """
    if not isinstance(indexer, dict):
        return None
    idx_id = indexer.get('id') or indexer.get('indexerId') or indexer.get('IndexerId')
    if not idx_id:
        return None
    search_type = search_kwargs.get('type', 'search')
    supported = _indexer_supported_our_names(indexer, search_type)

    base_query = (search_kwargs.get('query') or '').strip()

    params = {}
    if base_query:
        params['query'] = base_query
    # Forward supported IDs as standard Torznab query params (no tokens).
    for id_name in _TORZNAB_ID_PARAMS:
        if id_name in supported:
            v = search_kwargs.get(id_name)
            if v:
                params[id_name] = str(v)
    cats = search_kwargs.get('categories')
    if cats:
        params['categories'] = list(cats)
    params['type'] = search_type

    # q-only indexer (no supported IDs) with no base query and no categories
    # -> nothing to search with. When categories are present this is an
    # RSS-style / category-only search (e.g. Sonarr/Radarr test or indexer
    # ping), so forward it to the indexer even without a query/IDs.
    if (
        not params.get('query')
        and not any(params.get(id_name) for id_name in _TORZNAB_ID_PARAMS)
        and not params.get('categories')
    ):
        logger.debug(
            f"build_per_indexer_params: skipping indexer {idx_id} (q-only, no query/title, no supported IDs, no categories)"
        )
        return None

    # Paging: forward limit/offset when present; drop limit=0 (client test noise).
    if search_kwargs.get('limit'):
        try:
            if int(search_kwargs['limit']) > 0:
                params['limit'] = str(int(search_kwargs['limit']))
        except (TypeError, ValueError):
            params['limit'] = search_kwargs['limit']
    if search_kwargs.get('offset'):
        params['offset'] = search_kwargs['offset']
    return params


def dedupe_hashes_preserve_order(hashes):
    """Return list of unique hashes in the original order, normalized to lowercase.

    This ensures we don't make redundant calls to Torbox while keeping
    a stable ordering of values (useful for predictable chunking).
    """
    seen = set()
    out = []
    for h in (hashes or []):
        if not h:
            continue
        hl = h.lower()
        if hl not in seen:
            seen.add(hl)
            out.append(hl)
    return out

@app.get("/api")
async def torznab_proxy(request: Request):
    """Handles Torznab requests from Sonarr/Radarr."""
    params = request.query_params
    logger.info(f"Incoming request: {dict(params)} from {request.client}")

    if params.get('t') == 'caps':
        return Response(content=get_caps_xml(), media_type="application/xml")

    if params.get('t') in ['search', 'tvsearch', 'movie']:
        try:
            return await handle_search(params, request.app.state.session)
        except Exception:
            logger.exception("Unhandled error in search handler")
            return Response(status_code=500, content="Internal Server Error")
    
    return Response(status_code=400, content="Invalid request type")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/statsz")
async def statsz():
    listing = _INDEXERS_CACHE.get('listing')
    if listing is not None:
        age_seconds = int(listing.get('expires', 0.0) - time.time())
    else:
        age_seconds = None
    return {
        "status": "ok",
        "scrape_cache_size": len(_SCRAPE_CACHE),
        "tmdb_title_cache_size": len(_TMDB_TITLE_CACHE),
        "magnet_cache_size": len(_MAGNET_CACHE),
        "indexers_cache": {
            "size": len(_INDEXERS_CACHE),
            "age_seconds": age_seconds,
        },
        "last_search_latency_ms": last_search_latency_ms,
        "last_search_at": last_search_at,
        "torbox_hits": torbox_hits,
        "torbox_misses": torbox_misses,
    }


async def handle_search(params, session):
    """Performs search, checks cache, and returns enriched results."""
    global last_search_latency_ms, last_search_at
    t0 = time.time()
    try:
        return await _handle_search_impl(params, session)
    finally:
        last_search_at = time.time()
        last_search_latency_ms = (time.time() - t0) * 1000.0


async def _handle_search_impl(params, session):
    """Performs search, checks cache, and returns enriched results."""
    query = params.get('q', '')
    # Strip a trailing foreign-language origin tag (e.g. "Boys Over Flowers KR"
    # -> "Boys Over Flowers") so the cleaned title is searched against Prowlarr.
    cleaned_query = strip_foreign_language_tag(query)
    if cleaned_query != query:
        logger.info(f"Stripped trailing foreign-language tag: {query!r} -> {cleaned_query!r}")
        query = cleaned_query
    # Check if there are any valid identifier parameters (these are valid searches without q)
    has_identifier = any(params.get(k) for k in ('tvdbid', 'imdbid', 'tmdbid', 'season', 'ep'))
    # If query is missing but categories are present and a fallback is configured,
    # substitute it early so downstream logic picks it up. Don't apply fallback if identifiers are present.
    if not query and not has_identifier and params.get('cat'):
        logger.info(f"Incoming category-only request detected; applying fallback query '{PACHELARR_TEST_FALLBACK_QUERY}'")
    categories = [cat for cat in params.get('cat', '').split(',') if cat]

    # Build search parameters for Prowlarr; include tvdbid, season, ep, rid, imdbid when present
    search_kwargs = {
        'query': query,
        'categories': categories,
        'type': params.get('t', 'search')
    }
    logger.info(f"Initial search_kwargs: {search_kwargs}")
    # Pull in optional identifiers from parameters
    for key in ('tvdbid', 'season', 'ep', 'imdbid', 'tmdbid'):
        raw = params.get(key)
        if not raw:
            continue
        val = raw.strip()
        # Torznab season/ep are integers. A misconfigured upstream proxy or
        # client may inject trailing junk such as " HTTP/1.1" (the request
        # line version) into the query value, e.g. ep="1 HTTP/1.1". Strip
        # everything after the first whitespace token and keep only the
        # leading integer so it does not flow into the Prowlarr query token.
        if key in ('season', 'ep'):
            first_token = val.split()[0] if val.split() else ''
            # Keep only leading digits; drop any non-numeric prefix/suffix.
            digits = ''
            for ch in first_token:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits and digits != val:
                logger.warning(f"Sanitized {key!r}: {raw!r} -> {digits!r} (non-numeric trailing content stripped)")
                val = digits
            elif not digits:
                logger.warning(f"Non-numeric {key}={raw!r} dropped; not forwarding to Prowlarr")
                continue
            else:
                val = digits
        search_kwargs[key] = val

    # If we have an ID but no query text, try to look up the title
    # This helps Prowlarr work with indexers that don't support ID-based searches
    if not query and has_identifier:
        logger.info(f"Attempting title lookup for ID-based search: imdbid={params.get('imdbid')} tmdbid={params.get('tmdbid')} tvdbid={params.get('tvdbid')} rid={params.get('rid')}")
        title = await lookup_title_from_id(
            session,
            imdbid=params.get('imdbid'),
            tmdbid=params.get('tmdbid'),
            tvdbid=params.get('tvdbid'),
            rid=params.get('rid'),
            search_type=params.get('t', 'search')
        )
        if title:
            logger.info(f"Looked up title '{title}' from ID parameters")
            query = title
            search_kwargs['query'] = title
        else:
            logger.info("Title lookup failed or returned no results")

    # If we have a query but no ID, try to look up IDs from the title.
    # This lets the Prowlarr ID-token path enrich title-only searches for
    # ID-capable indexers. Only for movie/tvsearch (generic 'search' emits
    # no tokens, so the lookup would be wasted). Don't run when IDs were
    # already supplied (has_identifier guard avoids a double lookup).
    if query and not has_identifier and params.get('t') in ('movie', 'tvsearch'):
        logger.info(f"Attempting ID lookup for title-based search: query={query!r} type={params.get('t')}")
        ids = await lookup_identifier_from_query(session, query, search_type=params.get('t'))
        if ids:
            logger.info(f"Looked up IDs from title: {ids}")
            for k in ('tmdbid', 'imdbid', 'tvdbid'):
                if ids.get(k):
                    search_kwargs[k] = ids[k]
        else:
            logger.info("ID lookup from title returned no results")

    # Include offset/limit to forward client paging requests to Prowlarr
    if params.get('offset'):
        search_kwargs['offset'] = params.get('offset')
    if params.get('limit'):
        search_kwargs['limit'] = params.get('limit')
    # NOTE: per-indexer scoping is now driven by /api/v1/indexer capabilities
    # inside search_prowlarr; client-supplied indexerIds/indexerId are no
    # longer passed through (this app presents itself as a single indexer to
    # Radarr/Sonarr).

    # If we don't have a query nor identifier, avoid calling Prowlarr which can return 400
    # However, Sonarr often performs a 'test' search only with categories (no query string).
    # Allow category-only searches to be forwarded to Prowlarr so tools like
    # Sonarr can test the indexer and receive results (or an explicit empty result set from
    # Prowlarr). Additionally, if an optional fallback query is configured via
    # `PACHELARR_TEST_FALLBACK_QUERY`, use it for category-only requests so Sonarr's test
    # returns sample results.
    if not query and not search_kwargs.get('categories') and not has_identifier:
        logger.info('No query nor identifier nor categories present for search; returning empty feed to avoid Prowlarr 400')
        return Response(content=create_empty_rss(), media_type="application/xml")
    # If we don't have a query but categories were provided,
    # this is likely a category-only call (Sonarr test). If a fallback is
    # configured, substitute it as the query and log the behavior.
    # Don't apply fallback if we have identifiers (imdbid, tvdbid, etc.)
    if not query and not has_identifier and (params.get('cat') or search_kwargs.get('categories')):
        logger.info(f"Category-only search detected via raw params; substituting fallback query '{PACHELARR_TEST_FALLBACK_QUERY}' for test behavior")
    # Debugging: log fallback / query state for incoming search verification
    logger.info(f"Search debug: query={query!r} categories={search_kwargs.get('categories')!r} fallback={PACHELARR_TEST_FALLBACK_QUERY!r}")
    logger.debug(f"search_kwargs full: {search_kwargs}")

    prowlarr_results_xml = await search_prowlarr(session, search_kwargs)
    if not prowlarr_results_xml:
        return Response(content=create_empty_rss(), media_type="application/xml")

    info_hashes = extract_hashes_from_xml_pairs(prowlarr_results_xml)
    if not info_hashes:
        return Response(content=consolidate_and_emit_xml(prowlarr_results_xml, {}), media_type="application/xml")

    cached_status = await check_torbox_cache(session, info_hashes)

    uncached_seeders = {}
    if TRACKER_SCRAPE_ENABLED:
        logger.debug(f"TRACKER_SCRAPE_ENABLED is on; building tracker_map from {len(prowlarr_results_xml)} XML docs")
        tracker_map = {}
        resolved_count = 0
        unresolved_count = 0
        seen_hashes = set()
        for _indexer, xml_bytes in prowlarr_results_xml:
            if not xml_bytes:
                continue
            try:
                doc = ET.fromstring(xml_bytes)
            except Exception:
                continue
            for item in doc.iter('item'):
                ih = _infohash_from_xml_item(item)
                if not ih:
                    continue
                if ih in seen_hashes:
                    continue
                seen_hashes.add(ih)
                if cached_status.get(ih):
                    continue
                mag = _magnet_from_xml_item(item)
                if (not mag or 'tr=' not in (mag or '')):
                    try:
                        cached_mag = _magnet_cache_get(ih)
                        if cached_mag:
                            mag = cached_mag
                    except KeyError:
                        proxy = _proxy_url_from_xml_item(item)
                        if proxy:
                            resolved = await resolve_magnet_via_download(session, proxy, TRACKER_SCRAPE_TIMEOUT)
                            _magnet_cache_put(ih, resolved)
                            if resolved and 'tr=' in resolved:
                                mag = resolved
                                resolved_count += 1
                            else:
                                unresolved_count += 1
                        else:
                            unresolved_count += 1
                for tr in parse_trackers_from_magnet(mag):
                    tracker_map.setdefault(tr, []).append(ih)
        logger.debug(f"tracker_map built: entries={len(tracker_map)} magnets_resolved={resolved_count} magnets_unresolved={unresolved_count}")
        if tracker_map:
            uncached_seeders = await scrape_trackers_inverted(tracker_map)
        else:
            logger.debug("tracker_map empty; skipping scrape_trackers_inverted (no tr= in any magnet / no magnets returned by Prowlarr)")
    xml_response = consolidate_and_emit_xml(prowlarr_results_xml, cached_status, uncached_seeders)
    return Response(content=xml_response, media_type="application/xml")

async def _search_one_indexer(session, sem, base_url, headers, indexer, params):
    """Execute one per-indexer Torznab passthrough GET under the concurrency semaphore.

    Calls ``GET <PROWLARR_URL>/<indexerId>/api`` (path-scoped) and reads the
    raw Torznab XML response bytes. Returns the XML bytes, or None on any
    per-indexer error (one failing indexer never aborts the gather).

    ``params`` uses our-names (query/type/categories/ID params/limit/offset)
    and is mapped here to Torznab query params (q/t/cat repeated/IDs/same names).
    """
    idx_id = indexer.get('id') if isinstance(indexer, dict) else None
    url = urljoin(base_url, f"/{idx_id}/api")
    # Map our-names params -> Torznab query params.
    qp = {}
    if params.get('query'):
        qp['q'] = params['query']
    if params.get('type'):
        qp['t'] = params['type']
    cats = params.get('categories')
    if cats:
        qp['cat'] = list(cats)
    for id_name in _TORZNAB_ID_PARAMS:
        if params.get(id_name):
            qp[id_name] = params[id_name]
    if params.get('limit'):
        qp['limit'] = params['limit']
    if params.get('offset'):
        qp['offset'] = params['offset']
    async with sem:
        logger.debug(
            f"Prowlarr per-indexer Torznab: GET {url} indexerId={idx_id} "
            f"q={params.get('query')!r} t={params.get('type')!r} cats={params.get('categories')!r} "
            f"headers={{'X-Api-Key':'{_mask_prowlarr_key(PROWLARR_API_KEY)}'}}"
        )
        try:
            async with session.get(
                url,
                headers=headers,
                params=qp,
                timeout=aiohttp.ClientTimeout(total=PROWLARR_INDEXER_SEARCH_TIMEOUT),
            ) as response:
                response.raise_for_status()
                xml_bytes = await response.read()
                logger.debug(f"Prowlarr indexer {idx_id} returned {len(xml_bytes)} XML bytes")
                return xml_bytes
        except asyncio.TimeoutError as e:
            logger.warning(f"Prowlarr per-indexer Torznab search timed out for indexer {idx_id} after {PROWLARR_INDEXER_SEARCH_TIMEOUT}s: {e}")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"Prowlarr per-indexer Torznab search failed for indexer {idx_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Prowlarr per-indexer Torznab search error for indexer {idx_id}: {e}")
            return None


async def search_prowlarr_per_indexer(session, tasks):
    """Run per-indexer Torznab searches in parallel; return (indexer, xml_bytes) pairs.

    ``tasks`` is a list of (indexer, params) pairs (params may be None = skip;
    such tasks are dropped before dispatch). Concurrency is bounded by
    PROWLARR_PARALLEL_INDEXER_CONCURRENCY via asyncio.Semaphore. Each per-indexer
    call swallows its own errors and returns None so one failing indexer never
    aborts the others. Failed/None results are dropped; the returned list keeps
    only (indexer, xml_bytes) pairs that succeeded, in stable order.
    """
    live = [(idx, p) for idx, p in tasks if p is not None]
    if not live:
        logger.debug("search_prowlarr_per_indexer: no live indexer tasks; returning []")
        return []
    base_url = PROWLARR_URL
    headers = {"X-Api-Key": PROWLARR_API_KEY}
    sem = asyncio.Semaphore(max(PROWLARR_PARALLEL_INDEXER_CONCURRENCY, 1))
    coros = [_search_one_indexer(session, sem, base_url, headers, idx, p) for idx, p in live]
    batches = await asyncio.gather(*coros, return_exceptions=False)
    out = [(idx, xml) for (idx, _p), xml in zip(live, batches) if xml is not None]
    logger.info(
        f"search_prowlarr_per_indexer: {len(live)} indexers -> {len(out)} successful XML docs"
    )
    return out


async def search_prowlarr(session, search_kwargs):
    """Search Prowlarr per-indexer (capability-driven) and return Torznab XML pairs.

    Thin wrapper that:
      1. fetches the cached indexer list (get_prowlarr_indexers_cached),
      2. selects indexers eligible for this query (select_indexers_for_query),
      3. builds per-indexer params filtered by each indexer's capabilities
         (build_per_indexer_params), and
      4. runs the per-indexer ``/<indexerId>/api`` Torznab GETs in parallel
         (search_prowlarr_per_indexer).

    Returns ``[(indexer_dict, xml_bytes), ...]`` (only successful indexers).
    Returns ``[]`` when there are no indexers or no eligible tasks.
    """
    try:
        indexers = await get_prowlarr_indexers_cached(session)
    except aiohttp.ClientError as e:
        logger.warning(f"search_prowlarr: indexer fetch failed: {e}; returning []")
        return []
    if not indexers:
        logger.debug("search_prowlarr: no indexers available; returning []")
        return []
    search_type = search_kwargs.get('type', 'search')
    categories = search_kwargs.get('categories') or []
    has_ids = any(search_kwargs.get(k) for k in ('tvdbid', 'imdbid', 'tmdbid', 'season', 'ep'))
    selected = select_indexers_for_query(indexers, search_type, categories, has_ids)
    tasks = []
    for idx in selected:
        params = build_per_indexer_params(idx, search_kwargs)
        if params is not None:
            tasks.append((idx, params))
    if not tasks:
        logger.debug("search_prowlarr: no eligible indexer tasks after capability filtering; returning []")
        return []
    return await search_prowlarr_per_indexer(session, tasks)

_TORZNAB_NS = 'http://torznab.com/schemas/2015/feed'


def _xml_attr(item, name):
    """Return the value attribute of the first <torznab:attr name=name> on item, or None."""
    for el in item.iter(f'{{{_TORZNAB_NS}}}attr'):
        if el.get('name') == name:
            return el.get('value')
    return None


def _set_xml_attr(item, name, value):
    """Set the value attribute of the first <torznab:attr name=name>; create if missing."""
    for el in item.iter(f'{{{_TORZNAB_NS}}}attr'):
        if el.get('name') == name:
            el.set('value', value)
            return
    ET.SubElement(item, f'{{{_TORZNAB_NS}}}attr', name=name, value=value)


def _magnet_from_xml_item(item):
    """Return the first magnet:? URI found in <link>/<guid>/<enclosure url> of an XML item."""
    for tag in ('link', 'guid'):
        el = item.find(tag)
        if el is not None and el.text and 'magnet:?' in el.text:
            return el.text
    enc = item.find('enclosure')
    if enc is not None:
        u = enc.get('url')
        if u and 'magnet:?' in u:
            return u
    return None


def _proxy_url_from_xml_item(item):
    """Return the first http(s):// URL in <link>/<enclosure url> (Prowlarr proxy download URL)."""
    for tag in ('link',):
        el = item.find(tag)
        if el is not None and el.text and el.text.startswith(('http://', 'https://')):
            return el.text
    enc = item.find('enclosure')
    if enc is not None:
        u = enc.get('url')
        if u and u.startswith(('http://', 'https://')):
            return u
    return None


def _infohash_from_xml_item(item):
    """Return a lowercased infohash from a <torznab:attr name=infohash> else parse xt=urn:btih:."""
    ih = _xml_attr(item, 'infohash')
    if ih:
        return ih.lower()
    mag = _magnet_from_xml_item(item)
    if mag:
        try:
            parsed_magnet = parse_qs(unquote(mag.split('?')[1]))
            if 'xt' in parsed_magnet:
                return parsed_magnet['xt'][0].split(':')[-1].lower()
        except Exception:
            return None
    return None


def extract_hashes_from_xml_pairs(indexer_xml_pairs):
    """Return unique lowercased infohashes in first-seen order across XML pairs.

    Parses each (indexer, xml_bytes) doc's <item> elements and extracts hashes
    via <torznab:attr name=infohash> or xt=urn:btih: from the magnet. Used by
    handle_search to feed check_torbox_cache BEFORE consolidation.
    """
    raw_hashes = []
    for _indexer, xml_bytes in (indexer_xml_pairs or []):
        if not xml_bytes:
            continue
        try:
            doc = ET.fromstring(xml_bytes)
        except Exception:
            continue
        for item in doc.iter('item'):
            ih = _infohash_from_xml_item(item)
            if ih:
                raw_hashes.append(ih)
    return dedupe_hashes_preserve_order(raw_hashes)


def parse_trackers_from_magnet(magnet_uri):
    """Extract tracker URLs from a magnet URI (tr= parameters)."""
    if not magnet_uri:
        return []
    try:
        query = magnet_uri.split('?')[1]
    except Exception:
        return []
    # split by & and look for tr= entries
    trackers = []
    for part in query.split('&'):
        if part.startswith('tr='):
            val = part.split('=', 1)[1]
            trackers.append(unquote(val))
    # normalize and dedupe while preserving order
    out = []
    seen = set()
    for t in trackers:
        t_str = t.strip()
        if not t_str:
            continue
        if t_str not in seen:
            out.append(t_str)
            seen.add(t_str)
    return out


def _get_magnet_uri_for_item(item):
    """Return a magnet URI string from item, trying 'magnetUri' then 'guid'.

    This ensures we parse trackers even when Prowlarr returns magnet in 'guid'.
    """
    if not item:
        return None
    if item.get('magnetUri'):
        return item.get('magnetUri')
    g = item.get('guid')
    if isinstance(g, str) and 'magnet:?' in g:
        return g
    # `enclosure` may be a dict with 'url' or a string; handle both
    enc = item.get('enclosure')
    if isinstance(enc, dict) and isinstance(enc.get('url'), str) and 'magnet:?' in enc.get('url'):
        return enc.get('url')
    if isinstance(enc, str) and 'magnet:?' in enc:
        return enc
    return None


def infohash_from_item(item):
    """Return a lowercase infohash for a dict item, trying 'infoHash' then magnet parsing.

    Dict-based helper kept for compatibility; the XML flow uses
    _infohash_from_xml_item. Returns None if no hash can be found.
    """
    info = item.get('infoHash') if item else None
    if info:
        try:
            return info.lower()
        except Exception:
            return info
    mag = _get_magnet_uri_for_item(item)
    if mag:
        try:
            parsed_magnet = parse_qs(unquote(mag.split('?')[1]))
            if 'xt' in parsed_magnet:
                return parsed_magnet['xt'][0].split(':')[-1].lower()
        except Exception:
            return None
    return None


def _normalize_pubdate(raw):
    """Parse a raw pubDate string to an RFC1123-formatted GMT string.

    Handles Prowlarr's ISO8601 (e.g. 2025-05-10T16:57:09Z) and other ISO variants;
    falls back to now(UTC) on failure/empty.
    """
    if raw:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.strftime('%a, %d %b %Y %H:%M:%S GMT')


def consolidate_and_emit_xml(indexer_xml_pairs, cached_status, uncached_seeders=None):
    """Parse per-indexer Torznab XML, consolidate by infohash, and emit one merged RSS.

    ``indexer_xml_pairs`` is ``[(indexer_dict, xml_bytes), ...]``. Parses each
    doc with lxml, collects all <item> elements, groups by lowercased infohash,
    merges tr= trackers across the group, picks the canonical item (highest
    original seeders), mutates the canonical <item> node in place (title/guid/
    link/enclosure/seeders/peers attrs/pubDate), and appends the actual parsed
    <item> nodes into a new <rss><channel>. Non-hash items are emitted unchanged.
    Returns the serialized XML bytes.

    Because the appended items are the original parsed lxml nodes, every
    <torznab:attr>/child we do NOT mutate (size, category, downloadvolumefactor,
    grabs, etc.) passes through automatically.
    """
    cached_status = {k.lower(): v for k, v in (cached_status or {}).items()}
    uncached_seeders = uncached_seeders or {}

    # Collect (item_element, infohash_lower_or_None, magnet, proxy_url) records.
    records = []
    non_hash_items = []
    for _indexer, xml_bytes in (indexer_xml_pairs or []):
        if not xml_bytes:
            continue
        try:
            doc = ET.fromstring(xml_bytes)
        except Exception as e:
            logger.warning(f"consolidate_and_emit_xml: skipping malformed XML doc: {e}")
            continue
        for item in doc.iter('item'):
            ih = _infohash_from_xml_item(item)
            mag = _magnet_from_xml_item(item)
            proxy = _proxy_url_from_xml_item(item)
            if ih:
                records.append((item, ih, mag, proxy))
            else:
                non_hash_items.append(item)

    # Group by lowercased infohash, preserving first-seen order.
    groups = {}
    order = []
    for (item, ih, mag, proxy) in records:
        if ih not in groups:
            groups[ih] = []
            order.append(ih)
        groups[ih].append((item, mag, proxy))

    rss = ET.Element("rss", version="2.0", nsmap={'torznab': _TORZNAB_NS})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Torbox Cached Indexer"

    emitted = set()
    for ih in order:
        if ih in emitted:
            continue
        emitted.add(ih)
        group = groups[ih]
        # Canonical = max original seeders.
        def _orig_seeders(rec):
            v = _xml_attr(rec[0], 'seeders')
            try:
                return int(v) if v is not None else 0
            except Exception:
                return 0
        canonical_rec = max(group, key=_orig_seeders)
        canonical_item = canonical_rec[0]
        had_magnet = canonical_rec[1] is not None

        # Merge tr= trackers from EVERY item's magnet in the group (union, order-preserved).
        trackers = []
        seen = set()
        for (item, mag, _proxy) in group:
            for tr in parse_trackers_from_magnet(mag):
                if tr not in seen:
                    seen.add(tr)
                    trackers.append(tr)

        # Build canonical magnet.
        base = None
        base_mag = canonical_rec[1]
        if base_mag and 'magnet:?' in base_mag:
            try:
                parsed = parse_qs(unquote(base_mag.split('?', 1)[1]))
                if 'xt' in parsed:
                    base = f"magnet:?xt={parsed['xt'][0]}"
            except Exception:
                base = None
        if not base:
            base = f"magnet:?xt=urn:btih:{ih}"
        tr_parts = '&'.join('tr=' + t for t in trackers)
        if tr_parts:
            connector = '&' if '?' in base else '?'
            canonical_magnet = f"{base}{connector}{tr_parts}"
        else:
            canonical_magnet = base

        # Mutate canonical <item> nodes.
        guid_el = canonical_item.find('guid')
        if guid_el is None:
            guid_el = ET.SubElement(canonical_item, 'guid')
        guid_el.text = canonical_magnet
        if had_magnet:
            link_el = canonical_item.find('link')
            if link_el is None:
                link_el = ET.SubElement(canonical_item, 'link')
            link_el.text = canonical_magnet

        is_cached = ih in cached_status
        title_el = canonical_item.find('title')
        if is_cached:
            if title_el is not None:
                cur = (title_el.text or '').lstrip()
                if not cur.startswith('[CACHED] '):
                    title_el.text = f"[CACHED] {cur}"

        # seeders/peers attrs.
        orig_seeders = _orig_seeders(canonical_rec)
        try:
            v = _xml_attr(canonical_item, 'peers')
            orig_leechers = int(v) if v is not None else 0
        except Exception:
            orig_leechers = 0
        if is_cached:
            _set_xml_attr(canonical_item, 'seeders', str(max(orig_seeders, PACHELARR_SEEDERS_BOOST)))
        else:
            entry = uncached_seeders.get(ih) or {}
            try:
                scrape_seeders = int(entry.get('seeders', 0) or 0)
            except Exception:
                scrape_seeders = 0
            try:
                scrape_leechers = int(entry.get('leechers', 0) or 0)
            except Exception:
                scrape_leechers = 0
            if scrape_seeders or scrape_leechers:
                _set_xml_attr(canonical_item, 'seeders', str(max(orig_seeders, scrape_seeders)))
                _set_xml_attr(canonical_item, 'peers', str(max(orig_leechers, scrape_leechers)))

        # Ensure <enclosure url> populated: prefer canonical magnet, else proxy_url, else guid.
        enc_el = canonical_item.find('enclosure')
        enc_url = canonical_magnet or canonical_rec[2] or guid_el.text
        if enc_el is None:
            ET.SubElement(canonical_item, 'enclosure', url=enc_url or '', type='application/x-bittorrent')
        else:
            if not enc_el.get('url'):
                enc_el.set('url', enc_url or '')

        # Normalize <pubDate> to RFC1123; create if absent.
        pub_el = canonical_item.find('pubDate')
        if pub_el is None:
            pub_el = ET.SubElement(canonical_item, 'pubDate')
        pub_el.text = _normalize_pubdate(pub_el.text)

        logger.debug(f'consolidate_and_emit_xml: infohash={ih} cached={is_cached} trackers={len(trackers)}')
        channel.append(canonical_item)

    # Append non-hash items unchanged.
    for item in non_hash_items:
        channel.append(item)

    return ET.tostring(rss, pretty_print=True, xml_declaration=True, encoding='UTF-8')


# Module-level scrape result cache: {lowercased_infohash: {'seeders','leechers','expires'}}
# OrderedDict so GET/PUT can move_to_end and eviction (popitem(last=False)) is true LRU.
_SCRAPE_CACHE = OrderedDict()

# Module-level title->ID lookup cache:
# {(title_lower, year, search_type): {'ids': {...}, 'expires': float}}
# OrderedDict for true LRU eviction (popitem(last=False)).
_TMDB_TITLE_CACHE = OrderedDict()

# Module-level magnet-resolution cache: {lowercased_infohash: magnet_uri_or_None}
# Populated when Prowlarr's JSON API omits the magnet and we resolve it via the
# downloadUrl proxy. None is cached too (negative cache) to avoid refetching.
# OrderedDict for true LRU eviction (popitem(last=False)).
_MAGNET_CACHE = OrderedDict()
_MAGNET_CACHE_MAX = TRACKER_SCRAPE_CACHE_MAX


def _magnet_cache_get(h):
    """Return cached magnet (or None sentinel) for hash. Raises KeyError if absent.

    On a hit the entry is moved to the end (most-recently-used) for true LRU.
    There is no TTL/expiry on this cache; only LRU + KeyError-on-miss.
    """
    if not h:
        raise KeyError(h)
    key = h.lower() if isinstance(h, str) else h
    val = _MAGNET_CACHE[key]
    _MAGNET_CACHE.move_to_end(key)
    return val


def _magnet_cache_put(h, magnet):
    """Insert magnet (may be None) for hash with true LRU bound.

    move_to_end on insert keeps this key most-recently-used; eviction drops the
    least-recently-used (first) entry via popitem(last=False).
    """
    if not h:
        return
    key = h.lower() if isinstance(h, str) else h
    _MAGNET_CACHE[key] = magnet
    _MAGNET_CACHE.move_to_end(key)
    while len(_MAGNET_CACHE) > _MAGNET_CACHE_MAX:
        try:
            _MAGNET_CACHE.popitem(last=False)
        except Exception:
            break


async def resolve_magnet_via_download(session, download_url, timeout=5.0):
    """Resolve a real magnet URI by following the Prowlarr downloadUrl proxy.

    Prowlarr's JSON /api/v1/search may omit the magnet for some indexers and
    instead return a self-proxy download URL. When the source enclosure is a
    magnet, fetching that proxy URL yields a redirect to the magnet URI (or
    serves it in the response body / Location header). We capture the magnet
    without trying to follow the non-http 'magnet:' scheme.

    Returns the magnet URI string, or None if it cannot be resolved.
    """
    if not download_url:
        return None
    try:
        # Don't auto-follow: we want to inspect the Location header ourselves,
        # because the target may be a 'magnet:' URL which aiohttp cannot follow.
        async with session.get(
            download_url,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={"X-Api-Key": PROWLARR_API_KEY} if download_url.startswith(str(PROWLARR_URL or '')) else {},
        ) as resp:
            loc = resp.headers.get('Location') or resp.headers.get('location')
            if loc and 'magnet:?' in loc:
                return loc
            # Some proxies 200 with the magnet as the body (text/uri-list or plain).
            if resp.status == 200:
                text = await resp.text()
                if text and 'magnet:?' in text:
                    idx = text.find('magnet:')
                    if idx >= 0:
                        # Take until whitespace/end
                        end = len(text)
                        for i, ch in enumerate(text[idx:], start=idx):
                            if ch in ' \t\r\n<':
                                end = i
                                break
                        return text[idx:end]
            # Follow one http(s) hop if it's a normal 3xx to another http URL.
            if resp.status in (301, 302, 303, 307, 308) and loc and loc.startswith('http'):
                return await resolve_magnet_via_download(session, loc, timeout)
    except Exception as e:
        logger.debug(f"resolve_magnet_via_download error for {download_url[:80]}: {e}")
    return None


def _scrape_cache_get(h):
    """Return cached scrape entry for hash if present and unexpired, else None.

    On a live hit the entry is moved to the end (most-recently-used) for true
    LRU. Expired-but-present entries are left in place (returned as None) so a
    stale entry can still serve as a last-good fallback on a later fetch error.
    """
    if not h:
        return None
    key = h.lower() if isinstance(h, str) else h
    entry = _SCRAPE_CACHE.get(key)
    if entry is None:
        return None
    if entry.get('expires', 0) <= time.time():
        return None
    _SCRAPE_CACHE.move_to_end(key)
    return entry


def _scrape_cache_put(h, entry):
    """Insert/refresh a scrape cache entry for hash with TTL-based expiry.

    move_to_end on insert keeps this key most-recently-used; eviction drops the
    least-recently-used (first) entry via popitem(last=False) for true LRU.
    """
    if not h or not entry:
        return
    key = h.lower() if isinstance(h, str) else h
    stored = dict(entry)
    stored['expires'] = time.time() + TRACKER_SCRAPE_CACHE_TTL
    _SCRAPE_CACHE[key] = stored
    _SCRAPE_CACHE.move_to_end(key)
    while len(_SCRAPE_CACHE) > TRACKER_SCRAPE_CACHE_MAX:
        try:
            _SCRAPE_CACHE.popitem(last=False)
        except Exception:
            break


def _tmdb_title_cache_get(key):
    """Return cached title->ID ids for key if present and unexpired, else None.

    On a live hit the entry is moved to the end (most-recently-used) for true
    LRU. Expired-but-present entries are left in place (returned as None).
    """
    if not key:
        return None
    entry = _TMDB_TITLE_CACHE.get(key)
    if entry is None:
        return None
    if entry.get('expires', 0) <= time.time():
        return None
    _TMDB_TITLE_CACHE.move_to_end(key)
    return entry.get('ids')


def _tmdb_title_cache_put(key, ids):
    """Insert a title->ID cache entry for key with TTL-based expiry.

    move_to_end on insert keeps this key most-recently-used; eviction drops the
    least-recently-used (first) entry via popitem(last=False) for true LRU.
    """
    if not key or not ids:
        return
    _TMDB_TITLE_CACHE[key] = {'ids': dict(ids), 'expires': time.time() + TMDB_TITLE_LOOKUP_CACHE_TTL}
    _TMDB_TITLE_CACHE.move_to_end(key)
    while len(_TMDB_TITLE_CACHE) > TMDB_TITLE_LOOKUP_CACHE_MAX:
        try:
            _TMDB_TITLE_CACHE.popitem(last=False)
        except Exception:
            break


# Module-level Prowlarr indexer-listing cache.
# Stores the full IndexerResource[] list (so capability-driven per-indexer
# search can read each indexer's searchParams/categories). Format:
#   {'indexers': [...], 'expires': float}
# A single listing object holds the entire indexer list under the fixed
# 'listing' key; PROWLARR_INDEXERS_CACHE_MAX bounds how many cached listings we
# keep (default 1). OrderedDict so eviction (popitem(last=False)) is true LRU.
_INDEXERS_CACHE = OrderedDict()


def _indexers_cache_get():
    """Return the cached indexer list if present and unexpired, else None.

    On a live hit the 'listing' entry is moved to the end (most-recently-used)
    for true LRU. Expired-but-present entries are left in place (returned as
    None) so a stale listing can still serve as a last-good fallback on a later
    fetch error (see get_prowlarr_indexers_cached).
    """
    entry = _INDEXERS_CACHE.get('listing')
    if entry is None:
        return None
    if entry.get('expires', 0) <= time.time():
        return None
    _INDEXERS_CACHE.move_to_end('listing')
    return entry.get('indexers')


def _indexers_cache_put(indexers):
    """Store the full indexer list under a single 'listing' key with TTL expiry.

    move_to_end('listing') on insert keeps it most-recently-used. Bounded by
    PROWLARR_INDEXERS_CACHE_MAX (least-recently-used extra keys evicted via
    popitem(last=False) when the bound is exceeded).
    """
    if indexers is None:
        return
    _INDEXERS_CACHE['listing'] = {'indexers': list(indexers), 'expires': time.time() + PROWLARR_INDEXERS_CACHE_TTL}
    _INDEXERS_CACHE.move_to_end('listing')
    # Single-listing model by default; evict extra keys if the bound is tightened.
    while len(_INDEXERS_CACHE) > max(PROWLARR_INDEXERS_CACHE_MAX, 1):
        try:
            _INDEXERS_CACHE.popitem(last=False)
        except Exception:
            break


def _parse_tracker_host_port(tracker_url):
    """Return (host, port) for a tracker URL. Only supports udp:// and returns default port if missing."""
    try:
        from urllib.parse import urlparse
        p = urlparse(tracker_url)
        hostname = p.hostname
        port = p.port
        if not hostname:
            return None
        port = port or 6969
        return hostname, port
    except Exception:
        return None


async def _resolve_udp_addr(host, port, loop=None):
    """Resolve host:port to a deduped list of (ip, port) tuples suitable for UDP.

    Returns [] on resolution failure. The caller falls back to (host, port) directly.
    """
    if not host:
        return []
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except Exception as e:
        logger.debug(f"_resolve_udp_addr: getaddrinfo failed for {host}:{port}: {e}")
        return []
    seen = set()
    out = []
    for addr in infos:
        try:
            ip, p = addr[4][0], addr[4][1]
        except Exception:
            continue
        key = (ip, p)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


async def _udp_scrape_tracker(host, port, chunks, timeout):
    """Scrape all chunks for one tracker over a single reused UDP connection.

    `chunks` is a list of hash-lists (caller splits hashes into TRACKER_SCRAPE_BATCH_SIZE
    chunks). Resolves host to candidate addresses and tries each until one connects;
    for the first working address it performs one BEP-15 connect handshake and then
    sends one scrape request per chunk reusing the same conn_id and transport.

    Returns {hash_hex: {'seeders':int, 'leechers':int, 'downloads':int}}.
    """
    import random
    import struct

    class _ScrapeProto(asyncio.DatagramProtocol):
        def __init__(self):
            self.transport = None
            self.fut = None
        def connection_made(self, transport):
            self.transport = transport
        def datagram_received(self, data, addr):
            if self.fut is not None and not self.fut.done():
                self.fut.set_result(data)
        def error_received(self, exc):
            if self.fut is not None and not self.fut.done():
                self.fut.set_exception(exc)
        def connection_lost(self, exc):
            if self.fut is not None and not self.fut.done():
                self.fut.set_exception(exc or ConnectionError("transport closed"))
        def reset_future(self):
            self.fut = asyncio.get_event_loop().create_future()
            return self.fut

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    addrs = await _resolve_udp_addr(host, port, loop)
    if not addrs:
        addrs = [(host, port)]

    aggregate = {}
    for (ip, p) in addrs:
        proto = _ScrapeProto()
        try:
            transport, _ = await loop.create_datagram_endpoint(lambda: proto, remote_addr=(ip, p))
        except Exception as e:
            logger.debug(f"_udp_scrape_tracker: connect failed to {ip}:{p} ({host}:{port}): {e}")
            continue
        try:
            # BEP-15 connect handshake
            fut = proto.reset_future()
            trans_id = random.randrange(0, 1 << 31)
            conn_req = struct.pack('!QII', 0x41727101980, 0, trans_id)
            transport.sendto(conn_req)
            try:
                data = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                continue
            if len(data) < 16:
                continue
            action, trans, conn_id = struct.unpack('!IIQ', data[:16])
            if action != 0 or trans != trans_id:
                continue

            connected_ok = False
            for chunk in chunks:
                if not chunk:
                    continue
                try:
                    fut = proto.reset_future()
                    trans_id2 = random.randrange(0, 1 << 31)
                    payload = struct.pack('!QII', conn_id, 2, trans_id2)
                    valid_hashes = []
                    for h in chunk:
                        try:
                            payload += bytes.fromhex(h)
                            valid_hashes.append(h)
                        except Exception:
                            continue
                    transport.sendto(payload)
                    try:
                        data = await asyncio.wait_for(fut, timeout=timeout)
                    except asyncio.TimeoutError:
                        # reconnect on timeout
                        break
                    if len(data) < 8:
                        continue
                    action, trans = struct.unpack('!II', data[:8])
                    if action != 2:
                        continue
                    connected_ok = True
                    data_body = data[8:]
                    rec_count = len(data_body) // 12
                    if rec_count != len(valid_hashes):
                        logger.warning(f"_udp_scrape_tracker: record count {rec_count} != requested {len(valid_hashes)} for {host}:{port}; mapping positionally anyway")
                    for i in range(0, len(data_body), 12):
                        rec = data_body[i:i+12]
                        if len(rec) < 12:
                            break
                        seeders, leechers, downloads = struct.unpack('!III', rec)
                        idx = i // 12
                        if idx < len(valid_hashes):
                            aggregate[valid_hashes[idx]] = {'seeders': seeders, 'leechers': leechers, 'downloads': downloads}
                except Exception as e:
                    logger.debug(f"_udp_scrape_tracker: per-chunk error for {host}:{port}: {e}", exc_info=True)
                    break
            if connected_ok:
                return aggregate
        finally:
            try:
                transport.close()
            except Exception:
                pass
    return aggregate


async def scrape_trackers_inverted(tracker_to_hashes):
    """Given mapping tracker_url -> list of infohash hex strings, perform inverted scraping and
    return mapping infohash -> {'seeders':int, 'leechers':int} aggregated as per-metric max
    across trackers. Uses a bounded result cache keyed by lowercased infohash.
    """
    # Only implement UDP scrape for 'udp://' trackers, ignore others for now
    sem = asyncio.Semaphore(TRACKER_SCRAPE_CONCURRENCY)
    logger.debug(f"scrape_trackers_inverted: trackers={len(tracker_to_hashes)} concurrency={TRACKER_SCRAPE_CONCURRENCY} batch_size={TRACKER_SCRAPE_BATCH_SIZE} timeout={TRACKER_SCRAPE_TIMEOUT}")
    results_per_hash = {}
    # Cache entries to persist; applied AFTER all trackers complete so an in-flight
    # cross-tracker race cannot short-circuit aggregation within a single call.
    cache_puts = []

    def _aggregate(h, entry):
        cur = results_per_hash.get(h)
        if cur is None:
            results_per_hash[h] = {'seeders': entry.get('seeders', 0), 'leechers': entry.get('leechers', 0)}
        else:
            if entry.get('seeders', 0) > cur.get('seeders', 0):
                cur['seeders'] = entry.get('seeders', 0)
            if entry.get('leechers', 0) > cur.get('leechers', 0):
                cur['leechers'] = entry.get('leechers', 0)

    async def _process_tracker(url, hashes):
        if not url or not url.lower().startswith('udp://'):
            return
        hostport = _parse_tracker_host_port(url)
        if not hostport:
            return
        host, port = hostport
        # Partition hashes into cached vs uncached via the scrape cache.
        uncached = []
        for h in hashes:
            cached_entry = _scrape_cache_get(h)
            if cached_entry is not None:
                _aggregate(h, cached_entry)
            else:
                uncached.append(h)
        if not uncached:
            return
        # chunk hashes per TRACKER_SCRAPE_BATCH_SIZE
        chunks = [uncached[i:i+TRACKER_SCRAPE_BATCH_SIZE] for i in range(0, len(uncached), TRACKER_SCRAPE_BATCH_SIZE)]
        async with sem:
            try:
                res = await _udp_scrape_tracker(host, port, chunks, TRACKER_SCRAPE_TIMEOUT)
            except Exception:
                res = {}
        for h, entry in res.items():
            _aggregate(h, entry)
            cache_puts.append((h, entry))

    tasks = [asyncio.create_task(_process_tracker(url, hashes)) for url, hashes in tracker_to_hashes.items()]
    if tasks:
        await asyncio.gather(*tasks)
    # Persist scraped results to the cache only after aggregation is complete.
    for h, entry in cache_puts:
        _scrape_cache_put(h, entry)
    return results_per_hash


async def check_torbox_cache(session, hashes):
    """Checks Torbox cache for a list of info hashes."""
    global torbox_hits, torbox_misses
    try:
        headers = {
            "Content-Type": "application/json",
            # Torbox expects Bearer token authentication
            "Authorization": f"Bearer {TORBOX_API_KEY}"
        }
        # Mask Torbox API key for debug logging
        def _mask_key(k):
            if not k:
                return None
            if len(k) <= 8:
                return "****"
            return k[:4] + "*" * (len(k) - 8) + k[-4:]
        # if no hashes, skip the call
        if not hashes:
            return {}

        total_hashes = len(hashes)
        # dedupe hashes (case-insensitively) while preserving ordering
        unique_hashes = dedupe_hashes_preserve_order(hashes)
        dedupe_removed_count = total_hashes - len(unique_hashes)
        if dedupe_removed_count:
            logger.debug(f"Torbox cache check: dedupe_removed={dedupe_removed_count}")
        logger.debug(
            f"Torbox cache check: POST {TORBOX_CHECK_URL} total.hashes={total_hashes} unique.hashes={len(unique_hashes)} dedupe_removed={dedupe_removed_count} Authorization=Bearer {_mask_key(TORBOX_API_KEY)}"
        )

        # Helper to combine and normalize results to lowercase keys
        combined = {}
        total_hits = 0

        async def _call_chunk(chunk):
            """Call Torbox for given chunk, return mapping or raise.
            Handles 401 specially by returning None to indicate bail-out.
            """
            attempt = 1
            backoff = TORBOX_RETRY_BACKOFF
            while attempt <= TORBOX_MAX_RETRIES:
                try:
                    async with session.post(TORBOX_CHECK_URL, json={'hashes': chunk}, headers=headers) as response:
                        if response.status == 401:
                            logger.warning("Torbox returned 401 Unauthorized. Check TORBOX_API_KEY. Aborting cache checks.")
                            return None
                        if response.status >= 500:
                            logger.warning(f"Torbox server error (status {response.status}); attempt {attempt}/{TORBOX_MAX_RETRIES}")
                            # fall through to retry logic
                        else:
                            response.raise_for_status()
                            data = await response.json()
                            return data
                except aiohttp.ClientError as e:
                    logger.warning(f"Torbox request error: {e}; attempt {attempt}/{TORBOX_MAX_RETRIES}")
                # If not returned, sleep then retry
                await asyncio.sleep(backoff)
                backoff *= 2
                attempt += 1
            # After retries exhausted
            logger.warning("Torbox cache check failed after retries for chunk")
            return {}

        # Batch hashes and query Torbox for each chunk
        # use unique_hashes for chunking
        for i in range(0, len(unique_hashes), TORBOX_CHUNK_SIZE):
            chunk = unique_hashes[i:i+TORBOX_CHUNK_SIZE]
            logger.debug(f"Torbox cache chunk: POST {TORBOX_CHECK_URL} chunk.len={len(chunk)} Authorization=Bearer {_mask_key(TORBOX_API_KEY)}")
            try:
                result = await _call_chunk(chunk)
                if result is None:
                    # 401 or non-retriable error; abort and return empty map
                    return {}
                if isinstance(result, dict):
                    # First, try the common {'data': {...}} mapping
                    if 'data' in result:
                        data_map = result['data']
                        if isinstance(data_map, dict):
                            hits = len(data_map)
                            logger.debug(f"Torbox chunk response: hits={hits}")
                            total_hits += hits
                            for k, v in data_map.items():
                                combined[k.lower()] = v
                        elif isinstance(data_map, list):
                            hits = len(data_map)
                            logger.debug(f"Torbox chunk response list: hits={hits}")
                            total_hits += hits
                            for obj in data_map:
                                if isinstance(obj, dict) and obj.get('hash'):
                                    combined[obj['hash'].lower()] = obj
                    else:
                        # result may be directly a mapping
                        hits = len(result)
                        logger.debug(f"Torbox chunk response (mapping): hits={hits}")
                        total_hits += hits
                        for k, v in result.items():
                            combined[k.lower()] = v
                elif isinstance(result, list):
                    # Torbox may return a list of objects [{hash:..., ...}, ...]
                    hits = len(result)
                    logger.debug(f"Torbox chunk response list (top-level): hits={hits}")
                    total_hits += hits
                    for obj in result:
                        if isinstance(obj, dict) and obj.get('hash'):
                            combined[obj['hash'].lower()] = obj
                else:
                    logger.debug(f"Unexpected Torbox chunk response data type: {type(result)}")
            except Exception as e:
                logger.exception(f"Error processing Torbox chunk: {e}")
                # continue to next chunk
                continue
        logger.info(f"Torbox cache check: total cached hits={total_hits}")
        hits_n = len(combined)
        torbox_hits += hits_n
        torbox_misses += max(len(unique_hashes) - hits_n, 0)
        return combined
    except aiohttp.ClientError as e:
        logger.exception(f"Error checking Torbox cache: {e}")
        return {}


def get_caps_xml():
    """Returns the static capabilities XML for Torznab."""
    return """
<caps>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,tvdbid,imdbid,tmdbid"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
  </searching>
  <categories>
    <category id="2000" name="Movies"/>
    <category id="5000" name="TV"/>
  </categories>
</caps>
""".strip()

def create_empty_rss():
    """Creates an empty RSS feed for when there are no results."""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Torbox Cached Indexer"
    return ET.tostring(rss, pretty_print=True, xml_declaration=True, encoding='UTF-8')

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PACHELARR_PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)