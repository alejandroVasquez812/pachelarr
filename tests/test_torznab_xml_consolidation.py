"""Dedicated tests for consolidate_and_emit_xml (native XML consolidation).

Covers the new XML-native consolidation + emit pipeline that replaces the old
consolidate_all_items / generate_torznab_xml pair. Inputs are
``[(indexer_dict, xml_bytes), ...]`` (Torznab XML from Prowlarr's
``/<indexerId>/api`` passthrough); output is a single merged RSS document.

Run from the repo root so `import main` resolves.
"""
from urllib.parse import parse_qs, unquote

from lxml import etree as ET

import main as m
from pachelarr import settings
from tests._torznab_helpers import build_item, build_rss, pair

_TORZNAB = "{http://torznab.com/schemas/2015/feed}"


def _attr_map(item_elem):
    return {
        a.get("name"): a.get("value")
        for a in item_elem.findall(_TORZNAB + "attr")
    }


def _guid(item_elem):
    g = item_elem.find("guid")
    return g.text if g is not None else ""


def _link(item_elem):
    g = item_elem.find("link")
    return g.text if g is not None else ""


def _trs(magnet):
    if not magnet or "?" not in magnet:
        return []
    try:
        return parse_qs(unquote(magnet.split("?", 1)[1])).get("tr", [])
    except Exception:
        return []


def test_two_indexers_same_hash_disjoint_trackers_merges():
    h = "abc123abc123abc123abc123abc123abc123abcd"
    xml1 = build_rss([{"hash": h, "title": "FromIndexer1", "seeders": 5,
                       "trackers": ["http://t1/announce"]}])
    xml2 = build_rss([{"hash": h, "title": "FromIndexer2", "seeders": 12,
                       "trackers": ["http://t2/announce"]}])
    out = m.consolidate_and_emit_xml([pair(1, xml1), pair(2, xml2)], {})
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    it = items[0]
    attrs = _attr_map(it)
    assert attrs.get("infohash").lower() == h.lower()
    # Highest original seeders wins as canonical metadata.
    assert it.find("title").text == "FromIndexer2"
    assert attrs.get("seeders") == "12"
    guid = _guid(it)
    trs = set(_trs(guid))
    assert "http://t1/announce" in trs
    assert "http://t2/announce" in trs


def test_cached_hash_prefix_and_boosted_seeders():
    h = "c0ffee00000000000000000000000000000000aa"
    xml = build_rss([{"hash": h, "title": "CachedShow", "seeders": 3,
                      "trackers": ["http://t1/announce"]}])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {h.lower(): True})
    root = ET.fromstring(out)
    it = root.find(".//item")
    assert it.find("title").text.startswith("[CACHED] ")
    assert "CachedShow" in it.find("title").text
    seeders = int(_attr_map(it).get("seeders", 0))
    assert seeders >= settings.get_int("PACHELARR_SEEDERS_BOOST", 10000)


def test_uncached_scrape_seeders_peers_take_max():
    h = "deadbeef0000000000000000000000000000beef"
    xml = build_rss([{"hash": h, "title": "ScrapeShow", "seeders": 4, "peers": 2,
                      "trackers": ["http://t1/announce"]}])
    scrape = {h.lower(): {"seeders": 50, "leechers": 7}}
    out = m.consolidate_and_emit_xml([pair(1, xml)], {}, uncached_seeders=scrape)
    root = ET.fromstring(out)
    attrs = _attr_map(root.find(".//item"))
    assert attrs.get("seeders") == "50"
    assert attrs.get("peers") == "7"


def test_passthrough_attr_preserved():
    h = "feedface0000000000000000000000000000feed"
    # The canonical item is the highest-seeder one; put the pass-through attrs
    # on it so they survive the merge (consolidation keeps the canonical node's
    # non-mutated attrs intact; only trackers are unioned across the group).
    xml = build_rss([{"hash": h, "title": "Pass2", "seeders": 2,
                      "attrs": [("downloadvolumefactor", "0"),
                                ("uploadvolumefactor", "1")]},
                     {"hash": h, "title": "Pass", "seeders": 1}])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    attrs = _attr_map(root.find(".//item"))
    assert attrs.get("downloadvolumefactor") == "0"
    assert attrs.get("uploadvolumefactor") == "1"


def test_non_hash_item_emitted_unchanged():
    xml = build_rss([{"title": "NoHash", "link": "http://example.com/x",
                      "guid": "plain-guid", "seeders": 9, "peers": 3}])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    it = items[0]
    assert it.find("title").text == "NoHash"
    assert it.find("guid").text == "plain-guid"
    attrs = _attr_map(it)
    assert attrs.get("seeders") == "9"
    assert attrs.get("peers") == "3"
    assert "infohash" not in attrs


def test_malformed_doc_skipped_others_consolidate():
    h = "112233445566778899aabbccddeeff0011223344"
    good = build_rss([{"hash": h, "title": "Good", "seeders": 5,
                       "trackers": ["http://t1/announce"]}])
    malformed = b"<?xml version='1.0'?><rss><channel><item><title>broken"
    out = m.consolidate_and_emit_xml(
        [pair(1, malformed), pair(2, good)], {}
    )
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    assert items[0].find("title").text == "Good"


def test_empty_pairs_returns_valid_empty_rss():
    out = m.consolidate_and_emit_xml([], {})
    root = ET.fromstring(out)
    channel = root.find("channel")
    assert channel is not None
    assert channel.find("title").text == "Torbox Cached Indexer"
    assert root.findall(".//item") == []


def test_pubdate_normalized_iso_to_rfc1123():
    h = "cafebabe0000000000000000000000000000aaaa"
    xml = build_rss([{"hash": h, "title": "Dated", "seeders": 1,
                      "pubdate": "2025-05-10T16:57:09Z",
                      "trackers": ["http://t1/announce"]}])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    pub = root.find(".//item/pubDate")
    assert pub is not None
    # RFC-1123 form: Sat, 10 May 2025 16:57:09 GMT
    import re
    assert re.match(r"\w{3}, \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} GMT", pub.text)


def test_enclosure_populated_from_magnet_when_missing():
    h = "1234567890abcdef1234567890abcdef12345678"
    it = build_item({"hash": h, "title": "NoEnc", "seeders": 1,
                     "trackers": ["http://t1/announce"]})
    # Remove the enclosure built by the helper so the consolidator must
    # synthesize one from the magnet/guid.
    enc = it.find("enclosure")
    if enc is not None:
        it.remove(enc)
    xml = build_rss([it])
    out = m.consolidate_and_emit_xml([pair(1, xml)], {})
    root = ET.fromstring(out)
    enc_out = root.find(".//item/enclosure")
    assert enc_out is not None
    assert enc_out.get("url"), "enclosure url should be populated"
    assert "magnet:?" in enc_out.get("url") or enc_out.get("url").startswith("http")


def test_dedupe_across_indexers_one_item_per_hash():
    h = "aabbccddeeff00112233445566778899aabbccdd"
    xml1 = build_rss([{"hash": h, "title": "A", "seeders": 1,
                       "trackers": ["http://t1/announce"]}])
    xml2 = build_rss([{"hash": h, "title": "B", "seeders": 1,
                       "trackers": ["http://t2/announce"]}])
    xml3 = build_rss([{"hash": h, "title": "C", "seeders": 1,
                       "trackers": ["http://t3/announce"]}])
    out = m.consolidate_and_emit_xml(
        [pair(1, xml1), pair(2, xml2), pair(3, xml3)], {}
    )
    root = ET.fromstring(out)
    items = root.findall(".//item")
    assert len(items) == 1
    trs = set(_trs(_guid(items[0])))
    assert {"http://t1/announce", "http://t2/announce", "http://t3/announce"} <= trs
