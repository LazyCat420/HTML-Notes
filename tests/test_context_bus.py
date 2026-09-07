"""The context bus: widgets can see each other.

Builders took only the message, so a weather widget on canvas told "traffic"
nothing, and a stock card told "news" nothing. Now every committed widget
carries a typed subject {kind, value} (stamped on its root so it survives a
restart), the turn context lists SUBJECTS ON CANVAS, builders default their
parameters from the newest subject of the right kind when the ask names
none, and "compare these" / "same for X" resolve against recent subjects.
"""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

# main FIRST (see tests/test_live_widgets.py for why)
from app import main as m
from app import canvas_manager as cm
from app import config_builders as cb
from app import database
from app.main import app
from app.services import location as loc
from app.widgets.factory import generate_widget_html

client = TestClient(app)
SESSION = "test-session-context-bus"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Context Bus", "2026-09-07T00:00:00Z"))
    conn.commit()
    conn.close()
    m._session_widget_subjects.pop(SESSION, None)
    cm._session_turn_ledger.pop(SESSION, None)
    cm.set_session_canvas(SESSION, EMPTY)


def _canvas(*widgets):
    return '<div id="dashboard-grid" class="dashboard-grid">' + "".join(
        generate_widget_html(t, i, c) for (t, i, c) in widgets) + "</div>"


# ─── subjects ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wtype,cfg,expected", [
    ("weather", {"location": "Tokyo"}, {"kind": "place", "value": "Tokyo"}),
    ("scoreboard", {"league": "NBA"}, {"kind": "league", "value": "NBA"}),
    ("stock_card", {"symbol": "NVDA"}, {"kind": "ticker", "value": "NVDA"}),
    ("crypto_card", {"coin_id": "bitcoin", "name": "Bitcoin"}, {"kind": "coin", "value": "Bitcoin"}),
    ("profile_card", {"title": "Marie Curie"}, {"kind": "person", "value": "Marie Curie"}),
    ("map", {"subject": {"kind": "place", "value": "Seattle"}}, {"kind": "place", "value": "Seattle"}),
    ("data_card", {"title": "Anything"}, None),
    ("youtube_player", {"video_id": "x"}, None),
])
def test_subject_is_derived_or_taken_from_the_config(wtype, cfg, expected):
    assert cm.derive_widget_subject(wtype, cfg) == expected


def test_subject_is_stamped_on_the_widget_root():
    html = generate_widget_html("weather", "weather-1", {"location": "Tokyo", "current": {}, "daily": []})
    assert 'data-subject-kind="place"' in html and 'data-subject-value="Tokyo"' in html
    html = generate_widget_html("data_card", "news-1", {"title": "x", "items": []})
    assert "data-subject-kind" not in html


def test_subjects_are_newest_first_and_only_for_widgets_still_on_canvas():
    _seed()
    w = [("weather", "weather-1", {"location": "Tokyo", "current": {}, "daily": []}),
         ("stock_card", "stock-nvda", {"symbol": "NVDA", "values": [1], "labels": [1]}),
         ("scoreboard", "scores-1", {"league": "NBA", "events": []})]
    for t, i, c in w:
        cm.remember_widget_subject(SESSION, i, t, c)
    cm.set_session_canvas(SESSION, _canvas(*w[:2]))  # the scoreboard was dismissed
    subs = cm.canvas_subjects(SESSION)
    assert [(s["id"], s["kind"], s["value"]) for s in subs] == [
        ("stock-nvda", "ticker", "NVDA"), ("weather-1", "place", "Tokyo")]
    # re-remembering moves a widget to the front (it was just refreshed/edited)
    cm.remember_widget_subject(SESSION, "weather-1", "weather", {"location": "Osaka"})
    assert cm.canvas_subjects(SESSION)[0]["value"] == "Osaka"


def test_subjects_recover_from_the_dom_stamps_after_a_restart():
    _seed()
    cm.set_session_canvas(SESSION, _canvas(
        ("weather", "weather-1", {"location": "Tokyo", "current": {}, "daily": []}),
        ("stock_card", "stock-amd", {"symbol": "AMD", "values": [1], "labels": [1]})))
    m._session_widget_subjects.pop(SESSION, None)   # the restart
    subs = cm.canvas_subjects(SESSION)
    assert {(s["kind"], s["value"]) for s in subs} == {("place", "Tokyo"), ("ticker", "AMD")}
    assert cm.canvas_defaults(SESSION) == {"place": "Tokyo", "ticker": "AMD"}


def test_canvas_defaults_take_the_newest_per_kind():
    _seed()
    w = [("weather", "weather-1", {"location": "Tokyo", "current": {}, "daily": []}),
         ("map", "traffic-1", {"subject": {"kind": "place", "value": "Seattle"}, "markers": []}),
         ("stock_card", "stock-nvda", {"symbol": "NVDA", "values": [1], "labels": [1]})]
    for t, i, c in w:
        cm.remember_widget_subject(SESSION, i, t, c)
    cm.set_session_canvas(SESSION, _canvas(*w))
    assert cm.canvas_defaults(SESSION) == {"place": "Seattle", "ticker": "NVDA"}
    assert [s["value"] for s in cm.recent_subjects(SESSION, n=2, kind="place")] == ["Seattle", "Tokyo"]


def test_turn_context_lists_subjects_between_canvas_and_history():
    _seed()
    w = [("weather", f"weather-{i}", {"location": f"City{i}", "current": {}, "daily": []}) for i in range(8)]
    for t, i, c in w:
        cm.remember_widget_subject(SESSION, i, t, c)
    cm.set_session_canvas(SESSION, _canvas(*w))
    cm.record_turn(SESSION, "weather in city7", "fast-path:weather", [("weather-7", "weather", "City7", "")])
    block = cm.build_turn_context(SESSION)["context_block"]
    i_canvas, i_subj, i_hist = block.index("CURRENT CANVAS:"), block.index("SUBJECTS ON CANVAS"), block.index("RECENT TURNS")
    assert i_canvas < i_subj < i_hist
    assert "place City7 (#weather-7)" in block
    assert i_subj < 1200, "the router truncates at 1200 chars — subjects must be inside"


# ─── defaults ──────────────────────────────────────────────────────────────

def test_extract_location_precedence(monkeypatch):
    monkeypatch.setattr(loc.database, "get_user_facts", lambda: {"location": "Home Town"})
    assert loc.extract_location("weather in Paris", default="Seattle") == "Paris"
    assert loc.extract_location("weather", default="Seattle") == "Seattle"
    assert loc.extract_location("weather") == "Home Town"


def test_bare_weather_ask_defaults_to_the_place_on_canvas(patch_server):
    _seed()
    cm.remember_widget_subject(SESSION, "traffic-1", "map", {"subject": {"kind": "place", "value": "Seattle"}})
    canvas = _canvas(("map", "traffic-1", {"subject": {"kind": "place", "value": "Seattle"}, "markers": []}))
    cm.set_session_canvas(SESSION, canvas)
    seen = []

    async def fake_weather(location):
        seen.append(location)
        return {"location": location, "current": {"temp": 1, "condition": "Clear"}, "daily": []}
    patch_server("get_weather", fake_weather)

    async def router_boom(*a, **k):
        raise AssertionError("router ran")
    patch_server("route_with_llm", router_boom)
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "weather", "provider": "vllm",
            "model": "nemotron35", "current_canvas": canvas})
    assert res.status_code == 200 and seen == ["Seattle"], seen


@pytest.mark.asyncio
async def test_router_weather_and_sports_branches_use_defaults_only_when_the_query_names_nothing(patch_server):
    seen = {"w": [], "s": []}

    async def fake_weather(location):
        seen["w"].append(location)
        return {"location": location, "current": {}, "daily": []}

    async def fake_scores(league):
        seen["s"].append(league)
        return {"league": league, "events": []}
    patch_server("get_weather", fake_weather)
    patch_server("sports_scores", fake_scores)
    await cb.build_router_widget({"type": "weather", "query": ""}, SESSION, "how's the weather",
                                 defaults={"place": "Oslo"})
    await cb.build_router_widget({"type": "weather", "query": "Tokyo"}, SESSION, "weather in tokyo",
                                 defaults={"place": "Oslo"})
    await cb.build_router_widget({"type": "sports", "query": ""}, SESSION, "any scores?",
                                 defaults={"league": "nba"})
    assert [x.lower() for x in seen["w"]] == ["oslo", "tokyo"]
    assert seen["s"] == ["nba"]


# ─── anaphora ──────────────────────────────────────────────────────────────

def test_compare_these_resolves_the_two_most_recent_tickers(patch_server):
    _seed()
    w = [("stock_card", "stock-nvda", {"symbol": "NVDA", "values": [1], "labels": [1]}),
         ("stock_card", "stock-amd", {"symbol": "AMD", "values": [1], "labels": [1]})]
    for t, i, c in w:
        cm.remember_widget_subject(SESSION, i, t, c)
    canvas = _canvas(*w)
    cm.set_session_canvas(SESSION, canvas)
    specs = []

    async def fake_build(spec, session_id, message, defaults=None):
        specs.append(spec)
        return ("chart", "stock-compare", {"title": "NVDA vs AMD", "labels": [1], "values": [1]})
    patch_server("build_router_widget", fake_build)

    async def router_boom(*a, **k):
        raise AssertionError("router ran for an anaphora the canvas can resolve")
    patch_server("route_with_llm", router_boom)
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "compare these two", "provider": "vllm",
            "model": "nemotron35", "current_canvas": canvas})
    assert res.status_code == 200
    assert specs == [{"type": "stock", "query": "NVDA vs AMD"}], specs


def test_same_for_x_rewrites_the_previous_ask(patch_server):
    _seed()
    canvas = _canvas(("weather", "weather-1", {"location": "Seattle", "current": {}, "daily": []}))
    cm.set_session_canvas(SESSION, canvas)
    cm.remember_widget_subject(SESSION, "weather-1", "weather", {"location": "Seattle"})
    cm.record_turn(SESSION, "weather in seattle", "fast-path:weather", [("weather-1", "weather", "Seattle", "")])
    seen = []

    async def fake_weather(location):
        seen.append(location)
        return {"location": location, "current": {}, "daily": []}
    patch_server("get_weather", fake_weather)

    async def router_boom(*a, **k):
        raise AssertionError("router ran")
    patch_server("route_with_llm", router_boom)
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "same for tokyo", "provider": "vllm",
            "model": "nemotron35", "current_canvas": canvas})
    assert res.status_code == 200 and seen == ["tokyo"], seen
