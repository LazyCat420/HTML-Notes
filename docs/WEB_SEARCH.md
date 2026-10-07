# Web search in html-notes

**Date:** 2026-10-06
**Repository:** `html-notes`
**Status:** Health no longer searches, and every search goes to the shared keyless search. html-notes sends nothing to DuckDuckGo (verified).

---

## Why this matters

Every machine in the house leaves through one public IP, 71.198.70.27; the NAS and the workstation both report it. DuckDuckGo, Startpage, Ecosia, Google and Mojeek are bot-blocking that IP. DuckDuckGo first refused the NAS on 2026-07-27. On 2026-10-06 a plain `curl` from the workstation got DuckDuckGo's "bots use DuckDuckGo too" challenge. Automated searches from any project on this network make the block worse for all of them.

A read-only audit on 2026-10-06 found html-notes to be the largest automated DuckDuckGo user in the workspace:

- `/health/app` ran a real search for "test" (`app/routes/health.py`). docker-compose calls it every 30 s (`docker-compose.yml` healthcheck), and the result was cached for 5 minutes: about 288 searches a day with nobody using the app.
- Every boot ran the same search once more (`_warn_if_research_is_down` in `app/main.py`).
- `_SEARCH_ENGINES` (`app/main.py`) is DuckDuckGo Lite first and scraper-service's DuckDuckGo collector second. The collector is switched off on the NAS (`DISABLE_DDG_SEARCH`) and answers with zero results, so in practice every search is DuckDuckGo Lite.

## Health without searching (2026-10-06)

- Every real search records its outcome in `LAST_SEARCH` (`app/services/search.py`): when it ran, whether any engine answered, and which engine served it. Real searches are asks, the `html_notes_web_search` tool and watches.
- `/health/app` reports that record under `search`, with `probe: "none"`, and makes no request. `ok` is `null` until the first real search after a boot.
- `/health/app?fresh=1` still runs one live probe, for when a person wants to know now.
- The boot check no longer searches. It still checks the MCP tool path and logs the result.

Removes about 288 automated DuckDuckGo requests a day and one per restart.

### How it was verified

- `tests/test_llm_seam.py::test_health_reports_search_without_searching` calls the health check six times and asserts no search ran. It then checks that health reports a real search's outcome, and that `fresh=1` makes exactly one live probe.
- `tests/test_search_backends.py::test_web_search_ex_records_each_outcome` checks that the record is written for a hit, for no hits with the engine reachable, and for every engine down.
- 41 of 42 tests in `test_llm_seam.py`, `test_search_backends.py`, `test_api.py` and `test_runtime_readiness.py` pass. The failure, `test_api.py::test_note_apis` (HTTP 401 instead of 200), fails the same way on unchanged `main` (`db797e5`). It is not caused by this change and is still open.

## Search through the shared keyless search (2026-10-06)

- `_SEARCH_ENGINES` (`app/main.py`) is one engine, `shared-web`. It is `_search_shared_web` in `app/services/search.py`, which calls lazy-agent-service's `POST /execute/web_search`. That is Exa's free keyless index, with one cache and one rate limit for every project on the network (lazy-agent-service `documentation/chapters/04-shared-web-search.md`).
- `_search_duckduckgo` (DuckDuckGo Lite) and `_search_scraper_ddg` (scraper-service's DuckDuckGo collector) are deleted.
- In `news_search`, the scraper-service DuckDuckGo tier (`_scraper_service_news`) is deleted. The remaining order is:
  1. the shared `news_search` (keyed news APIs);
  2. Google News RSS;
  3. GDELT;
  4. the shared web search.
- When the shared search answers `rate_limited`, `busy` or `error`, or cannot be reached, `_search_shared_web` raises. `web_search_ex` therefore reports an outage, and the `html_notes_web_search` tool tells the model not to retry. It does not say "no results".
- Everything that searched through `web_search` now goes through the shared search: research asks, the agent tool, widget builders and watches. The shared search caches identical queries for 30 minutes, so a watch that re-runs every 5 minutes asks Exa at most twice an hour.

### How it was verified

- New tests in `tests/test_search_backends.py`:
  - the engine list is exactly `["shared-web"]` and names no DuckDuckGo engine;
  - results are normalized and filtered;
  - an empty `ok` is an empty list;
  - `rate_limited`, `busy`, `error` and a dead service all raise.
- `tests/test_news_general_path.py` now expects the subject-news fallback order without the DuckDuckGo tier. Its source guard reads `search.py` through `news_search`, because `_scraper_service_news` is gone.
- Deleted, because they only covered removed code: `test_free_engines_are_primary` (it asserted DuckDuckGo came first), `test_scraper_ddg_normalizes_and_filters_results` and `test_scraper_ddg_network_failure_returns_empty`.
- The full suite has 1,295 passed and 15 failed. The same 15 fail on unchanged `main` (`737f77b`), compared failure by failure, so none is caused by this change. They are still open:

  - `tests/test_agent_guardrails.py::test_compare_config_normalizes_and_aligns`
  - `tests/test_api.py::test_note_apis`
  - `tests/test_context_bus.py::test_compare_these_resolves_the_two_most_recent_tickers`
  - `tests/test_dynamic_routing.py::test_dynamic_question_routes_to_answer_card`
  - `tests/test_notes_vault.py::test_load_missing_note_404s`
  - `tests/test_notes_vault.py::test_note_path_stays_in_vault`
  - `tests/test_notes_vault.py::test_save_list_load_and_upsert_preserves_created`
  - `tests/test_notes_vault.py::test_save_rejects_traversal_and_writes_inside_vault`
  - `tests/test_themes.py::test_system_prompt_teaches_the_settings_route`
  - `tests/test_trending_stocks.py::test_build_trending_compare_none_when_feeds_down`
  - `tests/test_trending_stocks.py::test_build_trending_compare_uses_feed_symbols`
  - `tests/test_trending_stocks.py::test_build_trending_degrades_when_index_filter_empties_pool`
  - `tests/test_trending_stocks.py::test_build_trending_scopes_to_index_and_tags_provenance`
  - `tests/test_trending_stocks.py::test_router_stock_spec_reroutes_discovery_ask`
  - `tests/test_watches.py::test_dao_round_trip_and_guards`
