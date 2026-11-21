"""Removal must never wipe a canvas it could not understand.

Live failure 2026-11-20: "remove it" decomposed all three widgets (the empty
target satisfied `not target_tokens`, which matched everything), and "clear
skies in Denver" — a weather ask — deleted the Denver weather card because the
bare verb "clear" counted as a removal.

These pin three things: the intent regex only reads anchored, noun-backed
removals; an empty target resolves to ONE widget (focus, else most recent) and
never to the whole canvas; and every removal turn says what happened instead of
going silent.
"""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_removal_safety.db")

from app import main as m
from app import database
from app.main import app

client = TestClient(app)
SESSION = "test-session-removal-safety"

CANVAS = (
    '<div id="dashboard-grid" class="dashboard-grid">'
    '<div class="canvas-widget" id="widget-weather-1" data-widget-type="weather" data-title="Denver">Denver 42F</div>'
    '<div class="canvas-widget" id="widget-clock-1" data-widget-type="clock" data-title="Clock">12:00</div>'
    '<div class="canvas-widget" id="widget-chart-1" data-widget-type="stock_chart" data-title="NVDA">NVDA</div>'
    '</div>'
)


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Removal Safety", "2026-11-20T00:00:00Z"))
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


def _post(message, canvas=CANVAS, focus=None):
    body = {"session_id": SESSION, "message": message, "provider": "vllm",
            "model": "nemotron35", "current_canvas": canvas}
    if focus:
        body["focus_widget_id"] = focus
    return client.post("/session/message", json=body)


# ── intent regex ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "close the clock",
    "delete the grocery list",
    "hide the map",
    "remove it",
    "remove the weather widget",
    "get rid of the news",
    "dismiss that card",
    "stop the timer",
    "clear the widget",
    "please close the chart",
])
def test_removal_intent_matches_real_removals(text):
    assert m.REMOVAL_INTENT_RE.search(text), text


@pytest.mark.parametrize("text", [
    "clear skies in Denver",
    "clear the search box",           # UI field, not a widget
    "drop the ball on this",
    "stop the bleeding",
    "what's a good skin care routine",
    "how long to boil an egg",
    "watch the weather in Seattle",
    "weather in Paris",
    "play some music",
])
def test_removal_intent_leaves_ordinary_asks_alone(text):
    assert not m.REMOVAL_INTENT_RE.search(text), text


# ── endpoint behaviour ──────────────────────────────────────────────────────

def test_remove_it_with_focus_closes_only_the_focused_widget(patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    res = _post("remove it", focus="widget-clock-1")
    assert res.status_code == 200
    components = _events(res.text, "component")
    assert len(components) == 1
    html = components[0]["content"]
    assert "widget-clock-1" not in html
    assert "widget-weather-1" in html and "widget-chart-1" in html

    chunks = " ".join(e["content"] for e in _events(res.text, "chunk"))
    assert "Clock" in chunks and "Closed" in chunks


def test_remove_it_without_focus_closes_only_the_most_recent_widget(patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    res = _post("remove it")
    html = _events(res.text, "component")[0]["content"]
    assert "widget-chart-1" not in html       # last on the canvas
    assert "widget-weather-1" in html and "widget-clock-1" in html


def test_remove_it_on_empty_canvas_touches_nothing_and_says_so(patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    res = _post("remove it", canvas='<div id="dashboard-grid" class="dashboard-grid"></div>')
    assert res.status_code == 200
    assert not _events(res.text, "component")
    chunks = " ".join(e["content"] for e in _events(res.text, "chunk"))
    assert "no widget" in chunks.lower()


def test_removal_emits_a_debug_frame(patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    res = _post("close the clock")
    debug = _events(res.text, "debug")
    assert debug and debug[0]["widget_type"] == "remove", debug
    html = _events(res.text, "component")[0]["content"]
    assert "widget-clock-1" not in html
    assert "widget-weather-1" in html and "widget-chart-1" in html


def test_unmatched_target_reports_instead_of_wiping(patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    res = _post("close the crypto card")
    assert not _events(res.text, "component")
    chunks = " ".join(e["content"] for e in _events(res.text, "chunk"))
    assert "Nothing matched" in chunks


def test_list_item_removal_never_takes_the_widget_path(patch_server):
    """'remove eggs from my shopping list' names a strong verb, but the target
    is a LIST ITEM; the list-item guard keeps it off the canvas mutation."""
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    with patch("httpx.AsyncClient.stream", return_value=_DoneStream()):
        res = _post("remove eggs from my shopping list")
    debug = _events(res.text, "debug")
    assert not any(d.get("widget_type") == "remove" for d in debug), debug
    assert any(d.get("path") == "agent" for d in debug), debug


def test_clear_skies_is_not_a_removal(patch_server):
    """The verb "clear" must not reach the removal path on a weather ask."""
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    with patch("httpx.AsyncClient.stream", side_effect=AssertionError("agent streamed")):
        res = _post("clear skies in Denver", canvas=CANVAS.replace(
            '<div class="canvas-widget" id="widget-weather-1" data-widget-type="weather" data-title="Denver">Denver 42F</div>',
            '<div class="canvas-widget" id="widget-weather-2" data-widget-type="weather" data-title="Denver">Denver 42F</div>'))
    debug = _events(res.text, "debug")
    assert not any(d.get("widget_type") == "remove" for d in debug), debug


def test_widget_color_edit_is_not_settings(patch_server):
    """'make the chart green' names a widget part — it must not repaint the app."""
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    with patch("httpx.AsyncClient.stream", return_value=_DoneStream()):
        res = _post("make the chart green")
    debug = _events(res.text, "debug")
    assert not any(d.get("widget_type") == "settings" for d in debug), debug


def test_skin_care_ask_is_not_settings(patch_server):
    _seed()
    patch_server("route_with_llm", lambda *a, **k: None)

    with patch("httpx.AsyncClient.stream", return_value=_DoneStream()):
        res = _post("what's a good skin care routine")
    debug = _events(res.text, "debug")
    assert not any(d.get("widget_type") == "settings" for d in debug), debug
