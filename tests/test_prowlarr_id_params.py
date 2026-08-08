"""Tests for standard Torznab ID params in search_prowlarr (no {key:val} tokens).

Prowlarr's per-indexer ``/<indexerId>/api`` Torznab passthrough accepts the
standard ID params (``imdbid``, ``tvdbid``, ``tmdbid``, ``season``, ``ep``) as
distinct query params. These tests verify build_per_indexer_params emits them as
top-level params (never as ``{key:val}`` tokens embedded in the query) and that
search_prowlarr routes them through the per-indexer GET.

The FakeSession serves one fully-capable indexer for ``/api/v1/indexer`` (JSON,
capability selection unchanged) and records the params of the ``/<id>/api`` GET
(search, now XML). An empty RSS body is fine for param-assertion tests.

Run from the repo root so `import main` resolves.
"""
import asyncio

import aiohttp

import main as m
from pachelarr import settings

_EMPTY_RSS = b"<?xml version='1.0' encoding='UTF-8'?>\n<rss version=\"2.0\"><channel><title>x</title></channel></rss>"


async def _hs(params):
    async with aiohttp.ClientSession() as session:
        return await m.handle_search(params, session)


# One fully-capable indexer so build_per_indexer_params keeps every ID param.
_CAPABLE_INDEXER = {
    'id': 1,
    'enable': True,
    'supportsSearch': True,
    'protocol': 'torrent',
    'capabilities': {
        'supportsRawSearch': True,
        'movieSearchParams': ['q', 'imdbId', 'tmdbId', 'genre', 'year'],
        'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'tmdbId', 'genre', 'year'],
        'searchParams': ['q'],
        'categories': [{'id': 3000, 'name': 'TV', 'subCategories': [
            {'id': 5030, 'name': 'TV/HD'}, {'id': 5040, 'name': 'TV/SD'}]}],
    },
}


def _fake_session():
    class FakeCtx:
        def __init__(self, status, data, xml=None):
            self.status = status
            self._data = data
            self._xml = xml if xml is not None else _EMPTY_RSS

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._data

        async def read(self):
            return self._xml

        async def text(self):
            return self._xml.decode('utf-8', 'replace')

        def raise_for_status(self):
            if self.status >= 400:
                raise Exception(f"status {self.status}")

    class FakeSession:
        def __init__(self):
            self.last_params = None
            self.last_headers = None
            self.last_url = None
            self.indexer_calls = 0
            self.search_calls = 0

        def get(self, url, headers=None, params=None, timeout=None):
            if url.endswith('/api/v1/indexer'):
                self.indexer_calls += 1
                return FakeCtx(200, [dict(_CAPABLE_INDEXER)])
            # /<id>/api Torznab passthrough: record params for assertions.
            self.search_calls += 1
            self.last_url = url
            self.last_params = params or {}
            self.last_headers = headers
            return FakeCtx(200, [], xml=_EMPTY_RSS)

    m._INDEXERS_CACHE.clear()
    return FakeSession()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _q(p):
    """Return the query value from params, tolerant of q/query key names."""
    return p.get('q') or p.get('query') or ''


def _is_mapped(p, key):
    """True if `key` is present as a top-level param (q/t/imdbid/...)."""
    return key in (p or {})


def test_search_prowlarr_sends_movie_id_params():
    """Movie-type search sends imdbid/tmdbid as top-level params, no tokens."""
    session = _fake_session()
    kwargs = {
        'type': 'movie',
        'query': 'Restart the Earth 2021',
        'imdbid': '16118262',
        'tmdbid': '12345',
    }
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert p is not None
    # query value is just the title (no embedded tokens).
    q = _q(p)
    assert q == 'Restart the Earth 2021'
    assert '{imdbid:' not in q
    assert '{tmdbid:' not in q
    assert '{' not in q
    # IDs are top-level params.
    assert p.get('imdbid') == '16118262'
    assert p.get('tmdbid') == '12345'


def test_search_prowlarr_sends_tv_id_params():
    """TV-type search sends tvdbid/season/ep as top-level params, no tokens."""
    session = _fake_session()
    kwargs = {
        'type': 'tvsearch',
        'query': 'Some Show',
        'tvdbid': '76543',
        'season': '1',
        'ep': '2',
    }
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    q = _q(p)
    assert q == 'Some Show'
    assert '{tvdbid:' not in q
    assert '{season:' not in q
    assert '{episode:' not in q
    assert '{ep:' not in q
    assert '{' not in q
    assert p.get('tvdbid') == '76543'
    assert p.get('season') == '1'
    assert p.get('ep') == '2'


def test_search_prowlarr_no_tokens_for_generic_search_type():
    """type=search does not emit ID params (relies on TMDB title lookup)."""
    session = _fake_session()
    kwargs = {
        'type': 'search',
        'query': 'Some Movie',
        'imdbid': '16118262',
    }
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert _q(p) == 'Some Movie'
    assert '{imdbid:' not in _q(p)
    assert not _is_mapped(p, 'imdbid')


def test_search_prowlarr_id_only_movie_search_sends_id_param():
    """Movie search with IDs but no query still sends the ID param."""
    session = _fake_session()
    kwargs = {
        'type': 'movie',
        'imdbid': '16118262',
    }
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert p is not None
    assert p.get('imdbid') == '16118262'
    q = _q(p)
    assert q == '' or q is None
    assert '{imdbid:' not in (q or '')


def test_search_prowlarr_categories_only_fallback_still_works(monkeypatch):
    """Categories-only call still gets the fallback query; no tokens emitted.

    The fallback query is injected by handle_search into search_kwargs['query']
    before search_prowlarr is called, so we mirror that here and assert it flows
    through unchanged with categories as a list and no ID tokens.
    """
    settings.set_override("PACHELARR_TEST_FALLBACK_QUERY", "a")
    try:
        session = _fake_session()
        kwargs = {'query': settings.get_str("PACHELARR_TEST_FALLBACK_QUERY"), 'categories': ['5030', '5040']}
        _run(m.search_prowlarr(session, kwargs))
        p = session.last_params
        assert _q(p) == 'a'
        cats = p.get('cat')
        if cats is None:
            cats = p.get('categories')
        assert isinstance(cats, list)
        assert '{' not in (_q(p) or '')
    finally:
        settings.set_override("PACHELARR_TEST_FALLBACK_QUERY", None)


def test_search_prowlarr_plain_query_no_ids_unchanged():
    """A plain query with no IDs is forwarded unchanged."""
    session = _fake_session()
    kwargs = {'type': 'movie', 'query': 'Inception 2010'}
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    assert _q(p) == 'Inception 2010'


def test_handle_search_strips_http_version_from_ep_season(monkeypatch):
    """Regression: an upstream proxy injected ' HTTP/1.1' into ep/season values.

    handle_search must sanitize season/ep to leading digits only so the
    corrupted value does not flow into Prowlarr. The fake search returns XML
    pairs so handle_search completes through consolidation + emit.
    """
    from starlette.datastructures import QueryParams

    captured = {}

    async def fake_search(session, kwargs):
        captured.update(kwargs)
        return [(m if isinstance(m, dict) else {'id': 1}, _EMPTY_RSS)]

    monkeypatch.setattr('main.search_prowlarr', fake_search)

    params = QueryParams({
        't': 'tvsearch',
        'q': 'Some Show',
        'season': '1 HTTP/1.1',
        'ep': '1 HTTP/1.1',
        'tvdbid': '12345',
    })
    _run(_hs(params))

    assert captured.get('season') == '1', captured
    assert captured.get('ep') == '1', captured
    assert captured.get('tvdbid') == '12345'


def test_handle_search_drops_non_numeric_ep(monkeypatch):
    """A season/ep with no leading digits is dropped, not forwarded."""
    from starlette.datastructures import QueryParams

    captured = {}

    async def fake_search(session, kwargs):
        captured.update(kwargs)
        return [({'id': 1}, _EMPTY_RSS)]

    monkeypatch.setattr('main.search_prowlarr', fake_search)

    params = QueryParams({
        't': 'tvsearch',
        'q': 'Some Show',
        'ep': 'HTTP/1.1',
    })
    _run(_hs(params))

    assert 'ep' not in captured, captured


def test_search_prowlarr_no_keyval_tokens_anywhere():
    """A full-cap indexer with all IDs sends no {key:val} tokens anywhere in q."""
    session = _fake_session()
    kwargs = {
        'type': 'tvsearch',
        'query': 'Some Show',
        'tvdbid': '76543',
        'season': '1',
        'ep': '2',
        'imdbid': 'tt123',
        'tmdbid': '456',
    }
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    q = _q(p)
    for token in ('{imdbid:', '{tvdbid:', '{tmdbid:', '{season:', '{episode:', '{ep:'):
        assert token not in q, f"unexpected token {token!r} in q={q!r}"


def test_search_prowlarr_drops_dropped_ids():
    """rid/tvmaze/traktid/doubanid are dropped (no longer forwarded, no tokens)."""
    session = _fake_session()
    kwargs = {
        'type': 'tvsearch',
        'query': 'Some Show',
        'rid': '999',
        'tvmaze': '99',
        'traktid': '77',
        'doubanid': '55',
        'imdbid': 'tt1',
    }
    _run(m.search_prowlarr(session, kwargs))
    p = session.last_params
    q = _q(p)
    for dropped in ('rid', 'tvmaze', 'traktid', 'doubanid'):
        assert not _is_mapped(p, dropped), f"{dropped} should not be a param: {p}"
    for token in ('{rid:', '{tvmazeid:', '{traktid:', '{doubanid:'):
        assert token not in q
    # imdbid (kept) still flows as a top-level param.
    assert p.get('imdbid') == 'tt1'
