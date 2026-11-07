---
part: YouTube player
status: shipped
updated: 2026-11-06
type: record
---

# YouTube widget: a Sony Trinitron KV-1210

**2026-11-06.** The canvas YouTube widget was a rounded dark card with a title
bar, a 16:9 black box and a bare `<iframe>` embed. It now **is** a 1:1 Sony
Trinitron KV-1210 portable television — a matte-black ABS cabinet with a stepped
brushed-silver bezel, a top folding chrome carry handle, V-shaped telescoping
rabbit-ear antennas, a 3D inwardly-dished dark slate-navy `#232b3a` mask around
a `0.368 x 0.207 m` 16:9 tube, a `| SONY |` badge, a split right control bay
with two stacked square tuner wells, a 17-louver speaker grille, an RGB
`TRINITRON` badge and six vertical micro-controls — drawn edge to edge in its
own card (`app/static/crt/player.html`). Changing videos looks like changing the
channel: the tube fills with snow, a bright band rolls through it, the channel
paddle clicks round 30 degrees per channel, the tube flashes on as the new
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
| Textures | `apps/doc-client/tools/crt_tv/make_textures.py` (PIL + numpy) |
| Build | `testrun -- blender --background --python tools/crt_tv/build_tv.py -- static/models/crt-tv-v1-lite.glb lite` |
| Output | `crt-tv-v1-lite.glb` — 34 meshes, 13 materials, ~17.6 k tris (≈1.3 MB) |
| Visible bounds | 0.600 W x 0.330 H, aspect 1.8182:1 — 0.272 m cabinet, 0.047 m top band (handle + ears), feet |
| Tube | 0.368 x 0.207 m 16:9 glass centered at (-0.060, -0.010), front plane glTF z = +0.1405 |
| Mask | 3D inwardly-dished dark slate-navy `#232b3a`, 0.018 m rounded tube corners |
| Tuner wells | square wells at x = +0.197: VHF at z = +0.056, UHF at z = -0.004 |
| Micro strip | x = +0.264: `TV_POWER_KNOB` at z = -0.105, `TV_LED` at z = -0.128 |
| Named nodes | `TV_CH_KNOB`, `TV_VOL_KNOB`, `TV_POWER_KNOB`, `TV_LED`, `SCREEN_GLASS`, `CRT_MASK`, `CABINET`, `HANDLE_GRIP`, `SPEAKER_GRILLE`, `BRAND_PLATE` |

Preview page: `apps/doc-client/static/crt-tv-3d.html` (front / 3/4 / profile /
big / wide views of the same GLB).

## The KV-1210 geometry

`build_tv.py` models the set front-on in metres (Blender Z-up, front faces -Y),
then exports Y-up glTF so the card's coordinates read x across, y up, z toward
the viewer.

- **Top.** Two chrome stanchion posts on the cabinet roof carry a contoured
  matte-black grip bar with a brushed-silver front inlay — the folding carry
  handle. Behind it a dark dual-swivel block feeds two V-shaped telescoping
  chrome rabbit-ear dipoles: a thicker first segment, a thinner second segment
  and a chrome ball tip, angled outward to x = -0.232 / +0.212 with rod tips
  at z = +0.161.
  Handle and ears live in the top band z in [+0.116, +0.163].
- **Shell.** A matte-black ABS cabinet with a deep rear CRT-neck taper, a
  sculpted black left-side cheek (so the 3/4 view reads as a black side body)
  and two rubber feet. The front is framed by a stepped brushed-silver anodized
  bezel: 9.5 mm perimeter bars with beveled mitered ends, a dark reveal groove
  behind them and a silver divider rib between screen and controls.
- **Screen module (~72% of the width).** A raised brushed-silver inner rim
  surrounds the mask. `CRT_MASK` is a real dish, not a flat plate: four rounded
  loops step inward from the outer slate edge to the exact CRT aperture and
  then recede 0.015 m into a deep shadow lip, so the mask curves toward the
  glass. The aperture is `0.368 x 0.207 m` (16:9) centered at
  `(-0.060, -0.010)` with a `0.018 m` corner radius; the `SCREEN_GLASS` slab
  sits behind it at glTF z = +0.1405. A silver-bordered black `| SONY |`
  slab-serif badge is centered on the lower mask band at z = -0.1285.
- **Right control bay (~26% of the width), two sub-columns.** The main column
  at x = +0.197 is a brushed-silver tuner faceplate holding two stacked
  recessed square black wells (0.055 m) — VHF on top at z = +0.056, UHF below
  at z = -0.004 — each with a printed white channel ring (2..13 and 14..83),
  a conical chrome hub and a 3D sculpted diagonal chrome/white selector paddle
  (`TV_CH_KNOB`, `TV_VOL_KNOB`). Below them, a vertical speaker well carries 17
  horizontal beveled louvers between silver side rails (`SPEAKER_GRILLE`), with
  the Sony `TRINITRON` badge — RGB three-stripe emblem above silver lettering —
  at z = -0.1325. The narrow strip at x = +0.264 is a recessed dark channel
  with six vertical micro-controls: a rectangular silver push button at the
  top, two knurled chrome thumb-wheels with finger tabs beside the VHF/UHF
  wells, two compact knurled chrome tone/hold knobs beside the upper speaker
  grille, the recessed power push-switch `TV_POWER_KNOB` at z = -0.105, and the
  emissive red/amber `TV_LED` below it at z = -0.128.
- **Materials.** Brushed silver with a matching roughness map, chrome, satin
  knob metal, the textured slate mask, matte-black shell, near-black dark trim,
  white paddle inlay, glass at IOR 1.52, and an emissive LED.

## Texture pipeline

`make_textures.py` draws every printed surface with PIL and numpy, at 2x and
downsampled with Lanczos for clean edges. `build_tv.py` creates a temp
directory, shells out to it with the html-notes virtualenv Python
(`apps/html-notes/.venv/bin/python`), feeds the PNGs into textured materials,
embeds them in the GLB and deletes the temp directory — the card ships one
self-contained `.glb`, no sidecar images.

| Texture | Size | Content |
|---|---|---|
| `sony_badge.png` | 512 x 128 | dark plate, silver rim, vertical end-bars, slab-serif `SONY` stretched 1.24x horizontally |
| `trinitron_badge.png` | 512 x 192 | recessed silver-bordered plate, RGB three-stripe emblem, silver `TRINITRON` wordmark |
| `vhf_dial.png` | 512 x 512 | square well, brushed corner frame, 36 radial ticks, white channel numbers 2..13 |
| `uhf_dial.png` | 512 x 512 | same well, channel numbers 14..83 |
| `brushed_silver.png` + `brushed_silver_rough.png` | 512 x 512 | horizontal motion-blur streaks with a vertical sheen gradient; the roughness map is derived from the same streak field |
| `slate_mask.png` | 512 x 512 | `#232b3a` base with a pebble grain and a radial vignette |

The noise fields use fixed seeds (42 for the metal, 19 for the mask), so a
rebuild reproduces the same textures byte for byte.

## Hotspots and the tube overlay

`app/static/crt/player.html` (authored as
`apps/doc-client/static/crt-tv-player.html` and copied by
`scripts/sync-crt-card.sh`) places four zero-size anchors on the glTF tube
corners and projects them every `camera-change`:

| Slot | glTF position |
|---|---|
| `sc-tl` | `-0.244 0.0935 0.1405` |
| `sc-tr` | `0.124 0.0935 0.1405` |
| `sc-bl` | `-0.244 -0.1135 0.1405` |
| `sc-br` | `0.124 -0.1135 0.1405` |

So x in [-0.244, 0.124], y in [-0.1135, 0.0935] and z = 0.1405: exactly the
0.368 x 0.207 m glass centered at (-0.060, -0.010). `syncScreen()` turns the
projected rectangle into `#screen`, so the iframe **is** the glass at any
widget width. Before the GLB loads (or if it fails) it falls back to the
nominal inset from the 0.600 x 0.330 box (`TUBE_LEFT` 0.056 m, `TUBE_TOP`
0.0715 m). The overlay's corner radius is the mask's 0.018 m radius scaled by
the tube height, `18 * h / 207` px (18 px at 207 px), clamped to at least 4 px.

The invisible pick targets track the same projection: 0.055 m squares over the
VHF and UHF wells at `0.197 0.056 0.152` and `0.197 -0.004 0.152`, and a
0.022 m square over the power switch at `0.264 -0.105 0.146`. The camera sits
at `0deg 90deg 1.0658m` with a 20° field of view and targets `0m -0.005m 0m`,
chosen so the front plane (z = 0.130) fills the frame:
`d = 0.130 + 0.330 / (2·tan 10°) = 1.0658 m`.

## Boxless widgets

The HUD theme draws every widget as a holographic panel: `hud-theme.css` cuts
an octagon with `clip-path`, lifts the card 2 px on hover, and paints cyan
L-shaped corner brackets on `::before`. A television is its own object, so the
`BOXLESS WIDGETS` rule in `app/static/index.css` opts `.widget-container.youtube-player`
(and the Walkman's `.widget-container.music-widget-root`) out of all of it:

- fill, border, radius, shadow and backdrop filter are cleared, and every
  property carries `!important` because the frame rule is itself `!important`;
- `clip-path: none !important` — otherwise the octagon would slice the cabinet;
- `transform: none !important` — otherwise the 2 px hover lift would slide the
  device inside its slot;
- `::before` and `::after` are removed (`content: none !important;
  display: none !important`) — the cyan L-brackets and the update light-sweep
  would otherwise float over the device;
- the card's same-origin iframe is made transparent with no border, so the
  frame's iframe chrome does not paint the box the user sees.

## Why 16:9, and the antenna height budget

A 4:3 tube would letterbox every YouTube video: black bars above and below
inside a tube that is itself inside a bezel — two frames of nothing. The tube
is 16:9, so the video fills it.

The 1:1 KV-1210 reference has rabbit ears, so V2 brings them back. They cost
0.047 m above the 0.272 m cabinet (handle and ears occupy z in [+0.116,
+0.163]), which sets the visible height at 0.330 m and the aspect at 1.8182:1.
The widget's height is derived from its width, so the ears are paid for by the
camera fit, not by shrinking the tube: the camera frames the full 0.330 m
object and the 0.368 x 0.207 m glass keeps its size relative to the cabinet.

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
2. The `TV_CH_KNOB` paddle rotates 30 degrees per channel over 400 ms.
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
| card → parent | `channel-up` | the channel paddle was clicked |
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
| `app/static/index.css` | the `BOXLESS WIDGETS` rule (frame, brackets, clip-path, transform) |
| `apps/doc-client/tools/crt_tv/build_tv.py` | the Blender asset |
| `apps/doc-client/tools/crt_tv/make_textures.py` | the PIL texture pipeline (badges, dials, brushed silver, slate mask) |
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
