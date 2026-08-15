"""TheTVDB v4 API client for TV show metadata lookups.

TVDB is the **preferred** TV metadata provider when ``TVDB_API_KEY`` is set;
:mod:`pachelarr.tmdb` falls back to TMDB when TVDB is unavailable or returns
nothing. Movie lookups are always TMDB-only.

The TVDB v4 API uses a JWT auth model: ``POST /v4/login`` with ``{apikey, pin?}``
returns a bearer token valid for ~30 days. The token is cached in-memory in
``state._TVDB_TOKEN`` and refreshed lazily when missing or close to expiry.

All public lookup functions return ``None`` (never raise) when TVDB is not
configured or the API errors, so callers in :mod:`pachelarr.tmdb` can cleanly
fall back to TMDB.
"""
import base64
import json
import logging
import time

import aiohttp

from pachelarr import settings, state

logger = logging.getLogger("pachelarr")

_TVDB_BASE = "https://api4.thetvdb.com/v4"
# Conservative refresh window: re-login if the token expires within this many
# seconds. The actual JWT ``exp`` claim is decoded when possible; this is the
# fallback when decoding fails.
_REFRESH_LEEWAY = 60
# Fallback token TTL when the JWT ``exp`` claim cannot be decoded (24h).
_FALLBACK_TTL = 86400


def _decode_jwt_exp(token):
    """Best-effort decode of the ``exp`` claim from a JWT string.

    Returns the expiry as a unix timestamp (float), or ``None`` if the token
    is malformed or the claim is absent. No signature verification — we only
    need the expiry for refresh scheduling.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        # JWT base64url padding
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        if exp is not None:
            return float(exp)
    except Exception as e:
        logger.debug(f"TVDB JWT exp decode failed: {e}")
    return None


async def _login(session):
    """Authenticate with TVDB and store the JWT in ``state._TVDB_TOKEN``.

    Returns the token string, or ``None`` on failure.
    """
    api_key = settings.get_str("TVDB_API_KEY")
    if not api_key:
        return None
    pin = settings.get_str("TVDB_API_PIN", "")
    body = {"apikey": api_key}
    if pin:
        body["pin"] = pin
    try:
        async with session.post(
            f"{_TVDB_BASE}/login",
            json=body,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                logger.warning(f"TVDB login failed: status {response.status}")
                return None
            data = await response.json()
            token = data.get("token")
            if not token:
                logger.warning("TVDB login response missing token")
                return None
            exp = _decode_jwt_exp(token)
            if exp is not None:
                expires_at = exp
            else:
                expires_at = time.time() + _FALLBACK_TTL
            state._TVDB_TOKEN["token"] = token
            state._TVDB_TOKEN["expires_at"] = expires_at
            logger.info("TVDB login successful")
            return token
    except Exception as e:
        logger.warning(f"TVDB login error: {e}", exc_info=True)
        return None


async def _ensure_token(session):
    """Return a valid TVDB JWT, logging in (or re-logging in) if needed.

    Returns the token string, or ``None`` if TVDB is not configured or login
    failed.
    """
    api_key = settings.get_str("TVDB_API_KEY")
    if not api_key:
        return None
    token = state._TVDB_TOKEN.get("token")
    expires_at = state._TVDB_TOKEN.get("expires_at", 0.0)
    if token and (expires_at - time.time()) > _REFRESH_LEEWAY:
        return token
    return await _login(session)


def _invalidate_token():
    """Clear the cached TVDB token (e.g. after a 401 response)."""
    state._TVDB_TOKEN["token"] = None
    state._TVDB_TOKEN["expires_at"] = 0.0


async def _tvdb_get(session, url):
    """Authenticated GET to a TVDB endpoint.

    Handles token acquisition and a single re-login retry on 401. Returns the
    parsed JSON body dict on success (status 200), or ``None`` on any failure
    (non-200, network error, auth failure). The caller treats ``None`` as
    "TVDB unavailable" and falls back to TMDB.
    """
    token = await _ensure_token(session)
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=3)
    try:
        async with session.get(url, headers=headers, timeout=timeout) as response:
            if response.status == 401:
                logger.debug("TVDB returned 401; invalidating token and retrying once")
                _invalidate_token()
                token = await _login(session)
                if not token:
                    return None
                headers = {"Authorization": f"Bearer {token}"}
                async with session.get(url, headers=headers, timeout=timeout) as retry:
                    if retry.status != 200:
                        logger.debug(f"TVDB GET retry failed: status {retry.status}")
                        return None
                    return await retry.json()
            if response.status != 200:
                logger.debug(f"TVDB GET failed: status {response.status} for {url}")
                return None
            return await response.json()
    except Exception as e:
        logger.debug(f"TVDB GET error for {url}: {e}", exc_info=True)
        return None


def _format_title(name, year):
    """Format a TVDB series name + year into the ``"Title Year"`` convention."""
    if not name:
        return None
    if year:
        result = f"{name} {year}"
        logger.info(f"Successfully looked up TV show via TVDB: {name} ({year})")
        return result
    logger.info(f"Successfully looked up TV show via TVDB: {name}")
    return name


def _series_year(series):
    """Extract a year string from a TVDB SeriesBaseRecord."""
    year = series.get("year")
    if year:
        return str(year)
    first_aired = series.get("firstAired", "") or ""
    if first_aired:
        return first_aired.split("-")[0]
    return ""


async def lookup_title_from_id(session, tvdbid=None, imdbid=None, search_type='movie'):
    """Look up a TV show title from a TVDB ID or IMDb ID via the TVDB v4 API.

    - ``tvdbid``: resolved via ``GET /v4/series/{id}``.
    - ``imdbid`` (TV-eligible search_type only): resolved via
      ``GET /v4/search/remoteid/tt{imdbid}``. The ``tt`` prefix is added if the
      passed imdbid lacks it (codebase convention stores imdbid without ``tt``).

    Returns the formatted ``"Title Year"`` (or just ``"Title"``) string, or
    ``None`` when TVDB is not configured, the ID is absent, or the lookup fails.
    Never raises — callers fall back to TMDB on ``None``.
    """
    api_key = settings.get_str("TVDB_API_KEY")
    if not api_key:
        return None

    try:
        if tvdbid:
            data = await _tvdb_get(session, f"{_TVDB_BASE}/series/{tvdbid}")
            if data and data.get("data"):
                series = data["data"]
                name = series.get("name", "")
                year = _series_year(series)
                return _format_title(name, year)

        if imdbid and search_type in ('tvsearch', 'search'):
            remote_id = imdbid if imdbid.startswith("tt") else f"tt{imdbid}"
            data = await _tvdb_get(session, f"{_TVDB_BASE}/search/remoteid/{remote_id}")
            if data and data.get("data"):
                # The remoteid search returns a SearchByRemoteIdResult object.
                # It may contain a single result object (with a ``series`` key)
                # or an array of result objects depending on the API version.
                result_obj = data["data"]
                series = None
                if isinstance(result_obj, list):
                    for entry in result_obj:
                        series = entry.get("series") if isinstance(entry, dict) else None
                        if series:
                            break
                elif isinstance(result_obj, dict):
                    series = result_obj.get("series")
                if series and isinstance(series, dict):
                    name = series.get("name", "")
                    year = _series_year(series)
                    return _format_title(name, year)

        return None
    except Exception as e:
        logger.warning(f"TVDB title lookup error: {e}", exc_info=True)
        return None


async def lookup_tvdbid_from_title(session, query, year=None):
    """Resolve a TV show title to its TVDB series ID via ``GET /v4/search``.

    Returns the TVDB id as a string, or ``None`` when TVDB is not configured,
    the query is empty, or no results are found. Never raises.
    """
    api_key = settings.get_str("TVDB_API_KEY")
    if not api_key or not query:
        return None

    try:
        url = f"{_TVDB_BASE}/search?type=series&query={query}"
        if year:
            url += f"&year={year}"
        data = await _tvdb_get(session, url)
        if data and data.get("data"):
            results = data["data"]
            if isinstance(results, list) and len(results) > 0:
                first = results[0]
                series_id = first.get("id")
                if series_id is not None:
                    logger.info(f"Successfully looked up TVDB id from title: {series_id}")
                    return str(series_id)
        return None
    except Exception as e:
        logger.warning(f"TVDB title->id lookup error: {e}", exc_info=True)
        return None