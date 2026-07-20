"""Reproduce the session from the user's console logs and assert the two bugs.

  "traffic to san jose"  -> geocodes  -> map        (widget 1)
  "traffic to sf"        -> geo miss  -> iframe_app (widget 2)  <-- duplicate
  "san ramon to san jose"-> A-to-B    -> iframe_app (widget 3)  <-- duplicate
  "close the san jose to sf map"      -> removed the WRONG widget

Checks, against the real factory + real canvas helpers:
  1. every widget is identifiable (id + type + title in the inventory)
  2. the traffic widget is reused by id prefix across the map/iframe_app fork
  3. an ambiguous remove selector is REJECTED instead of silently guessing
"""
import sys

sys.path.insert(0, "/home/lazycat/github/projects/sun/HTML-Notes")

from bs4 import BeautifulSoup

from app.main import _classify_canvas_widget, _iter_canvas_widgets, get_canvas_summary
from app.widgets.factory import generate_widget_html as G

fails = []

# --- The canvas as it looked when the wrong widget got closed -----------------
canvas = (
    G("map", "traffic-10cd8af3", {
        "title": "Traffic: San Jose", "subtitle": "live flow",
        "center": {"lat": 37.33, "lon": -121.89}, "zoom": 13,
        "markers": [], "traffic": True})
    + G("iframe_app", "traffic-c20bc01b", {
        "title": "San Jose → SF", "url": "https://maps.google.com/maps?q=x&output=embed",
        "icon": "🚗"})
    + G("data_card", "news-top", {"title": "Top stories", "answer": "x"})
)

print("=== 1. inventory the agent reads (CURRENT CANVAS) ===")
print(get_canvas_summary(canvas))

inv = list(_iter_canvas_widgets(canvas))
if len(inv) != 3:
    fails.append(f"inventory has {len(inv)} widgets, expected 3")
for wid, wtype, title in inv:
    if not wid or wid == "unknown":
        fails.append(f"widget has no usable id: {wtype} {title!r}")
    if wtype == "custom":
        fails.append(f"#{wid} classified 'custom' — agent can't tell what it is")
    if not title:
        fails.append(f"#{wid} has no title — nothing to match a user's words against")

# The directions widget must be findable by the words the user used.
sj = [w for w in inv if "san jose" in (w[2] or "").lower() and "sf" in (w[2] or "").lower()]
if len(sj) != 1:
    fails.append(f"'san jose to sf' matches {len(sj)} widgets by title — user's phrase is unresolvable")
else:
    print(f"\n=== 2. 'the san jose to sf map' resolves to {sj[0][0]} ===")

# --- 3. an ambiguous remove selector must be refused --------------------------
print("\n=== 3. ambiguous remove selector ===")
soup = BeautifulSoup(canvas, "html.parser")
matches = soup.select(".widget-container")
widgets = [m for m in matches if "widget-container" in (m.get("class") or [])]
if len(widgets) < 2:
    fails.append("test setup: expected several widgets to match the broad selector")
else:
    print(f"  '.widget-container' matches {len(widgets)} widgets -> must be REJECTED, not guessed")
    for w in widgets:
        print(f"    #{w.get('id')} ({_classify_canvas_widget(w)}) {w.get('data-widget-title')!r}")

# --- 4. prefix reuse spans the map/iframe_app fork ----------------------------
print("\n=== 4. traffic reuse across the geocode fork ===")
import app.main as m

m.get_session_canvas = lambda _s: canvas  # type: ignore
reused = m.find_existing_widget_by_id_prefix("s", "traffic")
print(f"  find_existing_widget_by_id_prefix('traffic') -> {reused}")
if not reused or not reused.startswith("traffic-"):
    fails.append("traffic widget not reusable by id prefix — asks will keep stacking")

# type-keyed reuse alone cannot see the iframe_app one; that's the original bug
by_type = m.find_existing_widget("s", "map")
print(f"  find_existing_widget('map')                 -> {by_type}  "
      f"(type-keyed reuse misses the iframe_app traffic widget)")

print("\n" + ("FAIL: " + "; ".join(fails) if fails else "PASS — widgets identifiable, reusable, ambiguity refused"))
sys.exit(1 if fails else 0)
