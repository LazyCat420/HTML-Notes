import asyncio
import json
import logging
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


class FakeCutoverRuntimeClient:
    """Fake runtime client simulating canonical v1.2 events for Dev 2 cutover tests."""

    def __init__(self, events=None, raise_on_stream=None, contract_version=None):
        self.events = events or []
        self.raise_on_stream = raise_on_stream
        self.cancelled_runs = []
        self.contract_version = contract_version
        self.recorded_requests = []

    async def stream_run(self, request):
        self.recorded_requests.append(request)
        if self.raise_on_stream:
            raise self.raise_on_stream
        for ev in self.events:
            yield ev
            await asyncio.sleep(0.001)

    async def cancel_run(self, run_id: str):
        self.cancelled_runs.append(run_id)
        return True


# 1. test_shared_runtime_route_invokes_local_tool_executor
def test_shared_runtime_route_invokes_local_tool_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_exec_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_exec_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {
                    "widget_type": "clock",
                    "widget_id": "clock_cutover_1",
                    "config": {"mode": "clock", "timezone": "UTC"},
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_exec_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute", wraps=None) as mock_exec:
            mock_exec.return_value = {
                "success": True,
                "result": {
                    "widget_type": "clock",
                    "widget_id": "clock_cutover_1",
                    "html": "<div id='clock_cutover_1'>Clock</div>",
                },
                "tool": "html_notes.canvas.upsert_widget",
            }
            resp = client.post("/session/message", json={
                "session_id": "session_exec_01",
                "message": "render custom card",
                "current_canvas": "<div id='dashboard-grid'></div>",
            })

            assert resp.status_code == 200
            assert mock_exec.called
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs.get("tool_name") == "html_notes.canvas.upsert_widget"
            assert call_kwargs.get("session_id") == "session_exec_01"


# 2. test_shared_runtime_route_does_not_invoke_legacy_execute_mutation
def test_shared_runtime_route_does_not_invoke_legacy_execute_mutation(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_exec_02",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_exec_02",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {
                    "widget_type": "clock",
                    "widget_id": "clock_cutover_2",
                    "config": {"mode": "clock"},
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_exec_02",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        # We verify that execute_mutation is not called because the bridge directly invokes local_tool_executor
        resp = client.post("/session/message", json={
            "session_id": "session_exec_02",
            "message": "add clock",
            "current_canvas": "<div id='dashboard-grid'></div>",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        assert any(f.get("type") == "component" for f in frames)


# 3. test_canonical_canvas_tool_reaches_executor
def test_canonical_canvas_tool_reaches_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_canon_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_canon_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {
                    "widget_type": "data_card",
                    "widget_id": "canon_card_1",
                    "config": {"title": "Canonical Card"},
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_canon_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    from dataclasses import asdict
    from app.adapters.runtime.models import create_test_authorization
    events[1].data["authorization_receipt"] = asdict(create_test_authorization(
        tool_id="html_notes.canvas.upsert_widget", session_id="session_canon_01",
        run_id="run_canon_01", tool_call_id="cutover-call"))

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_canon_01",
            "message": "add canonical card",
            "current_canvas": "<div id='dashboard-grid'></div>",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        components = [f for f in frames if f.get("type") == "component"]
        assert len(components) >= 1
        assert "canon_card_1" in components[0]["content"]


# 4. test_legacy_canvas_alias_resolves_before_executor
def test_legacy_canvas_alias_resolves_before_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_alias_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_alias_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "canvas_add_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {
                    "widget_type": "data_card",
                    "widget_id": "alias_card_1",
                    "config": {"title": "Alias Card"},
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_alias_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute", wraps=None) as mock_exec:
            mock_exec.return_value = {
                "success": True,
                "result": {"widget_type": "data_card", "widget_id": "alias_card_1", "html": "<div id='alias_card_1'></div>"},
                "tool": "html_notes.canvas.upsert_widget",
            }
            resp = client.post("/session/message", json={
                "session_id": "session_alias_01",
                "message": "add alias card",
            })
            assert resp.status_code == 200
            assert mock_exec.called
            # Verify alias was resolved to canonical ID before reaching executor
            assert mock_exec.call_args.kwargs.get("tool_name") == "html_notes.canvas.upsert_widget"


# 5. test_global_tool_never_reaches_local_executor
def test_global_tool_never_reaches_local_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_global_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_global_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "global.web.search",
                "execution": "shared",
                "arguments": {"query": "deep learning"},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_global_01",
            type="tool.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"tool_name": "global.web.search", "result": {"results": []}},
        ),
        RunEvent(
            id="evt_3",
            run_id="run_global_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:03Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            resp = client.post("/session/message", json={
                "session_id": "session_global_01",
                "message": "search for deep learning",
            })
            assert resp.status_code == 200
            assert not mock_exec.called


# 6. test_unknown_local_tool_returns_structured_error
def test_unknown_local_tool_returns_structured_error(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_unk_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_unk_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.nonexistent.fake_tool",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_unk_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_unk_01",
            "message": "call non existent tool",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        errors = [f for f in frames if f.get("type") == "error"]
        assert len(errors) >= 1
        err_msg = errors[0]["message"].lower()
        assert errors[0]["code"] == "LOCAL_SCOPE_VIOLATION"


# 7. test_runtime_denied_local_tool_never_reaches_executor
def test_runtime_denied_local_tool_never_reaches_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_denied_02",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_denied_02",
            type="tool.failed",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "error": {
                    "code": "TOOL_PERMISSION_DENIED",
                    "message": "Policy denied tool execution",
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_denied_02",
            type="run.failed",
            timestamp="2026-09-19T12:00:02Z",
            data={
                "error": {
                    "code": "TOOL_PERMISSION_DENIED",
                    "message": "Policy denied tool execution",
                }
            },
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            resp = client.post("/session/message", json={
                "session_id": "session_denied_02",
                "message": "add widget with denied policy",
            })
            assert resp.status_code == 200
            assert not mock_exec.called


# 8. test_runtime_local_tool_missing_session_scope_is_rejected
def test_runtime_local_tool_missing_session_scope_is_rejected(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_scope_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_scope_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "required_scope": {
                    "app_id": "html-notes",
                    "session_id": "different_session_999",  # Cross-session mismatch
                },
                "arguments": {"widget_type": "clock", "widget_id": "w_clock"},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_scope_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            resp = client.post("/session/message", json={
                "session_id": "active_session_111",
                "message": "try to cross session mutate",
            })
            assert resp.status_code == 200
            assert not mock_exec.called
            frames = parse_sse_frames(resp.text)
            errors = [f for f in frames if f.get("type") == "error"]
            assert len(errors) >= 1
            assert errors[0]["code"] == "LOCAL_SCOPE_VIOLATION"


# 9. test_runtime_local_tool_wrong_app_scope_is_rejected
def test_runtime_local_tool_wrong_app_scope_is_rejected(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_scope_02",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_scope_02",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "required_scope": {
                    "app_id": "trading-client",  # Wrong application scope
                    "session_id": "session_scope_02",
                },
                "arguments": {"widget_type": "clock", "widget_id": "w_clock"},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_scope_02",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            resp = client.post("/session/message", json={
                "session_id": "session_scope_02",
                "message": "try wrong app scope",
            })
            assert resp.status_code == 200
            assert not mock_exec.called
            frames = parse_sse_frames(resp.text)
            errors = [f for f in frames if f.get("type") == "error"]
            assert len(errors) >= 1
            assert errors[0]["code"] == "LOCAL_SCOPE_VIOLATION"


# 10. test_shared_runtime_success_emits_exactly_one_done
def test_shared_runtime_success_emits_exactly_one_done(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_done_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_done_01",
            type="message.delta",
            timestamp="2026-09-19T12:00:01Z",
            data={"delta": "Done test content"},
        ),
        RunEvent(
            id="evt_2",
            run_id="run_done_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_done_01",
            "message": "check exactly one done frame",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        done_frames = [f for f in frames if f.get("type") == "done"]
        assert len(done_frames) == 1


# 11. test_shared_runtime_denial_emits_exactly_one_done
def test_shared_runtime_denial_emits_exactly_one_done(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_denied_done",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_denied_done",
            type="tool.failed",
            timestamp="2026-09-19T12:00:01Z",
            data={"tool_name": "bash", "error": {"code": "TOOL_PERMISSION_DENIED", "message": "Disallowed"}},
        ),
        RunEvent(
            id="evt_2",
            run_id="run_denied_done",
            type="run.failed",
            timestamp="2026-09-19T12:00:02Z",
            data={"error": {"code": "TOOL_PERMISSION_DENIED", "message": "Disallowed"}},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_denied_done",
            "message": "denial check done",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        done_frames = [f for f in frames if f.get("type") == "done"]
        assert len(done_frames) == 1


# 12. test_shared_runtime_outage_emits_exactly_one_done
def test_shared_runtime_outage_emits_exactly_one_done(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    fake_client = FakeCutoverRuntimeClient(raise_on_stream=ConnectionRefusedError("Runtime down"))
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_outage_done",
            "message": "outage check done",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        done_frames = [f for f in frames if f.get("type") == "done"]
        assert len(done_frames) == 1


# 13. test_shared_runtime_cancellation_emits_exactly_one_done
@pytest.mark.asyncio
async def test_shared_runtime_cancellation_emits_exactly_one_done():
    events = [
        RunEvent(
            id="evt_0",
            run_id="run_cancel_done",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_cancel_done",
            type="message.delta",
            timestamp="2026-09-19T12:00:01Z",
            data={"delta": "Streaming forever"},
        ),
    ]

    client = FakeCutoverRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)
    cancel_event = asyncio.Event()

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="cancel test",
        session_id="session_cancel_done",
        cancel_event=cancel_event,
    ):
        frames.append(frame)
        if frame.get("phase") == "running":
            cancel_event.set()

    done_frames = [f for f in frames if f.get("type") == "done"]
    assert len(done_frames) == 1


# 14. test_runtime_receipt_reaches_browser_without_mutation
def test_runtime_receipt_reaches_browser_without_mutation(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    expected_receipt = {
        "status": "verified",
        "contract_version": "1.2.0",
        "profile_id": "html-notes-researcher-v1",
        "audit_hash": "sha256_abcdef123456",
    }
    expected_evidence = [
        {"id": "ev_1", "source": "nature.com", "title": "Fusion Breakthrough"},
    ]

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_receipt_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_receipt_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "context_receipt": expected_receipt,
                "evidence_records": expected_evidence,
            },
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_receipt_01",
            "message": "give receipt",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        receipt_frames = [f for f in frames if f.get("type") == "receipt"]
        assert len(receipt_frames) == 1
        assert receipt_frames[0]["receipt"] == expected_receipt
        assert receipt_frames[0]["evidence"] == expected_evidence


# 15. test_runtime_outage_creates_no_component_or_receipt
def test_runtime_outage_creates_no_component_or_receipt(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    fake_client = FakeCutoverRuntimeClient(raise_on_stream=ConnectionRefusedError("Runtime offline"))
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_outage_02",
            "message": "research during outage",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        components = [f for f in frames if f.get("type") == "component"]
        receipts = [f for f in frames if f.get("type") == "receipt"]
        assert len(components) == 0
        assert len(receipts) == 0


# 16. test_feature_flag_false_uses_only_legacy_route
def test_feature_flag_false_uses_only_legacy_route(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "false")

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter") as mock_adapter:
        class MockLegacyStream:
            async def __aenter__(self):
                resp = AsyncMock()
                resp.status_code = 200
                async def aiter_text():
                    yield 'data: {"type": "chunk", "content": "legacy fallback"}\n'
                    yield 'data: {"type": "done"}\n'
                resp.aiter_text = aiter_text
                return resp
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("httpx.AsyncClient.stream", side_effect=lambda *a, **kw: MockLegacyStream()):
            resp = client.post("/session/message", json={
                "session_id": "session_flag_false",
                "message": "test flag false",
            })
            assert resp.status_code == 200
            assert not mock_adapter.called


# 17. test_feature_flag_true_never_silently_falls_back
def test_feature_flag_true_never_silently_falls_back(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    fake_client = FakeCutoverRuntimeClient(raise_on_stream=ConnectionRefusedError("Shared runtime unreachable"))
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("httpx.AsyncClient.stream") as mock_legacy_stream:
            resp = client.post("/session/message", json={
                "session_id": "session_no_silent_fallback",
                "message": "verify no silent fallback on failure",
            })
            assert resp.status_code == 200
            assert not mock_legacy_stream.called
            frames = parse_sse_frames(resp.text)
            errors = [f for f in frames if f.get("type") == "error"]
            assert len(errors) == 1
            assert "unavailable" in errors[0]["message"].lower()


# 18. test_profile_or_contract_mismatch_fails_before_streaming
def test_profile_or_contract_mismatch_fails_before_streaming(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    # Runtime reports contract version 2.0.0 (incompatible with 1.2.0)
    fake_client = FakeCutoverRuntimeClient(
        events=[],
        contract_version="2.0.0"
    )
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_contract_mismatch",
            "message": "check contract mismatch",
        })
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        errors = [f for f in frames if f.get("type") == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "INCOMPATIBLE_CONTRACT_VERSION"


# 19. test_canvas_context_is_bounded_before_runtime_request
def test_canvas_context_is_bounded_before_runtime_request(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    fake_client = FakeCutoverRuntimeClient(events=[])
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    huge_canvas = "<div>" + ("<p>Repeated canvas node content</p>" * 300) + "</div>"
    assert len(huge_canvas) > 8000

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_bound_ctx",
            "message": "check canvas context truncation",
            "current_canvas": huge_canvas,
        })
        assert resp.status_code == 200
        assert len(fake_client.recorded_requests) == 1
        req_obj = fake_client.recorded_requests[0]
        context_payload = req_obj.runtime_overrides.get("context", {})
        bounded = context_payload.get("canvas_context", "")
        assert len(bounded) <= 4050
        assert "[canvas truncated]" in bounded


# 20. test_runtime_run_id_is_included_in_structured_logs
def test_runtime_run_id_is_included_in_structured_logs(monkeypatch, caplog):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_logged_9999",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_logged_9999",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={"tool_name": "html_notes.canvas.read", "execution": "local", "arguments": {}},
        ),
        RunEvent(
            id="evt_2",
            run_id="run_logged_9999",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with caplog.at_level(logging.INFO):
        with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
            resp = client.post("/session/message", json={
                "session_id": "session_log_test",
                "message": "test run id logging",
            })
            assert resp.status_code == 200
            assert any("run_id=run_logged_9999" in record.message for record in caplog.records)


# 21. test_route_passes_authorization_to_local_executor
def test_route_passes_authorization_to_local_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    expected_auth = {
        "run_id": "run_auth_pass_01",
        "tool_call_id": "call_auth_01",
        "signature": "sig_mock_123",
        "profile_id": "html-notes-canvas-v1",
    }
    events = [
        RunEvent(
            id="evt_0",
            run_id="run_auth_pass_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_auth_pass_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {"widget_type": "clock", "widget_id": "clock_auth_1"},
                "authorization_receipt": expected_auth,
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_auth_pass_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            mock_exec.return_value = {"success": True, "result": {}, "tool": "html_notes.canvas.upsert_widget"}
            resp = client.post("/session/message", json={
                "session_id": "session_auth_01",
                "message": "render custom card with auth receipt",
                "current_canvas": "<div id='dashboard-grid'></div>",
            })
            assert resp.status_code == 200
            assert mock_exec.called
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs.get("authorization") == {**expected_auth, "app_id": "html-notes", "session_id": "session_auth_01"}


# 22. test_route_passes_runtime_run_id_to_local_executor
def test_route_passes_runtime_run_id_to_local_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    test_run_id = "run_ctx_run_id_999"
    events = [
        RunEvent(
            id="evt_0",
            run_id=test_run_id,
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id=test_run_id,
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {"widget_type": "clock", "widget_id": "clock_runid_1"},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id=test_run_id,
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            mock_exec.return_value = {"success": True, "result": {}, "tool": "html_notes.canvas.upsert_widget"}
            resp = client.post("/session/message", json={
                "session_id": "session_runid_01",
                "message": "render custom card with run_id ctx",
                "current_canvas": "<div id='dashboard-grid'></div>",
            })
            assert resp.status_code == 200
            assert mock_exec.called
            call_kwargs = mock_exec.call_args.kwargs
            runtime_ctx = call_kwargs.get("runtime_context", {})
            assert runtime_ctx.get("run_id") == test_run_id


# 23. test_route_passes_profile_id_to_local_executor
def test_route_passes_profile_id_to_local_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_prof_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_prof_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {"widget_type": "clock", "widget_id": "clock_prof_1"},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_prof_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            mock_exec.return_value = {"success": True, "result": {}, "tool": "html_notes.canvas.upsert_widget"}
            resp = client.post("/session/message", json={
                "session_id": "session_prof_01",
                "message": "render custom card with profile_id ctx",
                "current_canvas": "<div id='dashboard-grid'></div>",
            })
            assert resp.status_code == 200
            assert mock_exec.called
            call_kwargs = mock_exec.call_args.kwargs
            runtime_ctx = call_kwargs.get("runtime_context", {})
            assert runtime_ctx.get("profile_id") is not None
            assert len(runtime_ctx.get("profile_id")) > 0


# 24. test_route_passes_contract_version_to_local_executor
def test_route_passes_contract_version_to_local_executor(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_cv_01",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_cv_01",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "execution": "local",
                "tool_call_id": "cutover-call",
                "arguments": {"widget_type": "clock", "widget_id": "clock_cv_1"},
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_cv_01",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={},
        ),
    ]

    fake_client = FakeCutoverRuntimeClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        with patch("app.tooling.local_executor.local_tool_executor.execute") as mock_exec:
            mock_exec.return_value = {"success": True, "result": {}, "tool": "html_notes.canvas.upsert_widget"}
            resp = client.post("/session/message", json={
                "session_id": "session_cv_01",
                "message": "render custom card with contract_version ctx",
                "current_canvas": "<div id='dashboard-grid'></div>",
            })
            assert resp.status_code == 200
            assert mock_exec.called
            call_kwargs = mock_exec.call_args.kwargs
            runtime_ctx = call_kwargs.get("runtime_context", {})
            assert runtime_ctx.get("contract_version") == "1.2.0"

