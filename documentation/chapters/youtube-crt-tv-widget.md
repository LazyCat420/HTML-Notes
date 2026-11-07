---
part: YouTube player
status: shipped
updated: 2026-11-06
type: record
---

# YouTube widget: an old-school CRT television

**2026-11-06.** The canvas YouTube widget was a rounded dark card with a title
bar, a 16:9 black box and a bare `<iframe>` embed. It now **is** a television:
a Blender-built 0.600 x 0.330 m CRT cabinet — wood case, chrome-trimmed tube,
three knurled knobs with pointers, a slatted speaker grille, a "SUNVISION"
brand plate and an amber power LED — drawn edge to edge in its own card
(`app/static/crt/player.html`). Changing videos looks like changing the
channel: the tube fills with snow, a bright band rolls through it, the channel
dial clicks round 30 degrees per channel, the tube flashes on as the new
channel comes up, and a green OSD reads `CH 03 · <title>` for three seconds.

Same split as the Walkman music widget: the **card owns the device and
everything you see on it** (static, NO SIGNAL, OSD, knobs, power-on flash, the
YouTube player itself); the **widget owns the data** (video resolution,
candidate fallback, grid sizing). The parent frame is only the aspect-locked
box plus two hover chips (title, close).

## The asset

| Item | Value |
|---|---|
| Source | `apps/doc-client/tools/crt_tv/build_tv.py` (Blender 4.2, headless) |
| Build | `blender --background --python tools/crt_tv/build_tv.py -- static/models/crt-tv-v1-lite.glb lite` |
| Output | `crt-tv-v1-lite.glb` — 1.66 MB, 14 948 tris, 32 meshes, 7 materials |
| Cabinet | 0.600 W x 0.260 H x 0.320 D, feet to 0.330 H total |
| Tube | 0.400 x 0.225 m = 16:9, chrome trim, glass front at y = -0.1410 |
| Knobs | column x = 0.225: channel r 0.030, volume r 0.018, power r 0.010 |
| Named nodes | `TV_CH_KNOB`, `TV_VOL_KNOB`, `TV_POWER_KNOB`, `TV_LED`, `SCREEN_GLASS`, `CABINET`, `SPEAKER_GRILLE`, `BRAND_PLATE` |

Preview page: `apps/doc-client/static/crt-tv-3d.html` (front / 3/4 / profile /
big / wide views of the same GLB).

## Why 16:9 and no antenna

A 4:3 tube would letterbox every YouTube video: black bars above and below
inside a tube that is itself inside a bezel — two frames of nothing. The tube
is 16:9, so the video fills it.

Rabbit-ear antennas were in the first sketch and were cut: ears cost about
0.10 m of height above the cabinet, and the widget's height is derived from its
width (1.8182:1), so every pixel of ear would shrink the tube. The object reads
as a CRT without them.

## The resize grip is the cabinet

`WidgetResizer` gives every widget a corner grip and a stored pixel height.
Both are wrong here: a free height breaks the camera fit and crops the
cabinet (exactly the bug the Walkman had), and a grip would draw a box around a
television.

- The widget's height is **derived**: `tvBaseH = round(width / 1.8182)`, held
  by an Alpine `:style="{ height: tvHeight + 'px' }"` binding. Nothing else
  writes it.
- `WidgetResizer.decorate()` skips `.youtube-player`, so there is no corner
  grip; `apply()` ignores a stored height for it; `ensureExpandButton()` skips
  it (one object, no drawer).
- The drag target is the **model-viewer surface itself** — the cabinet's own
  bezel — inside the card, exactly as the Walkman's silver deck is. The card
  captures the pointer, posts `{phase:'start'|'move'|'end'|'reset', dx, dy, x,
  y}` in its own viewport coordinates, and the parent converts the motion into
  a grid column span (width snaps to columns, height follows through
  `ResizeObserver`).
- Only `{cols}` is saved. A stationary click (≤ 3 px) is not a resize.
  Double-clicking the cabinet drops the saved size.

## The channel-change sequence

`playById(id)` in the widget posts `{action:'tune', video, title, channel}`;
`channel` is the 0-based index of the attempt, so the dial advances one click
per channel. In the card:

1. `startNoise()` — a 220x124 noise canvas repainted every frame (rolling band,
   per-line jitter, a tear line) goes up over the tube; the old player is
   hidden.
2. The channel knob rotates 30 degrees per channel over 400 ms.
3. At 450 ms the `<iframe>` is swapped to
   `youtube.com/embed/<id>?autoplay=1&mute=1&enablejsapi=1`.
4. The tube is revealed when the player reports ready, or at a 3.5 s deadline —
   a video that refuses to embed must not hold the snow forever.
5. A 250 ms power-on flash, then the OSD (`CH nn · title`) for 3 s.

Every tune bumps `tuneSeq`; a timer that fires for a stale sequence does
nothing, so a fast second channel change cannot be overwritten by the first.

## Messages (postMessage, both directions)

| Direction | Action | Payload |
|---|---|---|
| card → parent | `ready` | card (re)loaded — parent re-sends the current tune/error |
| card → parent | `embed-error` | `{code}` — widget falls back to the next candidate |
| card → parent | `channel-up` | the channel knob was clicked |
| card → parent | `volume` | `{volume}` — remembered, applied in the card |
| card → parent | `power` | `{playing}` — remembered, applied in the card |
| card → parent | `resize` | `{phase, dx, dy, x, y, moved}` |
| parent → card | `tune` | `{video, title, channel}` |
| parent → card | `error` | `{message, watchUrl}` — NO SIGNAL screen |
| parent → card | `state` | `{volume}` |

Both sides check `e.source` (`e.source === f.contentWindow` in the parent,
`e.source === window.parent` in the card), so a foreign frame on the page
cannot steer the television.

## Dead channels

A video that blocks embedding (error 101/150) is a dead channel: the tube stays
full of snow and the OSD reads `NO SIGNAL / <reason> / PLEASE STAND BY`, with a
`WATCH ON YOUTUBE ↗` link (the video's own page) when the widget knows it. The
widget first walks its candidate list, then asks
`/api/youtube/candidates` once for more, then gives up. The channel knob walks
candidates, fetches once if needed, and then wraps around the discovered
channels so the dial never dead-ends.

## Files

| File | Role |
|---|---|
| `app/static/crt/player.html` | the card (copied from doc-client by `scripts/sync-crt-card.sh`) |
| `app/static/crt/crt-tv-v1-lite.glb`, `model-viewer.min.js`, `studio.hdr` | card assets |
| `app/static/js/widgets.js` → `youtubePlayerWidget` | video resolution, fallback, grid sizing, messages |
| `app/widgets/factory.py` → `render_youtube_player` | the aspect-locked box + hover chips |
| `app/static/index.js` | resizer exemptions, canvas serialization, self-heal twin |
| `apps/doc-client/tools/crt_tv/build_tv.py` | the Blender asset |
| `scripts/sync-crt-card.sh` | card + assets → `app/static/crt/` |
| `scripts/youtube_crt_check.py` | live check (aspect, tube fit, tune, static, knob, drag, reset) |
| `scripts/youtube_crt_flow_check.py` | live SSE chat flow (video ask → CRT widget; second ask → static channel change) |
| `tests/test_youtube_crt_geometry.mjs` | node tests against the real widget factory |
| `tests/test_widgets.py` | template contract: the parent holds no YouTube embed |

## Verification

- `node --test tests/test_youtube_crt_geometry.mjs` — 18 tests (aspect,
  derived height, tune/channel numbering, foreign-frame guard, ready re-send
  and query race guard, candidate fallback, no-signal watch link, knob walk and
  wrap-around, bezel drag columns-only, stationary click, return-to-origin
  drag reconciliation, vertical drag along the aspect, reset keeps the height).
  Mutation-proven: each test fails when its rule is broken.
- `scripts/youtube_crt_check.py` and `scripts/youtube_crt_flow_check.py` — live,
  against the deployed app.
- The GLB was judged in model-viewer front / 3/4 / profile, not in Blender
  renders.
