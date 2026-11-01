---
part: Music player
status: in-progress
updated: 2026-10-31
review-by: 2026-11-28
type: plan
---

# Music widget: compact bento layouts

**2026-10-29.** The canvas mini player (`render_mini_music_player` in
`app/widgets/factory.py`, with a hand-maintained twin in `app/static/index.js`)
prints the same artist **three times** for an ordinary artist ask, and the card
is pinned to a fixed 184px that doubles to 384px when the queue opens. This
chapter records the redundancy, the compaction rules, five full-width visual
directions, a four-layout **half-width retro set (300px)**, and a Walkman whose
key row is a real Blender-built glTF asset.

The live, interactive mockups (playing / long title / queue open / narrow
300px, all clickable) are at
**http://10.0.0.171:8910/static/music-player-designs.html** — open that page to
judge the designs; this chapter is the written record and the comparison.

Nothing in the widget has been changed yet. The five layouts are mockups only.

## The redundancy

For “play Buckethead” the card shows:

| Where | String | Source field | Why it is there |
|---|---|---|---|
| Top chip | `BUCKETHEAD` | `genreFilter` (the routing query) | Names the ask |
| Title line | `Buckethead - Soothsayer` | `currentTrack.title` (raw YouTube title) | Upload title |
| Second line | `Buckethead` | `currentTrack.artist` (the channel) | Channel name |

![Current widget with redundancy pins](artifacts/music-widget-designs/00-current.png)

Three strings, one fact. The chip is the same act because the ask named an
artist; the title repeats it because YouTube titles are `Channel - Song`; the
second line is the channel again. The rating controls and their status text
also take ~110px from the identity column, which is why the title is capped at
`max-w-[55%]`.

## The compaction rules

1. **Every string appears exactly once.** Identity is one line:
   `artist — title`, with the artist split out of the raw upload title when it
   is already a prefix.
2. **The chip only survives when it says something new.** Show it when
   `kind === 'genre'` and the genre differs from the artist; otherwise a 3px
   accent tick carries the mode.
3. **Thumbs, close, and volume are hover-only.** They keep their function and
   lose their permanent pixels.
4. **Times live inside the timeline tile**, not in a separate control row.
5. **The queue never changes the card height.** Overlay, or a portal panel
   anchored to the card. The masonry layout script re-spans on every height
   change, so a growing queue is not just 200px — it is a re-flow.
6. **Status text replaces the identity line** while loading; it never adds a
   row.

### Identity line

```
split   = raw.split(/\s+[-–—]\s+/)            // "Buckethead - Soothsayer"
left    = split[0]; rest = split.slice(1).join(" - ")
title   = rest || raw
strip   = /\s*-\s*Topic$|\(Official (Video|Audio|Lyric Video)\)|\[(HD|4K|Lyrics)\]/i
artist  = channel, unless channel ends in " - Topic"
show    = `${left} — ${title}`                // artist once, from the title's own prefix
```

Long example, the one the lab's “Long title” toggle uses:
`The Cinematic Orchestra - To Build a Home (feat. Patrick Watson) [Official Video]`
becomes `The Cinematic Orchestra — To Build a Home (feat. Patrick Watson)`.

### Bento grammar

Six tile roles, one datum each. Every layout below arranges the same roles, so
the data contract does not change with the skin:

| Role | Holds | Notes |
|---|---|---|
| `ART` | YouTube thumbnail (`i.ytimg.com/vi/<id>/mqdefault.jpg`) | Optional; Swiss omits it |
| `IDENTITY` | `artist — title` | Exactly one occurrence of each |
| `TIMELINE` | elapsed, bar, duration | 2–3px bar, 8–9px mono times |
| `TRANSPORT` | prev / play / next | Play key is the only filled accent |
| `META` | thumbs, volume, queue, rating status | Hover-revealed where possible |
| `QUEUE` | list overlay or portal panel | Never grows the card |

Radii 8–10px, gutters 4–6px, accent reserved for the play key and the progress
fill.

## Five directions

Heights are the real rendered heights of the mockups, measured in Chromium at
620px wide. All five keep the hub's forced glass frame (`index.css` overrides
background and radius with `!important`); the palette and inner grammar vary.

### 1 · Bento Rail — 72px (−112px, 61% smaller)

![Bento Rail](artifacts/music-widget-designs/01-bento-rail.png)

A single strip of four rectangles: art, identity, timeline, transport. It reads
like a status bar — the quietest option and the cheapest on canvas.

- One identity line; artist in 10.5px caps, em dash, title.
- Times in the timeline tile (`1:32 ▬▬▬ 4:02`).
- Queue is a **portal panel** anchored below the card: 0px height change.
- Thumbs and close are hover-only; volume moves into the queue header.
- At ≤420px the timeline tile and prev/next drop; art, identity, play, queue
  remain.

Trade-offs: least art presence (48px tile); long titles need the marquee; the
portal panel needs `position:fixed` plus a `body` append to escape
`overflow:hidden`.

### 2 · Bento 2×2 — 168px (−16px, and −216px with the queue open)

![Bento 2×2](artifacts/music-widget-designs/02-bento-grid.png)

Four equal rectangles with the art tile spanning both rows. The progress bar
rides the bottom edge of the art, so the timeline costs nothing extra.

- Identity tile allows two clamped lines, so a long title does not marquee.
- Art tile earns its space: real thumbnail, with progress in its foot.
- Meta tile carries thumbs, volume, queue, and the rating status.
- Queue covers the grid; five rows fit, card stays 168px.

Trade-offs: only 16px shorter than today (the win is the queue); the busiest of
the bento options; wants a real thumbnail rather than a glyph.

### 3 · Vinyl Dial — 120px (−64px)

![Vinyl Dial](artifacts/music-widget-designs/03-vinyl-dial.png)

One circular element does the heavy lifting: the art is a record that turns
while playing, with progress as a ring around it. Everything else is thin type
— a two-panel split, not a tile grid.

- Artist eyebrow + title: two stacked strings, each printed once.
- The progress ring replaces both the bar row and the times row.
- Record turns only while playing (6s linear), so the card is alive without a
  marquee.
- Queue overlay inside the card; three rows fit.

Trade-offs: most distinctive silhouette; round art needs a real thumbnail; warm
brass palette is the farthest from HUD cyan; rotation must respect
`prefers-reduced-motion`.

### 4 · Cassette Deck — 128px (−56px)

![Cassette Deck](artifacts/music-widget-designs/04-cassette-deck.png)

A tape shell: two reels, one LCD line, chunky mechanical keys. The identity is a
single marqueed LCD line, and the right reel doubles as the progress dial.

- LCD line holds `BUCKETHEAD — Soothsayer`, marqueed only on overflow.
- Tape counter in 8px mono replaces the time row.
- Reels spin only while playing.
- Keys are 30×28 with a real pressed state — the most tactile controls here.

Trade-offs: strongest personality, least “dashboard”; skeuomorphism costs a few
pixels to shadows; the LCD green/amber is a palette commitment.

### 5 · Swiss Editorial — 96px (−88px)

![Swiss Editorial](artifacts/music-widget-designs/05-swiss-editorial.png)

No tiles, no art, no chrome: a kicker, a big title, a 1px progress rule, and
outlined keys. The title is the interface.

- Kicker + title on separate rules; each string once, artist in 9px caps.
- Progress is a hairline under the title; times sit on its right end.
- Keys are outlined squares, not filled pills; accent only on the play key.
- No art at all, so the widget never waits on a thumbnail.

Trade-offs: most readable type (19px title), but nothing shows what the track
looks like — a real cost on a visual canvas.

### Queue behaviour

![Bento Rail with the portal queue panel open](artifacts/music-widget-designs/06-rail-queue-panel.png)

The rail opens its queue **outside** the card; the other four cover the card
from inside. Either way the card height is unchanged, which is the point: no
`h-[384px]` binding and no masonry re-span.

## Half-width retro set (300px)

You liked the cassette and wanted half the width with no dead space. These four
are **one grid column wide (300px)**, with 3–6px paddings and no empty rows:
every pixel is a datum, a control, or a texture. Same identity rule — artist
once, title once, times inside the readout.

### A · Walkman — 100px

![Walkman](artifacts/music-widget-designs/07-walkman.png)

The cassette rebuilt as an 1980s personal stereo: brushed aluminium shell, two
spools, one amber LCD line, black plastic keys. Two rows only — 58px shell and
26px keys, 4px apart.

- Amber LCD holds the identity line and the counter in the same window.
- Right spool is the progress ring; spools turn only while playing.
- Brushed metal is a repeating gradient — no image, no fetch.
- At 300px the LCD fits ~34 characters before it marquees.

### B · Clear Neon Cassette — 100px

![Clear Neon Cassette](artifacts/music-widget-designs/08-clear-neon.png)

The 90s clear-shell cassette: see-through body, tape spools on both sides, a LED
meter instead of a bar, neon-cyan type with a magenta artist.

- Both spools are rings: left shows remaining tape, right shows played.
- LED meter replaces the 1px bar: 12 segments, the last four turn magenta.
- Neon keys are 24px outlined squares — lighter than the walkman's plastic keys.
- Glow is CSS shadow, so nothing blurs the text.

### C · PS2 Pixel Shader — 100px

![PS2 Pixel Shader](artifacts/music-widget-designs/09-ps2-pixel.png)

A PlayStation 2 boot-screen readout: 2px stepped borders, a scanline overlay,
dither-blue surfaces, uppercase mono type with red/blue fringing, and a progress
bar made of discrete cells rather than a smooth fill.

- Cell bar: 16 cells, filled ones cyan, the leading edge violet.
- Chromatic fringe on the title only (1px red/blue shadow).
- Scanlines are a 1px repeating overlay above content, under controls.
- Times sit at both ends of the control row, 8px pixel mono.

### D · Buick LeSabre ’93 Cluster — 118px

![Buick LeSabre 1993 cluster](artifacts/music-widget-designs/10-buick-93.png)

A 1993 GM instrument cluster: woodgrain strip, green vacuum-fluorescent readout,
a needle sweeping an arc gauge as the progress, and chunky rocker keys with
engraved labels.

- VFD line holds artist + title in green, with the digital clock at the right.
- Needle gauge is the progress: −90° at 0%, +90° at 100%, 28px needle, red glow.
- Rocker keys labelled SEEK − / SEEK +, engraved 7px mono.
- Woodgrain is a 19px repeating gradient — no image, no fetch.

### A2 · Whole player in Blender — 150px

![The whole player as a 3D mesh](artifacts/music-widget-designs/11-walkman-3d.png)

![Close up: wood, chrome, engraved keys](artifacts/music-widget-designs/12-walkman-3d-detail.png)

The card is now one glTF mesh instead of styled DOM: a 300 × 150 device with a
walnut front panel, a chrome bezel frame, a recessed screen module, five silver
keys that face the viewer, and an embossed WALKMAN wordmark.

- Source: `apps/doc-client/tools/walkman_keys/build_body.py`, built headlessly
  (`blender --background --python … -- static/models/player-body.glb`).
- Asset: `static/models/player-body.glb`, 275 KB, 2 216 triangles, 7 materials,
  one 512 × 256 walnut grain texture packed into the file.
- Wood: generated grain (warped rings + streaks) written as a real PNG and
  packed — procedural Blender nodes do not survive glTF export.
- Keys: polished front faces (roughness 0.09) against satin sides (0.34), with
  the icons cut in as **real grooves** (boolean, 0.9 mm deep) and filled with a
  dark inlay recessed 0.2 mm, so the indent reads as a shadow line rather than a
  painted glyph.
- Screen: the glass is 3D; the text is a DOM canvas anchored to the screen
  centre by a hotspot, drawn from a 5 × 7 bitmap font at 1 px and drawn only in
  two colours — amber on near-black, 43-cell progress bar, live time, PLAY/PAUSE.
- Feedback: clicking a key lights an amber glow around and under it for 0.7 s
  (CSS halo + inset bottom glow on the hotspot), plus a synthesised click. No
  key travel animation.
- Fallback: if the GLB fails to load the card keeps the pixel screen on its own.
- Live: **http://localhost:8910/static/walkman-3d.html**

Why a mesh and not CSS: the chrome reflects a live environment, the wood grain
is a real texture, the keys are actual solids with real grooves, and the whole
device holds up at 2× in the close-up. Cost: one 281 KB request and a WebGL
context per widget — the reason to keep it to the one card that benefits.

All four keep the queue as an inset overlay (three rows fit) and the times
inside the readout. Because they replace the card surface with metal, plastic,
woodgrain, or dither, they need the **shell opt-out** (open question 4):
`index.css` forces `background-image` and `border-radius` on
`.widget-container` with `!important`, which would flatten these textures.

### A2b · Realism variants — six separate demos to pick from

![Seven variants side by side](artifacts/music-widget-designs/16-walkman-variants-board.png)

![The full stack at 2×](artifacts/music-widget-designs/17-walkman-fullstack.png)

The wood-panel player looked good but flat: the chrome mirrored model-viewer's
neutral grey studio, and the wood had colour but no relief. Six changes were
built separately so each can be judged on its own.

| Variant | What changes | Asset |
|---|---|---|
| 1 · Current | today's page, neutral studio environment | 281 KB, 2 216 tris |
| 2 · + Studio HDR | a generated studio environment: warm key softbox left, cool fill right, strip light behind, dark floor | same mesh |
| 3 · + Bevels | 4-segment bevels at 1.5× radius on every edge, clamped to 45% of the thinnest dimension | 323 KB, 4 312 tris |
| 4 · + Wood relief | 1 024 × 512 albedo, normal and roughness derived from the grain height, clearcoat lacquer (weight 0.35, roughness 0.05) | 2.7 MB, 3 textures |
| 5 · + Glass screen | coated cover glass: coat 1.0, coat roughness 0.02, IOR 1.52, roughness 0.03 | 281 KB |
| 6 · + AO | 16-ray ambient occlusion baked into `COLOR_0` on 24 primitives, 12 mm radius | 300 KB |
| 7 · All of it | bevels + relief + glass + AO on the studio HDR | 2.75 MB, 4 312 tris |

- Source: `apps/doc-client/tools/walkman_keys/build_body.py` now takes flags
  after `--`: `bevel`, `wood`, `glass`, `ao`, `full`.
  `tools/walkman_keys/make_env.py` generates the studio environment as a
  512 × 256 Radiance RGBE file (`static/models/studio.hdr`, 512 KB, peak 16,
  mean 0.343).
- Live: **http://localhost:8910/static/walkman-3d-variants.html** — every card
  after the first shares the studio HDR, so the differences are the asset, not
  the lighting. The toolbar swaps environment, tone mapping (ACES / AgX /
  neutral), exposure, shadow, and a 0.30 m close-up.

Why each one matters, in order of payoff:

1. **Environment.** Chrome is a mirror; it can only look as good as what it
   reflects. A neutral grey studio gives grey metal. The generated HDR puts a
   warm softbox on one side and a cool one on the other, so the bezel and key
   sides carry distinct bright and dark bands.
2. **Bevels.** Real objects have no 90° corners. A 0.5–0.8 mm bevel catches a
   thin highlight on every edge, which is what the eye reads as "manufactured"
   rather than "CG box". 2 216 → 4 312 triangles.
3. **Wood relief.** Colour alone reads as paint. The normal map is derived from
   the same grain height as the albedo, the roughness map makes pores rougher
   and figure glossier, and a thin clearcoat adds the lacquer sheen.
4. **Glass.** The screen should reflect the room. A coated cover glass gives a
   soft highlight that slides across it as you orbit.
5. **AO.** Baked occlusion darkens the key wells and the screen recess, so the
   device sits on its own geometry instead of floating.
6. **Tone mapping.** ACES with tuned exposure keeps the highlights from
   clipping; AgX is available if a softer roll-off is wanted.

Measured on 692 × 346 captures (2× DPR), each variant differs from the plain
HDR render in its own region: bevels 10 773 px (max Δ158) across every edge,
wood 12 487 px (Δ72) on the panel, glass 6 689 px (Δ58) inside the screen box,
AO 2 091 px (Δ76) around the recesses, full stack 20 444 px (Δ161).

Cost: the full stack is 2.75 MB because of three 1 024 × 512 PNGs. At 512 × 256
it drops to roughly 700 KB — the difference between a demo and something a
widget can load.

### A2c · v2 — the screen reads and the wood has relief

![v2 at widget size, 2×](artifacts/music-widget-designs/18-walkman-v2-card.png)

![v2 close up, 2×](artifacts/music-widget-designs/19-walkman-v2-hero.png)

![full stack vs v2, same camera and lighting](artifacts/music-widget-designs/20-walkman-v1-vs-v2.png)

Two complaints drove v2: the LCD border only appeared if contrast was cranked,
and the wood read as a flat texture. Diffuse light is the wrong lever for the
first — it lifts the wood mid-tones and the LCD background by the same amount,
so it adds no *local* contrast, and the exposure compensation needed to see the
border is exactly what washed out the picture. v2 fixes both with geometry and
material, not light.

**The border is now built, not lit.**

| Layer | Size | Why |
|---|---|---|
| Frame | 3 mm dark metal (base 0.30 / metal 1 / rough 0.12), four mitred bars sitting entirely *outside* the glass | a metal edge catches the rim strips and reads at any exposure; because it starts at the glass edge the canvas overlay is never covered |
| Spill ring | 2 mm emissive amber, mitred flush outside the frame | reads as backlight escaping the panel; emission colour is `(1.0, 0.12, 0.01)` because ACES desaturates anything less saturated into cream |
| Glass | unchanged coated cover glass, coat 1.0 / rough 0.02 | keeps the room reflection |

Both rings are separate meshes (`FRAME_T/B/L/R`, `HALO_T/B/L/R`) so the 5 mm
border is real geometry with real depth, not a CSS outline. Corner bars are cut
to meet exactly, so there are no bracket ends.

**The LCD is bigger and brighter.**

- The glass went from 0.200 m to **0.224 m** wide (+12%); bezel, frame, spill
  ring and canvas all derive from that constant, so the border grew with it.
  33 mm of wood remains on each side and the keys are unaffected.
- Ink moved from `rgb(255,195,94)` to `rgb(255,210,118)` with a 3 px shadow-blur
  glow; a faint amber backlight wash sits behind the text so the panel reads as
  lit rather than as a black rectangle.
- The hotspot keeps a fixed CSS size in model-viewer, so the overlay is scaled
  from the projected key spacing (the keys are exactly 0.216 m apart):
  `pxPerM = keySpanPx / 0.216`, then `width = 0.224 × pxPerM`. At the 300 px
  card that is 178 px wide; at the 620 px close-up it grows with the model.

**Wood got pores and a veneer split.** The albedo now carries a high-frequency
pore layer along the grain (±0.052), and the two halves of the panel are tinted
apart by 6% like a book-matched veneer. The normal map is derived at strength
3.1, roughness spans 0.18–0.62 (pores rough, figure glossy), and the clearcoat
is 0.45 / 0.04.

**The studio HDR gained two grazing rims** at ±72° azimuth, 104° elevation,
intensity 5.0 and 3.5. They are deliberately steep and low: they put a specular
line on the bezel, frame and key edges without lifting the wood.

Measured on 1 236 × 620 captures (2× DPR, same camera, same exposure):

| Metric | Value |
|---|---|
| Asset | `player-body-v2.glb`, 2.95 MB, 5 560 tris, 9 materials, 3 packed PNGs |
| Pixels changed vs full stack | 37 365 of 766 320 (4.9%), max Δ225 |
| Changed bbox | x 155–1 083, y 79–529 (the whole device, not one region) |
| Border contrast at the card | frame band mean 233 / 209 / 147 next to wood at 70 / 61 / 38 |
| LCD amber pixels | 1 258 in the 200 × 50 backing, all inside the glass rect |
| Variants board | 9 viewers, all loaded |

Cost: still three 1 024 × 512 PNGs. Dropping them to 512 × 256 brings v2 to
roughly 800 KB — the version that should actually ship into the widget.

![The embeddable card at 300 × 150](artifacts/music-widget-designs/22-walkman-embed-300.png)

![Same page at 620 × 340](artifacts/music-widget-designs/23-walkman-embed-620.png)

**It is already embeddable.** `static/walkman-player.html` is the v2 card on its
own: it fills whatever container it is given (aspect 2:1, minimum 150px tall),
draws the LCD overlay scaled from the projected key spacing, and exposes both a
JS API and a `postMessage` channel, so the canvas can drive it without touching
the widget code yet.

```html
<iframe src="http://localhost:8910/static/walkman-player.html?title=Buckethead%20%E2%80%94%20Soothsayer&duration=242&position=12"
        style="width:300px;height:150px;border:0;border-radius:16px"
        sandbox="allow-scripts" loading="lazy"></iframe>
```

```js
// from the canvas, once the iframe is loaded
frame.contentWindow.postMessage({ type: 'walkman', setTrack: {
  title: 'Aphex Twin — Xtal', duration: 293, position: 42, playing: true,
  queue: [{ title: 'Cola', artist: 'Autechre' }] } }, '*');

// the card posts back on every key press
addEventListener('message', e => {
  if (e.data?.type === 'walkman') console.log(e.data.action, e.data.state);
});
```

Keys map to `prev · play · stop · next · queue`; the screen click opens the
queue overlay; `window.walkmanPlayer.state` is readable and `press(i)` is
callable. Query params: `title`, `duration`, `position`, `paused=1`, `queue=1`.

One server change was needed: a sandboxed iframe has an opaque origin, so the
hub now sends `Access-Control-Allow-Origin: *` on every response
(`doc-client/app/main.py`, `_allow_embed` middleware) — otherwise the GLB fetch
is blocked and the card renders empty.

Live: **http://localhost:8910/static/walkman-player.html** (the embeddable card),
**http://localhost:8910/static/walkman-3d.html** (the player),
**http://localhost:8910/static/walkman-3d-variants.html** (eight variants plus
v2 in the grid and as the hero).

**v2.1 — the LCD fits, stays still, and the light stays on.** Three things the
first v2 still got wrong, and what fixed each.

| Symptom | Root cause | Fix |
|---|---|---|
| Title clipped at both ends | the marquee never stopped: a 23-character title is 262 px at 2× scale against a 196 px canvas, so it scrolled for ever | the LCD is now laid out to fit, not to scroll |
| Picture "shifting" on every click | not the model — the camera never moved (theta/phi/radius unchanged after real 80 px drags); it was the marquee advancing, plus one extra `drawScreen()` per click | single static draw path, plus `disable-rotate` on the card |
| Button light "brief" or invisible | `.hs-key` computed `position: static`, so the absolutely-positioned pseudo-element anchored to a distant ancestor and painted *behind* the WebGL canvas — measured 0 changed pixels at 0 / 300 / 1200 ms. `lit` was also stripped after 700 ms | `position: relative` + a blurred light that stays while `.on` is set |
| Light sat *on* the key face | a radial centred on the key box paints over the 3D key, which reads as a glowing sticker, not as backlight | the light is now clipped to below the key's bottom edge |
| LCD looks washed out | model-viewer fades hotspots to `opacity: .25` unless `--min-hotspot-opacity` is set; `.hs-screen` never set it | `--min-hotspot-opacity: 1` — ink renders at full 255 / 210 / 118 instead of a 25% ghost |

**Layout.** The backing is **4× the projected CSS size** (712 × 160 at the
300 px card, 1 472 × 328 at 620), so the canvas is supersampled and never
resampled from a fixed bitmap. The glyph scale is adaptive:

```
s = clamp(floor((W - 20) / (6 × longestString)), 2, 8)   // also ≤ floor(H / 29)
rows: 7s title · 2s · 7s time/state · 2s · 3s bar · 1s · 7s strip = 29s
```

At the 300 px card that is **s = 5**, a 8.75 CSS px cap — about 20% smaller than
the old 11 px, and the whole title fits with 5 px to spare. At 620 it is
**s = 8** (14 px cap) and the text fills 85% of the glass. Ellipsis only appears
past roughly 58 characters; nothing scrolls.

**Selection.** Only `play` holds a steady light: it stays lit for as long as
the track is playing and clears on pause or stop. Every other key —
`prev`, `stop`, `next`, `queue` — **flashes**: it lights on press, holds for
900 ms, then fades out over 300 ms. When a flash ends the steady state comes
back, so skipping tracks lights `next` for a moment and then returns the light
to `play`, because the deck is still running. `aria-pressed` mirrors whichever
key currently carries the light.

**The light is under the key — in depth, not below it on screen.** Two earlier
attempts read "under the button" as a place on the panel and painted a pool of
light *below* the key row, then, when that was cut off, as a band starting at
the key's bottom edge. Both are wrong: they put light *beside* the key. What an
analogue device does is light the key **from behind**, so the key body blocks
the light and you only see it where the key does not cover it — a halo hugging
the key's silhouette on all four sides, with the key face itself still dark.

That is exactly what `box-shadow` does: it paints outside the border box and
never under the element's own area. So each key now has two zero-size anchor
hotspots on its front-face corners in 3D (`x ∓ 0.024`, `y ± h/2`, `z 0.021`,
play `0.023`); model-viewer projects them, which gives the key's **projected**
rectangle at any orbit or zoom — the rectangle the extruded key actually
occupies, not the flat CSS hotspot box. A `.lamp` div is laid on that rectangle
and lit with `box-shadow: 0 0 0 1px rgba(255,214,136,.5) ,
0 0 7px 1px rgba(255,176,62,.3) , 0 0 17px 4px rgba(255,146,36,.13)`: a 1 px
seam line, then two falloffs onto the wood. The key face is inside the box, so
it is never painted; the light only shows around it. The intensities were
lowered from the first version (`.9 / .62 / .30` at 7 px and 20 px spread) —
the old halo read as a glowing sticker; the softer one reads as light escaping
a seam.

Measured on a 300 × 150 card at 2× DPR, same LCD frame, lamp toggled in the
DOM so nothing else changes: the **interior of the key face is 0 changed px,
max Δ 0**; the only pixels that change inside the lamp's bounding rectangle are
the four rounded-corner triangles (83 px), which are outside the rounded
border box and therefore legitimately painted by `box-shadow`. The halo covers
1 773 px of the band above the key, 1 793 below, 730 left and 718 right, with a
peak Δ of 341 at the seam and a mean of 19–25 per band — roughly a third dimmer
than the first version, which peaked near Δ 520.

### The queue key pops a library drawer down under the keys

The queue used to be a read-only "Up next" list. Pressing the queue key now
opens a **collapsible drawer below the device**: search field, shuffle toggle,
close button, and every track as a clickable row. It never covers the player —
the card grows downward instead, so the 3D device and all five keys stay visible
and clickable.

![The drawer open under the keys: search filtered to Buckethead, shuffle on, Colma selected](artifacts/music-widget-designs/28-walkman-player-library.png)

![The same card with the drawer closed, play lit](artifacts/music-widget-designs/29-walkman-player-play-lit.png)

- **Layout.** `#card` is a flex column: `#stage` (the model-viewer plus the five
  lamps) on top, `#queue` below. Closed, the drawer is `height:0` with
  `pointer-events:none`, so the card is exactly **300 × 150**; opening animates
  it to 132 px of content (148 px with padding) over 240 ms, and the card
  becomes 297 px tall. In standalone the card's top edge is pinned at
  `max(24px, 50vh - 75px)`, so the device does not move — the drawer grows
  downward under the keys.
- **Three ways out**, which is what the old overlay lacked: the queue key
  toggles it (and is always visible now), the `×` button in the drawer bar
  closes it, and `Esc` closes it anywhere.
- **Search** filters by artist *or* title as you type. `Enter` plays the first
  match, `Esc` closes, `/` opens the drawer and focuses the field.
- **Shuffle** (`SHUF`) toggles `state.shuffle` and tints amber while on; with it
  on, `next` picks a random other track.
- **Rows** show the index (or `>` for the playing track), the title and the
  artist. Clicking a row loads that track: title and duration from the library,
  position 0, playback starts, and `play` takes the steady light. The row tints
  amber and the drawer stays open so you can pick again.
- **Embeds.** A fixed-height container cannot grow, so there the drawer takes at
  most `min(132px, 50%)` of the card and the stage shrinks to make room. At
  620 × 310 the stage keeps 163 px and the drawer gets 147 px; at 300 × 150 the
  split is 60 / 90, which keeps every key and the `×` on screen.
- `window.walkmanPlayer` gained `setLibrary(list)`, `select(i)`,
  `openQueue()`, `closeQueue()` and `toggleShuffle()`; `postMessage` accepts
  `{type:'walkman', setLibrary:[…]}`, `select`, `press`, `openQueue` and emits
  `{action:'toggle', queueOpen}` plus `select` / `shuffle`.
- `?driven=1` hands the clock to the host: the card stops advancing
  `position`, so it never ends a track on its own while an embedded player owns
  the audio. `setTrack` only moves the light when `playing` actually changed,
  so a host that re-pushes state four times a second cannot cancel a key's
  flash.

Verified in the browser: closed card 150 px, drawer 0 px, card top unchanged at
275 px; opening gives card 297 px with the stage still 150 px and the drawer
starting exactly at the stage's bottom edge (150 px) — the device is never
overlapped; `×`, `Esc` and the queue key each close it and return the light to
`play`. Search `aphex` → 1 row, `Boards` → 1 row, `zzz` → the empty state;
clicking the `burial` row loads "Burial — Archangel" at position 0 with `play`
lit. Real mouse clicks at widget size behave the same.

![The card at 300 × 150, play lit from under the key](artifacts/music-widget-designs/24-walkman-player-300.png)

![The same card at 620 × 340](artifacts/music-widget-designs/25-walkman-player-620.png)

![Key row: nothing lit, then play lit from underneath two seconds later](artifacts/music-widget-designs/26-walkman-player-glow.png)

![Still working inside a sandboxed cross-origin iframe](artifacts/music-widget-designs/27-walkman-player-sandbox.png)

Measured:

| Check | Result |
|---|---|
| Title ink bbox, 300 px card | x 10–694 of 712 backing px — 5 px margin right, 2.5 CSS px |
| Same bbox over 60 frames | one distinct value; identical after 5 clicks |
| Glyphs touching the glass border | 0 columns, 0 rows of border ink |
| LCD opacity | `1` (was `0.25`) |
| Camera after an 80 px drag | theta 0, phi 1.5708, radius 0.55 — unchanged |
| Light inside the key rectangle (the key face) | 4 of 3 168 px, max Δ27 — edge antialiasing only |
| Light on the key's four edges | top 1 380/1 380, bottom 1 380/1 380, left 840/840, right 840/840, max Δ≈225 |
| Demo page, same check | 4 px inside, 1 380/1 380 top and bottom, 750/750 left and right |
| Sandboxed iframe | renders, 2 997 amber px, LCD at full brightness |
| Asset | `player-body-v2-lite.glb` — same geometry and materials at 0.224 m glass, maps at 512 × 256, **892 KB** (was 2.95 MB) |

The same layout, hotspot sizing and glow code runs in `walkman-3d.html`, so the
demo page and the embeddable card cannot drift apart. The flash rule is shared
too: on the demo page `play` holds its light, and `prev`/`stop`/`next`/`queue`
light for 900 ms and hand the light back to `play` while it is playing (verified:
next lit at 0 ms, back to play at 1 400 ms; stop lit at 0 ms, all dark at
1 400 ms). The demo page has no queue panel, so search and shuffle live in the
card only.

**Opening the card directly shows it at widget size.** `walkman-player.html`
fills its container when embedded, but when it is the top-level page it adds
`html.standalone` and pins the card to **300 × 150** in the middle of the
window, with a caption saying so. Before that, opening it in a full window blew
the device up to 1280 × 640 — every pixel re-rendered at 4× the size, which is
where the lag came from, and the fixed-size hotspots no longer lined up with
the giant keys.

### A3 · Pixel forest — 150px, the device grown into a tree

![The player inside a pixel forest](artifacts/music-widget-designs/13-pixel-forest.png)

![Clicking a rune lights it](artifacts/music-widget-designs/14-pixel-forest-glow.png)

![The same scene at 2×](artifacts/music-widget-designs/15-pixel-forest-wide.png)

The same 300 × 150 device, but the Blender scene grows a tree around it and the
whole frame is pushed through a pixel filter.

- Source: `apps/doc-client/tools/walkman_keys/build_forest.py` → 
  `static/models/forest.glb` — 240 KB, 3 484 triangles, 6 materials, no textures.
- Scene: a mossy stone slab (the player), a five-key row with **glowing runes**
  carved into the front face, a trunk that splits into curling branches, and
  scattered icosphere leaves. The tree sways with the beat and the branches
  wrap the device rather than sitting behind it.
- Pixel filter: rendered at half resolution (150 × 75 backing for the 300 px
  card, 310 × 155 for the 620 px view) and upsampled with
  `image-rendering: pixelated`. The shader converts every pixel to luminance,
  quantises it to a six-step green ramp with a 4 × 4 Bayer matrix, then pushes
  green and blue per band on a slow sine — the palette breathes, the geometry
  does not.
- Interaction: the five keys are DOM buttons projected onto the 3D keys every
  frame, so they track the sway. Pressing one fires the key's rune to full
  emission (rest 0.10 → 1.7), holds the sway while it is lit so the glow is
  legible, and shows a green halo ring around the button.
- Screen: the same DOM pixel canvas, anchored to a projected screen rectangle,
  drawn with the 5 × 7 bitmap font — artist and title on one line, 43-cell
  progress bar, `SIDE A 1987` strip.
- Live: **http://localhost:8910/static/pixel-forest.html**

Cost: one 240 KB GLB and one WebGL context, the same budget as A2. This one is
the "trippy" end of the set — it is the only direction that changes with the
music rather than only displaying it.

## Wired into the canvas widget (2026-10-31)

`render_mini_music_player` (`app/widgets/factory.py`) no longer draws its own
bento shell. It **embeds the 3D walkman card**, and since 2026-10-31 the card
carries every control itself — the template is the iframe and its error toast,
nothing else.

![The widget is the device: boxless, six engraved keys under the LCD](artifacts/music-widget-designs/32-hn-widget-boxless.png)

![The library drawer under the keys](artifacts/music-widget-designs/33-hn-widget-drawer.png)

**Why an iframe, not inline markup.** The card is one document with its own
`model-viewer`, LCD canvas and projection code. Re-implementing it inside the
Alpine template would create a second copy that drifts. The widget instead
points an iframe at it and speaks a small protocol.

**Same-origin on purpose.** The card is authored in doc-client but the
html-notes container is deployed on a different host from the hub, so a URL
derived from the browser's hostname pointed at the wrong box. The app therefore
serves its own copy at `/static/walkman/player.html` (with `model-viewer.min.js`,
`player-body-v3-lite.glb` and `studio.hdr` beside it, 1.5 MB), kept in sync by
`scripts/sync-walkman-card.sh`, and `WALKMAN_CARD_URL` defaults to that path.
One source of truth for the card's code, no cross-app URL and no CORS.

**The card is a view; the widget owns playback.** `?driven=1` stops the card's
internal clock, so it never advances or ends a track on its own — the app's
`<audio>` element is the only source of truth. `?paused=1` starts it dark until
the first state arrives.

Widget → card (`postMessage`, every 250 ms when anything changed):

| message | meaning |
|---|---|
| `setTrack {title, duration, position, playing, shuffle}` | LCD text, time row, the light on `play` |
| `setLibrary [{artist,title,duration}]` | what the drawer lists and searches |
| `openQueue: true/false` | open or close the drawer from the top bar |

Card → widget:

| action | widget does |
|---|---|
| `prev` / `next` | `prevTrack()` / `nextTrack()` (shuffle-aware) |
| `play` | `playPause()` |
| `stop` | pause, seek 0, clear the light |
| `select {index}` | `playAt(index)` — or `playHistoryTrack()` when the drawer lists history |
| `shuffle` | `toggleShuffle()` (reorders the unplayed tail) |
| `toggle {queueOpen}` | grows/shrinks the widget: **194 → 342 px** before the drawer, now **284 → 432 px** with the taller deck |
| `ended` | `nextTrack({auto:true})` |

**One identity on the LCD.** YouTube tracks carry the *channel* in `artist`
("Music Video Vault", `morrisoncoursevids`, "… - Topic") and often repeat the
artist inside the title, which is exactly the redundancy the first audit found.
`cleanTrack()` strips `- Topic`, bracketed noise (`(Official Video)`, `[HD]`,
`(Remastered 1959)`, `[Restored]`), drops the artist when it is a channel
(handle-shaped, or ending in vault/topic/records/official/channel) and prints
the artist only when the title does not already start with it. Measured on a
live jazz mix: `morrisoncoursevids | Duke Ellington, "Take the A Train"` →
`Duke Ellington, "Take the A Train"`; `Music Video Vault | Chet Baker - Time
After Time [ Restored]` → `Chet Baker - Time After Time`; `Wayne Shorter |
Footprints (Remastered)` → `Wayne Shorter — Footprints`.

**History reuses the drawer.** The history button swaps the drawer's library to
the recent-history list (`setLibrary` + `openQueue`) instead of building a
second panel; a row click then calls `playHistoryTrack()`.

**Sizes.** The widget is 632 px wide in a 3-column bento, so the deck gets a
246 px-tall stage (device ≈ 384 px wide) rather than the 150 px standalone size —
at 150 the deck sat small in a wide frame. Closed: **284 px** (30 px chip row +
246 px deck); drawer open: **432 px** (+148 px drawer). The LCD hotspot scales
with the deck, and its backing is 4× its projected CSS size, so the ink stays
inside the glass at every size.

### The deck floats: no frame, and the frame's controls move onto the player

The widget used to sit inside the canvas's holographic glass frame — a fill, a
1 px border, a 14 px radius, a drop shadow, a backdrop blur and a luminous top
hairline, all `!important` in `index.css`. Around a 3D object that reads as a
box drawn around a device that already has its own body.

The frame is gone. `.widget-container.music-widget-root` opts out of every one
of those properties with `!important` of its own (plus `::after { content: none }`
for the hairline and `overflow: visible` so the deck's light is never clipped),
and the card itself became transparent — `#card` lost its radial-gradient fill,
its 16 px radius and its shadow, so the model-viewer canvas sits straight on the
canvas behind it.

**The first box was the iframe.** `index.css` styles every
`.widget-container iframe` as an inset panel — `background: #02060c`,
`border: 1px solid rgba(127, 212, 255, .25)`, `border-radius: 4px` and a
`0 0 18px rgba(79, 195, 247, .18)` glow ("Iframes sit inside a glowing frame").
That opaque rectangle was what the user saw around the player, no matter how
transparent the widget and the card were. The same-origin card paints its own
device, so the iframe must be a window, not a panel: `.widget-container.music-widget-root
iframe` now sets `background/border/border-radius/box-shadow` to
`transparent/0/0/none !important`. Measured after that fix: widget and iframe
both `rgba(0,0,0,0)` fill, `0px` border, `none` shadow.

**A second box survived, and it was Chrome.** With widget, iframe and card all
computing `rgba(0,0,0,0)`, a screenshot of the widget still came back an opaque
`rgb(18,18,18)` rectangle with the canvas colour around it — and it stayed grey
even when the page behind it was painted magenta, so it was painted *inside* the
iframe. The model-viewer canvas read `alpha 0` at its corners, so the paint was
not the 3D view. The cause was the card's `:root { color-scheme: dark }`: Chrome
paints the document's **base background** — `#121212` for a dark color scheme —
behind content whose root background is transparent, and an iframe composites
that base. The card's transparent root therefore became a grey panel. Setting
`color-scheme: normal` inside the iframe replaced the rectangle with the canvas
gradient immediately. The fix scopes the dark scheme to the card's standalone
mode (`html.standalone`) and styles the drawer's scrollbar explicitly so it stays
dark in both modes.

**The bezel is the widget's edge.** With the box gone the deck still floated
inside the widget with margins, because the widget was 716 × 284 (aspect 2.52)
while the model is 0.300 × 0.150 m (2.0). Two changes put the device's own silver
bezel on the widget's boundary: the closed height is now the width over two
(`walkmanBaseH` comes from a `ResizeObserver` on the widget root, so 716 → 358,
and a size the user drags with the corner grip still wins), and the card's camera
sits at 0.4275 m instead of 0.55 m, so the 20° field of view spans exactly
0.150 m — the device fills 99.5% of the frame. Measured at 716 × 358: 308
background pixels (0.08%, the shell's rounded corners and antialiasing), and
every mid-edge pixel is metal, not canvas.

**The controls are on the device, not around it.** Floating chips above the
deck were the first version of this — but a row of buttons over a 3D object is
still a bar, and a bar is still a box. So the six controls became real keys:
Blender cut them into the front panel, engraved, in the free band between the
LCD and the transport row.

| control | on the deck | emits |
|---|---|---|
| like / dislike | key 1 / key 2, thumb silhouette mirrored vertically | `thumb_up` / `thumb_down` |
| history | key 3, clock dial with hands | `history` |
| mute | key 4, speaker cone plus a cross | `mute` |
| open in Music Player | key 5, box with an escaping arrow | `open` |
| close widget | key 6, X | `close` → `WidgetManager.dismiss()` |
| corner resize grip | the same `WidgetResizer` handle, an amber L bracket on the deck | — |

![The six secondary keys — like, dislike, history, mute, open, close](artifacts/music-widget-designs/35-hn-aux-keys.png)

Geometry: `AUX_W = 0.018`, `AUX_H = 0.011`, `AUX_GAP = 0.004` at `z = −0.014`,
one row of 128 mm centred above play/stop, between the LCD's lower edge
(`z = 0.008`) and the transport keys' top (`z = −0.036`). That is **half the
area** of the first 26 × 16 mm caps (416 → 198 mm²) and 37% of a transport key's
width, so they read as secondary keys instead of a second transport row.
Measured in the widget: aux hotspot 44 × 27 px against a transport key's
118 × 64 px.

The thumbs are the real icon, not a hand-built silhouette. Two attempts at
drawing a hand in the boolean cutter produced shapes that read as "random
shapes" at 27 px, so the groove is now the Font Awesome Free 6.5.2 `thumbs-up`
solid path (CC BY 4.0), flattened to two polygons — the hand and the cuff bar —
and placed in icon space (metres, y up, centred, 0.0105 × 0.0092 m). The SVG
path mixes absolute and relative `M/L/H/V/C/Z` commands, so a small flattener
walks it with ten samples per cubic and emits the vertices as literals in
`build_body.py`; winding is normalised counter-clockwise. `thumb_down` is the
same polygon set mirrored in y with the point order reversed, so the winding
stays outward. The booleans are exact (`ICON_CUT = 0.0009`) with a dark inlay,
so the icons are grooves, not raised bumps. `player-body-v3.glb` is 3,106,456 B
/ 9 materials / 6 `AUX_*` nodes; the `lite` build is 1,049,488 B.

Verified from the render, not by eye: the groove mask (`luminance < 20`) of the
like key shows the thumb standing above the fist with the cuff bar on the left,
the dislike key shows the same mask flipped vertically (thumb below the fist,
cuff still left), and both are centred in their caps to within 2 px. At the
358 px deck the icon is 26 × 22 CSS px inside a 44 × 27 px key.

Each key has its own hotspot (`hotspot-x0 … x5`) sized from the projected cap
(`0.018 × 0.011` m → 44 × 27 px at the 358 px deck, against a transport key's
118 × 64 px — 37% × 42%, half the area) and its own `.lamp`, so the amber
rim-halo lights the panel around the key that was pressed. Only `play` holds its
light; an aux key flashes for 450 ms. `thumb` and `mute` are *state*, not
flashes: the lamp stays on while the track is liked/disliked and while the app is
muted, and the card's `setTrack` re-syncs both when the host pushes a new track.
Verified with real mouse clicks through the iframe at 716 × 358:

| click | widget state | height |
|---|---|---|
| like | `thumbState: 'up'` | 358 |
| dislike | `thumbState: 'down'` | 358 |
| mute | `isMuted: true`, second click `false` | 358 |
| history | drawer opens in `history` mode | 506 |
| close | widget removed, id recorded in `dismissed_widgets` | — |

The widget is now **only the iframe**: `buttons: 0`, `close-widget-btn: 0`,
`h: 358` at the 716 px `col-span-2` width. `renderDynamicComponents()` skips its
automatic close-button pass for `.music-widget-root`, so a rendered widget cannot
grow a chip back.

**Resize is the canvas's resize.** The widget no longer has a frame to drag, so
the grip is the existing `.widget-resize-handle` that `WidgetResizer.decorate()`
appends to every widget. It writes `{cols, height}` to `widget_sizes` in
localStorage exactly as before; the widget reads that size back as
`walkmanBaseH` and renders `walkmanHeight = walkmanBaseH + (drawer ? 148 : 0)`,
so the drawer rides on top of whatever size the grip chose instead of fighting
it. A `window resize` listener and a `ResizeObserver` on the root adopt the
stored size after every drag, and fall back to `width / 2` when the user has
never dragged — which is what keeps the bezel on the widget's edge. Measured:
drag to 344 px stores `widget_sizes.wtest1 = {cols: 2, height: 344}`,
`walkmanBaseH` 344; opening the drawer then gives 492 and closing returns to
344. Undragged at `col-span-2` the height is 358 and the drawer gives 506.

### The LCD scrambles into the next title

Changing track does not swap text. `drawScreen()` keeps `lastLabel`; when the
label differs it starts a 620 ms dissolve from the old title to the new one, and
because the card already redraws every frame, the effect needs no extra timer.
Each 5×7 glyph cell decides per pixel, from a hash of (glyph, row, column,
frame): hold the old pixel, flip to the new one, or sparkle a random glyph pixel
while the cell is mid-transition. Progress is staggered left to right, so the
head of the line settles while the tail is still churning.

![Mid-dissolve: the head of the line has resolved, the tail is still scrambling](artifacts/music-widget-designs/34-hn-widget-scramble.png)

Measured on the embedded widget with a real click on `next`: the title changed
once, the canvas signature ramped through frames that matched **neither** the
old nor the new title (band-sum distance 13 893 → 8 311 vs the old frame, never
equal to the final), and settled 620–670 ms later — after which the signature
was bit-identical frame to frame.

**One owner of the queue.** A driven card must not guess the next track: it used
to commit its own `library[i]` title, then the host corrected it 80 ms later,
which printed a stale title for a frame and restarted the scramble. In
`driven=1` the card now only lights `play`, emits `select`, and lets the host
push the authoritative track; `prev`/`next` likewise defer to the host's
`prevTrack()`/`nextTrack()` instead of stepping its own stale copy.

**Verified live** (app on 8035, real mouse through the iframe): queue key →
drawer opens, widget 358 → 506, 100 rows; clicking row 3 plays that track
(`currentIndex` 2) and the LCD switches to `Chet Baker - Time After Time`;
`×` closes back to 358; `stop` pauses and clears the light; `play` resumes;
`SHUF` flips the app's shuffle and reorders the queue; history shows 4 rows and
selecting one plays it. Unit tests: `tests/test_widgets.py` asserts the iframe,
`walkmanSrc`, `onWalkmanLoad`, `cardUrl` and `walkmanHeight` in the template, and
now that **no `<button>`, no `close-widget-btn` and no thumb markup** come with
it — 71 passed.

## Side by side

| Layout | Width | Height | vs 184px | Queue | Art | Identity | Palette |
|---|---|---|---|---|---|---|---|
| Current | 620px | 184px | — | Grows to 384px, re-flows masonry | 40px gradient tile | 2 lines + chip (artist 3×) | purple / fuchsia |
| Bento Rail | 620px | 72px | −112px (61%) | Portal panel below card | 48px tile | 1 line | HUD cyan |
| Bento 2×2 | 620px | 168px | −16px | Inset overlay (5 rows) | 132px tile | up to 2 lines | violet glass |
| Vinyl Dial | 620px | 120px | −64px | Inset overlay (3 rows) | 92px disc | eyebrow + title | warm brass |
| Cassette Deck | 620px | 128px | −56px | Inset LCD overlay | 40px reel | 1 LCD line | olive / amber |
| Swiss Editorial | 620px | 96px | −88px | Inset overlay (3 rows) | none | kicker + title | ink / vermillion |
| Walkman | 300px | 100px | −84px | Inset overlay (3 rows) | spool glyph | 1 LCD line | aluminium / amber |
| Walkman 3D | 300px | 150px | −34px | Inset overlay (3 rows) | spool glyph | 1 LCD line | aluminium / amber + chrome |
| Pixel Forest | 300px | 150px | −34px | Inset overlay (3 rows) | none | 1 pixel line | forest green / black |
| Walkman 3D full stack | 300px | 150px | −34px | Inset overlay (3 rows) | spool glyph | 1 LCD line | walnut + chrome + studio HDR |
| Walkman 3D **v2** | 300px | 150px | −34px | Inset overlay (3 rows) | spool glyph | 1 LCD line, 2× glyphs | walnut + dark-metal frame + amber spill |
| Clear Neon | 300px | 100px | −84px | Inset overlay (3 rows) | 2 spools | 1 LCD line | cyan / magenta |
| PS2 Pixel | 300px | 100px | −84px | Inset overlay (3 rows) | none | 1 pixel line | dither blue |
| Buick ’93 | 300px | 118px | −70px | Inset overlay (3 rows) | none | 1 VFD line | wood / VFD green |

The half-width set is the answer to the gap problem: at 300px each card holds
two rows (readout + keys) with 3–6px padding, so nothing is padding for its own
sake. The full-width set still exists for anyone who wants art on the canvas.

## Recommendation

**Cassette, at half width.** Of the four half-width variants, **Walkman** is the
safest: it keeps the cassette read, has the most legible amber LCD, uses two
rows and no art fetch, and its keys are the largest targets at 300px. **Clear
Neon** is the pick if the canvas wants colour; **PS2 Pixel** if it wants the
retro-game signal look; **Buick ’93** if it wants period-car character at the
cost of 18px and a woodgrain palette.

Bento Rail remains the option if a full-width, art-less status bar is wanted;
Bento 2×2 stays the art-forward pick. Vinyl and Swiss are still on the table.

## What would change in code

- `app/widgets/factory.py` → `render_mini_music_player`: the chosen layout's
  markup, plus the identity helper.
- `app/static/index.js` (~line 3540, the self-heal twin): the same markup,
  structurally identical. Both copies must move together.
- The `:class="showQueue ? 'h-[384px]' : 'h-[184px]'"` binding: replaced by one
  fixed height per layout.
- `app/static/index.css` (`.music-widget-root` block): marquee/typography
  rules, plus container-query collapses.
- `widgets.js` → `asTrack`: keep `id` so the thumbnail URL can be derived;
  optionally add `thumb` to the track shape.
- For the retro set: a music-specific shell opt-out, because
  `.widget-container` carries `background-image` and `border-radius` with
  `!important` and would overwrite the metal, plastic, woodgrain, and dither
  surfaces.

## Verification of the mockups

- Rendered heights in Chromium: 184 / 72 / 168 / 120 / 128 / 96px for the
  full-width set, and 100 / 100 / 100 / 118px for the half-width set; content
  `scrollHeight` never exceeds the declared height at 620px or at 300px.
- No element escapes its card at either width (geometry check over every
  descendant), and the grid tiles do not overlap.
- Queue rows visible: rail 4 (portal), grid 5, vinyl 3, tape 4, swiss 3;
  walkman / neon / ps2 3 each, buick 3.
- The PS2 cell bar and the neon LED meter track progress (8 of 16 cells and 6 of
  12 segments lit at ~50%); the Buick needle transform matches −90° + 1.8° per
  percent.
- The 3D player loads from one GLB: 2 216 triangles, 7 materials, wood texture
  packed (mean RGB 64/34/16), icons centred on every key (x −0.108…0.108) and
  recessed 0.2–0.9 mm inside the face; the screen canvas lands inside the
  hotspot box with 1 027 amber pixels; a key click lights 2 475 warm pixels in
  a halo around that key only.
- The pixel forest renders: 4 258 of 11 250 backing pixels lit, 32–37% of the
  card green-dominant with no cyan cast after the palette was rebalanced; the
  five runes sit on the key row and a press moves 44 pixels by up to 135 green
  levels inside that row (measured on a frozen frame, so the delta is the glow
  and not the sway).
- The sway holds while a key is lit, so the glow is not smeared by motion.
- Each screenshot contains its design's accent colour, and every pair differs
  (mean RGB delta 39.6–88.0), so the layouts are genuinely different pixels,
  not recolours of one.
- Long-title state: each layout clips or marquees within its own box (track
  widths 370–445px inside a 300px card, page scroll width unchanged); the
  baseline replica demonstrates the raw-title overflow it has today.

## Open questions

1. **Which half-width skin** — Walkman, Clear Neon, PS2 Pixel, or Buick ’93?
2. **Art** — real YouTube thumbnail, or keep a glyph and skip the fetch? The
   half-width set currently fetches nothing.
3. **Queue** — inset overlay (all half-width designs), or a portal panel?
4. **Shell** — the retro set needs the music widget to opt out of the
   `!important` frame rules; confirm that is acceptable.
5. **3D player** — worth one WebGL context per widget, or keep the 3D only in
   the demo and ship the flat card? If it ships, ship **v2**: it is the only
   variant where the LCD border is visible at 300 px without cranking contrast.
   The question left is file size — 2.95 MB as built, or ~800 KB with the three
   maps cut to 512 × 256 and the normal map dropped.
6. **Pixel forest** — ship as the "music-reactive" skin (needs an audio
   analyser, not the synthesised beat), or keep it as a demo? It costs the same
   as A2 but only reads as trippy when it actually follows the track.
