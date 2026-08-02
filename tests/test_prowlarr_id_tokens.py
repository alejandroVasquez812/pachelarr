"""Tests for Prowlarr ID token embedding in search_prowlarr.

Prowlarr's /api/v1/search binds only Query/Type/Categories/IndexerIds/Limit/Offset.
IDs (imdbid, tmdbid, etc.) must be embedded as {key:value} tokens inside the
query string, where QueryToParams() parses them (only for type=movie and
type=tvsearch) and strips the braces. These tests verify that behavior.

NOTE: search_prowlarr is now a per-indexer wrapper that first fetches
/api/v1/indexer. The FakeSession here serves one fully-capable indexer for the
indexer endpoint and [] for the search endpoint, and records last_params only
for search calls so token-embedding assertions target the search GET.
"""
import asyncio

import main as m


# One fully-capable indexer so build_per_indexer_params keeps every ID token.
_CAPABLE_INDEXER = {
    'id': 1,
    'enable': True,
    'supportsSearch': True,
    'capabilities': {
        'supportsRawSearch': True,
        'movieSearchParams': ['q', 'imdbId', 'tmdbId', 'traktId', 'doubanId', 'genre', 'year'],
        'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'rId', 'tvMazeId', 'traktId', 'tmdbId', 'doubanId', 'genre', 'year'],
        'searchParams': ['q'],
        'categories': [{'id': 3000, 'name': 'TV', 'subCategories': [
            {'id': 5030, 'name': 'TV/HD'}, {'id': 5040, 'name': 'TV/SD'}]}],
    },
}


def _fake_session():
    class FakeCtx:
        def __init__(self, status, data):
            self.status = status
            self._data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._data

        def raise_for_status(self):
            if self.status >= 400:
                raise Exception(f"status {self.status}")

    class FakeSession:
        def __init__(self):
            self.last_params = None
            self.last_headers = None
            self.indexer_calls = 0
            self.search_calls = 0

        def get(self, url, headers=None, params=None):
            if url.endswith('/api/v1/indexer'):
                self.indexer_calls += 1
                return FakeCtx(200, [dict(_CAPABLE_INDEXER)])
            # /api/v1/search: record params for token assertions.
            self.search_calls += 1
            self.last_params = params
            self.last_headers = headers
            return FakeCtx(200, [])

    # The module-level indexer cache persists across tests; clear it so each
    # test refetches the (per-test) indexer list from the FakeSession.
    m._INDEXERS_CACHE.clear()
    return FakeSession()


def test_search_prowlarr_embeds_movie_id_tokens():
    """Movie-type search embeds imdbid/tmdbid as {key:value} tokens in query."""
    session = _fake_session()
    kwargs = {
        'type': 'movie',
        'query': 'Restart the Earth 2021',
        'imdbid': '16118262',
        'tmdbid': '12345',
    }
    asyncio.get_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert p is not None
    q = p.get('query', '')
    assert 'Restart the Earth 2021' in q
    assert '{imdbid:16118262}' in q
    assert '{tmdbid:12345}' in q
    # IDs must NOT be sent as separate query params
    assert 'imdbid' not in p
    assert 'tmdbid' not in p


def test_search_prowlarr_embeds_tv_id_tokens_with_name_mapping():
    """TV-type search maps tvmaze->tvmazeid and ep->episode in tokens."""
    session = _fake_session()
    kwargs = {
        'type': 'tvsearch',
        'query': 'Some Show',
        'tvdbid': '76543',
        'season': '1',
        'ep': '2',
        'tvmaze': '99',
    }
    asyncio.get_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
    p = session.last_params
    q = p.get('query', '')
    assert 'Some Show' in q
    assert '{tvdbid:76543}' in q
    assert '{season:1}' in q
    assert '{episode:2}' in q
    assert '{tvmazeid:99}' in q
    # Old/our field names must not appear as raw token keys
    assert '{ep:' not in q
    assert '{tvmaze:' not in q
    assert 'tvdbid' not in p
    assert 'tvmaze' not in p


def test_search_prowlarr_no_tokens_for_generic_search_type():
    """type=search does not emit ID tokens (relies on TMDB title lookup)."""
    session = _fake_session()
    kwargs = {
        'type': 'search',
        'query': 'Some Movie',
        'imdbid': '16118262',
    }
    asyncio.get_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert p.get('query') == 'Some Movie'
    assert '{imdbid:' not in (p.get('query') or '')
    assert 'imdbid' not in p


def test_search_prowlarr_id_only_movie_search_builds_token_query():
    """Movie search with IDs but no query still produces a token-bearing query."""
    session = _fake_session()
    kwargs = {
        'type': 'movie',
        'imdbid': '16118262',
    }
    asyncio.get_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
    p = session.last_params
    q = p.get('query', '')
    assert q == '{imdbid:16118262}'


def test_search_prowlarr_categories_only_fallback_still_works(monkeypatch):
    """Categories-only call still gets the fallback query; no tokens emitted.

    The fallback query is now injected by handle_search into search_kwargs['query']
    before search_prowlarr is called, so we mirror that here and assert it flows
    through unchanged with categories as a list and no ID tokens.
    """
    m.PACHELARR_TEST_FALLBACK_QUERY = 'a'
    try:
        session = _fake_session()
        # handle_search would set search_kwargs['query'] = fallback when the
        # incoming request was category-only; emulate that contract here.
        kwargs = {'query': m.PACHELARR_TEST_FALLBACK_QUERY, 'categories': ['5030', '5040']}
        asyncio.get_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
        p = session.last_params
        assert p.get('query') == 'a'
        assert isinstance(p.get('categories'), list)
        # No IDs were supplied, so no tokens should be embedded.
        assert '{' not in (p.get('query') or '')
    finally:
        m.PACHELARR_TEST_FALLBACK_QUERY = ''


def test_search_prowlarr_plain_query_no_ids_unchanged():
    """A plain query with no IDs is forwarded unchanged."""
    session = _fake_session()
    kwargs = {'type': 'movie', 'query': 'Inception 2010'}
    asyncio.get_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert p.get('query') == 'Inception 2010'


def test_handle_search_strips_http_version_from_ep_season(monkeypatch):
    """Regression: an upstream proxy injected ' HTTP/1.1' into ep/season values.

    handle_search must sanitize season/ep to leading digits only so the
    corrupted value does not flow into the Prowlarr query token.
    """
    from starlette.datastructures import QueryParams

    captured = {}

    async def fake_search(session, kwargs):
        captured.update(kwargs)
        return [{'infoHash': 'AAA111', 'title': 'T1',
                 'magnetUri': 'magnet:?xt=urn:btih:AAA111'}]

    monkeypatch.setattr('main.search_prowlarr', fake_search)

    # Simulate the corrupted incoming query: ep and season carry ' HTTP/1.1'.
    params = QueryParams({
        't': 'tvsearch',
        'q': 'Some Show',
        'season': '1 HTTP/1.1',
        'ep': '1 HTTP/1.1',
        'tvdbid': '12345',
    })
    asyncio.get_event_loop().run_until_complete(m.handle_search(params))

    # season/ep must be reduced to their leading integer.
    assert captured.get('season') == '1', captured
    assert captured.get('ep') == '1', captured
    # Non-integer Torznab fields must be passed through untouched.
    assert captured.get('tvdbid') == '12345'


def test_handle_search_drops_non_numeric_ep(monkeypatch):
    """A season/ep with no leading digits is dropped, not forwarded."""
    from starlette.datastructures import QueryParams

    captured = {}

    async def fake_search(session, kwargs):
        captured.update(kwargs)
        return [{'infoHash': 'BBB222', 'title': 'T2',
                 'magnetUri': 'magnet:?xt=urn:btih:BBB222'}]

    monkeypatch.setattr('main.search_prowlarr', fake_search)

    params = QueryParams({
        't': 'tvsearch',
        'q': 'Some Show',
        'ep': 'HTTP/1.1',
    })
    asyncio.get_event_loop().run_until_complete(m.handle_search(params))

    assert 'ep' not in captured, captured