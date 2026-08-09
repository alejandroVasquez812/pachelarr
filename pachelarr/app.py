import asyncio
import logging
import time
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request, Response
from lxml import etree as ET

from pachelarr import db, settings, state
from pachelarr.prowlarr import _indexer_is_enabled

logger = logging.getLogger("pachelarr")

# Stats flush interval (seconds). Kept as a constant, not a setting, to avoid
# a loop where flushing a stats-interval setting triggers another flush.
_STATS_FLUSH_INTERVAL = 30.0


async def _stats_flush_loop():
    """Background task: periodically flush in-memory stats counters to SQLite."""
    try:
        while True:
            await asyncio.sleep(_STATS_FLUSH_INTERVAL)
            _flush_stats()
    except asyncio.CancelledError:
        # Final flush on shutdown.
        _flush_stats()
        raise


def _flush_stats():
    try:
        db.stats_save(
            state.torbox_hits, state.torbox_misses,
            state.last_search_latency_ms, state.last_search_at,
        )
    except Exception as e:
        logger.debug(f"stats flush failed: {e}", exc_info=True)

    # Flush per-indexer stats only when that granularity is enabled.
    if settings.stats_granularity_enabled("PER_INDEXER"):
        try:
            for indexer_id, s in state._INDEXER_STATS.items():
                db.upsert_indexer_stats(
                    indexer_id, s["requests"], s["errors"], s["total_latency_ms"],
                    s["last_latency_ms"], s["cached"], s["uncached"],
                )
        except Exception as e:
            logger.debug(f"indexer stats flush failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app):
    # Initialize the SQLite DB (auto-migrate + seed settings from env on first run).
    db.init()
    settings.seed_from_env_if_empty()
    db.load_caches_into_lru()

    # Seed in-memory stats counters from the DB so they survive restarts.
    try:
        loaded = db.stats_load()
        state.torbox_hits = loaded["torbox_hits"]
        state.torbox_misses = loaded["torbox_misses"]
        state.last_search_latency_ms = loaded["last_search_latency_ms"]
        state.last_search_at = loaded["last_search_at"]
    except Exception as e:
        logger.warning(f"Could not load stats from DB (starting fresh): {e}", exc_info=True)

    # Load per-indexer stats + recent search history so they survive restarts.
    try:
        state._INDEXER_STATS = db.load_indexer_stats()
        state._SEARCH_HISTORY.clear()
        for rec in db.load_searches(settings.get_int("STATS_PER_SEARCH_MAX", 100)):
            state._SEARCH_HISTORY.append(rec)
    except Exception as e:
        logger.warning(f"Could not load granular stats from DB (starting fresh): {e}", exc_info=True)

    _required = {
        "PROWLARR_URL": settings.get_str("PROWLARR_URL"),
        "PROWLARR_API_KEY": settings.get_str("PROWLARR_API_KEY"),
        "TORBOX_API_KEY": settings.get_str("TORBOX_API_KEY"),
    }
    _missing = [name for name, val in _required.items() if not (val and str(val).strip())]
    if _missing:
        msg = f"Missing required environment variables: {', '.join(_missing)}. Set them and restart."
        logger.error(msg)
        raise RuntimeError(msg)
    connector_limit = max(settings.get_int("PROWLARR_PARALLEL_INDEXER_CONCURRENCY", 8) * 2, 16)
    try:
        connector = aiohttp.TCPConnector(limit=connector_limit, limit_per_host=0)
    except (TypeError, ValueError):
        connector = aiohttp.TCPConnector()
    app.state.session = aiohttp.ClientSession(connector=connector)

    # Start the stats flush background task.
    app.state.stats_flush_task = asyncio.create_task(_stats_flush_loop())

    try:
        yield
    finally:
        # Cancel the flush loop and do a final flush.
        task = getattr(app.state, "stats_flush_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        _flush_stats()
        await app.state.session.close()
        db.close()


app = FastAPI(lifespan=lifespan)


@app.get("/api")
async def torznab_proxy(request: Request):
    """Handles Torznab requests from Sonarr/Radarr."""
    from pachelarr.torznab import get_caps_xml

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
    listing = state._INDEXERS_CACHE.get('listing')
    if listing is not None:
        age_seconds = int(listing.get('expires', 0.0) - time.time())
    else:
        age_seconds = None
    return {
        "status": "ok",
        "scrape_cache_size": len(state._SCRAPE_CACHE),
        "tmdb_title_cache_size": len(state._TMDB_TITLE_CACHE),
        "magnet_cache_size": len(state._MAGNET_CACHE),
        "indexers_cache": {
            "size": len(state._INDEXERS_CACHE),
            "age_seconds": age_seconds,
        },
        "last_search_latency_ms": state.last_search_latency_ms,
        "last_search_at": state.last_search_at,
        "torbox_hits": state.torbox_hits,
        "torbox_misses": state.torbox_misses,
    }


@app.get("/statsz/indexers")
async def statsz_indexers():
    listing = state._INDEXERS_CACHE.get('listing')
    raw = listing.get('indexers') if listing is not None else None
    indexers = []
    if raw:
        for idx in raw:
            idx_id = idx.get('id') or idx.get('indexerId') or idx.get('IndexerId')
            stats = state._INDEXER_STATS.get(idx_id, {})
            requests = stats.get("requests", 0)
            total_latency_ms = stats.get("total_latency_ms", 0.0)
            indexers.append({
                "id": idx_id,
                "name": idx.get('name') or idx.get('indexerName') or '',
                "protocol": idx.get('protocol') or '',
                "enabled": _indexer_is_enabled(idx),
                "supportsSearch": bool(idx.get('supportsSearch', True)),
                "requests": requests,
                "avg_latency_ms": (total_latency_ms / requests) if requests else 0,
                "last_latency_ms": stats.get("last_latency_ms") or 0,
                "cached": stats.get("cached", 0),
                "uncached": stats.get("uncached", 0),
                "errors": stats.get("errors", 0),
            })
    return {
        "generated_at": time.time(),
        "indexers": indexers,
    }


@app.get("/statsz/searches")
async def statsz_searches():
    """Return the in-memory per-search history, most recent first."""
    return {
        "generated_at": time.time(),
        "searches": list(reversed(state._SEARCH_HISTORY)),
    }


# --------------------------------------------------------------------------- #
# REST settings API
# --------------------------------------------------------------------------- #

def _check_settings_auth(request: Request):
    """Return None if authorized, else a Response with the appropriate error."""
    api_key = settings.get_str("PACHELARR_API_KEY")
    if not api_key:
        return Response(status_code=401, content="PACHELARR_API_KEY is not configured")
    provided = request.headers.get("X-Api-Key") or request.query_params.get("apikey")
    if not provided:
        return Response(status_code=401, content="Missing API key")
    if provided != api_key:
        return Response(status_code=403, content="Invalid API key")
    return None


@app.get("/settings")
async def get_settings(request: Request):
    err = _check_settings_auth(request)
    if err is not None:
        return err
    return settings.snapshot()


@app.get("/settings/{key}")
async def get_setting(key: str, request: Request):
    err = _check_settings_auth(request)
    if err is not None:
        return err
    if not settings.is_registered(key):
        return Response(status_code=404, content=f"unknown setting {key!r}")
    snap = settings.snapshot()
    return snap[key]


@app.put("/settings")
async def put_settings(request: Request):
    err = _check_settings_auth(request)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="invalid JSON body")
    if not isinstance(body, dict):
        return Response(status_code=400, content="expected a JSON object of {key: value}")
    applied = {}
    errors = {}
    for key, value in body.items():
        if not settings.is_registered(key):
            errors[key] = "unknown setting"
            continue
        try:
            settings.apply_setting(key, value)
            applied[key] = settings.get_typed(key)
        except settings.RestartRequiredError as e:
            errors[key] = str(e)
        except ValueError as e:
            errors[key] = str(e)
    if errors:
        return Response(status_code=400, content=str({"applied": applied, "errors": errors}))
    result = {"applied": applied, "settings": settings.snapshot()}
    return result


# --------------------------------------------------------------------------- #
# Search handler
# --------------------------------------------------------------------------- #

async def handle_search(params, session):
    """Performs search, checks cache, and returns enriched results."""
    t0 = time.time()
    try:
        return await _handle_search_impl(params, session)
    finally:
        if settings.stats_granularity_enabled("GLOBAL"):
            state.last_search_at = time.time()
            state.last_search_latency_ms = (time.time() - t0) * 1000.0


async def _handle_search_impl(params, session):
    """Performs search, checks cache, and returns enriched results."""
    import main
    from pachelarr import scrape, tmdb, torbox, torznab

    t0 = time.time()

    query = params.get('q', '')
    cleaned_query = tmdb.strip_foreign_language_tag(query)
    if cleaned_query != query:
        logger.info(f"Stripped trailing foreign-language tag: {query!r} -> {cleaned_query!r}")
        query = cleaned_query
    has_identifier = any(params.get(k) for k in ('tvdbid', 'imdbid', 'tmdbid', 'season', 'ep'))
    fallback_query = settings.get_str("PACHELARR_TEST_FALLBACK_QUERY", "")
    if not query and not has_identifier and params.get('cat'):
        logger.info(f"Incoming category-only request detected; applying fallback query '{fallback_query}'")  # noqa: E501
    categories = [cat for cat in params.get('cat', '').split(',') if cat]

    search_kwargs = {
        'query': query,
        'categories': categories,
        'type': params.get('t', 'search')
    }
    logger.info(f"Initial search_kwargs: {search_kwargs}")
    for key in ('tvdbid', 'season', 'ep', 'imdbid', 'tmdbid'):
        raw = params.get(key)
        if not raw:
            continue
        val = raw.strip()
        if key in ('season', 'ep'):
            first_token = val.split()[0] if val.split() else ''
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

    if not query and has_identifier:
        logger.info(f"Attempting title lookup for ID-based search: imdbid={params.get('imdbid')} tmdbid={params.get('tmdbid')} tvdbid={params.get('tvdbid')} rid={params.get('rid')}")  # noqa: E501
        title = await tmdb.lookup_title_from_id(
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

    if query and not has_identifier and params.get('t') in ('movie', 'tvsearch'):
        logger.info(f"Attempting ID lookup for title-based search: query={query!r} type={params.get('t')}")
        ids = await main.lookup_identifier_from_query(session, query, search_type=params.get('t'))
        if ids:
            logger.info(f"Looked up IDs from title: {ids}")
            for k in ('tmdbid', 'imdbid', 'tvdbid'):
                if ids.get(k):
                    search_kwargs[k] = ids[k]
        else:
            logger.info("ID lookup from title returned no results")

    if params.get('offset'):
        search_kwargs['offset'] = params.get('offset')
    if params.get('limit'):
        search_kwargs['limit'] = params.get('limit')

    if not query and not search_kwargs.get('categories') and not has_identifier:
        logger.info('No query nor identifier nor categories present for search; returning empty feed to avoid Prowlarr 400')  # noqa: E501
        return Response(content=torznab.create_empty_rss(), media_type="application/xml")
    if not query and not has_identifier and (params.get('cat') or search_kwargs.get('categories')):
        logger.info(f"Category-only search detected via raw params; substituting fallback query '{fallback_query}' for test behavior")  # noqa: E501
    logger.info(f"Search debug: query={query!r} categories={search_kwargs.get('categories')!r} fallback={fallback_query!r}")  # noqa: E501
    logger.debug(f"search_kwargs full: {search_kwargs}")

    prowlarr_results_xml = await main.search_prowlarr(session, search_kwargs)
    if not prowlarr_results_xml:
        return Response(content=torznab.create_empty_rss(), media_type="application/xml")

    info_hashes = torznab.extract_hashes_from_xml_pairs(prowlarr_results_xml)
    if not info_hashes:
        return Response(content=torznab.consolidate_and_emit_xml(prowlarr_results_xml, {}), media_type="application/xml")  # noqa: E501

    cached_status = await torbox.check_torbox_cache(session, info_hashes)

    uncached_seeders = {}
    if settings.get_bool("TRACKER_SCRAPE_ENABLED", False):
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
            except ET.XMLSyntaxError:
                continue
            for item in doc.iter('item'):
                ih = torznab._infohash_from_xml_item(item)
                if not ih:
                    continue
                if ih in seen_hashes:
                    continue
                seen_hashes.add(ih)
                if cached_status.get(ih):
                    continue
                mag = torznab._magnet_from_xml_item(item)
                if (not mag or 'tr=' not in (mag or '')):
                    try:
                        cached_mag = scrape._magnet_cache_get(ih)
                        if cached_mag:
                            mag = cached_mag
                    except KeyError:
                        proxy = torznab._proxy_url_from_xml_item(item)
                        if proxy:
                            resolved = await scrape.resolve_magnet_via_download(session, proxy, settings.get_float("TRACKER_SCRAPE_TIMEOUT", 5.0))  # noqa: E501
                            scrape._magnet_cache_put(ih, resolved)
                            if resolved and 'tr=' in resolved:
                                mag = resolved
                                resolved_count += 1
                            else:
                                unresolved_count += 1
                        else:
                            unresolved_count += 1
                for tr in torznab.parse_trackers_from_magnet(mag):
                    tracker_map.setdefault(tr, []).append(ih)
        logger.debug(f"tracker_map built: entries={len(tracker_map)} magnets_resolved={resolved_count} magnets_unresolved={unresolved_count}")  # noqa: E501
        if tracker_map:
            uncached_seeders = await scrape.scrape_trackers_inverted(tracker_map)
        else:
            logger.debug("tracker_map empty; skipping scrape_trackers_inverted (no tr= in any magnet / no magnets returned by Prowlarr)")  # noqa: E501
    xml_response = torznab.consolidate_and_emit_xml(prowlarr_results_xml, cached_status, uncached_seeders)

    # Record per-search stats (latency reflects the whole search). The
    # record_search helper no-ops when per-search granularity is disabled.
    state.record_search({
        "ts": time.time(),
        "query": query,
        "search_type": params.get('t', 'search'),
        "latency_ms": (time.time() - t0) * 1000.0,
        "torbox_cached": len(cached_status),
        # cached_status only holds hit hashes (misses are absent), so uncached
        # is the set difference of all hashes vs cached ones.
        "torbox_uncached": max(len(info_hashes) - len(cached_status), 0),
        "indexer_count": len(prowlarr_results_xml),
    })

    # Fire-and-forget per-indexer cache attribution so it never blocks the
    # response. No-op when per-indexer granularity is disabled.
    if settings.stats_granularity_enabled("PER_INDEXER"):
        asyncio.create_task(_attribute_indexer_cache(prowlarr_results_xml, cached_status))

    return Response(content=xml_response, media_type="application/xml")


async def _attribute_indexer_cache(xml_pairs, cached_status):
    """Attribute torbox cached/uncached counts to each indexer.

    Parses each indexer's XML, maps its infohashes onto ``cached_status``, and
    accumulates the cached/uncached totals via ``state.record_indexer_cache_attribution``.
    Best-effort: any failure is logged at debug and dropped.
    """
    from pachelarr import torznab
    try:
        for indexer, xml_bytes in xml_pairs:
            if not xml_bytes:
                continue
            idx_id = indexer.get('id') if isinstance(indexer, dict) else None
            if idx_id is None:
                continue
            cached_n = 0
            uncached_n = 0
            try:
                doc = ET.fromstring(xml_bytes)
            except ET.XMLSyntaxError:
                continue
            seen = set()
            for item in doc.iter('item'):
                ih = torznab._infohash_from_xml_item(item)
                if not ih or ih in seen:
                    continue
                seen.add(ih)
                if cached_status.get(ih):
                    cached_n += 1
                else:
                    uncached_n += 1
            state.record_indexer_cache_attribution(idx_id, cached_n, uncached_n)
    except Exception as e:
        logger.debug(f"indexer cache attribution failed: {e}", exc_info=True)
