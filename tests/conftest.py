"""Shared pytest fixtures for the Pachelarr test suite.

 Initializes an in-memory SQLite DB (so the suite needs no on-disk fixture),
 seeds settings from env once, and resets the in-memory caches + overrides +
 stats counters between tests so module-level state does not leak across
 tests.

 ``PACHELARR_DATA_DIR`` is forced to ``:memory:`` for the session so the
 FastAPI lifespan's ``db.init()`` call (no explicit path) also resolves to
 the in-memory DB instead of creating an on-disk ``./data/pachelarr.db``.

 Run pytest from the repo root so ``import main`` resolves.
"""
import os

import pytest

from pachelarr import db, settings, state


def _reset_state():
    """Clear caches, stats counters, and the settings override layer."""
    state._SCRAPE_CACHE.clear()
    state._TMDB_TITLE_CACHE.clear()
    state._MAGNET_CACHE.clear()
    state._INDEXERS_CACHE.clear()
    state._TORBOX_CACHE.clear()
    state.torbox_hits = 0
    state.torbox_misses = 0
    state.last_search_latency_ms = None
    state.last_search_at = None
    state._INDEXER_STATS.clear()
    state._SEARCH_HISTORY.clear()
    settings.clear_overrides()


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    """Open an in-memory DB once for the whole session and seed settings.

    Also forces ``PACHELARR_DATA_DIR=:memory:`` so the lifespan's ``db.init()``
    (no explicit path) reuses the in-memory connection instead of opening a
    file DB under ``./data``.
    """
    os.environ["PACHELARR_DATA_DIR"] = ":memory:"
    db.init(":memory:")
    settings.seed_from_env_if_empty()
    yield
    db.close()


@pytest.fixture(autouse=True)
def _reset_between_tests():
    """Reset mutable module-level state before and after every test."""
    _reset_state()
    yield
    _reset_state()
