# Router reliability & widget ergonomics — 2026-10-08

## Proven failures (measured on the NAS, 2026-10-08)

| # | Failure | Evidence |
|---|---|---|
| 1 | Keywordless music asks ("play smooth jazz") missed the tier-2 gate `\b(music\|player\|radio)\b` and fell to the tier-3 agent | probe: `route=('agent', None, 'none')`, **104.8s** to first widget |
| 2 | `fast_llm_json` classifier intermittently ReadTimeouts (`10.0.0.141:8000` is a dev box that stalls under load) | container log + 12-sample probe showing 0.4–0.9s when healthy |
| 3 | `lazy-agent-service` restarts itself mid-turn (`RestartCount: 2`, exit 0) — in-flight SSE turns die with "peer closed connection" | `docker inspect` mid-probe: `Up 2 minutes` while others were `Up 43 hours` |
| 4 | `RuntimeChatAdapter` reported client cancels AND runtime crashes identically as `"Shared agent runtime unavailable: "` (empty string = `str(CancelledError())`) | container log line 7795 |
| 5 | Every "next track" paid a full yt-dlp extraction | 8.1–16.5s cold vs **0.12s** cached, per-track |
| 6 | Genre discovery LLM batches intermittently returned empty / Prism 500 | music-player logs |

## Fixes (html-notes `32865e6`, music-player `fa70b5f`)

- **Deterministic music gate** (`app/routes/message.py`): a play-verb + residual
  genre ("play smooth jazz", "listen to some reggae") now builds the widget
  directly. `_NON_MUSIC_SUBJECTS` guards "play the news" / "play a podcast".
  `MUSIC_FILLER_WORDS` gained pronouns/verbs ("i", "listen") so residuals are clean.
- **Sticky failover** (`app/llm.py`): an endpoint that timed out demotes itself
  for 60s instead of costing every caller its 20s timeout first.
- **Adapter honesty** (`app/services/runtime_chat_adapter.py`): client
  cancellation re-raises quietly; real failures log `type: message`; a rejected
  cancel (run already finalized server-side) logs info, not warning.
- **Prefetch** (music-player `GET /api/youtube/stream-info/{id}` + widget JS):
  warms the 5h extraction cache while track N plays (N+1 on start, N+2 after
  30s idle). Measured: cold 10.0s → 0.32s after warm.
- **Discovery retry**: one bounded retry (3s backoff) on empty/5xx LLM batches.
- **Ergonomics**: youtube_player uses `w-full aspect-video` (measured 826×520
  at an 826px column; was a thin fixed-456px strip); mini_music_player chrome
  compacted (826×265). Self-heal rehydration twins in `index.js` mirrored.

## Verification

`bench/replay_benchmark.py` replays canonical utterances and reports route +
time-to-first-widget. Post-deploy: `play smooth jazz` → tier2 in **0.3s**
(was 105s/flaky); zero errors across the music/news/clock set. Visual checks:
video 826×520, music 826×265 in headless Chromium against the deployed canvas.

## Fast-first audio (2026-10-08, later wave)

Genre radio now yields 2 genre-seeded tracks from a single direct YouTube
search BEFORE the LLM discovery phase (music-player `03b4102`, Phase 0 in
`get_genre_radio_mix_stream`); the ids are seeded into Phase 2 dedup so the
full mix never replays them. Measured cold genre (`zamrock`): first tracks at
**3.9s** (was ~30-50s: LLM discovery 10-30s + first artist search + 10s
extraction).
