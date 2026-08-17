# Music widget roadmap — playback health + click-through UX

2026-08-17. The checklist for finishing the canvas mini player ↔ music-player
integration. Companion docs: `MUSIC_HANDOFF.md` (the shipped handoff),
`music-player/DEEP_LINK_HANDOFF.md` (receiver contract),
`music-player/AUDIO_PIPELINE.md` item 4 (the CDN 1MB cap),
`music-player/LLM_DISCOVERY.md` (the discovery fallback ladder).

## Phase 0 — DONE (this session): why the widget sat silent

Three independent failures stacked. Each was measured live, fixed, and
re-verified against the deployed stack:

- [x] **Genre discovery returned 0 artists for every new genre** ("play house
  music" → "Searching signals..." forever). A three-layer outage in
  music-player's LLM path, fixed as a fallback ladder (`fe31c4d`, `3025c4a`,
  `dd7d44b`, `7c68f03`): unknown persona → registered persona → prefixed name
  (`/custom-agents` silently prefixes `CUSTOM_`) → single-shot when prism's
  agentic loop returns 0 tokens → default model (`deepseek-v4-flash-0731`)
  when the configured Qwen/jetson-shim model starves on any sizeable prompt.
  Verified: `POST /api/radio/genre?genre=house&refresh=true` → **50 real
  artists** (Frankie Knuckles, Larry Levan, ...).
- [x] **One dead track ended the session** — the widget now skips tracks the
  CDN refuses, bounded at 8 consecutive (`HTML-Notes@d27d05a`).
- [x] **End-to-end**: "play house music" in the live canvas → widget skipped 8
  gated tracks and settled on Jesse Saunders playing, 96-track queue.

Upstream defects found but NOT ours to fix (documented for the owner):
prism's agentic tool loop returns empty output on every iteration; the jetson
vllm shim returns 0 tokens for sizeable prompts. Both in Rod's territory —
music-player now routes around them.

## Phase 1 — Make most tracks playable (decision needed, then ~half a day)

YouTube serves only the first ~1MB without a PO token or cookie, so ~5 of 6
tracks die at the CDN (full diagnosis: `AUDIO_PIPELINE.md` item 4). The skip
mitigation makes queues survivable; this phase makes them mostly playable.

- [ ] **DECISION (owner):** cookies or PO tokens?
  - **Option A — PO token provider** (recommended: no account risk): run
    `bgutil-ytdlp-pot-provider` as a container beside scraper-service; add its
    yt-dlp plugin to the scraper image; pass the provider URL via extractor
    args.
  - **Option B — cookies:** export a logged-in YouTube session (Netscape
    format) into the scraper's `/app/cookies.txt` (currently **0 bytes**; the
    code path exists end-to-end and activates on a non-empty file). Fastest,
    but uses a real account.
- [ ] ⚠ **Deploy hazard, either option:** the scraper lives in
  **trading-service**. Deploying it (a) kills live trading cycles, (b) the
  staged `.env.deploy` flag map arms a 30-table Mongo migration on the NEXT
  deploy of either trading repo. Reconcile with the migration work before any
  scraper deploy. (Option B may avoid a deploy entirely if `cookies.txt` is
  volume-mounted — verify before assuming.)
- [ ] Verify with the measured probe: `Range: bytes=0-` on 10 mix tracks,
  before vs after. Target: >8/10 playable (was 2/12).

## Phase 2 — Widget resilience polish (HTML-Notes only, independent)

- [ ] **Pre-flight probe:** before handing a track to the audio element,
  `fetch` the stream URL with `Range: bytes=0-` (CORS is open; abort after
  headers). Drop dead tracks in the background so playback rarely hits a 403
  at all — the current skip is reactive and audible as a delay.
- [ ] **Silence the noise:** `loadTrack`'s `play()` promise is uncaught →
  "Uncaught (in promise) NotSupportedError" spam in the console. Add a
  `.catch` (the `error` event listener already drives the skip).
- [ ] **Show the skip:** surface `streamStatus` ("Skipping a track the source
  refused…") in the card — today it's set but the searching layout hides it
  once a track exists.
- [ ] **Cap the searching state:** if neither genre nor artist path produced a
  track in ~90s, show a retry affordance instead of "Tuning in…" forever.

## Phase 3 — Click-through UX (the feature request)

Current state: clicking the **title/artist block** (once a track is playing)
hands off the current track at the current second — shipped in
`MUSIC_HANDOFF.md`. This phase splits it into two distinct actions:

- [ ] **3.1 Song title click → "more like this".** Handoff as today
  (`?track=..&t=..`) plus `seed=similar`. Receiver: after the handed-off
  track starts, music-player kicks its genre/similar pipeline (existing
  `get_similar_artists` + mix plumbing) and fills the queue behind the
  playing track — append, never replace.
- [ ] **3.2 Artist name click → "explore this artist".** Handoff plus
  `seed=artist&artist=<name>`. Receiver: add the artist node to the graph
  (existing `handleAddNode(name,'artist')`) and queue their catalog behind
  the playing track.
- [ ] **3.3 Receiver plumbing:** extend `DeepLinkBootstrap` to parse `seed`,
  and after `playing` fires dispatch a `CustomEvent` (the app already uses
  this channel: `retro-play-youtube`, `retro-focus-node`) that
  `MusicPlayerWidget` handles without interrupting playback.
- [ ] **3.4 Sender split:** `h4` (title) vs `p` (artist) get separate
  `@click.stop` handlers + distinct hover affordances; BOTH template twins
  (`factory.py` + `index.js` self-heal) and `openInFullPlayer(mode)` gains a
  mode param. Extend `tests/test_music_handoff.mjs` for both modes.
- [ ] **3.5 Stored-widget gap:** widgets already sitting on a canvas keep
  their pre-click HTML (server template changes don't rewrite stored canvas
  markup). Decide: version-bump the widget so self-heal re-renders, or accept
  fresh-spawns-only and note it.
- [ ] **3.6 No-track click:** while "Searching signals…" there is nothing to
  hand off — currently a no-op. Make it open the bare app (still useful) with
  the genre as `?genre=` once the receiver learns to seed from genre alone.

## Phase 4 — What YOU should test (after each phase)

1. **"play house music"** (or any genre NOT already cached): widget resolves
   and audio starts — first-ever load of a brand-new genre may take ~1-2 min
   (LLM discovery), cached genres seconds. Bug: "Tuning in…" forever.
2. **Let it play through a few tracks**: occasional quick skips are the CDN
   cap; a red "Nothing in this queue would play" after 8 straight is the
   bounded give-up. Bug: silence with no message.
3. **Click the song title** mid-play: new tab, same song, same second,
   canvas paused. After 3.1: the new tab's queue fills with similar music.
4. **Click the artist name** (after 3.2): new tab playing the same song, with
   that artist's catalog queued behind it.
5. **After Phase 1**: repeat (2) — skips should become rare (>8/10 tracks
   play) instead of the norm.
