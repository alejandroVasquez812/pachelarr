"""Tests for lookup_identifier_from_query (title -> IDs via TMDB).

Reverse of lookup_title_from_id: given a title query (optionally with a
trailing year), resolve imdbid/tvdbid/tmdbid via TMDB search + external_ids so
search_prowlarr can emit {imdbid:..}/{tvdbid:..} tokens for ID-only indexers.

Requires both TMDB_API_KEY and TMDB_TITLE_LOOKUP_ENABLED. imdbid is stored
WITHOUT the 'tt' prefix to match the codebase convention.
"""
import asyncio

import aiohttp

import main as m
from tests._fakes import FakeCtx


async def _hs(params):
    async with aiohttp.ClientSession() as session:
        return await m.handle_search(params, session)


class FakeSession:
    """Routes GETs by URL substring to canned responses and records call order.

    `routes` is a list of (url_substring, status, data) tuples matched in order.
    Each route is consumed once; a second call with the same substring reuses
    the last matching route. `urls` records every URL requested in order.
    """

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.urls.append(url)
        for sub, status, data in self.routes:
            if sub in url:
                return FakeCtx(status, data)
        # Default: empty results so callers handle the no-match case
        return FakeCtx(200, {'results': []})


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_FAKE_XML_PAIRS = [({"id": 1}, b'<rss><channel><item><title>T1</title>'
                    b'<link>magnet:?xt=urn:btih:aaa111</link><guid>magnet:?xt=urn:btih:aaa111</guid>'
                    b'<enclosure url="magnet:?xt=urn:btih:aaa111" type="application/x-bittorrent"/>'
                    b'<torznab:attr name="seeders" value="1"/><torznab:attr name="peers" value="0"/>'
                    b'<torznab:attr name="infohash" value="aaa111"/></item></channel></rss>')]


def _enable_title_lookup(monkeypatch):
    monkeypatch.setattr(m, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(m, "TMDB_TITLE_LOOKUP_ENABLED", True)
    m._TMDB_TITLE_CACHE.clear()


def test_lookup_returns_movie_imdbid(monkeypatch):
    """Movie search resolves tmdbid + imdbid (tt prefix stripped). Year passed to search."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/movie", 200, {'results': [{'id': 27205, 'title': 'Inception'}]}),
        ("/movie/27205/external_ids", 200, {'imdb_id': 'tt1375666'}),
    ])
    ids = _run(m.lookup_identifier_from_query(session, "Inception 2010", search_type='movie'))
    assert ids == {'tmdbid': '27205', 'imdbid': '1375666'}, ids
    # year was parsed and passed to the search endpoint
    assert any("year=2010" in u for u in session.urls), session.urls
    assert len(session.urls) == 2, session.urls


def test_lookup_returns_tv_tvdbid_and_imdbid(monkeypatch):
    """TV search resolves tmdbid + imdbid (tt stripped) + tvdbid (int -> str)."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/tv", 200, {'results': [{'id': 1396, 'name': 'Breaking Bad'}]}),
        ("/tv/1396/external_ids", 200, {'imdb_id': 'tt0903747', 'tvdb_id': 81189}),
    ])
    ids = _run(m.lookup_identifier_from_query(session, "Breaking Bad", search_type='tvsearch'))
    assert ids == {'tmdbid': '1396', 'imdbid': '0903747', 'tvdbid': '81189'}, ids


def test_lookup_disabled_by_default(monkeypatch):
    """With TMDB_TITLE_LOOKUP_ENABLED=False, returns None and makes no calls."""
    monkeypatch.setattr(m, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(m, "TMDB_TITLE_LOOKUP_ENABLED", False)
    m._TMDB_TITLE_CACHE.clear()
    session = FakeSession([("/search/movie", 200, {'results': [{'id': 1}]})])
    ids = _run(m.lookup_identifier_from_query(session, "Inception 2010", search_type='movie'))
    assert ids is None
    assert session.urls == [], session.urls


def test_lookup_no_api_key(monkeypatch):
    """With TMDB_API_KEY empty + enabled, returns None and makes no calls."""
    monkeypatch.setattr(m, "TMDB_API_KEY", "")
    monkeypatch.setattr(m, "TMDB_TITLE_LOOKUP_ENABLED", True)
    m._TMDB_TITLE_CACHE.clear()
    session = FakeSession([("/search/movie", 200, {'results': [{'id': 1}]})])
    ids = _run(m.lookup_identifier_from_query(session, "Inception 2010", search_type='movie'))
    assert ids is None
    assert session.urls == [], session.urls


def test_lookup_no_tmdb_results_returns_none(monkeypatch):
    """Empty search results -> None, no external_ids call."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([("/search/movie", 200, {'results': []})])
    ids = _run(m.lookup_identifier_from_query(session, "Nonexistent Title", search_type='movie'))
    assert ids is None
    # only the search call happened, not external_ids
    assert len(session.urls) == 1, session.urls


def test_lookup_partial_ids_when_imdb_null(monkeypatch):
    """If external_ids has null imdb_id, only tmdbid is returned (partial is OK)."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/movie", 200, {'results': [{'id': 550}]}),
        ("/movie/550/external_ids", 200, {'imdb_id': None}),
    ])
    ids = _run(m.lookup_identifier_from_query(session, "Fight Club", search_type='movie'))
    assert ids == {'tmdbid': '550'}, ids


def test_lookup_cache_hit_skips_network(monkeypatch):
    """Second call with same (title, year, search_type) hits cache, no .get calls."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/movie", 200, {'results': [{'id': 27205}]}),
        ("/movie/27205/external_ids", 200, {'imdb_id': 'tt1375666'}),
    ])
    ids1 = _run(m.lookup_identifier_from_query(session, "Inception 2010", search_type='movie'))
    assert ids1 == {'tmdbid': '27205', 'imdbid': '1375666'}
    first_call_count = len(session.urls)
    assert first_call_count == 2, session.urls
    # Second call: cache hit, no network
    session2 = FakeSession([("/search/movie", 200, {'results': [{'id': 999}]})])
    ids2 = _run(m.lookup_identifier_from_query(session2, "Inception 2010", search_type='movie'))
    assert ids2 == ids1, (ids1, ids2)
    assert session2.urls == [], session2.urls


def test_lookup_cache_key_includes_search_type(monkeypatch):
    """Same title as movie vs tv are different cache entries (no collision)."""
    _enable_title_lookup(monkeypatch)
    # Movie lookup populates cache for ('inception 2010', '2010', 'movie')
    session_m = FakeSession([
        ("/search/movie", 200, {'results': [{'id': 27205}]}),
        ("/movie/27205/external_ids", 200, {'imdb_id': 'tt1375666'}),
    ])
    ids_m = _run(m.lookup_identifier_from_query(session_m, "Inception 2010", search_type='movie'))
    assert 'imdbid' in ids_m
    # TV lookup with same title must NOT hit the movie cache entry
    session_t = FakeSession([
        ("/search/tv", 200, {'results': [{'id': 1396}]}),
        ("/tv/1396/external_ids", 200, {'imdb_id': 'tt0903747', 'tvdb_id': 81189}),
    ])
    ids_t = _run(m.lookup_identifier_from_query(session_t, "Inception 2010", search_type='tvsearch'))
    # TV path ran its own network calls (not a cache hit)
    assert len(session_t.urls) == 2, session_t.urls
    assert 'tvdbid' in ids_t, ids_t


def test_lookup_no_year_passed_when_absent(monkeypatch):
    """Query without a trailing 4-digit token sends no year param to TMDB."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/tv", 200, {'results': [{'id': 1396}]}),
        ("/tv/1396/external_ids", 200, {'imdb_id': 'tt0903747', 'tvdb_id': 81189}),
    ])
    _run(m.lookup_identifier_from_query(session, "Breaking Bad", search_type='tvsearch'))
    search_url = session.urls[0]
    assert "first_air_date_year" not in search_url, search_url


def test_handle_search_invokes_lookup_for_movie_title(monkeypatch):
    """handle_search calls lookup_identifier_from_query and forwards IDs to search_prowlarr."""
    _enable_title_lookup(monkeypatch)
    from starlette.datastructures import QueryParams

    captured = {}

    async def fake_lookup(session, query, search_type='movie'):
        captured['lookup_called'] = True
        captured['lookup_query'] = query
        captured['lookup_type'] = search_type
        return {'imdbid': '1375666', 'tmdbid': '27205'}

    async def fake_search(session, kwargs):
        captured['search_kwargs'] = dict(kwargs)
        return _FAKE_XML_PAIRS

    monkeypatch.setattr(m, "lookup_identifier_from_query", fake_lookup)
    monkeypatch.setattr(m, "search_prowlarr", fake_search)
    params = QueryParams({"t": "movie", "q": "Inception 2010"})
    resp = _run(_hs(params))
    assert resp.body is not None
    assert captured.get('lookup_called') is True
    assert captured.get('lookup_query') == "Inception 2010"
    assert captured.get('lookup_type') == "movie"
    sk = captured.get('search_kwargs', {})
    assert sk.get('imdbid') == '1375666', sk
    assert sk.get('tmdbid') == '27205', sk
    # original query preserved
    assert sk.get('query') == "Inception 2010", sk


def test_handle_search_skips_lookup_when_identifier_present(monkeypatch):
    """When an identifier is present, lookup_identifier_from_query is NOT called."""
    _enable_title_lookup(monkeypatch)
    from starlette.datastructures import QueryParams

    async def fake_lookup(session, query, search_type='movie'):
        raise AssertionError("lookup should not be called when identifier present")

    async def fake_search(session, kwargs):
        return _FAKE_XML_PAIRS

    monkeypatch.setattr(m, "lookup_identifier_from_query", fake_lookup)
    monkeypatch.setattr(m, "search_prowlarr", fake_search)
    params = QueryParams({"t": "movie", "q": "Some Title", "imdbid": "12345"})
    resp = _run(_hs(params))
    assert resp.body is not None


def test_handle_search_skips_lookup_for_generic_search(monkeypatch):
    """Generic 'search' type does not invoke lookup_identifier_from_query."""
    _enable_title_lookup(monkeypatch)
    from starlette.datastructures import QueryParams

    async def fake_lookup(session, query, search_type='movie'):
        raise AssertionError("lookup should not be called for generic search type")

    async def fake_search(session, kwargs):
        return _FAKE_XML_PAIRS

    monkeypatch.setattr(m, "lookup_identifier_from_query", fake_lookup)
    monkeypatch.setattr(m, "search_prowlarr", fake_search)
    params = QueryParams({"t": "search", "q": "Some Title"})
    resp = _run(_hs(params))
    assert resp.body is not None


def test_strip_foreign_language_tag():
    """Trailing 2-letter origin tag is removed only when it's the final token."""
    assert m.strip_foreign_language_tag("Boys Over Flowers KR") == "Boys Over Flowers"
    assert m.strip_foreign_language_tag("Boys Over Flowers JP") == "Boys Over Flowers"
    assert m.strip_foreign_language_tag("Boys Over Flowers ru") == "Boys Over Flowers"
    # Tag mid-string is NOT stripped (only the trailing standalone token)
    assert m.strip_foreign_language_tag("KR Boys Over Flowers") == "KR Boys Over Flowers"
    # No tag present -> unchanged
    assert m.strip_foreign_language_tag("Boys Over Flowers") == "Boys Over Flowers"
    # Single token (no space) -> unchanged even if it looks like a tag
    assert m.strip_foreign_language_tag("KR") == "KR"
    # Whitespace is trimmed
    assert m.strip_foreign_language_tag("Boys Over Flowers KR   ") == "Boys Over Flowers"
    assert m.strip_foreign_language_tag("Boys Over Flowers   KR") == "Boys Over Flowers"
    # Empty/None passthrough
    assert m.strip_foreign_language_tag("") == ""
    assert m.strip_foreign_language_tag(None) is None
    # Non-tag 2-letter final token (e.g. "US" is in the set but "XY" is not)
    assert m.strip_foreign_language_tag("Some XY") == "Some XY"


def test_strip_foreign_language_tag_after_year(monkeypatch):
    """Year is parsed first, then the tag behind it is stripped for TMDB search."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/tv", 200, {'results': [{'id': 1396}]}),
        ("/tv/1396/external_ids", 200, {'imdb_id': 'tt0903747', 'tvdb_id': 81189}),
    ])
    ids = _run(m.lookup_identifier_from_query(session, "Boys Over Flowers KR 2009", search_type='tvsearch'))
    assert ids == {'tmdbid': '1396', 'imdbid': '0903747', 'tvdbid': '81189'}, ids
    search_url = session.urls[0]
    # Year still forwarded as first_air_date_year
    assert "first_air_date_year=2009" in search_url, search_url
    # The KR tag must not appear in the query portion sent to TMDB search
    query_part = search_url.split("query=")[-1].split("&")[0]
    assert "KR" not in query_part, search_url


def test_lookup_strips_trailing_foreign_language_tag(monkeypatch):
    """Trailing origin tag is stripped before the TMDB search call."""
    _enable_title_lookup(monkeypatch)
    session = FakeSession([
        ("/search/tv", 200, {'results': [{'id': 1396}]}),
        ("/tv/1396/external_ids", 200, {'imdb_id': 'tt0903747', 'tvdb_id': 81189}),
    ])
    _run(m.lookup_identifier_from_query(session, "Boys Over Flowers KR", search_type='tvsearch'))
    search_url = session.urls[0]
    query_part = search_url.split("query=")[-1].split("&")[0]
    assert "KR" not in query_part, search_url


def test_handle_search_skips_lookup_when_no_query(monkeypatch):
    """No query (empty q) does not invoke lookup_identifier_from_query."""
    _enable_title_lookup(monkeypatch)
    from starlette.datastructures import QueryParams

    async def fake_lookup(session, query, search_type='movie'):
        raise AssertionError("lookup should not be called when no query present")

    async def fake_search(session, kwargs):
        return _FAKE_XML_PAIRS

    monkeypatch.setattr(m, "lookup_identifier_from_query", fake_lookup)
    monkeypatch.setattr(m, "search_prowlarr", fake_search)
    # category-only search (no q), with fallback disabled so it returns empty
    monkeypatch.setattr(m, "PACHELARR_TEST_FALLBACK_QUERY", "")
    params = QueryParams({"t": "movie", "cat": "5030"})
    resp = _run(_hs(params))
    # empty feed is returned (no query, no identifier, categories present forwarded)
    assert resp.body is not None
