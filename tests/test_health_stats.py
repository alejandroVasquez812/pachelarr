"""Tests for /healthz and /statsz endpoints (improvement #2).

 Uses FastAPI's TestClient, which runs the event loop + lifespan internally.
 The lifespan env-validation (improvement #4) raises RuntimeError if the
 required settings (PROWLARR_URL, PROWLARR_API_KEY, TORBOX_API_KEY) are unset,
 so we set them via the settings store BEFORE constructing the client.

 Settings overrides use settings.set_override (the live-getter runtime model),
 not module globals. Run from the repo root so ``import main`` resolves.
 """
import pytest
from fastapi.testclient import TestClient

import main as m
from pachelarr import settings, state

_REQUIRED = ("PROWLARR_URL", "PROWLARR_API_KEY", "TORBOX_API_KEY")


@pytest.fixture
def client():
    saved = {name: settings.get_typed(name) for name in _REQUIRED}
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    m._INDEXERS_CACHE.clear()
    try:
        with TestClient(m.app) as c:
            yield c
    finally:
        m._INDEXERS_CACHE.clear()
        for name, val in saved.items():
            settings.set_override(name, val)


def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_statsz_returns_expected_keys(client):
    r = client.get("/statsz")
    assert r.status_code == 200
    data = r.json()
    for key in (
        "status",
        "scrape_cache_size",
        "tmdb_title_cache_size",
        "magnet_cache_size",
        "indexers_cache",
        "last_search_latency_ms",
        "last_search_at",
        "torbox_hits",
        "torbox_misses",
    ):
        assert key in data, f"missing key {key}"
    assert data["status"] == "ok"
    assert isinstance(data["scrape_cache_size"], int)
    assert isinstance(data["tmdb_title_cache_size"], int)
    assert isinstance(data["magnet_cache_size"], int)
    assert isinstance(data["torbox_hits"], int)
    assert isinstance(data["torbox_misses"], int)
    ic = data["indexers_cache"]
    assert isinstance(ic, dict)
    assert "size" in ic and "age_seconds" in ic
    assert isinstance(ic["size"], int)


def test_statsz_indexers_cache_age_null_when_empty(client):
    m._INDEXERS_CACHE.clear()
    r = client.get("/statsz")
    assert r.status_code == 200
    ic = r.json()["indexers_cache"]
    assert ic["size"] == 0
    assert ic["age_seconds"] is None


def test_statsz_indexers_cache_age_numeric_when_seeded(client):
    m._INDEXERS_CACHE.clear()
    m._indexers_cache_put([{"id": 1, "name": "demo"}])
    try:
        r = client.get("/statsz")
        assert r.status_code == 200
        ic = r.json()["indexers_cache"]
        assert ic["size"] == 1
        assert isinstance(ic["age_seconds"], int)
    finally:
        m._INDEXERS_CACHE.clear()


def test_statsz_indexers_returns_expected_shape(client):
    m._INDEXERS_CACHE.clear()
    r = client.get("/statsz/indexers")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["indexers"], list)
    assert isinstance(data["generated_at"], (int, float))


def test_statsz_indexers_empty_when_cache_miss(client):
    m._INDEXERS_CACHE.clear()
    r = client.get("/statsz/indexers")
    assert r.status_code == 200
    assert r.json()["indexers"] == []


def test_statsz_indexers_maps_seeded_indexers_with_zeroed_metrics(client):
    m._INDEXERS_CACHE.clear()
    m._indexers_cache_put([{"id": 1, "name": "demo", "protocol": "torrent", "enable": True, "supportsSearch": True}])
    try:
        r = client.get("/statsz/indexers")
        assert r.status_code == 200
        data = r.json()
        assert len(data["indexers"]) == 1
        entry = data["indexers"][0]
        assert entry["id"] == 1
        assert entry["name"] == "demo"
        assert entry["protocol"] == "torrent"
        assert entry["enabled"] is True
        assert entry["supportsSearch"] is True
        for field in ("requests", "avg_latency_ms", "last_latency_ms", "cached", "uncached", "errors"):
            assert entry[field] == 0, f"expected {field} to be 0"
    finally:
        m._INDEXERS_CACHE.clear()


def test_statsz_indexers_returns_real_counters(client):
    m._INDEXERS_CACHE.clear()
    m._indexers_cache_put([{"id": 1, "name": "demo", "protocol": "torrent", "enable": True, "supportsSearch": True}])
    state._INDEXER_STATS.clear()
    state.record_indexer_stat(1, 50.0)
    state.record_indexer_cache_attribution(1, 1, 2)
    try:
        r = client.get("/statsz/indexers")
        assert r.status_code == 200
        data = r.json()
        assert len(data["indexers"]) == 1
        entry = data["indexers"][0]
        assert entry["id"] == 1
        assert entry["requests"] == 1
        assert entry["avg_latency_ms"] == 50
        assert entry["last_latency_ms"] == 50
        assert entry["cached"] == 1
        assert entry["uncached"] == 2
        assert entry["errors"] == 0
    finally:
        m._INDEXERS_CACHE.clear()
        state._INDEXER_STATS.clear()


def test_statsz_indexers_ignores_stats_when_per_indexer_disabled(client):
    m._INDEXERS_CACHE.clear()
    m._indexers_cache_put([{"id": 1, "name": "demo", "enable": True, "supportsSearch": True}])
    state._INDEXER_STATS.clear()
    settings.set_override("STATS_PER_INDEXER_ENABLED", False)
    try:
        state.record_indexer_stat(1, 50.0)
        assert state._INDEXER_STATS == {}
        r = client.get("/statsz/indexers")
        assert r.status_code == 200
        entry = r.json()["indexers"][0]
        assert entry["requests"] == 0
        assert entry["avg_latency_ms"] == 0
        assert entry["last_latency_ms"] == 0
    finally:
        settings.set_override("STATS_PER_INDEXER_ENABLED", None)
        m._INDEXERS_CACHE.clear()
        state._INDEXER_STATS.clear()


def test_statsz_searches_returns_expected_shape(client):
    r = client.get("/statsz/searches")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["generated_at"], (int, float))
    assert isinstance(data["searches"], list)
    assert data["searches"] == []


def test_statsz_searches_lists_history_most_recent_first(client):
    state._SEARCH_HISTORY.clear()
    state.record_search({"ts": 1.0, "query": "old", "search_type": "search",
                         "latency_ms": 10.0, "torbox_cached": 0,
                         "torbox_uncached": 1, "indexer_count": 1})
    state.record_search({"ts": 2.0, "query": "new", "search_type": "search",
                         "latency_ms": 20.0, "torbox_cached": 1,
                         "torbox_uncached": 0, "indexer_count": 2})
    try:
        r = client.get("/statsz/searches")
        assert r.status_code == 200
        data = r.json()
        assert len(data["searches"]) == 2
        assert data["searches"][0]["query"] == "new"
        assert data["searches"][1]["query"] == "old"
    finally:
        state._SEARCH_HISTORY.clear()
