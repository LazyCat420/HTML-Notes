---
part: Music player
status: in-progress
updated: 2026-10-29
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
- Asset: `static/models/player-body.glb`, 281 KB, 2 216 triangles, 7 materials,
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
5. **3D player** — worth one WebGL context and a 281 KB request per widget, or
   keep the 3D only in the demo and ship the flat card?
