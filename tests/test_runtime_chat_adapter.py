import asyncio
import pytest
from app.services.runtime_chat_adapter import RuntimeChatAdapter

@pytest.mark.asyncio
async def test_runtime_chat_adapter_event_translation():
    adapter = RuntimeChatAdapter()
    
    received_types = []
    chunks = []
    tool_calls = []
    receipt = None
    
    async for frame in adapter.stream_chat_turn(
        query="Research renewable energy stocks",
        session_id="session_test_99",
        canvas_html="<div id='canvas'></div>"
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
    
    assert any("Synthesizing notes" in c for c in chunks)
    assert "canvas_add_widget" in tool_calls
    assert receipt == {"status": "verified"}

@pytest.mark.asyncio
async def test_runtime_chat_adapter_mutation_callback():
    adapter = RuntimeChatAdapter()
    mutation_invoked = []

    async def mock_execute_mutation(tool_name, args):
        mutation_invoked.append((tool_name, args))
        yield f"data: {{\"type\": \"component\", \"widget_id\": \"{args.get('widget_id')}\"}}\n\n"

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="Add a notes widget",
        session_id="session_test_99",
        canvas_html="<div id='canvas'></div>",
        execute_mutation_cb=mock_execute_mutation
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
    adapter = RuntimeChatAdapter()
    cancel_event = asyncio.Event()

    frames = []
    async for frame in adapter.stream_chat_turn(
        query="Long query",
        session_id="session_test_99",
        canvas_html="<div id='canvas'></div>",
        cancel_event=cancel_event
    ):
        frames.append(frame)
        # Cancel right after first frame
        cancel_event.set()

    statuses = [f for f in frames if f.get("type") == "status" and f.get("phase") == "cancelled"]
    assert len(statuses) == 1
    assert "cancelled" in statuses[0]["message"]
