import os

from pachelarr import prowlarr, scrape, state, tmdb, torbox, torznab  # noqa: F401
from pachelarr.app import app, handle_search, healthz, lifespan, statsz, torznab_proxy  # noqa: F401
from pachelarr.prowlarr import (  # noqa: F401
    _collect_indexer_category_ids,
    _indexer_is_enabled,
    _indexer_supported_our_names,
    _indexers_cache_get,
    _indexers_cache_put,
    _mask_prowlarr_key,
    _normalize_indexer_list,
    _search_one_indexer,
    build_per_indexer_params,
    get_all_prowlarr_indexers,
    get_prowlarr_indexers_cached,
    isTorrentIndexer,
    search_prowlarr,
    search_prowlarr_per_indexer,
    select_indexers_for_query,
)
from pachelarr.scrape import (  # noqa: F401
    _magnet_cache_get,
    _magnet_cache_put,
    _parse_tracker_host_port,
    _resolve_udp_addr,
    _scrape_cache_get,
    _scrape_cache_put,
    _udp_scrape_tracker,
    resolve_magnet_via_download,
    scrape_trackers_inverted,
)
from pachelarr.state import (  # noqa: F401
    _INDEXERS_CACHE,
    _MAGNET_CACHE,
    _MAGNET_CACHE_MAX,
    _PROWLARR_ENUM_TO_OUR_NAME,
    _SCRAPE_CACHE,
    _SEARCH_PARAMS_FIELD,
    _TMDB_TITLE_CACHE,
    _TORZNAB_ID_PARAMS,
    _TORZNAB_NS,
    FOREIGN_LANGUAGE_TAGS,
    PACHELARR_API_KEY,
    PACHELARR_SEEDERS_BOOST,
    PACHELARR_TEST_FALLBACK_QUERY,
    PROWLARR_API_KEY,
    PROWLARR_INDEXER_SEARCH_TIMEOUT,
    PROWLARR_INDEXERS_CACHE_MAX,
    PROWLARR_INDEXERS_CACHE_TTL,
    PROWLARR_PARALLEL_INDEXER_CONCURRENCY,
    PROWLARR_URL,
    TMDB_API_KEY,
    TMDB_TITLE_LOOKUP_CACHE_MAX,
    TMDB_TITLE_LOOKUP_CACHE_TTL,
    TMDB_TITLE_LOOKUP_ENABLED,
    TORBOX_API_KEY,
    TORBOX_CHECK_URL,
    TORBOX_CHUNK_SIZE,
    TORBOX_MAX_RETRIES,
    TORBOX_RETRY_BACKOFF,
    TRACKER_SCRAPE_BATCH_SIZE,
    TRACKER_SCRAPE_CACHE_MAX,
    TRACKER_SCRAPE_CACHE_TTL,
    TRACKER_SCRAPE_CONCURRENCY,
    TRACKER_SCRAPE_ENABLED,
    TRACKER_SCRAPE_TIMEOUT,
    last_search_at,
    last_search_latency_ms,
    logger,
    torbox_hits,
    torbox_misses,
)
from pachelarr.tmdb import (  # noqa: F401
    _tmdb_title_cache_get,
    _tmdb_title_cache_put,
    lookup_identifier_from_query,
    lookup_title_from_id,
    strip_foreign_language_tag,
)
from pachelarr.torbox import check_torbox_cache  # noqa: F401
from pachelarr.torznab import (  # noqa: F401
    _get_magnet_uri_for_item,
    _infohash_from_xml_item,
    _magnet_from_xml_item,
    _normalize_pubdate,
    _proxy_url_from_xml_item,
    _set_xml_attr,
    _xml_attr,
    consolidate_and_emit_xml,
    create_empty_rss,
    dedupe_hashes_preserve_order,
    extract_hashes_from_xml_pairs,
    get_caps_xml,
    infohash_from_item,
    parse_trackers_from_magnet,
)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PACHELARR_PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
