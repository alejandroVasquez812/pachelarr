import logging
import time

import aiohttp

from pachelarr import db, settings, state

logger = logging.getLogger("pachelarr")


def strip_foreign_language_tag(query):
    if not query:
        return query
    stripped = query.strip()
    if ' ' not in stripped:
        return query
    last_token = stripped.rsplit(' ', 1)[-1]
    if last_token.isalpha() and len(last_token) == 2 and last_token.upper() in state.FOREIGN_LANGUAGE_TAGS:
        cleaned = stripped.rsplit(' ', 1)[0].rstrip()
        return cleaned if cleaned else query
    return query


async def lookup_title_from_id(session, imdbid=None, tmdbid=None, tvdbid=None, rid=None, search_type='movie'):
    """Look up movie/TV title from external IDs using TMDB API.

    TMDB supports:
    - IMDb IDs (movies and TV shows)
    - TVDB IDs (TV shows)
    - TVRage IDs (TV shows, deprecated)
    - Direct TMDB IDs (movies and TV shows)

    Requires TMDB_API_KEY environment variable.
    Get a free API key at: https://www.themoviedb.org/settings/api
    """
    tmdb_api_key = settings.get_str("TMDB_API_KEY")
    if not tmdb_api_key:
        logger.debug("TMDB_API_KEY not configured, skipping title lookup. Set TMDB_API_KEY env var to enable ID-based search support.")  # noqa: E501
        return None

    try:
        if imdbid:
            url = f"https://api.themoviedb.org/3/find/tt{imdbid}?api_key={tmdb_api_key}&external_source=imdb_id"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('movie_results') and len(data['movie_results']) > 0:
                        movie = data['movie_results'][0]
                        title = movie.get('title', '')
                        release_date = movie.get('release_date', '')
                        year = release_date.split('-')[0] if release_date else ''
                        if title and year:
                            logger.info(f"Successfully looked up movie via TMDB (IMDb): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up movie via TMDB (IMDb): {title}")
                            return title
                    if data.get('tv_results') and len(data['tv_results']) > 0:
                        show = data['tv_results'][0]
                        title = show.get('name', '')
                        first_air = show.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (IMDb): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (IMDb): {title}")
                            return title

        if tvdbid:
            url = f"https://api.themoviedb.org/3/find/{tvdbid}?api_key={tmdb_api_key}&external_source=tvdb_id"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('tv_results') and len(data['tv_results']) > 0:
                        show = data['tv_results'][0]
                        title = show.get('name', '')
                        first_air = show.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (TVDB): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (TVDB): {title}")
                            return title

        if rid:
            url = f"https://api.themoviedb.org/3/find/{rid}?api_key={tmdb_api_key}&external_source=tvrage_id"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('tv_results') and len(data['tv_results']) > 0:
                        show = data['tv_results'][0]
                        title = show.get('name', '')
                        first_air = show.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (TVRage): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (TVRage): {title}")
                            return title

        if tmdbid:
            if search_type in ('movie', 'search'):
                url = f"https://api.themoviedb.org/3/movie/{tmdbid}?api_key={tmdb_api_key}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                    if response.status == 200:
                        data = await response.json()
                        title = data.get('title', '')
                        release_date = data.get('release_date', '')
                        year = release_date.split('-')[0] if release_date else ''
                        if title and year:
                            logger.info(f"Successfully looked up movie via TMDB (TMDB ID): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up movie via TMDB (TMDB ID): {title}")
                            return title
            else:
                url = f"https://api.themoviedb.org/3/tv/{tmdbid}?api_key={tmdb_api_key}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                    if response.status == 200:
                        data = await response.json()
                        title = data.get('name', '')
                        first_air = data.get('first_air_date', '')
                        year = first_air.split('-')[0] if first_air else ''
                        if title and year:
                            logger.info(f"Successfully looked up TV show via TMDB (TMDB ID): {title} ({year})")
                            return f"{title} {year}"
                        elif title:
                            logger.info(f"Successfully looked up TV show via TMDB (TMDB ID): {title}")
                            return title

        logger.debug(f"Could not lookup title for imdbid={imdbid} tmdbid={tmdbid} tvdbid={tvdbid} rid={rid}")
        return None
    except Exception as e:
        logger.warning(f"Error looking up title from ID: {e}", exc_info=True)
        return None


async def lookup_identifier_from_query(session, query, search_type='movie'):
    """Look up TMDB/IMDb/TVDB IDs from a title query via TMDB search.

    Reverse of lookup_title_from_id: given a title (optionally with a trailing
    year), resolve imdbid (movies+TV) and tvdbid (TV) so search_prowlarr can
    emit {imdbid:..}/{tvdbid:..} tokens for ID-only indexers.

    Returns a dict like {'tmdbid':..,'imdbid':..,'tvdbid':..} (only keys found),
    or None on failure/empty. imdbid is stored WITHOUT the 'tt' prefix to match
    codebase convention. Requires TMDB_API_KEY AND TMDB_TITLE_LOOKUP_ENABLED.
    """
    tmdb_api_key = settings.get_str("TMDB_API_KEY")
    if not tmdb_api_key or not settings.get_bool("TMDB_TITLE_LOOKUP_ENABLED") or not query:
        logger.debug("Title->ID lookup disabled or missing query/TMDB_API_KEY; skipping.")
        return None

    year = None
    stripped = query.strip()
    if ' ' in stripped:
        last_token = stripped.rsplit(' ', 1)[-1]
        if last_token.isdigit() and len(last_token) == 4:
            year = last_token
            stripped = stripped.rsplit(' ', 1)[0].rstrip()

    stripped = strip_foreign_language_tag(stripped)

    cache_key = (stripped.lower(), year, search_type)
    cached = _tmdb_title_cache_get(cache_key)
    if cached is not None:
        logger.debug(f"Title->ID lookup cache hit for {cache_key!r}: {cached}")
        return cached

    try:
        tmdb_id = None
        if search_type == 'movie':
            url = f"https://api.themoviedb.org/3/search/movie?api_key={tmdb_api_key}&query={stripped}"
            if year:
                url += f"&year={year}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    if results:
                        tmdb_id = results[0].get('id')
            if tmdb_id:
                ext_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids?api_key={tmdb_api_key}"
                async with session.get(ext_url, timeout=aiohttp.ClientTimeout(total=3)) as ext_response:
                    if ext_response.status == 200:
                        ext_data = await ext_response.json()
                        imdb_raw = ext_data.get('imdb_id') or ''
                        imdbid = imdb_raw[2:] if imdb_raw.startswith('tt') else imdb_raw
                        ids = {'tmdbid': str(tmdb_id)}
                        if imdbid:
                            ids['imdbid'] = imdbid
                        logger.info(f"Successfully looked up movie IDs from title: {ids}")
                        _tmdb_title_cache_put(cache_key, ids)
                        return ids
        elif search_type == 'tvsearch':
            url = f"https://api.themoviedb.org/3/search/tv?api_key={tmdb_api_key}&query={stripped}"
            if year:
                url += f"&first_air_date_year={year}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    if results:
                        tmdb_id = results[0].get('id')
            if tmdb_id:
                ext_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids?api_key={tmdb_api_key}"
                async with session.get(ext_url, timeout=aiohttp.ClientTimeout(total=3)) as ext_response:
                    if ext_response.status == 200:
                        ext_data = await ext_response.json()
                        imdb_raw = ext_data.get('imdb_id') or ''
                        imdbid = imdb_raw[2:] if imdb_raw.startswith('tt') else imdb_raw
                        tvdb_raw = ext_data.get('tvdb_id')
                        ids = {'tmdbid': str(tmdb_id)}
                        if imdbid:
                            ids['imdbid'] = imdbid
                        if tvdb_raw:
                            ids['tvdbid'] = str(tvdb_raw)
                        logger.info(f"Successfully looked up TV IDs from title: {ids}")
                        _tmdb_title_cache_put(cache_key, ids)
                        return ids

        logger.debug(f"Could not lookup IDs from title: query={query!r} search_type={search_type}")
        return None
    except Exception as e:
        logger.warning(f"Error looking up IDs from title: {e}", exc_info=True)
        return None


def _tmdb_title_cache_get(key):
    if not key:
        return None
    entry = state._TMDB_TITLE_CACHE.get(key)
    if entry is None:
        return None
    if entry.get('expires', 0) <= time.time():
        return None
    state._TMDB_TITLE_CACHE.move_to_end(key)
    return entry.get('ids')


def _tmdb_key_to_str(key):
    """Serialize a TMDB cache key tuple to a stable string for SQLite storage."""
    import json
    return json.dumps(key, sort_keys=True)


def _tmdb_title_cache_put(key, ids):
    if not key or not ids:
        return
    ttl = settings.get_int("TMDB_TITLE_LOOKUP_CACHE_TTL", 300)
    max_entries = settings.get_int("TMDB_TITLE_LOOKUP_CACHE_MAX", 5000)
    expires = time.time() + ttl
    state._TMDB_TITLE_CACHE[key] = {'ids': dict(ids), 'expires': expires}
    state._TMDB_TITLE_CACHE.move_to_end(key)
    while len(state._TMDB_TITLE_CACHE) > max_entries:
        try:
            state._TMDB_TITLE_CACHE.popitem(last=False)
        except KeyError:
            break
    try:
        db.upsert_tmdb_title(_tmdb_key_to_str(key), dict(ids), expires)
    except Exception as e:
        logger.debug(f"_tmdb_title_cache_put: DB upsert failed for {key}: {e}", exc_info=True)
