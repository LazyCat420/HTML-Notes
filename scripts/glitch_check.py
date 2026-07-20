"""Drive the REAL glitch functions from index.js in a headless browser.

The property that matters is not "does it animate" — it is that the text ends up
byte-identical to what the server sent, and that a canvas serialized MID-glitch
carries the final wording rather than scrambled glyphs. The server adopts the
client canvas as canonical, so noise captured mid-run would become the widget's
permanent content and would change its data-sig.
"""
import re
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path("/home/lazycat/github/projects/sun/HTML-Notes")
SRC = (ROOT / "app/static/index.js").read_text()
CSS = (ROOT / "app/static/index.css").read_text()


def extract(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", start)
    depth = 0
    for j in range(i, len(SRC)):
        if SRC[j] == "{":
            depth += 1
        elif SRC[j] == "}":
            depth -= 1
            if depth == 0:
                return SRC[start:j + 1]
    raise RuntimeError(name)


def const(name):
    """Take the line up to its LAST semicolon.

    Not the first: GLITCH_GLYPHS has a ';' inside its string literal, and cutting
    there produced an unterminated string whose only symptom was
    "window.__html is not a function". Not the whole line either: several of
    these carry a trailing // comment.
    """
    line = re.search(rf"^\s*const {name} = (.*)$", SRC, re.M).group(1)
    cut = line.rfind(";")
    return f"const {name} = {line[:cut] if cut != -1 else line};"


HARNESS = "\n".join([
    const("GLITCH_MIN_MS"), const("GLITCH_MAX_MS"), const("GLITCH_MS_PER_CHAR"),
    const("GLITCH_CHAR_LIFE"), const("GLITCH_MAX_CHARS"), const("GLITCH_GLYPHS"),
    "const GLITCH_SKIP_SELECTOR = '.map-widget,.youtube-widget,.music-widget';",
    "const activeGlitches = new Set();",
    extract("finishGlitches"),
    extract("isAlpineDriven"),
    extract("glitchTextNodes"),
    extract("captureWidgetText"),
    extract("glitchIntoText"),
])

WIDGET = (
    '<div class="widget-container data-card" id="dc-1" data-sig="abc123" x-data="{}">'
    '<div class="widget-header"><h3>Best Sandals</h3>'
    '<button class="close-widget-btn">x</button></div>'
    '<div class="p-5"><p>Teva Hurricane leads for <strong>comfort</strong>, '
    'and <a href="https://x.com">Chaco Z/1</a> wins on arch support.</p>'
    '<ul><li>Keen Newport H2</li><li>Bedrock Cairn</li></ul></div>'
    '</div>'
)

PAGE = f"""<!doctype html><html><head><style>{CSS}</style></head>
<body><div id="live-canvas"><div id="dashboard-grid">{WIDGET}</div></div>
<script>{HARNESS}
window.__start = () => {{
  glitchIntoText(document.getElementById('dc-1'),
                 'OLD WORDING THAT WAS PREVIOUSLY ON THIS CARD');
}};
window.__text = () => document.getElementById('dc-1').innerText;
window.__html = () => document.getElementById('dc-1').outerHTML;
window.__finish = () => {{ finishGlitches(); return window.__html(); }};
</script></body></html>"""

fails = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content(PAGE)

    final_html = pg.evaluate("window.__html()")
    final_text = pg.evaluate("window.__text()")
    h0 = pg.evaluate("document.getElementById('dc-1').getBoundingClientRect().height")

    pg.evaluate("window.__start()")
    pg.wait_for_timeout(120)
    mid_text = pg.evaluate("window.__text()")
    print(f"mid-glitch scrambled : {mid_text != final_text}")
    print(f"  sample: {mid_text[:70]!r}")
    if mid_text == final_text:
        fails.append("text never scrambled — the glitch did not run")
    if len(mid_text) != len(final_text):
        fails.append(f"character count changed mid-glitch "
                     f"({len(mid_text)} vs {len(final_text)}) — text will reflow")

    # The guard that actually protects the server's canvas.
    forced = pg.evaluate("window.__finish()")
    if forced != final_html:
        fails.append("finishGlitches() did not restore the exact server HTML")
    else:
        print("finishGlitches() restores server HTML exactly  ✓")

    # Full natural run.
    pg.set_content(PAGE)
    pg.evaluate("window.__start()")
    pg.wait_for_timeout(2600)
    end_html = pg.evaluate("window.__html()")
    end_h = pg.evaluate("document.getElementById('dc-1').getBoundingClientRect().height")
    print(f"after settle         : height {h0:.0f} -> {end_h:.0f}")
    if end_html != final_html:
        fails.append("DOM differs from the server HTML after the glitch settled")
    else:
        print("DOM restored byte-identical to server HTML  ✓")
    if end_h != h0:
        fails.append(f"LAYOUT SHIFT: height {h0} -> {end_h}")

    # live widgets must be skipped
    pg.set_content(PAGE.replace('x-data="{}"', "x-data=\"clockWidget('local')\""))
    before = pg.evaluate("window.__text()")
    pg.evaluate("window.__start()")
    pg.wait_for_timeout(120)
    if pg.evaluate("window.__text()") != before:
        fails.append("alpine-driven widget was glitched; live widgets must be skipped")
    else:
        print("alpine widget skipped  ✓")

    # reduced motion must bail
    pg2 = b.new_page(reduced_motion="reduce")
    pg2.set_content(PAGE)
    rm_before = pg2.evaluate("window.__text()")
    pg2.evaluate("window.__start()")
    pg2.wait_for_timeout(120)
    if pg2.evaluate("window.__text()") != rm_before:
        fails.append("animated despite prefers-reduced-motion")
    else:
        print("reduced-motion honored ✓")

    b.close()

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
