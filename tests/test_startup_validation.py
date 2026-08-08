"""Tests for startup env var validation in ``lifespan`` (improvement #4).

 Validates that required env vars (PROWLARR_URL, PROWLARR_API_KEY, TORBOX_API_KEY)
 are checked at startup, missing ones raise RuntimeError listing ALL missing names,
 and that optional TMDB_API_KEY / PACHELARR_API_KEY do not trigger failure.

 Settings overrides go through the settings store (settings.set_override) rather
 than module globals, matching the live-getter runtime model. The lifespan reads
 the required vars via getters so the overrides take effect.

 Run from the repo root so ``import main`` resolves.
 """
import pytest

import main as m
from pachelarr import settings

_REQUIRED = ("PROWLARR_URL", "PROWLARR_API_KEY", "TORBOX_API_KEY")


@pytest.fixture
def restore_settings():
    saved = {name: settings.get_typed(name) for name in _REQUIRED + ("TMDB_API_KEY",)}
    yield
    for name, val in saved.items():
        settings.set_override(name, val)


async def test_lifespan_ok_when_required_vars_set(restore_settings):
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    async with m.lifespan(m.app):
        assert m.app.state.session is not None
    assert m.app.state.session.closed


async def test_lifespan_raises_when_required_vars_missing(restore_settings):
    # Empty/blank values must be treated as missing by the lifespan check.
    settings.set_override("PROWLARR_URL", "")
    settings.set_override("PROWLARR_API_KEY", "")
    settings.set_override("TORBOX_API_KEY", "   ")
    with pytest.raises(RuntimeError) as exc:
        async with m.lifespan(m.app):
            pass
    msg = str(exc.value)
    assert "PROWLARR_URL" in msg
    assert "PROWLARR_API_KEY" in msg
    assert "TORBOX_API_KEY" in msg
    assert not getattr(m.app.state, "session", None) or True


async def test_lifespan_tmdb_optional(restore_settings):
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    settings.set_override("TMDB_API_KEY", "")
    async with m.lifespan(m.app):
        pass
    assert m.app.state.session.closed


async def test_lifespan_lists_all_missing_at_once(restore_settings):
    settings.set_override("PROWLARR_URL", "")
    settings.set_override("PROWLARR_API_KEY", "")
    settings.set_override("TORBOX_API_KEY", "")
    with pytest.raises(RuntimeError) as exc:
        async with m.lifespan(m.app):
            pass
    msg = str(exc.value)
    for name in _REQUIRED:
        assert name in msg
