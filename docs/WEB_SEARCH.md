# Web search in html-notes

**Date:** 2026-10-06
**Repository:** `html-notes`
**Status:** Health no longer searches (verified). Search engines still DuckDuckGo; moving off it is the next step.

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

## Next

`_SEARCH_ENGINES` still starts with DuckDuckGo. The plan is one shared keyless search in lazy-agent-service that every project calls: Exa's free keyless endpoint plus Bing News RSS, with a cache and a single rate limit for the whole network. html-notes will then send its searches there instead of to DuckDuckGo.
