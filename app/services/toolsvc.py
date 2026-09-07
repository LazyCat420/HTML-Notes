"""tools-service (:5590) — 26 cached, keyless route families the canvas never
used. This module maps ten of them onto EXISTING widget types so a new kind
costs one dict entry + one mapper, never a new renderer:

    earthquakes / wildfires / iss → map      launches → data_card
    apod → image                              moon / tides → kpi_row
    space_weather / commodities / trends → table

Three doors reach it: the deterministic fast lane (`toolsvc_kind_for`), the
LLM router (one ROUTER_WIDGETS type per kind) and the agent
(canvas_add_widget config={'toolsvc': kind} is rehydrated server-side). The
action registry (app_actions.json) exposes the same endpoints read-only so
the agent can quote a number in its spoken sentence.

Standalone on purpose: no app.main import, so it can never take part in the
main ↔ canvas_manager import cycle.
"""
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("app.main")

TOOLS_SERVICE_URL = os.getenv("TOOLS_SERVICE_URL", "http://10.0.0.16:5590").rstrip("/")

# kind → where it lives, what it renders as, the id prefix the debug frame
# and the reuse chain see, and a catalog line for the LLM router.
TOOLSVC_KINDS = {
    "earthquakes": {"path": "/weather/earthquakes", "widget": "map", "prefix": "earthquakes",
                    "catalog": 'recent EARTHQUAKES on a map (USGS). Use for "earthquakes", "quakes", "seismic activity". query ignored'},
    "wildfires": {"path": "/weather/wildfires", "widget": "map", "prefix": "wildfires",
                  "catalog": 'active WILDFIRES on a map (NASA EONET). query ignored'},
    "iss": {"path": "/weather/iss", "widget": "map", "prefix": "iss",
            "catalog": 'where the International Space Station is RIGHT NOW + who is in space. query ignored'},
    "launches": {"path": "/weather/launches", "widget": "data_card", "prefix": "launches",
                 "catalog": 'upcoming / recent ROCKET LAUNCHES with photos (Launch Library). query ignored'},
    "apod": {"path": "/weather/apod", "widget": "image", "prefix": "apod",
             "catalog": "NASA's Astronomy Picture of the Day. query ignored"},
    "moon": {"path": "/weather/moon-phase", "widget": "kpi_row", "prefix": "moon",
             "catalog": 'the MOON tonight — phase, illumination, next new/full moon. query ignored'},
    "tides": {"path": "/weather/tides", "widget": "kpi_row", "prefix": "tides",
              "catalog": "today's TIDES (NOAA, San Francisco station). query ignored"},
    "space_weather": {"path": "/weather/space-weather", "widget": "table", "prefix": "space-weather",
                      "catalog": 'SPACE WEATHER — recent solar flares (NASA DONKI). Use for "solar flares", "geomagnetic storm", "aurora forecast". query ignored'},
    "commodities": {"path": "/market/commodities/summary", "widget": "table", "prefix": "commodities",
                    "catalog": 'COMMODITY / index / crypto MOVERS — biggest gainers and losers (oil, gold, indices). query ignored'},
    "trends": {"path": "/trend/trends", "widget": "table", "prefix": "trends",
               "catalog": "WHAT'S TRENDING right now across Google, Wikipedia, HN, Mastodon, Bluesky, GitHub, TV. NOT for trending STOCKS (that is stock_trending). query ignored"},
}

# Deterministic asks, matched in this order. Each pattern is deliberately
# narrow: "launch the trading client" is an app launch, "trending stocks" is
# stock discovery, "moon" alone is a song.
_ASK_TABLE = [
    ("earthquakes", re.compile(r"\b(earthquakes?|quakes?|seismic|tremors?)\b", re.I)),
    ("wildfires", re.compile(r"\b(wild ?fires?|forest fires?|bush ?fires?)\b", re.I)),
    ("iss", re.compile(r"\b(the iss|iss (location|position|right now|now)|international space station|space station)\b", re.I)),
    ("launches", re.compile(r"\b(rocket launch(es)?|rockets?|spacex|starship|falcon 9|space launch(es)?|"
                            r"(upcoming|next|recent|latest) launch(es)?|launch schedule)\b", re.I)),
    ("apod", re.compile(r"\b(apod|astronomy (picture|photo|image)|picture of the day|nasa'?s? (picture|photo|image))\b", re.I)),
    ("moon", re.compile(r"\b(moon phase|phase of the moon|full moon|new moon|the moon tonight|moon tonight|"
                        r"what'?s the moon|how'?s the moon|lunar phase)\b", re.I)),
    ("tides", re.compile(r"\b(tides?|high tide|low tide|tide (chart|times?|table))\b", re.I)),
    ("space_weather", re.compile(r"\b(space weather|solar (flares?|storms?|wind|activity)|geomagnetic|aurora forecast|kp index)\b", re.I)),
    ("commodities", re.compile(r"\b(commodit(y|ies)|crude oil|brent|wti|oil prices?)\b", re.I)),
    ("trends", re.compile(r"(\b(what'?s|whats|what is) trending\b(?!\s+(in\s+)?(stocks?|tickers?|shares?|crypto|coins?|tokens?)))|"
                          r"\btrending (topics|now|today|right now|on the (internet|web))\b|\btop trends\b", re.I)),
]


def toolsvc_kind_for(text: str) -> Optional[str]:
    """The kind a deterministic ask names, or None."""
    t = (text or "").strip()
    if not t:
        return None
    for kind, rx in _ASK_TABLE:
        if rx.search(t):
            return kind
    return None


async def toolsvc_get(path: str, params: Optional[dict] = None, timeout: float = 8.0) -> Any:
    """GET one tools-service route. Returns the parsed JSON, or
    {"is_error": True, "error": ...} — never raises."""
    url = f"{TOOLS_SERVICE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, params=params or None)
            if r.status_code != 200:
                return {"is_error": True, "error": f"HTTP {r.status_code}"}
            return r.json()
    except Exception as e:
        logger.warning(f"[TOOLSVC] {path} failed: {e}")
        return {"is_error": True, "error": str(e)}


def _bad(payload: Any) -> bool:
    return payload is None or (isinstance(payload, dict) and payload.get("is_error"))


def _when(iso: str, fmt: str = "%b %d %H:%M UTC") -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).strftime(fmt)
    except Exception:
        return str(iso or "")[:16]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _subject(value: str) -> dict:
    return {"kind": "topic", "value": value}


# ── mappers: payload → (widget_type, id_prefix, config) ─────────────────────

def _map_earthquakes(d: Any):
    quakes = [q for q in (d if isinstance(d, list) else (d or {}).get("earthquakes") or []) if isinstance(q, dict)]
    quakes.sort(key=lambda q: _num(q.get("magnitude")), reverse=True)
    markers = []
    for q in quakes[:40]:
        mag = _num(q.get("magnitude"))
        color = "#ef4444" if mag >= 5 else "#f59e0b" if mag >= 3 else "#38bdf8"
        markers.append({"lat": q.get("latitude"), "lon": q.get("longitude"),
                        "label": f"M{mag:.1f} · {q.get('place') or q.get('title') or 'unknown'}",
                        "detail": f"{_when(q.get('time'))} · depth {_num(q.get('depth')):.0f} km",
                        "color": color})
    if not markers:
        return None
    biggest = quakes[0]
    return ("map", "earthquakes", {
        "title": "Recent earthquakes",
        "subtitle": f"{len(quakes)} in the USGS feed · biggest M{_num(biggest.get('magnitude')):.1f} {biggest.get('place') or ''}".strip(),
        "markers": markers, "zoom": 2, "subject": _subject("earthquakes")})


def _map_wildfires(d: Any):
    events = [e for e in ((d or {}).get("events") or []) if isinstance(e, dict)]
    markers = []
    for e in events[:40]:
        c = e.get("coordinates") or {}
        size = _num(e.get("magnitudeValue"))
        markers.append({"lat": c.get("lat"), "lon": c.get("lng", c.get("lon")),
                        "label": e.get("title") or "Wildfire",
                        "detail": f"{size:,.0f} {e.get('magnitudeUnit') or 'acres'} · {_when(e.get('date'), '%b %d')}",
                        "emoji": "🔥", "color": "#f97316"})
    if not markers:
        return None
    return ("map", "wildfires", {"title": "Active wildfires",
                                 "subtitle": f"{len(events)} open events · NASA EONET",
                                 "markers": markers, "zoom": 4, "subject": _subject("wildfires")})


def _map_iss(d: Any):
    pos = (d or {}).get("position") or {}
    if pos.get("latitude") is None:
        return None
    astro = (d or {}).get("astronauts") or {}
    people = [p.get("name") for p in (astro.get("people") or []) if isinstance(p, dict) and p.get("name")]
    total = astro.get("total") or len(people)
    return ("map", "iss", {
        "title": "International Space Station",
        "subtitle": f"{total} people in space · {_when(pos.get('timestamp'), '%H:%M UTC')}",
        "markers": [{"lat": pos.get("latitude"), "lon": pos.get("longitude"), "label": "ISS",
                     "detail": f"{total} people in space: " + ", ".join(people[:6]) + (" …" if len(people) > 6 else ""),
                     "emoji": "🛰️", "color": "#a78bfa"}],
        "zoom": 3, "subject": _subject("the ISS")})


def _map_launches(d: Any):
    launches = [l for l in ((d or {}).get("launches") or []) if isinstance(l, dict)]
    items = []
    for l in launches[:8]:
        bits = [l.get("status") or "", _when(l.get("net"))]
        who = " · ".join(x for x in (l.get("provider"), l.get("padLocation")) if x)
        desc = " · ".join(b for b in bits if b)
        if l.get("missionDescription"):
            desc += f" — {str(l['missionDescription'])[:220]}"
        items.append({"title": l.get("name") or "Launch", "description": desc,
                      "url": l.get("webcastUrl") or "", "image": l.get("imageUrl") or "",
                      "meta": who, "badge": l.get("statusAbbrev") or ""})
    if not items:
        return None
    return ("data_card", "launches", {"title": "Rocket launches", "icon": "🚀",
                                      "subtitle": f"{len(launches)} in the Launch Library window",
                                      "items": items, "subject": _subject("rocket launches")})


def _map_apod(d: Any):
    d = d or {}
    url = d.get("url") or d.get("hdurl") or ""
    if d.get("status") == "no_data" or not url:
        return ("data_card", "apod", {
            "title": "Astronomy picture of the day", "icon": "🔭",
            "answer": "NASA has not published today's picture yet — try again later in the day.",
            "items": [], "subject": _subject("NASA picture of the day")})
    title = d.get("title") or "Astronomy picture of the day"
    if str(d.get("mediaType") or d.get("media_type") or "").lower() == "video":
        return ("data_card", "apod", {"title": title, "icon": "🔭", "answer": str(d.get("explanation") or "")[:600],
                                      "items": [{"title": "Watch today's video", "url": url, "description": ""}],
                                      "subject": _subject("NASA picture of the day")})
    return ("image", "apod", {"title": title, "url": url,
                              "caption": str(d.get("explanation") or "")[:300],
                              "subject": _subject("NASA picture of the day")})


def _map_moon(d: Any):
    d = d or {}
    if not d.get("phaseName"):
        return None
    return ("kpi_row", "moon", {
        "title": "The moon tonight",
        "metrics": [
            {"label": "Phase", "value": f"{d.get('phaseEmoji') or ''} {d.get('phaseName')}".strip()},
            {"label": "Illumination", "value": f"{_num(d.get('illuminationPercent')):.0f}", "unit": "%"},
            {"label": "Age", "value": f"{_num(d.get('ageInDays')):.1f}", "unit": "days"},
            {"label": "Next new moon", "value": _when(d.get("nextNewMoonUtc"), "%b %d")},
            {"label": "Next full moon", "value": _when(d.get("nextFullMoonUtc"), "%b %d")},
        ], "subject": _subject("the moon")})


def _map_tides(d: Any):
    preds = [p for p in ((d or {}).get("predictions") or []) if isinstance(p, dict)]
    if not preds:
        return None
    metrics = []
    for p in preds[:6]:
        kind = str(p.get("type") or "").lower()
        t = str(p.get("time") or "")
        metrics.append({"label": f"{'High' if kind == 'high' else 'Low'} tide",
                        "value": f"{_num(p.get('height')):.2f}", "unit": "m",
                        "delta": f"at {t[-5:] if len(t) >= 5 else t}",
                        "good": "up" if kind == "high" else "down"})
    station = preds[0].get("stationId") or ""
    return ("kpi_row", "tides", {"title": "Tides today",
                                 "subtitle": f"NOAA station {station} (San Francisco)" if station else "NOAA",
                                 "metrics": metrics, "subject": _subject("tides")})


def _map_space_weather(d: Any):
    flares = [f for f in ((d or {}).get("flares") or []) if isinstance(f, dict)]
    rows = [{"classType": f.get("classType") or "", "peakTime": _when(f.get("peakTime")),
             "region": f.get("sourceLocation") or (f"AR{f['activeRegionNumber']}" if f.get("activeRegionNumber") else "—"),
             "link": f"[DONKI]({f['link']})" if f.get("link") else ""} for f in flares[-20:]]
    rows.reverse()
    if not rows:
        return ("data_card", "space-weather", {"title": "Space weather", "icon": "☀️",
                                               "answer": "No solar flares in NASA's recent window — quiet sun.",
                                               "items": [], "subject": _subject("space weather")})
    return ("table", "space-weather", {
        "title": "Solar flares — recent", "icon": "☀️",
        "columns": [{"key": "classType", "label": "Class"}, {"key": "peakTime", "label": "Peak"},
                    {"key": "region", "label": "Region"}, {"key": "link", "label": "Source"}],
        "rows": rows, "subject": _subject("space weather")})


def _map_commodities(d: Any):
    d = d or {}
    rows = []
    for side in ("gainers", "losers"):
        for r in (d.get(side) or []):
            if isinstance(r, dict):
                rows.append({"ticker": r.get("ticker") or "", "name": r.get("name") or "",
                             "price": r.get("price"), "changePercent": r.get("changePercent"),
                             "unit": r.get("unit") or ""})
    if not rows:
        return None
    return ("table", "commodities", {
        "title": "Commodities & indices — movers",
        "subtitle": f"{d.get('total') or len(rows)} instruments tracked",
        "columns": [{"key": "ticker", "label": "Ticker"}, {"key": "name", "label": "Name"},
                    {"key": "price", "label": "Price", "format": "number"},
                    {"key": "changePercent", "label": "Change", "format": "percent"},
                    {"key": "unit", "label": "Unit"}],
        "rows": rows, "sort": {"key": "changePercent", "dir": "desc"},
        "subject": _subject("commodities")})


def _map_trends(d: Any):
    trends = [t for t in ((d or {}).get("trends") or []) if isinstance(t, dict)]
    trends.sort(key=lambda t: _num(t.get("volume")), reverse=True)
    rows = []
    for t in trends[:25]:
        name = str(t.get("name") or "").strip()
        url = t.get("url") or ""
        rows.append({"name": f"[{name}]({url})" if url else name, "source": t.get("source") or "",
                     "volume": t.get("volume")})
    if not rows:
        return ("data_card", "trends", {"title": "Trending now", "icon": "📈",
                                        "answer": "Nothing is trending in the feeds right now.",
                                        "items": [], "subject": _subject("trending topics")})
    srcs = (d or {}).get("sources") or {}
    live = [k for k, v in srcs.items() if isinstance(v, dict) and (v.get("count") or 0) > 0]
    return ("table", "trends", {
        "title": "Trending right now", "icon": "📈",
        "subtitle": f"{len(trends)} items · " + ", ".join(live[:6]) if live else f"{len(trends)} items",
        "columns": [{"key": "name", "label": "Topic"}, {"key": "source", "label": "Source"},
                    {"key": "volume", "label": "Volume", "format": "number"}],
        "rows": rows, "subject": _subject("trending topics")})


_MAPPERS = {
    "earthquakes": _map_earthquakes, "wildfires": _map_wildfires, "iss": _map_iss,
    "launches": _map_launches, "apod": _map_apod, "moon": _map_moon, "tides": _map_tides,
    "space_weather": _map_space_weather, "commodities": _map_commodities, "trends": _map_trends,
}


async def build_toolsvc_config(kind: str) -> Optional[tuple]:
    """(widget_type, id_prefix, config) for a kind, or None when the kind is
    unknown or its source did not answer — the caller falls through, it never
    renders a dead card."""
    spec = TOOLSVC_KINDS.get(kind)
    if not spec:
        return None
    payload = await toolsvc_get(spec["path"])
    if _bad(payload):
        return None
    try:
        return _MAPPERS[kind](payload)
    except Exception as e:
        logger.warning(f"[TOOLSVC] mapper for {kind} failed: {e}")
        return None
