"""Deterministic lanes must not steal asks that belong elsewhere.

Measured live on 2026-11-20 against :8035, every case below reached the WRONG
lane: "how long to boil an egg" → directions, "what time does the lakers game
start" → clock, "add a link to my grocery list" → crypto card, "job positions
in Seattle" → the trading positions table, "watch the weather in Seattle" →
YouTube, "make the chart green" / "skin care routine" → settings panel.

Each lane keeps its real asks; these pin the new guards.
"""
import json
import os
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_routing_misroutes.db")

from app import main as m
from app import database
from app.main import app
from app.services.toolsvc import toolsvc_kind_for

client = TestClient(app)
SESSION = "test-session-misroutes"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Misroutes", "2026-11-20T00:00:00Z"))
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


class _DoneStream:
    """Async-context-manager stand-in for httpx's streaming response."""
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_text(self):
        yield 'data: {"type": "chunk", "content": "agent"}\n'
        yield 'data: {"type": "done"}\n'


def _post(message, patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)
    with patch("httpx.AsyncClient.stream", return_value=_DoneStream()):
        return client.post("/session/message", json={
            "session_id": SESSION, "message": message, "provider": "vllm",
            "model": "nemotron35", "current_canvas": EMPTY})


def _fast_path_types(res):
    return [d.get("widget_type") for d in _events(res.text, "debug")
            if d.get("path") == "fast-path"]


# ── cooking vs travel ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "how long to cook a 145F chicken to 165",
    "how long to boil an egg",
    "how long do i bake a potato",
    "how long to roast a chicken",
])
def test_cooking_asks_are_not_directions(text):
    assert m.DIRECTIONS_ASK_RE.search(text), "precondition: directions regex matches"
    assert m.COOKING_ASK_RE.search(text), text


@pytest.mark.parametrize("text", [
    "how long to the airport",
    "directions to the nearest pharmacy",
    "traffic in seattle",
    "how do i get to the space needle",
])
def test_real_travel_asks_stay_in_directions(text):
    assert m.DIRECTIONS_ASK_RE.search(text)
    assert not m.COOKING_ASK_RE.search(text), text


def test_boil_an_egg_does_not_reach_the_traffic_lane(patch_server):
    res = _post("how long to boil an egg", patch_server)
    assert "traffic" not in _fast_path_types(res), _events(res.text, "debug")


# ── league game time vs clock ───────────────────────────────────────────────

def test_lakers_game_time_does_not_reach_the_clock_lane(patch_server):
    res = _post("what time does the lakers game start", patch_server)
    assert "clock" not in _fast_path_types(res), _events(res.text, "debug")


@pytest.mark.parametrize("text", [
    "what time is it in tokyo",
    "show me a clock",
    "start a 5 minute timer",
    "set a pomodoro",
])
def test_real_clock_asks_are_not_sports(text):
    assert not re.search(
        r'\b(game|match|scores?|playing|kickoff|tipoff|face ?off|start time|tip[- ]?off)\b',
        text), text


def test_timer_still_reaches_the_clock_lane(patch_server):
    res = _post("start a 5 minute timer", patch_server)
    assert "clock" in _fast_path_types(res), _events(res.text, "debug")


# ── list edit vs crypto ─────────────────────────────────────────────────────

def test_grocery_list_link_is_not_a_crypto_card(patch_server):
    res = _post("add a link to my grocery list", patch_server)
    assert "crypto" not in _fast_path_types(res), _events(res.text, "debug")


@pytest.mark.parametrize("text", [
    "price of bitcoin",
    "who holds PEPE",
    "$BONK whales",
])
def test_real_crypto_asks_still_match(text):
    assert m.CRYPTO_WORD_RE.search(text.lower()) or m.CASHTAG_RE.search(text), text


# ── job positions vs trading positions ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    "job positions in seattle",
    "open positions at google",
    "positions available near me",
])
def test_job_searches_are_not_the_portfolio_table(text):
    assert toolsvc_kind_for(text) != "positions", text


@pytest.mark.parametrize("text", [
    "my positions",
    "what do i hold",
    "show my trading positions",
])
def test_real_portfolio_asks_still_match(text):
    assert toolsvc_kind_for(text) == "positions", text


@pytest.mark.parametrize("text", [
    "my watchlist",
    "stock watchlist",
    "market watch list",
])
def test_real_watchlist_asks_still_match(text):
    assert toolsvc_kind_for(text) == "watchlist", text


def test_job_positions_never_hits_the_trading_lane(patch_server):
    res = _post("job positions in Seattle", patch_server)
    assert "table" not in _fast_path_types(res), _events(res.text, "debug")


# ── weather vs video ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "watch the weather in Seattle",
    "show me the weather forecast for Paris",
    "what's the weather tonight",
])
def test_weather_asks_are_not_video(text):
    assert m.WEATHER_ASK_RE.search(text), text
    assert not m.EXPLICIT_VIDEO_RE.search(text), text


@pytest.mark.parametrize("text", [
    "show me a video of a cookie recipe",
    "find me a youtube clip of the aurora",
    "play a trailer for dune",
])
def test_explicit_video_asks_still_win(text):
    assert m.EXPLICIT_VIDEO_RE.search(text), text


def test_watch_the_weather_does_not_reach_the_video_lane(patch_server):
    res = _post("watch the weather in Seattle", patch_server)
    assert "video" not in _fast_path_types(res), _events(res.text, "debug")
