"""End-to-end: "play jungle music" against the LIVE app must go through the
music-player service's genre SSE mix — never the local library.

Pins the fix for "jungle music played from my library": the hardcoded
client-side genre list is gone, so an arbitrary genre must hit
/api/youtube/mix/<genre>/stream?type=genre on the music-player service (:8002),
start streaming audio from it, and populate the queue UI.

Requires the live stack: html-notes (:8035) + music-player (:8002) +
scraper-service (:3031) on the NAS. Live check — not CI.

Run: .venv/bin/python scripts/music_widget_check.py
"""
import sys
import urllib.parse
from playwright.sync_api import sync_playwright

HOST = "http://10.0.0.16:8035"
MUSIC = ":8002"

results, failures = [], 0


def check(name, ok, detail=""):
    global failures
    results.append((ok, name, detail))
    if not ok:
        failures += 1


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()

    music_requests = []
    pg.on("request", lambda r: music_requests.append(r.url) if MUSIC in r.url else None)

    pg.goto(HOST, wait_until="domcontentloaded")
    pg.wait_for_selector("#chat-input", timeout=15000)

    pg.fill("#chat-input", "play jungle music")
    pg.press("#chat-input", "Enter")

    # The widget shell renders fast; audio may take the cold pipeline a while.
    pg.wait_for_selector('[id*="music"] , [x-data*="musicPlayerWidget"]', timeout=30000)
    widget = pg.locator('[x-data*="musicPlayerWidget"]').first

    # 1. Routing: exactly one genre SSE stream request, zero library dumps.
    pg.wait_for_timeout(2000)
    stream_reqs = [u for u in music_requests if "/api/youtube/mix/" in u and "/stream" in u]
    check("genre SSE stream requested",
          any("type=genre" in u and "jungle" in urllib.parse.unquote(u) for u in stream_reqs),
          f"stream reqs: {stream_reqs}")
    check("no /api/tracks library dump",
          not any(u.rstrip("/").endswith("/api/tracks") for u in music_requests),
          str([u for u in music_requests if "tracks" in u]))

    # 2. Playback: within 60s (cold pipeline) the audio element streams from
    #    the music-player service.
    try:
        pg.wait_for_function(
            """() => {
                const w = document.querySelector('[x-data*="musicPlayerWidget"]');
                if (!w) return false;
                const d = Alpine.$data(w);
                return d && d.audio && d.audio.src && d.audio.src.includes('/api/youtube/stream/');
            }""", timeout=60000)
        check("audio streams from music-player", True)
    except Exception:
        state = pg.evaluate(
            """() => {
                const w = document.querySelector('[x-data*="musicPlayerWidget"]');
                if (!w) return 'no widget';
                const d = Alpine.$data(w);
                return d ? {src: d.audio && d.audio.src, err: d.error, q: d.queue.length,
                            status: d.streamStatus} : 'no alpine data';
            }""")
        check("audio streams from music-player", False, str(state))

    # 2b. Geometry on the SSE-rendered widget (not an injected one): the canvas
    # re-render path (renderDynamicComponents -> initCanvasWidgets) must keep the
    # walkman's Alpine height binding. A stripped inline height leaves the deck
    # in a 4:1 auto-height box, so the card would no longer be 2:1.
    geo = pg.evaluate(
        """() => {
            const w = document.querySelector('[x-data*="musicPlayerWidget"]');
            const r = w.getBoundingClientRect();
            const cs = getComputedStyle(w);
            return { w: r.width, h: r.height, inlineH: w.style.height,
                     maxH: cs.maxHeight, handle: !!w.querySelector(':scope > .widget-resize-handle') };
        }""")
    check("SSE-rendered widget keeps its derived 2:1 height",
          geo["w"] > 40 and abs(geo["h"] - geo["w"] / 2) <= 2,
          str(geo))
    check("no corner grip on the music widget", not geo["handle"], str(geo))

    # 3. Queue: more than one track landed; the panel opens and jumping works.
    qlen = pg.evaluate(
        """() => Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]')).queue.length""")
    check("queue holds multiple tracks", qlen > 1, f"queue={qlen}")

    # The queue lives on the deck's card now: the drawer and its rows are inside
    # the iframe (#queue / #qrows), opened by the k4 "Queue" key hotspot.
    card = None
    for _ in range(30):
        card = next((f for f in pg.frames if "player.html" in (f.url or "")), None)
        if card:
            break
        pg.wait_for_timeout(200)
    check("walkman card iframe is present", card is not None, "no player.html frame")
    if card:
        card.evaluate("() => document.querySelectorAll('.hs-key')[4].click()")
        try:
            card.wait_for_function(
                "() => document.getElementById('queue').classList.contains('on')", timeout=5000)
            opened = True
        except Exception:
            opened = False
        check("queue key opens the drawer on the deck", opened,
              card.evaluate("() => document.getElementById('queue').className"))
        rows = card.locator("#qrows .row").count()
        check("queue panel shows upcoming rows", rows >= 1, f"rows={rows}")
        pg.wait_for_timeout(400)
        host = pg.evaluate(
            """() => {
                const w = document.querySelector('[x-data*="musicPlayerWidget"]');
                const d = Alpine.$data(w);
                return { open: d.walkmanOpen, base: d.walkmanBaseH, h: d.walkmanHeight,
                         box: Math.round(w.getBoundingClientRect().height) };
            }""")
        check("the card tells the host the drawer opened",
              host["open"] is True and host["h"] == host["base"] + 148
              and abs(host["box"] - host["h"]) <= 1, str(host))

        if rows >= 1:
            target = card.evaluate(
                """() => { const r = [...document.querySelectorAll('#qrows .row')]
                             .find(r => r.dataset.i === '1') || document.querySelector('#qrows .row');
                           return r ? r.dataset.i : null; }""")
            card.evaluate(
                """() => { const r = [...document.querySelectorAll('#qrows .row')]
                             .find(r => r.dataset.i === '1') || document.querySelector('#qrows .row');
                           r.click(); }""")
            pg.wait_for_timeout(800)
            after = pg.evaluate(
                """() => Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]')).currentIndex""")
            check("clicking a queue row jumps the track", str(after) == str(target),
                  f"row {target} -> currentIndex {after}")

    # 4. The SSE stream must not linger: once done/playing, the EventSource is
    #    closed (reconnect-storm guard against the 10/min mix rate limit).
    pg.wait_for_timeout(1500)
    es_open = pg.evaluate(
        """() => { const d = Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]'));
                   return d.es !== null && (!d.queue.length); }""")
    check("no orphaned EventSource after tracks landed", not es_open)

    b.close()

for ok, name, detail in results:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{detail}]" if not ok and detail else ""))
print(f"\n{'✗' if failures else '✓'} {len(results) - failures}/{len(results)} checks passed")
sys.exit(1 if failures else 0)
