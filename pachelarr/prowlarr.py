import asyncio
import logging
import time
from urllib.parse import urljoin

import aiohttp

from pachelarr import db, settings, state

logger = logging.getLogger("pachelarr")


def _mask_prowlarr_key(k):
    """Mask a Prowlarr API key for safe debug logging."""
    if not k:
        return None
    if len(k) <= 8:
        return "****"
    return k[:4] + "*" * (len(k) - 8) + k[-4:]


def _normalize_indexer_list(raw):
    """Normalize a Prowlarr /api/v1/indexer response to a list of indexer dicts."""
    indexers = []
    if isinstance(raw, list):
        indexers = raw
    elif isinstance(raw, dict):
        for key in ('records', 'results', 'indexers', 'items', 'data'):
            if key in raw and isinstance(raw[key], list):
                indexers = raw[key]
                break
        if not indexers:
            if all(isinstance(v, dict) for v in raw.values()):
                indexers = [v for v in raw.values()]
            else:
                indexers = [raw]
    return indexers


def _indexer_is_enabled(idx):
    """Return True if an indexer dict should be considered enabled."""
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
    """Fetch the full list of Prowlarr indexers (IndexerResource[]) with caching."""
    cached = _indexers_cache_get()
    if cached is not None:
        logger.debug(f"Prowlarr indexers cache hit: {len(cached)} indexers")
        return cached
    prowlarr_url = settings.get_str("PROWLARR_URL")
    prowlarr_api_key = settings.get_str("PROWLARR_API_KEY")
    try:
        url = urljoin(prowlarr_url, "/api/v1/indexer")
        headers = {"X-Api-Key": prowlarr_api_key}
        logger.debug(
            f"Prowlarr indexers request: GET {url} headers={{'X-Api-Key': '{_mask_prowlarr_key(prowlarr_api_key)}'}}"  # noqa: E501
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
        last_good = state._INDEXERS_CACHE.get('listing')
        if last_good is not None:
            stale = last_good.get('indexers', [])
            logger.warning(f"Prowlarr indexers: serving {len(stale)} stale cached indexers after fetch error")
            return stale
        logger.warning("Prowlarr indexers fetch failed and no cache available; returning empty list")
        return []


async def get_all_prowlarr_indexers(session):
    """Backward-compat shim: return enabled indexer IDs as ints."""
    indexers = await get_prowlarr_indexers_cached(session)
    return [idx.get('id') for idx in indexers if _indexer_is_enabled(idx)]


def _collect_indexer_category_ids(categories):
    """Flatten an indexer's capabilities.categories tree into a set of ids."""
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
    """Select indexers eligible to answer this query."""
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
    """Return the set of OUR param names an indexer supports for a search type."""
    caps = (indexer.get('capabilities') or {}) if isinstance(indexer, dict) else {}
    field = state._SEARCH_PARAMS_FIELD.get(search_type)
    supported_ours = {'q'}
    if field:
        enums = caps.get(field) or []
        if isinstance(enums, list):
            for e in enums:
                if not isinstance(e, str):
                    continue
                ours = state._PROWLARR_ENUM_TO_OUR_NAME.get(e)
                if ours:
                    supported_ours.add(ours)
                elif e and e.islower():
                    supported_ours.add(e)
    return supported_ours


def build_per_indexer_params(indexer, search_kwargs):
    """Build per-indexer Torznab GET params, filtered by indexer capabilities."""
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
    for id_name in state._TORZNAB_ID_PARAMS:
        if id_name in supported:
            v = search_kwargs.get(id_name)
            if v:
                params[id_name] = str(v)
    cats = search_kwargs.get('categories')
    if cats:
        params['categories'] = list(cats)
    params['type'] = search_type

    if (
        not params.get('query')
        and not any(params.get(id_name) for id_name in state._TORZNAB_ID_PARAMS)
        and not params.get('categories')
    ):
        logger.debug(
            f"build_per_indexer_params: skipping indexer {idx_id} (q-only, no query/title, no supported IDs, no categories)"  # noqa: E501
        )
        return None

    if search_kwargs.get('limit'):
        try:
            if int(search_kwargs['limit']) > 0:
                params['limit'] = str(int(search_kwargs['limit']))
        except (TypeError, ValueError):
            params['limit'] = search_kwargs['limit']
    if search_kwargs.get('offset'):
        params['offset'] = search_kwargs['offset']

    # Apply user-configured param overrides (global + per-indexer).
    # Per-indexer overrides win over global; both win over computed params.
    overrides = state.get_param_overrides(idx_id)
    if overrides:
        params.update(overrides)
        logger.debug(
            f"build_per_indexer_params: applied param overrides to indexer {idx_id}: {overrides}"  # noqa: E501
        )

    return params


async def _search_one_indexer(session, sem, base_url, headers, indexer, params):
    """Execute one per-indexer Torznab passthrough GET under the concurrency semaphore."""
    idx_id = indexer.get('id') if isinstance(indexer, dict) else None
    url = urljoin(base_url, f"/{idx_id}/api")
    qp = {}
    if params.get('query'):
        qp['q'] = params['query']
    if params.get('type'):
        qp['t'] = params['type']
    cats = params.get('categories')
    if cats:
        qp['cat'] = list(cats)
    for id_name in state._TORZNAB_ID_PARAMS:
        if params.get(id_name):
            qp[id_name] = params[id_name]
    if params.get('limit'):
        qp['limit'] = params['limit']
    if params.get('offset'):
        qp['offset'] = params['offset']
    search_timeout = settings.get_float("PROWLARR_INDEXER_SEARCH_TIMEOUT", 10.0)
    async with sem:
        logger.debug(
            f"Prowlarr per-indexer Torznab: GET {url} indexerId={idx_id} "
            f"q={params.get('query')!r} t={params.get('type')!r} cats={params.get('categories')!r} "
            f"headers={{'X-Api-Key':'{_mask_prowlarr_key(headers.get('X-Api-Key'))}}}"
        )
        _t0 = time.perf_counter()
        try:
            async with session.get(
                url,
                headers=headers,
                params=qp,
                timeout=aiohttp.ClientTimeout(total=search_timeout),
            ) as response:
                response.raise_for_status()
                xml_bytes = await response.read()
                _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
                state.record_indexer_stat(idx_id, _elapsed_ms, error=False)
                logger.debug(f"Prowlarr indexer {idx_id} returned {len(xml_bytes)} XML bytes")
                return xml_bytes
        except asyncio.TimeoutError as e:
            _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
            state.record_indexer_stat(idx_id, _elapsed_ms, error=True)
            logger.warning(f"Prowlarr per-indexer Torznab search timed out for indexer {idx_id} after {search_timeout}s: {e}")  # noqa: E501
            return None
        except aiohttp.ClientError as e:
            _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
            state.record_indexer_stat(idx_id, _elapsed_ms, error=True)
            logger.warning(f"Prowlarr per-indexer Torznab search failed for indexer {idx_id}: {e}")
            return None
        except Exception as e:
            _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
            state.record_indexer_stat(idx_id, _elapsed_ms, error=True)
            logger.warning(f"Prowlarr per-indexer Torznab search error for indexer {idx_id}: {e}", exc_info=True)
            return None


async def search_prowlarr_per_indexer(session, tasks):
    """Run per-indexer Torznab searches in parallel; return (indexer, xml_bytes) pairs."""
    live = [(idx, p) for idx, p in tasks if p is not None]
    if not live:
        logger.debug("search_prowlarr_per_indexer: no live indexer tasks; returning []")
        return []
    base_url = settings.get_str("PROWLARR_URL")
    headers = {"X-Api-Key": settings.get_str("PROWLARR_API_KEY")}
    concurrency = max(settings.get_int("PROWLARR_PARALLEL_INDEXER_CONCURRENCY", 8), 1)
    sem = asyncio.Semaphore(concurrency)
    coros = [_search_one_indexer(session, sem, base_url, headers, idx, p) for idx, p in live]
    batches = await asyncio.gather(*coros, return_exceptions=False)
    out = [(idx, xml) for (idx, _p), xml in zip(live, batches) if xml is not None]
    logger.info(
        f"search_prowlarr_per_indexer: {len(live)} indexers -> {len(out)} successful XML docs"
    )
    return out


async def search_prowlarr(session, search_kwargs):
    """Search Prowlarr per-indexer (capability-driven) and return Torznab XML pairs."""
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


def _indexers_cache_get():
    """Return the cached indexer list if present and unexpired, else None."""
    entry = state._INDEXERS_CACHE.get('listing')
    if entry is None:
        return None
    if entry.get('expires', 0) <= time.time():
        return None
    state._INDEXERS_CACHE.move_to_end('listing')
    return entry.get('indexers')


def _indexers_cache_put(indexers):
    """Store the full indexer list under a single 'listing' key with TTL expiry + SQLite write-through."""
    if indexers is None:
        return
    ttl = settings.get_int("PROWLARR_INDEXERS_CACHE_TTL", 300)
    max_entries = max(settings.get_int("PROWLARR_INDEXERS_CACHE_MAX", 1), 1)
    expires = time.time() + ttl
    state._INDEXERS_CACHE['listing'] = {'indexers': list(indexers), 'expires': expires}
    state._INDEXERS_CACHE.move_to_end('listing')
    while len(state._INDEXERS_CACHE) > max_entries:
        try:
            state._INDEXERS_CACHE.popitem(last=False)
        except KeyError:
            break
    try:
        db.upsert_indexers(list(indexers), expires)
    except Exception as e:
        logger.debug(f"_indexers_cache_put: DB upsert failed: {e}", exc_info=True)
