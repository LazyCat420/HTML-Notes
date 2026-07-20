"""Load the LIVE map iframe document and prove the traffic overlay is painted.

Not just "is the tileLayer in the JS" — actually let Leaflet run, wait for the
traffic tiles to load, and confirm coloured flow pixels are on screen above the
basemap. Compares the same view with and without traffic so the difference is
attributable to the overlay and not to the basemap's own road colours.
"""
import base64
import json
import sys

from playwright.sync_api import sync_playwright

OUT = "/tmp/claude-1000/-home-lazycat-github-projects-sun/d6eeae79-b23c-4682-96f5-177348115221/scratchpad"
HOST = "http://10.0.0.16:8035"

# Central Oakland / Bay Bridge approach — dense, reliably congested road network.
BASE = {"center": {"lat": 37.8044, "lon": -122.2712}, "zoom": 13, "markers": [
    {"lat": 37.8044, "lon": -122.2712, "label": "Oakland", "emoji": "🚦"}]}


def tok(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode()


def load(pg, payload, shot):
    pg.goto(f"{HOST}/widgets/map?d={tok(payload)}", wait_until="load")
    # Leaflet has to instantiate, request tiles, and paint them.
    pg.wait_for_timeout(6000)
    info = pg.evaluate("""() => {
      const imgs = [...document.querySelectorAll('img.leaflet-tile')];
      const traffic = imgs.filter(i => i.src.includes('/widgets/map/traffic/'));
      return {
        totalTiles: imgs.length,
        trafficTiles: traffic.length,
        trafficLoaded: traffic.filter(i => i.complete && i.naturalWidth > 0).length,
        layers: document.querySelectorAll('.leaflet-tile-pane .leaflet-layer').length,
        attribution: document.querySelector('.leaflet-control-attribution')?.textContent || '',
      };
    }""")
    pg.screenshot(path=f"{OUT}/{shot}")
    return info


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 900, "height": 600})
    fails = []

    off = load(pg, BASE, "map_no_traffic.png")
    on = load(pg, {**BASE, "traffic": True}, "map_traffic.png")

    print(f"traffic OFF: {off}")
    print(f"traffic ON : {on}")

    if on["trafficTiles"] == 0:
        fails.append("no traffic tiles requested at all")
    elif on["trafficLoaded"] < on["trafficTiles"]:
        fails.append(f"only {on['trafficLoaded']}/{on['trafficTiles']} traffic tiles loaded")
    if on["layers"] < 2:
        fails.append(f"expected 2 tile layers (base + traffic), saw {on['layers']}")
    if "TomTom" not in on["attribution"]:
        fails.append(f"TomTom attribution missing: {on['attribution']!r}")
    if off["trafficTiles"]:
        fails.append("traffic tiles requested even with traffic off")

    # Pixel proof: the overlay must actually CHANGE the rendered map.
    from PIL import Image, ImageChops
    a = Image.open(f"{OUT}/map_no_traffic.png").convert("RGB")
    c = Image.open(f"{OUT}/map_traffic.png").convert("RGB")
    diff = ImageChops.difference(a, c)
    changed = sum(1 for px in diff.getdata() if px[0] + px[1] + px[2] > 30)
    pct = 100.0 * changed / (a.size[0] * a.size[1])
    print(f"pixels changed by the overlay: {changed} ({pct:.2f}% of the map)")
    if pct < 0.5:
        fails.append(f"overlay changed only {pct:.2f}% of pixels — not visibly painted")

    b.close()

print("\n" + ("FAIL: " + "; ".join(fails) if fails else "PASS — traffic overlay is painted on the map"))
sys.exit(1 if fails else 0)
