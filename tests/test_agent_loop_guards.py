"""The runaway and research-budget guards must actually stop the proxy from
consuming the gateway stream.

Both guards `break` — but out of the inner `while "\\n" in buffer` line loop
only. The outer `async for chunk in resp.aiter_text()` loop tested a single
flag, `canvas_settled`, which those guards never set, so a turn that the log
claimed to be "cutting short" ran to completion anyway: every remaining chunk
was pulled, parsed and forwarded.

The probe: script a stream whose guard-tripping events are followed by a long
tail of chunks, and count how many chunks the proxy pulled. A working cut
leaves most of the tail unread; the bug reads all of it.
"""
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

from app.main import _MAX_IDENTICAL_TOOL_CALLS, _MAX_RESEARCH_CALLS, app
from app import database
from fastapi.testclient import TestClient

client = TestClient(app)

TAIL = 25
SEARCH = "mcp__lazy-tool-service__html_notes_web_search"


class _CountingResponse:
    """Like the progress-channel mock, but records how many chunks were pulled."""

    def __init__(self, chunks):
        self.status_code = 200
        self._chunks = chunks
        self.pulled = 0

    async def aiter_text(self):
        for chunk in self._chunks:
            self.pulled += 1
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


def _search_event(query):
    return {"type": "tool_execution", "status": "done",
            "tool": {"name": SEARCH, "args": {"query": query}, "result": "ok"}}


def _tail():
    # Harmless status frames — each one a separate chunk, so a proxy that keeps
    # reading has to pull them one at a time.
    return [{"type": "status", "message": "reasoning..."} for _ in range(TAIL)]


@pytest.fixture
def drive(monkeypatch, patch_server):
    database.init_db()
    session_id = "test-session-loop-guards"
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (session_id, "Loop Guards", "2026-09-07T00:00:00Z"))
    conn.commit()
    conn.close()

    async def _no_plan(*a, **kw):
        return None
    patch_server("route_with_llm", _no_plan)

    def run(events):
        chunks = [f'data: {json.dumps(e)}\n' for e in events] + ['data: {"type": "done"}\n']
        resp = _CountingResponse(chunks)
        with patch("httpx.AsyncClient.stream", return_value=resp):
            res = client.post("/session/message", json={
                "session_id": session_id,
                "message": "Add an audio box please",
                "provider": "vllm",
                "model": "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit",
                "current_canvas": '<div id="dashboard-grid" class="dashboard-grid"></div>',
            })
        assert res.status_code == 200
        return resp, len(chunks)

    return run


def test_runaway_guard_stops_consuming_the_stream(drive):
    events = [_search_event("same") for _ in range(_MAX_IDENTICAL_TOOL_CALLS)] + _tail()
    resp, total = drive(events)
    # The cut lands on the Nth identical call; everything after it must stay
    # unread. Allow one chunk of slack for the buffer split.
    assert resp.pulled <= _MAX_IDENTICAL_TOOL_CALLS + 1, (
        f"proxy pulled {resp.pulled} of {total} chunks after the runaway cut")


def test_research_budget_stops_consuming_the_stream(drive):
    events = [_search_event(f"q{i}") for i in range(_MAX_RESEARCH_CALLS)] + _tail()
    resp, total = drive(events)
    assert resp.pulled <= _MAX_RESEARCH_CALLS + 1, (
        f"proxy pulled {resp.pulled} of {total} chunks after the budget cut")


def test_a_clean_turn_reads_to_the_end(drive):
    """Negative control: with no guard tripped the proxy must still drain the
    stream, or the test above would pass for a proxy that reads nothing."""
    events = [_search_event("a")] + _tail()
    resp, total = drive(events)
    assert resp.pulled == total
