"""Shared test fakes for the Pachelarr test suite.

These fakes are standalone (they do not import ``main``) and provide the
common aiohttp-like context-manager response objects used across multiple
test modules. Fakes that have bespoke behavior (XML routing, GET-by-URL
routing, post() semantics) remain in their respective test modules.
"""
import aiohttp


class FakeCtx:
    """Minimal aiohttp response context manager for JSON endpoints.

    ``data`` is the JSON body. It may be a plain object or a zero-arg callable
    returning the body (re-invoked on each ``.json()`` call). On
    ``raise_for_status`` a status >= 400 raises ``aiohttp.ClientError``,
    matching the real aiohttp contract used by ``check_torbox_cache``.
    """

    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if callable(self._data):
            return self._data()
        return self._data

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientError(f"status {self.status}")