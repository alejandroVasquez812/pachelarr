"""Tests for improvement #9: make the four module-level caches truly LRU.

Covers:
- _MAGNET_CACHE: true LRU eviction (least-recently-used dropped) + KeyError-on-miss.
- _SCRAPE_CACHE: returns None on miss and on expired; live hits touch LRU order.
- _INDEXERS_CACHE: single-listing model returns the listing when fresh, None
  when expired.

Per AGENTS.md, module-level caches are shared across tests, so each test clears
the relevant cache before and after. Run from the repo root so ``import main``
resolves.
"""
import pytest

import main as m


def test_magnet_cache_lru_evicts_least_recently_used():
    """With MAX=3, put a,b,c; touch 'a'; put 'd' -> 'b' (LRU) is evicted, 'a' survives."""
    saved_max = m._MAGNET_CACHE_MAX
    m._MAGNET_CACHE.clear()
    try:
        m._MAGNET_CACHE_MAX = 3
        m._magnet_cache_put('a', 'magnet-a')
        m._magnet_cache_put('b', 'magnet-b')
        m._magnet_cache_put('c', 'magnet-c')
        assert len(m._MAGNET_CACHE) == 3
        # Touch 'a' so it becomes most-recently-used; LRU is now 'b'.
        got = m._magnet_cache_get('a')
        assert got == 'magnet-a'
        # Over the limit; eviction must drop the LRU entry ('b'), not 'a'.
        m._magnet_cache_put('d', 'magnet-d')
        assert 'a' in m._MAGNET_CACHE, "'a' (touched) should survive LRU eviction"
        assert 'b' not in m._MAGNET_CACHE, "'b' (LRU) should have been evicted"
        assert 'c' in m._MAGNET_CACHE
        assert 'd' in m._MAGNET_CACHE
        assert len(m._MAGNET_CACHE) == 3
    finally:
        m._MAGNET_CACHE.clear()
        m._MAGNET_CACHE_MAX = saved_max


def test_magnet_cache_get_raises_keyerror_on_absent_key():
    m._MAGNET_CACHE.clear()
    try:
        with pytest.raises(KeyError):
            m._magnet_cache_get('nonexistent')
    finally:
        m._MAGNET_CACHE.clear()


def test_magnet_cache_get_raises_keyerror_on_falsy_key():
    # The contract requires KeyError for a falsy key too (existing behavior).
    with pytest.raises(KeyError):
        m._magnet_cache_get('')


def test_magnet_cache_negative_sentinel_is_returned_not_raised():
    """A cached None (negative cache) is a hit, not a KeyError."""
    m._MAGNET_CACHE.clear()
    try:
        m._magnet_cache_put('h1', None)
        assert m._magnet_cache_get('h1') is None
    finally:
        m._MAGNET_CACHE.clear()


def test_magnet_cache_put_normalizes_lowercase_key():
    m._MAGNET_CACHE.clear()
    try:
        m._magnet_cache_put('ABCD', 'm1')
        assert m._magnet_cache_get('abcd') == 'm1'
    finally:
        m._MAGNET_CACHE.clear()


def test_scrape_cache_get_returns_none_on_miss():
    m._SCRAPE_CACHE.clear()
    try:
        assert m._scrape_cache_get('missing') is None
    finally:
        m._SCRAPE_CACHE.clear()


def test_scrape_cache_get_returns_none_on_expired():
    m._SCRAPE_CACHE.clear()
    try:
        key = 'deadbeef'
        m._scrape_cache_put(key, {'seeders': 1, 'leechers': 0, 'downloads': 2})
        assert key in m._SCRAPE_CACHE
        # Force expiry into the past.
        m._SCRAPE_CACHE[key]['expires'] = m._SCRAPE_CACHE[key]['expires'] - 9999
        assert m._scrape_cache_get(key) is None
        # Expired entries are intentionally left in place (last-good fallback).
        assert key in m._SCRAPE_CACHE
    finally:
        m._SCRAPE_CACHE.clear()


def test_scrape_cache_get_returns_entry_on_hit():
    m._SCRAPE_CACHE.clear()
    try:
        key = 'cafef00d'
        m._scrape_cache_put(key, {'seeders': 5, 'leechers': 1, 'downloads': 9})
        entry = m._scrape_cache_get(key)
        assert entry is not None
        assert entry['seeders'] == 5
        assert 'expires' in entry
    finally:
        m._SCRAPE_CACHE.clear()


def test_scrape_cache_put_is_truly_lru():
    """Eviction drops the least-recently-used entry, not the soonest-expiring one."""
    saved_max = m.TRACKER_SCRAPE_CACHE_MAX
    m._SCRAPE_CACHE.clear()
    try:
        m.TRACKER_SCRAPE_CACHE_MAX = 3
        m._scrape_cache_put('a', {'seeders': 1})
        m._scrape_cache_put('b', {'seeders': 2})
        m._scrape_cache_put('c', {'seeders': 3})
        # Touch 'a' so it is most-recently-used; LRU is now 'b'.
        assert m._scrape_cache_get('a')['seeders'] == 1
        # Make 'a' expire soonest to prove eviction is LRU, not by expires.
        m._SCRAPE_CACHE['a']['expires'] = 1.0
        m._scrape_cache_put('d', {'seeders': 4})
        assert 'b' not in m._SCRAPE_CACHE, "'b' (LRU) should be evicted"
        assert 'a' in m._SCRAPE_CACHE, "'a' (touched) should survive despite soonest expiry"
        assert len(m._SCRAPE_CACHE) == 3
    finally:
        m._SCRAPE_CACHE.clear()
        m.TRACKER_SCRAPE_CACHE_MAX = saved_max


def test_indexers_cache_returns_listing_when_fresh():
    m._INDEXERS_CACHE.clear()
    try:
        indexers = [{"id": 1, "name": "demo"}]
        m._indexers_cache_put(indexers)
        got = m._indexers_cache_get()
        assert got is not None
        assert got == indexers
        # Shape preserved for /statsz.
        assert 'indexers' in m._INDEXERS_CACHE['listing']
        assert 'expires' in m._INDEXERS_CACHE['listing']
    finally:
        m._INDEXERS_CACHE.clear()


def test_indexers_cache_returns_none_when_expired():
    m._INDEXERS_CACHE.clear()
    try:
        m._indexers_cache_put([{"id": 1}])
        assert m._indexers_cache_get() is not None
        # Force expiry into the past.
        m._INDEXERS_CACHE['listing']['expires'] = (
            m._INDEXERS_CACHE['listing']['expires'] - 9999
        )
        assert m._indexers_cache_get() is None
        # Expired entries are intentionally left in place (last-good fallback).
        assert 'listing' in m._INDEXERS_CACHE
    finally:
        m._INDEXERS_CACHE.clear()


def test_indexers_cache_returns_none_when_empty():
    m._INDEXERS_CACHE.clear()
    try:
        assert m._indexers_cache_get() is None
    finally:
        m._INDEXERS_CACHE.clear()


def test_all_caches_are_ordereddict_instances():
    """Improvement #9 requirement: all four caches are OrderedDict."""
    from collections import OrderedDict
    assert isinstance(m._MAGNET_CACHE, OrderedDict)
    assert isinstance(m._SCRAPE_CACHE, OrderedDict)
    assert isinstance(m._TMDB_TITLE_CACHE, OrderedDict)
    assert isinstance(m._INDEXERS_CACHE, OrderedDict)
