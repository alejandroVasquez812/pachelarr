"""Tests for lookup_title_from_id (ID -> title via TMDB) caching.

Covers the ``_id_title_cache_get`` / ``_id_title_cache_put`` helpers and that
``lookup_title_from_id`` returns a cached title on a repeat without hitting the
TMDB API.

Run from the repo root so `import main` resolves.
"""
import asyncio

import aiohttp

import main as m
from pachelarr import settings, state, tmdb
from tests._fakes import FakeCtx


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class FakeGetSession:
    """Routes GETs by URL substring to canned responses and records call order."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        for sub, status, data in self.routes:
            if sub in url:
                return FakeCtx(status, data)
        return FakeCtx(200, {})


def _enable(monkeypatch):
    settings.set_override("TMDB_API_KEY", "test-key")
    state._TMDB_TITLE_CACHE.clear()


# ---------------------------------------------------------------------------
# Cache helper unit tests
# ---------------------------------------------------------------------------

def test_id_title_cache_get_miss():
    state._TMDB_TITLE_CACHE.clear()
    assert tmdb._id_title_cache_get("imdbid", "1375666", "movie") is None


def test_id_title_cache_put_and_get_roundtrip():
    state._TMDB_TITLE_CACHE.clear()
    tmdb._id_title_cache_put("imdbid", "1375666", "movie", "Inception 2010")
    assert tmdb._id_title_cache_get("imdbid", "1375666", "movie") == "Inception 2010"


def test_id_title_cache_is_namespaced_from_title_keys():
    """ID->title keys do not collide with title->ID keys in the same cache."""
    state._TMDB_TITLE_CACHE.clear()
    tmdb._id_title_cache_put("imdbid", "1375666", "movie", "Inception 2010")
    # The title->ID cache stores an ids dict under a (lower, year, type) key.
    tmdb._tmdb_title_cache_put(("inception 2010", "2010", "movie"), {"tmdbid": "1"})
    assert tmdb._id_title_cache_get("imdbid", "1375666", "movie") == "Inception 2010"
    # The ids-dict entry must still be retrievable via the title->ID getter.
    assert tmdb._tmdb_title_cache_get(("inception 2010", "2010", "movie")) == {"tmdbid": "1"}


def test_id_title_cache_expiry():
    state._TMDB_TITLE_CACHE.clear()
    tmdb._id_title_cache_put("imdbid", "1", "movie", "Foo 1999")
    key = tmdb._id_title_cache_key("imdbid", "1", "movie")
    # Force expiry.
    state._TMDB_TITLE_CACHE[key]["expires"] = 0
    assert tmdb._id_title_cache_get("imdbid", "1", "movie") is None


def test_id_title_cache_put_ignores_empty_title():
    state._TMDB_TITLE_CACHE.clear()
    tmdb._id_title_cache_put("imdbid", "1", "movie", "")
    assert len(state._TMDB_TITLE_CACHE) == 0


def test_id_title_cache_put_evicts_beyond_max(monkeypatch):
    state._TMDB_TITLE_CACHE.clear()
    settings.set_override("TMDB_TITLE_LOOKUP_CACHE_MAX", 2)
    try:
        tmdb._id_title_cache_put("imdbid", "1", "movie", "A")
        tmdb._id_title_cache_put("imdbid", "2", "movie", "B")
        tmdb._id_title_cache_put("imdbid", "3", "movie", "C")
        assert len(state._TMDB_TITLE_CACHE) == 2
        assert tmdb._id_title_cache_get("imdbid", "1", "movie") is None
        assert tmdb._id_title_cache_get("imdbid", "3", "movie") == "C"
    finally:
        settings.set_override("TMDB_TITLE_LOOKUP_CACHE_MAX", None)


# ---------------------------------------------------------------------------
# lookup_title_from_id caching integration
# ---------------------------------------------------------------------------

def test_lookup_title_from_id_caches_and_reuses(monkeypatch):
    """A successful ID lookup is cached; a repeat returns it without API calls."""
    _enable(monkeypatch)
    session = FakeGetSession([
        ("/find/tt1375666", 200, {
            "movie_results": [{"title": "Inception", "release_date": "2010-07-16"}],
        }),
    ])
    title1 = _run(m.lookup_title_from_id(session, imdbid="1375666", search_type='movie'))
    assert title1 == "Inception 2010"
    assert len(session.urls) == 1

    # Second call: same id + search_type -> cache hit, no network.
    session2 = FakeGetSession([("/find/tt1375666", 200, {
        "movie_results": [{"title": "WRONG", "release_date": "2099-01-01"}],
    })])
    title2 = _run(m.lookup_title_from_id(session2, imdbid="1375666", search_type='movie'))
    assert title2 == "Inception 2010"
    assert session2.urls == [], session2.urls


def test_lookup_title_from_id_caches_tv_via_tvdbid(monkeypatch):
    _enable(monkeypatch)
    session = FakeGetSession([
        ("/find/81189", 200, {
            "tv_results": [{"name": "Breaking Bad", "first_air_date": "2008-01-20"}],
        }),
    ])
    title1 = _run(m.lookup_title_from_id(session, tvdbid="81189", search_type='tvsearch'))
    assert title1 == "Breaking Bad 2008"

    session2 = FakeGetSession([("/find/81189", 200, {"tv_results": []})])
    title2 = _run(m.lookup_title_from_id(session2, tvdbid="81189", search_type='tvsearch'))
    assert title2 == "Breaking Bad 2008"
    assert session2.urls == [], session2.urls


def test_lookup_title_from_id_cache_key_includes_search_type(monkeypatch):
    """The same tmdbid cached as movie vs tv are distinct entries."""
    _enable(monkeypatch)
    session = FakeGetSession([
        ("/movie/550", 200, {"title": "Fight Club", "release_date": "1999-10-15"}),
    ])
    title_movie = _run(m.lookup_title_from_id(session, tmdbid="550", search_type='movie'))
    assert title_movie == "Fight Club 1999"

    # A tvsearch lookup for the same id must NOT hit the movie cache entry.
    session_tv = FakeGetSession([
        ("/tv/550", 200, {"name": "Some Show", "first_air_date": "2001-03-01"}),
    ])
    title_tv = _run(m.lookup_title_from_id(session_tv, tmdbid="550", search_type='tvsearch'))
    assert title_tv == "Some Show 2001"
    assert len(session_tv.urls) == 1


def test_lookup_title_from_id_no_api_key_skips_cache_and_returns_none(monkeypatch):
    state._TMDB_TITLE_CACHE.clear()
    settings.set_override("TMDB_API_KEY", "")
    session = FakeGetSession([("/find/tt1", 200, {})])
    assert _run(m.lookup_title_from_id(session, imdbid="1", search_type='movie')) is None
    assert session.urls == [], session.urls
