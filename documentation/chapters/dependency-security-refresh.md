---
part: Ops and quality
status: shipped
updated: 2026-10-22
review-by: 2026-11-22
type: record
---

# Dependency security refresh (Python pins)

**2026-10-22.** `requirements.txt` is what the Docker image installs
(`python:3.11-slim`), so the pins, not the local `.venv`, decide what ships.
`pip-audit -r requirements.txt` on the old pins reported 40 advisories in four
packages: `starlette` 0.36.3 (14, pulled in by `fastapi==0.110.0`),
`python-multipart` 0.0.9 (14), `jinja2` 3.1.3 (8) and `pymongo` 4.10.1 (4).

## What changed

| Package | From | To |
|---|---|---|
| fastapi | 0.110.0 | 0.161.0 |
| starlette (now pinned explicitly) | 0.36.3 (transitive) | 1.9.0 |
| uvicorn | 0.28.0 | 0.54.0 |
| pydantic | 2.6.4 | 2.14.1 |
| jinja2 | 3.1.3 | 3.1.6 |
| python-multipart | 0.0.9 | 0.0.32 |
| pymongo | 4.10.1 | 4.19.0 |
| beautifulsoup4 | 4.12.3 | 4.15.0 |
| ddgs | 9.14.4 | 9.16.0 |
| respx | 0.22.0 | 0.23.1 |
| certifi, anyio (now pinned explicitly) | transitive | 2026.10.19, 4.15.1 |

`httpx`, `pytest` and `pytest-asyncio` were already at their newest release.
`starlette`, `certifi` and `anyio` are pinned so the image build is
reproducible and a future resolver cannot slide back to a vulnerable release.

## Verification

- The set resolves under Python 3.11 and 3.12 (`pip install --dry-run`, 3.11
  with `--python-version 3.11 --only-binary=:all:`), and installs into a clean
  3.12 venv.
- `pip-audit` on that venv: **No known vulnerabilities found** (was 40).
- Test suite (`tests/`, serial): baseline on the old pins was 3 failed / 1328
  passed. The three failures are in `tests/test_app_hub.py` (portal registry
  URL expectations) and are unrelated to dependencies.
- One test needed a change for the new FastAPI:
  `tests/test_watches.py::test_events_route_streams_pushed_lines` iterated
  `app.routes` and read `.path`, but FastAPI 0.16x wraps `include_router()`
  routes in an `_IncludedRouter` that has no `.path`. The test now checks
  `app.openapi()["paths"]`. No application code changed.

The app imports no deprecated Starlette API (`TemplateResponse`, `on_event`), so
the FastAPI 0.110 to 0.161 and Starlette 0.36 to 1.9 jump needed no code change.

## Resolved tickets

| Ticket | Root cause | Commit |
|---|---|---|
| Vulnerable Python pins (starlette, python-multipart, jinja2, pymongo) | old pins; starlette held back by `fastapi==0.110.0` | see `git log -- requirements.txt` |

A NAS redeploy is needed for the new pins to reach the container.

## Follow-up: .venv was tracked in git

`.venv/` (2,931 files) had been committed since the initial commit despite the `.gitignore` entry, so any venv refresh showed up as ~650 modified files. It is now untracked with `git rm -r --cached .venv`; the files stay on disk and the Docker image is unaffected (it builds its own venv from `requirements.txt`).
