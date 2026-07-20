"""End-to-end: drive a real follow-up against the LIVE app and confirm the
typewriter reveal fires on the widget that gets rewritten in place.

A MutationObserver records tw-word spans as they appear, because the reveal is
finished (and unwrapped) long before any polling loop could sample it.
"""
import sys
from playwright.sync_api import sync_playwright

HOST = "http://10.0.0.16:8035"

OBSERVER = """
window.__tw = {maxSpans: 0, sawIn: 0, updates: 0, ids: []};
new MutationObserver(muts => {
  for (const m of muts) {
    if (m.type === 'attributes' && m.target.classList) {
      if (m.target.classList.contains('is-updating')) {
        window.__tw.updates++;
        if (m.target.id) window.__tw.ids.push(m.target.id);
      }
      if (m.target.classList.contains('tw-in')) window.__tw.sawIn++;
    }
  }
  for (const m of muts) {
    if (m.type === 'childList') {
      m.addedNodes.forEach(n => {
        if (n.classList && n.classList.contains('tw-word')) window.__tw.maxSpans++;
      });
    }
  }
}).observe(document.getElementById('live-canvas'),
           {subtree: true, childList: true, attributes: true,
            attributeFilter: ['class']});
"""


def send(page, text, timeout_ms, expect_change_from=None):
    """expect_change_from: a data-sig that must CHANGE before we call it done.
    Waiting on "a widget exists" is useless for a follow-up — one already does."""
    page.fill("#chat-input", text)
    page.press("#chat-input", "Enter")
    if expect_change_from is None:
        page.wait_for_function(
            "() => document.querySelectorAll('#dashboard-grid .widget-container').length > 0",
            timeout=timeout_ms)
    else:
        page.wait_for_function(
            """(old) => {
                 const w = document.querySelector('#dashboard-grid .widget-container');
                 return w && w.getAttribute('data-sig') !== old;
               }""",
            arg=expect_change_from, timeout=timeout_ms)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(HOST, wait_until="networkidle")
    pg.evaluate("localStorage.clear()")
    pg.reload(wait_until="networkidle")
    pg.wait_for_selector("#chat-input", timeout=30000)
    pg.evaluate(OBSERVER)

    print("turn 1: asking the original question...", flush=True)
    send(pg, "best waterproof hiking sandals", 300000)
    pg.wait_for_timeout(3000)
    first = pg.evaluate("""() => Array.from(
        document.querySelectorAll('#dashboard-grid .widget-container'))
        .map(w => ({id: w.id, sig: w.getAttribute('data-sig')}))""")
    print(f"  widgets: {first}")

    pg.evaluate("window.__tw = {maxSpans:0, sawIn:0, updates:0, ids:[]}")

    print("turn 2: follow-up (should rewrite the SAME widget)...", flush=True)
    send(pg, "only the ones under $50", 300000,
         expect_change_from=first[0]["sig"] if first else None)
    pg.wait_for_timeout(2500)

    after = pg.evaluate("""() => Array.from(
        document.querySelectorAll('#dashboard-grid .widget-container'))
        .map(w => ({id: w.id, sig: w.getAttribute('data-sig')}))""")
    tw = pg.evaluate("window.__tw")
    leftover = pg.evaluate("document.querySelectorAll('span.tw-word').length")
    print(f"  widgets: {after}")
    print(f"  observer: {tw}")
    print(f"  leftover tw-word spans: {leftover}")

    fails = []
    if len(after) != len(first):
        fails.append(f"widget COUNT changed {len(first)} -> {len(after)} "
                     f"(follow-up stacked instead of updating)")
    if first and after and first[0]["id"] != after[0]["id"]:
        fails.append(f"widget id changed {first[0]['id']} -> {after[0]['id']}")
    if first and after and first[0]["sig"] == after[0]["sig"]:
        fails.append("data-sig unchanged — the widget was not actually rewritten")
    if tw["updates"] == 0:
        fails.append("no widget ever got .is-updating")
    if tw["maxSpans"] == 0:
        fails.append("typewriter never wrapped any words")
    if tw["sawIn"] == 0:
        fails.append("words were wrapped but never revealed (.tw-in)")
    if leftover:
        fails.append(f"{leftover} tw-word spans LEAKED into the canvas")
    if errors:
        fails.append(f"page errors: {errors[:2]}")

    pg.screenshot(path="/tmp/claude-1000/-home-lazycat-github-projects-sun/"
                       "66accc7b-c320-4e9e-963d-e8b7eeef7873/scratchpad/followup.png")
    b.close()

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("LIVE FOLLOW-UP + TYPEWRITER VERIFIED")
