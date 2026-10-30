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
chapter records the redundancy, the compaction rules, and five visual
directions for a bento-style replacement.

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

## Side by side

| Layout | Height | vs 184px | Queue | Art | Identity | Palette |
|---|---|---|---|---|---|---|
| Current | 184px | — | Grows to 384px, re-flows masonry | 40px gradient tile | 2 lines + chip (artist 3×) | purple / fuchsia |
| Bento Rail | 72px | −112px (61%) | Portal panel below card | 48px tile | 1 line | HUD cyan |
| Bento 2×2 | 168px | −16px | Inset overlay (5 rows) | 132px tile | up to 2 lines | violet glass |
| Vinyl Dial | 120px | −64px | Inset overlay (3 rows) | 92px disc | eyebrow + title | warm brass |
| Cassette Deck | 128px | −56px | Inset LCD overlay | 40px reel | 1 LCD line | olive / amber |
| Swiss Editorial | 96px | −88px | Inset overlay (3 rows) | none | kicker + title | ink / vermillion |

## Recommendation

**Bento Rail** as the default: it is the only option that beats the current card
by more than half, it keeps every control, and its queue costs zero layout
shift. **Bento 2×2** is the alternative if the art should stay prominent.
Vinyl and Cassette are the expressive picks if the canvas should have more
personality than a status bar; Swiss is the pick if the canvas should read as a
document.

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

## Verification of the mockups

- Rendered heights in Chromium: 184 / 72 / 168 / 120 / 128 / 96px; content
  `scrollHeight` never exceeds the declared height at 620px or at 300px.
- No element escapes its card at 620px or 300px (geometry check over every
  descendant), and the grid tiles do not overlap.
- Queue rows visible: rail 4 (portal), grid 5, vinyl 3, tape 4, swiss 3.
- Each screenshot contains its design's accent colour, and every pair of
  screenshots differs (mean RGB delta 39.6–88.0), so the five are genuinely
  different pixels, not recolours of one layout.
- Long-title state: each layout clips or marquees within its own box; the
  baseline replica demonstrates the raw-title overflow it has today.

## Open questions

1. **Mood** — quiet HUD status bar (rail), expressive hi-fi (vinyl / cassette),
   or print editorial (Swiss)?
2. **Art** — real YouTube thumbnail, or keep a gradient glyph and skip the
   fetch?
3. **Queue** — floating portal panel, or inset overlay?
4. **Shell** — stay inside the forced glass frame, or let the music widget opt
   out of the `!important` frame rules for its own shell?
