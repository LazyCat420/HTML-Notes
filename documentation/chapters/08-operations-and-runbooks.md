---
part: Operations
type: reference
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:Dockerfile, html-notes:docker-compose.yml, html-notes:deploy.sh, html-notes:package.json, html-notes:scripts/youtube_crt_check.py, html-notes:scripts/news_check.py, html-notes:app/routes/health.py
---

# Operations and runbooks

## Local development

```bash
npm run build:css                       # compile app/static/tailwind.css
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8035 --reload
# or
npm run dev                             # build:css + uvicorn --reload
npm start                               # build:css + uvicorn (no reload)
```

The app listens on `8035`. The static canvas is served from `app/static/`; the
SQLite file is created on first boot.

## Build and deploy

- `Dockerfile` builds on `python:3.11-slim` and installs `requirements.txt`; the
  `css` stage compiles Tailwind with `npm ci --ignore-scripts`.
- `docker-compose.yml` runs the app container; `deploy.sh` is the deploy entry
  point (`npm run deploy`).
- A dependency change needs a rebuild and redeploy for the image to pick up the new
  pins. The local `.venv` is not the shipped environment.
- `.venv/` is untracked (it was committed since the initial commit; removed with
  `git rm -r --cached .venv`). The files stay on disk; the image builds its own venv.

## Health and live checks

| Check | Command | What it proves |
|---|---|---|
| App health | `GET /health/app` | The app is up and reports its agent dependency status. |
| Agent health | `GET /models` (through `app/adapters/runtime/`) | The shared runtime answers, with the same attribution headers as `/agent`. |
| News pipeline | `python scripts/news_check.py` | The news card returns vetted stories and summaries. |
| Music widget | `python scripts/music_widget_check.py`, `music_widget_resize_check.py` | Playback, history and resize behaviour against the live app. |
| Music handoff | `python scripts/music_handoff_check.py` | The canvas player hands off to music-player. |
| YouTube CRT | `python scripts/youtube_crt_check.py` | Aspect, tube fit, tune, static, knob, drag and reset against the deployed app. |
| CRT chat flow | `python scripts/youtube_crt_flow_check.py` | A video ask produces the CRT widget; a second ask changes channel. |
| Routing eval | `python scripts/routing_eval.py --mode system` | The real `/session/message` chain against the labelled eval set. |
| Golden routing | `python scripts/golden_routing_live.py` | The live routing answers match the golden set. |

Live checks are run against the deployed app; they are not part of the unit test
suite and they can fail for environment reasons (box load, network). Record the
result with its timestamp.

## Recovery

| Symptom | First check | Action |
|---|---|---|
| Canvas empty after a restart | `notes.db` exists and the note row has a canvas | Restore the database backup; do not recreate notes. |
| Widget renders unstyled | `app/static/tailwind.css` exists and is the last stylesheet | Run `npm run build:css`; check the Docker `css` stage. |
| Music widget plays silence after a swap | `window.__hnMusicCarry` and the new widget's `adoptCarry` | The carry expires after 2 s; a dismiss stops audio by design. |
| Music ask plays the wrong genre | `data-hn-config` on the widget, then the router debug frame | The self-heal must rebuild from config; a fallback plays genre-first until the router refines. |
| News card has one story | The relevance gate and `_attach_article_bodies` logs | A slow site loses only itself; the editor must not drop a gated story. |
| CRT shows NO SIGNAL | The embed error code and the candidate list | The widget walks candidates, fetches once, then wraps. |
| Agent probes logged as anonymous | The `x-project` / `x-username` headers on `/health/app` and `/models` | Send the same attribution headers as `/agent`. |

## Capacity and shared resources

HTML-Notes shares the workstation with other services. Heavy tests and live checks
run through `testrun` on `main`, never inside a worktree; a browser or headless
probe is closed on every exit path. The local vLLM boxes are shared with the rest of
the workspace, so a slow router is an environment fact, not a code bug.
