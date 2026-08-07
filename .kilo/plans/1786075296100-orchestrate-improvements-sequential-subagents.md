# Plan: Orchestrate Pachelarr Improvements via Sequential Subagents

> Tracker file: `IMPROVEMENTS.md` (10 items, P0→P3)
> Working tree: clean (main.py + tests committed at `436e066`)
> Decisions confirmed with user:
> - Isolation: new branch `improvements/*`, **one commit per completed improvement**
> - Ordering: strictly sequential (one subagent at a time) because almost every item edits `main.py`
> - Include improvement #7 (package split) as the final step
> - After each improvement: subagent updates `IMPROVEMENTS.md` checkboxes, then orchestrator commits + marks complete

---

## Context

`IMPROVEMENTS.md` lists 10 recommendations from a codebase review. They range from XS (rename an import) to L (split `main.py` into a package). Almost all touch `main.py` or shared test fakes, so parallel subagents would corrupt each other's edits. The orchestrator's job is to dispatch **one subagent per improvement, sequentially**, verify it, commit it, and tick off the tracker.

Conventions the subagents must follow (from `AGENTS.md`):
- Tests import `main` as a module; run pytest from repo root.
- `test_integration_prowlarr.py` has a pre-existing `IndentationError` (~L1009) — fix only where the task explicitly calls for it.
- Module-level caches (`_SCRAPE_CACHE`, `_INDEXERS_CACHE`, etc.) are shared across tests; tests must `.clear()` them.
- Env vars are module globals read at import; tests monkeypatch the module attribute.
- No CI/lint config exists yet (item #10 adds it).
- Do **not** add comments to source unless asked.

## Orchestrator workflow (per improvement)

1. Dispatch exactly **one** subagent (see dispatch template below).
2. Subagent works autonomously: edits, runs tests, updates `IMPROVEMENTS.md` checkboxes for its item only, reports back.
3. Orchestrator verifies: `git diff --stat`, `python3 -m pytest -q` (or the scoped subset named in the task), `git diff IMPROVEMENTS.md`.
4. If tests pass and diff matches the item scope → orchestrator commits with the item's canonical message.
5. Orchestrator moves to the next item. If a subagent fails/stalls, orchestrator rolls back to the previous commit and re-dispatches with the failure output appended.

## Branch strategy

- Create `improvements/pachelarr-2026-08-06` off current HEAD (`436e066`).
- Each improvement = one commit on this branch. Commit messages:
  - `fix(tests): restore _udp_scrape_one import / indent collection` (#1)
  - `feat(api): add per-indexer HTTP search timeout` (#8)
  - `perf(http): share a single aiohttp.ClientSession` (#3)
  - `feat(config): validate required env vars at startup` (#4)
  - `feat(api): add /healthz and /statsz endpoints` (#2)
  - `fix(caches): make module caches true LRU` (#9)
  - `refactor: narrow broad except Exception blocks` (#5)
  - `test: enable pytest-asyncio auto mode and dedupe fakes` (#6)
  - `chore: add ruff lint/format config and make targets` (#10)
  - `refactor: split main.py into pachelarr/ package` (#7)
- Final state: `main.py` re-exports `app` so `uvicorn main:app` still works; all 93+ tests pass.

## Dispatch order (sorted by dependency, not just priority)

Sequencing resolves dependencies between items:

1. **#1 — Fix broken test collection** (P0, XS). Must run first: nothing else can be safely verified until `pytest --collect-only` reports 0 errors.
2. **#8 — Per-indexer HTTP timeout** (P1, XS). Isolated to `_search_one_indexer`; safe after #1.
3. **#3 — Shared ClientSession** (P1, S). Touches `handle_search` + startup; do before #4 since both edit startup/lifespan.
4. **#4 — Env var startup validation** (P1, S). Builds on the lifespan added in #3.
5. **#2 — `/healthz` + `/statsz`** (P2, S). Adds routes; independent of search path.
6. **#9 — LRU caches** (P2, S). Isolated to the four cache helpers; pure refactor.
7. **#5 — Narrow `except Exception`** (P2, M). Wide diff across `main.py`; do after #6 so LRU tests already lock cache behavior.
8. **#6 — pytest-asyncio auto + dedupe fakes** (P2, M). Restructures tests; do before #7's import churn and after #5 so behavior is stable.
9. **#10 — ruff + make lint** (P2, S–M). Run last before the split so it auto-formats the final single-file shape once.
10. **#7 — Split into package** (P3, L). Final, highest-risk; benefits from all prior items being green and lint-clean.

## Per-subagent task specs

Each subagent receives the dispatch template below plus its item-specific spec. Each spec lists: scope (files), acceptance (test command + result), and the `IMPROVEMENTS.md` checkboxes to mark `[x]`.

### Dispatch template (sent to every subagent)

```
ROLE: You are implementing exactly ONE improvement in the Pachelarr repo at
/home/ale23xd/projects/pachelarr. Do not touch other improvements.

CONSTRAINTS:
- Edit ONLY the files named in your task scope. No stray edits.
- Do NOT add comments to source unless the task explicitly requires one.
- Do NOT commit. Do NOT create branches. Do NOT push.
- Follow AGENTS.md conventions (tests import `main`, clear module caches in
  tests, env vars are module globals, run pytest from repo root).
- Preserve `uvicorn main:app` working (main.py must keep exposing `app`).

WHEN DONE:
1. Run the acceptance test command. It MUST pass (0 errors/failures).
2. Update IMPROVEMENTS.md: change each task bullet under your item from
   "[ ]" to "[x]" (only your item). Do not edit other items.
3. Reply with: files changed (with line counts), the exact `git diff --stat`,
   and the test command output. Do NOT commit.

IF BLOCKED: stop and report what failed + the exact error; do not guess.
```

### Item specs

#### #1 — Fix broken test collection
- Scope: `tests/test_udp_scrape_protocol.py`, `tests/test_integration_prowlarr.py`, optionally `main.py` (only if you choose the alias approach).
- Tasks: (a) make `test_udp_scrape_protocol.py` importable (rename its `_udp_scrape_one` import to `_udp_scrape_tracker`, OR add a `_udp_scrape_one = _udp_scrape_tracker` alias in `main.py` — prefer renaming the test import, less surface); (b) fix the `IndentationError` around `test_integration_prowlarr.py:1009`.
- Acceptance: `python3 -m pytest --collect-only -q` → 0 errors; `python3 -m pytest -q` → no collection errors (pre-existing test failures, if any, are allowed only if they predate this task — record them).
- Checkboxes: the three under "## 1. Fix broken test collection".

#### #8 — Per-indexer HTTP timeout
- Scope: `main.py` (env var block + `_search_one_indexer`), `README.md` env-var table, optionally a test.
- Tasks: add `PROWLARR_INDEXER_SEARCH_TIMEOUT` (default `10.0`); pass `aiohttp.ClientTimeout(total=...)` to the per-indexer `session.get`; ensure `asyncio.TimeoutError` returns `None` (caught by existing `except Exception` is fine, but prefer an explicit `except asyncio.TimeoutError`).
- Acceptance: `python3 -m pytest tests/test_prowlarr_per_indexer.py -q` passes; add one test asserting a hung indexer yields `None` and does not abort the gather.
- Checkboxes: the three under "## 8. Add per-indexer HTTP timeouts".

#### #3 — Shared ClientSession
- Scope: `main.py` (app startup/shutdown, `handle_search`, `check_torbox_cache`, TMDB lookups), affected tests.
- Tasks: create one `aiohttp.ClientSession` at startup with a shared `TCPConnector`; pass it through `handle_search` and downstream calls; close on shutdown (use FastAPI `lifespan`); update tests that construct their own session to keep working (they use fakes — ensure fakes still satisfy the new call sites).
- Acceptance: full suite `python3 -m pytest -q` passes.
- Checkboxes: the three under "## 3. Share a single aiohttp.ClientSession".

#### #4 — Env var startup validation
- Scope: `main.py` (lifespan/startup), `README.md` (mention required vars fail fast).
- Tasks: in the lifespan added by #3, validate `PROWLARR_URL`, `PROWLARR_API_KEY`, `TORBOX_API_KEY`; on missing, log a clear error and raise to abort startup. `TMDB_API_KEY` stays optional.
- Acceptance: `python3 -m pytest -q` passes; add a test that startup raises when required keys are unset.
- Checkboxes: the two under "## 4. Validate required env vars at startup".

#### #2 — `/healthz` + `/statsz`
- Scope: `main.py` (routes), `docker-compose.yml` (healthcheck), `README.md`.
- Tasks: `GET /healthz` → `{"status":"ok"}`; `GET /statsz` → JSON of the four cache `len()`s, indexer-listing age (`expires - now` of `_INDEXERS_CACHE['listing']`, or null), last-search latency (track a module-level timestamp/dict), Torbox hit/miss counters (add two module-level ints). Add `healthcheck:` to compose.
- Acceptance: `python3 -m pytest -q` passes; add tests hitting the two routes via FastAPI `TestClient`.
- Checkboxes: the three under "## 2. Add a /healthz + /statsz endpoint".

#### #9 — LRU caches
- Scope: `main.py` (the four `_SCRAPE_CACHE`/`_TMDB_TITLE_CACHE`/`_MAGNET_CACHE`/`_INDEXERS_CACHE` helpers), `tests/`.
- Tasks: convert each cache dict to `collections.OrderedDict`; `move_to_end` on get and on put; evict via `popitem(last=False)`. Preserve the existing `expires` TTL semantics (still skip expired entries; eviction is LRU, not TTL-ordered). Add a test that inserts `MAX+1` keys and asserts the LRU key was evicted (not a random one).
- Acceptance: `python3 -m pytest tests/test_prowlarr_per_indexer.py tests/test_torbox.py -q` passes + new LRU test passes.
- Checkboxes: the three under "## 9. Make caches truly LRU".

#### #5 — Narrow `except Exception`
- Scope: `main.py` (all ~35 broad catches), minimal test churn.
- Tasks: replace with specific exceptions where determinable (`ET.XMLSyntaxError` / `lxml.etree.XMLSyntaxError`, `ValueError`, `KeyError`, `struct.error`, `asyncio.TimeoutError`, `aiohttp.ClientError`); keep broad catches only where genuinely unknown (e.g. user-supplied XML), but add `logger.debug(..., exc_info=True)`. Do NOT change behavior — only logging/specificity. UDP scrape path is highest priority.
- Acceptance: `python3 -m pytest -q` passes (behavior unchanged).
- Checkboxes: the three under "## 5. Narrow broad except Exception blocks".

#### #6 — pytest-asyncio auto + dedupe fakes
- Scope: `pyproject.toml`, `tests/_fakes.py` (new), `tests/test_integration_prowlarr.py`, `tests/test_integration_prowlarr_minimal.py`.
- Tasks: add `[tool.pytest.ini_options] asyncio_mode = "auto"`; extract the duplicated `FakeSession`/`FakeCtx` into `tests/_fakes.py`; rewrite `test_integration_prowlarr.py` to import from `_fakes` (this removes the indentation-fragile duplication). Keep all assertions.
- Acceptance: `python3 -m pytest -q` passes; test count does not drop.
- Checkboxes: the three under "## 6. Configure pytest-asyncio auto mode + dedupe test fakes".

#### #10 — ruff + make lint
- Scope: `pyproject.toml`, `Makefile`, `main.py`, `tests/` (auto-format only).
- Tasks: add `[tool.ruff]` (line-length 120, select E/F/W/I/UP/B), `make lint` (`ruff check .`) and `make format` (`ruff format .` + `ruff check --fix .`). Run once; commit the formatted result. Do not fix logic — only style. Optionally add `[tool.mypy]` scoped to `main.py` with `ignore_missing_imports = true` (non-blocking).
- Acceptance: `make lint` exits 0; `python3 -m pytest -q` still passes after format.
- Checkboxes: the four under "## 10. Add linting/formatting".

#### #7 — Split into package (final)
- Scope: new `pachelarr/` package, `main.py`, `tests/`, `Dockerfile`, `AGENTS.md`, `pyproject.toml`.
- Tasks: create modules per the IMPROVEMENTS.md layout (config/tmdb/prowlarr/torznab/torbox/scrape/caches/app). Move logic 1:1 — no behavior changes. `main.py` becomes `from pachelarr.app import app` (and re-export any test-referenced symbols so `import main` still works). Update `Dockerfile` CMD/entrypoint if needed. Update `AGENTS.md` "Not a package" note.
- Acceptance: `uvicorn main:app` still imports; `python3 -m pytest -q` passes with 0 failures; `make lint` passes.
- Checkboxes: the four under "## 7. Split main.py into a package".

## Risks

- **#3 shared session + test fakes:** some tests construct `aiohttp.ClientSession()` or a fake; changing call sites to accept an injected session may break fakes. Mitigation: subagent must run the full suite; orchestrator verifies before commit.
- **#5 broad-catch narrowing:** risk of letting a previously-swallowed exception propagate and break a test. Mitigation: behavior must be byte-identical; the subagent keeps `except Exception` where a specific type isn't determinable and only adds logging.
- **#6 dedupe fakes:** highest risk of silently dropping assertions. Mitigation: subagent must report test count before/after; orchestrator rejects if count drops.
- **#7 package split:** import-cycle and `import main` compatibility risk. Mitigation: `main.py` re-exports everything tests touch; full suite + `uvicorn main:app` import check is the gate.
- **Sequential dependency:** if #3 or #4 stalls, downstream #2/#5/#6/#10/#7 are blocked. Mitigation: each subagent is scoped to fail fast and report; orchestrator re-dispatches with the failure trace.

## Validation (end of run)

1. `git log --oneline improvements/pachelarr-2026-08-06` shows ~10 commits, one per item.
2. `python3 -m pytest --collect-only -q` → 0 errors.
3. `python3 -m pytest -q` → all green.
4. `make lint` → exits 0 (exists after #10).
5. `uvicorn main:app` imports without error (after #7).
6. `IMPROVEMENTS.md` — all 10 items' checkboxes are `[x]`.
7. `docker compose build` + healthcheck passes (after #2's compose edit).

## Out of scope

- Actual `git push` or PR creation — user commits locally only.
- New features beyond the 10 listed improvements.
- Performance benchmarking (the perf gains from #3 are structural, not measured here).
- Upstream (`u/`) syncing.