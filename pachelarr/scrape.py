import asyncio
import logging
import socket
import time

import aiohttp

from pachelarr import state

logger = logging.getLogger("pachelarr")


def _magnet_cache_get(h):
    """Return cached magnet (or None sentinel) for hash. Raises KeyError if absent."""
    if not h:
        raise KeyError(h)
    key = h.lower() if isinstance(h, str) else h
    val = state._MAGNET_CACHE[key]
    state._MAGNET_CACHE.move_to_end(key)
    return val


def _magnet_cache_put(h, magnet):
    """Insert magnet (may be None) for hash with true LRU bound."""
    import main

    if not h:
        return
    key = h.lower() if isinstance(h, str) else h
    state._MAGNET_CACHE[key] = magnet
    state._MAGNET_CACHE.move_to_end(key)
    while len(state._MAGNET_CACHE) > main._MAGNET_CACHE_MAX:
        try:
            state._MAGNET_CACHE.popitem(last=False)
        except KeyError:
            break


async def resolve_magnet_via_download(session, download_url, timeout=5.0):
    """Resolve a real magnet URI by following the Prowlarr downloadUrl proxy."""
    import main

    if not download_url:
        return None
    try:
        async with session.get(
            download_url,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={"X-Api-Key": main.PROWLARR_API_KEY} if download_url.startswith(str(main.PROWLARR_URL or '')) else {},  # noqa: E501
        ) as resp:
            loc = resp.headers.get('Location') or resp.headers.get('location')
            if loc and 'magnet:?' in loc:
                return loc
            if resp.status == 200:
                text = await resp.text()
                if text and 'magnet:?' in text:
                    idx = text.find('magnet:')
                    if idx >= 0:
                        end = len(text)
                        for i, ch in enumerate(text[idx:], start=idx):
                            if ch in ' \t\r\n<':
                                end = i
                                break
                        return text[idx:end]
            if resp.status in (301, 302, 303, 307, 308) and loc and loc.startswith('http'):
                return await resolve_magnet_via_download(session, loc, timeout)
    except Exception as e:
        logger.debug(f"resolve_magnet_via_download error for {download_url[:80]}: {e}", exc_info=True)
    return None


def _scrape_cache_get(h):
    """Return cached scrape entry for hash if present and unexpired, else None."""
    if not h:
        return None
    key = h.lower() if isinstance(h, str) else h
    entry = state._SCRAPE_CACHE.get(key)
    if entry is None:
        return None
    if entry.get('expires', 0) <= time.time():
        return None
    state._SCRAPE_CACHE.move_to_end(key)
    return entry


def _scrape_cache_put(h, entry):
    """Insert/refresh a scrape cache entry for hash with TTL-based expiry."""
    import main

    if not h or not entry:
        return
    key = h.lower() if isinstance(h, str) else h
    stored = dict(entry)
    stored['expires'] = time.time() + state.TRACKER_SCRAPE_CACHE_TTL
    state._SCRAPE_CACHE[key] = stored
    state._SCRAPE_CACHE.move_to_end(key)
    while len(state._SCRAPE_CACHE) > main.TRACKER_SCRAPE_CACHE_MAX:
        try:
            state._SCRAPE_CACHE.popitem(last=False)
        except KeyError:
            break


def _parse_tracker_host_port(tracker_url):
    """Return (host, port) for a tracker URL. Only supports udp:// and returns default port if missing."""
    try:
        from urllib.parse import urlparse
        p = urlparse(tracker_url)
        hostname = p.hostname
        port = p.port
        if not hostname:
            return None
        port = port or 6969
        return hostname, port
    except ValueError:
        return None


async def _resolve_udp_addr(host, port, loop=None):
    """Resolve host:port to a deduped list of (ip, port) tuples suitable for UDP."""
    if not host:
        return []
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except OSError as e:
        logger.debug(f"_resolve_udp_addr: getaddrinfo failed for {host}:{port}: {e}")
        return []
    seen = set()
    out = []
    for addr in infos:
        try:
            ip, p = addr[4][0], addr[4][1]
        except (IndexError, TypeError):
            continue
        key = (ip, p)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


async def _udp_scrape_tracker(host, port, chunks, timeout):
    """Scrape all chunks for one tracker over a single reused UDP connection."""
    import random
    import struct

    class _ScrapeProto(asyncio.DatagramProtocol):
        def __init__(self):
            self.transport = None
            self.fut = None
        def connection_made(self, transport):
            self.transport = transport
        def datagram_received(self, data, addr):
            if self.fut is not None and not self.fut.done():
                self.fut.set_result(data)
        def error_received(self, exc):
            if self.fut is not None and not self.fut.done():
                self.fut.set_exception(exc)
        def connection_lost(self, exc):
            if self.fut is not None and not self.fut.done():
                self.fut.set_exception(exc or ConnectionError("transport closed"))
        def reset_future(self):
            self.fut = asyncio.get_event_loop().create_future()
            return self.fut

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    addrs = await _resolve_udp_addr(host, port, loop)
    if not addrs:
        addrs = [(host, port)]

    aggregate = {}
    for (ip, p) in addrs:
        proto = _ScrapeProto()
        try:
            transport, _ = await loop.create_datagram_endpoint(lambda proto=proto: proto, remote_addr=(ip, p))
        except Exception as e:
            logger.debug(f"_udp_scrape_tracker: connect failed to {ip}:{p} ({host}:{port}): {e}", exc_info=True)
            continue
        try:
            fut = proto.reset_future()
            trans_id = random.randrange(0, 1 << 31)
            conn_req = struct.pack('!QII', 0x41727101980, 0, trans_id)
            transport.sendto(conn_req)
            try:
                data = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                continue
            if len(data) < 16:
                continue
            action, trans, conn_id = struct.unpack('!IIQ', data[:16])
            if action != 0 or trans != trans_id:
                continue

            connected_ok = False
            for chunk in chunks:
                if not chunk:
                    continue
                try:
                    fut = proto.reset_future()
                    trans_id2 = random.randrange(0, 1 << 31)
                    payload = struct.pack('!QII', conn_id, 2, trans_id2)
                    valid_hashes = []
                    for h in chunk:
                        try:
                            payload += bytes.fromhex(h)
                            valid_hashes.append(h)
                        except (ValueError, struct.error):
                            continue
                    transport.sendto(payload)
                    try:
                        data = await asyncio.wait_for(fut, timeout=timeout)
                    except asyncio.TimeoutError:
                        break
                    if len(data) < 8:
                        continue
                    action, trans = struct.unpack('!II', data[:8])
                    if action != 2:
                        continue
                    connected_ok = True
                    data_body = data[8:]
                    rec_count = len(data_body) // 12
                    if rec_count != len(valid_hashes):
                        logger.warning(f"_udp_scrape_tracker: record count {rec_count} != requested {len(valid_hashes)} for {host}:{port}; mapping positionally anyway")  # noqa: E501
                    for i in range(0, len(data_body), 12):
                        rec = data_body[i:i+12]
                        if len(rec) < 12:
                            break
                        seeders, leechers, downloads = struct.unpack('!III', rec)
                        idx = i // 12
                        if idx < len(valid_hashes):
                            aggregate[valid_hashes[idx]] = {'seeders': seeders, 'leechers': leechers, 'downloads': downloads}  # noqa: E501
                except Exception as e:
                    logger.debug(f"_udp_scrape_tracker: per-chunk error for {host}:{port}: {e}", exc_info=True)
                    break
            if connected_ok:
                return aggregate
        finally:
            try:
                transport.close()
            except Exception:
                pass
    return aggregate


async def scrape_trackers_inverted(tracker_to_hashes):
    """Given mapping tracker_url -> list of infohash hex strings, perform inverted scraping and
    return mapping infohash -> {'seeders':int, 'leechers':int} aggregated as per-metric max
    across trackers. Uses a bounded result cache keyed by lowercased infohash.
    """
    import main

    sem = asyncio.Semaphore(state.TRACKER_SCRAPE_CONCURRENCY)
    logger.debug(f"scrape_trackers_inverted: trackers={len(tracker_to_hashes)} concurrency={state.TRACKER_SCRAPE_CONCURRENCY} batch_size={main.TRACKER_SCRAPE_BATCH_SIZE} timeout={state.TRACKER_SCRAPE_TIMEOUT}")  # noqa: E501
    results_per_hash = {}
    cache_puts = []

    def _aggregate(h, entry):
        cur = results_per_hash.get(h)
        if cur is None:
            results_per_hash[h] = {'seeders': entry.get('seeders', 0), 'leechers': entry.get('leechers', 0)}
        else:
            if entry.get('seeders', 0) > cur.get('seeders', 0):
                cur['seeders'] = entry.get('seeders', 0)
            if entry.get('leechers', 0) > cur.get('leechers', 0):
                cur['leechers'] = entry.get('leechers', 0)

    async def _process_tracker(url, hashes):
        if not url or not url.lower().startswith('udp://'):
            return
        hostport = _parse_tracker_host_port(url)
        if not hostport:
            return
        host, port = hostport
        uncached = []
        for h in hashes:
            cached_entry = _scrape_cache_get(h)
            if cached_entry is not None:
                _aggregate(h, cached_entry)
            else:
                uncached.append(h)
        if not uncached:
            return
        chunks = [uncached[i:i+main.TRACKER_SCRAPE_BATCH_SIZE] for i in range(0, len(uncached), main.TRACKER_SCRAPE_BATCH_SIZE)]  # noqa: E501
        async with sem:
            try:
                res = await main._udp_scrape_tracker(host, port, chunks, state.TRACKER_SCRAPE_TIMEOUT)
            except Exception as e:
                logger.debug(f"scrape_trackers_inverted: tracker {url} failed: {e}", exc_info=True)
                res = {}
        for h, entry in res.items():
            _aggregate(h, entry)
            cache_puts.append((h, entry))

    tasks = [asyncio.create_task(_process_tracker(url, hashes)) for url, hashes in tracker_to_hashes.items()]
    if tasks:
        await asyncio.gather(*tasks)
    for h, entry in cache_puts:
        _scrape_cache_put(h, entry)
    return results_per_hash
