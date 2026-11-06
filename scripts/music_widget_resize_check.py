"""Live check: the music widget's box tracks the walkman deck 1:1, and the
deck's own silver bezel — not a box around the widget — is its resize grip.

The deck is a 0.300 x 0.150 m object drawn edge to edge in the iframe, so the
widget must be 2:1 and the LCD must never run past the frame's left/right edge
(that was the "cuts off slightly on the right" crop: a saved height made the
stage taller than 2:1, shrinking the camera's horizontal span).

Requires the live stack: html-notes (:8035). Run against the deployed app.
Run: .venv/bin/python scripts/music_widget_resize_check.py
"""
import os
import sys
from playwright.sync_api import sync_playwright

# Live app by default; HN_HOST lets the same checks run against a local static
# server while iterating on the frontend.
HOST = os.environ.get("HN_HOST", "http://10.0.0.16:8035")
WIDGET_ID = "music-resize-check"
# The model is 0.300 m wide; the glass in the shipped GLB measures ~0.239 m, so
# the LCD hotspot is 0.796 of the frame width when the deck fills it edge to
# edge (measured at the reference 732 x 366 card; the 3D body is 2:1, so the
# ratio is size-independent). A taller-than-2:1 stage pushes it past 1.0 (the
# crop the user saw).
LCD_RATIO = 0.796

results, failures = [], 0


def check(name, ok, detail=""):
    global failures
    results.append((ok, name, detail))
    if not ok:
        failures += 1


INJECT = """
(id) => {
  document.querySelectorAll('#' + id).forEach(n => n.remove());
  try { const s = JSON.parse(localStorage.getItem('widget_sizes') || '{}'); delete s[id];
        localStorage.setItem('widget_sizes', JSON.stringify(s)); } catch (e) {}
  const grid = document.querySelector('#dashboard-grid');
  const el = document.createElement('div');
  el.id = id;
  el.className = 'widget-container music-widget-root col-span-2 relative text-white flex flex-col';
  el.setAttribute('x-data', "musicPlayerWidget({ genre: '', kind: '', autoplay: false })");
  el.setAttribute(':style', "{ height: walkmanHeight + 'px' }");
  el.innerHTML = `<div class="relative z-10 flex-1 min-h-0">
      <iframe class="w-full h-full block border-0"
              src="/static/walkman/player.html?driven=1&paused=1&v=2"
              title="Walkman music player" allow="autoplay"></iframe>
    </div>`;
  grid.appendChild(el);
  window.Alpine.initTree(el);
  if (window.WidgetResizer) window.WidgetResizer.decorate(el);
  if (window.__masonryLayout) window.__masonryLayout();
  return true;
}
"""

MEASURE = """
(id) => {
  const el = document.getElementById(id);
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const size = (JSON.parse(localStorage.getItem('widget_sizes') || '{}'))[id] || null;
  return {
    w: r.width, h: r.height, ratio: r.height / r.width,
    gridColumn: el.style.gridColumn || '',
    outlineStyle: cs.outlineStyle,
    handle: !!el.querySelector(':scope > .widget-resize-handle'),
    bezelClass: el.classList.contains('is-resizing-bezel'),
    saved: size,
  };
}
"""

INNER = """
() => {
  const mv = document.getElementById('mv');
  const scr = document.querySelector('.hs-screen');
  const fr = document.getElementById('card').getBoundingClientRect();
  const sr = scr ? scr.getBoundingClientRect() : null;
  return {
    frame: {w: fr.width, h: fr.height},
    screen: sr ? {x: sr.x, w: sr.width} : null,
    loaded: !!mv.loaded,
  };
}
"""


def widget_frame(pg, timeout_ms=15000):
    """The card iframe is created before its document loads, so pg.frames only
    lists it once the document starts; poll instead of reading one snapshot."""
    import time
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for f in pg.frames:
            if "player.html" in (f.url or ""):
                return f
        pg.wait_for_timeout(200)
    return None


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(HOST, wait_until="domcontentloaded")
    pg.wait_for_function("() => window.Alpine && window.WidgetResizer")
    pg.evaluate(INJECT, WIDGET_ID)
    fr = widget_frame(pg)
    if fr is None:
        print("✗ the widget iframe never appeared")
        sys.exit(1)
    try:
        fr.wait_for_function("() => document.getElementById('mv') && document.getElementById('mv').loaded",
                             timeout=20000)
    except Exception:
        pass
    pg.wait_for_timeout(1200)

    # A height saved by the retired corner grip (520 px on a 732 px card, a
    # 1.4:1 stage) must be ignored: WidgetResizer.apply and the widget itself
    # both derive the height from the width now.
    pg.evaluate("""(id) => {
      const s = JSON.parse(localStorage.getItem('widget_sizes') || '{}');
      s[id] = { cols: 2, height: 520 };
      localStorage.setItem('widget_sizes', JSON.stringify(s));
      const el = document.getElementById(id);
      window.WidgetResizer.apply(el);
    }""", WIDGET_ID)
    pg.wait_for_timeout(600)

    m = pg.evaluate(MEASURE, WIDGET_ID)
    inner = fr.evaluate(INNER)
    check("widget is 2:1 at its default size", abs(m["ratio"] - 0.5) < 0.01,
          f"h/w={m['ratio']:.3f}")
    check("a saved 520 px height is ignored (no crop from an old session)",
          abs(m["h"] - m["w"] / 2) <= 2, f"w={m['w']:.0f} h={m['h']:.0f}")
    check("no corner grip on the music widget", not m["handle"], str(m))
    check("no outline box while idle", m["outlineStyle"] == "none", m["outlineStyle"])
    ratio = inner["screen"]["w"] / inner["frame"]["w"] if inner["screen"] else 0
    check("deck fills the frame (LCD = 0.796 of the width)",
          abs(ratio - LCD_RATIO) < 0.02, f"lcd/frame={ratio:.3f} want {LCD_RATIO:.3f}")
    check("deck is centred, nothing cut off on either side",
          inner["screen"] and inner["screen"]["x"] > 0
          and inner["screen"]["x"] + inner["screen"]["w"] < inner["frame"]["w"] + 1,
          str(inner["screen"]))

    # Drag the deck's silver bezel: the top metal band, 8 px inside the widget.
    box = pg.locator("#" + WIDGET_ID).bounding_box()
    x0 = box["x"] + box["width"] / 2
    y0 = box["y"] + 8
    pg.mouse.move(x0, y0)
    pg.mouse.down()
    mid = None
    for i in range(1, 11):
        pg.mouse.move(x0 + i * 20, y0 + i * 10)
        if i == 5:
            mid = pg.evaluate(MEASURE, WIDGET_ID)
    pg.mouse.up()
    pg.wait_for_timeout(600)

    check("bezel drag marks the widget without drawing a box",
          mid is not None and mid["bezelClass"] and mid["outlineStyle"] == "none",
          str(mid))
    after = pg.evaluate(MEASURE, WIDGET_ID)
    check("bezel drag widened the widget by grid columns", "span" in after["gridColumn"],
          after["gridColumn"])
    check("width and height stay 2:1 after the drag", abs(after["ratio"] - 0.5) < 0.01,
          f"h/w={after['ratio']:.3f}")
    check("only columns are saved (height is derived)",
          after["saved"] is not None and "height" not in after["saved"], str(after["saved"]))
    # model-viewer recomputes hotspot transforms on its next frame, so the LCD
    # rect can trail the box by one paint. Poll for up to 2 s.
    ratio = 0
    for _ in range(10):
        inner = fr.evaluate(INNER)
        ratio = inner["screen"]["w"] / inner["frame"]["w"] if inner["screen"] else 0
        if abs(ratio - LCD_RATIO) < 0.02:
            break
        pg.wait_for_timeout(200)
    check("deck still fills the frame after enlarging",
          abs(ratio - LCD_RATIO) < 0.02, f"lcd/frame={ratio:.3f} want {LCD_RATIO:.3f}")

    # A drag that starts on the LCD is a hotspot, not a resize.
    scr = fr.evaluate("() => { const r = document.querySelector('.hs-screen').getBoundingClientRect();"
                      " return {x: r.x + r.width/2, y: r.y + r.height/2}; }")
    cols_before = pg.evaluate(MEASURE, WIDGET_ID)["gridColumn"]
    pg.mouse.move(box["x"] + scr["x"], box["y"] + scr["y"])
    pg.mouse.down()
    for i in range(1, 8):
        pg.mouse.move(box["x"] + scr["x"] + i * 20, box["y"] + scr["y"] + i * 10)
    pg.mouse.up()
    pg.wait_for_timeout(400)
    drag_on_screen = pg.evaluate(MEASURE, WIDGET_ID)
    check("dragging the LCD does not resize the widget",
          drag_on_screen["gridColumn"] == cols_before and not drag_on_screen["bezelClass"],
          f"{cols_before} -> {drag_on_screen['gridColumn']}")

    # Pulling the LEFT bezel outward must grow the widget, not shrink it: the
    # drag direction is resolved against the half of the deck it started on.
    box = pg.locator("#" + WIDGET_ID).bounding_box()
    left_x = box["x"] + 10
    mid_y = box["y"] + box["height"] * 0.35
    cols_before = pg.evaluate(MEASURE, WIDGET_ID)["gridColumn"]
    pg.mouse.move(left_x, mid_y)
    pg.mouse.down()
    for i in range(1, 11):
        pg.mouse.move(left_x - i * 20, mid_y)
    pg.mouse.up()
    pg.wait_for_timeout(600)
    outward = pg.evaluate(MEASURE, WIDGET_ID)
    check("pulling the left bezel outward grows the widget",
          outward["w"] > box["width"] + 50,
          f"{box['width']:.0f} -> {outward['w']:.0f} ({cols_before} -> {outward['gridColumn']})")

    # A click on the metal that never moves must not resize anything.
    before_click = pg.evaluate(MEASURE, WIDGET_ID)
    pg.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + 8)
    pg.wait_for_timeout(500)
    after_click = pg.evaluate(MEASURE, WIDGET_ID)
    check("a stationary click on the bezel changes nothing",
          after_click["w"] == before_click["w"] and after_click["h"] == before_click["h"]
          and after_click["gridColumn"] == before_click["gridColumn"],
          f"{before_click['w']:.0f}x{before_click['h']:.0f} -> {after_click['w']:.0f}x{after_click['h']:.0f}")

    # The drawer adds its own band under the 2:1 stage, never squeezing it.
    fr.evaluate("() => window.walkmanPlayer.openQueue()")
    pg.wait_for_timeout(900)
    opened = pg.evaluate(MEASURE, WIDGET_ID)
    check("the drawer adds 148 px on top of the 2:1 stage",
          abs(opened["h"] - (opened["w"] / 2 + 148)) <= 2,
          f"w={opened['w']:.0f} h={opened['h']:.0f}")
    inner = fr.evaluate(INNER)
    ratio = inner["screen"]["w"] / inner["frame"]["w"] if inner["screen"] else 0
    check("deck still fills the frame with the drawer open",
          abs(ratio - LCD_RATIO) < 0.02, f"lcd/frame={ratio:.3f} want {LCD_RATIO:.3f}")
    fr.evaluate("() => window.walkmanPlayer.closeQueue()")
    pg.wait_for_timeout(600)

    # Double-click the bezel drops the saved size.
    pg.mouse.move(x0, y0)
    pg.mouse.dblclick(x0, y0)
    pg.wait_for_timeout(600)
    reset = pg.evaluate(MEASURE, WIDGET_ID)
    check("double-click on the bezel resets the size",
          reset["saved"] is None and reset["gridColumn"] == "", str(reset))

    b.close()

for ok, name, detail in results:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{detail}]" if not ok and detail else ""))
print(f"\n{'✗' if failures else '✓'} {len(results) - failures}/{len(results)} checks passed")
sys.exit(1 if failures else 0)
