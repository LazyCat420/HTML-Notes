"""Provisional-widget smoke check against the SHIPPED pieces.

What matters is the failure mode where a card wears "composing…" forever, or a
provisional card is held invisible by the paced-reveal gate (defeating the whole
point of an early preview). So this drives:

  1. the real server factory output (data-provisional stamped / absent),
  2. the real index.css badge (pseudo-element actually renders, and disappears
     the moment the attribute is stripped),
  3. the shipped client cleanup sites in index.js (source-level: reveal-gate
     bypass in reconcileCanvas, done-handler strip, serialize-path strip).

Run: .venv/bin/python scripts/provisional_check.py
"""
import re
import sys

sys.path.insert(0, "/home/lazycat/github/projects/sun/HTML-Notes")

from playwright.sync_api import sync_playwright

from app.widgets.factory import generate_widget_html

ROOT = "/home/lazycat/github/projects/sun/HTML-Notes"
JS = open(f"{ROOT}/app/static/index.js").read()
CSS = open(f"{ROOT}/app/static/index.css").read()

failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


# ---- 1. shipped client code contains the three provisional hooks ----------
check("reconcile append branch bypasses reveal gate for provisional widgets",
      re.search(r"hasAttribute\('data-provisional'\)\s*\n?\s*\|\|\s*!\(revealGateActive\(\)", JS)
      is not None)
check("done handler strips lingering data-provisional",
      "elements.liveCanvas.querySelectorAll('[data-provisional]')" in JS)
check("canvas serialization strips data-provisional",
      JS.count("removeAttribute('data-provisional')") >= 2)

# ---- 2. real factory render ------------------------------------------------
cfg = {"title": "Today's News", "items": [
    {"title": "Story A", "description": "Summary A.", "url": "https://example.com/a"}]}
prov_html = generate_widget_html("data_card", "news-e2e01", {**cfg, "provisional": True})
final_html = generate_widget_html("data_card", "news-e2e01", cfg)
check("factory stamps data-provisional on provisional config",
      'data-provisional="1"' in prov_html)
check("factory omits data-provisional on final config",
      "data-provisional" not in final_html)
sig = re.compile(r'data-sig="([0-9a-f]+)"')
check("provisional and final renders carry different data-sig",
      sig.search(prov_html).group(1) != sig.search(final_html).group(1))

# ---- 3. real CSS badge in a headless browser -------------------------------
page_html = f"""<!doctype html><html><head><style>{CSS}</style></head>
<body><div id="dashboard-grid">{prov_html}</div></body></html>"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(page_html)
    badge = page.evaluate(
        """() => {
            const el = document.querySelector('.widget-container[data-provisional]')
                    || document.querySelector('[data-provisional]');
            if (!el) return {found: false};
            const s = getComputedStyle(el, '::before');
            return {found: true, content: s.content,
                    visible: getComputedStyle(el).visibility !== 'hidden'};
        }""")
    check("provisional widget node present and visible", bool(badge.get("found")) and badge.get("visible"))
    check("composing badge pseudo-element renders",
          "composing" in (badge.get("content") or ""))

    after = page.evaluate(
        """() => {
            const el = document.querySelector('[data-provisional]');
            el.removeAttribute('data-provisional');
            return getComputedStyle(el, '::before').content;
        }""")
    check("badge disappears when the attribute is stripped",
          "composing" not in (after or ""))
    browser.close()

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all provisional-widget checks passed")
