"""H8 / H9 from the 2026-09-06 battle test.

H8: "build me a custom widget that tracks my water intake" and "Add an audio
box please" came back as a fast REPLY, byte-identical 3/3, and 0 of 7
deliberately multi-step asks ever reached the agent. The router's reply
verdict won, and the re-defer guard only rescues question-shaped or cooking
asks. An imperative build must skip the router entirely.

H9: the `not is_data_ask` gate made the compose and answer lanes unreachable
for any ask containing a data word — "what is a stock split" fell through to
the LLM router because it contains "stock". The bare word "stock" is a topic;
only the words that name a FETCH (price, chart, weather, news, image…) should
send an answer-shaped ask elsewhere.
"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import database
from app.main import app

client = TestClient(app)
SESSION = "test-session-build-gate"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Build Gate", "2026-09-07T00:00:00Z"))
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
    status_code = 200

    async def aiter_text(self):
        yield 'data: {"type": "done"}\n'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


def _post(message):
    return client.post("/session/message", json={
        "session_id": SESSION, "message": message, "provider": "vllm",
        "model": "nemotron35", "current_canvas": EMPTY})


# ─── H8: imperative builds ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "build me a custom widget that counts my push-ups",
    "Add an audio box please",
    "make me a water intake tracker",
    "create a panel that shows my todo count",
])
def test_build_ask_regex_matches_imperative_builds(text):
    assert m.BUILD_ASK_RE.search(text.lower())


@pytest.mark.parametrize("text", [
    "what is a stock split",
    "weather in tokyo",
    "add milk to the grocery list",
    "add a chart of nvidia",
])
def test_build_ask_regex_leaves_ordinary_asks_alone(text):
    assert not m.BUILD_ASK_RE.search(text.lower())


def test_build_ask_skips_the_router_and_reaches_the_agent(patch_server):
    _seed()

    async def router_boom(message_, context_block):
        raise AssertionError("route_with_llm ran for an imperative build")
    patch_server("route_with_llm", router_boom)

    with patch("httpx.AsyncClient.stream", return_value=_DoneStream()) as agent_stream:
        res = _post("build me a custom widget that counts my push-ups")
    assert res.status_code == 200
    debug = _events(res.text, "debug")
    assert debug and debug[0]["path"] == "agent", debug
    assert debug[0]["router"]["status"] == "build"
    urls = [str(a) for c in agent_stream.call_args_list for a in c.args]
    assert any("/agent" in u for u in urls), "the agent was never called"


@pytest.mark.asyncio
async def test_router_re_defers_a_build_even_when_the_model_replies(patch_server):
    """Belt and braces: if a build ask ever does reach the router and the model
    answers with a chatty reply, the verdict is overridden to defer."""
    async def fake(instruction, max_tokens=400):
        return {"reply": "I can add an audio box for you. What should it play?",
                "reason": "offer", "checks": {"wants": "see"}}
    patch_server("fast_llm_json", fake)
    plan = await m.route_with_llm("build me a custom widget for my bike rides", "")
    assert plan and plan.get("defer") is True, plan


# ─── H9: the data gate ─────────────────────────────────────────────────────

def test_answer_lane_is_reachable_with_the_topic_word_stock(patch_server):
    _seed()

    async def router_boom(message_, context_block):
        raise AssertionError("route_with_llm ran for a definition ask")
    patch_server("route_with_llm", router_boom)

    async def fake_answer(message_):
        return {"title": "Stock split", "answer": "A split divides shares.", "items": []}
    patch_server("build_answer_config", fake_answer)

    with patch("httpx.AsyncClient.stream") as agent_stream:
        res = _post("what is a stock split")
    debug = _events(res.text, "debug")
    assert debug and (debug[0]["path"], debug[0].get("id_prefix")) == ("fast-path", "answer"), debug
    assert not agent_stream.called


def test_answer_lane_yields_to_a_live_price_lookup(patch_server):
    """'what is the stock price of nvidia' names a fetch — the answer builder
    must NOT claim it; the router (which knows stock_card) still runs."""
    _seed()
    seen = []

    async def router_reply(message_, context_block):
        seen.append(message_)
        return {"reply": "stub", "reason": "test", "checks": {}}
    patch_server("route_with_llm", router_reply)

    async def answer_boom(message_):
        raise AssertionError("the answer builder claimed a live price lookup")
    patch_server("build_answer_config", answer_boom)

    with patch("httpx.AsyncClient.stream"):
        res = _post("what is the stock price of nvidia")
    assert res.status_code == 200
    assert seen, "the router never ran"


def test_compose_lane_is_reachable_with_a_data_word(patch_server):
    _seed()

    async def router_boom(message_, context_block):
        raise AssertionError("route_with_llm ran for a compose ask")
    patch_server("route_with_llm", router_boom)

    async def fake_plan(message_):
        return [{"type": "answer", "query": "price of gold"},
                {"type": "image", "query": "gold bars"}]
    patch_server("build_composition_plan", fake_plan)

    async def fake_build(spec, session_id, message_, defaults=None):
        return ("data_card", spec["type"], {"title": spec["type"], "answer": "x", "items": []})
    patch_server("build_router_widget", fake_build)

    with patch("httpx.AsyncClient.stream") as agent_stream:
        res = _post("tell me everything about the price of gold")
    debug = _events(res.text, "debug")
    assert debug and debug[0]["path"] == "router", debug
    assert not agent_stream.called
