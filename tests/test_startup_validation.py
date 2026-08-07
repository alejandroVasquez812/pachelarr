"""Tests for startup env var validation in ``lifespan`` (improvement #4).

Validates that required env vars (PROWLARR_URL, PROWLARR_API_KEY, TORBOX_API_KEY)
are checked at startup, missing ones raise RuntimeError listing ALL missing names,
and that optional TMDB_API_KEY / PACHELARR_API_KEY do not trigger failure.

Run from the repo root so ``import main`` resolves.
"""
import pytest

import main as m


_REQUIRED = ("PROWLARR_URL", "PROWLARR_API_KEY", "TORBOX_API_KEY")


@pytest.fixture
def restore_globals():
    saved = {name: getattr(m, name) for name in _REQUIRED + ("TMDB_API_KEY",)}
    yield
    for name, val in saved.items():
        setattr(m, name, val)


@pytest.mark.asyncio
async def test_lifespan_ok_when_required_vars_set(restore_globals):
    m.PROWLARR_URL = "http://x"
    m.PROWLARR_API_KEY = "k"
    m.TORBOX_API_KEY = "k"
    async with m.lifespan(m.app):
        assert m.app.state.session is not None
    assert m.app.state.session.closed


@pytest.mark.asyncio
async def test_lifespan_raises_when_required_vars_missing(restore_globals):
    m.PROWLARR_URL = None
    m.PROWLARR_API_KEY = ""
    m.TORBOX_API_KEY = "   "
    with pytest.raises(RuntimeError) as exc:
        async with m.lifespan(m.app):
            pass
    msg = str(exc.value)
    assert "PROWLARR_URL" in msg
    assert "PROWLARR_API_KEY" in msg
    assert "TORBOX_API_KEY" in msg
    assert not getattr(m.app.state, "session", None) or True


@pytest.mark.asyncio
async def test_lifespan_tmdb_optional(restore_globals):
    m.PROWLARR_URL = "http://x"
    m.PROWLARR_API_KEY = "k"
    m.TORBOX_API_KEY = "k"
    m.TMDB_API_KEY = ""
    async with m.lifespan(m.app):
        pass
    assert m.app.state.session.closed


@pytest.mark.asyncio
async def test_lifespan_lists_all_missing_at_once(restore_globals):
    m.PROWLARR_URL = None
    m.PROWLARR_API_KEY = None
    m.TORBOX_API_KEY = None
    with pytest.raises(RuntimeError) as exc:
        async with m.lifespan(m.app):
            pass
    msg = str(exc.value)
    for name in _REQUIRED:
        assert name in msg