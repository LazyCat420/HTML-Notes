# Handoff — 2026-07-20 (frontend: restrained sci-fi HUD theme)

**Deployed:** HTML-Notes `5ba6cf8` → synology `:8035`. Health 200; live `/`
serves `hud-theme.css?v=906b6d82f9` + the Share Tech Mono font; `/static/hud-theme.css`
= 200 (18.4 KB). Live empty-state + local widget renders screenshot-verified.
**Scope:** CSS-only overlay. No JS/HTML structure or Python logic changed except
one additive line in `app/main.py` (asset-fingerprint list — landed via `05d9454`).

## What shipped

A futuristic-HUD skin, added as a **final override layer** rather than a rewrite:
`app/static/hud-theme.css`, linked in `index.html` **after** `index.css` so it wins
over the tail "CANVAS REDESIGN" calm-neutral block — the same layering trick that
block used on the neon rules above it. The two prior themes in `index.css` are
untouched and still present underneath.

- **Backdrop:** faint masked grid + very soft STATIC scanlines (no animation).
- **Panels:** clip-path "cut" corners (top-right/bottom-left notch), thin cyan
  hairline + soft glow, drawn L-brackets on the other two corners via a single
  `::before` (leaves `.is-updating::after` sweep from index.css intact).
- **Headers/labels:** Share Tech Mono, uppercase, wide-tracked; a quiet status
  pip that slow-breathes (`hud-breathe`, 3.4s) — never a hard blink.
- **Readouts:** clock / `.metric-value` / `.font-mono` → mono + tabular + soft cyan.
- **Command strip, turn-status bars, activity log:** re-cut to match; status-bar
  shimmer slowed (2.6s) so long turns don't strobe.
- **Music player:** purple→cyan retint by targeting its Tailwind classes
  (`from-fuchsia-500`, `from-purple-400`, `bg-purple-300`, …) — its Alpine twin
  in `index.js` is NOT touched, so no template-drift risk with the music commit.

Per the brief: glow/colors kept soft, exactly one gentle looping pulse, and every
loop is killed under `prefers-reduced-motion`. Cuts/brackets shrink < 520px.

## Gotchas / notes for next session

- **The app did NOT look like the neon HUD on GitHub.** `index.css` stacks THREE
  themes now: neon (top) → calm-neutral "CANVAS REDESIGN" (tail, was live) → this
  HUD overlay (separate file, now on top). Edit the layer that actually wins.
- **`main.py` fingerprint edit was swept into `05d9454`** (a parallel session's
  `git add`), not my commit — it's in HEAD either way. `_CACHE_BUSTED_ASSETS` now
  lists `hud-theme.css`; bumping the file's mtime re-versions it automatically, so
  the hardcoded `?v=1.0` in index.html is only a fallback.
- **The Phase-1 "layout column bug" in the original plan does not exist** — the
  activity log is `position:fixed; display:none`, never a grid sibling. No fix made.
- Deeper plan items needing JS (widget size-tiers, per-widget action buttons, a
  60px single-row music strip) were left out to keep this a safe CSS-only pass and
  avoid colliding with the concurrent music-widget work. Resize/move handles for
  widgets already exist (`.widget-resize-handle` / `.widget-move-handle`).

---

# Handoff — 2026-07-20 (music widget → music-player service, real queue)

**Deployed:** HTML-Notes `05d9454` → synology `:8035` (a parallel session then
deployed `5ba6cf8`, the HUD theme, which includes this commit — served
widgets.js verified byte-identical after both).
**Tests:** 340 pytest + 16 node, all green. Live E2E 7/7.

## What shipped

"Play jungle music" fetched nothing from YouTube and "jungle house music"
played the local library. Root cause: the mini music player decided
genre-vs-artist with a hardcoded client-side `KNOWN_MUSIC_GENRES` set —
"jungle" wasn't on it, so both routed as artist search and fell through to
local-library matching. The music-player service (`:8002`) already handles
arbitrary genres (LLM + MusicBrainz artist discovery, strict YouTube
verification, 7-day cache); the widget just never asked it.

The widget is now a thin client over that service:

- **SSE mix consumption** (`/api/youtube/mix/<term>/stream`): playback starts
  on the first artist's tracks instead of racing a timeout against the ~18s
  cold pipeline. `type=artist` is served by the same endpoint (single
  tracks+done pair) — one code path for both kinds. **Zero music-player
  changes were needed.**
- **`kind` classified at routing time** (app/main.py): fast-path "X
  music/radio" phrasing defaults `kind=genre`; the LLM router catalog teaches
  `modifiers: {"kind": genre|artist}` for named acts; builder sanitizes.
- **Failover ladder** (widgets.js `failover()`): genre miss → artist mix
  (one-shot) → plain search → honest "Couldn't find X" error. The local
  library survives ONLY for bare no-query widgets; a named genre never dumps
  it (the "smooth jazz played Burzum" class of bug). The ~7MB `/api/tracks`
  fetch is gone from the mix path.
- **Real queue**: visible upcoming list (queue_music button; click-to-jump,
  hover-remove), dedupe by video id across SSE batches, background refill via
  `refresh=true` when <5 tracks remain (90s floor — the mix endpoint is
  rate-limited 10/min service-side).

## Where the code is

- `app/static/js/widgets.js` — `musicPlayerWidget` rewritten. Takes an options
  object `{genre, kind, autoplay, base}` with a legacy-positional shim.
- `app/widgets/factory.py` `render_mini_music_player` — object x-data, queue
  panel, streamStatus subtitle, height binds to `showQueue` (280↔420px).
- `app/main.py` — fast-path spawn + router catalog + builder pass `kind`.
- `app/static/index.js` ~1906 — self-heal template synced (it is a
  hand-maintained twin of the factory template; both carry cross-ref comments).

## Gotchas found along the way

- **EventSource auto-reconnects after a server close.** An unclosed stream
  re-runs the entire discovery pipeline in a loop against the 10/min rate
  limit. `closeStream()` is mandatory on `done`, `error`, AND `destroy()` —
  the .mjs suite pins all three. `destroy()` also covers the media-singleton
  swap (server replaces the div → Alpine destroy fires).
- **"jungle house" legitimately has no discoverable artists** — the genre
  pipeline SSEs an `error` event. The failover then gets 30+ real
  jungle-house tracks from the artist mix. Verified end-to-end in the browser:
  genre stream → error → artist stream → playing, zero library requests.
- The old test suite pinned the deleted heuristics and was green while the
  feature was broken — the rewrite pins the service contract instead
  (`tests/test_music_genre_routing.mjs`).
- `_content_sig` hashes the whole config, so `kind` participates for free: a
  genre→artist correction re-renders, a same-config re-ask leaves the playing
  widget alone (pinned in `tests/test_widgets.py`).
- Deploy note: the working tree had another session's in-progress HUD work, so
  this was deployed from a pinned sibling worktree
  (`git worktree add ../html-notes-deploy <sha>` → deploy.sh → remove), per
  the trading-service recipe. `../deploy-kit` and `../lazycat-sdk` resolve
  because the worktree sits in the sun root.

## Verification

- `node --test tests/` — 16 pass (13 in the rewritten music suite: SSE
  contract, close-on-done/error/destroy, /api/tracks absence, failover order,
  enqueue dedupe, removeAt index math, refill gating, routing kind).
- `pytest tests/` — 340 pass (new: x-data carries kind/base/queue UI; kind in
  the content signature).
- `scripts/music_widget_check.py` (live, needs NAS stack): "play jungle music"
  → one genre SSE request, zero `/api/tracks`, audio from
  `/api/youtube/stream/`, queue rows render, row-click jumps, no orphaned
  EventSource. 7/7.
- Scratch live check for "jungle house music": genre→artist failover chain
  observed in network log, 30 tracks queued, playing, no library.

## Not done / watchlist

- The queue is per-widget, in-memory — no persistence across widget swaps
  (matches the music-player frontend's own localStorage-only design; theirs
  isn't shared either).
- `POST /api/radio/preference` (thumbs up/down feeding the service's artist
  weighting) is unused by the widget — natural next step if mix quality needs
  user steering.
- If music-player's `AUTH_ENABLED` ever flips on, both audio and SSE need the
  stream-ticket flow; browser-direct `:8002` calls would need revisiting.
