import asyncio
import random
import socket
import struct

from main import _udp_scrape_tracker


class MockTrackerProtocol(asyncio.DatagramProtocol):
    def __init__(self, seeders_list=None, leechers_list=None):
        super().__init__()
        self.transport = None
        self.conn_id = random.getrandbits(64)
        # default seeders/leechers per hash (if list shorter, reuse last value)
        self.seeders_list = seeders_list or [7]
        self.leechers_list = leechers_list or [0]

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        # If data length == 16 and looks like a connect request (QII)
        try:
            if len(data) >= 16:
                # Try unpack as connect request: QII
                magic, action, trans = struct.unpack('!QII', data[:16])
                if action == 0:
                    # connect response: IIQ (action, trans, conn_id)
                    resp = struct.pack('!IIQ', 0, trans, self.conn_id)
                    self.transport.sendto(resp, addr)
                    return
            # Otherwise treat it as scrape: QII + (20*nhashes)
            # the client sends conn_id (8), action=2 (4), trans (4), then hashes
            if len(data) >= 16:
                conn_id, action, trans = struct.unpack('!QII', data[:16])
                if action == 2:
                    hashes_data = data[16:]
                    n = len(hashes_data) // 20
                    resp_header = struct.pack('!II', 2, trans)
                    body = b''
                    for i in range(n):
                        s = self.seeders_list[i] if i < len(self.seeders_list) else self.seeders_list[-1]
                        lch = self.leechers_list[i] if i < len(self.leechers_list) else self.leechers_list[-1]
                        # seeders, leechers, completed(downloads)
                        body += struct.pack('!III', s, lch, 0)
                    self.transport.sendto(resp_header + body, addr)
                    return
        except Exception:
            # ignore errors
            return


async def test_udp_scrape_tracker_local_server():
    loop = asyncio.get_event_loop()
    # prepare the server on localhost:0 (random free port)
    protocol = MockTrackerProtocol(seeders_list=[5, 10], leechers_list=[2, 4])
    transport, _ = await loop.create_datagram_endpoint(lambda: protocol, local_addr=('127.0.0.1', 0))
    try:
        sockname = transport.get_extra_info('sockname')
        port = sockname[1]
        # choose two valid 20-byte hex hashes
        hash_a = 'a' * 40  # 20 bytes of 0xaa
        hash_b = 'b' * 40  # 20 bytes of 0xbb
        res = await _udp_scrape_tracker('127.0.0.1', port, [[hash_a, hash_b]], timeout=2.0)
        # New return shape: {hash: {'seeders':int, 'leechers':int, 'downloads':int}}
        key_a = hash_a.lower()
        key_b = hash_b.lower()
        assert key_a in res and key_b in res
        assert res[key_a]['seeders'] == 5
        assert res[key_a]['leechers'] == 2
        assert res[key_a]['downloads'] == 0
        assert res[key_b]['seeders'] == 10
        assert res[key_b]['leechers'] == 4
        assert res[key_b]['downloads'] == 0
    finally:
        transport.close()


async def test_udp_scrape_tracker_record_count_mismatch(caplog):
    """When the server returns fewer records than requested, _udp_scrape_tracker maps positionally
    and logs a warning about the record-count mismatch."""
    import logging

    class FixedCountProtocol(asyncio.DatagramProtocol):
        """Replies to scrape with a FIXED number of records regardless of requested hashes."""
        def __init__(self, seeders_list, leechers_list):
            super().__init__()
            self.transport = None
            self.conn_id = random.getrandbits(64)
            self.seeders_list = seeders_list
            self.leechers_list = leechers_list

        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            try:
                if len(data) >= 16:
                    magic, action, trans = struct.unpack('!QII', data[:16])
                    if action == 0:
                        self.transport.sendto(struct.pack('!IIQ', 0, trans, self.conn_id), addr)
                        return
                if len(data) >= 16:
                    conn_id, action, trans = struct.unpack('!QII', data[:16])
                    if action == 2:
                        # Always return exactly len(seeders_list) records, ignoring request count.
                        resp_header = struct.pack('!II', 2, trans)
                        body = b''
                        for i in range(len(self.seeders_list)):
                            body += struct.pack('!III', self.seeders_list[i], self.leechers_list[i], 0)
                        self.transport.sendto(resp_header + body, addr)
                        return
            except Exception:
                return

    loop = asyncio.get_event_loop()
    # Server replies with only ONE record regardless of how many hashes were requested.
    protocol = FixedCountProtocol(seeders_list=[9], leechers_list=[1])
    transport, _ = await loop.create_datagram_endpoint(lambda: protocol, local_addr=('127.0.0.1', 0))
    try:
        sockname = transport.get_extra_info('sockname')
        port = sockname[1]
        hash_a = 'a' * 40
        hash_b = 'b' * 40
        with caplog.at_level(logging.WARNING, logger='pachelarr'):
            res = await _udp_scrape_tracker('127.0.0.1', port, [[hash_a, hash_b]], timeout=2.0)
        # Only the first hash receives a positional record.
        assert hash_a.lower() in res
        assert hash_b.lower() not in res
        assert res[hash_a.lower()]['seeders'] == 9
        assert any('record count' in r.getMessage() for r in caplog.records)
    finally:
        transport.close()


async def test_scrape_trackers_inverted_cache_hit(monkeypatch):
    """A second identical scrape call should hit the cache and not re-invoke
    the per-tracker helper for already-cached hashes."""
    import main as m
    from main import scrape_trackers_inverted
    m._SCRAPE_CACHE.clear()

    calls = []

    async def fake_udp_scrape_tracker(host, port, chunks, timeout):
        calls.append((host, port, chunks))
        out = {}
        for chunk in chunks:
            for h in chunk:
                out[h] = {'seeders': 42, 'leechers': 3, 'downloads': 0}
        return out

    monkeypatch.setattr('main._udp_scrape_tracker', fake_udp_scrape_tracker)
    tracker_map = {'udp://tracker1:6969/announce': ['abc1', 'abc2']}

    out1 = await scrape_trackers_inverted(tracker_map)
    assert out1['abc1']['seeders'] == 42
    assert out1['abc2']['seeders'] == 42

    # Second call: cache should be populated, helper not invoked again.
    out2 = await scrape_trackers_inverted(tracker_map)
    assert out2['abc1']['seeders'] == 42
    assert out2['abc2']['seeders'] == 42
    assert len(calls) == 1, f"expected helper called once, got {len(calls)}"
    m._SCRAPE_CACHE.clear()


async def test_scrape_trackers_inverted_multi_chunk_single_call(monkeypatch):
    """When hashes exceed TRACKER_SCRAPE_BATCH_SIZE, the per-tracker helper receives
    multiple chunks in a single call (connection reuse)."""
    import main as m
    from main import scrape_trackers_inverted
    m._SCRAPE_CACHE.clear()

    observed_chunks = []

    async def fake_udp_scrape_tracker(host, port, chunks, timeout):
        observed_chunks.append(chunks)
        out = {}
        for chunk in chunks:
            for h in chunk:
                out[h] = {'seeders': 5, 'leechers': 0, 'downloads': 0}
        return out

    monkeypatch.setattr('main._udp_scrape_tracker', fake_udp_scrape_tracker)
    # Force a small batch size so we get multiple chunks.
    monkeypatch.setattr(m, 'TRACKER_SCRAPE_BATCH_SIZE', 2)
    hashes = [f'h{i:040x}' for i in range(5)]  # 5 hashes -> 3 chunks (2,2,1)
    tracker_map = {'udp://tracker1:6969/announce': hashes}

    out = await scrape_trackers_inverted(tracker_map)
    assert len(out) == 5
    # The helper was called exactly once with multiple chunks (connection reuse).
    assert len(observed_chunks) == 1
    assert len(observed_chunks[0]) == 3
    # All hashes covered across the chunks.
    flat = [h for chunk in observed_chunks[0] for h in chunk]
    assert sorted(flat) == sorted(hashes)
    m._SCRAPE_CACHE.clear()


async def test_resolve_udp_addr_dedupes(monkeypatch):
    """_resolve_udp_addr dedupes resolved (ip, port) tuples and returns [] on failure."""
    from main import _resolve_udp_addr

    loop = asyncio.get_event_loop()

    class FakeInfoList:
        def __init__(self, addrs):
            self._addrs = addrs

        def __iter__(self):
            return iter(self._addrs)

    # getaddrinfo returns 5-tuples where index [4] is (ip, port).
    infos = [
        (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('1.2.3.4', 6969)),
        (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('1.2.3.4', 6969)),  # duplicate
        (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('5.6.7.8', 6969)),
    ]

    async def fake_getaddrinfo(host, port, **kwargs):
        return infos

    monkeypatch.setattr(loop, 'getaddrinfo', fake_getaddrinfo)
    out = await _resolve_udp_addr('tracker.example', 6969, loop=loop)
    assert out == [('1.2.3.4', 6969), ('5.6.7.8', 6969)]

    # Failure path: return [].
    async def failing_getaddrinfo(host, port, **kwargs):
        raise OSError('boom')

    monkeypatch.setattr(loop, 'getaddrinfo', failing_getaddrinfo)
    out_fail = await _resolve_udp_addr('tracker.example', 6969, loop=loop)
    assert out_fail == []
