import asyncio
import pytest
from app.services.runtime_chat_adapter import RuntimeChatAdapter
from lazycat.models import RunEvent


class FakeRuntimeClient:
    """Explicit unit-test fake client conforming to canonical v1 event contract."""

    def __init__(self, events=None, raise_on_stream=None):
        self.events = events or []
        self.raise_on_stream = raise_on_stream
        self.cancelled_runs = []

    async def stream_run(self, request):
        if self.raise_on_stream:
            raise self.raise_on_stream
        for ev in self.events:
            yield ev
            await asyncio.sleep(0.001)

    async def cancel_run(self, run_id: str):
        self.cancelled_runs.append(run_id)
        return True


@pytest.mark.asyncio
async def test_runtime_chat_adapter_event_translation():
    events = [
        RunEvent(
            id="evt_0",
            run_id="run_test_101",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_test_101",
            type="message.delta",
            timestamp="2026-09-19T12:00:01Z",
            data={"delta": "Synthesizing research notes for the query..."},
        ),
        RunEvent(
            id="evt_2",
            run_id="run_test_101",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:02Z",
            data={
                "tool_name": "mcp__lazy-tool-service__news_search",
                "arguments": {"query": "clean energy"},
            },
        ),
        RunEvent(
            id="evt_3",
            run_id="run_test_101",
            type="run.completed",
            timestamp="2026-09-19T12:00:03Z",
            data={
                "context_receipt": {"status": "verified"},
                "evidence_records": [{"id": "ev_01", "source": "news_api"}],
                "usage": {"total_tokens": 120},
            },
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)

    received_types = []
    chunks = []
    tool_calls = []
    receipt = None

    async for frame in adapter.stream_chat_turn(
        query="Research renewable energy stocks",
        session_id="session_test_99",
        canvas_html="<div id='canvas'></div>",
    ):
        t = frame.get("type")
        received_types.append(t)
        if t == "chunk":
            chunks.append(frame.get("content"))
        elif t == "tool_call":
            tool_calls.append(frame.get("tool"))
        elif t == "receipt":
            receipt = frame.get("receipt")

    assert "status" in received_types
    assert "chunk" in received_types
    assert "tool_call" in received_types
    assert "receipt" in received_types
    assert "done" in received_types

    assert any("Synthesizing research notes" in c for c in chunks)
    assert "mcp__lazy-tool-service__news_search" in tool_calls
    assert receipt == {"status": "verified"}


@pytest.mark.asyncio
async def test_runtime_chat_adapter_mutation_callback():
    events = [
        RunEvent(
            id="evt_0",
            run_id="run_test_102",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_test_102",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "canvas_add_widget",
                "arguments": {
                    "widget_type": "data_card",
                    "widget_id": "card_res_01",
                    "config": {"title": "Research Result"},
                },
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_test_102",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)
    mutation_invoked = []

    async def mock_execute_mutation(tool_name, args):
        mutation_invoked.append((tool_name, args))
        yield f"data: {{\"type\": \"component\", \"widget_id\": \"{args.get('widget_id')}\"}}\n\n"

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="Add a notes widget",
        session_id="session_test_99",
        canvas_html="<div id='canvas'></div>",
        execute_mutation_cb=mock_execute_mutation,
    ):
        frames.append(frame)

    assert len(mutation_invoked) == 1
    assert mutation_invoked[0][0] == "canvas_add_widget"
    assert mutation_invoked[0][1]["widget_id"] == "card_res_01"

    raw_sse_frames = [f for f in frames if f.get("type") == "raw_sse"]
    assert len(raw_sse_frames) == 1
    assert "card_res_01" in raw_sse_frames[0]["frame"]


@pytest.mark.asyncio
async def test_runtime_chat_adapter_cancellation():
    events = [
        RunEvent(
            id="evt_0",
            run_id="run_test_103",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_test_103",
            type="message.delta",
            timestamp="2026-09-19T12:00:01Z",
            data={"delta": "Working..."},
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)
    cancel_event = asyncio.Event()

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="Long query",
        session_id="session_test_99",
        canvas_html="<div id='canvas'></div>",
        cancel_event=cancel_event,
    ):
        frames.append(frame)
        if frame.get("phase") == "running":
            cancel_event.set()

    assert "run_test_103" in client.cancelled_runs
    statuses = [f for f in frames if f.get("type") == "status" and f.get("phase") == "cancelled"]
    assert len(statuses) == 1
    assert "cancelled" in statuses[0]["message"]


@pytest.mark.asyncio
async def test_runtime_chat_adapter_tool_denial():
    events = [
        RunEvent(
            id="evt_0",
            run_id="run_test_104",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_test_104",
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
            run_id="run_test_104",
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

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="Run shell command",
        session_id="session_test_99",
    ):
        frames.append(frame)

    errors = [f for f in frames if f.get("type") == "error"]
    assert len(errors) >= 1
    assert errors[0]["code"] == "TOOL_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_runtime_chat_adapter_runtime_outage_degraded_mode():
    """Verifies that runtime connection failures emit structured errors and NEVER invent fake receipts."""
    client = FakeRuntimeClient(raise_on_stream=ConnectionRefusedError("Connection to port 8080 refused"))
    adapter = RuntimeChatAdapter(runtime_client=client)

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="Research query during outage",
        session_id="session_test_99",
    ):
        frames.append(frame)

    # Must contain error frame
    errors = [f for f in frames if f.get("type") == "error"]
    assert len(errors) == 1
    assert "unavailable" in errors[0]["message"].lower()

    # Must NOT emit fake receipt or chunk or component
    receipts = [f for f in frames if f.get("type") == "receipt"]
    chunks = [f for f in frames if f.get("type") == "chunk"]
    raw_sse = [f for f in frames if f.get("type") == "raw_sse"]

    assert len(receipts) == 0
    assert len(chunks) == 0
    assert len(raw_sse) == 0
