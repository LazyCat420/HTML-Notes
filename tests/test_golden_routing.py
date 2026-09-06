"""The golden routing suite (AGENT_ROADMAP 1.1): utterance -> where it lands.

`GOLDEN` is the single source of truth. scripts/golden_routing_live.py imports
it and runs the same rows against the DEPLOYED container, reading the debug
frame's `id_prefix` — the one instrument that says which builder actually ran.
"I fixed the builder" is not done; every row here passing live is done.

Seeded with the exact messages the owner typed on 2026-09-05/06 and what each
one wrongly produced at the time.
"""
import json
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import database
from app.main import app


@dataclass(frozen=True)
class Row:
    message: str
    path: str                 # "fast-path" | "agent"
    id_prefix: Optional[str]  # None for the agent
    widgets: Optional[int]    # expected .widget-container count; None = don't care
    offline: bool = True      # can the offline harness drive it without a network?
    note: str = ""


GOLDEN = [
    # ── conversational: a reply, never a widget ─────────────────────────────
    Row("hello", "fast-path", "reply", 0, note="rendered NEWS: HELLO (GREETING) live"),
    Row("thanks", "fast-path", "reply", 0),
    # ── news, deterministic, ONE card ───────────────────────────────────────
    Row("stock market news", "fast-path", "stock-news", 1, note="Tata / Indian indices live"),
    Row("market news", "fast-path", "stock-news", 1, note="generic one-sentence overview live"),
    Row("stock market for the day please", "fast-path", "stock-news", 1,
        note="router composed 3 widgets incl. a chart of NKE/VST/SCHD/KO/ETN"),
    Row("stock market news for the day please.", "fast-path", "stock-news", 1),
    Row("news about nvidia earnings", "fast-path", "stock-news", 1),
    Row("whats going on in the news", "fast-path", "news", 1, note="searched the words 'top stories' live"),
    Row("latest news on the israel hamas ceasefire", "fast-path", "news", 1, note="1 article live"),
    # ── other words own these (offline harness cannot fake their builders) ──
    Row("bloomberg live news", "fast-path", "live", 1, offline=False),
    Row("cnn live news", "fast-path", "live", 1, offline=False),
    Row("weather in tokyo", "fast-path", "weather", 1, offline=False),
    # ── canvas control ──────────────────────────────────────────────────────
    Row("close everything", "fast-path", "clear", 0),
    # ── the agent is still the right answer for a genuine build ─────────────
    Row("Add an audio box please", "agent", None, None, offline=False),
]

client = TestClient(app)
SESSION = "test-session-golden"
FINANCE_CANVAS = (
    '<div id="dashboard-grid" class="dashboard-grid">'
    '<div class="widget-container" id="stock-news-1" data-widget-type="data_card"><h3>Market News</h3></div>'
    '<div class="widget-container" id="stock-compare-1" data-widget-type="chart"><h3>NKE vs VST</h3></div>'
    '</div>')
EMPTY_CANVAS = '<div id="dashboard-grid" class="dashboard-grid"></div>'


def _seed():
    database.init_db()
    conn = database.get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Golden", "2026-09-06T00:00:00Z"))
    conn.commit(); conn.close()


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


def _widget_count(sse) -> int:
    comps = _events(sse, "component")
    if not comps:
        return 0
    html = comps[-1].get("html") or comps[-1].get("content") or ""
    return html.count('class="widget-container') + html.count("class='widget-container")


def _drive(message, canvas, patch_server):
    """One turn through send_message with every LLM and network seam faked."""
    _seed()

    async def router_boom(message_, context_block):
        raise AssertionError(f"route_with_llm ran for a deterministic ask: {message_!r}")

    async def router_reply(message_, context_block):
        return {"reply": "Hey. Ask me for news, weather, a stock, or a map.",
                "reason": "greeting", "checks": {"wants": "converse"}}

    async def fake_card(message_, *, finance=False, general=None, depth="card", subject_hint=""):
        return {"title": "News: Test", "answer": "Something specific happened [0].",
                "subtitle": "1 stories · Reuters",
                "items": [{"title": "A story", "description": "d", "url": "https://r.com/a",
                           "meta": "Reuters", "badge": "News"}]}

    async def ground_boom(message_):
        raise AssertionError("ground_query ran inside the golden harness")

    news_ask = m.classify_news_ask(message)
    patch_server("route_with_llm", router_reply if news_ask is None and
                 message.lower().strip(" !.") in ("hello", "thanks") else router_boom)
    patch_server("build_news_card", fake_card)
    patch_server("ground_query", ground_boom)

    with patch("httpx.AsyncClient.stream") as agent_stream:
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": message,
            "provider": "vllm", "model": "nemotron35", "current_canvas": canvas})
    assert res.status_code == 200
    urls = [str(a) for c in agent_stream.call_args_list for a in c.args]
    assert not any("/agent" in u for u in urls), f"agent reached for {message!r}"
    return res.text


@pytest.mark.parametrize("row", [r for r in GOLDEN if r.offline], ids=lambda r: r.message)
def test_golden_offline(row, patch_server):
    sse = _drive(row.message, EMPTY_CANVAS, patch_server)
    debug = _events(sse, "debug")
    assert debug, f"no debug frame for {row.message!r}"
    got_path, got_prefix = debug[0].get("path"), debug[0].get("id_prefix")
    assert (got_path, got_prefix) == (row.path, row.id_prefix), (
        f"{row.message!r} -> {got_path}/{got_prefix}, expected {row.path}/{row.id_prefix}"
        + (f"  ({row.note})" if row.note else ""))
    if row.widgets is not None:
        assert _widget_count(sse) == row.widgets, f"{row.message!r} widget count"


def test_hello_on_a_finance_canvas_is_still_a_reply(patch_server):
    """The exact context that biased the live failure."""
    sse = _drive("hello", FINANCE_CANVAS, patch_server)
    debug = _events(sse, "debug")
    assert debug and debug[0].get("id_prefix") == "reply"
    # A reply emits NO component frame — that is how it leaves the existing
    # canvas alone. (Counting widgets inside a frame that must not exist was
    # the wrong assertion; an empty count here is the canvas being untouched.)
    assert _events(sse, "component") == [], "a reply must not repaint the canvas"


def test_every_golden_row_is_pinned_by_a_pure_check():
    """The pure half: the deterministic classifiers agree with the table, with
    no model in the loop at all."""
    for row in GOLDEN:
        na = m.classify_news_ask(row.message)
        if row.id_prefix in ("news", "stock-news", "stock-report"):
            assert na is not None and na.id_prefix == row.id_prefix, row
        else:
            assert na is None, f"{row.message!r} must not be claimed as news: {na}"
