"""Tests for admin action endpoints: param overrides, cache invalidation, stats reset.

Covers auth (missing/wrong key), param override CRUD + merge into
build_per_indexer_params, indexer cache invalidation (in-memory + SQLite),
and stats reset (all indexers, single indexer, search history).
"""
import pytest
from fastapi.testclient import TestClient

import main as m
from pachelarr import db, settings, state

_REQUIRED = ("PROWLARR_URL", "PROWLARR_API_KEY", "TORBOX_API_KEY")


@pytest.fixture
def client():
    saved = {name: settings.get_typed(name) for name in _REQUIRED + ("PACHELARR_API_KEY",)}
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    settings.set_override("PACHELARR_API_KEY", "admin-key")
    # Ensure clean override state for each test.
    state._PARAM_OVERRIDES.clear()
    try:
        with TestClient(m.app) as c:
            # Lifespan has now run db.init(); clean persisted overrides.
            for scope in ("global", "indexer:1", "indexer:2"):
                db.delete_param_overrides(scope)
            state._PARAM_OVERRIDES.clear()
            yield c
    finally:
        state._PARAM_OVERRIDES.clear()
        for name, val in saved.items():
            settings.set_override(name, val)


AUTH = {"X-Api-Key": "admin-key"}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def test_overrides_get_requires_auth(client):
    assert client.get("/overrides").status_code == 401


def test_overrides_put_requires_auth(client):
    assert client.put("/overrides", json={}).status_code == 401


def test_overrides_delete_requires_auth(client):
    assert client.delete("/overrides").status_code == 401


def test_cache_invalidate_requires_auth(client):
    assert client.post("/cache/indexers/invalidate").status_code == 401


def test_stats_reset_requires_auth(client):
    assert client.post("/statsz/reset").status_code == 401


def test_stats_reset_indexers_requires_auth(client):
    assert client.post("/statsz/reset/indexers").status_code == 401


def test_stats_reset_searches_requires_auth(client):
    assert client.post("/statsz/reset/searches").status_code == 401


# --------------------------------------------------------------------------- #
# Param overrides CRUD
# --------------------------------------------------------------------------- #

def test_put_global_overrides(client):
    body = {"scope": "global", "params": {"cat": ["2000"], "limit": "50"}}
    r = client.put("/overrides", json=body, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == "global"
    assert data["overrides"]["global"]["cat"] == ["2000"]


def test_put_per_indexer_overrides(client):
    body = {"scope": "indexer:5", "params": {"cat": ["5000"]}}
    r = client.put("/overrides", json=body, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["overrides"]["indexer:5"]["cat"] == ["5000"]


def test_get_overrides_empty(client):
    r = client.get("/overrides", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {}


def test_get_overrides_after_put(client):
    client.put("/overrides", json={"scope": "global", "params": {"q": "test"}}, headers=AUTH)
    r = client.get("/overrides", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["global"]["q"] == "test"


def test_delete_overrides(client):
    client.put("/overrides", json={"scope": "global", "params": {"q": "test"}}, headers=AUTH)
    r = client.delete("/overrides?scope=global", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["deleted"] == "global"
    assert "global" not in r.json()["overrides"]


def test_put_overrides_invalid_scope(client):
    r = client.put("/overrides", json={"scope": "bad", "params": {}}, headers=AUTH)
    assert r.status_code == 400


def test_put_overrides_missing_scope(client):
    r = client.put("/overrides", json={"params": {}}, headers=AUTH)
    assert r.status_code == 400


def test_put_overrides_missing_params(client):
    r = client.put("/overrides", json={"scope": "global"}, headers=AUTH)
    assert r.status_code == 400


def test_put_overrides_empty_params_deletes(client):
    client.put("/overrides", json={"scope": "global", "params": {"q": "x"}}, headers=AUTH)
    r = client.put("/overrides", json={"scope": "global", "params": {}}, headers=AUTH)
    assert r.status_code == 200
    assert "global" not in r.json()["overrides"]


def test_delete_overrides_missing_scope(client):
    r = client.delete("/overrides", headers=AUTH)
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Override merge in build_per_indexer_params
# --------------------------------------------------------------------------- #

def test_global_overrides_applied_to_build_params():
    from pachelarr.prowlarr import build_per_indexer_params
    state._PARAM_OVERRIDES["global"] = {"cat": ["2000"]}
    indexer = {"id": 1, "capabilities": {"searchParams": ["q"]}}
    params = build_per_indexer_params(indexer, {"query": "test", "type": "search"})
    assert params is not None
    assert params.get("cat") == ["2000"]
    assert params.get("query") == "test"


def test_per_indexer_overrides_win_over_global():
    from pachelarr.prowlarr import build_per_indexer_params
    state._PARAM_OVERRIDES["global"] = {"cat": ["2000"]}
    state._PARAM_OVERRIDES["indexer:1"] = {"cat": ["5000"]}
    indexer = {"id": 1, "capabilities": {"searchParams": ["q"]}}
    params = build_per_indexer_params(indexer, {"query": "test", "type": "search"})
    assert params is not None
    assert params.get("cat") == ["5000"]


def test_no_overrides_no_change():
    from pachelarr.prowlarr import build_per_indexer_params
    indexer = {"id": 1, "capabilities": {"searchParams": ["q"]}}
    params = build_per_indexer_params(indexer, {"query": "test", "type": "search"})
    assert params is not None
    assert "cat" not in params


# --------------------------------------------------------------------------- #
# Cache invalidation
# --------------------------------------------------------------------------- #

def test_invalidate_indexers_cache_clears_memory(client):
    state._INDEXERS_CACHE["listing"] = {"indexers": [{"id": 1}], "expires": 9999999999}
    r = client.post("/cache/indexers/invalidate", headers=AUTH)
    assert r.status_code == 200
    assert len(state._INDEXERS_CACHE) == 0


def test_invalidate_indexers_cache_clears_db(client):
    # Insert a cache row directly
    with db._lock:
        db._require_conn().execute(
            "INSERT OR REPLACE INTO cache_indexers (id, indexers_json, expires) VALUES (1, '[]', 9999999999)"
        )
    client.post("/cache/indexers/invalidate", headers=AUTH)
    with db._lock:
        row = db._require_conn().execute("SELECT * FROM cache_indexers WHERE id = 1").fetchone()
    assert row is None


# --------------------------------------------------------------------------- #
# Stats reset
# --------------------------------------------------------------------------- #

def test_reset_all_stats(client):
    state._INDEXER_STATS[1] = {"requests": 10, "errors": 2, "total_latency_ms": 500.0,
                               "last_latency_ms": 50.0, "cached": 3, "uncached": 7}
    state._SEARCH_HISTORY.append({"ts": 1, "query": "test"})
    r = client.post("/statsz/reset", headers=AUTH)
    assert r.status_code == 200
    assert len(state._INDEXER_STATS) == 0
    assert len(state._SEARCH_HISTORY) == 0


def test_reset_indexer_stats_all(client):
    state._INDEXER_STATS[1] = {"requests": 5, "errors": 0, "total_latency_ms": 100.0,
                               "last_latency_ms": 20.0, "cached": 1, "uncached": 4}
    state._INDEXER_STATS[2] = {"requests": 3, "errors": 1, "total_latency_ms": 60.0,
                               "last_latency_ms": 20.0, "cached": 0, "uncached": 3}
    r = client.post("/statsz/reset/indexers", headers=AUTH)
    assert r.status_code == 200
    assert len(state._INDEXER_STATS) == 0


def test_reset_single_indexer_stats(client):
    state._INDEXER_STATS[1] = {"requests": 5, "errors": 0, "total_latency_ms": 100.0,
                               "last_latency_ms": 20.0, "cached": 1, "uncached": 4}
    state._INDEXER_STATS[2] = {"requests": 3, "errors": 1, "total_latency_ms": 60.0,
                               "last_latency_ms": 20.0, "cached": 0, "uncached": 3}
    r = client.post("/statsz/reset/indexers/1", headers=AUTH)
    assert r.status_code == 200
    assert 1 not in state._INDEXER_STATS
    assert 2 in state._INDEXER_STATS


def test_reset_single_indexer_invalid_id(client):
    r = client.post("/statsz/reset/indexers/abc", headers=AUTH)
    assert r.status_code == 400


def test_reset_search_history(client):
    state._SEARCH_HISTORY.append({"ts": 1, "query": "a"})
    state._SEARCH_HISTORY.append({"ts": 2, "query": "b"})
    r = client.post("/statsz/reset/searches", headers=AUTH)
    assert r.status_code == 200
    assert len(state._SEARCH_HISTORY) == 0


def test_reset_indexer_stats_clears_db(client):
    db.upsert_indexer_stats(99, 10, 2, 500.0, 50.0, 3, 7)
    client.post("/statsz/reset/indexers", headers=AUTH)
    stats = db.load_indexer_stats()
    assert 99 not in stats


def test_reset_single_indexer_stats_clears_db(client):
    db.upsert_indexer_stats(50, 10, 2, 500.0, 50.0, 3, 7)
    db.upsert_indexer_stats(51, 5, 1, 300.0, 60.0, 2, 3)
    client.post("/statsz/reset/indexers/50", headers=AUTH)
    stats = db.load_indexer_stats()
    assert 50 not in stats
    assert 51 in stats


def test_reset_searches_clears_db(client):
    db.insert_search({"ts": 1, "query": "x", "search_type": "search",
                      "latency_ms": 10, "torbox_cached": 1, "torbox_uncached": 0,
                      "indexer_count": 1})
    client.post("/statsz/reset/searches", headers=AUTH)
    searches = db.load_searches(100)
    assert len(searches) == 0