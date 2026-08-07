def test_search_prowlarr_does_not_forward_limit_zero(monkeypatch):
    import main as m
    from tests._torznab_helpers import empty_rss

    # One fully-capable indexer so the per-indexer wrapper issues a real search
    # GET. The module-level indexer cache persists across tests; clear it so
    # this test refetches the FakeSession's indexer list.
    m._INDEXERS_CACHE.clear()
    _capable = {
        'id': 1, 'enable': True, 'supportsSearch': True, 'protocol': 'torrent',
        'capabilities': {
            'searchParams': ['q'],
            'movieSearchParams': ['q', 'imdbId', 'tmdbId'],
            'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'tmdbId'],
            'categories': [{'id': 5030}, {'id': 5040}],
        },
    }

    class FakeCtx:
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

        def raise_for_status(self):
            if self.status >= 400:
                raise Exception(f"status {self.status}")

    class FakeSession:
        def __init__(self):
            self.last_params = None
            self.last_headers = None
            self.last_url = None

        def get(self, url, headers=None, params=None):
            if url.endswith('/api/v1/indexer'):
                return FakeCtx(200, data=[dict(_capable)])
            # /<id>/api Torznab passthrough.
            self.last_url = url
            self.last_params = params
            self.last_headers = headers
            return FakeCtx(200, xml=empty_rss())

    session = FakeSession()
    kwargs = {"query": "Love Death and Robots", "limit": "0", "offset": "0", "categories": ["5030", "5040"]}
    import asyncio

    asyncio.new_event_loop().run_until_complete(m.search_prowlarr(session, kwargs))
    assert session.last_params is not None
    assert not session.last_params.get("limit")
    assert session.last_params.get("limit") is None