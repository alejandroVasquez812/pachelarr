"""Shared helpers for building minimal Torznab RSS XML used by the test suite.

These helpers construct XML bytes that mimic what Prowlarr's ``/<indexerId>/api``
Torznab passthrough returns, so tests can feed ``consolidate_and_emit_xml`` and
``extract_hashes_from_xml_pairs`` realistic inputs without a live Prowlarr.
"""
from lxml import etree as ET

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
_TORZNAB_PREFIX = "{%s}" % _TORZNAB_NS


def _attr(name, value):
    return _TORZNAB_PREFIX + "attr", {"name": name, "value": str(value)}


def build_item(item):
    """Build a single <item> Element from a simple dict.

    Supported dict keys (all optional unless noted):
      hash      - infohash value (emitted as torznab:attr name="infohash").
                  Required for a hash-item; omit for a non-hash item.
      title     - <title> text (default "T").
      link      - <link> text. Falls back to magnet if absent.
      guid      - <guid> text. Falls back to magnet if absent.
      magnet    - convenience: sets link+guid to this magnet when they are
                  absent, and (if no `hash`) derives the hash from xt=.
      seeders   - torznab:attr name="seeders" value (default 0).
      peers     - torznab:attr name="peers" value (default 0).
      size      - torznab:attr name="size" value (default 0).
      enclosure - <enclosure url=...> override (default: magnet or guid).
      pubdate   - <pubDate> text (default omitted).
      category  - <category> text (default omitted).
      attrs     - list of (name, value) pairs to add as extra torznab:attr
                  (pass-through preservation tests use this).
      trackers  - list of tracker URLs; when `magnet` is absent but `hash` is
                  present, a magnet is synthesized as
                  magnet:?xt=urn:btih:<hash>&tr=<t1>&tr=<t2>...
    """
    it = ET.Element("item")
    title = item.get("title", "T")
    ET.SubElement(it, "title").text = title

    h = item.get("hash")
    magnet = item.get("magnet")
    if magnet is None and h is not None:
        trackers = item.get("trackers") or []
        trs = "".join("&tr=" + t for t in trackers)
        magnet = "magnet:?xt=urn:btih:%s%s" % (h, trs)

    link = item.get("link")
    if link is None and magnet is not None:
        link = magnet
    ET.SubElement(it, "link").text = link if link is not None else ""

    guid = item.get("guid")
    if guid is None and magnet is not None:
        guid = magnet
    ET.SubElement(it, "guid").text = guid if guid is not None else ""

    if "pubdate" in item:
        ET.SubElement(it, "pubDate").text = item["pubdate"]
    if "category" in item:
        ET.SubElement(it, "category").text = str(item["category"])

    enc = item.get("enclosure")
    if enc is None:
        enc = magnet or guid or link or ""
    ET.SubElement(it, "enclosure", url=str(enc), type="application/x-bittorrent")

    seeders = item.get("seeders", 0)
    peers = item.get("peers", 0)
    size = item.get("size", 0)
    ET.SubElement(it, _attr("seeders", seeders)[0], name="seeders", value=str(seeders))
    ET.SubElement(it, _attr("peers", peers)[0], name="peers", value=str(peers))
    if h is not None:
        ET.SubElement(it, _TORZNAB_PREFIX + "attr", name="infohash", value=str(h))
    ET.SubElement(it, _TORZNAB_PREFIX + "attr", name="size", value=str(size))

    for name, value in (item.get("attrs") or []):
        ET.SubElement(it, _TORZNAB_PREFIX + "attr", name=name, value=str(value))

    return it


def build_rss(items):
    """Build a full <rss><channel>...<item/>...</channel></rss> bytes string.

    ``items`` is a list of item dicts (see build_item) OR pre-built Element
    objects. Returns serialized bytes with the torznab namespace declared.
    """
    rss = ET.Element("rss", version="2.0", nsmap={"torznab": _TORZNAB_NS})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Torbox Cached Indexer"
    for it in items:
        elem = it if isinstance(it, ET._Element) else build_item(it)
        channel.append(elem)
    return ET.tostring(rss, pretty_print=True, xml_declaration=True, encoding="UTF-8")


def empty_rss():
    """An empty <rss><channel/></rss> document as bytes."""
    return build_rss([])


def indexer(id_):
    return {"id": id_, "enable": True, "supportsSearch": True, "protocol": "torrent"}


def pair(indexer_id, xml_bytes):
    """Build an (indexer_dict, xml_bytes) pair for consolidate_and_emit_xml."""
    return (indexer(indexer_id), xml_bytes)