"""SQLite-backed persistence for caches, settings, and stats.

Single-file SQLite database under ``<PACHELARR_DATA_DIR>/pachelarr.db``.

Design (see ``.kilo/plans/1786154159930-sqlite-cache-settings-stats.md``):

- One module-level :class:`sqlite3.Connection` (``check_same_thread=False``)
  guarded by a :class:`threading.Lock`. SQLite ops are sub-millisecond local
  writes; the lock serializes the bounded write traffic.
- ``migrate()`` creates all tables idempotently and seeds the single stats row.
- Settings: ``setting_get`` / ``setting_set`` read/write the ``settings`` table.
  Seeding from env happens in :mod:`pachelarr.settings` (which knows defaults).
- Caches: per-cache ``upsert_*`` helpers write through on every ``*_cache_put``;
  ``load_*`` helpers rebuild the in-memory LRU on startup (TTL caches skip
  expired rows).
- Stats: ``stats_load`` seeds the in-memory counters; ``stats_save`` flushes
  them (periodic + shutdown).

The module is safe to import without an open DB: every public call requires
:func:`init` first and raises a clear error otherwise. Tests point the DB at
``:memory:`` via :func:`init` so the suite needs no on-disk fixture.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger("pachelarr")

# Defaults for the DB path. ``PACHELARR_DATA_DIR`` is read once at init time;
# it is intentionally NOT a live setting (the path is fixed for the process).
_DEFAULT_DATA_DIR = "./data"
_DB_FILENAME = "pachelarr.db"

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_db_path: Optional[str] = None


class DBNotInitialized(RuntimeError):
    """Raised when a DB op is attempted before :func:`init`."""


def _require_conn() -> sqlite3.Connection:
    if _conn is None:
        raise DBNotInitialized("db.init() has not been called")
    return _conn


def db_path() -> Optional[str]:
    """Return the path of the currently-open DB (or None before init)."""
    return _db_path


def init(db_path: Optional[str] = None) -> str:
    """Open the SQLite DB, apply PRAGMAs, run :func:`migrate`, and return the path.

    If ``db_path`` is None, resolve it from ``PACHELARR_DATA_DIR`` (creating the
    directory if needed). A ``":memory:"`` path opens an in-memory DB (tests).

    If a connection is already open for the same resolved path, this is a
    no-op (so the test session's in-memory DB survives the lifespan's init
    call). Passing an explicit different path re-opens against that path.
    """
    global _conn, _db_path
    with _lock:
        # Resolve the desired path first so we can compare with the current one.
        desired = db_path
        if desired is None:
            data_dir = os.getenv("PACHELARR_DATA_DIR", _DEFAULT_DATA_DIR)
            if data_dir == ":memory:":
                desired = ":memory:"
            else:
                os.makedirs(data_dir, exist_ok=True)
                desired = os.path.join(data_dir, _DB_FILENAME)

        # Re-init is a no-op ONLY when the caller did not pass an explicit path
        # and the resolved path matches the currently-open one. An explicit
        # db_path (including ":memory:") always forces a fresh connection so
        # tests can isolate against a brand-new in-memory DB.
        if db_path is None and _conn is not None and _db_path == desired:
            return _db_path

        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
            _db_path = None

        db_path = desired
        _db_path = db_path
        conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # WAL + NORMAL synchronous keeps writes fast with crash-safe durability
        # good enough for a cache/settings store; busy_timeout waits on locks.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        _conn = conn
        migrate()
        logger.info(f"SQLite DB opened at {db_path}")
        return db_path


def close() -> None:
    """Close the DB connection (does not delete the file)."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        _db_path = None


# --------------------------------------------------------------------------- #
# Schema migration
# --------------------------------------------------------------------------- #

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cache_scrape (
        key       TEXT PRIMARY KEY,
        seeders   INTEGER NOT NULL,
        leechers  INTEGER NOT NULL,
        downloads INTEGER NOT NULL DEFAULT 0,
        expires   REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cache_scrape_expires ON cache_scrape(expires)",
    """
    CREATE TABLE IF NOT EXISTS cache_magnet (
        key        TEXT PRIMARY KEY,
        magnet     TEXT,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cache_torbox (
        key        TEXT PRIMARY KEY,
        cached     INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cache_tmdb_title (
        key      TEXT PRIMARY KEY,
        ids_json TEXT NOT NULL,
        expires  REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cache_tmdb_expires ON cache_tmdb_title(expires)",
    """
    CREATE TABLE IF NOT EXISTS cache_indexers (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        indexers_json TEXT NOT NULL,
        expires       REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stats (
        id                   INTEGER PRIMARY KEY CHECK (id = 1),
        torbox_hits          INTEGER NOT NULL DEFAULT 0,
        torbox_misses        INTEGER NOT NULL DEFAULT 0,
        last_search_latency_ms REAL,
        last_search_at       REAL,
        updated_at           REAL NOT NULL
    )
    """,
    "INSERT OR IGNORE INTO stats (id, updated_at) VALUES (1, 0)",
    """
    CREATE TABLE IF NOT EXISTS stats_indexers (
        indexer_id         INTEGER PRIMARY KEY,
        requests           INTEGER NOT NULL DEFAULT 0,
        errors             INTEGER NOT NULL DEFAULT 0,
        total_latency_ms   REAL NOT NULL DEFAULT 0,
        last_latency_ms    REAL,
        cached             INTEGER NOT NULL DEFAULT 0,
        uncached           INTEGER NOT NULL DEFAULT 0,
        updated_at         REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stats_searches (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ts               REAL NOT NULL,
        query            TEXT,
        search_type      TEXT,
        latency_ms       REAL,
        torbox_cached    INTEGER,
        torbox_uncached  INTEGER,
        indexer_count    INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS param_overrides (
        scope        TEXT PRIMARY KEY,
        params_json  TEXT NOT NULL,
        updated_at   REAL NOT NULL
    )
    """,
]


def migrate() -> None:
    """Create all tables if missing and seed the single stats row. Idempotent.

    Assumes the caller (typically :func:`init`) already holds ``_lock``.
    """
    conn = _require_conn()
    for stmt in _SCHEMA:
        conn.execute(stmt)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

def setting_get(key: str) -> Optional[str]:
    """Return the stored value for ``key`` or None if absent."""
    conn = _require_conn()
    with _lock:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row is not None else None


def setting_set(key: str, value: str) -> None:
    """Upsert a settings row with an updated_at timestamp."""
    conn = _require_conn()
    now = time.time()
    with _lock:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )


def settings_count() -> int:
    """Return the number of stored settings rows (used to detect first run)."""
    conn = _require_conn()
    with _lock:
        row = conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()
    return int(row["n"])


def settings_all() -> dict:
    """Return all settings as a dict {key: value}."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def settings_replace(updates: dict) -> None:
    """Upsert multiple settings in one transaction."""
    conn = _require_conn()
    now = time.time()
    with _lock:
        conn.execute("BEGIN")
        try:
            for key, value in updates.items():
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at",
                    (key, value, now),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# --------------------------------------------------------------------------- #
# Cache: scrape
# --------------------------------------------------------------------------- #

def upsert_scrape(key: str, entry: dict) -> None:
    conn = _require_conn()
    seeders = int(entry.get("seeders", 0) or 0)
    leechers = int(entry.get("leechers", 0) or 0)
    downloads = int(entry.get("downloads", 0) or 0)
    expires = float(entry.get("expires", 0.0) or 0.0)
    with _lock:
        conn.execute(
            "INSERT INTO cache_scrape (key, seeders, leechers, downloads, expires) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET seeders=excluded.seeders, "
            "leechers=excluded.leechers, downloads=excluded.downloads, expires=excluded.expires",
            (key, seeders, leechers, downloads, expires),
        )


def load_scrape(now: float, max_entries: int) -> list:
    """Return non-expired scrape rows (key, entry) ordered oldest-first so the
    caller's ``move_to_end`` re-establishes recency on later access."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT key, seeders, leechers, downloads, expires FROM cache_scrape "
            "WHERE expires > ? ORDER BY expires ASC LIMIT ?",
            (now, max_entries),
        ).fetchall()
    return [
        (r["key"], {
            "seeders": r["seeders"], "leechers": r["leechers"],
            "downloads": r["downloads"], "expires": r["expires"],
        })
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Cache: magnet
# --------------------------------------------------------------------------- #

def upsert_magnet(key: str, magnet: Optional[str]) -> None:
    conn = _require_conn()
    with _lock:
        conn.execute(
            "INSERT INTO cache_magnet (key, magnet, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET magnet=excluded.magnet, "
            "updated_at=excluded.updated_at",
            (key, magnet, time.time()),
        )


def load_magnet(max_entries: int) -> list:
    """Return magnet rows (key, magnet) ordered oldest-first. NULL magnet is the
    negative sentinel and is preserved as None."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT key, magnet FROM cache_magnet ORDER BY updated_at ASC LIMIT ?",
            (max_entries,),
        ).fetchall()
    return [(r["key"], r["magnet"]) for r in rows]


# --------------------------------------------------------------------------- #
# Cache: torbox (known-cached infohashes)
# --------------------------------------------------------------------------- #

def upsert_torbox(key: str) -> None:
    """Record an infohash as known-cached (write-through from _TORBOX_CACHE)."""
    conn = _require_conn()
    with _lock:
        conn.execute(
            "INSERT INTO cache_torbox (key, cached, updated_at) VALUES (?, 1, ?) "
            "ON CONFLICT(key) DO UPDATE SET cached=excluded.cached, "
            "updated_at=excluded.updated_at",
            (key, time.time()),
        )


def load_torbox(max_entries: int) -> list:
    """Return known-cached infohash keys ordered oldest-first so the caller's
    ``move_to_end`` re-establishes recency on later access."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT key FROM cache_torbox ORDER BY updated_at ASC LIMIT ?",
            (max_entries,),
        ).fetchall()
    return [r["key"] for r in rows]


# --------------------------------------------------------------------------- #
# Cache: tmdb title
# --------------------------------------------------------------------------- #

def upsert_tmdb_title(key: str, ids: dict, expires: float) -> None:
    conn = _require_conn()
    with _lock:
        conn.execute(
            "INSERT INTO cache_tmdb_title (key, ids_json, expires) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET ids_json=excluded.ids_json, "
            "expires=excluded.expires",
            (key, json.dumps(ids), expires),
        )


def load_tmdb_title(now: float, max_entries: int) -> list:
    """Return non-expired TMDB rows (key, ids, expires) ordered oldest-first."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT key, ids_json, expires FROM cache_tmdb_title "
            "WHERE expires > ? ORDER BY expires ASC LIMIT ?",
            (now, max_entries),
        ).fetchall()
    return [
        (r["key"], json.loads(r["ids_json"]), r["expires"])
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Cache: indexers (single 'listing' row)
# --------------------------------------------------------------------------- #

def upsert_indexers(indexers: list, expires: float) -> None:
    conn = _require_conn()
    with _lock:
        conn.execute(
            "INSERT INTO cache_indexers (id, indexers_json, expires) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET indexers_json=excluded.indexers_json, "
            "expires=excluded.expires",
            (json.dumps(indexers), expires),
        )


def load_indexers(now: float) -> Optional[list]:
    """Return the cached indexer list if present and unexpired, else None."""
    conn = _require_conn()
    with _lock:
        row = conn.execute(
            "SELECT indexers_json, expires FROM cache_indexers WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    if float(row["expires"]) <= now:
        return None
    return json.loads(row["indexers_json"])


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #

def stats_load() -> dict:
    """Return the single stats row as a dict (zeroed if the row is fresh)."""
    conn = _require_conn()
    with _lock:
        row = conn.execute(
            "SELECT torbox_hits, torbox_misses, last_search_latency_ms, last_search_at "
            "FROM stats WHERE id = 1"
        ).fetchone()
    if row is None:
        return {
            "torbox_hits": 0, "torbox_misses": 0,
            "last_search_latency_ms": None, "last_search_at": None,
        }
    return {
        "torbox_hits": int(row["torbox_hits"]),
        "torbox_misses": int(row["torbox_misses"]),
        "last_search_latency_ms": row["last_search_latency_ms"],
        "last_search_at": row["last_search_at"],
    }


def stats_save(torbox_hits: int, torbox_misses: int,
               last_search_latency_ms: Optional[float],
               last_search_at: Optional[float]) -> None:
    conn = _require_conn()
    with _lock:
        conn.execute(
            "UPDATE stats SET torbox_hits = ?, torbox_misses = ?, "
            "last_search_latency_ms = ?, last_search_at = ?, updated_at = ? "
            "WHERE id = 1",
            (torbox_hits, torbox_misses, last_search_latency_ms,
             last_search_at, time.time()),
        )


def upsert_indexer_stats(indexer_id, requests, errors, total_latency_ms,
                         last_latency_ms, cached, uncached) -> None:
    """Upsert one indexer's accumulated stats row."""
    conn = _require_conn()
    with _lock:
        conn.execute(
            "INSERT INTO stats_indexers (indexer_id, requests, errors, "
            "total_latency_ms, last_latency_ms, cached, uncached, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(indexer_id) DO UPDATE SET requests = excluded.requests, "
            "errors = excluded.errors, total_latency_ms = excluded.total_latency_ms, "
            "last_latency_ms = excluded.last_latency_ms, "
            "cached = excluded.cached, uncached = excluded.uncached, "
            "updated_at = excluded.updated_at",
            (int(indexer_id), int(requests), int(errors), float(total_latency_ms),
             last_latency_ms, int(cached), int(uncached), time.time()),
        )


def load_indexer_stats() -> dict:
    """Return all per-indexer stats as ``{indexer_id: {requests, errors,
    total_latency_ms, last_latency_ms, cached, uncached}}``."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT indexer_id, requests, errors, total_latency_ms, "
            "last_latency_ms, cached, uncached FROM stats_indexers"
        ).fetchall()
    out = {}
    for r in rows:
        out[int(r["indexer_id"])] = {
            "requests": int(r["requests"]),
            "errors": int(r["errors"]),
            "total_latency_ms": float(r["total_latency_ms"]),
            "last_latency_ms": r["last_latency_ms"],
            "cached": int(r["cached"]),
            "uncached": int(r["uncached"]),
        }
    return out


def insert_search(record) -> None:
    """Insert one search record, then prune rows beyond STATS_PER_SEARCH_MAX."""
    conn = _require_conn()
    from pachelarr import settings
    cap = max(settings.get_int("STATS_PER_SEARCH_MAX", 100), 1)
    with _lock:
        conn.execute(
            "INSERT INTO stats_searches (ts, query, search_type, latency_ms, "
            "torbox_cached, torbox_uncached, indexer_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.get("ts"), record.get("query"), record.get("search_type"),
                record.get("latency_ms"), record.get("torbox_cached"),
                record.get("torbox_uncached"), record.get("indexer_count"),
            ),
        )
        conn.execute(
            "DELETE FROM stats_searches WHERE id NOT IN "
            "(SELECT id FROM stats_searches ORDER BY id DESC LIMIT ?)",
            (cap,),
        )


def load_searches(limit: int) -> list:
    """Return the most-recent ``limit`` search records as dicts, newest first."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT ts, query, search_type, latency_ms, torbox_cached, "
            "torbox_uncached, indexer_count FROM stats_searches "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [
        {
            "ts": r["ts"],
            "query": r["query"],
            "search_type": r["search_type"],
            "latency_ms": r["latency_ms"],
            "torbox_cached": r["torbox_cached"],
            "torbox_uncached": r["torbox_uncached"],
            "indexer_count": r["indexer_count"],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Param overrides (global + per-indexer Torznab query param overrides)
# --------------------------------------------------------------------------- #

def upsert_param_overrides(scope: str, params: dict) -> None:
    """Upsert one param-overrides row (scope = "global" or "indexer:<id>")."""
    conn = _require_conn()
    with _lock:
        conn.execute(
            "INSERT INTO param_overrides (scope, params_json, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET params_json = excluded.params_json, "
            "updated_at = excluded.updated_at",
            (scope, json.dumps(params), time.time()),
        )


def get_param_overrides(scope: str) -> Optional[dict]:
    """Return the params dict for one scope, or None if absent."""
    conn = _require_conn()
    with _lock:
        row = conn.execute(
            "SELECT params_json FROM param_overrides WHERE scope = ?", (scope,)
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["params_json"])
    except (json.JSONDecodeError, TypeError):
        return None


def get_all_param_overrides() -> dict:
    """Return all overrides as ``{scope: params_dict}``."""
    conn = _require_conn()
    with _lock:
        rows = conn.execute(
            "SELECT scope, params_json FROM param_overrides"
        ).fetchall()
    out = {}
    for r in rows:
        try:
            out[r["scope"]] = json.loads(r["params_json"])
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def delete_param_overrides(scope: str) -> None:
    """Delete one param-overrides row by scope."""
    conn = _require_conn()
    with _lock:
        conn.execute("DELETE FROM param_overrides WHERE scope = ?", (scope,))


def load_param_overrides_into_state() -> None:
    """Rebuild the in-memory ``state._PARAM_OVERRIDES`` dict from SQLite.

    Called from the FastAPI lifespan after ``load_caches_into_lru``.
    """
    from pachelarr import state
    state._PARAM_OVERRIDES = get_all_param_overrides()


# --------------------------------------------------------------------------- #
# Cache / stats deletion helpers
# --------------------------------------------------------------------------- #

def delete_indexers_cache_row() -> None:
    """Delete the single cached indexer listing row (cache_indexers)."""
    conn = _require_conn()
    with _lock:
        conn.execute("DELETE FROM cache_indexers WHERE id = 1")


def delete_indexer_stats(indexer_id=None) -> None:
    """Delete all per-indexer stats rows, or one if ``indexer_id`` is given."""
    conn = _require_conn()
    with _lock:
        if indexer_id is not None:
            conn.execute(
                "DELETE FROM stats_indexers WHERE indexer_id = ?", (int(indexer_id),)
            )
        else:
            conn.execute("DELETE FROM stats_indexers")


def delete_searches() -> None:
    """Delete all search history rows."""
    conn = _require_conn()
    with _lock:
        conn.execute("DELETE FROM stats_searches")


# --------------------------------------------------------------------------- #
# Load caches into in-memory LRU
# --------------------------------------------------------------------------- #

def load_caches_into_lru() -> None:
    """Rebuild the four in-memory OrderedDict caches from SQLite.

    Called from the FastAPI lifespan after :func:`init`. TTL caches skip
    expired rows; the magnet cache keeps its NULL negative sentinel. Each
    cache is capped at its configured ``*_MAX`` setting. Importing
    :mod:`pachelarr.state` here (lazily) avoids a circular import at module
    load time.
    """
    from pachelarr import settings, state

    now = time.time()

    # scrape cache
    max_scrape = settings.get_int("TRACKER_SCRAPE_CACHE_MAX", 5000)
    for key, entry in load_scrape(now, max_scrape):
        state._SCRAPE_CACHE[key] = entry
        state._SCRAPE_CACHE.move_to_end(key)

    # magnet cache
    max_magnet = settings.get_int("TRACKER_SCRAPE_CACHE_MAX", 5000)
    for key, magnet in load_magnet(max_magnet):
        state._MAGNET_CACHE[key] = magnet
        state._MAGNET_CACHE.move_to_end(key)

    # tmdb title cache
    import json as _json
    max_tmdb = settings.get_int("TMDB_TITLE_LOOKUP_CACHE_MAX", 5000)
    for key_str, ids, expires in load_tmdb_title(now, max_tmdb):
        # The DB stores a JSON-serialized tuple key; reconstruct the tuple so
        # the in-memory LRU uses the same key shape as live cache_get/put.
        try:
            key = tuple(_json.loads(key_str))
        except (TypeError, ValueError):
            continue
        # The stored value is a dict either way; detect which shape it is
        # (title->ID ids-dict entries vs ID->title title-string entries).
        if isinstance(ids, dict) and "title" in ids:
            state._TMDB_TITLE_CACHE[key] = {"title": ids["title"], "expires": expires}
        else:
            state._TMDB_TITLE_CACHE[key] = {"ids": ids, "expires": expires}
        state._TMDB_TITLE_CACHE.move_to_end(key)

    # torbox known-cached infohash cache (no TTL; presence means cached).
    max_torbox = settings.get_int("TORBOX_CACHE_MAX", 5000)
    for key in load_torbox(max_torbox):
        state._TORBOX_CACHE[key] = True
        state._TORBOX_CACHE.move_to_end(key)

    # indexers cache: only one listing row; load if fresh.
    indexers = load_indexers(now)
    if indexers is not None:
        expires = now + settings.get_int("PROWLARR_INDEXERS_CACHE_TTL", 300)
        state._INDEXERS_CACHE["listing"] = {"indexers": indexers, "expires": expires}
        state._INDEXERS_CACHE.move_to_end("listing")
