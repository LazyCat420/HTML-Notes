---
part: Domain features
type: spec
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:app/services/news_pipeline.py, html-notes:app/services/search.py, html-notes:app/services/youtube_helpers.py, html-notes:app/services/youtube_service.py, html-notes:app/widgets/factory.py, html-notes:app/domain/musicbrainz_genres.txt
---

# Domain features

## News and research

The news pipeline is evidence-first:

1. **Collect** candidate stories from the configured feeds and the shared search
   service.
2. **Gate** them for relevance; a story that fails the gate never reaches the card.
3. **Resolve links**: Google News RSS links (`news.google.com/rss/articles/CBMi…`)
   never redirect server-side. `app/services/search.py` decodes them to the
   publisher URL with Google's `batchexecute` (8/8 live, about 1 s), so the card
   links point at the publisher rather than the redirector.
4. **Summarise**: each vetted story is scraped in its own call
   (`_attach_article_bodies`), so one slow site loses only itself. On a gated topic
   ask the editor cannot drop a story; any story it skips is summarised on its own
   (`_summarize_sources_individually`).
5. **Render** the news card with the vetted stories and their summaries.

A topic card reads article bodies, not ~200-character provider snippets. The live
"news about ai regulation" check returns 3 vetted stories and 3 summaries (before
the fix: 1), in about 23 s.

### Live news networks

`app/services/youtube_helpers.py` has a `_NEWS_NETWORKS` table (patterns, official
channels, handles). `is_network_live_ask` catches "put on / tune in / watch
<network>", so the news pre-router does not turn those into a headline card.
`pick_news_network_live` tries, in order:

1. the channel's `/@Handle/live` page (canonical `watch?v=` plus
   `"isLiveNow":true`);
2. an official-channel live search;
3. an honest fallback titled "<Network> isn't live on YouTube right now — latest: …".

A named network plays its own live stream, never a look-alike channel.

## Music asks

Music is the most exercised lane, and its rules are recorded in section 10:

- The fast lane detects a music-shaped ask; the router understands the subject and
  the kind.
- The builder trusts the router's query and does not re-strip the raw message.
- `resolve_music_kind` answers `"genre"` only for an exact MusicBrainz genre
  (`app/domain/musicbrainz_genres.txt`, CC0, 2,220 names), else `""`. A "last word
  is a genre" rule was dropped because "instrumental" is a listed genre.
- The router debug frame carries `modifiers`; a late answer emits
  `{"path": "router-refine", "config": {genre, kind}}` and rebuilds the same widget
  in place.
- History posts the music-player `PlayEvent` shape (`buildPlayEvent`; `track_id` =
  `youtube://<id>`) and maps `/history/recent` rows (`mapServerHistory`).

## Music widget behaviours

| Behaviour | Mechanism | Evidence |
|---|---|---|
| A new music ask does not cut the audio | `destroy()` parks the playing audio in `window.__hnMusicCarry`; the next widget adopts it (`adoptCarry`) and releases it when its own track fires `playing`; nothing claims it within 2 s, it stops. An explicit dismiss still stops it. | `tests/test_music_swap_keeps_playing.mjs` (7). |
| Thumbs rate the song, not the artist | Thumb state is keyed by track id in `localStorage` `hn_music_song_prefs`; thumbs up POSTs `youtube://<id>` to music-player `/api/favorites`, thumbs down or clearing DELETEs it. | `tests/test_music_thumbs_rate_the_song.mjs` (5). |
| The card shows one identity, not three | The compact bento layouts replace the repeated artist/title/channel lines; the card owns the device and the widget owns the data. | `documentation/chapters/07-ui-and-widgets.md`. |
| A slow router refines in place | Fallback player first, then `router-refine` if the router answers within 25 s. | `tests/test_music_routing_understanding.py`. |
| The volume fader and keys are uniform | The 3D silver volume fader, compact keys and taller LCD shipped on 2026-11-03. | `0add0b7`, `a01b495`, `e1f7a92`. |

## YouTube CRT widget

The canvas YouTube widget is a 1:1 Sony Trinitron KV-1210 portable television, drawn
edge to edge in its own card (`app/static/crt/player.html`). The card owns the device
and everything on it (static, NO SIGNAL, OSD, knobs, power-on flash, the player); the
widget owns the data (video resolution, candidate fallback, grid sizing).

| Aspect | Value |
|---|---|
| Asset | `app/static/crt/models/crt-tv-v1-lite.glb` (34 meshes, 13 materials, ~17.6 k tris, ≈1.3 MB), built by Blender 4.2 headless. |
| Textures | `make_textures.py` (PIL + numpy), fixed seeds 42 (metal) and 19 (mask), embedded in the GLB. |
| Visible bounds | 0.600 W x 0.330 H, aspect 1.8182:1; tube 0.368 x 0.207 m at glTF z = 0.1405. |
| Height | Derived: `tvBaseH = round(width / 1.8182)`; the resizer skips the widget and the drag target is the cabinet itself. |
| Channel change | Snow canvas, paddle rotates 30° per channel over 400 ms, iframe swapped at 450 ms, tube revealed on ready or a 3.5 s deadline, 250 ms flash, OSD `CH nn · title` for 3 s. Every tune bumps `tuneSeq`, so a stale timer does nothing. |
| Dead channels | Error 101/150 keeps the tube in snow with `NO SIGNAL / <reason> / PLEASE STAND BY` and a `WATCH ON YOUTUBE ↗` link; the widget walks candidates, fetches once, then wraps. |
| Messages | Both directions check `e.source`, so a foreign frame cannot steer the television. |

The card is authored in `apps/doc-client/static/crt-tv-player.html` and copied by
`scripts/sync-crt-card.sh`; `scripts/youtube_crt_check.py` and
`scripts/youtube_crt_flow_check.py` are the live checks.

## Widget catalog

`app/presentation/widgets/catalog.py` names the widget types the factory can render.
Adding a widget means adding a factory function, a catalog entry and a template
contract test; the model cannot request a type that is not in the catalog.
