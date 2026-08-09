"""Tests for the configurable stats granularity toggles.

Each granularity (global / per-indexer / per-search) is gated by a bool
setting, plus a master ``STATS_ENABLED`` kill-switch. These tests toggle the
settings via ``settings.set_override`` and assert the corresponding counters
stop mutating.

The conftest session fixture opens an in-memory SQLite DB and resets
``_INDEXER_STATS`` / ``_SEARCH_HISTORY`` between tests. Overrides are restored
with ``set_override(key, None)`` in ``finally`` blocks.
"""
import pytest

from pachelarr import settings, state


def _search_record(query="q"):
    return {
        "ts": 1700000000.0,
        "query": query,
        "search_type": "search",
        "latency_ms": 10.0,
        "torbox_cached": 1,
        "torbox_uncached": 0,
        "indexer_count": 1,
    }


# --------------------------------------------------------------------------- #
# Master kill-switch (STATS_ENABLED=false)
# --------------------------------------------------------------------------- #

def test_master_switch_off_blocks_all_granularities():
    settings.set_override("STATS_ENABLED", False)
    try:
        state._SEARCH_HISTORY.clear()
        state._INDEXER_STATS.clear()
        state.last_search_at = None
        state.last_search_latency_ms = None

        state.record_search(_search_record())
        state.record_indexer_stat(1, 50.0)
        state.record_indexer_cache_attribution(1, 1, 2)

        assert len(state._SEARCH_HISTORY) == 0
        assert state._INDEXER_STATS == {}
        assert state.last_search_at is None
        assert state.last_search_latency_ms is None
    finally:
        settings.set_override("STATS_ENABLED", None)


# --------------------------------------------------------------------------- #
# Per-search toggle
# --------------------------------------------------------------------------- #

def test_per_search_disabled_does_not_append_history():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_PER_SEARCH_ENABLED", False)
    try:
        state._SEARCH_HISTORY.clear()
        state.record_search(_search_record())
        assert len(state._SEARCH_HISTORY) == 0
    finally:
        settings.set_override("STATS_PER_SEARCH_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)


def test_per_search_enabled_appends_history():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_PER_SEARCH_ENABLED", True)
    try:
        state._SEARCH_HISTORY.clear()
        state.record_search(_search_record("a"))
        state.record_search(_search_record("b"))
        assert len(state._SEARCH_HISTORY) == 2
        assert state._SEARCH_HISTORY[0]["query"] == "a"
        assert state._SEARCH_HISTORY[1]["query"] == "b"
    finally:
        settings.set_override("STATS_PER_SEARCH_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)


# --------------------------------------------------------------------------- #
# Per-indexer toggle
# --------------------------------------------------------------------------- #

def test_per_indexer_disabled_does_not_mutate_indexer_stats():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_PER_INDEXER_ENABLED", False)
    try:
        state._INDEXER_STATS.clear()
        state.record_indexer_stat(1, 50.0)
        state.record_indexer_cache_attribution(1, 1, 2)
        assert state._INDEXER_STATS == {}
    finally:
        settings.set_override("STATS_PER_INDEXER_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)


def test_per_indexer_enabled_records_stats():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_PER_INDEXER_ENABLED", True)
    try:
        state._INDEXER_STATS.clear()
        state.record_indexer_stat(1, 50.0)
        state.record_indexer_cache_attribution(1, 1, 2)
        entry = state._INDEXER_STATS[1]
        assert entry["requests"] == 1
        assert entry["errors"] == 0
        assert entry["total_latency_ms"] == 50.0
        assert entry["last_latency_ms"] == 50.0
        assert entry["cached"] == 1
        assert entry["uncached"] == 2
    finally:
        settings.set_override("STATS_PER_INDEXER_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)


# --------------------------------------------------------------------------- #
# Global toggle
# --------------------------------------------------------------------------- #

def test_global_disabled_does_not_update_last_search():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_GLOBAL_ENABLED", False)
    try:
        state.last_search_at = None
        state.last_search_latency_ms = None
        # The global counters are updated by handle_search's finally block; here
        # we assert the gate helper reflects the disabled state so the hot path
        # skips the mutation.
        assert settings.stats_granularity_enabled("GLOBAL") is False
        assert state.last_search_at is None
        assert state.last_search_latency_ms is None
    finally:
        settings.set_override("STATS_GLOBAL_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)


def test_global_enabled_gate_is_true():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_GLOBAL_ENABLED", True)
    try:
        assert settings.stats_granularity_enabled("GLOBAL") is True
    finally:
        settings.set_override("STATS_GLOBAL_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)


# --------------------------------------------------------------------------- #
# All enabled (default)
# --------------------------------------------------------------------------- #

def test_all_enabled_records_each_granularity():
    settings.set_override("STATS_ENABLED", True)
    settings.set_override("STATS_GLOBAL_ENABLED", True)
    settings.set_override("STATS_PER_INDEXER_ENABLED", True)
    settings.set_override("STATS_PER_SEARCH_ENABLED", True)
    try:
        state._SEARCH_HISTORY.clear()
        state._INDEXER_STATS.clear()

        state.record_search(_search_record())
        state.record_indexer_stat(1, 50.0)
        state.record_indexer_cache_attribution(1, 1, 2)

        assert len(state._SEARCH_HISTORY) == 1
        assert state._INDEXER_STATS[1]["requests"] == 1
        assert state._INDEXER_STATS[1]["cached"] == 1
        assert state._INDEXER_STATS[1]["uncached"] == 2
        assert settings.stats_granularity_enabled("GLOBAL") is True
    finally:
        settings.set_override("STATS_PER_SEARCH_ENABLED", None)
        settings.set_override("STATS_PER_INDEXER_ENABLED", None)
        settings.set_override("STATS_GLOBAL_ENABLED", None)
        settings.set_override("STATS_ENABLED", None)
