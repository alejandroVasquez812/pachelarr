# Pachelarr — Agent Notes

A single-file FastAPI proxy between Radarr/Sonarr and Prowlarr that boosts Torbox-cached torrents and resolves IDs to titles via TMDB.

## Architecture

- **Entrypoint:** `main.py` is a thin re-export shim (`uvicorn main:app`); all logic lives in the `pachelarr/` package (`app.py` FastAPI app, `db.py` SQLite persistence, `settings.py` DB-backed settings + live getters, `state.py` module-level caches/counters, `tmdb.py`/`prowlarr.py`/`torznab.py`/`torbox.py`/`scrape.py`).
- **Package layout:** `pachelarr/` holds the implementation; `main.py` re-exports every name so tests keep using `import main as m`. Tests no longer rebind module globals for settings — they use `settings.set_override(...)` (the live-getter runtime model). `monkeypatch.setattr('main.search_prowlarr', ...)` and friends (function patches) are unchanged.
- **SQLite store (`pachelarr/db.py`):** A single SQLite DB at `<PACHELARR_DATA_DIR>/pachelarr.db` (default `./data`, `/app/data` in container) persists (a) the four in-memory LRU caches (write-through on every `*_cache_put`, rebuilt from SQLite on startup skipping expired TTL entries), (b) all settings, and (c) stats counters (periodic flush + on shutdown). One module-level `sqlite3.Connection` (`check_same_thread=False`) guarded by a `threading.Lock`; PRAGMAs `WAL` + `synchronous=NORMAL` + `busy_timeout`. Auto-migrates (`CREATE TABLE IF NOT EXISTS`) on startup; seeds settings from env only when the settings table is empty (first run). Deleting the DB re-seeds from env and continues.
- **Settings (`pachelarr/settings.py`):** SQLite is the source of truth. On startup env/.dotenv seeds the DB (first run only); subsequent reads come from the DB via live typed getters (`get_str`/`get_int`/`get_bool`/`get_float`), so DB edits apply without a restart. An in-memory `_overrides` dict (populated by `set_override`) is the test override layer. `PACHELARR_DATA_DIR` and `PACHELARR_LOG_LEVEL` are restart-required (fixed at startup); editing them via the REST API returns 400. API keys are stored as plaintext (same exposure as env). The `SETTINGS` registry maps every setting key to `{type, default, from_env, secret, restart_required}`.
- **REST settings API (`app.py`):** `GET /settings`, `GET /settings/{key}`, `PUT /settings` (body `{key: value, ...}`), authenticated by `PACHELARR_API_KEY` (header `X-Api-Key` or query `apikey`). Typed validation per setting; unknown key or bad type → 400. No UI.
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
- **Settings overrides use `settings.set_override(key, value)`**, not module-global rebinds. The conftest session fixture opens an in-memory SQLite DB (`PACHELARR_DATA_DIR=:memory:`), seeds settings from env once, and resets the caches + overrides + stats counters between tests. `set_override(key, None)` removes the override so the getter falls back to the DB/default.
- `test_integration_prowlarr.py` contains significant code duplication (many inline `FakeSession` / `FakeCtx` classes). Be careful when editing — indentation in later sections is fragile. **Note:** this file previously had a pre-existing `IndentationError` that now collects cleanly; the per-indexer behavior is also covered by `test_integration_prowlarr_minimal.py` + `tests/test_prowlarr_per_indexer.py`.
- `test_udp_scrape_protocol.py` creates a real asyncio UDP datagram server on `127.0.0.1:0` to exercise `_udp_scrape_one`.
- The scrape result cache (`main._SCRAPE_CACHE`) and the indexer listing cache (`main._INDEXERS_CACHE`) are module-level `OrderedDict`s shared across tests. Cache-related tests call `main._SCRAPE_CACHE.clear()` / `main._INDEXERS_CACHE.clear()` to avoid cross-test contamination; the conftest also clears them between tests.
- The magnet cache cap (`magnet_cache_max()`) reads `TRACKER_SCRAPE_CACHE_MAX` live via a getter. Tests that exercise LRU eviction use `settings.set_override("TRACKER_SCRAPE_CACHE_MAX", N)` rather than the old `m._MAGNET_CACHE_MAX = N` / `m.TRACKER_SCRAPE_CACHE_MAX = N` rebinds (those globals no longer exist).
- `tests/test_prowlarr_per_indexer.py` covers the per-indexer search strategy (`get_prowlarr_indexers_cached`, `select_indexers_for_query`, `build_per_indexer_params`, `search_prowlarr_per_indexer`). Its `_IndexerSession` fake serves a fixed indexer list for `/api/v1/indexer` and records `/api/v1/search` GETs.
- `tests/test_db.py` covers the SQLite layer (schema, seeding, getters, write-through, LRU rebuild, stats). `tests/test_settings_api.py` covers the REST settings API (auth, snapshot, PUT validation, live edits).

## Key Code Conventions

- **Settings are read via live getters** (`settings.get_str`/`get_int`/`get_bool`/`get_float`), never as module globals. If you add a new setting, register it in the `SETTINGS` dict in `pachelarr/settings.py` so it is seeded, editable via the REST API, and covered by the typed getters.
- **Prowlarr response normalization:** The API can return a list or a dict with keys like `records`, `results`, `items`, `data`. Search and indexer-listing code handles all variants.
- **Hash handling:** Info hashes are normalized to lowercase everywhere for consistent deduplication and cache lookups.
- **Consolidation:** Duplicate results per infohash are merged into one canonical item, combining trackers from all magnet URIs and keeping the item with the highest original seeders. Performed natively on parsed lxml `<item>` nodes in `consolidate_and_emit_xml` (mutates title/guid/link/enclosure/seeders/peers attrs/pubDate in place and re-emits the merged tree; untouched `<torznab:attr>`/children pass through automatically).
- **Cache persistence:** The four in-memory `OrderedDict` LRU caches stay the hot read path; every `*_cache_put` also writes through to SQLite (`db.upsert_*`). On startup `db.load_caches_into_lru()` rebuilds them (TTL caches skip expired rows; the magnet cache keeps its `None` negative sentinel; each cap is the live `*_MAX` setting). Cache `_get` reads only the in-memory LRU.
- **Stats persistence:** `state.py` keeps `torbox_hits`/`torbox_misses`/`last_search_latency_ms`/`last_search_at` as in-memory counters (mutated on the hot path). A background `asyncio` task in the lifespan flushes them to SQLite every 30s + on shutdown; `stats_load` seeds them on startup. `/statsz` reads the in-memory counters (no DB hit).
- **Limit filtering:** `limit=0` from clients is explicitly filtered out and not forwarded to Prowlarr.
- **Categories:** Passed to Prowlarr as a Python list (repeated `cat` query param) to avoid validation errors.
- **Per-indexer scoping:** `handle_search` no longer passes client `indexerIds`/`indexerId` through to Prowlarr; per-indexer selection comes entirely from `/api/v1/indexer` capabilities inside `search_prowlarr`. Each per-indexer search is scoped via the URL path `GET /<indexerId>/api` (Torznab passthrough), not an `indexerIds=` query param. IDs (`imdbid`/`tvdbid`/`tmdbid`/`season`/`ep`) are sent as standard Torznab query params only when the indexer's `*SearchParams` lists that param. The retired IDs `rid`/`tvmaze`/`traktid`/`doubanid` are no longer forwarded (their `_PROWLARR_ENUM_TO_OUR_NAME` entries and `{key:val}` token machinery were removed).

## Build / Deploy

- **Docker:** Multi-stage Dockerfile using `python:3.9.18-slim-bookworm`. Builder stage installs Poetry deps; final stage copies site-packages, creates `/app/data` owned by the non-root `app` user, and runs as that user.
- **Docker Compose:** `docker-compose.yml` is the primary deployment artifact. Exposes port `6800` by default (maps to container `8080` unless overridden by `PACHELARR_PORT`). Mounts `./data:/app/data` so the SQLite DB (settings, caches, stats) survives container recreation; `PACHELARR_DATA_DIR=/app/data` is set in the environment.
- **No CI, lint, or typecheck config** is present in the repo.

## Notable Env Vars

| Variable | Note |
|----------|------|
| `PROWLARR_URL` / `PROWLARR_API_KEY` | Required. Target Prowlarr instance. |
| `TORBOX_API_KEY` | Required. Bearer token for Torbox cache checks. |
| `TMDB_API_KEY` | Strongly recommended. Without it, ID-only searches from Radarr/Sonarr return poor results. |
| `PACHELARR_DATA_DIR` | Directory holding `pachelarr.db` (SQLite caches/settings/stats). Default `./data` (`/app/data` in container). Restart-required; mount a volume in Docker. |
| `PACHELARR_TEST_FALLBACK_QUERY` | Optional fallback query for category-only searches (improves Sonarr "Test" behavior). Now injected into `search_kwargs['query']` by `handle_search` before `search_prowlarr` is called. |
| `TRACKER_SCRAPE_ENABLED` | Optional UDP tracker scraping for real seeders. Adds latency. Default `false`. Results are cached and leechers are exposed in the Torznab XML. |
| `TRACKER_SCRAPE_CACHE_TTL` | Optional. TTL in seconds for scrape result cache. Default `300`. |
| `TRACKER_SCRAPE_CACHE_MAX` | Optional. Max number of cached scrape entries. Default `5000`. |
| `PROWLARR_INDEXERS_CACHE_TTL` | Optional. TTL in seconds for the Prowlarr indexer listing cache (full `IndexerResource[]`). Default `300`. |
| `PROWLARR_INDEXERS_CACHE_MAX` | Optional. Max cached indexer listings (a single listing holds the whole list). Default `1`. |
| `PROWLARR_PARALLEL_INDEXER_CONCURRENCY` | Optional. Max number of concurrent per-indexer `GET /<indexerId>/api` calls. Default `8`. |
