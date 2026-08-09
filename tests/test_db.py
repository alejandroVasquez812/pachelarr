"""Tests for the SQLite persistence layer (pachelarr.db).

Covers schema creation + idempotent migrate, env-seed-on-empty vs.
env-does-not-overwrite-existing, typed settings getters + override layer,
write-through for every cache, LRU rebuild (TTL expiry skipped, magnet
negative sentinel, cap enforcement), and stats flush + reload.

The conftest session fixture opens an in-memory DB and seeds settings once;
these tests re-init to a fresh in-memory DB where they need isolation, then
restore the session DB.
"""
import time

import pytest

import main as m
from pachelarr import db, settings, state

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fresh_db():
    """Re-init to a brand new in-memory DB (independent of the session DB)."""
    db.init(":memory:")
    db.load_caches_into_lru()


@pytest.fixture
def fresh_db():
    """Yield a fresh empty in-memory DB, then restore a seeded session DB.

    Re-initing to ``:memory:`` gives an empty DB independent of the session DB.
    In teardown we re-init to ``:memory:`` and re-seed settings so the rest of
    the session sees a populated settings table again.
    """
    db.init(":memory:")
    try:
        yield
    finally:
        db.init(":memory:")
        settings.seed_from_env_if_empty()
        db.load_caches_into_lru()


# --------------------------------------------------------------------------- #
# Schema / migrate
# --------------------------------------------------------------------------- #

def test_migrate_creates_all_tables(fresh_db):
    conn = db._conn
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    for expected in ("settings", "cache_scrape", "cache_magnet",
                     "cache_tmdb_title", "cache_torbox", "cache_indexers", "stats"):
        assert expected in names, f"missing table {expected}"


def test_migrate_is_idempotent(fresh_db):
    # Calling migrate() directly (no lock re-entry since init holds it)
    # should not raise and should leave the stats row present.
    conn = db._conn
    db.migrate()
    row = conn.execute("SELECT COUNT(*) AS n FROM stats WHERE id = 1").fetchone()
    assert row["n"] == 1


def test_stats_row_seeded_on_migrate(fresh_db):
    loaded = db.stats_load()
    assert loaded["torbox_hits"] == 0
    assert loaded["torbox_misses"] == 0
    assert loaded["last_search_latency_ms"] is None
    assert loaded["last_search_at"] is None


# --------------------------------------------------------------------------- #
# Settings seeding
# --------------------------------------------------------------------------- #

def test_seed_from_env_only_when_empty(fresh_db):
    # First seed populates the table.
    seeded = settings.seed_from_env_if_empty()
    assert seeded is True
    assert db.settings_count() == len(settings.SETTINGS)

    # A second call must NOT overwrite (table is non-empty).
    seeded_again = settings.seed_from_env_if_empty()
    assert seeded_again is False


def test_seed_uses_registry_defaults_for_unset_env(fresh_db, monkeypatch):
    # Ensure a specific env var is unset so the default is used.
    monkeypatch.delenv("PACHELARR_SEEDERS_BOOST", raising=False)
    settings.seed_from_env_if_empty()
    # Default for PACHELARR_SEEDERS_BOOST is 10000.
    assert settings.get_int("PACHELARR_SEEDERS_BOOST", 0) == 10000


def test_seed_uses_env_when_present(fresh_db, monkeypatch):
    monkeypatch.setenv("PACHELARR_SEEDERS_BOOST", "555")
    settings.seed_from_env_if_empty()
    assert settings.get_int("PACHELARR_SEEDERS_BOOST", 0) == 555


def test_env_does_not_overwrite_existing_db_value(fresh_db, monkeypatch):
    settings.seed_from_env_if_empty()
    # User customizes via the DB.
    settings.apply_setting("PACHELARR_SEEDERS_BOOST", 999)
    # A later env change must NOT clobber the DB value on next seed (table non-empty).
    monkeypatch.setenv("PACHELARR_SEEDERS_BOOST", "1")
    settings.seed_from_env_if_empty()
    assert settings.get_int("PACHELARR_SEEDERS_BOOST", 0) == 999


# --------------------------------------------------------------------------- #
# Settings getters + override layer
# --------------------------------------------------------------------------- #

def test_getters_cast_typed_values(fresh_db):
    settings.seed_from_env_if_empty()
    settings.set_override("PACHELARR_SEEDERS_BOOST", 42)
    settings.set_override("TRACKER_SCRAPE_ENABLED", True)
    settings.set_override("TRACKER_SCRAPE_TIMEOUT", 7.5)
    settings.set_override("PROWLARR_URL", "http://example")
    assert settings.get_int("PACHELARR_SEEDERS_BOOST") == 42
    assert settings.get_bool("TRACKER_SCRAPE_ENABLED") is True
    assert settings.get_float("TRACKER_SCRAPE_TIMEOUT") == 7.5
    assert settings.get_str("PROWLARR_URL") == "http://example"


def test_override_none_removes_override(fresh_db):
    settings.seed_from_env_if_empty()
    settings.set_override("PACHELARR_SEEDERS_BOOST", 42)
    assert settings.get_int("PACHELARR_SEEDERS_BOOST") == 42
    settings.set_override("PACHELARR_SEEDERS_BOOST", None)
    # Falls back to DB/default (default 10000).
    assert settings.get_int("PACHELARR_SEEDERS_BOOST") == 10000


def test_clear_overrides_removes_all(fresh_db):
    settings.seed_from_env_if_empty()
    settings.set_override("PACHELARR_SEEDERS_BOOST", 42)
    settings.clear_overrides()
    assert settings.get_int("PACHELARR_SEEDERS_BOOST") == 10000


def test_get_returns_default_when_db_uninitialized():
    # Save the current connection, close it, then a getter must fall back to default.
    saved_path = db.db_path()
    db.close()
    try:
        assert settings.get_int("PACHELARR_SEEDERS_BOOST") == 10000
        assert settings.get_str("PROWLARR_URL", "fallback") == "fallback"
    finally:
        db.init(saved_path)


def test_validate_value_rejects_unknown_key():
    with pytest.raises(ValueError):
        settings.validate_value("NOT_A_SETTING", 1)


def test_validate_value_rejects_bad_type():
    with pytest.raises(ValueError):
        settings.validate_value("PACHELARR_SEEDERS_BOOST", "not-an-int")


def test_apply_setting_persists_to_db(fresh_db):
    settings.seed_from_env_if_empty()
    settings.apply_setting("PACHELARR_SEEDERS_BOOST", 321)
    # Direct DB read confirms persistence.
    raw = db.setting_get("PACHELARR_SEEDERS_BOOST")
    assert raw == "321"


def test_apply_setting_rejects_restart_required(fresh_db):
    settings.seed_from_env_if_empty()
    with pytest.raises(settings.RestartRequiredError):
        settings.apply_setting("PACHELARR_DATA_DIR", "/new/path")


# --------------------------------------------------------------------------- #
# Write-through cache persistence
# --------------------------------------------------------------------------- #

def test_scrape_cache_put_writes_through(fresh_db):
    state._SCRAPE_CACHE.clear()
    m._scrape_cache_put("abc", {"seeders": 5, "leechers": 1, "downloads": 9})
    rows = db.load_scrape(time.time(), 100)
    assert any(k == "abc" for k, _ in rows)
    entry = [e for k, e in rows if k == "abc"][0]
    assert entry["seeders"] == 5
    assert entry["leechers"] == 1
    assert "expires" in entry


def test_magnet_cache_put_writes_through_including_null_sentinel(fresh_db):
    state._MAGNET_CACHE.clear()
    m._magnet_cache_put("h1", "magnet:?xt=urn:btih:h1")
    m._magnet_cache_put("h2", None)  # negative sentinel
    rows = dict(db.load_magnet(100))
    assert rows["h1"] == "magnet:?xt=urn:btih:h1"
    assert rows["h2"] is None


def test_tmdb_title_cache_put_writes_through(fresh_db):
    state._TMDB_TITLE_CACHE.clear()
    m._tmdb_title_cache_put(("title", None, "movie"), {"tmdbid": "1", "imdbid": "2"})
    rows = db.load_tmdb_title(time.time(), 100)
    assert len(rows) == 1
    key_str, ids, _expires = rows[0]
    assert ids == {"tmdbid": "1", "imdbid": "2"}
    # The DB key is the JSON-serialized form of the tuple.
    import json
    assert tuple(json.loads(key_str)) == ("title", None, "movie")


def test_indexers_cache_put_writes_through(fresh_db):
    state._INDEXERS_CACHE.clear()
    m._indexers_cache_put([{"id": 1, "name": "demo"}])
    loaded = db.load_indexers(time.time())
    assert loaded == [{"id": 1, "name": "demo"}]


# --------------------------------------------------------------------------- #
# LRU rebuild from SQLite
# --------------------------------------------------------------------------- #

def test_load_caches_skips_expired_scrape_entries(fresh_db):
    # Insert an expired scrape row directly.
    conn = db._conn
    conn.execute(
        "INSERT INTO cache_scrape (key, seeders, leechers, downloads, expires) "
        "VALUES (?, 1, 0, 0, ?)",
        ("expired", time.time() - 100),
    )
    conn.execute(
        "INSERT INTO cache_scrape (key, seeders, leechers, downloads, expires) "
        "VALUES (?, 2, 0, 0, ?)",
        ("live", time.time() + 100),
    )
    state._SCRAPE_CACHE.clear()
    db.load_caches_into_lru()
    assert "expired" not in state._SCRAPE_CACHE
    assert "live" in state._SCRAPE_CACHE
    assert state._SCRAPE_CACHE["live"]["seeders"] == 2


def test_load_caches_skips_expired_tmdb_entries(fresh_db):
    import json
    conn = db._conn
    conn.execute(
        "INSERT INTO cache_tmdb_title (key, ids_json, expires) VALUES (?, ?, ?)",
        (json.dumps(("expired", None, "movie")), json.dumps({"tmdbid": "1"}), time.time() - 100),
    )
    conn.execute(
        "INSERT INTO cache_tmdb_title (key, ids_json, expires) VALUES (?, ?, ?)",
        (json.dumps(("live", None, "movie")), json.dumps({"tmdbid": "2"}), time.time() + 100),
    )
    state._TMDB_TITLE_CACHE.clear()
    db.load_caches_into_lru()
    assert ("expired", None, "movie") not in state._TMDB_TITLE_CACHE
    assert ("live", None, "movie") in state._TMDB_TITLE_CACHE


def test_load_caches_loads_magnet_with_null_sentinel(fresh_db):
    conn = db._conn
    conn.execute(
        "INSERT INTO cache_magnet (key, magnet, updated_at) VALUES (?, NULL, ?)",
        ("neg", time.time()),
    )
    conn.execute(
        "INSERT INTO cache_magnet (key, magnet, updated_at) VALUES (?, ?, ?)",
        ("pos", "magnet:?xt=urn:btih:pos", time.time()),
    )
    state._MAGNET_CACHE.clear()
    db.load_caches_into_lru()
    assert state._MAGNET_CACHE["neg"] is None
    assert state._MAGNET_CACHE["pos"] == "magnet:?xt=urn:btih:pos"


def test_load_caches_loads_fresh_indexers_listing(fresh_db):
    import json
    conn = db._conn
    indexers = [{"id": 1, "name": "demo"}]
    conn.execute(
        "INSERT INTO cache_indexers (id, indexers_json, expires) VALUES (1, ?, ?)",
        (json.dumps(indexers), time.time() + 100),
    )
    state._INDEXERS_CACHE.clear()
    db.load_caches_into_lru()
    assert "listing" in state._INDEXERS_CACHE
    assert state._INDEXERS_CACHE["listing"]["indexers"] == indexers


def test_load_caches_skips_expired_indexers_listing(fresh_db):
    import json
    conn = db._conn
    conn.execute(
        "INSERT INTO cache_indexers (id, indexers_json, expires) VALUES (1, ?, ?)",
        (json.dumps([{"id": 1}]), time.time() - 100),
    )
    state._INDEXERS_CACHE.clear()
    db.load_caches_into_lru()
    assert "listing" not in state._INDEXERS_CACHE


def test_load_caches_caps_magnet_at_max(fresh_db):
    # Insert more rows than the configured cap.
    conn = db._conn
    for i in range(10):
        conn.execute(
            "INSERT INTO cache_magnet (key, magnet, updated_at) VALUES (?, ?, ?)",
            (f"k{i}", f"magnet:{i}", time.time() + i),
        )
    state._MAGNET_CACHE.clear()
    settings.set_override("TRACKER_SCRAPE_CACHE_MAX", 3)
    try:
        db.load_caches_into_lru()
        assert len(state._MAGNET_CACHE) <= 3
    finally:
        settings.set_override("TRACKER_SCRAPE_CACHE_MAX", None)


# --------------------------------------------------------------------------- #
# Torbox cache (cache_torbox) + load_caches_into_lru
# --------------------------------------------------------------------------- #

def test_upsert_and_load_torbox_roundtrip(fresh_db):
    db.upsert_torbox("abc123")
    db.upsert_torbox("def456")
    assert set(db.load_torbox(100)) == {"abc123", "def456"}


def test_load_torbox_orders_oldest_first(fresh_db):
    db.upsert_torbox("old")
    db.upsert_torbox("new")
    assert db.load_torbox(100) == ["old", "new"]


def test_load_torbox_respects_limit(fresh_db):
    db.upsert_torbox("a")
    db.upsert_torbox("b")
    db.upsert_torbox("c")
    assert db.load_torbox(2) == ["a", "b"]


def test_load_caches_populates_torbox_cache(fresh_db):
    conn = db._conn
    conn.execute(
        "INSERT INTO cache_torbox (key, cached, updated_at) VALUES (?, 1, ?)",
        ("h1", time.time()),
    )
    conn.execute(
        "INSERT INTO cache_torbox (key, cached, updated_at) VALUES (?, 1, ?)",
        ("h2", time.time()),
    )
    state._TORBOX_CACHE.clear()
    db.load_caches_into_lru()
    assert state._TORBOX_CACHE["h1"] is True
    assert state._TORBOX_CACHE["h2"] is True


def test_load_caches_caps_torbox_at_max(fresh_db):
    conn = db._conn
    for i in range(10):
        conn.execute(
            "INSERT INTO cache_torbox (key, cached, updated_at) VALUES (?, 1, ?)",
            (f"k{i}", time.time() + i),
        )
    state._TORBOX_CACHE.clear()
    settings.set_override("TORBOX_CACHE_MAX", 3)
    try:
        db.load_caches_into_lru()
        assert len(state._TORBOX_CACHE) <= 3
    finally:
        settings.set_override("TORBOX_CACHE_MAX", None)


def test_load_caches_handles_id_title_tmdb_shape(fresh_db):
    """ID->title entries (title-string shape) survive the startup rebuild."""
    import json
    conn = db._conn
    conn.execute(
        "INSERT INTO cache_tmdb_title (key, ids_json, expires) VALUES (?, ?, ?)",
        (json.dumps(("id", "imdbid", "1375666", "movie")),
         json.dumps({"title": "Inception 2010"}), time.time() + 100),
    )
    state._TMDB_TITLE_CACHE.clear()
    db.load_caches_into_lru()
    entry = state._TMDB_TITLE_CACHE[("id", "imdbid", "1375666", "movie")]
    assert entry["title"] == "Inception 2010"
    assert "expires" in entry


# --------------------------------------------------------------------------- #
# Stats flush + reload
# --------------------------------------------------------------------------- #

def test_stats_save_and_load_roundtrip(fresh_db):
    db.stats_save(10, 3, 42.5, 1700000000.0)
    loaded = db.stats_load()
    assert loaded["torbox_hits"] == 10
    assert loaded["torbox_misses"] == 3
    assert loaded["last_search_latency_ms"] == 42.5
    assert loaded["last_search_at"] == 1700000000.0


def test_stats_zeroed_on_fresh_db(fresh_db):
    loaded = db.stats_load()
    assert loaded["torbox_hits"] == 0
    assert loaded["torbox_misses"] == 0


# --------------------------------------------------------------------------- #
# Per-indexer stats (stats_indexers)
# --------------------------------------------------------------------------- #

def test_upsert_indexer_stats_inserts_fresh_row(fresh_db):
    db.upsert_indexer_stats(7, 1, 0, 50.0, 50.0, 1, 2)
    loaded = db.load_indexer_stats()
    assert 7 in loaded
    entry = loaded[7]
    assert entry["requests"] == 1
    assert entry["errors"] == 0
    assert entry["total_latency_ms"] == 50.0
    assert entry["last_latency_ms"] == 50.0
    assert entry["cached"] == 1
    assert entry["uncached"] == 2


def test_upsert_indexer_stats_on_conflict_replaces_all_fields(fresh_db):
    db.upsert_indexer_stats(7, 1, 0, 50.0, 50.0, 1, 2)
    # Second upsert for the same indexer must replace every field, not merge.
    db.upsert_indexer_stats(7, 3, 1, 150.0, 60.0, 4, 5)
    loaded = db.load_indexer_stats()
    assert len(loaded) == 1
    entry = loaded[7]
    assert entry["requests"] == 3
    assert entry["errors"] == 1
    assert entry["total_latency_ms"] == 150.0
    assert entry["last_latency_ms"] == 60.0
    assert entry["cached"] == 4
    assert entry["uncached"] == 5


def test_load_indexer_stats_returns_expected_shape(fresh_db):
    db.upsert_indexer_stats(1, 2, 1, 100.0, 40.0, 3, 4)
    db.upsert_indexer_stats(2, 5, 0, 250.0, 50.0, 0, 5)
    loaded = db.load_indexer_stats()
    assert set(loaded.keys()) == {1, 2}
    for idx_id, entry in loaded.items():
        for key in ("requests", "errors", "total_latency_ms", "last_latency_ms",
                    "cached", "uncached"):
            assert key in entry, f"missing key {key} for indexer {idx_id}"


def test_load_indexer_stats_empty_when_no_rows(fresh_db):
    assert db.load_indexer_stats() == {}


# --------------------------------------------------------------------------- #
# Per-search history (stats_searches)
# --------------------------------------------------------------------------- #

def _insert_search_records(n, prefix="q"):
    for i in range(n):
        db.insert_search({
            "ts": 1700000000.0 + i,
            "query": f"{prefix}{i}",
            "search_type": "search",
            "latency_ms": 10.0 + i,
            "torbox_cached": i,
            "torbox_uncached": 0,
            "indexer_count": 1,
        })


def test_insert_search_and_load_searches_roundtrip(fresh_db):
    _insert_search_records(2)
    rows = db.load_searches(100)
    assert len(rows) == 2
    for row in rows:
        for key in ("ts", "query", "search_type", "latency_ms",
                    "torbox_cached", "torbox_uncached", "indexer_count"):
            assert key in row, f"missing key {key}"
    # Newest first (ORDER BY id DESC).
    assert rows[0]["query"] == "q1"
    assert rows[1]["query"] == "q0"


def test_insert_search_prunes_beyond_cap(fresh_db):
    settings.set_override("STATS_PER_SEARCH_MAX", 3)
    try:
        # Insert cap + 2 = 5 records; only the newest 3 survive.
        _insert_search_records(5)
        rows = db.load_searches(100)
        assert len(rows) == 3
        # Newest first: q4, q3, q2.
        assert [r["query"] for r in rows] == ["q4", "q3", "q2"]
    finally:
        settings.set_override("STATS_PER_SEARCH_MAX", None)


def test_load_searches_respects_limit(fresh_db):
    _insert_search_records(5)
    rows = db.load_searches(2)
    assert len(rows) == 2
    assert [r["query"] for r in rows] == ["q4", "q3"]
