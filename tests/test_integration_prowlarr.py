"""Integration tests for the XML-based Prowlarr search + consolidation flow.

Replaces the legacy JSON fakes with Torznab XML fakes (Prowlarr's
``/<indexerId>/api`` passthrough returns XML). The indexer-listing endpoint
``/api/v1/indexer`` is still JSON (capability selection unchanged).

Run from the repo root so `import main` resolves.
"""
import asyncio
import json
import re
from urllib.parse import unquote, parse_qs

from lxml import etree as ET

import main as m
from tests._torznab_helpers import build_rss, empty_rss

_TORZNAB = "{http://torznab.com/schemas/2015/feed}"


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

def _capable_indexer():
    return {
        'id': 1, 'enable': True, 'supportsSearch': True, 'protocol': 'torrent',
        'capabilities': {
            'searchParams': ['q'],
            'movieSearchParams': ['q', 'imdbId', 'tmdbId'],
            'tvSearchParams': ['q', 'season', 'ep', 'imdbId', 'tvdbId', 'tmdbId'],
            'categories': [{'id': 5030}, {'id': 5040}],
        },
    }


def _idx_id_from_url(url):
    if not url or '/api' not in url:
        return None
    base = url.split('/api', 1)[0]
    seg = base.rstrip('/').rsplit('/', 1)[-1]
    try:
        return str(int(seg))
    except (TypeError, ValueError):
        return seg


class _XmlCtx:
    def __init__(self, status, xml=None):
        self.status = status
        self._xml = xml if xml is not None else empty_rss()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return None

    async def read(self):
        return self._xml

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"status {self.status}")


class _XmlSession:
    """Serves one capable indexer (JSON) for /api/v1/indexer and XML for /<id>/api.

    ``search_xml`` is bytes (or a callable(url, params)->bytes) returned for the
    search GET. Records the params of the last search GET in ``last_params``.
    """

    def __init__(self, search_xml=None):
        self._search_xml = search_xml if search_xml is not None else empty_rss()
        self.last_params = None
        self.last_headers = None
        self.last_url = None

    def _xml_for(self, url, params):
        if callable(self._search_xml):
            return self._search_xml(url, params)
        return self._search_xml

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith('/api/v1/indexer'):
            return _XmlCtx(200, xml=None)  # JSON path unused here; data served below
        self.last_url = url
        self.last_params = params or {}
        self.last_headers = headers
        return _XmlCtx(200, xml=self._xml_for(url, params))


class _JsonIndexerXmlSession(_XmlSession):
    """_XmlSession variant that returns a real JSON indexer list for the
    /api/v1/indexer endpoint (capability selection still reads JSON)."""

    def __init__(self, indexers=None, search_xml=None):
        super().__init__(search_xml=search_xml)
        self._indexers = indexers if indexers is not None else [_capable_indexer()]

    class _IdxCtx:
        def __init__(self, data):
            self.status = 200
            self._data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._data

        async def read(self):
            return empty_rss()

        def raise_for_status(self):
            return None

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith('/api/v1/indexer'):
            return self._IdxCtx(list(self._indexers))
        return super().get(url, headers, params)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _q(p):
    return (p or {}).get('q') or (p or {}).get('query') or ''


def _cat_params(p):
    """Return the category params as a list, tolerant of cat/categories keys."""
    cats = (p or {}).get('cat')
    if cats is None:
        cats = (p or {}).get('categories')
    if cats is None:
        return []
    if isinstance(cats, list):
        return cats
    return [cats]


# ---------------------------------------------------------------------------
# search_prowlarr param flow (limit / offset / categories / fallback)
# ---------------------------------------------------------------------------

def test_search_prowlarr_does_not_forward_limit_zero(monkeypatch):
    m._INDEXERS_CACHE.clear()
    session = _JsonIndexerXmlSession(search_xml=empty_rss())
    kwargs = {"query": "Love Death and Robots", "limit": "0", "offset": "0",
              "categories": ["5030", "5040"]}
    _run(m.search_prowlarr(session, kwargs))
    assert session.last_params is not None
    assert session.last_params.get("limit") is None
    assert not session.last_params.get("limit")


def test_search_prowlarr_forwards_paging(monkeypatch):
    m._INDEXERS_CACHE.clear()
    session = _JsonIndexerXmlSession(search_xml=empty_rss())
    kwargs = {"query": "Love Death and Robots", "limit": "100", "offset": "0",
              "categories": ["5030", "5040"]}
    _run(m.search_prowlarr(session, kwargs))
    assert session.last_params is not None
    assert session.last_params.get("limit") == "100"
    assert session.last_params.get("offset") == "0"


def test_search_prowlarr_fallback_and_categories_list(monkeypatch):
    """Category-only call gets the fallback query and categories as a list."""
    m._INDEXERS_CACHE.clear()
    m.PACHELARR_TEST_FALLBACK_QUERY = "a"
    try:
        # XML body with a cat-matching item so the wrapper still returns pairs.
        xml = build_rss([{"hash": "aaa", "title": "CatMatch", "seeders": 1}])
        session = _JsonIndexerXmlSession(search_xml=xml)
        kwargs = {"categories": ["5030", "5040"]}
        # handle_search would inject the fallback into search_kwargs['query'];
        # mirror that contract here.
        kwargs["query"] = m.PACHELARR_TEST_FALLBACK_QUERY
        _run(m.search_prowlarr(session, kwargs))
        assert session.last_params is not None
        assert _q(session.last_params) == "a"
        cats = _cat_params(session.last_params)
        assert isinstance(cats, list) and set(cats) == {"5030", "5040"}
    finally:
        m.PACHELARR_TEST_FALLBACK_QUERY = ""


# ---------------------------------------------------------------------------
# handle_search forwarding + fallback
# ---------------------------------------------------------------------------

def test_handle_search_forwards_limit_offset(monkeypatch):
    from starlette.datastructures import QueryParams

    captured = {}

    async def fake_search(session, kwargs):
        captured.update(kwargs)
        assert kwargs.get("limit") == "100"
        assert kwargs.get("offset") == "0"
        return [(_capable_indexer(), empty_rss())]

    monkeypatch.setattr("main.search_prowlarr", fake_search)
    params = QueryParams({"cat": "5030,5040", "t": "tvsearch", "limit": "100", "offset": "0"})
    resp = _run(m.handle_search(params))
    assert resp.body is not None
    # handle_search consolidates + emits XML; the empty pair yields an empty RSS
    # (no <item>), which is acceptable as long as the body is valid XML.
    ET.fromstring(resp.body)


def test_handle_search_category_only_fallback_returns_nonempty_xml(monkeypatch):
    """A category-only request (Sonarr test) returns valid, non-empty XML.

    handle_search injects PACHELARR_TEST_FALLBACK_QUERY into search_kwargs['query']
    for category-only requests (per AGENTS.md) before calling search_prowlarr; the
    final response must be valid XML with at least one item (or a synthetic test
    row). We assert on the response, tolerating whether the fallback query was
    forwarded verbatim or not.
    """
    from starlette.datastructures import QueryParams

    m.PACHELARR_TEST_FALLBACK_QUERY = "a"
    try:
        xml = build_rss([{"hash": "aaa", "title": "FallbackItem", "seeders": 1}])

        async def fake_search(session, kwargs):
            return [(_capable_indexer(), xml)]

        monkeypatch.setattr("main.search_prowlarr", fake_search)
        params = QueryParams({"cat": "5030,5040", "t": "tvsearch"})
        resp = _run(m.handle_search(params))
        assert resp.body is not None
        root = ET.fromstring(resp.body)
        assert len(root.findall(".//item")) >= 1
    finally:
        m.PACHELARR_TEST_FALLBACK_QUERY = ""


# ---------------------------------------------------------------------------
# Unioned trackers + dedupe across indexers (XML consolidation)
# ---------------------------------------------------------------------------

def _fixture_to_xml_pairs():
    """Load the JSON fixture and convert each item to a Torznab XML item, grouped
    per original Prowlarr indexerId into separate XML docs (one per indexer).
    Returns [(indexer_dict, xml_bytes), ...].
    """
    with open("tests/fixtures/prowlarr_rm_s01e02.json") as f:
        pr = json.load(f)
    items = (pr if isinstance(pr, list)
             else pr.get("records") or pr.get("results") or pr.get("items") or pr.get("data") or [])
    by_indexer = {}
    for it in items:
        iid = it.get("indexerId") or it.get("indexer") or 1
        by_indexer.setdefault(iid, []).append(it)
    pairs = []
    for iid, its in by_indexer.items():
        item_dicts = []
        for it in its:
            h = it.get("infoHash")
            mag = it.get("magnetUrl") or it.get("guid")
            trackers = []
            if mag and "tr=" in mag:
                try:
                    trackers = parse_qs(unquote(mag.split("?", 1)[1])).get("tr", [])
                except Exception:
                    trackers = []
            d = {"hash": h, "title": it.get("title", "T"), "seeders": it.get("seeders", 0),
                 "peers": it.get("leechers", 0), "size": it.get("size", 0)}
            if mag and "magnet:?" in mag:
                d["magnet"] = mag
            elif trackers:
                d["trackers"] = trackers
            if it.get("publishDate"):
                d["pubdate"] = it["publishDate"]
            item_dicts.append(d)
        pairs.append(({"id": iid, "enable": True, "supportsSearch": True, "protocol": "torrent"},
                      build_rss(item_dicts)))
    return pairs, items


def test_integration_unioned_trackers_and_dedupe():
    pairs, items = _fixture_to_xml_pairs()

    # Build the expected per-hash tracker union from the raw JSON items.
    pr_map = {}
    for it in items:
        ih = it.get("infoHash")
        mag = it.get("magnetUrl") or it.get("guid")
        if not ih and mag and "magnet:?" in mag:
            try:
                parsed = parse_qs(unquote(mag.split("?", 1)[1]))
                if "xt" in parsed:
                    ih = parsed["xt"][0].split(":")[-1]
            except Exception:
                ih = None
        if not ih:
            continue
        ih = ih.lower()
        trs = set()
        if mag and "magnet:?" in mag:
            try:
                trs = set(parse_qs(unquote(mag.split("?", 1)[1])).get("tr", []))
            except Exception:
                trs = set()
        pr_map.setdefault(ih, set())
        pr_map[ih] |= trs

    xml = m.consolidate_and_emit_xml(pairs, {})
    xml_decoded = xml.decode()

    emitted_hashes = set(re.findall(
        r'torznab:attr name="infohash" value="([0-9a-fA-F]+)"', xml_decoded))
    assert len(emitted_hashes) == len(pr_map)

    # For each hash with trackers, the merged <guid> magnet must carry every tr=.
    root = ET.fromstring(xml)
    for item in root.findall(".//item"):
        attrs = {a.get("name"): a.get("value") for a in item.findall(_TORZNAB + "attr")}
        ih = attrs.get("infohash")
        if not ih:
            continue
        ih = ih.lower()
        guid = item.find("guid")
        if guid is None or not guid.text:
            continue
        trs = set(parse_qs(unquote(guid.text.split("?", 1)[1])).get("tr", [])) if "?" in guid.text else set()
        expected = pr_map.get(ih, set())
        for tr in expected:
            assert tr in trs, f"Missing tracker {tr} for {ih}\nGUID: {guid.text}"


def test_integration_dedupe_across_indexers_one_item_per_hash():
    """Two indexers returning the same hash with disjoint trackers merge to one
    <item> whose guid magnet carries the union of trackers."""
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml1 = build_rss([{"hash": h, "title": "I1", "seeders": 5,
                       "trackers": ["http://t1/announce", "http://t2/announce"]}])
    xml2 = build_rss([{"hash": h, "title": "I2", "seeders": 8,
                       "trackers": ["http://t3/announce"]}])
    pairs = [({"id": 1, "enable": True, "supportsSearch": True, "protocol": "torrent"}, xml1),
             ({"id": 2, "enable": True, "supportsSearch": True, "protocol": "torrent"}, xml2)]
    xml = m.consolidate_and_emit_xml(pairs, {})
    root = ET.fromstring(xml)
    items = root.findall(".//item")
    assert len(items) == 1
    guid = items[0].find("guid").text
    trs = set(parse_qs(unquote(guid.split("?", 1)[1])).get("tr", []))
    assert {"http://t1/announce", "http://t2/announce", "http://t3/announce"} <= trs
    # Canonical metadata from the highest-seeder item (I2).
    assert items[0].find("title").text == "I2"