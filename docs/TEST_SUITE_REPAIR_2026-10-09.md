# Test Suite Repair: 15 → 0 (2026-10-09)

The suite had 15 persistent failures. Diagnosis split them into three causes; all
are fixed and the full run is green (1317 passed).

## 1. Refactor-orphaned tests (12)

`app/main.py` was split into `app/routes/*.py`, `app/config_builders.py` and
`app/services/`, each starting with the
`sys.modules[__name__].__dict__.update(main.__dict__)` snapshot. Symbols stay
reachable as `main.<name>`, but **patching `app.main` after import no longer
affects the module whose globals a moved function actually resolves against** —
so mocks silently didn't apply and tests hit live feeds (trending tests got
different stocks each run: `MRNA` vs `AMC`).

- `test_trending_stocks.py` (5) + `test_agent_guardrails.py::compare_config`:
  converted `monkeypatch.setattr(m, …)` to the `patch_server` fixture from
  `tests/conftest.py`, which rebinds the name in every `app.*` namespace.
- `test_notes_vault.py` (4): handlers moved to `app/routes/notes.py`
  (`api_notes_save/list/load`); vault override now lands in all three
  namespaces that snapshot main (`main`, `routes.notes`, `utils`).
- `test_themes.py` (1): the system-prompt text moved to `app/routes/message.py`;
  the source-text guard reads it there now.
- `test_api.py::test_note_apis` (1): `/notes/create` requires `session_id`
  since the router move (401 otherwise) — test sends one and pins the 401.

## 2. Product bug: research lane swallowed compare-anaphora (1)

`classify_research_intent("compare these two")` matches `_COMPARISON_RE` on the
bare word "compare", so the research-protocol pre-router
(`app/routes/message.py`) claimed the ask with zero entities before the context
bus could rewrite it to the two most recent tickers. Fix: the research gate now
skips `COMPARE_THESE_RE`-matching asks, which belong to the anaphora branch.

## 3. Shared-DB pollution (2)

- `test_database.py` teardown **dropped** `chat_sessions`/`chat_messages`/notes
  tables from the shared DB — every later module 500'd with
  `no such table: chat_sessions`. Teardown now deletes its own rows.
- `test_watches.py`: `due_watches()` is global by design; other watch tests'
  rows leaked into the "exactly one due row" assertion. `_seed()` clears the
  watches table wholesale (the file is a test fixture DB).

## Verification

`testrun -- pytest tests -q` → **1317 passed, 0 failed** (was 15 failed).
