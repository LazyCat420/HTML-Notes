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
async def test_runtime_chat_adapter_rejects_unacknowledged_legacy_mutation():
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

    assert mutation_invoked == []
    assert any(f.get("type") == "error" for f in frames)
    assert client.cancelled_runs == ["run_test_102"]


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


@pytest.mark.asyncio
async def test_route_passes_authorization_to_local_executor():
    captured_args = {}

    async def mock_execute_cb(tool_name, tool_args, auth_receipt, ctx, rt_ctx=None):
        captured_args["auth_receipt"] = auth_receipt
        captured_args["rt_ctx"] = rt_ctx
        yield "data: {\"type\": \"status\"}\n\n"

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_bridge_test",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_bridge_test",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "arguments": {"widget_type": "clock", "widget_id": "clk_1"},
                "authorization_receipt": {"signature": "sig_valid_123", "nonce": "non_456"},
                "required_scope": ["app_id", "session_id"],
                "execution": "local",
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_bridge_test",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)

    frames = []
    async for f in adapter.stream_chat_turn(
        query="Add clock widget",
        session_id="session_bridge_1",
        execute_local_tool_cb=mock_execute_cb
    ):
        frames.append(f)

    auth = captured_args.get("auth_receipt") or {}
    assert auth.get("signature") == "sig_valid_123"
    assert auth.get("nonce") == "non_456"
    assert auth.get("run_id") == "run_bridge_test"
    assert auth.get("session_id") == "session_bridge_1"


@pytest.mark.asyncio
async def test_route_passes_runtime_run_id_to_local_executor():
    captured_rt_ctx = {}

    async def mock_execute_cb(tool_name, tool_args, auth_receipt, ctx, rt_ctx=None):
        captured_rt_ctx.update(rt_ctx or {})
        yield "data: {\"type\": \"status\"}\n\n"

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_specific_id_777",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_specific_id_777",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "arguments": {"widget_type": "clock", "widget_id": "clk_1"},
                "required_scope": ["app_id", "session_id"],
                "execution": "local",
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_specific_id_777",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)

    async for _ in adapter.stream_chat_turn(
        query="Add clock widget",
        session_id="session_bridge_2",
        execute_local_tool_cb=mock_execute_cb
    ):
        pass

    assert captured_rt_ctx.get("run_id") == "run_specific_id_777"


@pytest.mark.asyncio
async def test_route_passes_profile_id_to_local_executor():
    captured_rt_ctx = {}

    async def mock_execute_cb(tool_name, tool_args, auth_receipt, ctx, rt_ctx=None):
        captured_rt_ctx.update(rt_ctx or {})
        yield "data: {\"type\": \"status\"}\n\n"

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_profile_test",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_profile_test",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "arguments": {"widget_type": "clock", "widget_id": "clk_1"},
                "required_scope": ["app_id", "session_id"],
                "execution": "local",
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_profile_test",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client, default_profile_id="custom-notes-researcher-v2")

    async for _ in adapter.stream_chat_turn(
        query="Add clock widget",
        session_id="session_bridge_3",
        profile_id="custom-notes-researcher-v2",
        execute_local_tool_cb=mock_execute_cb
    ):
        pass

    assert captured_rt_ctx.get("profile_id") == "custom-notes-researcher-v2"


@pytest.mark.asyncio
async def test_route_passes_contract_version_to_local_executor():
    captured_rt_ctx = {}

    async def mock_execute_cb(tool_name, tool_args, auth_receipt, ctx, rt_ctx=None):
        captured_rt_ctx.update(rt_ctx or {})
        yield "data: {\"type\": \"status\"}\n\n"

    events = [
        RunEvent(
            id="evt_0",
            run_id="run_contract_test",
            type="run.started",
            timestamp="2026-09-19T12:00:00Z",
            data={"status": "running"},
        ),
        RunEvent(
            id="evt_1",
            run_id="run_contract_test",
            type="tool.invoked",
            timestamp="2026-09-19T12:00:01Z",
            data={
                "tool_name": "html_notes.canvas.upsert_widget",
                "arguments": {"widget_type": "clock", "widget_id": "clk_1"},
                "required_scope": ["app_id", "session_id"],
                "execution": "local",
            },
        ),
        RunEvent(
            id="evt_2",
            run_id="run_contract_test",
            type="run.completed",
            timestamp="2026-09-19T12:00:02Z",
            data={"context_receipt": {"status": "verified"}},
        ),
    ]

    client = FakeRuntimeClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)

    async for _ in adapter.stream_chat_turn(
        query="Add clock widget",
        session_id="session_bridge_4",
        execute_local_tool_cb=mock_execute_cb
    ):
        pass

    assert captured_rt_ctx.get("contract_version") == "1.2.0"


@pytest.mark.asyncio
async def test_close_cancels_nonterminal_run_and_preserves_context():
    client = FakeRuntimeClient(events=[dict(type='run.started', run_id='run-close', data={})])
    captured = []
    original = client.stream_run
    def capture(request):
        captured.append(request)
        return original(request)
    client.stream_run = capture
    adapter = RuntimeChatAdapter(runtime_client=client)
    history = [{'role': 'system', 'content': 'Application rules'}, {'role': 'user', 'content': 'Follow up'}]
    stream = adapter.stream_chat_turn(query='Follow up', session_id='session-close', canvas_html='<p>Context</p>', messages=history)
    async for frame in stream:
        if frame.get('run_id'):
            break
    await stream.aclose()
    assert client.cancelled_runs == ['run-close']
    assert captured[0].input[0].content == 'Application rules'
    assert captured[0].runtime_overrides['context']['canvas_context']


@pytest.mark.asyncio
async def test_real_local_observation_is_returned_to_runtime():
    from unittest.mock import AsyncMock
    from secrets import token_hex
    receipt = {'signature': token_hex(32)}
    client = FakeRuntimeClient(events=[
        dict(type='tool.invoked', run_id='run-observation', data={
            'tool_name': 'html_notes.notes.get', 'tool_call_id': 'call-1', 'execution': 'local',
            'arguments': {}, 'authorization_receipt': receipt,
            'required_scope': {'app_id': 'html-notes', 'session_id': 'session-observation'},
        }), dict(type='run.completed', run_id='run-observation', data={})])
    client.submit_tool_result = AsyncMock(return_value={'ok': True})
    observation = {'success': True, 'result': {'title': 'Actual persisted note'}}
    async def executor(*args):
        yield {'type': 'runtime_tool_result', 'result': observation}
    frames = [f async for f in RuntimeChatAdapter(runtime_client=client).stream_chat_turn(
        query='Read note', session_id='session-observation', execute_local_tool_cb=executor)]
    client.submit_tool_result.assert_awaited_once()
    assert client.submit_tool_result.call_args.kwargs['result'] == observation
    assert not any(f.get('type') == 'error' for f in frames)
    assert client.cancelled_runs == []
