"""DB-backed settings with live typed getters and an in-memory override layer.

The settings registry mirrors the env-var globals that previously lived in
:mod:`pachelarr.state`. At runtime all settings reads go through the getters
(``get_str`` / ``get_int`` / ``get_bool`` / ``get_float``) so DB edits apply
without a restart.

Resolution order for a read:

1. ``_overrides[key]`` — set by tests via :func:`set_override` (the test
   equivalent of the old ``m.X = v`` rebind). Production never touches this.
2. DB value — :func:`db.setting_get(key)`, if the DB is initialized and the row
   exists.
3. Registry default — :data:`SETTINGS[key].default`, also used when the DB is
   unavailable (e.g. before :func:`db.init`).

Seeding at startup is handled by :func:`seed_from_env_if_empty`, called from
the FastAPI lifespan: when the ``settings`` table is empty (first run) every
registered env var is read and stored. On subsequent starts env never
overwrites existing DB values, so user customizations win.

``PACHELARR_DATA_DIR`` and ``PACHELARR_LOG_LEVEL`` are NOT live settings: the
data dir fixes the DB path at startup, and log level configures logging at
import. They are still stored in the DB for visibility but edits need a
restart. :func:`is_restart_required` flags these for the REST API.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from pachelarr import db

load_dotenv()

logger = logging.getLogger("pachelarr")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Setting:
    key: str
    type: str            # "str" | "int" | "float" | "bool"
    default: object
    from_env: str        # env var name
    secret: bool = False
    restart_required: bool = False


# Settings that must NOT change at runtime (path/log level fixed at startup).
_RESTART_REQUIRED = {"PACHELARR_DATA_DIR", "PACHELARR_LOG_LEVEL"}

# Settings that hold secrets (masked nowhere — returned in plaintext via the
# authenticated settings API, same exposure as env today).
_SECRETS = {
    "PROWLARR_API_KEY", "TORBOX_API_KEY", "PACHELARR_API_KEY", "TMDB_API_KEY",
}


def _bool_default(default: str) -> bool:
    return default.lower() in ("1", "true", "yes")


SETTINGS = {
    # --- Prowlarr connection ---
    "PROWLARR_URL": Setting("PROWLARR_URL", "str", None, "PROWLARR_URL"),
    "PROWLARR_API_KEY": Setting("PROWLARR_API_KEY", "str", None, "PROWLARR_API_KEY", secret=True),

    # --- Torbox ---
    "TORBOX_API_KEY": Setting("TORBOX_API_KEY", "str", None, "TORBOX_API_KEY", secret=True),
    "TORBOX_CHECK_URL": Setting(
        "TORBOX_CHECK_URL", "str",
        "https://api.torbox.app/v1/api/torrents/checkcached", "TORBOX_CHECK_URL"),
    "TORBOX_CHUNK_SIZE": Setting("TORBOX_CHUNK_SIZE", "int", 100, "TORBOX_CHUNK_SIZE"),
    "TORBOX_MAX_RETRIES": Setting("TORBOX_MAX_RETRIES", "int", 3, "TORBOX_MAX_RETRIES"),
    "TORBOX_RETRY_BACKOFF": Setting("TORBOX_RETRY_BACKOFF", "float", 0.5, "TORBOX_RETRY_BACKOFF"),

    # --- Pachelarr ---
    "PACHELARR_API_KEY": Setting("PACHELARR_API_KEY", "str", None, "PACHELARR_API_KEY", secret=True),
    "PACHELARR_SEEDERS_BOOST": Setting("PACHELARR_SEEDERS_BOOST", "int", 10000, "PACHELARR_SEEDERS_BOOST"),
    "PACHELARR_TEST_FALLBACK_QUERY": Setting(
        "PACHELARR_TEST_FALLBACK_QUERY", "str", "", "PACHELARR_TEST_FALLBACK_QUERY"),
    "PACHELARR_DATA_DIR": Setting(
        "PACHELARR_DATA_DIR", "str", "./data", "PACHELARR_DATA_DIR", restart_required=True),
    "PACHELARR_LOG_LEVEL": Setting(
        "PACHELARR_LOG_LEVEL", "str", "INFO", "PACHELARR_LOG_LEVEL", restart_required=True),

    # --- TMDB ---
    "TMDB_API_KEY": Setting("TMDB_API_KEY", "str", "", "TMDB_API_KEY", secret=True),
    "TMDB_TITLE_LOOKUP_ENABLED": Setting("TMDB_TITLE_LOOKUP_ENABLED", "bool", False, "TMDB_TITLE_LOOKUP_ENABLED"),
    "TMDB_TITLE_LOOKUP_CACHE_TTL": Setting("TMDB_TITLE_LOOKUP_CACHE_TTL", "int", 300, "TMDB_TITLE_LOOKUP_CACHE_TTL"),
    "TMDB_TITLE_LOOKUP_CACHE_MAX": Setting("TMDB_TITLE_LOOKUP_CACHE_MAX", "int", 5000, "TMDB_TITLE_LOOKUP_CACHE_MAX"),

    # --- Tracker scraping ---
    "TRACKER_SCRAPE_ENABLED": Setting("TRACKER_SCRAPE_ENABLED", "bool", False, "TRACKER_SCRAPE_ENABLED"),
    "TRACKER_SCRAPE_CONCURRENCY": Setting("TRACKER_SCRAPE_CONCURRENCY", "int", 4, "TRACKER_SCRAPE_CONCURRENCY"),
    "TRACKER_SCRAPE_TIMEOUT": Setting("TRACKER_SCRAPE_TIMEOUT", "float", 5.0, "TRACKER_SCRAPE_TIMEOUT"),
    "TRACKER_SCRAPE_BATCH_SIZE": Setting("TRACKER_SCRAPE_BATCH_SIZE", "int", 50, "TRACKER_SCRAPE_BATCH_SIZE"),
    "TRACKER_SCRAPE_CACHE_TTL": Setting("TRACKER_SCRAPE_CACHE_TTL", "int", 300, "TRACKER_SCRAPE_CACHE_TTL"),
    "TRACKER_SCRAPE_CACHE_MAX": Setting("TRACKER_SCRAPE_CACHE_MAX", "int", 5000, "TRACKER_SCRAPE_CACHE_MAX"),

    # --- Prowlarr indexers / search ---
    "PROWLARR_INDEXERS_CACHE_TTL": Setting("PROWLARR_INDEXERS_CACHE_TTL", "int", 300, "PROWLARR_INDEXERS_CACHE_TTL"),
    "PROWLARR_INDEXERS_CACHE_MAX": Setting("PROWLARR_INDEXERS_CACHE_MAX", "int", 1, "PROWLARR_INDEXERS_CACHE_MAX"),
    "PROWLARR_PARALLEL_INDEXER_CONCURRENCY": Setting(
        "PROWLARR_PARALLEL_INDEXER_CONCURRENCY", "int", 8, "PROWLARR_PARALLEL_INDEXER_CONCURRENCY"),
    "PROWLARR_INDEXER_SEARCH_TIMEOUT": Setting(
        "PROWLARR_INDEXER_SEARCH_TIMEOUT", "float", 10.0, "PROWLARR_INDEXER_SEARCH_TIMEOUT"),

    # --- Stats granularity ---
    "STATS_ENABLED": Setting("STATS_ENABLED", "bool", True, "STATS_ENABLED"),
    "STATS_GLOBAL_ENABLED": Setting("STATS_GLOBAL_ENABLED", "bool", True, "STATS_GLOBAL_ENABLED"),
    "STATS_PER_INDEXER_ENABLED": Setting("STATS_PER_INDEXER_ENABLED", "bool", True, "STATS_PER_INDEXER_ENABLED"),
    "STATS_PER_SEARCH_ENABLED": Setting("STATS_PER_SEARCH_ENABLED", "bool", True, "STATS_PER_SEARCH_ENABLED"),
    "STATS_PER_SEARCH_MAX": Setting("STATS_PER_SEARCH_MAX", "int", 100, "STATS_PER_SEARCH_MAX"),
}


def is_registered(key: str) -> bool:
    return key in SETTINGS


def is_secret(key: str) -> bool:
    return key in _SECRETS


def is_restart_required(key: str) -> bool:
    return key in _RESTART_REQUIRED


def all_keys() -> list:
    return list(SETTINGS.keys())


# --------------------------------------------------------------------------- #
# Env value parsing (used for seeding and for the pre-DB import defaults)
# --------------------------------------------------------------------------- #

def _env_raw(key: str) -> Optional[str]:
    """Return the raw env string for a setting's env var, or None if unset."""
    spec = SETTINGS[key]
    val = os.getenv(spec.from_env)
    if val is None:
        return None
    return val


def _parse(key: str, raw: str) -> object:
    """Parse a raw string into the setting's native type."""
    spec = SETTINGS[key]
    if spec.type == "str":
        return raw
    if spec.type == "int":
        return int(raw)
    if spec.type == "float":
        return float(raw)
    if spec.type == "bool":
        return raw.strip().lower() in ("1", "true", "yes")
    raise ValueError(f"unknown setting type {spec.type!r} for {key!r}")


def _stringify(key: str, value: object) -> str:
    """Normalize a typed value to the string form stored in the DB."""
    spec = SETTINGS[key]
    if value is None:
        return ""
    if spec.type == "bool":
        return "true" if bool(value) else "false"
    return str(value)


def validate_value(key: str, value: object) -> object:
    """Validate and coerce a value for a registered key.

    Returns the typed value, or raises ``ValueError`` for an unknown key or a
    value that cannot be parsed to the setting's type. Used by the REST API.
    """
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key!r}")
    spec = SETTINGS[key]
    if value is None:
        if spec.type == "str":
            return ""
        raise ValueError(f"{key!r} requires a {spec.type} value, got None")
    try:
        if spec.type == "str":
            return str(value)
        if spec.type == "int":
            return int(value)
        if spec.type == "float":
            return float(value)
        if spec.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            s = str(value).strip().lower()
            if s in ("1", "true", "yes"):
                return True
            if s in ("0", "false", "no", ""):
                return False
            raise ValueError("not a boolean")
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key!r}: {e}") from e
    raise ValueError(f"unknown type {spec.type!r}")


# --------------------------------------------------------------------------- #
# Override layer (tests) + live getters (production)
# --------------------------------------------------------------------------- #

_overrides: dict = {}


def set_override(key: str, value: object) -> None:
    """Override a setting in-memory (tests only).

    Stores the typed value; ``None`` removes the override so the DB/default
    path is used again. Mirrors the old ``m.X = v`` test rebind.
    """
    if value is None:
        _overrides.pop(key, None)
        return
    _overrides[key] = validate_value(key, value)


def clear_overrides() -> None:
    """Remove all in-memory overrides (test teardown)."""
    _overrides.clear()


def get_typed(key: str) -> object:
    """Return the typed value for a setting, applying the resolution order."""
    if key in _overrides:
        return _overrides[key]
    # DB path
    try:
        raw = db.setting_get(key)
    except db.DBNotInitialized:
        raw = None
    if raw is not None:
        return _parse(key, raw)
    # default
    spec = SETTINGS[key]
    return spec.default


def get_str(key: str, default: Optional[str] = None) -> str:
    val = get_typed(key)
    if val is None or val == "":
        return default if default is not None else ""
    return str(val)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get_typed(key))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get_typed(key))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    val = get_typed(key)
    if isinstance(val, bool):
        return val
    try:
        return bool(val)
    except (TypeError, ValueError):
        return default


def stats_granularity_enabled(name: str) -> bool:
    """Return True if the given stats granularity is active.

    ``name`` is one of ``"GLOBAL"``, ``"PER_INDEXER"``, ``"PER_SEARCH"``. This
    is the single gate every hot-path stats check uses: stats collection is
    off unless the master ``STATS_ENABLED`` kill-switch AND the per-granularity
    toggle are both on.
    """
    return get_bool("STATS_ENABLED", False) and get_bool(f"STATS_{name}_ENABLED", False)


# --------------------------------------------------------------------------- #
# Seeding from env at startup
# --------------------------------------------------------------------------- #

def seed_from_env_if_empty() -> bool:
    """When the settings table is empty (first run), seed every registered
    setting from its env var (using the registry default if the env var is
    unset). Return True if seeding happened, False if the table already had
    values (existing customizations are never overwritten).
    """
    if db.settings_count() > 0:
        logger.debug("settings table non-empty; skipping env seed")
        return False
    updates = {}
    for key, spec in SETTINGS.items():
        raw = _env_raw(key)
        if raw is None:
            # env unset -> use the registry default
            updates[key] = _stringify(key, spec.default)
        else:
            # validate/coerce env through the native type then back to string
            try:
                typed = _parse(key, raw)
            except (TypeError, ValueError):
                logger.warning(
                    f"settings seed: env var {spec.from_env}={raw!r} is not a valid "
                    f"{spec.type}; using default {spec.default!r}"
                )
                typed = spec.default
            updates[key] = _stringify(key, typed)
    db.settings_replace(updates)
    logger.info(f"seeded {len(updates)} settings from env (first run)")
    return True


def apply_setting(key: str, value: object) -> object:
    """Validate, stringify, store a setting in the DB, and return the typed value.

    Used by the REST settings API. Raises ``ValueError`` for unknown keys or
    bad values. Rejects edits to restart-required settings with a
    ``RestartRequiredError`` so the API can return 409.
    """
    typed = validate_value(key, value)
    if is_restart_required(key):
        raise RestartRequiredError(
            f"{key!r} is fixed at startup; editing it requires a restart")
    db.setting_set(key, _stringify(key, typed))
    return typed


class RestartRequiredError(ValueError):
    """Raised when a PUT targets a setting that cannot change at runtime."""


def snapshot() -> dict:
    """Return a dict {key: {value, type, secret, default, restart_required}} for
    every registered setting, reading the current value via the getters."""
    out = {}
    for key, spec in SETTINGS.items():
        out[key] = {
            "value": get_typed(key),
            "type": spec.type,
            "secret": is_secret(key),
            "default": spec.default,
            "restart_required": is_restart_required(key),
        }
    return out
