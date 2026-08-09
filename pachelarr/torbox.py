import asyncio
import logging

import aiohttp

from pachelarr import settings, state
from pachelarr.torznab import dedupe_hashes_preserve_order

logger = logging.getLogger("pachelarr")


async def check_torbox_cache(session, hashes):
    """Checks Torbox cache for a list of info hashes."""
    try:
        torbox_api_key = settings.get_str("TORBOX_API_KEY")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {torbox_api_key}"
        }

        def _mask_key(k):
            if not k:
                return None
            if len(k) <= 8:
                return "****"
            return k[:4] + "*" * (len(k) - 8) + k[-4:]
        if not hashes:
            return {}

        total_hashes = len(hashes)
        unique_hashes = dedupe_hashes_preserve_order(hashes)
        dedupe_removed_count = total_hashes - len(unique_hashes)
        if dedupe_removed_count:
            logger.debug(f"Torbox cache check: dedupe_removed={dedupe_removed_count}")
        check_url = settings.get_str("TORBOX_CHECK_URL", "https://api.torbox.app/v1/api/torrents/checkcached")
        logger.debug(
            f"Torbox cache check: POST {check_url} total.hashes={total_hashes} unique.hashes={len(unique_hashes)} dedupe_removed={dedupe_removed_count} Authorization=Bearer {_mask_key(torbox_api_key)}"  # noqa: E501
        )

        combined = {}
        total_hits = 0

        async def _call_chunk(chunk):
            """Call Torbox for given chunk, return mapping or raise."""
            max_retries = settings.get_int("TORBOX_MAX_RETRIES", 3)
            attempt = 1
            backoff = settings.get_float("TORBOX_RETRY_BACKOFF", 0.5)
            while attempt <= max_retries:
                try:
                    async with session.post(check_url, json={'hashes': chunk}, headers=headers) as response:  # noqa: E501
                        if response.status == 401:
                            logger.warning("Torbox returned 401 Unauthorized. Check TORBOX_API_KEY. Aborting cache checks.")  # noqa: E501
                            return None
                        if response.status >= 500:
                            logger.warning(f"Torbox server error (status {response.status}); attempt {attempt}/{max_retries}")  # noqa: E501
                        else:
                            response.raise_for_status()
                            data = await response.json()
                            return data
                except aiohttp.ClientError as e:
                    logger.warning(f"Torbox request error: {e}; attempt {attempt}/{max_retries}")
                await asyncio.sleep(backoff)
                backoff *= 2
                attempt += 1
            logger.warning("Torbox cache check failed after retries for chunk")
            return {}

        chunk_size = settings.get_int("TORBOX_CHUNK_SIZE", 100)
        for i in range(0, len(unique_hashes), chunk_size):
            chunk = unique_hashes[i:i+chunk_size]
            logger.debug(f"Torbox cache chunk: POST {check_url} chunk.len={len(chunk)} Authorization=Bearer {_mask_key(torbox_api_key)}")  # noqa: E501
            try:
                result = await _call_chunk(chunk)
                if result is None:
                    return {}
                if isinstance(result, dict):
                    if 'data' in result:
                        data_map = result['data']
                        if isinstance(data_map, dict):
                            hits = len(data_map)
                            logger.debug(f"Torbox chunk response: hits={hits}")
                            total_hits += hits
                            for k, v in data_map.items():
                                combined[k.lower()] = v
                        elif isinstance(data_map, list):
                            hits = len(data_map)
                            logger.debug(f"Torbox chunk response list: hits={hits}")
                            total_hits += hits
                            for obj in data_map:
                                if isinstance(obj, dict) and obj.get('hash'):
                                    combined[obj['hash'].lower()] = obj
                    else:
                        hits = len(result)
                        logger.debug(f"Torbox chunk response (mapping): hits={hits}")
                        total_hits += hits
                        for k, v in result.items():
                            combined[k.lower()] = v
                elif isinstance(result, list):
                    hits = len(result)
                    logger.debug(f"Torbox chunk response list (top-level): hits={hits}")
                    total_hits += hits
                    for obj in result:
                        if isinstance(obj, dict) and obj.get('hash'):
                            combined[obj['hash'].lower()] = obj
                else:
                    logger.debug(f"Unexpected Torbox chunk response data type: {type(result)}")
            except Exception as e:
                logger.exception(f"Error processing Torbox chunk: {e}")
                continue
        logger.info(f"Torbox cache check: total cached hits={total_hits}")
        return combined
    except aiohttp.ClientError as e:
        logger.exception(f"Error checking Torbox cache: {e}")
        return {}


def _torbox_cache_get_known(hashes):
    """Return the subset of lowercased infohashes known-cached in _TORBOX_CACHE.

    Read-only probe that moves hits to the LRU end (recency) but does not
    consult Torbox. Misses are absent from the returned set.
    """
    known = set()
    for h in hashes:
        if not h:
            continue
        key = h.lower() if isinstance(h, str) else h
        if state._TORBOX_CACHE.get(key) is not None:
            known.add(key)
            state._TORBOX_CACHE.move_to_end(key)
    return known


def _torbox_cache_put_many(hashes):
    """Record infohashes as known-cached with LRU bound + SQLite write-through.

    Only cached hits should be passed here; uncached hashes are NOT cached so
    they get re-checked against Torbox on repeat searches. The cache has no
    TTL (cached results are very unlikely to flip).
    """
    from pachelarr import db
    for h in hashes:
        if not h:
            continue
        key = h.lower() if isinstance(h, str) else h
        state._TORBOX_CACHE[key] = True
        state._TORBOX_CACHE.move_to_end(key)
        max_torbox = state.torbox_cache_max()
        while len(state._TORBOX_CACHE) > max_torbox:
            try:
                state._TORBOX_CACHE.popitem(last=False)
            except KeyError:
                break
        try:
            db.upsert_torbox(key)
        except Exception as e:
            logger.debug(f"_torbox_cache_put_many: DB upsert failed for {key}: {e}", exc_info=True)
