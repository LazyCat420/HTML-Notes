"""End-to-end: clicking the mini player's title hands the track to music-player.

The sending half of the canvas -> music-player handoff. What must hold:

  1. The click opens ONE new tab at the music-player WEB app (:3232, not the
     :8002 API, which serves JSON to a browser tab).
  2. The link carries the track id, the position it was at, and the labels
     the widget is showing.
  3. The canvas widget STOPS. Two players on the same song is the failure the
     whole feature exists to avoid.
  4. The widget stays on the canvas, so playback can be resumed locally.

The URL shape is pinned offline by tests/test_music_handoff.mjs. This script
exists for what that cannot see: that a real click on the real card reaches
the handler at all.

Position note: YouTube playback depends on the scraper/CDN, and some tracks
403 on the open-ended Range an <audio> opens with (see music-player's
AUDIO_PIPELINE.md). If audio is flowing the real position is used; if it is
not, the element is parked at a known time first — the point of the assert is
that the handler reads the LIVE element rather than sending a constant.

Requires the live stack: html-notes (:8035) + music-player (:8002/:3232).
Live check — not CI.

Run: .venv/bin/python scripts/music_handoff_check.py
"""
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

HOST = "http://10.0.0.16:8035"
WEB = "3232"
PARKED_AT = 51

results, failures = [], 0


def check(name, ok, detail=""):
    global failures
    results.append((ok, name, detail))
    if not ok:
        failures += 1


DATA = """() => {
    const w = document.querySelector('[x-data*="musicPlayerWidget"]');
    if (!w) return null;
    const d = Alpine.$data(w);
    if (!d) return null;
    return {
        track: d.currentTrack, genre: d.genreFilter, isPlaying: d.isPlaying,
        t: d.audio ? d.audio.currentTime : null,
        paused: d.audio ? d.audio.paused : null,
        queue: d.queue.length,
    };
}"""

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
    ctx = b.new_context()
    pg = ctx.new_page()

    pg.goto(HOST, wait_until="domcontentloaded")
    pg.wait_for_selector("#chat-input", timeout=20000)
    pg.fill("#chat-input", "play some jazz")
    pg.press("#chat-input", "Enter")

    pg.wait_for_selector('[x-data*="musicPlayerWidget"]', timeout=45000)

    # A track must be SELECTED before the title can be handed anywhere.
    try:
        pg.wait_for_function(
            """() => { const w = document.querySelector('[x-data*="musicPlayerWidget"]');
                       const d = w && Alpine.$data(w); return d && d.currentTrack; }""",
            timeout=90000)
        check("widget resolved a track", True)
    except Exception:
        check("widget resolved a track", False, str(pg.evaluate(DATA)))
        for ok, name, detail in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))
        sys.exit(1)

    # Prefer a real playing position; park the element if the stream is dead.
    played_for_real = True
    try:
        pg.wait_for_function(
            """() => { const d = Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]'));
                       return d.audio && !d.audio.paused && d.audio.currentTime > 5; }""",
            timeout=45000)
    except Exception:
        played_for_real = False
        pg.evaluate(
            """parked => { const d = Alpine.$data(document.querySelector('[x-data*="musicPlayerWidget"]'));
                           if (d.audio) { d.audio.currentTime = parked; } }""",
            PARKED_AT)
    print(f"position source: {'live playback' if played_for_real else f'parked at {PARKED_AT}s'}")

    before = pg.evaluate(DATA)
    expect_t = int(before["t"] or 0)

    # The click must open the tab itself — that is what keeps it inside the
    # user gesture and out of the popup blocker.
    with ctx.expect_page(timeout=15000) as popup_info:
        pg.locator('[x-data*="musicPlayerWidget"] h4').first.click()
    popup = popup_info.value
    url = urllib.parse.urlparse(popup.url)
    q = urllib.parse.parse_qs(url.query)

    check("a new tab opened", True)
    check(f"it opened the WEB app, not the API (port {url.port})", str(url.port) == WEB, popup.url)
    check("link carries the same track id",
          q.get("track", [None])[0] == before["track"]["id"],
          f'{q.get("track")} vs {before["track"]["id"]}')
    got_t = int(q.get("t", ["-1"])[0])
    check(f"link carries the live position (~{expect_t}s, sent {got_t}s)",
          abs(got_t - expect_t) <= 2, f"expected~{expect_t} got {got_t}")
    check("link carries the title shown on the card",
          q.get("title", [None])[0] == before["track"]["title"], str(q.get("title")))
    check("link carries the artist shown on the card",
          q.get("artist", [None])[0] == before["track"]["artist"], str(q.get("artist")))
    if before["genre"]:
        check("link carries the genre", q.get("genre", [None])[0] == before["genre"], str(q.get("genre")))

    pg.wait_for_timeout(600)
    after = pg.evaluate(DATA)
    check("the canvas widget stopped playing", after["paused"] is True, f'paused={after["paused"]}')
    check("the widget is still on the canvas", after["track"] is not None)
    check("the widget kept its queue for a local resume", after["queue"] >= 1, f'queue={after["queue"]}')

    popup.close()
    b.close()

print()
for ok, name, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok and detail else ""))
print(f"\n{len(results) - failures}/{len(results)} passed")
sys.exit(1 if failures else 0)
