"""Tests for the Torbox cache check + XML consolidation + scrape pipeline.

check_torbox_cache still uses the Torbox JSON API (FakeSession.post returns
JSON) and is unchanged. The consolidate_* / generate_torznab_* tests target the
new XML-native consolidate_and_emit_xml, which takes
``[(indexer_dict, xml_bytes), ...]`` and emits a merged RSS document.

Run from the repo root so `import main` resolves.
"""

import aiohttp
from lxml import etree as ET

import main as m
from pachelarr import settings
from tests._fakes import FakeCtx
from tests._torznab_helpers import build_rss, pair

_TORZNAB = "{http://torznab.com/schemas/2015/feed}"


async def _hs(params):
    async with aiohttp.ClientSession() as session:
        return await m.handle_search(params, session)


class FakeSession:
    def __init__(self, responses):
        self._responses = responses
        self._idx = 0
        self.last_payload = None
        self.last_headers = None

    def post(self, url, json, headers):
        self.last_payload = json
        self.last_headers = headers
        resp = self._responses[self._idx]
        self._idx = min(self._idx + 1, len(self._responses) - 1)
        return FakeCtx(resp[0], resp[1] if len(resp) > 1 else {})


def _attr_map(item):
    return {a.get("name"): a.get("value") for a in item.findall(_TORZNAB + "attr")}


def _guid_magnet(item):
    g = item.find("guid")
    return g.text if g is not None and g.text else ""


def _trs(magnet):
    if not magnet or "?" not in magnet:
        return []
    from urllib.parse import parse_qs, unquote
    try:
        return parse_qs(unquote(magnet.split("?", 1)[1])).get("tr", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# check_torbox_cache (Torbox JSON API) — unchanged behavior
# ---------------------------------------------------------------------------

async def test_check_torbox_cache_single_chunk_success():
    mapping = {"ABC123": True}
    session = FakeSession([(200, mapping)])
    out = await m.check_torbox_cache(session, ["ABC123"])
    assert out == {"abc123": True}
    assert session.last_payload == {"hashes": ["abc123"]}
    assert session.last_headers and 'Authorization' in session.last_headers


async def test_check_torbox_cache_chunking():
    hashes = [f"HASH{i:03d}" for i in range(350)]

    def make_map():
        return {h.lower(): True for h in hashes}

    session = FakeSession([(200, make_map), (200, make_map)])
    out = await m.check_torbox_cache(session, hashes)
    assert len(out) == 350
    for k in hashes:
        assert out.get(k.lower()) is True
    assert session.last_payload and isinstance(session.last_payload, dict)
    assert 'hashes' in session.last_payload
    assert session.last_headers and 'Authorization' in session.last_headers


async def test_check_torbox_cache_401():
    session = FakeSession([(401, {})])
    out = await m.check_torbox_cache(session, ["ABC123"])
    assert out == {}


async def test_check_torbox_cache_list_data_response():
    def data_fn():
        return {"data": [{"hash": "ABC123", "name": "file1"}]}
    session = FakeSession([(200, data_fn)])
    out = await m.check_torbox_cache(session, ["ABC123"])
    assert out == {"abc123": {"hash": "ABC123", "name": "file1"}}


async def test_check_torbox_cache_direct_list_response():
    data = [{"hash": "ABC123", "name": "file1"}]
    session = FakeSession([(200, data)])
    out = await m.check_torbox_cache(session, ["ABC123"])
    assert out == {"abc123": {"hash": "ABC123", "name": "file1"}}


async def test_check_torbox_cache_deduplication_order():
    responses = [(200, {"data": {"abc123": True}})]
    session = FakeSession(responses)
    input_hashes = ["ABC123", "abc123", "AbC123"]
    out = await m.check_torbox_cache(session, input_hashes)
    assert session.last_payload == {"hashes": ["abc123"]}
    assert out == {"abc123": True}


# ---------------------------------------------------------------------------
# extract_hashes_from_xml_pairs
# ---------------------------------------------------------------------------

def test_extract_info_hashes_order():
    """extract_hashes_from_xml_pairs returns unique lowercased hashes in order."""
    xml = build_rss([
        {"hash": "AAABBBccc111", "title": "A", "seeders": 1},
        {"hash": "aaabbbCCC111", "title": "A2", "seeders": 1},
        {"hash": "zzzyyyxxx999", "title": "Z", "seeders": 1},
        {"hash": "ZZZyyyXXX999", "title": "Z2", "seeders": 1},
    ])
    out = m.extract_hashes_from_xml_pairs([pair(1, xml)])
    assert out == ['aaabbbccc111', 'zzzyyyxxx999']


# ---------------------------------------------------------------------------
# consolidate_and_emit_xml — tracker merge, dedupe, canonical magnet, seeders
# ---------------------------------------------------------------------------

def test_consolidate_uncached_items_merges_trackers():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([
        {"hash": h, "title": "T1", "seeders": 1, "trackers": ["http://tracker1/announce"]},
        {"hash": h, "title": "T1", "seeders": 1, "trackers": ["http://tracker2/announce"]},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    mag = _guid_magnet(items[0])
    assert "tr=http://tracker1/announce" in mag
    assert "tr=http://tracker2/announce" in mag


def test_consolidate_all_items_dedupe_and_merge_cached():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([
        {"hash": h, "title": "A", "seeders": 1, "trackers": ["http://tracker1/announce"]},
        {"hash": h, "title": "B", "seeders": 10, "trackers": ["http://tracker2/announce"]},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {h.lower(): True})
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    attrs = _attr_map(items[0])
    assert int(attrs.get("seeders", 0)) >= settings.get_int("PACHELARR_SEEDERS_BOOST", 10000)
    mag = _guid_magnet(items[0])
    assert "tr=http://tracker1/announce" in mag
    assert "tr=http://tracker2/announce" in mag


def test_consolidated_magnet_uses_ampersand_between_xt_and_tr():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([
        {"hash": h, "title": "T1", "seeders": 1, "trackers": ["http://tracker1/announce"]},
        {"hash": h, "title": "T1", "seeders": 1, "trackers": ["http://tracker2/announce"]},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    mag = _guid_magnet(root.find(".//item"))
    assert "?tr=" not in mag
    assert "&tr=" in mag
    trackers = set(_trs(mag))
    assert "http://tracker1/announce" in trackers
    assert "http://tracker2/announce" in trackers


def test_generate_torznab_uses_uncached_seeders():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([{"hash": h, "title": "T1", "seeders": 1, "peers": 0,
                      "trackers": ["udp://tracker1:6969/announce"]}])
    out = m.consolidate_and_emit_xml(
        [pair(1, xml)], {}, uncached_seeders={h.lower(): {"seeders": 50, "leechers": 0}})
    attrs = _attr_map(ET.fromstring(out).find(".//item"))
    assert attrs.get("seeders") == "50"


def test_generate_torznab_emission_dedupe():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([
        {"hash": h, "title": "T1", "seeders": 1, "trackers": ["udp://tracker1:6969/announce"]},
        {"hash": h, "title": "T1-dup", "seeders": 2, "trackers": ["udp://tracker2:6969/announce"]},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    assert out.decode().count("<item>") == 1


def test_generate_torznab_pubdate_present():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([{"hash": h, "title": "T1", "seeders": 1,
                      "pubdate": "2025-05-10T16:57:09Z",
                      "trackers": ["udp://tracker1:6969/announce"]}])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    s = out.decode()
    assert "<pubDate>" in s
    import re
    assert re.search(r'<pubDate>\w{3}, \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} GMT</pubDate>', s)


def test_generate_torznab_enclosure_populated():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    from tests._torznab_helpers import build_item
    elem = build_item({"hash": h, "title": "T1", "seeders": 1,
                       "trackers": ["udp://tracker1:6969/announce"]})
    enc = elem.find("enclosure")
    if enc is not None:
        elem.remove(enc)
    xml = build_rss([elem])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    s = out.decode()
    assert 'enclosure url' in s
    import re
    mm = re.search(r'enclosure url="([^"]+)"', s)
    assert mm and mm.group(1) != ''


async def test_scrape_trackers_inverted_max(monkeypatch):
    """scrape_trackers_inverted aggregates per-metric max across trackers."""
    m._SCRAPE_CACHE.clear()

    async def fake_udp_scrape_tracker(host, port, chunks, timeout):
        hashes = [h for chunk in chunks for h in chunk]
        if host.endswith('tracker1'):
            return {h: {'seeders': (5 if h == 'abc1' else 4), 'leechers': 0, 'downloads': 0} for h in hashes}
        if host.endswith('tracker2'):
            return {h: {'seeders': (10 if h == 'abc1' else 0), 'leechers': 0, 'downloads': 0} for h in hashes}
        return {}

    monkeypatch.setattr('main._udp_scrape_tracker', fake_udp_scrape_tracker)
    tracker_map = {
        'udp://tracker1:6969/announce': ['abc1', 'abc2'],
        'udp://tracker2:6969/announce': ['abc1']
    }
    out = await m.scrape_trackers_inverted(tracker_map)
    assert out['abc1']['seeders'] == 10
    assert out['abc2']['seeders'] == 4


def test_consolidate_all_items_union_and_canonical():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([
        {"hash": h, "title": "A", "seeders": 5, "trackers": ["http://t1/announce"]},
        {"hash": h, "title": "B", "seeders": 12, "trackers": ["http://t2/announce"]},
        {"hash": h, "title": "C", "seeders": 3, "trackers": ["http://t3/announce"]},
    ])
    out = m.consolidate_and_emit_xml(
        [pair(1, xml)], {}, uncached_seeders={h.lower(): {"seeders": 20, "leechers": 0}})
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    it = items[0]
    assert it.find("title").text == "B"
    mag = _guid_magnet(it)
    assert "tr=http://t1/announce" in mag
    assert "tr=http://t2/announce" in mag
    assert "tr=http://t3/announce" in mag
    assert _attr_map(it).get("seeders") == "20"


async def test_full_pipeline_integration(monkeypatch):
    """End-to-end: extract hashes -> torbox cache -> consolidate + emit XML."""
    h1 = "abc123abc123abc123abc123abc123abc123abcd"
    h2 = "def456def456def456def456def456def456def4"
    xml = build_rss([
        {"hash": h1, "title": "CachedTitle", "seeders": 1, "peers": 0,
         "trackers": ["http://t1/announce"]},
        {"hash": h2, "title": "UncachedTitle", "seeders": 1, "peers": 0,
         "trackers": ["http://t2/announce"]},
    ])
    pairs = [pair(1, xml)]
    hashes = m.extract_hashes_from_xml_pairs(pairs)
    assert hashes == [h1.lower(), h2.lower()]

    class TorboxSession:
        def __init__(self):
            self.last_payload = None
            self.last_headers = None

        def post(self, url, json, headers):
            self.last_payload = json
            self.last_headers = headers
            # Mark h1 as cached.
            return FakeCtx(200, {h1.lower(): True})

    tb = TorboxSession()
    cached_status = await m.check_torbox_cache(tb, hashes)
    assert cached_status.get(h1.lower()) is True

    out = m.consolidate_and_emit_xml(
        pairs, cached_status, uncached_seeders={h2.lower(): {"seeders": 7, "leechers": 0}})
    decoded = out.decode()
    assert decoded.count('<item>') == 2
    assert '[CACHED] CachedTitle' in decoded
    assert 'torznab:attr name="seeders" value="7"' in decoded


async def test_prowlarr_has_duplicates_but_cachebox_dedupes(monkeypatch):
    """Duplicate infohashes across indexers consolidate to one <item> with
    merged trackers."""
    dup_hash = 'f7ef4d7c1a7697b055726959aa2380bf35a600d5'
    other_hash = '2222222222222222222222222222222222222222'
    xml1 = build_rss([{"hash": dup_hash, "title": "A", "seeders": 1,
                       "trackers": ["http://t1/announce"]}])
    xml2 = build_rss([{"hash": dup_hash, "title": "B", "seeders": 1,
                       "trackers": ["http://t2/announce"]},
                      {"hash": other_hash, "title": "Other", "seeders": 1}])
    pairs = [pair(1, xml1), pair(2, xml2)]

    async def fake_search(session, kwargs):
        return pairs

    monkeypatch.setattr('main.search_prowlarr', fake_search)
    from starlette.datastructures import QueryParams
    params = QueryParams({'q': 'Rick and Morty S01E02', 't': 'tvsearch'})
    resp = await _hs(params)
    decoded = resp.body.decode()
    item_count = decoded.count('<item>')
    assert item_count == 2  # dup_hash (merged) + other_hash
    # The duplicate infohash must appear in exactly one emitted <item> (its
    # infohash attr), even though the hash string also occurs in the merged
    # guid/link/enclosure magnets of that single item.
    root = ET.fromstring(resp.body)
    dup_items = [it for it in root.findall(".//item")
                 if _attr_map(it).get("infohash", "").lower() == dup_hash.lower()]
    assert len(dup_items) == 1


def test_consolidate_includes_guid_trackers():
    """Trackers carried in a <guid> magnet (rather than a synthesized one) are
    unioned into the emitted guid magnet."""
    h = "8cadfe07aaba94e59d1ab4d73235591c1874892b"
    from tests._torznab_helpers import build_item
    i1 = build_item({"hash": h, "title": "T1", "seeders": 1,
                     "magnet": f"magnet:?xt=urn:btih:{h}&dn=foo&tr=udp://tracker-a/announce"})
    i2 = build_item({"hash": h, "title": "T1b", "seeders": 2,
                     "magnet": f"magnet:?xt=urn:btih:{h}&dn=foo&tr=http://tracker-b/announce"})
    xml = build_rss([i1, i2])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    mag = _guid_magnet(ET.fromstring(out).find(".//item"))
    assert "tr=udp://tracker-a/announce" in mag
    assert "tr=http://tracker-b/announce" in mag


def test_consolidate_creates_canonical_magnet_when_missing():
    """An item lacking a magnet still gets a canonical magnet from the hash,
    merging trackers found in other items of the same group."""
    h = "aaaa1111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    xml = build_rss([
        {"hash": h, "title": "A", "seeders": 1, "trackers": ["udp://t1/announce"]},
        {"hash": h, "title": "B", "seeders": 10},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    it = ET.fromstring(out).find(".//item")
    mag = _guid_magnet(it)
    assert f"magnet:?xt=urn:btih:{h}" in mag
    assert "tr=udp://t1/announce" in mag


def test_generate_xml_emits_canonical_guid():
    """Emitted <guid> equals the canonical merged magnet for the infohash."""
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml = build_rss([
        {"hash": h, "title": "A", "seeders": 1, "trackers": ["http://t1/announce"]},
        {"hash": h, "title": "B", "seeders": 5, "trackers": ["http://t2/announce"]},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    guid = items[0].find("guid").text
    trs = set(_trs(guid))
    assert {"http://t1/announce", "http://t2/announce"} <= trs
    assert guid.startswith("magnet:?xt=urn:btih:")


def test_consolidate_includes_enclosure_trackers():
    """Trackers carried in an <enclosure url=magnet:...> are unioned."""
    h = "bbbb2222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    from tests._torznab_helpers import build_item
    i1 = build_item({"hash": h, "seeders": 1,
                     "enclosure": f"magnet:?xt=urn:btih:{h}&tr=udp://e1/announce"})
    # build_item sets link/guid from magnet only when 'magnet' given; force the
    # enclosure as the magnet source by removing link/guid magnet fallbacks.
    for tag in ("link", "guid"):
        el = i1.find(tag)
        if el is not None and el.text and "magnet:?" in el.text:
            el.text = "http://example.com/enc1"
    i2 = build_item({"hash": h, "seeders": 2, "trackers": ["udp://m2/announce"]})
    xml = build_rss([i1, i2])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    mag = _guid_magnet(ET.fromstring(out).find(".//item"))
    assert "tr=udp://e1/announce" in mag
    assert "tr=udp://m2/announce" in mag


def test_generate_guid_contains_unioned_trackers():
    """Unioned trackers appear in the emitted <guid> magnet."""
    h = "8cadfe07aaba94e59d1ab4d73235591c1874892b"
    xml = build_rss([
        {"hash": h, "title": "A", "seeders": 1,
         "trackers": ["udp://tracker.opentrackr.org:1337/announce"]},
        {"hash": h, "title": "B", "seeders": 2,
         "trackers": ["udp://9.rarbg.me:2970/announce"]},
    ])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    decoded = out.decode()
    assert "tr=udp://9.rarbg.me:2970/announce" in decoded
    assert "tr=udp://tracker.opentrackr.org:1337/announce" in decoded
