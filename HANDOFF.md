# Handoff — 2026-07-20 (multi-ticker compare + x-for round-trip + tie-break)

**Deployed:** `b585494` → synology `:8035`. 379 pytest + 16 node green.
Live-verified in one run: "NVDA vs SPY vs TSM" → ONE chart
(`stock-compare-*`, "— 6mo % change", 3 datasets, legend), then two
stock-card turns with canvas round-trips → ZERO Alpine errors.

## 1. Multi-ticker comparison
"NVDA vs SPY vs TSM" used to fan into one stock_card per ticker (router
prose said "never open a second stock" — prose can't outvote sampling) and
each failed. Now: `build_stock_compare_config` (app/main.py, after
stock_snapshot) fetches N snapshots concurrently, drops failures, aligns on
the common tail, normalizes each series to % change from range start, and
renders through the EXISTING chart widget — Chart.js draws N datasets +
legend natively, zero client work. Cap 8 tickers.

Wired in three places:
- Router stock builder: compare phrasing (`_COMPARE_SPLIT_RE`) → explicit
  uppercase tickers, else per-segment `_resolve_ticker` (names like
  "nvidia vs taiwan semi").
- Route cleaner: N stock specs collapse into one joined "A vs B" query.
- Agent path: `widget_type='chart'` + `config.compare_symbols=[...]` →
  injector rebuilds server-side; SYSTEM_PROMPT teaches the route ("To add
  a ticker, call again with the SAME widget_id and the full symbol list" —
  follow-ups like "add AMD" work through the normal update path).

GOTCHA: `coerce_widget_type` force-converts chart→stock_card when a cached
symbol appears in the title/id — which is EXACTLY what a compare chart's
title looks like. It now skips configs with `compare_symbols` or >1
dataset. Don't remove that guard.

## 2. The Alpine "r is not defined" storm (stock card)
`getCleanedCanvasHtml` serialized Alpine-EXPANDED `<template x-for>` output
(range buttons, metric rows); the server adopted that HTML as canonical;
the next `Alpine.initTree` evaluated loop-scoped bindings outside any
x-for scope — hundreds of console errors + duplicated nodes on
re-expansion. Fix in index.js: strip every `template[x-for]`'s generated
siblings before persisting — walk siblings until the first with `x-show` /
`x-for` / `.close-widget-btn` (audited invariant: every static sibling
after an x-for in factory.py carries one of those). Covers the stock card
and every future x-for widget; the older youtube/checklist hand-rolled
strips remain for their extra rules.

## 3. Jimothy double-box (ambiguous-tie targeting)
"tell me more about jimothy" scored 1.00 against TWO cards (his own AND a
reddit-lawsuit card whose gist mentioned him); first-in-DOM-order won and
the lawsuit card got overwritten with meme-coin content. `_followup_target_id`
now collects contenders within 0.1 of the best score: the recency focus
wins ties (the thread the user is in), else the most recent contender.

---

# Handoff — 2026-07-20 (stacking in-place updates)

**Deployed:** `5497273` → synology `:8035`. 372 pytest + 16 node green.

In-place data_card updates no longer hard-replace: the new answer renders on
top, previous content survives under an "**Earlier**" rule, and once the card
passes `_STACK_WORD_BUDGET` (800 words — middle of the user's requested
500-1000 range) the oldest words roll off the bottom. Sources/items
accumulate too (dedupe by url, newest first, cap 8). Implementation:
`_stack_data_card_update` + `_session_widget_configs` (per-session in-memory
config store, same lifetime as the turn ledger), wired into the agent
`canvas_add_widget` path just before render.

Guards: data_cards only (stock/map/clock are stateful displays → always
replace); only genuine in-place updates (id already on canvas); substring
check so a model that rewrote WITH history doesn't get it duplicated back;
no stub-stacking when the new answer alone fills the budget.

Live-verified: three costco follow-ups grew one card 35 → 223 → 545 words
with the Earlier section visible; `[WIDGET STACK]` log line shows the merge.

Note: the router/fast-lane path does NOT stack (agent path only) — if a
fast-lane build updates the same card id, the remembered config refreshes
but no merge happens. Extend there if it ever matters.

---

# Handoff — 2026-07-20 (anaphora: "tell me more about Miku" = the restaurant)

**Deployed:** `3d9ce6e` → synology `:8035`. 361 pytest + 16 node green. The
exact live scenario (sushi card → clones video → "tell me more about Miku")
re-driven twice; final run resolves Miku to the Vancouver restaurant, updates
the sushi card in place, video untouched.

## The failure
"Tell me more about Miku" right after a sushi card listing "Miku, Tojo,
Shizen" produced a Hatsune Miku YouTube video in the video widget. The model's
world-knowledge prior won because the session context never reached it.

## The fix — three layers (each exposed by re-driving live)
1. **Ledger gist keeps distinctive names** (`_widget_detail`): it kept the
   first 160 chars of the answer — which truncated exactly before "…include
   Miku, Tojo, and Shizen". Now: 150-char prefix + up to 8 proper-noun-ish
   tokens from the REST of the text, budget 200 (and `record_turn`'s clip
   raised to match — 160 there silently re-amputated the names).
2. **Body-scan targeting tier** (`_followup_target_id`): the live sushi answer
   was ~2000 chars dense with names, so the 8-name cap filled before "Miku" —
   gists can't hold every entity. New tier between gist scoring and the
   deictic fallback: scan every widget's full rendered text on the canvas,
   require FULL subject-token coverage, accept a UNIQUE hit only (two body
   matches = ambiguous = don't guess).
3. **Anchored directive + prompt rule**: the follow-up directive/rewrite now
   include "currently showing: <title — gist — …±window around the referenced
   name…>" via `_widget_showing(session, wid, message)` — the model sees
   "…options include Miku, Tojo…" next to the ask. SYSTEM_PROMPT gained
   "NAMES RESOLVE AGAINST THE CONVERSATION FIRST" (canvas meaning beats the
   famous meaning).

Verified decision trail in container logs:
`[WIDGET TARGET] follow-up subject found in the BODY of #vancouver-sushi-trip
— beats recency #video-...` → agent searched "Miku Vancouver restaurant".

## Answer to "do we have a context-around-the-word system?"
We do now, at three ranges: ledger gists (cheap, per-turn), full-body scan
(exact, targeting only), and the quoted ±window in the directive (what the
model actually reads). All session-scoped — nothing persists past the
conversation, per the current scope.

## Tests
SEAM E in tests/test_followup_targeting.py (gist keeps names; body scan;
unique-hit rule; anchor window; record_turn budget). Anchor/prompt pins in
tests/test_agent_guardrails.py.

## Typo tolerance (follow-up commit `c6ff64f`)
All three matching ranges are now fuzzy via bounded Levenshtein
(`_fuzzy_hit`), scaled by token length: <4 exact-only, 4-7 distance 1
("mikku"→"miku"), 8+ distance 2 ("birkenstok"→"birkenstock"). Bounds exist
because loose matching IS the historical bug class — substrings and
half-word rewrites stay dead ("john"≠"johnny", "cost"≠"costco",
"jass"≠"jazz"), each pinned by a negative test in SEAM F. Live-verified:
"tell me more about Mikku" scored 1.00 against the sushi card and updated
it in place.

## Watchlist
- `_widget_showing` body parse runs twice per follow-up turn (directive +
  rewrite) — cheap, but could be memoized if canvases get huge.

---

# Handoff — 2026-07-20 (agent guardrails: image trust + follow-up targeting)

**Deployed:** `6aa0414` → synology `:8035`. Tests: 352 pytest + 16 node, green.
Both reported failures re-driven live end-to-end and confirmed fixed.

## The three live failures and their fixes

**1. "Compare birkenstock shoes to other shoes" → pasta captioned as a
Birkenstock sandal.** The image injector's guard was
`not config.get("images")` — a model-supplied images ARRAY bypassed
build_image_config, the vision gate, and URL verification entirely. Both URLs
loaded fine, so liveness checks can't catch a wrong-content pair: the model
paired its own captions with unrelated remembered URLs. Fix (main.py, image
injector branch): fires on TYPE alone now. Re-sources via `build_image_config`
(captions become the search query when nothing else names the subject); model
URLs survive only as a last resort, individually `_image_url_loads`-verified
AND `filter_images_by_relevance`-vision-gated (note its signature:
`(subject, negatives, items)` with items keyed `'image'`, not `'url'`).
Prompt-side: the image routing line now tells the agent it has NO image tool
and must NEVER write url/images entries; new COMPARE rule routes comparisons
to a data_card table first, images only as an explicit add-on.
Live re-test: agent emitted `config={'image_query': ...}` only — no URLs —
and the widget rendered one real, loading, on-subject image.

**2. "Tell me more about the deals at costco" (right after a Birkenstock card)
edited the SANDALS widget.** The follow-up directive/rewrite hard-targeted
`focus_id` — pure recency — whenever the (loose) refinement regex fired,
pre-empting the topical scorer. Fixed in THREE layers, each caught by
re-driving the scenario live after the previous fix:
- `_followup_target_id` (new, main.py ~2680): scores the message against
  every canvas widget (title + ledger gist, across types); a ≥0.5 winner
  beats recency; subject-free deictic asks keep recency. Resolved ONCE per
  turn so the directive and the message rewrite can never disagree.
- Fresh-subject guard, both in `_followup_target_id` AND in
  `find_reuse_target`'s deictic fallback: ≥2 subject tokens matching nothing
  on canvas = new topic = no forced reuse. Without the second one,
  "find me MORE info on birkenstock arizona" (trips `more\b`) got retargeted
  by the RESOLVER even after the directive stood down — the model honestly
  asked for a new id and `_resolve_agent_widget_id` overrode it.
- `_SUBJECT_STOP` additions: "happened/next/info/…" (deictic narrative) and
  "anything/related/…" (vague scope). "anything hardware related" diluted the
  overlap to 0.4 and missed the 0.5 threshold purely on filler.
Live re-test (scratch `target_check.py`): costco card → birkenstock gets its
OWN card → the costco follow-up updates the COSTCO card in place, sandals
untouched.

**3. Music widget console error** (`null.currentTime`): destroy() nulls
`this.audio` but queued events still fire; `src=''` in destroy itself fires a
spurious error event. Handlers now guard.

## Testing pattern worth keeping
The unit suite was green after fix #1 of bug 2 — only re-driving the real
3-turn scenario in a browser exposed layers 2 and 3. For targeting changes,
run the live scenario (`scripts/`-style playwright, 3 real agent turns),
then check `[AGENT TURN]` / `[WIDGET TARGET]` container logs for the
decision trail. New tests: SEAM D in tests/test_followup_targeting.py,
tests/test_agent_guardrails.py (source pins on the injector predicates,
prompt rules, directive wiring), fresh-subject case in test_followup_reuse.py.

## Watchlist
- The compare ask renders ONE image (vision gate is strict) — fine, but "a
  couple pictures" asks could source more candidates before gating.
- Cross-type association (image follow-up attaching to a data_card thread) is
  still unbuilt; new-widget is the designed behavior there.
- The `canvas_add_widget` tool DOC in lazy-agent-service
  (tool_schemas/html-notes/html-notes.json) still documents url/images
  configs; the server now ignores them, but tightening that doc would save
  the model wasted tokens. Needs a lazy-agent-service deploy.

---

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
