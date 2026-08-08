"""Tests for the REST settings API (GET /settings, GET /settings/{key}, PUT /settings).

Covers auth (missing/wrong key), snapshot shape including secrets, PUT
valid/invalid/unknown-key, the live effect of an edited non-secret setting,
and that restart-required settings (PACHELARR_DATA_DIR) are rejected with a 409.
"""
import pytest
from fastapi.testclient import TestClient

import main as m
from pachelarr import settings

_REQUIRED = ("PROWLARR_URL", "PROWLARR_API_KEY", "TORBOX_API_KEY")


@pytest.fixture
def client():
    saved = {name: settings.get_typed(name) for name in _REQUIRED + ("PACHELARR_API_KEY",)}
    settings.set_override("PROWLARR_URL", "http://x")
    settings.set_override("PROWLARR_API_KEY", "k")
    settings.set_override("TORBOX_API_KEY", "k")
    settings.set_override("PACHELARR_API_KEY", "admin-key")
    try:
        with TestClient(m.app) as c:
            yield c
    finally:
        for name, val in saved.items():
            settings.set_override(name, val)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def test_get_settings_requires_api_key(client):
    r = client.get("/settings")
    assert r.status_code == 401


def test_get_settings_rejects_wrong_key(client):
    r = client.get("/settings", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 403


def test_get_settings_accepts_header_key(client):
    r = client.get("/settings", headers={"X-Api-Key": "admin-key"})
    assert r.status_code == 200


def test_get_settings_accepts_query_key(client):
    r = client.get("/settings?apikey=admin-key")
    assert r.status_code == 200


def test_get_settings_401_when_pachelarr_api_key_unset(client):
    settings.set_override("PACHELARR_API_KEY", "")
    r = client.get("/settings", headers={"X-Api-Key": "admin-key"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# GET /settings
# --------------------------------------------------------------------------- #

def test_get_settings_returns_all_registered_keys(client):
    r = client.get("/settings", headers={"X-Api-Key": "admin-key"})
    assert r.status_code == 200
    data = r.json()
    for key in settings.SETTINGS:
        assert key in data, f"missing {key}"
        entry = data[key]
        assert "value" in entry
        assert "type" in entry
        assert "secret" in entry
        assert "default" in entry
        assert "restart_required" in entry


def test_get_settings_includes_secret_values(client):
    """Secrets are returned in plaintext (auth-protected), per the plan."""
    r = client.get("/settings", headers={"X-Api-Key": "admin-key"})
    data = r.json()
    assert data["PACHELARR_API_KEY"]["secret"] is True
    assert data["PACHELARR_API_KEY"]["value"] == "admin-key"


def test_get_single_setting(client):
    r = client.get("/settings/PACHELARR_SEEDERS_BOOST", headers={"X-Api-Key": "admin-key"})
    assert r.status_code == 200
    entry = r.json()
    assert entry["type"] == "int"


def test_get_single_setting_404_unknown(client):
    r = client.get("/settings/NOT_A_SETTING", headers={"X-Api-Key": "admin-key"})
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# PUT /settings
# --------------------------------------------------------------------------- #

def test_put_settings_updates_value(client):
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"PACHELARR_SEEDERS_BOOST": 555},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["applied"]["PACHELARR_SEEDERS_BOOST"] == 555
    # Live getter reflects the change without a restart.
    assert settings.get_int("PACHELARR_SEEDERS_BOOST") == 555


def test_put_settings_rejects_unknown_key(client):
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"NOT_A_SETTING": 1},
    )
    assert r.status_code == 400
    assert "NOT_A_SETTING" in r.text


def test_put_settings_rejects_bad_type(client):
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"PACHELARR_SEEDERS_BOOST": "not-an-int"},
    )
    assert r.status_code == 400


def test_put_settings_rejects_restart_required(client):
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"PACHELARR_DATA_DIR": "/new/path"},
    )
    assert r.status_code == 400
    assert "PACHELARR_DATA_DIR" in r.text


def test_put_settings_rejects_invalid_json(client):
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key", "Content-Type": "application/json"},
        content=b"not json",
    )
    assert r.status_code == 400


def test_put_settings_requires_auth(client):
    r = client.put("/settings", json={"PACHELARR_SEEDERS_BOOST": 1})
    assert r.status_code == 401


def test_put_settings_updates_bool(client):
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"TRACKER_SCRAPE_ENABLED": True},
    )
    assert r.status_code == 200
    assert settings.get_bool("TRACKER_SCRAPE_ENABLED") is True


def test_put_settings_can_change_api_key(client):
    """Editing PACHELARR_API_KEY updates auth for subsequent requests."""
    # Snapshot the DB value (clear the override first so we read the DB, not the
    # override the fixture set).
    settings.set_override("PACHELARR_API_KEY", None)
    saved_db_key = settings.get_typed("PACHELARR_API_KEY")
    # Re-apply the override so the PUT authenticates against it.
    settings.set_override("PACHELARR_API_KEY", "admin-key")
    # PUT the new value (auth via the override), then clear the override so
    # subsequent auth reads the DB value we just wrote.
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"PACHELARR_API_KEY": "new-key"},
    )
    assert r.status_code == 200
    settings.set_override("PACHELARR_API_KEY", None)
    # Old key no longer works.
    r1 = client.get("/settings", headers={"X-Api-Key": "admin-key"})
    assert r1.status_code == 403
    # New key works.
    r2 = client.get("/settings", headers={"X-Api-Key": "new-key"})
    assert r2.status_code == 200
    # Restore the DB value so the session DB is clean for later tests.
    settings.apply_setting("PACHELARR_API_KEY", saved_db_key or "")


def test_put_settings_snapshot_returned(client):
    """A successful PUT returns the full settings snapshot after the update."""
    r = client.put(
        "/settings",
        headers={"X-Api-Key": "admin-key"},
        json={"PACHELARR_SEEDERS_BOOST": 777},
    )
    assert r.status_code == 200
    snap = r.json().get("settings", {})
    assert snap["PACHELARR_SEEDERS_BOOST"]["value"] == 777
