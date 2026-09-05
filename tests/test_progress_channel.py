"""The in-flight progress channel: what the browser is told while a slow agent
turn is still working.

A research turn takes 30-120s. Everything prism knows about that time — which
tool is running, what it was asked for, how far through the agentic loop it is —
already arrives on the SSE stream; the proxy used to discard nearly all of it,
so the canvas drew a fake asymptotic creep and read as stuck.

The invariants these tests hold down:

1. `tool_call` carries the ARGS, not just the name. "html_notes_web_search" is
   not a progress report; "searching: nvidia q3 earnings" is. The browser has
   always read this field — it was the server that never sent it.
2. Args are SUMMARIZED, never passed through. This frame now rides on every tool
   call, down the same stream as the canvas HTML, and a canvas_add_widget
   `config` runs to several KB.
3. `phase` is a closed vocabulary the client keys off, so a status line can be
   reworded without changing what the canvas shows — and a status carrying NO
   phase (a `thinking` event) leaves the phase where it was rather than bouncing
   the card backwards.
4. Prism's `iteration_progress` becomes a `progress` frame with a real
   denominator. That is the whole point: a bar with a true fraction, instead of
   a timer pretending to be one.
"""
import json
import os

import pytest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

import app.main as m
from app.main import (_MAX_DETAIL_CHARS, _MAX_RESEARCH_CALLS, _PHASE_COMPOSING,
                      _PHASE_READING, _PHASE_RESEARCHING, _PHASE_ROUTING,
                      _TOOL_PHASES, _phase_for_tool, _summarize_tool_args, app)
from app import database
from fastapi.testclient import TestClient

client = TestClient(app)


# ─── tool arg summarization ────────────────────────────────────────────────

def test_summary_keeps_the_argument_that_says_what_is_happening():
    assert _summarize_tool_args({"query": "nvidia q3 earnings"}) == {
        "query": "nvidia q3 earnings"}
    assert _summarize_tool_args({"url": "https://reuters.com/x"}) == {
        "url": "https://reuters.com/x"}


def test_summary_drops_plumbing():
    """A widget config is the reason this function exists — several KB of HTML
    on a frame that now fires on every single tool call."""
    out = _summarize_tool_args({
        "query": "solar output",
        "widget_id": "data-abc123",
        "config": {"html": "x" * 5000},
        "limit": 6,
    })
    assert out == {"query": "solar output"}


def test_summary_truncates_a_long_value():
    assert len(_summarize_tool_args({"query": "n" * 500})["query"]) == _MAX_DETAIL_CHARS


def test_summary_never_sends_more_than_two_values():
    out = _summarize_tool_args({"query": "a", "url": "b", "topic": "c",
                                "ticker": "d", "location": "e"})
    assert len(out) <= 2


def test_summary_survives_junk():
    """Args come off the wire — whatever prism sent, not a typed model."""
    assert _summarize_tool_args(None) == {}
    assert _summarize_tool_args("not a dict") == {}
    assert _summarize_tool_args([1, 2]) == {}
    assert _summarize_tool_args({"query": None, "url": 42, "topic": "   "}) == {}


# ─── phase derivation ──────────────────────────────────────────────────────

def test_phase_reads_through_the_mcp_prefix():
    assert _phase_for_tool(
        "mcp__lazy-tool-service__html_notes_read_page") == _PHASE_READING
    # The fork serves some tools unprefixed; both forms must key the same.
    assert _phase_for_tool("html_notes_read_page") == _PHASE_READING


def test_canvas_mutators_are_composing():
    for tool in ("canvas_add_widget", "canvas_modify_dom",
                 "create_widget", "update_widget"):
        assert _phase_for_tool(tool) == _PHASE_COMPOSING, tool


def test_unknown_tool_defaults_to_researching():
    """Safe by construction, not merely likely: everything that ISN'T research
    is a mutator, and every mutator is named in the table."""
    assert _phase_for_tool("some_future_tool") == _PHASE_RESEARCHING
    assert _phase_for_tool("") == _PHASE_RESEARCHING
    assert _phase_for_tool(None) == _PHASE_RESEARCHING


def test_search_tools_are_not_miscategorised_as_composing():
    for tool in ("html_notes_web_search", "html_notes_news",
                 "html_notes_stock_history"):
        assert _phase_for_tool(tool) == _PHASE_RESEARCHING, tool


def test_every_mapped_phase_is_in_the_vocabulary():
    """The client renders a label per phase and ignores what it does not know —
    a typo here would silently blank the card's headline."""
    known = {_PHASE_ROUTING, _PHASE_RESEARCHING, _PHASE_READING, _PHASE_COMPOSING}
    assert set(_TOOL_PHASES.values()) <= known


# ─── the emitted frames ────────────────────────────────────────────────────

class _MockAsyncResponse:
    def __init__(self, status_code, chunks):
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def agent_turn(monkeypatch, patch_server):
    """Drive one turn down the PRISM AGENT path with a scripted prism stream.

    The tier-2 classifier is stubbed to `None` (rather than left to time out
    against a vLLM that isn't running) so the turn defers to the agent
    deterministically and in milliseconds."""
    database.init_db()
    session_id = "test-session-progress-channel"
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (session_id, "Progress Channel Test", "2026-08-05T00:00:00Z"))
    conn.commit()
    conn.close()

    async def _no_plan(*a, **kw):
        return None
    patch_server("route_with_llm", _no_plan)

    def run(events):
        chunks = [f'data: {json.dumps(e)}\n' for e in events] + ['data: {"type": "done"}\n']
        with patch("httpx.AsyncClient.stream",
                   return_value=_MockAsyncResponse(200, chunks)):
            res = client.post("/session/message", json={
                "session_id": session_id,
                # Deliberately keyword-free: every fast lane is regex-matched, so
                # a word like "video"/"weather"/"news" would route around the
                # agent entirely and never reach the scripted stream.
                "message": "Add an audio box please",
                "provider": "vllm",
                "model": "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit",
                "current_canvas": '<div id="dashboard-grid" class="dashboard-grid"></div>',
            })
        assert res.status_code == 200
        return res.text

    return run


def _events_of(sse_text, kind):
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


def test_iteration_progress_becomes_a_real_fraction(agent_turn):
    sse = agent_turn([
        {"type": "status", "message": "iteration_progress",
         "iteration": 2, "maxIterations": 9},
    ])
    prog = [p for p in _events_of(sse, "progress") if p.get("source") == "iteration"]
    assert prog, "prism's iteration counter must reach the browser"
    assert prog[0]["step"] == 2 and prog[0]["of"] == 9


def test_malformed_iteration_progress_emits_nothing(agent_turn):
    """A zero denominator would divide the bar by zero; a missing one would
    render a confident-looking fraction out of nothing."""
    sse = agent_turn([
        {"type": "status", "message": "iteration_progress",
         "iteration": 1, "maxIterations": 0},
        {"type": "status", "message": "iteration_progress", "iteration": 1},
        {"type": "status", "message": "generation_progress", "tokPerSec": 40},
    ])
    assert [p for p in _events_of(sse, "progress")
            if p.get("source") == "iteration"] == []


def test_tool_call_carries_args_and_phase(agent_turn):
    sse = agent_turn([
        {"type": "tool_execution", "status": "calling",
         "tool": {"name": "mcp__lazy-tool-service__html_notes_web_search",
                  "args": {"query": "nvidia q3 earnings", "limit": 6}}},
    ])
    calls = _events_of(sse, "tool_call")
    assert calls, "a tool call must reach the browser"
    assert calls[0]["args"] == {"query": "nvidia q3 earnings"}
    assert calls[0]["phase"] == _PHASE_RESEARCHING


def test_research_budget_also_reports_progress(agent_turn):
    """prism does not send iteration_progress on every turn, and a bar with no
    denominator is the fake creep being removed."""
    sse = agent_turn([
        {"type": "tool_execution", "status": "done",
         "tool": {"name": "mcp__lazy-tool-service__html_notes_web_search",
                  "args": {"query": "a"}, "result": "ok"}},
    ])
    prog = [p for p in _events_of(sse, "progress") if p.get("source") == "research"]
    assert prog and prog[0]["of"] == _MAX_RESEARCH_CALLS


def test_thinking_carries_no_phase(agent_turn):
    """Thinking happens WITHIN a phase. Letting it claim one bounced the card
    backwards between 'reading' and 'researching' on every reasoning burst."""
    sse = agent_turn([{"type": "thinking", "content": "hmm"}])
    reasoning = [s for s in _events_of(sse, "status")
                 if s.get("message") == "reasoning..."]
    assert reasoning, "the reasoning status still has to be emitted"
    assert "phase" not in reasoning[0]


def test_agent_path_opens_by_declaring_the_routing_phase(agent_turn):
    sse = agent_turn([])
    assert [s for s in _events_of(sse, "status")
            if s.get("phase") == _PHASE_ROUTING]
