---
part: Governance
type: decisions
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:app/routes/message.py, html-notes:app/llm.py, html-notes:app/main.py, html-notes:app/config.py, html-notes:app/widgets/factory.py, html-notes:app/static/index.js
---

# Decisions

Settled choices. Changing one means editing this page with the evidence that
reversed it, not silently diverging.

## DEC-001 — Only the music lane goes through the LLM router

The router turned `"cnn live news"` / `"bloomberg live news"` into a news card
(11/17 on video asks) — the exact failure the deterministic video/live override
exists to prevent. News (10/12) already runs through `classify_news_ask`
(`app/main.py`). Video and news keep their deterministic lanes until the router
beats them on `tests/routing_eval.json`. Consequence: the router catalog only has
to be right about music and chat; music asks are gated by the fast lane in
`app/routes/message.py` before any model call.

## DEC-002 — Show the fallback player now, refine it in place

Blocking variants either added 4 s for nothing or gained nothing, because the
shared local boxes (Jetson `nemotron35`, Gold Spark DeepSeek) are saturated by
other agents. The fallback player starts immediately with a subject taken from the
message; a router answer within `MUSIC_ROUTER_REFINE_S` (25 s) rebuilds the same
widget via the `router-refine` debug path. Consequence: users hear the wrong thing
briefly on the ~8% the router gets wrong, rather than hearing nothing for 4 s.

## DEC-003 — No live MusicBrainz artist lookup

Measured 0.6–9.3 s per call and noisy (`smooth jazz` → artist "Smooth Jazz Band",
score 100). `resolve_music_kind` answers `"genre"` only for an exact match against
the checked-in list `app/domain/musicbrainz_genres.txt` (CC0, 2,220 names);
anything else is `""` (genre-first mix). The "last word is a genre" heuristic was
dropped because "instrumental" is a listed genre.

## DEC-004 — Router output carries `modifiers.kind` only; no `subject_type`, no `confidence`

`modifiers.kind` is the only consumer, and extra output tokens cost latency on
every route. The catalog uses placeholder examples only — real example names get
copied into the query ("heavy metal music" → query "buckethead"). Grounding is
server-side: `music_query_grounded` (`app/main.py`) drops a router subject that
shares no word stem with the message.

## DEC-005 — Tailwind is compiled at build time; nothing Tailwind runs in the browser

The CDN runtime (~3 MB, production warning on every load) is replaced by
`app/static/tailwind.css` (~47 KB) built by the Tailwind CLI (`tailwindcss@3.4.17`)
in the Docker `css` stage and by `npm run build:css` locally. It is the last
stylesheet in `<head>` so the cascade is unchanged (verified by 0-pixel screenshot
diff). Dynamic classes are covered by `tailwind.safelist.json` — 771 tokens mined
from live canvases in `notes.db` by `scripts/mine_tailwind_safelist.py` — and
enforced by `tests/test_tailwind_build.py`.

## DEC-006 — The server owns widget config; the client rebuilds only from `data-hn-config`

The factory stamps strict JSON `data-hn-config` next to the Alpine state.
Self-heal (`musicWidgetNeedsRebuild` / `recoverMusicWidgetConfig` in
`app/static/index.js`) rebuilds only when the Alpine wiring is missing or in the
old positional form, and rebuilds from that config, never from a guessed default.
The 2026-11-03 "buckethead plays Duke Ellington" bug was a self-heal inventing
`genre = 'jazz'`; the clock heal once forced `'local'` over the server's tz. A
widget with no config is left as-is and reported.

## DEC-007 — Device-style widgets are boxless and self-resizing

The Walkman music card and the Sony Trinitron CRT card are rendered objects, not
panels: the `BOXLESS WIDGETS` rule in `app/static/index.css` clears frame, clip,
hover lift, brackets and the light-sweep for them; `WidgetResizer` skips them,
their height is derived from width (`round(width / 1.8182)` for the CRT), and the
drag target is the device's own bezel. A stationary click (≤ 3 px) is not a
resize; only `{cols}` is persisted. Consequence: the HUD theme must never gain a
global rule these two cannot opt out of with `!important`.

## DEC-008 — History is posted in the music-player's wire format, not ours

`buildPlayEvent` (`app/static/js/widgets.js`) posts the exact `PlayEvent` shape
music-player validates (`id, track_id, timestamp, duration, completion_rate,
source, context`), with `track_id = youtube://<id>` as the music-player web app
records it, and `mapServerHistory` reads the bare list `/history/recent` returns.
The 422 on every track (2026-11-03) came from inventing our own body.

## DEC-009 — The shared runtime is the only model surface

All model calls go through `app/adapters/runtime/` (`/agent`, `/models`,
`/health`) with the same attribution headers (`x-project` / `x-username`) as
`/agent`; probes without them logged as anonymous. HTML-Notes never executes
arbitrary agent HTML and never holds model credentials itself.
