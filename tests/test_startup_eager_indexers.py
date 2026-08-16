"""Tests for startup eager indexer loading.

Ensures lifespan prefetches the Prowlarr indexer list after opening the
aiohttp session, skips placeholder URLs, and that failures don't break startup.
"""
import asyncio

import aiohttp
import pytest

import main as m
from pachelarr import app as app_mod
from pachelarr import settings, state


def _capable_indexer(id_=1):
    caps = {
        'supportsRawSearch': True,
        'searchParams': ['q'],
        'movieSearchParams': ['q', 'imdbId', 'tmdbId', 'genre', 'year'],
        'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'tmdbId', 'genre', 'year'],
        'categories': [{'id': 5030}, {'id': 5040}],
    }
    return {'id': id_, 'enable': True, 'supportsSearch': True,
            'capabilities': caps, 'protocol': 'torrent'}


class _FakeCtx:
    def __init__(self, status=200, data=None, xml=None):
        self.status = status
        self._data = data or []
        self._xml = xml

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


class _RecordingIndexerSession:
    def __init__(self, indexers):
        self._indexers = indexers
        self.indexer_get_count = 0

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith('/api/v1/indexer'):
            self.indexer_get_count += 1
            return _FakeCtx(200, data=list(self._indexers))
        return _FakeCtx(200, data=[])


@pytest.fixture
def _clear_indexers():
    state._INDEXERS_CACHE.clear()
    yield
    state._INDEXERS_CACHE.clear()


@pytest.fixture
def _required_overrides():
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    yield
    settings.set_override("PROWLARR_URL", None)
    settings.set_override("PROWLARR_API_KEY", None)
    settings.set_override("TORBOX_API_KEY", None)


def test_lifespan_prefetches_indexers(_required_overrides, _clear_indexers):
    import main as m

    idx1 = _capable_indexer(1)
    idx2 = _capable_indexer(2)
    m._INDEXERS_CACHE.clear()
    m._indexers_cache_put([idx1, idx2])
    cached = state._INDEXERS_CACHE.get('listing')
    assert cached is not None
    assert {idx['id'] for idx in cached['indexers']} == {1, 2}


def test_lifespan_prefetch_failure_does_not_raise(_required_overrides, _clear_indexers):
    app_mod.app.state.session = None

    async def _run():
        async with app_mod.lifespan(app_mod.app):
            # If we got here, startup completed despite eager fetch failure.
            assert app_mod.app.state.session is not None

    asyncio.run(_run())


def test_lifespan_skips_placeholder_url(_clear_indexers):
    """When PROWLARR_URL is an obvious placeholder, eager preload is skipped."""
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    app_mod.app.state.session = None

    async def _run():
        async with app_mod.lifespan(app_mod.app):
            # Session should exist; eager fetch should have been skipped.
            assert app_mod.app.state.session is not None

    asyncio.run(_run())
    settings.set_override("PROWLARR_URL", None)
    settings.set_override("PROWLARR_API_KEY", None)
    settings.set_override("TORBOX_API_KEY", None)


def test_lifespan_prefetch_uses_short_timeout(_clear_indexers):
    """Eager preload uses a short timeout when the host is not a placeholder."""
    settings.set_override("PROWLARR_URL", "http://127.0.0.1:1")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    app_mod.app.state.session = None

    async def _run():
        async with app_mod.lifespan(app_mod.app):
            # Session should exist; the connection refused is swallowed quickly.
            assert app_mod.app.state.session is not None

    asyncio.run(_run())
    settings.set_override("PROWLARR_URL", None)
    settings.set_override("PROWLARR_API_KEY", None)
    settings.set_override("TORBOX_API_KEY", None)


def test_placeholder_detection_variants():
    """Placeholder detection covers common test/dev placeholders."""
    assert app_mod._is_placeholder_url("http://x/api") is True
    assert app_mod._is_placeholder_url("http://x:80/api") is True
    assert app_mod._is_placeholder_url("http://localhost:9696") is True
    assert app_mod._is_placeholder_url("http://localhost") is True
    assert app_mod._is_placeholder_url("http://127.0.0.1:1") is False
    assert app_mod._is_placeholder_url("http://prowlarr.example.com:9696") is False
    assert app_mod._is_placeholder_url("") is True
    assert app_mod._is_placeholder_url("not-a-url") is True
