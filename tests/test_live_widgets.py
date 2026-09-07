"""Live widgets: a scoreboard, a forecast or a stock card is only true at the
moment it rendered. Every committed live widget now remembers its RECIPE —
the router spec that rebuilds it — so it can be refreshed in place without
an agent turn, and the recipe survives a restart.
"""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

# main FIRST: canvas_manager imports main, and main's trailing
# `from app.canvas_manager import *` would run against a half-initialised
# module if canvas_manager were imported first — every route then loses
# those names (NameError: find_existing_widget) for the whole session.
from app import main as m
from app import canvas_manager as cm
from app import database
from app.main import app
from app.widgets import factory
from app.widgets.factory import generate_widget_html, live_ttl

client = TestClient(app)
SESSION = "test-session-live-widgets"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Live Widgets", "2026-09-07T00:00:00Z"))
    conn.commit()
    conn.close()
    m._session_widget_recipes.pop(SESSION, None)


# ─── recipes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wtype,cfg,expected", [
    ("weather", {"location": "Tokyo", "current": {}}, {"type": "weather", "query": "Tokyo"}),
    ("scoreboard", {"league": "NBA", "events": []}, {"type": "sports", "query": "NBA"}),
    ("stock_card", {"symbol": "NVDA", "values": [1]}, {"type": "stock", "query": "NVDA"}),
    ("crypto_card", {"coin_id": "bitcoin", "name": "Bitcoin"}, {"type": "crypto", "query": "bitcoin"}),
    ("data_card", {"title": "News"}, None),
    ("youtube_player", {"video_id": "x"}, None),
])
def test_recipe_is_derived_from_the_live_types_only(wtype, cfg, expected):
    assert cm.derive_widget_recipe(wtype, cfg) == expected


def test_fast_path_weather_turn_remembers_its_recipe_in_memory_and_sqlite(patch_server):
    _seed()

    async def fake_weather(location):
        return {"location": "Tokyo", "current": {"temp": 21, "condition": "Clear"},
                "daily": [], "title": "Tokyo"}
    patch_server("get_weather", fake_weather)

    res = client.post("/session/message", json={
        "session_id": SESSION, "message": "weather in tokyo", "provider": "vllm",
        "model": "nemotron35", "current_canvas": EMPTY})
    assert res.status_code == 200
    recipes = m._session_widget_recipes.get(SESSION) or {}
    assert recipes, "no recipe remembered for the weather widget"
    wid, recipe = next(iter(recipes.items()))
    assert wid.startswith("weather-")
    assert recipe["spec"] == {"type": "weather", "query": "Tokyo"}
    assert recipe["widget_type"] == "weather"
    stored = database.get_widget_state(f"recipe:{SESSION}:{wid}")
    assert stored and json.loads(stored)["spec"]["query"] == "Tokyo"


def test_recipes_reload_from_sqlite_after_a_restart():
    _seed()
    database.set_widget_state(f"recipe:{SESSION}:weather-restart1",
                              json.dumps({"spec": {"type": "weather", "query": "Oslo"},
                                          "widget_type": "weather"}))
    m._session_widget_recipes.pop(SESSION, None)
    assert cm.get_widget_recipe(SESSION, "weather-restart1")["spec"]["query"] == "Oslo"


# ─── the refresh endpoint ──────────────────────────────────────────────────

def _canvas_with(wtype, wid, cfg):
    return f'<div id="dashboard-grid" class="dashboard-grid">{generate_widget_html(wtype, wid, cfg)}</div>'


def test_refresh_rebuilds_the_widget_in_place_and_bumps_the_version(patch_server):
    _seed()
    wid = "scores-refresh01"
    old = {"league": "NBA", "title": "NBA", "events": [
        {"name": "LAL @ BOS", "state": "in", "status": "Q2", "home": {"name": "Boston", "score": "40"},
         "away": {"name": "Lakers", "score": "38"}}]}
    cm.set_session_canvas(SESSION, _canvas_with("scoreboard", wid, old))
    v0 = cm._session_canvas_version[SESSION]
    cm.remember_widget_recipe(SESSION, wid, "scoreboard", old)

    new = {**old, "events": [{**old["events"][0], "home": {"name": "Boston", "score": "55"}}]}

    async def fake_build(spec, session_id, message):
        assert spec == {"type": "sports", "query": "NBA"}
        return ("scoreboard", "scores", new)
    patch_server("build_router_widget", fake_build)

    res = client.post(f"/api/widget/{SESSION}/{wid}/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["changed"] is True and body["version"] > v0
    assert body["content"].count('class="widget-container') == 1, "must replace, not stack"
    assert "55" in body["content"] and f'id="{wid}"' in body["content"]


def test_refresh_with_unchanged_data_commits_nothing(patch_server):
    _seed()
    wid = "weather-same0001"
    cfg = {"location": "Tokyo", "current": {"temp": 21, "condition": "Clear"}, "daily": []}
    cm.set_session_canvas(SESSION, _canvas_with("weather", wid, cfg))
    v0 = cm._session_canvas_version[SESSION]
    cm.remember_widget_recipe(SESSION, wid, "weather", cfg)

    async def fake_build(spec, session_id, message):
        return ("weather", "weather", dict(cfg))
    patch_server("build_router_widget", fake_build)

    body = client.post(f"/api/widget/{SESSION}/{wid}/refresh").json()
    assert body["changed"] is False and body["version"] == v0


def test_refresh_404s_for_an_unknown_widget_and_502s_when_the_source_is_down(patch_server):
    _seed()
    assert client.post(f"/api/widget/{SESSION}/weather-nope/refresh").status_code == 404
    wid = "weather-down0001"
    cfg = {"location": "Tokyo", "current": {}, "daily": []}
    cm.set_session_canvas(SESSION, _canvas_with("weather", wid, cfg))
    cm.remember_widget_recipe(SESSION, wid, "weather", cfg)

    async def dead(spec, session_id, message):
        return None
    patch_server("build_router_widget", dead)
    assert client.post(f"/api/widget/{SESSION}/{wid}/refresh").status_code == 502


# ─── TTLs and the chrome ───────────────────────────────────────────────────

def test_live_ttl_table():
    live = {"events": [{"state": "in"}]}
    idle = {"events": [{"state": "post"}]}
    assert live_ttl("scoreboard", live) == 30
    assert live_ttl("scoreboard", idle) == 300
    assert live_ttl("weather", {}) == 600
    assert live_ttl("crypto_card", {}) == 60
    assert live_ttl("stock_card", {}) in (60, 900)
    assert live_ttl("youtube_player", {}) == 0
    assert live_ttl("data_card", {}) == 0


def test_live_widgets_carry_the_refresh_chrome():
    board = generate_widget_html("scoreboard", "scores-1", {
        "league": "NBA", "title": "NBA", "events": [
            {"name": "x", "state": "in", "status": "Q1", "home": {"name": "A", "score": "1"},
             "away": {"name": "B", "score": "0"}}]})
    assert 'x-data="liveWidget({ttl: 30})"' in board
    weather = generate_widget_html("weather", "weather-1", {
        "location": "Tokyo", "current": {"temp": 1, "condition": "Clear"}, "daily": []})
    assert 'x-data="liveWidget({ttl: 600})"' in weather
    video = generate_widget_html("youtube_player", "video-1", {"video_id": "abc", "title": "t"})
    assert "liveWidget(" not in video, "media never auto-refreshes"
