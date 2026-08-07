"""Tests for /healthz and /statsz endpoints (improvement #2).

Uses FastAPI's TestClient, which runs the event loop + lifespan internally.
The lifespan env-validation (improvement #4) raises RuntimeError if the
required module globals (PROWLARR_URL, PROWLARR_API_KEY, TORBOX_API_KEY) are
unset, so we set them on the module BEFORE constructing the client.

Run from the repo root so ``import main`` resolves.
"""
import pytest
from fastapi.testclient import TestClient

import main as m

_REQUIRED = ("PROWLARR_URL", "PROWLARR_API_KEY", "TORBOX_API_KEY")


@pytest.fixture
def client():
    saved = {name: getattr(m, name) for name in _REQUIRED}
    for name in _REQUIRED:
        setattr(m, name, "k")
    m.PROWLARR_URL = "http://x"
    m.PROWLARR_API_KEY = "k"
    m.TORBOX_API_KEY = "k"
    m._INDEXERS_CACHE.clear()
    try:
        with TestClient(m.app) as c:
            yield c
    finally:
        m._INDEXERS_CACHE.clear()
        for name, val in saved.items():
            setattr(m, name, val)


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
