import asyncio
import logging

import aiohttp

from pachelarr import state
from pachelarr.torznab import dedupe_hashes_preserve_order

logger = logging.getLogger("pachelarr")


async def check_torbox_cache(session, hashes):
    """Checks Torbox cache for a list of info hashes."""
    import main

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {main.TORBOX_API_KEY}"
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
        logger.debug(
            f"Torbox cache check: POST {state.TORBOX_CHECK_URL} total.hashes={total_hashes} unique.hashes={len(unique_hashes)} dedupe_removed={dedupe_removed_count} Authorization=Bearer {_mask_key(main.TORBOX_API_KEY)}"  # noqa: E501
        )

        combined = {}
        total_hits = 0

        async def _call_chunk(chunk):
            """Call Torbox for given chunk, return mapping or raise."""
            attempt = 1
            backoff = state.TORBOX_RETRY_BACKOFF
            while attempt <= state.TORBOX_MAX_RETRIES:
                try:
                    async with session.post(state.TORBOX_CHECK_URL, json={'hashes': chunk}, headers=headers) as response:  # noqa: E501
                        if response.status == 401:
                            logger.warning("Torbox returned 401 Unauthorized. Check TORBOX_API_KEY. Aborting cache checks.")  # noqa: E501
                            return None
                        if response.status >= 500:
                            logger.warning(f"Torbox server error (status {response.status}); attempt {attempt}/{state.TORBOX_MAX_RETRIES}")  # noqa: E501
                        else:
                            response.raise_for_status()
                            data = await response.json()
                            return data
                except aiohttp.ClientError as e:
                    logger.warning(f"Torbox request error: {e}; attempt {attempt}/{state.TORBOX_MAX_RETRIES}")
                await asyncio.sleep(backoff)
                backoff *= 2
                attempt += 1
            logger.warning("Torbox cache check failed after retries for chunk")
            return {}

        for i in range(0, len(unique_hashes), state.TORBOX_CHUNK_SIZE):
            chunk = unique_hashes[i:i+state.TORBOX_CHUNK_SIZE]
            logger.debug(f"Torbox cache chunk: POST {state.TORBOX_CHECK_URL} chunk.len={len(chunk)} Authorization=Bearer {_mask_key(main.TORBOX_API_KEY)}")  # noqa: E501
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
        hits_n = len(combined)
        state.torbox_hits += hits_n
        state.torbox_misses += max(len(unique_hashes) - hits_n, 0)
        return combined
    except aiohttp.ClientError as e:
        logger.exception(f"Error checking Torbox cache: {e}")
        return {}
