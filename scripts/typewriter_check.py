"""Drive the REAL typeInRevisedText / unwrapTypedWords / getCleanedCanvasHtml
logic from index.js in a headless browser.

The property that actually matters is not "does it animate" — it is that after
the reveal the DOM is byte-identical to what the server sent. The server adopts
the client canvas as canonical between turns, so a leaked <span class="tw-word">
would be permanent and would change the widget's data-sig.
"""
import re
import pathlib
import json
from playwright.sync_api import sync_playwright

SRC = pathlib.Path("/home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js").read_text()
CSS = pathlib.Path("/home/lazycat/github/projects/sun/HTML-Notes/app/static/index.css").read_text()


def extract(name, kind="function"):
    """Pull one top-level function/const out of index.js by brace matching."""
    if kind == "function":
        start = SRC.index(f"function {name}(")
    else:
        start = SRC.index(f"const {name}")
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


HARNESS = "\n".join([
    extract("typeInRevisedText"),
    extract("unwrapTypedWords"),
    extract("isAlpineDriven"),
    # constants the above close over
    "const TYPE_MIN_MS = %s;" % re.search(r"TYPE_MIN_MS = (\d+)", SRC).group(1),
    "const TYPE_MAX_MS = %s;" % re.search(r"TYPE_MAX_MS = (\d+)", SRC).group(1),
    "const TYPE_MS_PER_WORD = %s;" % re.search(r"TYPE_MS_PER_WORD = ([\d.]+)", SRC).group(1),
    "const TYPE_MAX_WORDS = %s;" % re.search(r"TYPE_MAX_WORDS = (\d+)", SRC).group(1),
    "const TYPE_SKIP_SELECTOR = '.map-widget,.youtube-widget,.music-widget';",
])

SERVER_HTML = (
    '<div class="widget-container data-card" id="dc-1" data-sig="abc123">'
    '<div class="widget-header"><h3>Best Sandals</h3>'
    '<button class="close-widget-btn">x</button></div>'
    '<div class="widget-body"><p>Teva Hurricane leads for <strong>comfort</strong>, '
    'and <a href="https://x.com">Chaco Z/1</a> wins on arch support.</p>'
    '<ul><li>Keen Newport H2</li><li>Bedrock Cairn</li></ul></div>'
    '</div>'
)

PAGE = f"""<!doctype html><html><head><style>{CSS}</style></head>
<body><div id="live-canvas"><div id="dashboard-grid">{SERVER_HTML}</div></div>
<script>{HARNESS}
window.__run = () => {{
  const w = document.getElementById('dc-1');
  window.__before = w.outerHTML;
  typeInRevisedText(w);
  return {{
    spans: w.querySelectorAll('span.tw-word').length,
    revealed: w.querySelectorAll('span.tw-word.tw-in').length,
    height: w.getBoundingClientRect().height
  }};
}};
window.__state = () => {{
  const w = document.getElementById('dc-1');
  return {{
    spans: w.querySelectorAll('span.tw-word').length,
    revealed: w.querySelectorAll('span.tw-word.tw-in').length,
    html: w.outerHTML,
    height: w.getBoundingClientRect().height,
    invisible: Array.from(w.querySelectorAll('span.tw-word'))
                    .filter(s => getComputedStyle(s).opacity === '0').length
  }};
}};
</script></body></html>"""

fails = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content(PAGE)

    before_html = pg.evaluate("document.getElementById('dc-1').outerHTML")
    h0 = pg.evaluate("document.getElementById('dc-1').getBoundingClientRect().height")

    started = pg.evaluate("window.__run()")
    print(f"immediately after start : spans={started['spans']} "
          f"revealed={started['revealed']}")
    if started["spans"] < 5:
        fails.append(f"expected words to be wrapped, got {started['spans']} spans")
    if started["revealed"] >= started["spans"]:
        fails.append("everything revealed on frame 0 — no progressive reveal")

    pg.wait_for_timeout(250)
    mid = pg.evaluate("window.__state()")
    print(f"mid-reveal              : revealed={mid['revealed']}/{mid['spans']} "
          f"height={mid['height']:.0f}")
    if mid["height"] != h0:
        fails.append(f"LAYOUT SHIFT: height {h0} -> {mid['height']}")

    pg.wait_for_timeout(2200)
    end = pg.evaluate("window.__state()")
    print(f"after reveal            : spans={end['spans']} "
          f"invisible={end['invisible']} height={end['height']:.0f}")

    if end["spans"] != 0:
        fails.append(f"LEAKED {end['spans']} tw-word spans into the canvas")
    if end["invisible"]:
        fails.append(f"{end['invisible']} words left invisible")
    if end["html"] != before_html:
        fails.append("DOM differs from what the server sent after the reveal:\n"
                     f"  before: {before_html[:160]}\n"
                     f"  after : {end['html'][:160]}")
    else:
        print("DOM restored byte-identical to server HTML  ✓")

    # a live/interactive widget must be skipped entirely
    pg.set_content(PAGE.replace('id="dc-1"', 'id="dc-1" x-data="clockWidget(\'local\')"'))
    pg.evaluate("window.__run()")
    skipped = pg.evaluate("document.querySelectorAll('span.tw-word').length")
    print(f"alpine widget (must skip): spans={skipped}")
    if skipped:
        fails.append("alpine-driven widget was animated; live widgets must be skipped")

    # reduced motion must bail
    pg2 = b.new_page(reduced_motion="reduce")
    pg2.set_content(PAGE)
    pg2.evaluate("window.__run()")
    rm = pg2.evaluate("document.querySelectorAll('span.tw-word').length")
    print(f"prefers-reduced-motion  : spans={rm}")
    if rm:
        fails.append("animated despite prefers-reduced-motion")

    b.close()

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
