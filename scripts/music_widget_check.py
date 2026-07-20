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

    # 3. Queue: more than one track landed; the panel opens and jumping works.
    qlen = pg.evaluate(
        """() => Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]')).queue.length""")
    check("queue holds multiple tracks", qlen > 1, f"queue={qlen}")

    widget.locator('button[title="Queue"]').click()
    pg.wait_for_timeout(400)
    rows = widget.locator('[x-show="showQueue"] .group\\/row').count()
    check("queue panel shows upcoming rows", rows >= 1, f"rows={rows}")

    if rows >= 1:
        before = pg.evaluate(
            """() => Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]')).currentIndex""")
        widget.locator('[x-show="showQueue"] .group\\/row').first.click()
        pg.wait_for_timeout(400)
        after = pg.evaluate(
            """() => Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]')).currentIndex""")
        check("clicking a queue row jumps the track", after == before + 1, f"{before} -> {after}")

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
