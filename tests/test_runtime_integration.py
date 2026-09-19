import json
import os
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.runtime_chat_adapter import RuntimeChatAdapter
from lazycat.models import RunEvent

client = TestClient(app)


def parse_sse_frames(text: str):
    frames = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                frames.append(json.loads(line[6:]))
            except Exception:
                pass
    return frames


class FakeTestRuntimeClient:
    """Fake runtime client simulating lazy-agent-service SSE event streaming."""

    def __init__(self, events=None, raise_on_stream=None):
        self.events = events or []
        self.raise_on_stream = raise_on_stream
        self.cancelled_runs = []

    async def stream_run(self, request):
        if self.raise_on_stream:
            raise self.raise_on_stream
        for ev in self.events:
            yield ev

    async def cancel_run(self, run_id: str):
        self.cancelled_runs.append(run_id)
        return True


def test_feature_flag_disabled_uses_legacy_route(monkeypatch):
    """When USE_SHARED_RUNTIME is false (default), the request routes to the legacy /agent path."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "false")

    legacy_called = False

    class MockLegacyStream:
        def __init__(self, *args, **kwargs):
            nonlocal legacy_called
            legacy_called = True

        async def __aenter__(self):
            resp = AsyncMock()
            resp.status_code = 200

            async def aiter_text():
                yield 'data: {"type": "chunk", "content": "legacy reply"}\n'
                yield 'data: {"type": "done"}\n'

            resp.aiter_text = aiter_text
            return resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient.stream", side_effect=MockLegacyStream):
        resp = client.post("/session/message", json={
            "session_id": "test_legacy_session",
            "message": "research solar panels"
        })
        assert resp.status_code == 200
        assert legacy_called is True
        frames = parse_sse_frames(resp.text)
        assert any(f.get("content") == "legacy reply" for f in frames)


def test_shared_runtime_search_only_stream(monkeypatch):
    """Search-only request reaches the shared runtime and streams real text without canvas mutations."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_search_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_search_01",
            type="message.delta",
            timestamp="2026-09-19T12:00:01Z",
            data={"delta": "Renewable energy adoption grew 15% year-over-year in 2025."},
        ),
        RunEvent(
            id="evt_2",
            run_id="run_search_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={
                "context_receipt": {"status": "verified"},
                "evidence_records": [{"id": "ev_01", "source": "iea.org"}],
            },
        ),
    ]

    fake_client = FakeTestRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "test_search_session",
            "message": "tell me about renewable energy adoption"
        })

        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)

        # Check for chunk with actual response
        chunks = [f for f in frames if f.get("type") == "chunk"]
        assert len(chunks) >= 1
        assert "Renewable energy adoption grew" in chunks[0]["content"]

        # Check for verified receipt
        receipts = [f for f in frames if f.get("type") == "receipt"]
        assert len(receipts) == 1
        assert receipts[0]["receipt"]["status"] == "verified"

        # HTML-Notes renders the text answer card with the streamed research prose
        components = [f for f in frames if f.get("type") == "component"]
        assert len(components) >= 1
        assert "Renewable energy adoption grew" in components[0]["content"]


def test_shared_runtime_canvas_widget_mutation(monkeypatch):
    """Canvas widget request is authorized by runtime and executed locally via execute_mutation."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_canvas_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_canvas_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "mcp__lazy-tool-service__canvas_add_widget",
                "arguments": {
                    "widget_type": "data_card",
                    "widget_id": "card_solar_99",
                    "config": {"title": "Solar Energy Overview", "text": "Details here"},
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_canvas_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    fake_client = FakeTestRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "test_canvas_session",
            "message": "add a solar overview card to my canvas",
            "current_canvas": "<div id='dashboard-grid'></div>"
        })

        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)

        # Confirm tool call event was surfaced
        tool_calls = [f for f in frames if f.get("type") == "tool_call"]
        assert len(tool_calls) >= 1
        assert "canvas_add_widget" in tool_calls[0]["tool"]

        # Confirm that HTML-Notes local executor ran commit_canvas and emitted component frame
        components = [f for f in frames if f.get("type") == "component"]
        assert len(components) >= 1
        assert "card_solar_99" in components[0]["content"]


def test_shared_runtime_tool_denial_handling(monkeypatch):
    """Tool denial by runtime policy gate emits error and produces NO canvas changes."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_denied_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_denied_01",
            type="tool.failed",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "system_bash",
                "error": {
                    "code": "TOOL_PERMISSION_DENIED",
                    "message": "Tool 'system_bash' is disallowed by profile whitelist",
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_denied_01",
            type="run.failed",
            timestamp="2026-09-19T12:00:02Z",
            data={
                "error": {
                    "code": "TOOL_PERMISSION_DENIED",
                    "message": "Tool 'system_bash' is disallowed by profile whitelist",
                }
            },
        ),
    ]

    fake_client = FakeTestRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "test_denied_session",
            "message": "run bash command to rm -rf"
        })

        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)

        errors = [f for f in frames if f.get("type") == "error"]
        assert len(errors) >= 1
        assert errors[0]["code"] == "TOOL_PERMISSION_DENIED"

        # No widgets committed
        components = [f for f in frames if f.get("type") == "component"]
        assert len(components) == 0


def test_shared_runtime_outage_never_invents_success(monkeypatch):
    """Runtime outage or network failure yields explicit degraded-mode error and NO fake cards/receipts."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    fake_client = FakeTestRuntimeClient(raise_on_stream=ConnectionRefusedError("Shared runtime at port 8080 unreachable"))
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "test_outage_session",
            "message": "research AI chip manufacturers"
        })

        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)

        errors = [f for f in frames if f.get("type") == "error"]
        assert len(errors) == 1
        assert "unavailable" in errors[0]["message"].lower()

        # Critical acceptance requirement: Zero invented receipts or widgets
        receipts = [f for f in frames if f.get("type") == "receipt"]
        components = [f for f in frames if f.get("type") == "component"]
        assert len(receipts) == 0
        assert len(components) == 0
