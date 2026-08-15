"""Tests for the TVDB v4 API client (pachelarr/tvdb.py).

Covers:
- Login token acquisition + reuse (no re-login within the refresh window).
- tvdbid -> title via GET /v4/series/{id}.
- imdbid -> title (TV) via GET /v4/search/remoteid/tt{id}.
- title -> tvdbid via GET /v4/search?type=series.
- TVDB failure (non-200 / empty) -> returns None (caller falls back to TMDB).
- No TVDB_API_KEY -> no TVDB calls (returns None immediately).
- PIN included in login body only when set.

Run from the repo root so `import main` resolves.
"""
import asyncio
import base64
import json
import time

import main as m
from pachelarr import settings, state, tvdb
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


class FakeTvdbSession:
    """Routes GETs and POSTs by URL substring to canned responses.

    Records every URL requested (in order) so tests can assert call sequences.
    ``get_routes`` and ``post_routes`` are lists of (url_substring, FakeCtx).
    The first matching route is used; subsequent matches of the same substring
    reuse the same FakeCtx. ``post_bodies`` captures the JSON body of each POST
    so tests can assert login payload contents.
    """

    def __init__(self, get_routes=None, post_routes=None):
        self.get_routes = get_routes or []
        self.post_routes = post_routes or []
        self.urls = []
        self.post_bodies = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        for sub, ctx in self.get_routes:
            if sub in url:
                return ctx
        return FakeCtx(404, {})

    def post(self, url, json=None, timeout=None):
        self.urls.append(url)
        self.post_bodies.append(json)
        for sub, ctx in self.post_routes:
            if sub in url:
                return ctx
        return FakeCtx(404, {})


def _make_jwt(exp_delta=86400):
    """Build a minimal JWT string with an ``exp`` claim for token decode tests."""
    header = b'{"alg":"HS256","typ":"JWT"}'
    payload = json.dumps({"exp": int(time.time()) + exp_delta}).encode()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return f"{b64(header)}.{b64(payload)}.sig"


def _enable_tvdb(pin=None):
    settings.set_override("TVDB_API_KEY", "tvdb-test-key")
    if pin is not None:
        settings.set_override("TVDB_API_PIN", pin)
    state._TVDB_TOKEN["token"] = None
    state._TVDB_TOKEN["expires_at"] = 0.0


def _disable_tvdb():
    settings.set_override("TVDB_API_KEY", None)
    settings.set_override("TVDB_API_PIN", None)
    state._TVDB_TOKEN["token"] = None
    state._TVDB_TOKEN["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def test_login_acquires_and_caches_token():
    """A GET triggers login first; a second GET reuses the token (no re-login)."""
    _enable_tvdb()
    token = _make_jwt()
    login_ctx = FakeCtx(200, {"token": token})
    series_ctx = FakeCtx(200, {"data": {"name": "Breaking Bad", "year": "2008"}})
    session = FakeTvdbSession(
        get_routes=[("/series/", series_ctx)],
        post_routes=[("/login", login_ctx)],
    )
    title1 = _run(tvdb.lookup_title_from_id(session, tvdbid="81189"))
    assert title1 == "Breaking Bad 2008"
    # One login POST + one series GET.
    assert len(session.urls) == 2

    # Second call: token still valid -> no re-login.
    session2 = FakeTvdbSession(
        get_routes=[("/series/", FakeCtx(200, {"data": {"name": "Breaking Bad", "year": "2008"}}))],
        post_routes=[("/login", FakeCtx(200, {"token": "WRONG"}))],
    )
    title2 = _run(tvdb.lookup_title_from_id(session2, tvdbid="81189"))
    assert title2 == "Breaking Bad 2008"
    # No POST this time, just the GET.
    assert len(session2.urls) == 1
    assert "/login" not in session2.urls[0]
    _disable_tvdb()


def test_login_includes_pin_when_set():
    """When TVDB_API_PIN is set, the login POST body includes it."""
    _enable_tvdb(pin="1234")
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/series/", FakeCtx(200, {"data": {"name": "Foo", "year": "2020"}}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    _run(tvdb.lookup_title_from_id(session, tvdbid="1"))
    assert len(session.post_bodies) == 1
    body = session.post_bodies[0]
    assert body["apikey"] == "tvdb-test-key"
    assert body["pin"] == "1234"
    _disable_tvdb()


def test_login_omits_pin_when_not_set():
    """When TVDB_API_PIN is empty, the login POST body has no pin key."""
    _enable_tvdb(pin="")
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/series/", FakeCtx(200, {"data": {"name": "Foo", "year": "2020"}}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    _run(tvdb.lookup_title_from_id(session, tvdbid="1"))
    body = session.post_bodies[0]
    assert "pin" not in body
    assert body["apikey"] == "tvdb-test-key"
    _disable_tvdb()


def test_login_failure_returns_none():
    """A failed login (non-200) means TVDB returns None (fall back to TMDB)."""
    _enable_tvdb()
    session = FakeTvdbSession(
        get_routes=[("/series/", FakeCtx(200, {"data": {"name": "X"}}))],
        post_routes=[("/login", FakeCtx(401, {}))],
    )
    result = _run(tvdb.lookup_title_from_id(session, tvdbid="1"))
    assert result is None
    _disable_tvdb()


# ---------------------------------------------------------------------------
# ID -> title lookups
# ---------------------------------------------------------------------------

def test_tvdbid_to_title():
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/series/81189", FakeCtx(200, {"data": {"name": "Breaking Bad", "year": "2008"}}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, tvdbid="81189"))
    assert title == "Breaking Bad 2008"
    _disable_tvdb()


def test_tvdbid_to_title_no_year():
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/series/1", FakeCtx(200, {"data": {"name": "Some Show"}}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, tvdbid="1"))
    assert title == "Some Show"
    _disable_tvdb()


def test_imdbid_to_title_tv():
    """imdbid lookup for TV uses /search/remoteid/tt{id} and extracts series."""
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/search/remoteid/tt0903747", FakeCtx(
            200, {"data": {"series": {"name": "Breaking Bad", "year": "2008"}}}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, imdbid="0903747", search_type='tvsearch'))
    assert title == "Breaking Bad 2008"
    _disable_tvdb()


def test_imdbid_to_title_movie_search_type_skipped():
    """imdbid with search_type='movie' is not TV-eligible -> returns None."""
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/search/remoteid/", FakeCtx(200, {"data": {"series": {"name": "X"}}}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, imdbid="0903747", search_type='movie'))
    assert title is None
    # No TVDB calls at all (not even login) since movie search_type skips TVDB.
    assert session.urls == []
    _disable_tvdb()


def test_tvdb_failure_returns_none():
    """A non-200 series response -> None (caller falls back)."""
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/series/999", FakeCtx(404, {}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, tvdbid="999"))
    assert title is None
    _disable_tvdb()


def test_empty_series_data_returns_none():
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/series/1", FakeCtx(200, {"data": None}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, tvdbid="1"))
    assert title is None
    _disable_tvdb()


def test_no_api_key_returns_none_without_calls():
    """Without TVDB_API_KEY, no network calls are made."""
    _disable_tvdb()
    session = FakeTvdbSession()
    title = _run(tvdb.lookup_title_from_id(session, tvdbid="81189"))
    assert title is None
    assert session.urls == []


# ---------------------------------------------------------------------------
# title -> tvdbid lookups
# ---------------------------------------------------------------------------

def test_title_to_tvdbid():
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/search?", FakeCtx(200, {"data": [{"id": 81189, "name": "Breaking Bad"}]}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    tvdbid = _run(tvdb.lookup_tvdbid_from_title(session, "Breaking Bad"))
    assert tvdbid == "81189"
    _disable_tvdb()


def test_title_to_tvdbid_with_year():
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/search?", FakeCtx(200, {"data": [{"id": 81189, "name": "Breaking Bad"}]}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    tvdbid = _run(tvdb.lookup_tvdbid_from_title(session, "Breaking Bad", year="2008"))
    assert tvdbid == "81189"
    # Verify year was passed in the URL.
    assert any("year=2008" in u for u in session.urls), session.urls
    _disable_tvdb()


def test_title_to_tvdbid_empty_results():
    _enable_tvdb()
    token = _make_jwt()
    session = FakeTvdbSession(
        get_routes=[("/search?", FakeCtx(200, {"data": []}))],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    tvdbid = _run(tvdb.lookup_tvdbid_from_title(session, "Nonexistent Show"))
    assert tvdbid is None
    _disable_tvdb()


def test_title_to_tvdbid_no_api_key():
    _disable_tvdb()
    session = FakeTvdbSession()
    tvdbid = _run(tvdb.lookup_tvdbid_from_title(session, "Breaking Bad"))
    assert tvdbid is None
    assert session.urls == []


# ---------------------------------------------------------------------------
# 401 retry
# ---------------------------------------------------------------------------

def test_401_triggers_relogin_and_retry():
    """A 401 on the first GET invalidates the token, re-logs in, and retries."""
    _enable_tvdb()
    token = _make_jwt()
    # First login succeeds, then series GET returns 401, then re-login succeeds,
    # then the retry GET succeeds.
    call_count = {"series": 0}

    def series_ctx_factory():
        call_count["series"] += 1
        if call_count["series"] == 1:
            return FakeCtx(401, {})
        return FakeCtx(200, {"data": {"name": "Breaking Bad", "year": "2008"}})

    class DynamicSeriesCtx:
        def __init__(self):
            self.status = 401

        async def __aenter__(self):
            ctx = series_ctx_factory()
            self.status = ctx.status
            self._data = ctx._data
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return self._data

    session = FakeTvdbSession(
        get_routes=[("/series/", DynamicSeriesCtx())],
        post_routes=[("/login", FakeCtx(200, {"token": token}))],
    )
    title = _run(tvdb.lookup_title_from_id(session, tvdbid="81189"))
    assert title == "Breaking Bad 2008"
    # Should have: login, series GET (401), re-login, series GET (200) = 4 calls.
    assert len(session.urls) == 4
    _disable_tvdb()