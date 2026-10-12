---
part: Runtime
status: shipped
updated: 2026-10-11
review-by: 2026-10-25
---

# Music Swap, Song Thumbs, Live News Networks and News Summaries

**2026-10-11. Tested with nemotron35, the weakest model in rotation, on the
idea that guardrails that hold for it also hold for GLM.**

## 1. A new music ask cut the music to silence (`f0bc09f`)

A new music ask swaps the canvas, and the swap tore down the old widget. Its
`destroy()` stopped the audio before the new widget had a track, so the user
heard silence.

Fix (`app/static/js/widgets.js`): `destroy()` now parks the playing audio in
`window.__hnMusicCarry` (`parkForSuccessor`). The next music widget adopts it
in `init` (`adoptCarry`) and lets it go when its own track fires `playing`
(`releaseCarry`). If nothing claims the audio within 2 s, it stops. An explicit
close is different: `WidgetManager.dismiss` marks the element
`data-user-dismissed="1"`, and that still stops the music.

Tests: `tests/test_music_swap_keeps_playing.mjs` (7) on
`tests/_music_widget_harness.mjs`.

## 2. Thumbs up rated the artist, not the song (`4a69f7c`)

Thumb state is now keyed by track id (localStorage `hn_music_song_prefs`).
Thumbs up POSTs the song to music-player `/api/favorites`
(`youtube://<id>`, rating 5). Thumbs down, or clearing an earlier thumbs up,
DELETEs it. The title reads "Like this song".
Tests: `tests/test_music_thumbs_rate_the_song.mjs` (5).

## 3. "cnn live news" / "fox live news" / "put on fox news" (`83e923c`, `5013a4d`)

A named network now plays its own live stream, never a look-alike channel.
`app/services/youtube_helpers.py` has a `_NEWS_NETWORKS` table (patterns,
official channels, handles).

`pick_news_network_live` tries these in order:

1. The channel's `/@Handle/live` page: a canonical `watch?v=` plus
   `"isLiveNow":true`.
2. An official-channel live search.
3. An honest fallback, titled "<Network> isn't live on YouTube right now —
   latest: …".

`is_network_live_ask` catches "put on / tune in / watch <network>", so the
news pre-router no longer turns those into a headline card.
Tests: `tests/test_live_news_networks.py` (11).

## 4. News summaries: none, or only one link

Two causes, fixed in two repos:

- **No summary at all.** lazy-agent-service read Exa's rate-limit reply (HTTP
  200, `_meta["ai.exa/rateLimited"]`) as success with 0 results, cached it,
  and never reached SearXNG. Its `SCRAPER_SERVICE_URL` also defaulted to
  localhost inside the container. Fixed in lazy-agent-service `66fab33`.
- **Only one link summarised** (this repo, `c8db79e`):
  - The relevance gate vetted 3 stories, and then the editor's omissions
    deleted 2 of them. On a gated topic ask the editor can no longer drop a
    story. Any story it skips is summarised on its own
    (`_summarize_sources_individually`).
  - Topic cards now read the article bodies, not ~200-char provider snippets.
  - Google News RSS links (`news.google.com/rss/articles/CBMi…`) never
    redirect server-side. The scraper spent ~24 s failing on each one, which
    sank the shared 12 s batch. `search._resolve_google_news_links` decodes
    them to the publisher URL with Google's `batchexecute` (8/8 live, about
    1 s), so the card links point at the publisher too.
  - `_attach_article_bodies` scrapes each story in its own call, so one slow
    site loses only itself, and it logs each miss with its reason.

Live "news about ai regulation": 3 vetted stories, 3 summaries (before: 1),
23 s.

Tests: `tests/test_news_topic_summaries.py` (3),
`tests/test_google_news_links_resolve.py` (3),
`tests/test_market_news_grounding.py` (updated for per-item scrapes).

## Watchlist

- `fast_llm_json` calls vLLM directly instead of the lazy-agent-service
  harness.
- Answer cards can leak raw citations ("Sources : 0, 1, 2").
- "what happened with hurricane simon" routes to a map.
