import logging
from urllib.parse import parse_qs, unquote

from lxml import etree as ET

from pachelarr import settings, state

logger = logging.getLogger("pachelarr")


def _xml_attr(item, name):
    """Return the value attribute of the first <torznab:attr name=name> on item, or None."""
    for el in item.iter(f'{{{state._TORZNAB_NS}}}attr'):
        if el.get('name') == name:
            return el.get('value')
    return None


def _set_xml_attr(item, name, value):
    """Set the value attribute of the first <torznab:attr name=name>; create if missing."""
    for el in item.iter(f'{{{state._TORZNAB_NS}}}attr'):
        if el.get('name') == name:
            el.set('value', value)
            return
    ET.SubElement(item, f'{{{state._TORZNAB_NS}}}attr', name=name, value=value)


def _magnet_from_xml_item(item):
    """Return the first magnet:? URI found in <link>/<guid>/<enclosure url> of an XML item."""
    for tag in ('link', 'guid'):
        el = item.find(tag)
        if el is not None and el.text and 'magnet:?' in el.text:
            return el.text
    enc = item.find('enclosure')
    if enc is not None:
        u = enc.get('url')
        if u and 'magnet:?' in u:
            return u
    return None


def _proxy_url_from_xml_item(item):
    """Return the first http(s):// URL in <link>/<enclosure url> (Prowlarr proxy download URL)."""
    for tag in ('link',):
        el = item.find(tag)
        if el is not None and el.text and el.text.startswith(('http://', 'https://')):
            return el.text
    enc = item.find('enclosure')
    if enc is not None:
        u = enc.get('url')
        if u and u.startswith(('http://', 'https://')):
            return u
    return None


def _infohash_from_xml_item(item):
    """Return a lowercased infohash from a <torznab:attr name=infohash> else parse xt=urn:btih:."""
    ih = _xml_attr(item, 'infohash')
    if ih:
        return ih.lower()
    mag = _magnet_from_xml_item(item)
    if mag:
        try:
            parsed_magnet = parse_qs(unquote(mag.split('?')[1]))
            if 'xt' in parsed_magnet:
                return parsed_magnet['xt'][0].split(':')[-1].lower()
        except (IndexError, ValueError, KeyError):
            return None
    return None


def extract_hashes_from_xml_pairs(indexer_xml_pairs):
    """Return unique lowercased infohashes in first-seen order across XML pairs."""
    raw_hashes = []
    for _indexer, xml_bytes in (indexer_xml_pairs or []):
        if not xml_bytes:
            continue
        try:
            doc = ET.fromstring(xml_bytes)
        except ET.XMLSyntaxError:
            continue
        for item in doc.iter('item'):
            ih = _infohash_from_xml_item(item)
            if ih:
                raw_hashes.append(ih)
    return dedupe_hashes_preserve_order(raw_hashes)


def parse_trackers_from_magnet(magnet_uri):
    """Extract tracker URLs from a magnet URI (tr= parameters)."""
    if not magnet_uri:
        return []
    try:
        query = magnet_uri.split('?')[1]
    except IndexError:
        return []
    trackers = []
    for part in query.split('&'):
        if part.startswith('tr='):
            val = part.split('=', 1)[1]
            trackers.append(unquote(val))
    out = []
    seen = set()
    for t in trackers:
        t_str = t.strip()
        if not t_str:
            continue
        if t_str not in seen:
            out.append(t_str)
            seen.add(t_str)
    return out


def _get_magnet_uri_for_item(item):
    """Return a magnet URI string from item, trying 'magnetUri' then 'guid'."""
    if not item:
        return None
    if item.get('magnetUri'):
        return item.get('magnetUri')
    g = item.get('guid')
    if isinstance(g, str) and 'magnet:?' in g:
        return g
    enc = item.get('enclosure')
    if isinstance(enc, dict) and isinstance(enc.get('url'), str) and 'magnet:?' in enc.get('url'):
        return enc.get('url')
    if isinstance(enc, str) and 'magnet:?' in enc:
        return enc
    return None


def infohash_from_item(item):
    """Return a lowercase infohash for a dict item, trying 'infoHash' then magnet parsing."""
    info = item.get('infoHash') if item else None
    if info:
        try:
            return info.lower()
        except AttributeError:
            return info
    mag = _get_magnet_uri_for_item(item)
    if mag:
        try:
            parsed_magnet = parse_qs(unquote(mag.split('?')[1]))
            if 'xt' in parsed_magnet:
                return parsed_magnet['xt'][0].split(':')[-1].lower()
        except (IndexError, ValueError, KeyError):
            return None
    return None


def _normalize_pubdate(raw):
    """Parse a raw pubDate string to an RFC1123-formatted GMT string."""
    from datetime import datetime, timezone
    if raw:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.strftime('%a, %d %b %Y %H:%M:%S GMT')


def consolidate_and_emit_xml(indexer_xml_pairs, cached_status, uncached_seeders=None):
    """Parse per-indexer Torznab XML, consolidate by infohash, and emit one merged RSS."""
    cached_status = {k.lower(): v for k, v in (cached_status or {}).items()}
    uncached_seeders = uncached_seeders or {}

    records = []
    non_hash_items = []
    for _indexer, xml_bytes in (indexer_xml_pairs or []):
        if not xml_bytes:
            continue
        try:
            doc = ET.fromstring(xml_bytes)
        except ET.XMLSyntaxError as e:
            logger.warning(f"consolidate_and_emit_xml: skipping malformed XML doc: {e}")
            continue
        for item in doc.iter('item'):
            ih = _infohash_from_xml_item(item)
            mag = _magnet_from_xml_item(item)
            proxy = _proxy_url_from_xml_item(item)
            if ih:
                records.append((item, ih, mag, proxy))
            else:
                non_hash_items.append(item)

    groups = {}
    order = []
    for (item, ih, mag, proxy) in records:
        if ih not in groups:
            groups[ih] = []
            order.append(ih)
        groups[ih].append((item, mag, proxy))

    rss = ET.Element("rss", version="2.0", nsmap={'torznab': state._TORZNAB_NS})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Torbox Cached Indexer"

    emitted = set()
    for ih in order:
        if ih in emitted:
            continue
        emitted.add(ih)
        group = groups[ih]

        def _orig_seeders(rec):
            v = _xml_attr(rec[0], 'seeders')
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0
        canonical_rec = max(group, key=_orig_seeders)
        canonical_item = canonical_rec[0]
        had_magnet = canonical_rec[1] is not None

        trackers = []
        seen = set()
        for (_item, mag, _proxy) in group:
            for tr in parse_trackers_from_magnet(mag):
                if tr not in seen:
                    seen.add(tr)
                    trackers.append(tr)

        base = None
        base_mag = canonical_rec[1]
        if base_mag and 'magnet:?' in base_mag:
            try:
                parsed = parse_qs(unquote(base_mag.split('?', 1)[1]))
                if 'xt' in parsed:
                    base = f"magnet:?xt={parsed['xt'][0]}"
            except (IndexError, ValueError, KeyError):
                base = None
        if not base:
            base = f"magnet:?xt=urn:btih:{ih}"
        tr_parts = '&'.join('tr=' + t for t in trackers)
        if tr_parts:
            connector = '&' if '?' in base else '?'
            canonical_magnet = f"{base}{connector}{tr_parts}"
        else:
            canonical_magnet = base

        guid_el = canonical_item.find('guid')
        if guid_el is None:
            guid_el = ET.SubElement(canonical_item, 'guid')
        guid_el.text = canonical_magnet
        if had_magnet:
            link_el = canonical_item.find('link')
            if link_el is None:
                link_el = ET.SubElement(canonical_item, 'link')
            link_el.text = canonical_magnet

        is_cached = ih in cached_status
        title_el = canonical_item.find('title')
        if is_cached:
            if title_el is not None:
                cur = (title_el.text or '').lstrip()
                if not cur.startswith('[CACHED] '):
                    title_el.text = f"[CACHED] {cur}"

        orig_seeders = _orig_seeders(canonical_rec)
        try:
            v = _xml_attr(canonical_item, 'peers')
            orig_leechers = int(v) if v is not None else 0
        except (TypeError, ValueError):
            orig_leechers = 0
        if is_cached:
            boost = settings.get_int("PACHELARR_SEEDERS_BOOST", 10000)
            _set_xml_attr(canonical_item, 'seeders', str(max(orig_seeders, boost)))
        else:
            entry = uncached_seeders.get(ih) or {}
            try:
                scrape_seeders = int(entry.get('seeders', 0) or 0)
            except (TypeError, ValueError):
                scrape_seeders = 0
            try:
                scrape_leechers = int(entry.get('leechers', 0) or 0)
            except (TypeError, ValueError):
                scrape_leechers = 0
            if scrape_seeders or scrape_leechers:
                _set_xml_attr(canonical_item, 'seeders', str(max(orig_seeders, scrape_seeders)))
                _set_xml_attr(canonical_item, 'peers', str(max(orig_leechers, scrape_leechers)))

        enc_el = canonical_item.find('enclosure')
        enc_url = canonical_magnet or canonical_rec[2] or guid_el.text
        if enc_el is None:
            ET.SubElement(canonical_item, 'enclosure', url=enc_url or '', type='application/x-bittorrent')
        else:
            if not enc_el.get('url'):
                enc_el.set('url', enc_url or '')

        pub_el = canonical_item.find('pubDate')
        if pub_el is None:
            pub_el = ET.SubElement(canonical_item, 'pubDate')
        pub_el.text = _normalize_pubdate(pub_el.text)

        logger.debug(f'consolidate_and_emit_xml: infohash={ih} cached={is_cached} trackers={len(trackers)}')
        channel.append(canonical_item)

    for item in non_hash_items:
        channel.append(item)

    return ET.tostring(rss, pretty_print=True, xml_declaration=True, encoding='UTF-8')


def dedupe_hashes_preserve_order(hashes):
    """Return list of unique hashes in the original order, normalized to lowercase."""
    seen = set()
    out = []
    for h in (hashes or []):
        if not h:
            continue
        hl = h.lower()
        if hl not in seen:
            seen.add(hl)
            out.append(hl)
    return out


def get_caps_xml():
    """Returns the static capabilities XML for Torznab."""
    return """
<caps>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,tvdbid,imdbid,tmdbid"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
  </searching>
  <categories>
    <category id="2000" name="Movies"/>
    <category id="5000" name="TV"/>
  </categories>
</caps>
""".strip()


def create_empty_rss():
    """Creates an empty RSS feed for when there are no results."""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Torbox Cached Indexer"
    return ET.tostring(rss, pretty_print=True, xml_declaration=True, encoding='UTF-8')
