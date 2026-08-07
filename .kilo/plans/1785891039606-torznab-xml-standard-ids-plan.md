# Plan: Torznab XML per-indexer search + standard ID params (remove `{key:val}` tokens)

## Goal

Update the Pachelarr indexer system to:
1. Use **only standard Torznab ID parameters** — `imdbid`, `tvdbid`, `tmdbid`, `season`, `ep` — sent as distinct query params (NOT `{key:val}` tokens) whenever supported by the indexer.
2. Remove the `{key:val}` token-embedding machinery and the extra IDs it carried (`rid`, `tvmaze`, `traktid`, `doubanid`).
3. Keep `/api/v1/indexer` in **JSON** for capability-based indexer selection (unchanged).
4. Transition the **per-indexer search response** from JSON to **Torznab XML** via Prowlarr's `/<indexerId>/api` Torznab passthrough endpoint.
5. Rewrite consolidation to operate **natively on XML nodes** (mutate `seeders`/`peers`/`title`/`guid`/`enclosure`/magnet in place; emit the merged tree directly), replacing `generate_torznab_xml`.
6. Update `get_caps_xml` to advertise the full keep-list of ID params.
7. Update/create tests with XML fakes.

## Resolved decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | ID set + param style | Drop `rid`/`tvmaze`/`traktid`/`doubanid`; send `imdbid`/`tvdbid`/`tmdbid`/`season`/`ep` as standard Torznab query params; delete token maps. |
| 2 | XML search endpoint | `GET /<indexerId>/api?t=...&imdbid=...&tvdbid=...&tmdbid=...&season=...&ep=...&cat=...&limit=...&offset=...` (Prowlarr Torznab passthrough). |
| 3 | Consolidation approach | Native XML consolidation + direct emit (not adapter-to-dict). |
| 4 | Magnet resolution fallback | Keep `resolve_magnet_via_download`; source proxy URL from XML `<link>`/`<enclosure>`/`<guid>` when no `magnet:?` present. |
| 5 | Caps advertised params | `tv-search`: `q,season,ep,tvdbid,imdbid,tmdbid` ; `movie-search`: `q,imdbid,tmdbid` ; `search`: `q`. |
| 6 | `test_integration_prowlarr.py` | Indentation already fixed; rewrite its JSON fakes to XML. Also fix stale `CACHEBOX_TEST_FALLBACK_QUERY` references → `PACHELARR_TEST_FALLBACK_QUERY`. |

## Baseline (read before starting — do NOT regress pass-count below 39)

Current suite on Python 3.12: **36 failed, 39 passed**. The 36 failures are **pre-existing/environmental**, NOT caused by this change:
- `test_prowlarr_per_indexer.py` cache/select/parallel tests fail because `_capable_indexer()`/`_q_only_indexer()` test helpers omit `protocol: 'torrent'`, so `isTorrentIndexer` filters them out → empty results. (8 failures)
- `test_title_to_identifier_lookup.py` fails with `coroutine ... was never awaited` (asyncio event-loop / Python 3.12 `asyncio.get_event_loop()` deprecation). (12+ failures)
- Some `test_integration_prowlarr.py` tests reference retired `CACHEBOX_TEST_FALLBACK_QUERY`.

**Implementer rule:** after changes, the 39 currently-passing tests must still pass, AND new/rewritten tests must pass. Pre-existing failures that remain failing for the same root cause are acceptable, but the implementer SHOULD fix the `protocol: 'torrent'` gap in test helpers (trivial, unblocks ~8 tests) and the `CACHEBOX` rename since both are in-scope touched files.

---

## Implementation tasks

### Task 1: Replace token machinery with standard Torznab ID params

**File:** `main.py`

1. Delete `_TOKEN_KEY_FOR_MOVIE` and `_TOKEN_KEY_FOR_TV` (L443–459).
2. Add a single map of the **keep-list** our-param → Torznab query-param name. All names match (lowercase):
   ```python
   _TORZNAB_ID_PARAMS = ('imdbid', 'tvdbid', 'tmdbid', 'season', 'ep')
   ```
   (These are sent as-is as query params; no name remapping needed. `ep` is the Torznab param for episode.)
3. Keep `_PROWLARR_ENUM_TO_OUR_NAME` and `_indexer_supported_our_names` (still needed to know which IDs each indexer supports — read from JSON `/api/v1/indexer` capabilities). Remove the `rid`/`tvMazeId`/`traktId`/`doubanId` entries from `_PROWLARR_ENUM_TO_OUR_NAME` since those IDs are no longer forwarded (keep `imdbId`/`tmdbId`/`tvdbId`/`season`/`ep`/`q`/`year`/`genre`).
4. Rewrite `build_per_indexer_params(indexer, search_kwargs)`:
   - Keep: `idx_id` extraction, q-only skip logic, categories-as-list, limit/offset drop-zero, `type`.
   - **Remove** all `{key:val}` token composition (`tokens`, `composed_query`, the movie/tv token loops).
   - **New:** for each id in `_TORZNAB_ID_PARAMS`, if `id in supported` (capability-filtered) AND `search_kwargs.get(id)` is truthy, add `params[id] = str(value)`.
   - `params['query']` = just `base_query` (the title). IDs travel as separate params, not embedded in query.
   - **q-only indexer with only IDs and no query → still return None (skip)** — unchanged behavior.
   - Sanitization of `season`/`ep` to leading digits stays in `handle_search` (unchanged).
   - **Remove `params['indexerIds']`** — scoping is now via URL path (`/<indexerId>/api`), not a query param.
5. In `handle_search` (L679, L695): drop `rid`, `tvmaze`, `traktid`, `doubanid` from the `has_identifier` check and the optional-identifier pull-in loop. Keep only `imdbid`, `tvdbid`, `tmdbid`, `season`, `ep`.

### Task 2: Switch per-indexer search to Torznab XML endpoint

**File:** `main.py`

1. Rewrite `_search_one_indexer(session, sem, base_url, headers, indexer, params)`:
   - Build URL as `urljoin(PROWLARR_URL, f"/{idx_id}/api")` (path-scoped per indexer).
   - Map our `params` dict to Torznab query params:
     - `params['query']` → `q`
     - `params['type']` → `t` (`movie`/`tvsearch`/`search`)
     - `params['categories']` (list) → repeated `cat` params
     - `params['imdbid'/'tvdbid'/'tmdbid'/'season'/'ep']` → same-named params
     - `params['limit']`/`params['offset']` → same-named params (drop limit=0 already handled upstream)
   - Send `X-Api-Key` header (Prowlarr still authenticates the passthrough).
   - **Read response as TEXT (XML), not JSON**: `xml_bytes = await response.read()` / `response.text()`.
   - `response.raise_for_status()` on >=400.
   - Return the raw XML bytes (or `None` on per-indexer error, swallowed like today). Keep the per-indexer error isolation (one failing indexer returns None, others continue).
2. Rewrite `search_prowlarr_per_indexer(session, tasks)`:
   - `live = [(idx, p) for idx, p in tasks if p is not None]` (unchanged filter).
   - Run the coros under the same `asyncio.Semaphore(PROWLARR_PARALLEL_INDEXER_CONCURRENCY)`.
   - `await asyncio.gather(...)` returns a list of XML-bytes-or-None, in stable `live` order.
   - Return the list `[(idx, xml_bytes_or_None), ...]` (preserve order; drop None entries or keep as None — decide: **drop None entries**, keep only `(idx, xml_bytes)` pairs that succeeded).
3. Rewrite `search_prowlarr(session, search_kwargs)` wrapper:
   - Steps 1–3 unchanged (fetch cached indexers JSON, select, build per-indexer params).
   - Step 4: call the new `search_prowlarr_per_indexer`, return the list of `(indexer, xml_bytes)` pairs.
4. Delete `_normalize_prowlarr_results` (L869–887) — no longer needed (was JSON normalization).

### Task 3: Native XML consolidation + emit (replace `generate_torznab_xml`)

**File:** `main.py`

This is the core rewrite. New function `consolidate_and_emit_xml(indexer_xml_pairs, cached_status, uncached_seeders)` that:

1. **Parses each** `(indexer, xml_bytes)` with `lxml.etree.fromstring`. Tolerate malformed/empty: skip the doc on parse error (log warning), continue with others.
2. **Extracts items** from each doc's `//channel/item`. For each `<item>`, extract:
   - `infohash`: from `<torznab:attr name="infohash">` (preferred) else parse `xt=urn:btih:` from the magnet in `<link>`/`<guid>`/`<enclosure>`.
   - `magnet`: first of `<link>`/`<guid>`/`<enclosure url=>` containing `magnet:?`.
   - `proxy_url`: first of `<link>`/`<enclosure url=>` that is an `http(s)://.../download...` Prowlarr proxy URL (used for `resolve_magnet_via_download` fallback when no magnet with `tr=`).
   - `seeders`/`peers`: from `<torznab:attr name="seeders"|"peers">` (int).
   - `title`, `pubDate`, `comments`/`infoUrl`, `size`, `category` attrs — preserved as nodes for pass-through.
3. **Groups by lowercased infohash** across all indexer docs (same dedupe as today). Items without a hash go into a `non_hash_items` list (preserved, emitted unchanged).
4. **Per group:** pick canonical = item with highest original `seeders` (parse int, default 0). Merge `tr=` trackers from **every** item's magnet in the group (union, order-preserved, deduped) — reuse `parse_trackers_from_magnet`.
5. **Build canonical magnet** for the group: `magnet:?xt=urn:btih:<hash>` + merged `tr=` (same construction as `consolidate_all_items` L1195–1214). Set the canonical item's `<guid>` and (if magnet) `<link>` to this combined magnet so GUID carries unioned trackers (preserves current `*arr` behavior).
6. **Apply cached/scrape mutations** to the canonical item's XML nodes:
   - Cached hash: prefix `<title>` text with `[CACHED] ` ; set `<torznab:attr name="seeders">` value to `max(orig_seeders, PACHELARR_SEEDERS_BOOST)`.
   - Uncached with scrape entry: set `<torznab:attr name="seeders">` = `max(orig, scrape_seeders)`; set `<torznab:attr name="peers">` = `max(orig_leechers, scrape_leechers)`.
   - Ensure `<enclosure url=>` populated (prefer magnet, else proxy URL, else guid) — preserves current enclosure behavior.
   - Normalize `<pubDate>` to RFC1123 (reuse existing parse logic L1928–1946).
7. **Emit one merged RSS tree:** new `<rss>` root + `<channel>` with `<title>Torbox Cached Indexer</title>`, then append the canonical (mutated) `<item>` for each unique hash (skip already-emitted hashes via an `emitted` set), then append `non_hash_items` unchanged. Serialize with `ET.tostring(..., pretty_print=True, xml_declaration=True, encoding='UTF-8')` (match current output contract for `*arr`).
8. **Pass-through attrs preserved:** because canonical items are the actual parsed `<item>` nodes (moved into the new channel), every `<torznab:attr>`/element Prowlarr emitted that we DON'T mutate stays automatically (size, category, downloadvolumefactor, grabs, etc.). This is the forward-compat win.

**Delete** `consolidate_uncached_items` (L1081), `consolidate_all_items` (L1154), `generate_torznab_xml` (L1854) — replaced by the new function. **Keep** `extract_info_hashes`-equivalent logic inline in the new function (it now reads from XML). **Keep** `infohash_from_item`/`_get_magnet_uri_for_item`/`parse_trackers_from_magnet` as helpers but adapt them to take an XML element + extracted-fields dict instead of a Python dict (or add thin XML-aware variants). `dedupe_hashes_preserve_order` stays (used for Torbox chunking — still operates on a list of hash strings).

### Task 4: Wire `handle_search` to the new XML flow

**File:** `main.py` (L789–867)

1. `prowlarr_results_xml = await search_prowlarr(session, search_kwargs)` now returns `[(indexer, xml_bytes), ...]`.
2. **Info-hash extraction for Torbox:** parse each XML doc's items to collect hashes (the new consolidation function can expose a helper `extract_hashes_from_xml_pairs(pairs) -> [hash, ...]`, or do a light pre-scan). Feed to `check_torbox_cache(session, info_hashes)` (unchanged — Torbox API still JSON, unaffected).
3. Empty-results shortcut: if `search_prowlarr` returns `[]` pairs → `create_empty_rss()` (unchanged).
4. **Tracker-scrape branch (L811–865):** build `tracker_map` from the XML pairs:
   - For each unique uncached hash's canonical magnet (after merge, before emit) OR directly from each item's magnet: if a magnet with `tr=` exists, parse trackers. If not, and a `proxy_url` exists, call `resolve_magnet_via_download(session, proxy_url)` (unchanged function) with magnet cache (`_magnet_cache_get`/`_magnet_cache_put`). Build `tracker_map[tr].append(hash)`.
   - `scrape_trackers_inverted(tracker_map)` unchanged (UDP scrape, returns `uncached_seeders` dict).
5. `xml_response = consolidate_and_emit_xml(prowlarr_results_xml, cached_status, uncached_seeders)` → `Response(content=xml_response, media_type="application/xml")`.
6. `create_test_rss()` path (category-only test fallback): keep as-is (still used when Prowlarr returns no results but a synthetic test row helps Sonarr). Confirm `handle_search` still falls back to it where it currently does.

### Task 5: Update `get_caps_xml`

**File:** `main.py` (L1993–2007)

Replace the `<searching>` block:
```xml
<search>
  <search available="yes" supportedParams="q"/>
  <tv-search available="yes" supportedParams="q,season,ep,tvdbid,imdbid,tmdbid"/>
  <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
</search>
```
Categories block unchanged.

### Task 6: Update tests — XML fakes + standard-param assertions

Run from repo root (`make test`). All fakes that served `/api/v1/search` JSON must serve `/<id>/api` XML.

#### 6a. `tests/test_prowlarr_id_tokens.py` → rename/refocus to `test_prowlarr_id_params.py`
- Fakes: `FakeSession.get` now matches `/<id>/api` (not `/api/v1/search`); return `FakeCtx(200, XML_BYTES)` where `FakeCtx` has `.read()`/`.text()` returning XML (an empty `<rss>...<channel/></rss>` is fine for param-assertion tests). Keep serving `/api/v1/indexer` JSON (capability selection unchanged).
- `test_search_prowlarr_embeds_movie_id_tokens` → `test_search_prowlarr_sends_movie_id_params`: assert `params['imdbid']=='16118262'`, `params['tmdbid']=='12345'`, `params['q']=='Restart the Earth 2021'`, and NO `{key:val}` tokens in `q`, and `imdbid`/`tmdbid` are top-level params (not absent).
- `test_search_prowlarr_embeds_tv_id_tokens_with_name_mapping` → `test_search_prowlarr_sends_tv_id_params`: assert `params['tvdbid']=='76543'`, `params['season']=='1'`, `params['ep']=='2'`. **Remove** the `tvmaze`/`{tvmazeid:}` assertions (tvmaze dropped). Assert no `{...}` tokens in `q`.
- `test_search_prowlarr_no_tokens_for_generic_search_type` → keep (generic `search` takes no IDs): assert `params['q']=='Some Movie'`, no `imdbid` param.
- `test_search_prowlarr_id_only_movie_search_builds_token_query` → `test_search_prowlarr_id_only_movie_search_sends_id_param`: assert `params['imdbid']=='16118262'`, `params.get('q')` is empty/None, no token in q.
- `test_search_prowlarr_categories_only_fallback_still_works`: keep; assert `cat` params present as repeated/list, `q` = fallback.
- `test_search_prowlarr_plain_query_no_ids_unchanged`: keep; assert `params['q']=='Inception 2010'`.
- `test_handle_search_strips_http_version_from_ep_season` / `test_handle_search_drops_non_numeric_ep`: keep (sanitization logic unchanged); update `fake_search` to return XML pairs `[(idx, b'<rss><channel/></rss>')]` instead of dicts, OR monkeypatch a new `consolidate_and_emit_xml` stub. Update `captured` assertion target (still `search_kwargs` shape).
- **New test:** `test_search_prowlarr_no_keyval_tokens_anywhere`: for a full-cap indexer with all IDs, assert none of `{imdbid:`, `{tvdbid:`, `{tmdbid:`, `{season:`, `{episode:` appear in `q`.
- **New test:** `test_search_prowlarr_drops_dropped_ids`: send `rid`/`tvmaze`/`traktid`/`doubanid` in kwargs; assert none appear in params and no tokens emitted (regression guard for the removal).

#### 6b. `tests/test_prowlarr_per_indexer.py`
- Fix `_capable_indexer()`/`_q_only_indexer()`: add `'protocol': 'torrent'` to the returned dict (fixes the pre-existing `isTorrentIndexer` filter failures — unblocks ~8 tests).
- Remove `traktId`/`doubanId`/`rId`/`tvMazeId` from the `movieSearchParams`/`tvSearchParams` lists in test fixtures (they're no longer in `_PROWLARR_ENUM_TO_OUR_NAME`).
- `_IndexerSession.get`: match `/<id>/api` for search; return XML. `search_data` becomes XML bytes. Provide a helper `_torznab_xml(items)` to build a minimal Torznab RSS with `<item>`s from simple dicts for tests.
- `test_build_per_indexer_params_*`: update assertions — no `{...}` tokens; IDs are top-level params; `indexerIds` no longer in params (scoped by URL). `test_build_per_indexer_params_full_cap_keeps_tokens` → `..._sends_id_params`: assert `params['tvdbid']=='76543'`, `params['season']=='1'`, `params['ep']=='2'`, `params['imdbid']=='tt123'`, no `{...}`.
- `test_build_per_indexer_params_movie_vs_tvsearch`: movie params include `imdbid` not `tvdbid`; tvsearch includes both.
- `test_search_prowlarr_per_indexer_parallel_calls_n_indexers`: each fake `/<id>/api` returns an XML doc with one `<item>` whose `infohash` attr = `H<id>`; assert concatenated XML pairs produce 3 items after consolidation.
- `test_search_prowlarr_per_indexer_one_failure_does_not_abort_others`: failing indexer returns None/error; successful one's XML item survives.
- `test_search_prowlarr_per_indexer_respects_concurrency_cap`: unchanged mechanism (semaphore), fakes return XML ctx.
- `test_search_prowlarr_per_indexer_skips_none_tasks`: unchanged (None params dropped before dispatch).

#### 6c. `tests/test_integration_prowlarr.py` (rewrite JSON fakes → XML)
- Replace all `FakeCtx.json()`/JSON-data fakes with XML: `FakeCtx` gets `.read()`/`.text()` returning XML bytes; `FakeSession.get` matches `/<id>/api`.
- Fix all `CACHEBOX_TEST_FALLBACK_QUERY` → `PACHELARR_TEST_FALLBACK_QUERY` (L233,234,349,350,492,493,608,609,748,750,859,860,1005,1007,1116,1117) — both `setenv` and `m.<NAME>` lines.
- `test_search_prowlarr_fallback_and_categories_list`: fake returns XML with `cat`-matching items; assert fallback `q` flows, `cat` repeated.
- `test_search_prowlarr_forwards_paging`: assert `limit`/`offset` forwarded as params on `/<id>/api`; `limit=0` dropped.
- `test_search_prowlarr_does_not_forward_limit_zero`: update to XML fake; assert no `limit` param.
- `test_handle_search_forwards_limit_offset`: monkeypatch `search_prowlarr` to return XML pairs; assert `search_kwargs` carries limit/offset.
- `test_handle_search_category_only_fallback_returns_nonempty_xml`: assert final response is valid XML with ≥1 item (or test row).
- `test_integration_unioned_trackers_and_dedupe` (L907): feed two XML docs with same infohash + different `tr=` trackers; assert merged output has union of trackers in one `<item>` and the GUID magnet contains all `tr=`.

#### 6d. `tests/test_integration_prowlarr_minimal.py`
- `FakeCtx`: add `.read()` returning XML bytes; `FakeSession.get` matches `/<id>/api` returning empty `<rss><channel/></rss>`.
- `test_search_prowlarr_does_not_forward_limit_zero`: assert `limit` not in params for the `/<id>/api` call.

#### 6e. `tests/test_torbox.py` (XML input to consolidation/XML emit)
- This file tests `consolidate_uncached_items`/`consolidate_all_items`/`generate_torznab_xml` (now deleted) + `check_torbox_cache` + scrape. **Major rewrite.**
- `check_torbox_cache` tests: unchanged (Torbox API is still JSON; `FakeSession.post` returns JSON). Keep as-is.
- `test_extract_info_hashes_order`: rewrite to call the new `extract_hashes_from_xml_pairs` (or equivalent) on XML inputs.
- `test_consolidate_*` and `test_generate_torznab_*`: rewrite to build XML pairs `[(idx, xml_with_items)]` and call `consolidate_and_emit_xml`; assert on the emitted XML (parse with lxml, check `<item>` count, `<torznab:attr name="seeders">` values, `[CACHED]` title prefix, unioned `tr=` in `<guid>` magnet, dedupe, enclosure populated, pubDate present, canonical GUID).
- `test_scrape_trackers_inverted_max`: monkeypatch `_udp_scrape_tracker` (unchanged); feed XML pairs with magnets; assert seeders/peers attrs reflect scrape max.
- `test_full_pipeline_integration` / `test_prowlarr_has_duplicates_but_cachebox_dedupes`: monkeypatch `search_prowlarr` to return XML pairs with duplicate infohashes across indexers; assert one consolidated `<item>` with merged trackers.
- `test_consolidated_magnet_uses_ampersand_between_xt_and_tr` / `test_consolidate_creates_canonical_magnet_when_missing` / `test_consolidate_includes_guid_trackers` / `test_generate_guid_contains_unioned_trackers`: rewrite to assert on the emitted XML's `<guid>`/`<link>` magnet string containing `xt=...&tr=...`.

#### 6f. New dedicated test file: `tests/test_torznab_xml_consolidation.py`
Cover the new `consolidate_and_emit_xml` directly with focused cases:
- Two indexers, same hash, disjoint trackers → one item, unioned `tr=`, highest-seeder metadata wins.
- Cached hash → `[CACHED]` prefix + boosted seeders attr.
- Uncached + scrape entry → seeders/peers attrs = max(orig, scrape).
- Pass-through attr preservation: include a `<torznab:attr name="downloadvolumefactor">` in input XML; assert it survives in output (forward-compat proof).
- Non-hash item → emitted unchanged.
- Malformed XML doc → skipped, other docs still consolidate.
- Empty pairs → returns empty RSS (matches `create_empty_rss` contract).
- pubDate normalization (ISO → RFC1123).
- `<enclosure>` populated from magnet when input lacked it.

### Task 7: Misc cleanup

- Update the `AGENTS.md` "Per-indexer scoping" note: `handle_search` now scopes via `/<indexerId>/api` URL path (not `indexerIds=` param); IDs sent as standard Torznab params (not `{key:val}` tokens); search response is Torznab XML.
- Remove the env-var table rows / notes referencing the token behavior if any (none found, but verify).
- Confirm `lxml` already a dependency (it is — `from lxml import etree as ET`). No new deps.

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Prowlarr `/<indexerId>/api` param names differ from spec for some IDs | Task 1/2 maps only the 5 keep-list IDs using standard Torznab names; verify against one real Prowlarr indexer before full rollout (manual smoke test: `curl "$PROWLARR_URL/<id>/api?t=tvsearch&tvdbid=76543&season=1&ep=2" -H "X-Api-Key: ..."`). |
| Native XML consolidation rewrites the most-tested logic (merge/dedupe/trackers) | Task 6f adds a dedicated consolidation test file mirroring every existing `consolidate_*`/`generate_torznab_*` assertion against XML output. Behavior parity is the acceptance bar. |
| Forward-compat attr passthrough depends on copying parsed `<item>` nodes into the new channel | lxml allows moving elements across trees via `new_channel.append(item_elem)` (detaches from old parent). Verify with the pass-through-attr test in 6f. |
| Magnet resolution fallback needs the proxy URL from XML | Task 3 step 2 extracts `proxy_url` explicitly; Task 4 step 4 feeds it to the unchanged `resolve_magnet_via_download`. |
| Dropping `rid`/`tvmaze`/`traktid`/`doubanid` is a behavior change for clients sending them | TMDB title-lookup fallback in `handle_search` covers imdbid/tvdbid/tmdbid (already implemented). `rid`/`tvmaze`/`traktid`/`doubanid` lose direct forwarding — accepted per decision #1. Document in AGENTS.md. |
| Pre-existing 36 test failures may obscure regressions | Baseline recorded above. Implementer fixes the `protocol:'torrent'` test-helper gap and `CACHEBOX`→`PACHELARR` rename (both in-scope), which should bring pre-existing failures down; any remaining failures must match the documented root causes. |

## Validation

1. `make test` from repo root — all currently-passing tests still pass; all new/rewritten tests pass.
2. Manual smoke against a real Prowlarr instance:
   - `curl "http://localhost:6800/api?t=movie&q=Inception"` → valid Torznab XML with items.
   - `curl "http://localhost:6800/api?t=tvsearch&tvdbid=76543&season=1&ep=2"` → valid XML; verify a Prowlarr indexer that supports tvdbid received `tvdbid` as a standard param (check Prowlarr logs).
   - `curl "http://localhost:6800/api?t=caps"` → updated `supportedParams`.
   - Confirm no `{key:val}` tokens appear in any Prowlarr-side query log.
3. Confirm Torbox cache boost still applies: a known-cached hash shows `[CACHED]` title + boosted seeders in output XML.
4. If `TRACKER_SCRAPE_ENABLED=true`: confirm uncached items get scrape-derived seeders/peers attrs.

## Out of scope

- Rust migration / streaming-merge optimization (separate effort).
- TMDB lookup logic (`lookup_title_from_id`/`lookup_identifier_from_query`) — unchanged.
- UDP scrape protocol (`_udp_scrape_*`, `scrape_trackers_inverted`) — unchanged.
- Torbox cache API (`check_torbox_cache`) — unchanged (still JSON).
- Caching infrastructure (`_indexers_cache_*`, `_scrape_cache_*`, `_tmdb_title_cache_*`, `_magnet_cache_*`) — unchanged.