"""The decision node: reply or act?

Every routing tier used to have exactly two outputs — which widget, or defer
to the agent — and the agent's first rule was "call the tool immediately". A
greeting therefore had no legal outcome except a widget. Observed live on
2026-09-06: "hello" with a finance canvas up became "lead with news + trending
stocks" and rendered an NYT article about Gen-Z phone greetings under the
title "NEWS: HELLO (GREETING)".

These pin the third outcome — a conversational reply, no widget — end to end.
"""
import ast
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import database
from app.main import app

client = TestClient(app)
SESSION = "test-session-reply-node"


def _seed_session():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Reply Node Test", "2026-09-06T00:00:00Z"))
    conn.commit()
    conn.close()


def _events(sse_text, kind):
    out = []
    for line in sse_text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            evt = json.loads(line[6:])
        except Exception:
            continue
        if evt.get("type") == kind:
            out.append(evt)
    return out


REPLY = "Hey there. Ask me for news, weather, a stock, or a map — or say show my apps."


@pytest.mark.asyncio
async def test_route_with_llm_returns_a_reply_verdict(patch_server):
    async def fake(instruction, max_tokens=400):
        return {"reply": REPLY, "reason": "greeting", "checks": {"wants": "converse"}}
    patch_server("fast_llm_json", fake)
    plan = await m.route_with_llm("hello", "")
    assert plan and plan.get("reply") == REPLY
    assert "widgets" not in plan and not plan.get("defer")


def test_reply_is_the_first_question_the_router_asks():
    """Order matters: the composition rules below it are what turned a greeting
    into a dashboard. The reply rule must precede them and the widget catalog."""
    from tests._sources import LLM_SRC
    first = LLM_SRC.index("FIRST decide whether this ask needs anything fetched or built")
    assert first < LLM_SRC.index("A NARROW single-intent ask")
    assert first < LLM_SRC.index("WIDGET TYPES:")
    assert "converse|answer|watch" in LLM_SRC, "the pre-flight `wants` enum must include converse"


@pytest.mark.parametrize("canvas", [
    '<div id="dashboard-grid" class="dashboard-grid"></div>',
    # A populated finance canvas — exactly the context that biased the router
    # into composing "news + trending stocks" for a greeting.
    '<div id="dashboard-grid" class="dashboard-grid">'
    '<div class="widget-container" id="stock-news-1" data-widget-type="data_card">'
    '<h3>Market News</h3></div>'
    '<div class="widget-container" id="stock-compare-1" data-widget-type="chart">'
    '<h3>NKE vs VST vs SCHD</h3></div></div>',
], ids=["empty-canvas", "finance-canvas"])
def test_hello_streams_a_reply_and_builds_nothing(patch_server, canvas):
    _seed_session()

    async def reply_verdict(message, context_block):
        return {"reply": REPLY, "reason": "greeting", "checks": {"wants": "converse"}}
    patch_server("route_with_llm", reply_verdict)

    async def _boom(*a, **k):
        raise AssertionError("a widget builder ran for a greeting")
    for name in ("build_news_card", "build_news_config", "build_stock_news_config",
                 "build_trending_compare_config", "build_answer_config"):
        patch_server(name, _boom)

    with patch("httpx.AsyncClient.stream") as agent_stream:
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "hello",
            "provider": "vllm", "model": "nemotron35",
            "current_canvas": canvas,
        })
        assert res.status_code == 200
        # Not `.called`: the very first TestClient request in a process also
        # runs app startup, whose boot probes use the same httpx method. Only a
        # call aimed at the gateway's /agent endpoint means the agent ran.
        urls = [str(a) for c in agent_stream.call_args_list for a in c.args]
        assert not any("/agent" in u for u in urls), f"the agent must not be reached for a reply: {urls}"

    sse = res.text
    debug = _events(sse, "debug")
    assert debug and debug[0].get("widget_type") == "reply" and debug[0].get("id_prefix") == "reply", debug
    assert debug[0].get("path") == "fast-path"
    assert _events(sse, "component") == [], "a reply turn must not paint the canvas"
    chunks = _events(sse, "chunk")
    assert chunks and REPLY in chunks[0]["content"]
    assert _events(sse, "done")


def test_open_candidates_returns_its_response():
    """`_stream_open_candidates` defined stream() and fell off the end, so an
    ambiguous app-open returned None — an empty HTTP body."""
    from tests._sources import MESSAGE_SRC
    tree = ast.parse(MESSAGE_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_stream_open_candidates")
    last = fn.body[-1]
    assert isinstance(last, ast.Return) and isinstance(last.value, ast.Call), (
        "_stream_open_candidates must return its StreamingResponse")
    assert "StreamingResponse" in ast.dump(last.value.func)


def test_clear_canvas_emits_a_debug_frame():
    """Without it, 'close everything' is invisible to an id_prefix-based gate."""
    from tests._sources import MESSAGE_SRC
    start = MESSAGE_SRC.index("def _stream_clear_canvas(")
    body = MESSAGE_SRC[start:MESSAGE_SRC.index("\n        def ", start + 10)]
    assert '"widget_type": "clear_all"' in body and '"id_prefix": "clear"' in body


def test_agent_first_rule_allows_prose():
    from tests._sources import MESSAGE_SRC
    assert '"1. Call the tool immediately. Write no preamble' not in MESSAGE_SRC
    assert "Decide first whether the ask needs a tool at all" in MESSAGE_SRC
