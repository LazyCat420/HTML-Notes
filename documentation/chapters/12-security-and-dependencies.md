---
part: Governance
type: reference
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:requirements.txt, html-notes:package.json, html-notes:package-lock.json, html-notes:Dockerfile, html-notes:app/tooling/policy.py, html-notes:tests/test_local_tool_admission_security.py
---

# Security and dependencies

## Python pins (what the image ships)

`requirements.txt` is what the Docker image installs (`python:3.11-slim`), so the
pins — not the local `.venv` — decide what ships. The 2026-10-22 security refresh
(`69bed45`) took `pip-audit -r requirements.txt` from **40 advisories** to
**0**. On the old pins: `starlette` 0.36.3 (14, pulled in by `fastapi==0.110.0`),
`python-multipart` 0.0.9 (14), `jinja2` 3.1.3 (8), `pymongo` 4.10.1 (4).

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

`httpx`, `pytest` and `pytest-asyncio` were already newest. `starlette`, `certifi`
and `anyio` are pinned so the image build is reproducible and a future resolver
cannot slide back to a vulnerable release.

Refresh rules:

- Resolve-check under 3.11 and 3.12 (`pip install --dry-run`, 3.11 with
  `--python-version 3.11 --only-binary=:all:`), install into a clean 3.12 venv,
  then `pip-audit` that venv: must report **No known vulnerabilities found**.
- A dependency change needs a rebuild and redeploy for the image to pick up the
  new pins. The local `.venv` is not the shipped environment.
- The FastAPI 0.110→0.161 / Starlette 0.36→1.9 jump needed no app-code change (no
  deprecated `TemplateResponse`/`on_event`); one test read `.path` on
  `_IncludedRouter` wrappers and now checks `app.openapi()["paths"]`.

## Node packages (build tooling only)

`node_modules/` is dev/build-time only — Tailwind CLI and nothing shipped:

- `npm install --save-exact --before=<date> --ignore-scripts`, so every transitive
  package predates the 5-day rule and no lifecycle script runs.
- `npm audit` reports 0 vulnerabilities.
- The Docker `css` stage uses `npm ci --ignore-scripts` from the lockfile.

## Repository hygiene

- `.venv/` (2,931 files) had been committed since the initial commit despite the
  `.gitignore` entry; now untracked with `git rm -r --cached .venv`. Files stay on
  disk; the image builds its own venv from `requirements.txt`.
- `app/static/tailwind.css` is generated and not committed.
- No secrets in the repo or in documentation; no `.env` file is opened, printed, or
  committed. Runtime credentials live with the shared lazy-agent-service, which
  HTML-Notes never holds (ch.10 DEC-009).

## Boundaries and tool policy

- The only model surface is the shared runtime adapter (`app/adapters/runtime/`)
  with attribution headers; see ch.10 DEC-009.
- Local tool admission is guarded by `app/tooling/policy.py`; the guard tests are
  `tests/test_local_tool_admission_security.py`, `tests/test_agent_guardrails.py`
  and `tests/test_agent_loop_guards.py`. HTML-Notes does not execute arbitrary
  agent HTML.
- Live network calls (news, search, YouTube, music-player) are server-side and
  proxied; the client never talks to third-party services with server credentials.

## Shared-resource budget

Heavy tests and live checks run through `testrun` on `main`, never inside a
worktree. Browser/headless probes are closed on every exit path. The local vLLM
boxes are shared; a slow router is an environment fact (ch.09), not a bug.
