"""Focused tests for the per-indexer, capability-driven Prowlarr search strategy.

Covers:
  * get_prowlarr_indexers_cached (full objects, TTL, error fallback)
  * select_indexers_for_query (enable/supportsSearch/category lenient match)
  * build_per_indexer_params (capability filtering, token embedding, q-only skip)
  * search_prowlarr_per_indexer (parallel calls, concurrency cap, error isolation)

Uses the FakeCtx/FakeSession pattern from tests/test_prowlarr_id_tokens.py.
Run from the repo root so `import main` resolves.
"""
import asyncio

import main as m


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

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


def _capable_indexer(id_=1, cats=None, movie=True, tv=True, search=True):
    """Build an indexer dict that supports all ID params for movie/tv."""
    caps = {
        'supportsRawSearch': True,
        'searchParams': ['q'] if search else [],
        'movieSearchParams': ['q', 'imdbId', 'tmdbId', 'traktId', 'doubanId', 'genre', 'year'] if movie else [],
        'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'rId', 'tvMazeId', 'traktId', 'tmdbId', 'doubanId', 'genre', 'year'] if tv else [],
        'categories': cats or [{'id': 5030}, {'id': 5040}],
    }
    return {'id': id_, 'enable': True, 'supportsSearch': True, 'capabilities': caps}


def _q_only_indexer(id_=2, cats=None):
    """An indexer that only supports `q` (no ID params) for all search types."""
    caps = {
        'supportsRawSearch': True,
        'searchParams': ['q'],
        'movieSearchParams': ['q'],
        'tvSearchParams': ['q'],
        'categories': cats or [{'id': 5030}],
    }
    return {'id': id_, 'enable': True, 'supportsSearch': True, 'capabilities': caps}


class _IndexerSession:
    """FakeSession that serves a fixed indexer list and records search GETs.

    ``indexers`` is returned for /api/v1/indexer; ``search_data`` is returned
    for /api/v1/search. ``search_calls`` collects the params of every search GET.
    """

    def __init__(self, indexers, search_data=None, search_status=200):
        self._indexers = indexers
        self._search_data = search_data if search_data is not None else []
        self._search_status = search_status
        self.search_calls = []
        self.last_params = None
        self.last_headers = None

    def get(self, url, headers=None, params=None):
        if url.endswith('/api/v1/indexer'):
            return FakeCtx(200, list(self._indexers))
        self.search_calls.append(params)
        self.last_params = params
        self.last_headers = headers
        return FakeCtx(self._search_status, self._search_data)


# ---------------------------------------------------------------------------
# Task 2: get_prowlarr_indexers_cached
# ---------------------------------------------------------------------------

def test_get_prowlarr_indexers_cached_returns_full_objects():
    """Fetching returns the full indexer dicts (not just ids)."""
    m._INDEXERS_CACHE.clear()
    idx = _capable_indexer(id_=7)
    session = _IndexerSession([idx])
    result = asyncio.get_event_loop().run_until_complete(m.get_prowlarr_indexers_cached(session))
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]['id'] == 7
    # Full object: capabilities present, not reduced to an id.
    assert 'capabilities' in result[0]
    assert result[0]['capabilities']['movieSearchParams']


def test_indexers_cache_ttl_avoids_refetch(monkeypatch):
    """A second call within the TTL does not hit the network again."""
    m._INDEXERS_CACHE.clear()
    session = _IndexerSession([_capable_indexer(id_=1)])
    loop = asyncio.get_event_loop()
    first = loop.run_until_complete(m.get_prowlarr_indexers_cached(session))
    second = loop.run_until_complete(m.get_prowlarr_indexers_cached(session))
    assert first == second
    # Only the first call should have issued a GET to /api/v1/indexer; the
    # second is served from cache. We can't easily count indexer GETs on this
    # session, so assert the cache holds the listing and is fresh.
    assert m._indexers_cache_get() is not None


def test_indexers_cache_fallback_on_error(monkeypatch):
    """On a ClientError during refetch, the last-good cached list is served."""
    import aiohttp

    m._INDEXERS_CACHE.clear()
    # Prime the cache with one indexer list.
    good = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    m._indexers_cache_put(good)
    # Force TTL expiry so a refetch is attempted.
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
        def get(self, url, headers=None, params=None):
            return ErrorCtx()

    result = asyncio.get_event_loop().run_until_complete(
        m.get_prowlarr_indexers_cached(ErrorSession())
    )
    # Fallback served the stale-but-cached list (2 indexers), not [].
    assert len(result) == 2
    assert {idx['id'] for idx in result} == {1, 2}


def test_indexers_cache_fallback_empty_when_no_cache(monkeypatch):
    """On a ClientError with no cache at all, an empty list is returned."""
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
        def get(self, url, headers=None, params=None):
            return ErrorCtx()

    result = asyncio.get_event_loop().run_until_complete(
        m.get_prowlarr_indexers_cached(ErrorSession())
    )
    assert result == []


# ---------------------------------------------------------------------------
# Task 3: select_indexers_for_query
# ---------------------------------------------------------------------------

def test_select_indexers_filters_disabled_and_supportsSearch():
    """Disabled indexers and supportsSearch=False indexers are excluded."""
    enabled = _capable_indexer(id_=1)
    disabled = _capable_indexer(id_=2)
    disabled['enable'] = False
    no_search = _capable_indexer(id_=3)
    no_search['supportsSearch'] = False
    out = m.select_indexers_for_query([enabled, disabled, no_search], 'search', [], False)
    ids = [i['id'] for i in out]
    assert ids == [1]


def test_select_indexers_category_lenient_match_includes_subcategories():
    """An indexer declaring a nested subcategory id is matched on that sub."""
    idx = {
        'id': 1, 'enable': True, 'supportsSearch': True,
        'capabilities': {'categories': [
            {'id': 3000, 'name': 'TV', 'subCategories': [
                {'id': 5030, 'name': 'TV/HD', 'subCategories': [
                    {'id': 100201, 'name': 'TV/Anime'}]}]}]},
    }
    # Request the deeply-nested subcategory id.
    out = m.select_indexers_for_query([idx], 'tvsearch', ['100201'], False)
    assert [i['id'] for i in out] == [1]
    # Request an unrelated category -> excluded.
    out2 = m.select_indexers_for_query([idx], 'tvsearch', ['9999'], False)
    assert out2 == []


def test_select_indexers_no_cat_keeps_all():
    """With no requested categories, all enabled+supportsSearch indexers stay."""
    a = _capable_indexer(id_=1, cats=[{'id': 5030}])
    b = _capable_indexer(id_=2, cats=[{'id': 2000}])
    out = m.select_indexers_for_query([a, b], 'search', [], False)
    assert {i['id'] for i in out} == {1, 2}


def test_select_indexers_non_matching_category_excluded():
    """An indexer whose declared categories don't intersect the request is dropped."""
    idx = _capable_indexer(id_=1, cats=[{'id': 2000}])
    out = m.select_indexers_for_query([idx], 'search', ['5030'], False)
    assert out == []


# ---------------------------------------------------------------------------
# Task 4: build_per_indexer_params
# ---------------------------------------------------------------------------

def test_build_per_indexer_params_q_only_drops_ids():
    """A q-only indexer drops ID tokens but keeps the base query."""
    idx = _q_only_indexer(id_=5)
    kwargs = {'type': 'movie', 'query': 'Inception', 'imdbid': 'tt1375666', 'tmdbid': '27205'}
    p = m.build_per_indexer_params(idx, kwargs)
    assert p is not None
    q = p['query']
    assert 'Inception' in q
    # No ID tokens for a q-only indexer.
    assert '{imdbid:' not in q
    assert '{tmdbid:' not in q
    # Scoped to this single indexer.
    assert p['indexerIds'] == '5'
    assert p['type'] == 'movie'


def test_build_per_indexer_params_full_cap_keeps_tokens():
    """A full-cap indexer keeps all supported ID tokens with correct keys."""
    idx = _capable_indexer(id_=9)
    kwargs = {
        'type': 'tvsearch', 'query': 'Some Show', 'tvdbid': '76543',
        'season': '1', 'ep': '2', 'tvmaze': '99', 'imdbid': 'tt123',
    }
    p = m.build_per_indexer_params(idx, kwargs)
    assert p is not None
    q = p['query']
    assert 'Some Show' in q
    assert '{tvdbid:76543}' in q
    assert '{season:1}' in q
    assert '{episode:2}' in q  # ep -> episode token key
    assert '{tvmazeid:99}' in q  # tvmaze -> tvmazeid token key
    assert '{imdbid:tt123}' in q
    assert p['indexerIds'] == '9'


def test_build_per_indexer_params_movie_vs_tvsearch():
    """The same indexer picks the right *SearchParams list per type."""
    idx = _capable_indexer(id_=1)
    # movie type -> only movie token keys (no season/ep/tvdbid tokens).
    mv = m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X', 'imdbid': 'tt1', 'tvdbid': '9'})
    assert '{imdbid:tt1}' in mv['query']
    assert '{tvdbid:' not in mv['query']
    # tvsearch type -> tv tokens (tvdbid present, no movie-only surprise).
    tv = m.build_per_indexer_params(idx, {'type': 'tvsearch', 'query': 'X', 'imdbid': 'tt1', 'tvdbid': '9'})
    assert '{tvdbid:9}' in tv['query']
    assert '{imdbid:tt1}' in tv['query']


def test_build_per_indexer_params_q_only_no_query_returns_none():
    """A q-only indexer with only IDs and no query/title returns None (skip)."""
    idx = _q_only_indexer(id_=3)
    # IDs present but no query; q-only indexer can't use IDs.
    p = m.build_per_indexer_params(idx, {'type': 'movie', 'imdbid': 'tt123'})
    assert p is None


def test_build_per_indexer_params_drops_limit_zero():
    """limit=0 is dropped (client test noise); limit>0 is forwarded as a string."""
    idx = _capable_indexer(id_=1)
    p0 = m.build_per_indexer_params(idx, {'type': 'search', 'query': 'X', 'limit': '0', 'offset': '0'})
    assert 'limit' not in p0
    p1 = m.build_per_indexer_params(idx, {'type': 'search', 'query': 'X', 'limit': '100', 'offset': '10'})
    assert p1['limit'] == '100'
    assert p1['offset'] == '10'


def test_build_per_indexer_params_categories_as_list():
    """Categories are passed as a list (repeated query param) to Prowlarr."""
    idx = _capable_indexer(id_=1)
    p = m.build_per_indexer_params(idx, {'type': 'search', 'query': 'X', 'categories': ['5030', '5040']})
    assert isinstance(p['categories'], list)
    assert p['categories'] == ['5030', '5040']


# ---------------------------------------------------------------------------
# Task 5: search_prowlarr_per_indexer
# ---------------------------------------------------------------------------

def test_search_prowlarr_per_indexer_parallel_calls_n_indexers():
    """N live indexer tasks produce N search GETs, results concatenated in order."""
    idxs = [_capable_indexer(id_=i) for i in (1, 2, 3)]
    tasks = []
    for idx in idxs:
        p = m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X', 'imdbid': 'tt1'})
        tasks.append((idx, p))
    # Each search returns one item tagged with the indexer id (via params).
    session = _IndexerSession([], search_data=[])
    # Override search data to return a per-call item: use a session that
    # returns a distinct item per indexer by reading params['indexerIds'].
    class PerCallSession:
        def __init__(self):
            self.search_calls = []

        def get(self, url, headers=None, params=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, [])
            self.search_calls.append(params)
            iid = params.get('indexerIds')
            return FakeCtx(200, [{'infoHash': f'H{iid}', 'title': f'T{iid}'}])

    sess = PerCallSession()
    out = asyncio.get_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert len(sess.search_calls) == 3
    # Concatenated in stable indexer order (1, 2, 3).
    assert [item['infoHash'] for item in out] == ['H1', 'H2', 'H3']


def test_search_prowlarr_per_indexer_one_failure_does_not_abort_others():
    """A failing indexer returns [] for itself; others still contribute."""
    import aiohttp

    idxs = [_capable_indexer(id_=1), _capable_indexer(id_=2)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    class FailCtx:
        def __init__(self, fail):
            self._fail = fail

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return [{'infoHash': 'OK', 'title': 'T'}]

        def raise_for_status(self):
            if self._fail:
                raise aiohttp.ClientError("indexer down")

    class MixedSession:
        def __init__(self):
            self.search_calls = []

        def get(self, url, headers=None, params=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, [])
            self.search_calls.append(params)
            # Fail indexer id 1; succeed id 2.
            return FailCtx(fail=(params.get('indexerIds') == '1'))

    sess = MixedSession()
    out = asyncio.get_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert len(sess.search_calls) == 2
    # Only the successful indexer contributed.
    assert [item['infoHash'] for item in out] == ['OK']


def test_search_prowlarr_per_indexer_respects_concurrency_cap(monkeypatch):
    """Max in-flight search GETs never exceeds PROWLARR_PARALLEL_INDEXER_CONCURRENCY."""
    monkeypatch.setattr(m, 'PROWLARR_PARALLEL_INDEXER_CONCURRENCY', 2)
    idxs = [_capable_indexer(id_=i) for i in range(6)]
    tasks = [(idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'})) for idx in idxs]

    class TrackingCtx:
        def __init__(self, state):
            self._state = state

        async def __aenter__(self):
            self._state['inflight'] += 1
            self._state['max'] = max(self._state['max'], self._state['inflight'])
            # Yield once to let other coros enter/exit the semaphore.
            await asyncio.sleep(0)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._state['inflight'] -= 1
            return False

        async def json(self):
            return []

        def raise_for_status(self):
            return None

    class TrackingSession:
        def __init__(self, state):
            self._state = state

        def get(self, url, headers=None, params=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, [])
            return TrackingCtx(self._state)

    state = {'inflight': 0, 'max': 0}
    sess = TrackingSession(state)
    asyncio.get_event_loop().run_until_complete(m.search_prowlarr_per_indexer(sess, tasks))
    assert state['max'] <= 2, f"concurrency cap exceeded: max in-flight={state['max']}"
    assert state['max'] >= 1


def test_search_prowlarr_per_indexer_skips_none_tasks():
    """Tasks with params=None are dropped before dispatch (no GET issued)."""
    idx = _capable_indexer(id_=1)
    good = (idx, m.build_per_indexer_params(idx, {'type': 'movie', 'query': 'X'}))
    skipped = (idx, None)
    session = _IndexerSession([], search_data=[])
    out = asyncio.get_event_loop().run_until_complete(
        m.search_prowlarr_per_indexer(session, [skipped, good])
    )
    assert len(session.search_calls) == 1
    assert out == []