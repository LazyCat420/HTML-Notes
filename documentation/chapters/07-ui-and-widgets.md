---
part: Canvas and widgets
type: spec
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:app/widgets/factory.py, html-notes:app/presentation/widgets/catalog.py, html-notes:app/static/index.html, html-notes:app/static/index.js, html-notes:app/static/index.css, html-notes:app/static/js/widgets.js, html-notes:app/static/crt/player.html
---

# UI and widgets

## The canvas

The canvas is an Alpine-driven grid in `app/static/index.html`. Every widget is a
card in that grid; the canvas manager owns its column span and its serialized
state. The client mounts widgets, wires their actions and keeps transient UI state.

The widget lifecycle is:

1. **Render** — the server sends a widget frame with its HTML and `data-hn-config`.
2. **Mount** — `app/static/js/widgets.js` initialises the widget's Alpine component.
3. **Act** — a click, resize or message updates the widget locally and posts the
   action to the server when the server owns the state.
4. **Persist** — the server updates the canvas row.
5. **Repair** — on reload, the self-heal path rebuilds a widget whose Alpine wiring
   is missing, from `data-hn-config`.

## Widget factory

`app/widgets/factory.py` is the only place that produces widget HTML. Each render
function:

- takes typed data (never raw model prose as HTML);
- escapes text and attributes;
- stamps `data-hn-config` as strict JSON next to `x-data`;
- uses classes that exist in the compiled Tailwind build.

`app/presentation/widgets/catalog.py` is the registry of widget types. A type that
is not in the catalog cannot be requested by the model.

## HUD theme and boxless devices

`app/static/hud-theme.css` draws every widget as a holographic panel: an octagon
cut with `clip-path`, a 2 px hover lift, and cyan L-shaped corner brackets on
`::before`. Two widgets are real objects, not panels, so the `BOXLESS WIDGETS` rule
in `app/static/index.css` opts `.widget-container.youtube-player` and the Walkman's
`.widget-container.music-widget-root` out of all of it:

- fill, border, radius, shadow and backdrop filter are cleared with `!important`
  (the frame rule is itself `!important`);
- `clip-path: none !important` and `transform: none !important`, so the octagon and
  the hover lift cannot slice or slide the device;
- `::before` and `::after` are removed, so brackets and the update light-sweep do
  not float over the device;
- the same-origin card iframe is transparent with no border.

## Tailwind is compiled at build time

Nothing Tailwind runs in the browser. `app/static/tailwind.css` (about 47 KB
minified) is built by the Tailwind CLI (`tailwindcss@3.4.17`, the same v3 engine as
the old CDN, stock config, Preflight included):

- in the Docker image, by the `css` stage in `Dockerfile`;
- locally, by `npm run build:css` (also before `npm run dev` / `npm start`).

`tailwind.css` is linked as the **last** stylesheet in `<head>`, so the cascade order
is unchanged, and it is cache-busted like `index.css` (`_CACHE_BUSTED_ASSETS`).
`tailwind.config.js` scans `app/**/*.py` and `app/static/**/*.{js,html}`; the
safelist (`tailwind.safelist.json`) holds 771 class tokens mined from canvases in
the live `notes.db` by `scripts/mine_tailwind_safelist.py`, covering classes older
templates and agent cards used that the current source no longer has.

A class assembled at runtime from pieces (`f"text-{tone}-400"`) is caught by
`tests/test_tailwind_build.py`; spell it out in source or add it to the safelist.

## Music widget: compact bento layouts

The canvas mini player (`render_mini_music_player`) prints the same artist three
times for an ordinary artist ask, and the card was pinned to a fixed 184 px that
doubled to 384 px when the queue opened. The compact design work replaced that with
bento layouts and a single identity line:

| Direction | Height | Change |
|---|---|---|
| Bento Rail | 72 px | −112 px, 61% smaller |
| Bento 2×2 | 168 px | −16 px, −216 px with the queue open |
| Vinyl Dial | 120 px | −64 px |
| Cassette Deck | 128 px | −56 px |
| Swiss Editorial | 96 px | −88 px |

The half-width retro set (300 px) includes the Walkman (100 px), Clear Neon Cassette
(100 px), PS2 Pixel Shader (100 px) and the Buick LeSabre '93 cluster (118 px). The
Walkman's key row is a real Blender-built glTF asset. The shipped variant adds a 3D
silver volume fader, uniform compact keys and a taller LCD (`0add0b7`, `a01b495`,
`e1f7a92`), and the LCD scrambles into the next title rather than cutting.

The deck floats: no frame, and the frame's controls move onto the player. Its height
is derived from its width, so a resize never crops the device; the drag target is
the deck's own bezel and only the column span is saved.

## YouTube CRT widget

The CRT card (`app/static/crt/player.html`) is a 1:1 Sony Trinitron KV-1210: matte
black cabinet, stepped brushed-silver bezel, folding chrome carry handle, V-shaped
rabbit ears, dished `#232b3a` mask around a 0.368 x 0.207 m 16:9 tube, `| SONY |`
badge, two tuner wells, a 17-louver speaker grille, RGB `TRINITRON` badge and six
vertical micro-controls. The micro-controls are decorative and non-selectable
(`user-select: none`, no hotspot buttons).

The split mirrors the Walkman: the **card owns the device** (static, NO SIGNAL,
OSD, knobs, power-on flash, the YouTube player itself); the **widget owns the
data** (video resolution, candidate fallback, grid sizing). The parent frame is
only the aspect-locked box plus two hover chips (title, close). Changing videos
looks like changing the channel: the tube fills with snow, a bright band rolls
through, the channel paddle clicks 30° per channel, the tube flashes on, and a
green OSD reads `CH 03 · <title>` for three seconds.

The overlay projects four zero-size anchors on the glTF tube corners every
`camera-change`, so the iframe **is** the glass at any widget width. Before the GLB
loads (or if it fails), it falls back to the nominal inset (`TUBE_LEFT` 0.056 m,
`TUBE_TOP` 0.0715 m). The camera sits at `0deg 90deg 1.0658m` with a 20° field of
view, chosen so the front plane fills the frame. The overlay's corner radius is the
mask's 0.022 m radius scaled by tube height (`22 * h / 207` px), clamped to ≥ 4 px
(`bbcdf2d`). Invisible 0.055 m pick targets track the same projection over the VHF
and UHF wells.

The card is authored at `apps/doc-client/static/crt-tv-player.html` and copied by
`scripts/sync-crt-card.sh`. Printed surfaces are drawn by `make_textures.py`
(PIL/numpy at 2x, Lanczos downsample) and embedded into one self-contained `.glb`
by `build_tv.py` — no sidecar images. Noise fields use fixed seeds (42 metal, 19
mask), so a rebuild reproduces the textures byte for byte.

The height is derived (`round(width / 1.8182)`), the resizer skips the widget, and
the drag target is the cabinet's own bezel. A television is its own object, so the
boxless rule applies here too.

## Self-heal

`app/static/index.js` contains the client-side repair path:

- `musicWidgetNeedsRebuild` detects missing Alpine wiring or the old positional
  config form.
- `recoverMusicWidgetConfig` rebuilds from `data-hn-config`.
- The clock self-heal keeps the server's `clockWidget(tz, …)` call.
- `WidgetResizer.decorate()` skips `.youtube-player` and the Walkman card;
  `apply()` ignores a stored height for them; `ensureExpandButton()` skips them.

A rebuild never invents a default genre or timezone; a widget with no config is
left as-is and reported.
