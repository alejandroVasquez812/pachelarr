# Pachelarr — Agent Notes

A single-file FastAPI proxy between Radarr/Sonarr and Prowlarr that boosts Torbox-cached torrents and resolves IDs to titles via TMDB.

## Architecture

- **Entrypoint:** `main.py` (single file, ~2000 lines). Runs via `uvicorn main:app`.
- **Not a package:** There is no `src/` or `app/` package. All logic lives in `main.py`, imported by tests as `import main`.
- **Protocol:** Torznab XML proxy. Receives Torznab queries from Radarr/Sonarr, queries Prowlarr, enriches results, returns XML.
- **Search is per-indexer/capability-driven:** `search_prowlarr` is a thin wrapper that fetches the cached `IndexerResource[]` list (`get_prowlarr_indexers_cached`), selects indexers eligible for the query (`select_indexers_for_query`: `enable` + `supportsSearch` + lenient category match incl. nested `subCategories`), builds per-indexer params filtered by each indexer's `*SearchParams` capabilities (`build_per_indexer_params`, forwarding only supported IDs as standard Torznab query params — no `{key:val}` tokens), then runs the per-indexer `GET /<indexerId>/api` (Torznab passthrough) calls in parallel (`search_prowlarr_per_indexer`, bounded by `PROWLARR_PARALLEL_INDEXER_CONCURRENCY`) and returns `[(indexer, xml_bytes), ...]`. Downstream pipeline (`extract_hashes_from_xml_pairs` -> `consolidate_and_emit_xml`) parses and re-emits the Torznab XML natively.
- **External deps:** Prowlarr instance, Torbox API, optional TMDB API for ID-to-title lookups.

## Developer Setup

- **Dependency manager:** Poetry (`pyproject.toml`). Fallback to `pip install -r requirements-dev.txt` if Poetry is absent.
- **Install dev deps:**
  ```bash
  make install-dev
  ```
- **Run tests (from repo root):**
  ```bash
  make test          # python3 -m pytest -q -s
  ```
- **Docker test:**
  ```bash
  make docker-build-dev   # build with dev deps
  make docker-test        # run pytest inside container
  ```

## Testing Quirks

- Tests import `main` as a module. Always run pytest from the repository root so `main.py` is on `sys.path`.
- `test_integration_prowlarr.py` contains significant code duplication (many inline `FakeSession` / `FakeCtx` classes). Be careful when editing — indentation in later sections is fragile. **Note:** this file currently has a pre-existing `IndentationError` around line 1009 that prevents collection; run it in isolation only after fixing, or rely on `test_integration_prowlarr_minimal.py` + `tests/test_prowlarr_per_indexer.py` for the per-indexer behavior.
- `test_udp_scrape_protocol.py` creates a real asyncio UDP datagram server on `127.0.0.1:0` to exercise `_udp_scrape_one`.
- The scrape result cache (`main._SCRAPE_CACHE`) and the indexer listing cache (`main._INDEXERS_CACHE`) are module-level dicts shared across tests. Cache-related tests call `main._SCRAPE_CACHE.clear()` / `main._INDEXERS_CACHE.clear()` to avoid cross-test contamination.
- Integration tests monkeypatch module-level globals directly (e.g., `m.PACHELARR_TEST_FALLBACK_QUERY = 'a'`) because env vars are read at import time. (Older duplicated blocks may still reference the retired `m.CACHEBOX_TEST_FALLBACK_QUERY` name; only `PACHELARR_TEST_FALLBACK_QUERY` is read by `main.py`.)
- `tests/test_prowlarr_per_indexer.py` covers the per-indexer search strategy (`get_prowlarr_indexers_cached`, `select_indexers_for_query`, `build_per_indexer_params`, `search_prowlarr_per_indexer`). Its `_IndexerSession` fake serves a fixed indexer list for `/api/v1/indexer` and records `/api/v1/search` GETs.

## Key Code Conventions

- **Env vars are module-level globals** read once at import. If you add a new config, tests that monkeypatch it must also patch the module attribute after import.
- **Prowlarr response normalization:** The API can return a list or a dict with keys like `records`, `results`, `items`, `data`. Search and indexer-listing code handles all variants.
- **Hash handling:** Info hashes are normalized to lowercase everywhere for consistent deduplication and cache lookups.
- **Consolidation:** Duplicate results per infohash are merged into one canonical item, combining trackers from all magnet URIs and keeping the item with the highest original seeders. Performed natively on parsed lxml `<item>` nodes in `consolidate_and_emit_xml` (mutates title/guid/link/enclosure/seeders/peers attrs/pubDate in place and re-emits the merged tree; untouched `<torznab:attr>`/children pass through automatically).
- **Limit filtering:** `limit=0` from clients is explicitly filtered out and not forwarded to Prowlarr.
- **Categories:** Passed to Prowlarr as a Python list (repeated `cat` query param) to avoid validation errors.
- **Per-indexer scoping:** `handle_search` no longer passes client `indexerIds`/`indexerId` through to Prowlarr; per-indexer selection comes entirely from `/api/v1/indexer` capabilities inside `search_prowlarr`. Each per-indexer search is scoped via the URL path `GET /<indexerId>/api` (Torznab passthrough), not an `indexerIds=` query param. IDs (`imdbid`/`tvdbid`/`tmdbid`/`season`/`ep`) are sent as standard Torznab query params only when the indexer's `*SearchParams` lists that param. The retired IDs `rid`/`tvmaze`/`traktid`/`doubanid` are no longer forwarded (their `_PROWLARR_ENUM_TO_OUR_NAME` entries and `{key:val}` token machinery were removed).

## Build / Deploy

- **Docker:** Multi-stage Dockerfile using `python:3.9.18-slim-bookworm`. Builder stage installs Poetry deps; final stage copies site-packages and runs as non-root `app` user.
- **Docker Compose:** `docker-compose.yml` is the primary deployment artifact. Exposes port `6800` by default (maps to container `8080` unless overridden by `PACHELARR_PORT`).
- **No CI, lint, or typecheck config** is present in the repo.

## Notable Env Vars

| Variable | Note |
|----------|------|
| `PROWLARR_URL` / `PROWLARR_API_KEY` | Required. Target Prowlarr instance. |
| `TORBOX_API_KEY` | Required. Bearer token for Torbox cache checks. |
| `TMDB_API_KEY` | Strongly recommended. Without it, ID-only searches from Radarr/Sonarr return poor results. |
| `PACHELARR_TEST_FALLBACK_QUERY` | Optional fallback query for category-only searches (improves Sonarr "Test" behavior). Now injected into `search_kwargs['query']` by `handle_search` before `search_prowlarr` is called. |
| `TRACKER_SCRAPE_ENABLED` | Optional UDP tracker scraping for real seeders. Adds latency. Default `false`. Results are cached and leechers are exposed in the Torznab XML. |
| `TRACKER_SCRAPE_CACHE_TTL` | Optional. TTL in seconds for scrape result cache. Default `300`. |
| `TRACKER_SCRAPE_CACHE_MAX` | Optional. Max number of cached scrape entries. Default `5000`. |
| `PROWLARR_INDEXERS_CACHE_TTL` | Optional. TTL in seconds for the Prowlarr indexer listing cache (full `IndexerResource[]`). Default `300`. |
| `PROWLARR_INDEXERS_CACHE_MAX` | Optional. Max cached indexer listings (a single listing holds the whole list). Default `1`. |
| `PROWLARR_PARALLEL_INDEXER_CONCURRENCY` | Optional. Max number of concurrent per-indexer `GET /<indexerId>/api` calls. Default `8`. |
