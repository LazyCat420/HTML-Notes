"""End-to-end: asking the LIVE app for a video spawns a CRT television, and
asking for a second video changes the channel — the tube goes to snow, the dial
turns, and the new channel comes up.

This is the user-visible flow, not an injected widget: it drives the real chat
input and the real SSE turn, so it also proves the widget the server renders
carries the CRT card and the derived 1.8182:1 box.

Requires the live stack: html-notes (:8035). Live check — not CI.

Run: .venv/bin/python scripts/youtube_crt_flow_check.py
"""
import sys
import time
from playwright.sync_api import sync_playwright

HOST = "http://10.0.0.16:8035"
CABINET_ASPECT = 0.6 / 0.33

results, failures = [], 0


def check(name, ok, detail=""):
    global failures
    results.append((ok, name, detail))
    if not ok:
        failures += 1


def report():
    for ok, name, detail in results:
        print(f"{'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))
    print(f"\n{len(results) - failures}/{len(results)} checks passed")


def crt_frame(pg):
    """The card of the newest youtube widget, if its document is up."""
    for f in pg.frames:
        if "crt/player.html" in (f.url or ""):
            return f
    return None


def static_on(fr):
    try:
        return fr.evaluate("() => { const s = document.getElementById('static');"
                           " if (!s) return false; const c = getComputedStyle(s);"
                           " return c.opacity !== '0' && c.display !== 'none'; }")
    except Exception:
        return False


def widget_boxes(pg):
    return pg.evaluate("""() => [...document.querySelectorAll('.widget-container.youtube-player')].map(el => {
        const r = el.getBoundingClientRect();
        return {id: el.id, w: r.width, h: r.height, ratio: r.width / r.height,
                iframes: el.querySelectorAll('iframe').length,
                src: (el.querySelector('iframe') || {}).src || ''};
    })""")


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(HOST, wait_until="domcontentloaded")
    pg.wait_for_selector("#chat-input", timeout=30000)

    # ── first ask: a video ──────────────────────────────────────────────────
    pg.fill("#chat-input", "play a video of a cat")
    pg.press("#chat-input", "Enter")
    try:
        pg.wait_for_function("() => document.querySelectorAll('.widget-container.youtube-player').length > 0",
                             timeout=180000)
        check("a video ask spawns a CRT widget", True)
    except Exception:
        check("a video ask spawns a CRT widget", False, "no .youtube-player appeared")

    # The card may take a moment to load the GLB.
    deadline = time.time() + 60
    fr = None
    while time.time() < deadline:
        fr = crt_frame(pg)
        if fr is not None:
            try:
                fr.wait_for_function("() => document.getElementById('mv') && document.getElementById('mv').loaded",
                                     timeout=20000)
                break
            except Exception:
                pass
        pg.wait_for_timeout(500)
    check("the spawned widget is the CRT card", fr is not None and "crt/player.html" in (fr.url or ""),
          (fr.url if fr else "no frame"))

    boxes = widget_boxes(pg)
    check("the spawned widget is 1.8182:1",
          any(abs(b["ratio"] - CABINET_ASPECT) < 0.02 for b in boxes),
          str([(round(b["w"]), round(b["h"]), round(b["ratio"], 3)) for b in boxes]))
    check("the spawned widget holds exactly one iframe (the card)",
          all(b["iframes"] == 1 for b in boxes), str([b["iframes"] for b in boxes]))

    if fr is not None:
        try:
            fr.wait_for_function("() => { const y = document.getElementById('yt');"
                                 " return (y && y.src && y.src.includes('embed'))"
                                 " || document.getElementById('nosig').classList.contains('on'); }",
                                 timeout=45000)
            check("the tube tunes to the video (or NO SIGNAL if it blocks embedding)", True)
        except Exception:
            check("the tube tunes to the video (or NO SIGNAL if it blocks embedding)", False)

    # ── second ask: the channel changes, and it looks like static ───────────
    pg.fill("#chat-input", "now play a video of a dog")
    pg.press("#chat-input", "Enter")
    saw_static = False
    deadline = time.time() + 180
    while time.time() < deadline and not saw_static:
        fr2 = crt_frame(pg)
        if fr2 is not None and static_on(fr2):
            saw_static = True
            break
        pg.wait_for_timeout(120)
    check("changing the video puts snow on the tube", saw_static)

    boxes2 = widget_boxes(pg)
    check("every CRT widget is still 1.8182:1 after the second turn",
          all(abs(b["ratio"] - CABINET_ASPECT) < 0.02 for b in boxes2),
          str([(round(b["w"]), round(b["h"]), round(b["ratio"], 3)) for b in boxes2]))

    b.close()

report()
sys.exit(1 if failures else 0)
