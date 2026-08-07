"""Tests for resolve_magnet_via_download and the magnet-resolution cache.

Validates that when Prowlarr's JSON API omits the magnet (returning only a
downloadUrl proxy), the tracker scraper can still recover the real magnet —
either from a redirect Location header or from the response body — and cache it.
"""
import pytest

import main as m
from main import _magnet_cache_get, _magnet_cache_put, resolve_magnet_via_download


class FakeResp:
    def __init__(self, status=200, location=None, body=""):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """Minimal aiohttp-like session for resolve_magnet_via_download.

    Calls recorded in order. Each entry maps to a FakeResp or a callable
    (url -> FakeResp) for redirect-chain tests.
    """
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, allow_redirects=False, timeout=None, headers=None):
        self.calls.append(url)
        r = self._responses.pop(0)
        if callable(r):
            return r(url)
        return r


MAGNET = ("magnet:?xt=urn:btih:be2dd3fd4d7a1938cd00d7a92910b132a92dc25b"
          "&tr=udp://tracker.opentrackr.org:1337/announce"
          "&tr=udp://open.demonii.com:1337/announce")


@pytest.fixture(autouse=True)
def clear_magnet_cache():
    m._MAGNET_CACHE.clear()
    yield
    m._MAGNET_CACHE.clear()


async def test_resolve_magnet_from_redirect_location():
    # Prowlarr download proxy returns 302 with the magnet as Location.
    sess = FakeSession([FakeResp(status=302, location=MAGNET)])
    got = await resolve_magnet_via_download(sess, "http://prowlarr/download?apikey=x&link=y")
    assert got == MAGNET
    assert "magnet:?" in got
    assert "tr=" in got


async def test_resolve_magnet_from_response_body():
    # Some proxies 200 with the magnet inline (text/uri-list or plain).
    sess = FakeSession([FakeResp(status=200, body=f"\n{MAGNET}\n")])
    got = await resolve_magnet_via_download(sess, "http://prowlarr/download?apikey=x&link=y")
    assert got == MAGNET


async def test_resolve_magnet_from_body_extracts_until_whitespace():
    body = f"garbage {MAGNET} trailing"
    sess = FakeSession([FakeResp(status=200, body=body)])
    got = await resolve_magnet_via_download(sess, "http://prowlarr/download?apikey=x&link=y")
    assert got == MAGNET
    assert "trailing" not in got


async def test_resolve_returns_none_when_no_magnet():
    # 200 with arbitrary HTML / non-magnet body.
    sess = FakeSession([FakeResp(status=200, body="<html>not a magnet</html>")])
    got = await resolve_magnet_via_download(sess, "http://prowlarr/download?apikey=x&link=y")
    assert got is None


async def test_resolve_follows_http_redirect_then_finds_magnet():
    # First hop: 302 to another http URL. Second hop: 302 to the magnet.
    def second(url):
        return FakeResp(status=302, location=MAGNET)
    sess = FakeSession([
        FakeResp(status=302, location="http://prowlarr/second"),
        second,
    ])
    got = await resolve_magnet_via_download(sess, "http://prowlarr/download?apikey=x&link=y")
    assert got == MAGNET
    assert len(sess.calls) == 2


async def test_resolve_none_input_returns_none():
    sess = FakeSession([])
    got = await resolve_magnet_via_download(sess, None)
    assert got is None


def test_magnet_cache_roundtrip_and_negative_cache():
    # Positive cache
    _magnet_cache_put("ABC123", MAGNET)
    assert _magnet_cache_get("ABC123") == MAGNET
    # Negative cache (None sentinel) - should return None, not raise KeyError
    _magnet_cache_put("DEF456", None)
    assert _magnet_cache_get("DEF456") is None
    # Missing key raises KeyError (so the resolver knows to fetch)
    with pytest.raises(KeyError):
        _magnet_cache_get("ZZZ999")
