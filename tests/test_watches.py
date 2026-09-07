"""Watches: standing asks that push widgets without a message.

"tell me when NVDA drops 3%", "watch the lakers game", "top stories every
morning at 8", "keep this updated". A watch is a sqlite row with a closed
kind, a spec and a condition; a scheduler evaluates due watches with
EXISTING builders (never the agent), commits the widget through the normal
canvas path, and pushes the component frame over a per-session SSE stream
plus a notify line. Guards: a closed kind registry, per-session and global
caps, a minimum interval, a mandatory expiry, a fire timeout.
"""
import asyncio
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

from app import main as m
from app import canvas_manager as cm
from app import database
from app.main import app
from app.services import watches as W
from app.widgets.factory import generate_widget_html

client = TestClient(app)
SESSION = "test-session-watches"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Watches", "2026-09-07T00:00:00Z"))
    cur.execute("DELETE FROM watches WHERE session_id = ?", (SESSION,))
    conn.commit()
    conn.close()
    cm.set_session_canvas(SESSION, EMPTY)
    m._session_widget_subjects.pop(SESSION, None)


# ─── parsing ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_price_alert_parses_ticker_direction_and_threshold(patch_server):
    async def fake_resolve(name):
        return {"nvidia": "NVDA", "tesla": "TSLA"}.get(name.lower().strip(), "")
    patch_server("_resolve_ticker", fake_resolve)
    w = await W.parse_watch("tell me when NVDA drops 3%", {})
    assert w["kind"] == "price_alert" and w["spec"]["symbol"] == "NVDA"
    assert w["spec"]["condition"] == {"field": "change_pct", "op": "<=", "value": -3.0}
    w = await W.parse_watch("alert me if tesla goes above $400", {})
    assert w["kind"] == "price_alert" and w["spec"]["symbol"] == "TSLA"
    assert w["spec"]["condition"] == {"field": "price", "op": ">=", "value": 400.0}
    w = await W.parse_watch("let me know when nvidia rises 5 percent", {})
    assert w["spec"]["condition"]["op"] == ">=" and w["spec"]["condition"]["value"] == 5.0


@pytest.mark.asyncio
async def test_game_briefing_and_refresh_parse_with_canvas_defaults():
    w = await W.parse_watch("watch the lakers game", {"league": "nba"})
    assert w["kind"] == "game" and w["spec"] == {"league": "nba", "team": "lakers"}
    w = await W.parse_watch("watch the nba game tonight", {})
    assert w["kind"] == "game" and w["spec"]["league"] == "nba" and w["spec"]["team"] == ""
    w = await W.parse_watch("top stories every morning at 8", {"place": "Seattle"})
    assert w["kind"] == "briefing" and w["spec"] == {"hour": 8, "minute": 0, "place": "Seattle"}
    w = await W.parse_watch("news briefing every day at 7:30pm", {})
    assert w["spec"]["hour"] == 19 and w["spec"]["minute"] == 30
    w = await W.parse_watch("keep this updated", {}, focus_widget_id="scores-abc")
    assert w["kind"] == "refresh" and w["spec"] == {"widget_id": "scores-abc"}


@pytest.mark.asyncio
async def test_ambiguous_or_neighbouring_asks_are_not_watches(patch_server):
    async def none(name):
        return ""
    patch_server("_resolve_ticker", none)
    for text in ["watch a video of cats", "remind me every morning to take my pills",
                 "tell me when", "watch the lakers game",      # no league anywhere
                 "keep this updated",                          # no focus widget
                 "tell me when it drops 3%"]:                  # no ticker
        assert await W.parse_watch(text, {}) is None, text


def test_watch_intent_regex_is_narrow():
    for t in ["tell me when NVDA drops 3%", "alert me if tesla hits 400", "watch the lakers game",
              "top stories every morning at 8", "keep this updated"]:
        assert W.WATCH_INTENT_RE.search(t.lower()), t
    for t in ["weather in tokyo", "watch a video of cats", "play some music", "nvda stock"]:
        assert not W.WATCH_INTENT_RE.search(t.lower()), t


# ─── the store and its guards ──────────────────────────────────────────────

def test_dao_round_trip_and_guards():
    _seed()
    row = W.create_watch(SESSION, "price_alert", {"symbol": "NVDA", "condition": {"field": "change_pct", "op": "<=", "value": -3}},
                         label="NVDA drops 3%", interval_s=5)
    assert row and row["interval_s"] == W.WATCH_KINDS["price_alert"]["min_interval"], "interval clamped up"
    assert row["expires"] > time.time() + 3600, "expiry is mandatory"
    assert [w["id"] for w in database.list_watches(SESSION)] == [row["id"]]
    assert [w["id"] for w in database.due_watches(time.time() + 10)] == [row["id"]]
    database.mark_watch_run(row["id"], next_run=time.time() + 300, last_fp="x", fired=True)
    got = database.list_watches(SESSION)[0]
    assert got["fire_count"] == 1 and got["last_fp"] == "x"
    assert database.due_watches(time.time()) == []
    assert database.delete_watch(row["id"], "some-other-session") is False, "cross-session delete refused"
    assert database.delete_watch(row["id"], SESSION) is True
    assert database.list_watches(SESSION) == []


def test_caps_and_expiry():
    _seed()
    for i in range(W.MAX_WATCHES_PER_SESSION):
        assert W.create_watch(SESSION, "refresh", {"widget_id": f"w{i}"}, label=f"w{i}")
    assert W.create_watch(SESSION, "refresh", {"widget_id": "one-too-many"}, label="x") is None
    assert W.create_watch(SESSION, "not_a_kind", {}, label="x") is None
    conn = database.get_connection()
    conn.execute("UPDATE watches SET expires = ? WHERE session_id = ?", (time.time() - 1, SESSION))
    conn.commit(); conn.close()
    database.expire_watches(time.time())
    assert database.list_watches(SESSION) == []


# ─── firing ────────────────────────────────────────────────────────────────

def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_price_alert_fires_only_when_the_condition_holds(patch_server):
    _seed()
    row = W.create_watch(SESSION, "price_alert",
                         {"symbol": "NVDA", "condition": {"field": "change_pct", "op": "<=", "value": -3}},
                         label="NVDA drops 3%")
    q = cm.subscribe_session_events(SESSION)
    try:
        calls = []

        async def snap(symbol, rng="1d"):
            calls.append(symbol)
            return {"symbol": symbol, "price": 100.0, "change_pct": -1.0,
                    "labels": [1, 2], "values": [101, 100]}
        patch_server("stock_snapshot", snap)
        fired = await W.fire_watch(database.list_watches(SESSION)[0])
        assert fired is False and calls == ["NVDA"]
        assert _drain(q) == [], "nothing pushed while the condition is false"
        assert 'data-widget-type="stock_card"' not in (cm.get_session_canvas(SESSION) or "")

        async def snap_down(symbol, rng="1d"):
            return {"symbol": symbol, "price": 96.0, "change_pct": -4.2,
                    "labels": [1, 2], "values": [100, 96]}
        patch_server("stock_snapshot", snap_down)
        fired = await W.fire_watch(database.list_watches(SESSION)[0])
        assert fired is True
        lines = _drain(q)
        kinds = [json.loads(l[6:].strip())["type"] for l in lines]
        assert kinds == ["component", "notify"], kinds
        assert 'data-widget-type="stock_card"' in (cm.get_session_canvas(SESSION) or "")
        assert f'id="watch-{row["id"]}"' in (cm.get_session_canvas(SESSION) or "")
        notify = json.loads(lines[1][6:].strip())
        assert "NVDA" in notify["title"] and "-4.2" in notify["body"]
    finally:
        cm.unsubscribe_session_events(SESSION, q)


@pytest.mark.asyncio
async def test_game_watch_fires_on_a_score_change_and_is_quiet_otherwise(patch_server):
    _seed()
    W.create_watch(SESSION, "game", {"league": "nba", "team": "lakers"}, label="Lakers game")
    q = cm.subscribe_session_events(SESSION)
    try:
        board = {"league": "NBA", "title": "NBA", "events": [
            {"name": "LAL @ BOS", "state": "in", "status": "Q2",
             "home": {"name": "Boston Celtics", "score": "40"}, "away": {"name": "Los Angeles Lakers", "score": "38"}}]}

        async def scores(league):
            return dict(board)
        patch_server("sports_scores", scores)
        assert await W.fire_watch(database.list_watches(SESSION)[0]) is True, "first look is a change"
        _drain(q)
        assert await W.fire_watch(database.list_watches(SESSION)[0]) is False, "same score, no push"
        assert _drain(q) == []
        board["events"][0]["away"]["score"] = "41"
        assert await W.fire_watch(database.list_watches(SESSION)[0]) is True
        assert [json.loads(l[6:])["type"] for l in _drain(q)] == ["component", "notify"]
    finally:
        cm.unsubscribe_session_events(SESSION, q)


@pytest.mark.asyncio
async def test_a_watch_never_reaches_the_agent_or_a_destructive_action():
    src = open(W.__file__).read()
    assert "/agent" not in src and "app_action" not in src and "run_action" not in src
    for kind in W.WATCH_KINDS.values():
        assert kind["min_interval"] >= 30 and kind["ttl"] > 0


# ─── routing + API ─────────────────────────────────────────────────────────

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


def test_watch_ask_creates_a_row_and_a_watch_widget(patch_server):
    _seed()

    async def fake_resolve(name):
        return "NVDA" if "nvidia" in name.lower() else ""
    patch_server("_resolve_ticker", fake_resolve)

    async def router_boom(*a, **k):
        raise AssertionError("router ran for a watch ask")
    patch_server("route_with_llm", router_boom)
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "tell me when nvidia drops 3%", "provider": "vllm",
            "model": "nemotron35", "current_canvas": EMPTY})
    assert res.status_code == 200
    debug = _events(res.text, "debug")
    assert debug and (debug[0]["path"], debug[0].get("id_prefix")) == ("fast-path", "watch"), debug
    rows = database.list_watches(SESSION)
    assert len(rows) == 1 and rows[0]["kind"] == "price_alert"
    html = _events(res.text, "component")[-1]["content"]
    assert 'data-widget-type="watch"' in html and "NVDA" in html


def test_watch_api_lists_and_deletes_session_scoped():
    _seed()
    row = W.create_watch(SESSION, "refresh", {"widget_id": "scores-1"}, label="keep scores updated")
    got = client.get(f"/api/watches?session_id={SESSION}").json()
    assert [w["id"] for w in got["watches"]] == [row["id"]]
    assert client.delete(f"/api/watches/{row['id']}?session_id=other").status_code == 404
    assert client.delete(f"/api/watches/{row['id']}?session_id={SESSION}").status_code == 200
    assert client.get(f"/api/watches?session_id={SESSION}").json()["watches"] == []


def test_watch_widget_renders_active_watches_with_cancel():
    html = generate_widget_html("watch", "watch-list", {"watches": [
        {"id": "w1", "kind": "price_alert", "label": "NVDA drops 3%", "interval_s": 300, "fire_count": 0}]})
    assert 'data-widget-type="watch"' in html and "NVDA drops 3%" in html
    assert "watchListWidget(" in html


def test_events_route_streams_pushed_lines():
    """A pushed line reaches a subscriber; the route wraps this queue."""
    _seed()
    q = cm.subscribe_session_events(SESSION)
    try:
        cm.push_session_event(SESSION, 'data: {"type": "notify", "title": "x", "body": "y"}\n\n')
        assert q.get_nowait().startswith('data: {"type": "notify"')
    finally:
        cm.unsubscribe_session_events(SESSION, q)
    assert any(r.path == "/session/{session_id}/events" for r in app.routes)
