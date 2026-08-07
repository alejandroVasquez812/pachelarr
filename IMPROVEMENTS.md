# Pachelarr — Improvement Report

> Generated: 2026-08-06
> Source: codebase review of `main.py` (1810 lines) + `tests/` (10 files, 93 tests)
> Status legend: [ ] TODO · [~] In progress · [x] Done · [-] Won't do

---

## 1. Fix broken test collection

**Problem:** `tests/test_udp_scrape_protocol.py` fails at import time:
```
ImportError: cannot import name '_udp_scrape_one' from 'main'. Did you mean: '_udp_scrape_tracker'?
```
The function was renamed to `_udp_scrape_tracker` but the test still imports the old name, so the entire module is skipped on every run — silently hiding its coverage.

A second issue (noted in `AGENTS.md`): `tests/test_integration_prowlarr.py` has a pre-existing `IndentationError` around line 1009 that prevents collection of the duplicated `FakeSession`/`FakeCtx` tests.

**Tasks:**
- [x] Rename the import in `test_udp_scrape_protocol.py` (or restore a `_udp_scrape_one` alias in `main.py`).
- [x] Fix the `IndentationError` around `test_integration_prowlarr.py:1009` so the module collects.
- [x] Run `pytest --collect-only -q` and confirm 0 errors.

**Effort:** XS · **Impact:** High (restores test coverage)
**Priority:** P0

---

## 2. Add a `/healthz` + `/statsz` endpoint

**Problem:** There is exactly one route (`GET /api`) and zero observability. No Docker healthcheck exists in `docker-compose.yml`. Silent degradation (Prowlarr unreachable but returning `[]`, caches exhausted) is invisible.

**Tasks:**
- [x] Add `GET /healthz` → `{"status":"ok"}` for compose healthchecks.
- [x] Add `GET /statsz` → cache sizes (`_SCRAPE_CACHE`, `_TMDB_TITLE_CACHE`, `_MAGNET_CACHE`, `_INDEXERS_CACHE`), indexer-listing age, last-search latency, Torbox hit/miss counters.
- [x] Wire a `healthcheck:` block into `docker-compose.yml`.

**Effort:** S · **Impact:** Medium (ops)
**Priority:** P2

---

## 3. Share a single `aiohttp.ClientSession`

**Problem:** `handle_search` opens a **new** `ClientSession` on every search (`main.py:653`). aiohttp strongly recommends reusing a session (connection pool, DNS cache, keep-alive). For a proxy that fans out to N indexers in parallel per request, this is the single highest-leverage perf change.

**Tasks:**
- [x] Create one session at startup (`app.state.session`) and close it on shutdown (use FastAPI `lifespan`).
- [x] Thread `TORBOX_CHUNK_SIZE` / retry settings into a shared `TCPConnector`.
- [x] Update tests that build their own session (e.g. `FakeSession` fakes) so they keep working.

**Effort:** S · **Impact:** High (perf)
**Priority:** P1

---

## 4. Validate required env vars at startup

**Problem:** `PROWLARR_URL` / `PROWLARR_API_KEY` / `TORBOX_API_KEY` are required but read as bare `os.getenv(...)` (`main.py:19-22`). If unset, the app boots fine and only fails on the first search with a cryptic 500.

**Tasks:**
- [x] Add a startup validator (`@app.on_event("startup")` or `lifespan`) that checks required keys.
- [x] Log a clear, actionable error and exit if missing.

**Effort:** S · **Impact:** High (DX)
**Priority:** P1

---

## 5. Narrow broad `except Exception` blocks

**Problem:** ~35 bare `except Exception` blocks exist (see grep over `main.py`). Several are in hot paths (`_infohash_from_xml_item`, `consolidate_and_emit_xml`, scrape parsing) and silently drop data with no log, making failures invisible.

**Tasks:**
- [ ] Replace broad catches with specific exceptions (`lxml.etree.XMLSyntaxError`, `ValueError`, `KeyError`, `struct.error`).
- [ ] Where a broad catch is intentional, add `logger.debug(..., exc_info=True)`.
- [ ] Audit the UDP scrape path (`_udp_scrape_tracker`, `scrape_trackers_inverted`) — most critical for debugging dead trackers.

**Effort:** M · **Impact:** Medium (debuggability)
**Priority:** P2

---

## 6. Configure `pytest-asyncio` auto mode + dedupe test fakes

**Problem:** `pytest-asyncio ^0.20.3` is pinned but there is no `asyncio_mode = "auto"` config, so every async test needs `@pytest.mark.asyncio`. AGENTS.md also flags heavy `FakeSession`/`FakeCtx` duplication in `test_integration_prowlarr.py`, with indentation that is fragile to edit.

**Tasks:**
- [ ] Add to `pyproject.toml`:
  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  ```
- [ ] Extract shared `FakeSession` / `FakeCtx` into `tests/_fakes.py`.
- [ ] Trim `test_integration_prowlarr.py` to use the shared fakes (also resolves the indentation fragility).

**Effort:** M · **Impact:** Medium (test velocity)
**Priority:** P2

---

## 7. Split `main.py` into a package

**Problem:** `main.py` is 1810 lines holding TMDB lookups, Prowlarr search, XML consolidation, Torbox cache check, UDP scrape protocol, caches, and the FastAPI app. Hard to navigate and test in isolation.

**Proposed layout:**
```
pachelarr/
  config.py     # env var globals + validation
  tmdb.py        # lookup_title_from_id, lookup_identifier_from_query
  prowlarr.py    # indexer listing, per-indexer search
  torznab.py     # XML parsing/consolidation/emit
  torbox.py      # check_torbox_cache
  scrape.py      # UDP scrape protocol
  caches.py      # _SCRAPE_CACHE / _TMDB_TITLE_CACHE / _MAGNET_CACHE / _INDEXERS_CACHE
  app.py         # FastAPI app + handle_search (thin)
main.py          # keeps `uvicorn main:app` working: `from pachelarr.app import app`
```

**Tasks:**
- [ ] Create the `pachelarr/` package and move logic module by module.
- [ ] Keep `main.py` as the uvicorn entrypoint re-exporting `app`.
- [ ] Update test imports (`import main` → `from pachelarr import ...` where needed; keep `import main` for backward compat if possible).
- [ ] Update `AGENTS.md` "Not a package" note.

**Effort:** L · **Impact:** High (maintainability) — invasive, do last
**Priority:** P3

---

## 8. Add per-indexer HTTP timeouts

**Problem:** `_search_one_indexer` calls `session.get(url, headers=..., params=qp)` with **no `timeout=`** (`main.py:852`), relying on aiohttp's default 5-min total timeout. With `PROWLARR_PARALLEL_INDEXER_CONCURRENCY=8`, a single hung indexer can stall a search for minutes.

**Tasks:**
- [x] Add `PROWLARR_INDEXER_SEARCH_TIMEOUT` env var (default e.g. 10s).
- [x] Pass `aiohttp.ClientTimeout(total=PROWLARR_INDEXER_SEARCH_TIMEOUT)` to each per-indexer `session.get`.
- [x] Confirm `_search_one_indexer` still returns `None` on `asyncio.TimeoutError` (one bad indexer never aborts the gather).

**Effort:** XS · **Impact:** High (reliability)
**Priority:** P1

---

## 9. Make caches truly LRU

**Problem:** All four module-level caches (`_SCRAPE_CACHE`, `_TMDB_TITLE_CACHE`, `_MAGNET_CACHE`, `_INDEXERS_CACHE`) evict by `min(expires)` or `next(iter(...))` — not real LRU. In particular `_MAGNET_CACHE_put` evicts a **random** entry (`next(iter(_MAGNET_CACHE))`), since dict insertion order ≠ access order.

**Tasks:**
- [x] Switch caches to `collections.OrderedDict` + `move_to_end` on get/put.
- [-] Or replace with a small `lru_cache`-style helper.
- [x] Add a test that evicts the least-recently-used entry (not a random one).

**Effort:** S · **Impact:** Medium (correctness)
**Priority:** P2

---

## 10. Add linting/formatting

**Problem:** No flake8/ruff/black/mypy config exists, and there is no CI. The repo has unused dict-based helpers (`_get_magnet_uri_for_item`, `infohash_from_item` kept "for compatibility" but only used by tests now), and inconsistent quoting/style.

**Tasks:**
- [ ] Add `ruff` config to `pyproject.toml` (lint + format).
- [ ] Add `make lint` and `make format` targets to the `Makefile`.
- [ ] Run a first pass and fix/ignore findings.
- [ ] Optionally add `mypy` for the cache/config modules (highest-value typing surface).

**Effort:** S (setup) / M (cleanup) · **Impact:** Medium (long-term quality)
**Priority:** P2

---

## Priority Summary

| # | Improvement | Effort | Impact | Priority |
|---|---|---|---|---|
| 1 | Fix broken test imports | XS | High | P0 |
| 8 | Per-indexer HTTP timeout | XS | High | P1 |
| 3 | Shared `ClientSession` | S | High | P1 |
| 4 | Startup env validation | S | High | P1 |
| 2 | `/healthz` + `/statsz` | S | Medium | P2 |
| 9 | Real LRU caches | S | Medium | P2 |
| 5 | Narrow `except Exception` | M | Medium | P2 |
| 6 | pytest-asyncio auto + dedupe fakes | M | Medium | P2 |
| 10 | Add ruff + `make lint` | S–M | Medium | P2 |
| 7 | Split `main.py` into a package | L | High | P3 |

**Quick wins (XS–S, do first):** #1, #8, #3, #4.