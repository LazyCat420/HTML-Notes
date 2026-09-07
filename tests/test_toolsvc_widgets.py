"""tools-service (:5590) has 26 cached, keyless route families and the canvas
used none of them. This pack maps ten of them onto EXISTING widget types —
earthquakes/wildfires/ISS → map, launches → data_card, APOD → image, moon and
tides → kpi_row, solar flares / commodity movers / trends → table — reached
three ways: a deterministic fast lane (TOOLSVC_ASK_RE table), the LLM router
(one type per kind), and the agent (canvas_add_widget config={'toolsvc': kind}
rehydrates server-side). Payloads below were captured live on 2026-09-07.
"""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

from app import main as m
from app import database
from app.main import app
from app.services import toolsvc

client = TestClient(app)
SESSION = "test-session-toolsvc"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'

PAYLOADS = {
    "/weather/earthquakes": [
        {"usgsId": "aka1", "magnitude": 2.0, "place": "49 km W of Nanwalek, Alaska",
         "time": "2026-09-07T09:14:34.583Z", "url": "https://earthquake.usgs.gov/x",
         "longitude": -152.786, "latitude": 59.397, "depth": 71.2, "title": "M 2.0 - Nanwalek"},
        {"usgsId": "ci2", "magnitude": 5.4, "place": "10 km S of Ridgecrest, CA",
         "time": "2026-09-07T08:00:00Z", "longitude": -117.6, "latitude": 35.6, "depth": 8.0},
    ],
    "/weather/wildfires": {"count": 1, "events": [
        {"eonetId": "EONET_23868", "title": "Wildfire Ayers Pond, Prairie, Montana",
         "coordinates": {"lng": -104.83, "lat": 46.65}, "magnitudeValue": 565.8,
         "magnitudeUnit": "acres", "date": "2026-09-03T01:45:00Z"}]},
    "/weather/iss": {"position": {"latitude": 42.93, "longitude": 173.31, "timestamp": "2026-09-07T09:20:02Z"},
                     "astronauts": {"total": 12, "people": [{"name": "Sunita Williams", "craft": "ISS"}]}},
    "/weather/launches": {"count": 1, "launches": [
        {"name": "Falcon 9 Block 5 | Starlink Group 15-24", "status": "Launch Successful",
         "statusAbbrev": "Success", "net": "2026-09-06T14:26:54Z", "provider": None,
         "padLocation": None, "imageUrl": "https://img/falcon.png", "webcastUrl": None,
         "missionDescription": None}]},
    "/weather/apod": {"status": "no_data", "lastFetch": None},
    "/weather/moon-phase": {"phaseName": "Waning Crescent", "phaseEmoji": "🌘",
                            "illuminationPercent": 17.8, "ageInDays": 25.44,
                            "nextNewMoonUtc": "2026-09-11T11:30:22Z", "nextFullMoonUtc": "2026-09-26T05:52:23Z"},
    "/weather/tides": {"count": 2, "predictions": [
        {"time": "2026-09-07 10:38", "height": 1.552, "type": "high", "stationId": "9414849"},
        {"time": "2026-09-07 15:28", "height": 0.853, "type": "low", "stationId": "9414849"}]},
    "/weather/space-weather": {"flares": [
        {"classType": "C5.7", "peakTime": "2026-09-01T21:10:00.000Z", "sourceLocation": "N15E90",
         "activeRegionNumber": None, "link": "https://x/flr"}]},
    "/market/commodities/summary": {"total": 124, "gainers": [
        {"ticker": "MTF=F", "name": "Micro WTI Crude Oil", "price": 104.75, "change": 8.05,
         "changePercent": 8.32, "unit": "USD/barrel"}], "losers": [
        {"ticker": "NEAR-USD", "name": "NEAR Protocol", "price": 2.34, "change": -0.09,
         "changePercent": -3.77, "unit": "USD"}]},
    "http://10.0.0.16:8801/api/status": {
        "comfy_head": {"active": False, "enabled": False, "queue": None},
        "deepseek": {"desired": True, "phase": "running", "id": "GLM-5.3-Flash-EXL3", "health": "Up 39 hours",
                     "activity": {"running": 1, "waiting": 0, "tok_s": 22.9, "kv_pct": 13.4,
                                  "prefix_hit_pct": 68.9, "ttft_avg_s": 45.55}}},
    "http://10.0.0.16:8888/api/v1/portfolio/performance": {
        "bot_id": "test_bot", "current_value": 103809.38, "cash": 24432.39, "pnl": 3809.38, "pnl_pct": 3.81,
        "realized_pnl": 2257.54, "win_rate": 28.57, "total_trades": 59, "open_positions": 27},
    "http://10.0.0.16:8888/api/v1/portfolio": {"total_value": 103809.38, "positions": [
        {"ticker": "ALLY", "qty": 157.99, "avg_entry_price": 45.59, "current_price": 43.73, "sector": None},
        {"ticker": "AMD", "qty": 0.74, "avg_entry_price": 477.6, "current_price": 500.0, "sector": "Information Technology"}]},
    "http://10.0.0.16:8888/api/v1/watchlist": [
        {"ticker": "SCHD", "source": "portfolio_nav", "added_at": "2026-09-06T05:31:06", "health_score": 50}],
    "http://10.0.0.16:8888/api/v1/sectors/market-regime": {"regime": {
        "date": "2026-09-07T00:00:00", "breadth_sp500": 50.0, "dollar_change_5d": -0.55, "dollar_index": 99.12,
        "regime_label": "Neutral", "vix_signal": "Normal", "vix_term_signal": "Normal", "yield_signal": "Normal",
        "yield_2y10y_spread": 0.0}},
    "http://10.0.0.16:5050/api/dashboard/stats": {"active_pests": 0, "active_plants": 3, "pending_tasks": 2,
                                                  "recent_harvests": 1, "tasks_due_soon": 1},
    "/trend/trends": {"count": 2, "trends": [
        {"name": "Small thing", "source": "mastodon", "volume": 12, "url": "https://m/1"},
        {"name": "Killing of the Clancy children", "source": "wikipedia", "volume": 466193,
         "url": "https://en.wikipedia.org/wiki/x"}]},
}


@pytest.fixture
def fake_toolsvc(patch_server):
    async def get(path, params=None, timeout=8.0):
        return PAYLOADS.get(path, {"is_error": True, "error": f"no fixture for {path}"})
    patch_server("toolsvc_get", get)
    return get


# ─── mappers ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_earthquakes_become_a_map_with_magnitude_coloured_markers(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("earthquakes")
    assert (wtype, prefix) == ("map", "earthquakes")
    marks = cfg["markers"]
    assert marks[0]["label"].startswith("M5.4"), "biggest first"
    assert marks[0]["lat"] == 35.6 and marks[0]["lon"] == -117.6
    assert marks[0]["color"] != marks[1]["color"], "a M5 and a M2 must not look alike"
    assert cfg["subject"] == {"kind": "topic", "value": "earthquakes"}


@pytest.mark.asyncio
async def test_wildfires_and_iss_are_maps(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("wildfires")
    assert wtype == "map" and cfg["markers"][0]["emoji"] == "🔥"
    assert cfg["markers"][0]["lat"] == 46.65 and cfg["markers"][0]["lon"] == -104.83
    wtype, prefix, cfg = await m.build_toolsvc_config("iss")
    assert (wtype, prefix) == ("map", "iss")
    assert len(cfg["markers"]) == 1 and cfg["markers"][0]["lat"] == 42.93
    assert "12" in cfg["markers"][0]["detail"]


@pytest.mark.asyncio
async def test_launches_are_a_data_card_with_photos(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("launches")
    assert (wtype, prefix) == ("data_card", "launches")
    item = cfg["items"][0]
    assert item["title"].startswith("Falcon 9") and item["image"] == "https://img/falcon.png"
    assert "Success" in (item.get("badge") or "") and item["description"]


@pytest.mark.asyncio
async def test_moon_and_tides_are_kpi_rows(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("moon")
    assert (wtype, prefix) == ("kpi_row", "moon")
    labels = [x["label"] for x in cfg["metrics"]]
    assert "Phase" in labels and "Illumination" in labels
    assert any("Waning Crescent" in str(x["value"]) for x in cfg["metrics"])
    wtype, prefix, cfg = await m.build_toolsvc_config("tides")
    assert wtype == "kpi_row" and len(cfg["metrics"]) == 2
    assert cfg["metrics"][0]["label"] == "High tide" and "10:38" in cfg["metrics"][0]["delta"]


@pytest.mark.asyncio
async def test_flares_commodities_and_trends_are_tables(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("space_weather")
    assert wtype == "table" and cfg["rows"][0]["classType"] == "C5.7"
    wtype, prefix, cfg = await m.build_toolsvc_config("commodities")
    assert wtype == "table"
    keys = [c["key"] for c in cfg["columns"]]
    assert "changePercent" in keys and any(c.get("format") == "percent" for c in cfg["columns"])
    assert {r["ticker"] for r in cfg["rows"]} == {"MTF=F", "NEAR-USD"}
    wtype, prefix, cfg = await m.build_toolsvc_config("trends")
    assert wtype == "table"
    assert cfg["rows"][0]["volume"] == 466193, "sorted by volume, biggest first"
    assert "[Killing of the Clancy children](https://en.wikipedia.org/wiki/x)" == cfg["rows"][0]["name"]


@pytest.mark.asyncio
async def test_the_users_own_services_render_as_kpi_rows_and_tables(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("spark")
    assert (wtype, prefix) == ("kpi_row", "spark")
    assert any("tok/s" in x["label"] for x in cfg["metrics"]) and "GLM-5.3-Flash-EXL3" in cfg["subtitle"]
    wtype, prefix, cfg = await m.build_toolsvc_config("portfolio")
    assert wtype == "kpi_row" and any(x["label"] == "P&L" and x["delta"] == "+3.81%" for x in cfg["metrics"])
    wtype, prefix, cfg = await m.build_toolsvc_config("positions")
    assert wtype == "table" and cfg["rows"][0]["ticker"] == "ALLY" and cfg["rows"][1]["pnl_pct"] == 4.69
    wtype, prefix, cfg = await m.build_toolsvc_config("watchlist")
    assert wtype == "table" and cfg["rows"][0]["ticker"] == "SCHD"
    wtype, prefix, cfg = await m.build_toolsvc_config("market_regime")
    assert wtype == "kpi_row" and cfg["metrics"][0]["value"] == "Neutral"
    wtype, prefix, cfg = await m.build_toolsvc_config("garden")
    assert wtype == "kpi_row" and cfg["metrics"][0]["value"] == "3"


@pytest.mark.asyncio
async def test_absolute_urls_bypass_the_tools_service_base(monkeypatch):
    seen = []

    class _Resp:
        status_code = 200
        def json(self): return {"ok": True}

    class _Client:
        def __init__(self, timeout=None): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params=None):
            seen.append(url); return _Resp()
    monkeypatch.setattr(toolsvc.httpx, "AsyncClient", _Client)
    await toolsvc.toolsvc_get("http://10.0.0.16:8801/api/status")
    await toolsvc.toolsvc_get("/weather/iss")
    assert seen == ["http://10.0.0.16:8801/api/status", f"{toolsvc.TOOLS_SERVICE_URL}/weather/iss"]


@pytest.mark.asyncio
async def test_apod_with_no_data_is_an_honest_card_not_a_broken_image(fake_toolsvc):
    wtype, prefix, cfg = await m.build_toolsvc_config("apod")
    assert wtype == "data_card" and "not published" in cfg["answer"].lower()


@pytest.mark.asyncio
async def test_a_dead_source_builds_nothing(patch_server):
    async def dead(path, params=None, timeout=8.0):
        return {"is_error": True, "error": "boom"}
    patch_server("toolsvc_get", dead)
    assert await m.build_toolsvc_config("earthquakes") is None
    assert await m.build_toolsvc_config("nonsense") is None


# ─── the ask table ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("recent earthquakes", "earthquakes"),
    ("any quakes today?", "earthquakes"),
    ("wildfires in the west", "wildfires"),
    ("where is the iss", "iss"),
    ("where is the space station right now", "iss"),
    ("upcoming rocket launches", "launches"),
    ("when is the next spacex launch", "launches"),
    ("nasa picture of the day", "apod"),
    ("what's the moon phase tonight", "moon"),
    ("tide times", "tides"),
    ("any solar flares lately", "space_weather"),
    ("commodities today", "commodities"),
    ("what's trending right now", "trends"),
    ("trending topics", "trends"),
    ("how are the sparks doing", "spark"),
    ("gpu status", "spark"),
    ("my portfolio", "portfolio"),
    ("how's the bot doing", "portfolio"),
    ("what am I holding", "positions"),
    ("my open positions", "positions"),
    ("show my watchlist", "watchlist"),
    ("are we risk-on or risk-off", "market_regime"),
    ("how's my garden", "garden"),
])
def test_ask_table_maps_phrasings_to_kinds(text, kind):
    assert m.toolsvc_kind_for(text) == kind


@pytest.mark.parametrize("text", [
    "launch the trading client",      # an app launch, not a rocket
    "trending stocks this month",     # stock discovery owns this
    "what's trending in crypto",      # ditto
    "weather in tokyo",
    "add a chart of nvidia",
    "moon by pink floyd",
    "watch the lakers game",          # a watch, not the watchlist
    "tell me when NVDA drops 3%",
])
def test_ask_table_leaves_neighbouring_asks_alone(text):
    assert m.toolsvc_kind_for(text) is None


# ─── routing ───────────────────────────────────────────────────────────────

def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "toolsvc", "2026-09-07T00:00:00Z"))
    conn.commit()
    conn.close()


def _events(sse, kind):
    out = []
    for line in sse.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            if ev.get("type") == kind:
                out.append(ev)
    return out


def _post(message, canvas=EMPTY):
    return client.post("/session/message", json={
        "session_id": SESSION, "message": message, "provider": "vllm",
        "model": "nemotron35", "current_canvas": canvas})


def test_earthquake_ask_takes_the_fast_lane_not_the_web_search_map(fake_toolsvc, patch_server):
    _seed()

    async def router_boom(message_, context_block):
        raise AssertionError("route_with_llm ran for a deterministic toolsvc ask")
    patch_server("route_with_llm", router_boom)

    async def map_boom(*a, **k):
        raise AssertionError("the web-search map builder ran for an earthquake ask")
    patch_server("build_map_config", map_boom)

    with patch("httpx.AsyncClient.stream") as agent_stream:
        res = _post("recent earthquakes")
    assert res.status_code == 200
    debug = _events(res.text, "debug")
    assert debug and (debug[0]["path"], debug[0].get("id_prefix")) == ("fast-path", "earthquakes"), debug
    comp = _events(res.text, "component")
    assert comp and 'data-widget-type="map"' in comp[-1]["content"]
    urls = [str(a) for c in agent_stream.call_args_list for a in c.args]
    assert not any("/agent" in u for u in urls), "the agent was reached"


def test_a_dead_source_falls_through_instead_of_a_dead_card(patch_server):
    _seed()

    async def dead(path, params=None, timeout=8.0):
        return {"is_error": True}
    patch_server("toolsvc_get", dead)
    seen = []

    async def router_reply(message_, context_block):
        seen.append(message_)
        return {"reply": "stub", "reason": "test", "checks": {}}
    patch_server("route_with_llm", router_reply)
    with patch("httpx.AsyncClient.stream"):
        res = _post("upcoming rocket launches")
    assert res.status_code == 200 and seen, "must fall through to the next tier"


class _Stream:
    status_code = 200

    def __init__(self, events):
        self._chunks = [f'data: {json.dumps(e)}\n' for e in events] + ['data: {"type": "done"}\n']

    async def aiter_text(self):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


def test_agent_can_ask_for_a_kind_and_the_server_fills_the_widget(fake_toolsvc, patch_server):
    """canvas_add_widget(config={'toolsvc': 'iss'}) — the model names the
    kind, never types coordinates; the server even corrects the widget_type."""
    _seed()

    async def _no_plan(*a, **kw):
        return None
    patch_server("route_with_llm", _no_plan)
    ev = {"type": "tool_execution", "status": "done",
          "tool": {"name": "mcp__lazy-tool-service__canvas_add_widget",
                   "args": {"widget_type": "data_card", "config": {"toolsvc": "iss"}}, "result": "ok"}}
    with patch("httpx.AsyncClient.stream", return_value=_Stream([ev])):
        res = _post("Add an audio box please")
    comp = _events(res.text, "component")
    assert comp, "no component frame"
    html = comp[-1]["content"]
    assert 'data-widget-type="map"' in html, "the server picks the right widget for the kind"
    assert "International Space Station" in html


# ─── the agent's other door: the action registry ───────────────────────────

def test_action_registry_exposes_every_kind_read_only():
    reg = json.load(open(os.path.join(os.path.dirname(m.__file__), "app_actions.json")))
    for kind, spec in toolsvc.TOOLSVC_KINDS.items():
        block = reg["actions"].get(spec.get("app_id", "tools-service")) or {}
        assert kind in block, f"{kind} missing from app_actions.json"
        assert block[kind]["method"] == "GET" and block[kind]["destructive"] is False
        assert block[kind]["url"].endswith(spec["path"])


def test_router_catalog_and_prompt_know_the_kinds():
    for kind in toolsvc.TOOLSVC_KINDS:
        assert kind in m.ROUTER_WIDGETS, kind
    from tests._sources import MESSAGE_SRC
    assert "config={'toolsvc': '<earthquakes|" in MESSAGE_SRC
