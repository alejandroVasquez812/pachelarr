"""Focused tests for the per-indexer, capability-driven Prowlarr search strategy.

Covers:
  * get_prowlarr_indexers_cached (full objects, TTL, error fallback)
  * select_indexers_for_query (enable/supportsSearch/category lenient match)
  * build_per_indexer_params (capability filtering, standard ID params, q-only skip)
  * search_prowlarr_per_indexer (parallel calls, concurrency cap, error isolation)

The per-indexer search endpoint is Prowlarr's Torznab passthrough
``/<indexerId>/api`` (returns XML). The indexer-listing endpoint
``/api/v1/indexer`` is still JSON (capability selection unchanged).

Run from the repo root so `import main` resolves.
"""
import asyncio

import main as m
from pachelarr import settings
from tests._torznab_helpers import build_rss, empty_rss

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class FakeCtx:
    """Context manager that serves JSON (for /api/v1/indexer) or XML bytes (for
    /<id>/api) depending on construction.

    ``data`` is the JSON body (for the indexer endpoint); ``xml`` is the Torznab
    RSS bytes returned for a search GET.
    """

    def __init__(self, status, data=None, xml=None):
        self.status = status
        self._data = data
        self._xml = xml if xml is not None else empty_rss()

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


def _capable_indexer(id_=1, cats=None, movie=True, tv=True, search=True):
    """Build an indexer dict that supports all kept ID params for movie/tv."""
    caps = {
        'supportsRawSearch': True,
        'searchParams': ['q'] if search else [],
        'movieSearchParams': ['q', 'imdbId', 'tmdbId', 'genre', 'year'] if movie else [],
        'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'tmdbId', 'genre', 'year'] if tv else [],
        'categories': cats or [{'id': 5030}, {'id': 5040}],
    }
    return {'id': id_, 'enable': True, 'supportsSearch': True,
            'capabilities': caps, 'protocol': 'torrent'}


def _q_only_indexer(id_=2, cats=None):
    """An indexer that only supports `q` (no ID params) for all search types."""
    caps = {
        'supportsRawSearch': True,
        'searchParams': ['q'],
        'movieSearchParams': ['q'],
        'tvSearchParams': ['q'],
        'categories': cats or [{'id': 5030}],
    }
    return {'id': id_, 'enable': True, 'supportsSearch': True,
            'capabilities': caps, 'protocol': 'torrent'}


def _torznab_xml(items):
    """Build a minimal Torznab RSS document from simple item dicts.

    Each dict supports: hash, title, seeders, peers, trackers (synthesizes a
    magnet when `hash` is given). An empty list yields an empty channel.
    """
    return build_rss(items)


def _idx_id_from_url(url):
    """Extract the indexer id from a /<id>/api URL, or None."""
    # URL like http://host/1/api ; the id is the path segment before /api.
    if not url:
        return None
    if '/api' not in url:
        return None
    base = url.split('/api', 1)[0]
    seg = base.rstrip('/').rsplit('/', 1)[-1]
    try:
        return str(int(seg))
    except (TypeError, ValueError):
        return seg


def _params_idx_id(params, url):
    """Best-effort: the indexer id targeted by this search GET.

    Scoping is via URL path now (/<id>/api), not an indexerIds param. Fall back
    to the params' indexerIds for impls still using it during transition.
    """
    iid = _idx_id_from_url(url)
    if iid is not None:
        return iid
    if params and 'indexerIds' in params:
        return str(params['indexerIds'])
    return None


class _IndexerSession:
    """FakeSession that serves a fixed indexer list and records search GETs.

    ``indexers`` is returned (JSON) for /api/v1/indexer; ``search_data`` (XML
    bytes, or a callable(url, params)->bytes) is returned for /<id>/api.
    ``search_calls`` collects the params of every search GET.
    """

    def __init__(self, indexers, search_data=None, search_status=200):
        self._indexers = indexers
        self._search_data = search_data
        self._search_status = search_status
        self.search_calls = []
        self.search_urls = []
        self.last_params = None
        self.last_headers = None

    def _xml_for(self, url, params):
        sd = self._search_data
        if sd is None:
            return empty_rss()
        if callable(sd):
            return sd(url, params)
        if isinstance(sd, (bytes, bytearray)):
            return bytes(sd)
        return sd

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith('/api/v1/indexer'):
            return FakeCtx(200, data=list(self._indexers))
        self.search_calls.append(params)
        self.search_urls.append(url)
        self.last_params = params
        self.last_headers = headers
        return FakeCtx(self._search_status, xml=self._xml_for(url, params))


# ---------------------------------------------------------------------------
# Task 2: get_prowlarr_indexers_cached
# ---------------------------------------------------------------------------

def test_get_prowlarr_indexers_cached_returns_full_objects():
    m._INDEXERS_CACHE.clear()
    idx = _capable_indexer(id_=7)
    session = _IndexerSession([idx])
    result = asyncio.new_event_loop().run_until_complete(m.get_prowlarr_indexers_cached(session))
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]['id'] == 7
    assert 'capabilities' in result[0]
    assert result[0]['capabilities']['movieSearchParams']


def test_indexers_cache_ttl_avoids_refetch(monkeypatch):
    m._INDEXERS_CACHE.clear()
    session = _IndexerSession([_capable_indexer(id_=1)])
    loop = asyncio.new_event_loop()
    first = loop.run_until_complete(m.get_prowlarr_indexers_cached(session))
    second = loop.run_until_complete(m.get_prowlarr_indexers_cached(session))
    assert first == second
    assert m._indexers_cache_get() is not None


def test_indexers_cache_fallback_on_error(monkeypatch):
    import aiohttp

    m._INDEXERS_CACHE.clear()
    good = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    m._indexers_cache_put(good)
    m._INDEXERS_CACHE['listing']['expires'] = 0.0

    class ErrorCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return []

        def raise_for_status(self):
            raise aiohttp.ClientError("boom")

    class ErrorSession:
        def get(self, url, headers=None, params=None, timeout=None):
            return ErrorCtx()

    result = asyncio.new_event_loop().run_until_complete(
        m.get_prowlarr_indexers_cached(ErrorSession())
    )
    assert len(result) == 2
    assert {idx['id'] for idx in result} == {1, 2}


def test_indexers_cache_fallback_empty_when_no_cache(monkeypatch):
    import aiohttp

    m._INDEXERS_CACHE.clear()

    class ErrorCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return []

        def raise_for_status(self):
            raise aiohttp.ClientError("boom")

    class ErrorSession:
        def get(self, url, headers=None, params=None, timeout=None):
            return ErrorCtx()

    result = asyncio.new_event_loop().run_until_complete(
        m.get_prowlarr_indexers_cached(ErrorSession())
    )
    assert result == []


# ---------------------------------------------------------------------------
# Task 3: select_indexers_for_query
# ---------------------------------------------------------------------------

def test_select_indexers_filters_disabled_and_supportsSearch():
    enabled = _capable_indexer(id_=1)
    disabled = _capable_indexer(id_=2)
    disabled['enable'] = False
    no_search = _capable_indexer(id_=3)
    no_search['supportsSearch'] = False
    out = m.select_indexers_for_query([enabled, disabled, no_search], 'search', [], False)
    ids = [i['id'] for i in out]
    assert ids == [1]


def test_select_indexers_category_lenient_match_includes_subcategories():
    idx = {
        'id': 1, 'enable': True, 'supportsSearch': True, 'protocol': 'torrent',
        'capabilities': {'categories': [
            {'id': 3000, 'name': 'TV', 'subCategories': [
                {'id': 5030, 'name': 'TV/HD', 'subCategories': [
                    {'id': 100201, 'name': 'TV/Anime'}]}]}]},
    }
    out = m.select_indexers_for_query([idx], 'tvsearch', ['100201'], False)
    assert [i['id'] for i in out] == [1]
    out2 = m.select_indexers_for_query([idx], 'tvsearch', ['9999'], False)
    assert out2 == []


def test_select_indexers_no_cat_keeps_all():
    a = _capable_indexer(id_=1, cats=[{'id': 5030}])
    b = _capable_indexer(id_=2, cats=[{'id': 2000}])
    out = m.select_indexers_for_query([a, b], 'search', [], False)
    assert {i['id'] for i in out} == {1, 2}


def test_select_indexers_non_matching_category_excluded():
    idx = _capable_indexer(id_=1, cats=[{'id': 2000}])
    out = m.select_indexers_for_query([idx], 'search', ['5030'], False)
    assert out == []


# ---------------------------------------------------------------------------
# Task 4: build_per_indexer_params
# ---------------------------------------------------------------------------

def test_build_per_indexer_params_q_only_drops_ids():
    idx = _q_only_indexer(id_=5)
    kwargs = {'type': 'movie', 'query': 'Inception', 'imdbid': 'tt1375666', 'tmdbid': '27205'}
    p = m.build_per_indexer_params(idx, kwargs)
    assert p is not None
    q = p.get('query', '')
    assert 'Inception' in q
    # q-only indexer supports no ID params -> none forwarded.
    assert 'imdbid' not in p
    assert 'tmdbid' not in p
    assert '{imdbid:' not in q
    assert '{tmdbid:' not in q
    # No indexerIds key (scoping is via URL path).
    assert 'indexerIds' not in p
    assert p['type'] == 'movie'


def test_build_per_indexer_params_sends_id_params():
    idx = _capable_indexer(id_=9)
    kwargs = {
        'type': 'tvsearch', 'query': 'Some Show', 'tvdbid': '76543',
        'season': '1', 'ep': '2', 'imdbid': 'tt123',
    }
    p = m.build_per_indexer_params(idx, kwargs)
    assert p is not None
    q = p.get('query', '')
    assert 'Some Show' in q
    # IDs are top-level params (no {key:val} tokens).
    assert p['tvdbid'] == '76543'
    assert p['season'] == '1'
    assert p['ep'] == '2'
    assert p['imdbid'] == 'tt123'
    assert '{' not in q
    assert 'indexerIds' not in p


def test_build_per_indexer_params_movie_vs_tvsearch():
    idx = _capable_indexer(id_=1)
    mv = m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X', 'imdbid': 'tt1', 'tvdbid': '9'})
    # movie type -> imdbid supported, tvdbid not in movieSearchParams.
    assert mv['imdbid'] == 'tt1'
    assert 'tvdbid' not in mv
    assert '{' not in mv.get('query', '')
    tv = m.build_per_indexer_params(idx, {'type': 'tvsearch', 'query': 'X', 'imdbid': 'tt1', 'tvdbid': '9'})
    assert tv['tvdbid'] == '9'
    assert tv['imdbid'] == 'tt1'
    assert '{' not in tv.get('query', '')


def test_build_per_indexer_params_q_only_no_query_returns_none():
    idx = _q_only_indexer(id_=3)
    p = m.build_per_indexer_params(idx, {'type': 'movie', 'imdbid': 'tt123'})
    assert p is None


def test_build_per_indexer_params_q_only_no_query_but_categories_returns_params():
    # Sonarr/Radarr RSS-style / category-only search: no query/title and no
    # supported IDs, but categories present. Should be forwarded (not skipped).
    idx = _q_only_indexer(id_=3)
    p = m.build_per_indexer_params(idx, {'type': 'search', 'categories': ['5030', '5040']})
    assert p is not None
    assert 'query' not in p
    assert p['categories'] == ['5030', '5040']
    assert p['type'] == 'search'
    assert not any(k in p for k in ('imdbid', 'tvdbid', 'tmdbid', 'season', 'ep'))


def test_build_per_indexer_params_drops_limit_zero():
    idx = _capable_indexer(id_=1)
    p0 = m.build_per_indexer_params(idx, {'type': 'search', 'query': 'X', 'limit': '0', 'offset': '0'})
    assert 'limit' not in p0
    p1 = m.build_per_indexer_params(idx, {'type': 'search', 'query': 'X', 'limit': '100', 'offset': '10'})
    assert p1['limit'] == '100'
    assert p1['offset'] == '10'


def test_build_per_indexer_params_categories_as_list():
    idx = _capable_indexer(id_=1)
    p = m.build_per_indexer_params(idx, {'type': 'search', 'query': 'X', 'categories': ['5030', '5040']})
    assert isinstance(p['categories'], list)
    assert p['categories'] == ['5030', '5040']


# ---------------------------------------------------------------------------
# Task 5: search_prowlarr_per_indexer
# ---------------------------------------------------------------------------

def test_search_prowlarr_per_indexer_parallel_calls_n_indexers():
    """N live indexer tasks produce N search GETs; each XML pair carries H<id>."""
    idxs = [_capable_indexer(id_=i) for i in (1, 2, 3)]
    tasks = [
        (idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X', 'imdbid': 'tt1'}))
        for idx in idxs
    ]

    def search_xml(url, params):
        iid = _params_idx_id(params, url)
        return _torznab_xml([{"hash": f"H{iid}", "title": f"T{iid}", "seeders": 1}])

    sess = _IndexerSession([], search_data=search_xml)
    out = asyncio.new_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert len(sess.search_calls) == 3
    # search_prowlarr_per_indexer returns [(indexer, xml_bytes), ...] in order.
    assert len(out) == 3
    for i, (idx, xml) in enumerate(out, start=1):
        assert idx['id'] == i
        assert b"H%d" % i in xml


def test_search_prowlarr_per_indexer_one_failure_does_not_abort_others():
    """A failing indexer returns None (dropped); the successful one survives."""
    import aiohttp

    idxs = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    class FailCtx:
        def __init__(self, fail):
            self._fail = fail
            self.status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return []

        async def read(self):
            return _torznab_xml([{"hash": "OKOK", "title": "T", "seeders": 1}])

        def raise_for_status(self):
            if self._fail:
                raise aiohttp.ClientError("indexer down")

    class MixedSession:
        def __init__(self):
            self.search_calls = []
            self.search_urls = []

        def get(self, url, headers=None, params=None, timeout=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, data=[])
            self.search_calls.append(params)
            self.search_urls.append(url)
            iid = _params_idx_id(params, url)
            return FailCtx(fail=(iid == '1'))

    sess = MixedSession()
    out = asyncio.new_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert len(sess.search_calls) == 2
    # Only the successful indexer's pair survives.
    assert len(out) == 1
    assert b"OKOK" in out[0][1]


def test_search_prowlarr_per_indexer_respects_concurrency_cap(monkeypatch):
    settings.set_override("PROWLARR_PARALLEL_INDEXER_CONCURRENCY", 2)
    idxs = [_capable_indexer(id_=i) for i in range(6)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    class TrackingCtx:
        def __init__(self, state):
            self._state = state
            self.status = 200

        async def __aenter__(self):
            self._state['inflight'] += 1
            self._state['max'] = max(self._state['max'], self._state['inflight'])
            await asyncio.sleep(0)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._state['inflight'] -= 1
            return False

        async def json(self):
            return []

        async def read(self):
            return empty_rss()

        def raise_for_status(self):
            return None

    class TrackingSession:
        def __init__(self, state):
            self._state = state

        def get(self, url, headers=None, params=None, timeout=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, data=[])
            return TrackingCtx(self._state)

    state = {'inflight': 0, 'max': 0}
    sess = TrackingSession(state)
    asyncio.new_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert state['max'] <= 2, f"concurrency cap exceeded: max in-flight={state['max']}"
    assert state['max'] >= 1


def test_search_prowlarr_per_indexer_skips_none_tasks():
    idx = _capable_indexer(id_=1)
    good = (idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'}))
    skipped = (idx, None)
    session = _IndexerSession([], search_data=empty_rss())
    out = asyncio.new_event_loop().run_until_complete(
        m.search_prowlarr_per_indexer(session, [skipped, good])
    )
    assert len(session.search_calls) == 1
    # One successful pair.
    assert len(out) == 1


def test_search_prowlarr_per_indexer_timeout_does_not_abort_others():
    """A hung/timed-out indexer (asyncio.TimeoutError) is dropped, not fatal.

    Mirrors test_search_prowlarr_per_indexer_one_failure_does_not_abort_others
    but the failing indexer raises asyncio.TimeoutError from the
    ``async with session.get(...)`` block (as real aiohttp does on timeout).
    The good indexer's XML must still come back; no exception propagates.
    """
    idxs = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    class TimeoutCtx:
        def __init__(self):
            self.status = 200

        async def __aenter__(self):
            raise asyncio.TimeoutError("indexer hung")

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return []

        async def read(self):
            return empty_rss()

        def raise_for_status(self):
            return None

    class OkCtx:
        def __init__(self):
            self.status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return []

        async def read(self):
            return _torznab_xml([{"hash": "GOOD1", "title": "T", "seeders": 1}])

        def raise_for_status(self):
            return None

    class HungSession:
        def __init__(self):
            self.search_calls = []
            self.search_urls = []

        def get(self, url, headers=None, params=None, timeout=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, data=[])
            self.search_calls.append(params)
            self.search_urls.append(url)
            iid = _params_idx_id(params, url)
            return TimeoutCtx() if iid == '1' else OkCtx()

    sess = HungSession()
    out = asyncio.new_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    # Both indexers were attempted.
    assert len(sess.search_calls) == 2
    # The timed-out indexer is dropped; only the good one survives.
    assert len(out) == 1
    assert out[0][0]['id'] == 2
    assert b"GOOD1" in out[0][1]


# ---------------------------------------------------------------------------
# Task 6: per-indexer stat recording
# ---------------------------------------------------------------------------

def test_search_prowlarr_per_indexer_records_success_stat(monkeypatch):
    """A successful per-indexer search records a positive latency for its id."""
    from pachelarr import state as state_mod

    calls = []
    monkeypatch.setattr(state_mod, "record_indexer_stat",
                        lambda idx_id, latency_ms, error=False: calls.append(
                            (idx_id, latency_ms, error)))

    idxs = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    def search_xml(url, params):
        iid = _params_idx_id(params, url)
        return _torznab_xml([{"hash": f"H{iid}", "title": f"T{iid}", "seeders": 1}])

    sess = _IndexerSession([], search_data=search_xml)
    out = asyncio.new_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert len(out) == 2
    assert len(calls) == 2
    for idx_id, latency_ms, error in calls:
        assert idx_id in (1, 2)
        assert latency_ms >= 0
        assert error is False


def test_search_prowlarr_per_indexer_records_error_stat(monkeypatch):
    """A failing per-indexer search records error=True for its id."""
    import aiohttp

    from pachelarr import state as state_mod

    calls = []
    monkeypatch.setattr(state_mod, "record_indexer_stat",
                        lambda idx_id, latency_ms, error=False: calls.append(
                            (idx_id, latency_ms, error)))

    idxs = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    class FailCtx:
        def __init__(self, fail):
            self._fail = fail
            self.status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return []

        async def read(self):
            return _torznab_xml([{"hash": "OKOK", "title": "T", "seeders": 1}])

        def raise_for_status(self):
            if self._fail:
                raise aiohttp.ClientError("indexer down")

    class MixedSession:
        def __init__(self):
            self.search_calls = []
            self.search_urls = []

        def get(self, url, headers=None, params=None, timeout=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, data=[])
            self.search_calls.append(params)
            self.search_urls.append(url)
            iid = _params_idx_id(params, url)
            return FailCtx(fail=(iid == '1'))

    sess = MixedSession()
    out = asyncio.new_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert len(out) == 1
    assert len(calls) == 2
    by_id = {idx_id: (latency_ms, error) for idx_id, latency_ms, error in calls}
    assert by_id[1][1] is True   # failing indexer -> error=True
    assert by_id[2][1] is False  # successful indexer -> error=False
    assert by_id[1][0] >= 0
    assert by_id[2][0] >= 0
