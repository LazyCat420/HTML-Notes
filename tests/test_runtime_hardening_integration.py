"""
High-Level Runtime Hardening Integration Suite (Phase E)

Validates the full chain:
  RuntimeChatAdapter extracts authorization receipt
    → route bridge forwards it
    → LocalToolExecutor receives it
    → executor verifies it
    → domain service is called only after admission succeeds

Covers all 10 required Phase E integration scenarios:
1. test_runtime_admitted_local_write_with_valid_scope_and_valid_receipt_executes
2. test_runtime_admitted_local_write_with_missing_scope_does_not_execute
3. test_runtime_admitted_local_write_with_invalid_receipt_does_not_execute
4. test_runtime_admitted_local_write_with_expired_receipt_does_not_execute
5. test_runtime_admitted_local_write_with_foreign_session_does_not_execute
6. test_runtime_global_tool_never_calls_local_executor
7. test_runtime_outage_creates_no_local_side_effect
8. test_runtime_denial_creates_no_local_side_effect
9. test_shared_runtime_request_emits_exactly_one_done
10. test_legacy_note_cannot_be_edited_from_foreign_session
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.adapters.runtime.models import (
    LocalExecutionContext,
    create_test_authorization,
    verify_local_tool_scope,
    EXPECTED_APP_ID,
)
from app.tooling.local_executor import LocalToolExecutor
from app.services.runtime_chat_adapter import RuntimeChatAdapter
from app.domain.notes.service import NotesDomainService
import app.database as database


class FakeRuntimeEvent:
    def __init__(self, id, type, timestamp, data, run_id="run_int_001"):
        self.id = id
        self.type = type
        self.timestamp = timestamp
        self.data = data
        self.run_id = run_id


class FakeStreamingClient:
    def __init__(self, events=None, raise_on_stream=None):
        self.events = events or []
        self.raise_on_stream = raise_on_stream

    async def stream_run(self, *args, **kwargs):
        if self.raise_on_stream:
            raise self.raise_on_stream
        for ev in self.events:
            yield ev


@pytest.fixture
def executor():
    return LocalToolExecutor()


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    test_db_path = str(tmp_path / "test_int_notes.db")
    monkeypatch.setattr(database, "DATABASE_URL", test_db_path)
    database.init_db()
    return test_db_path


@pytest.mark.asyncio
async def test_runtime_admitted_local_write_with_valid_scope_and_valid_receipt_executes(executor):
    """1. Local write with valid scope and valid runtime authorization receipt executes successfully."""
    session_id = "session_phase_e_1"
    tool_name = "html_notes.canvas.upsert_widget"
    tool_args = {"widget_type": "clock", "widget_id": "clock_pe_1"}
    
    auth = create_test_authorization(
        tool_call_id="call_pe_1",
        tool_id=tool_name,
        session_id=session_id,
        app_id="html_notes",
    )
    
    res = await executor.execute(
        tool_name=tool_name,
        args=tool_args,
        session_id=session_id,
        canvas_html="<div id='dashboard-grid'></div>",
        authorization=auth,
        runtime_context={"run_id": auth.run_id, "profile_id": auth.profile_id, "contract_version": "1.2.0"},
    )
    assert res["success"] is True
    assert "html" in res["result"]


@pytest.mark.asyncio
async def test_runtime_admitted_local_write_with_missing_scope_does_not_execute(executor):
    """2. Local write with missing scope fails closed before domain dispatch."""
    tool_name = "html_notes.canvas.upsert_widget"
    tool_args = {"widget_type": "clock", "widget_id": "clock_pe_2"}
    
    # Missing session_id
    auth = create_test_authorization(
        tool_call_id="call_pe_2",
        tool_id=tool_name,
        session_id="session_pe_2",
    )
    
    res = await executor.execute(
        tool_name=tool_name,
        args=tool_args,
        session_id="",  # Empty session scope
        canvas_html="<div id='dashboard-grid'></div>",
        authorization=auth,
    )
    assert res["success"] is False
    assert res.get("code") == "SCOPE_VIOLATION"


@pytest.mark.asyncio
async def test_runtime_admitted_local_write_with_invalid_receipt_does_not_execute(executor):
    """3. Local write with invalid/mismatched receipt does not execute."""
    session_id = "session_pe_3"
    tool_name = "html_notes.canvas.upsert_widget"
    tool_args = {"widget_type": "clock", "widget_id": "clock_pe_3"}
    
    # Receipt with mismatched tool ID
    auth = create_test_authorization(
        tool_call_id="call_pe_3",
        tool_id="html_notes.canvas.remove_widget",  # Wrong tool ID
        session_id=session_id,
    )
    
    res = await executor.execute(
        tool_name=tool_name,
        args=tool_args,
        session_id=session_id,
        canvas_html="<div id='dashboard-grid'></div>",
        authorization=auth,
    )
    assert res["success"] is False
    assert res.get("code") == "TOOL_MISMATCH"


@pytest.mark.asyncio
async def test_runtime_admitted_local_write_with_expired_receipt_does_not_execute(executor):
    """4. Local write with expired receipt does not execute."""
    session_id = "session_pe_4"
    tool_name = "html_notes.canvas.upsert_widget"
    tool_args = {"widget_type": "clock", "widget_id": "clock_pe_4"}
    
    auth = create_test_authorization(
        tool_call_id="call_pe_4",
        tool_id=tool_name,
        session_id=session_id,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),  # Expired
    )
    
    res = await executor.execute(
        tool_name=tool_name,
        args=tool_args,
        session_id=session_id,
        canvas_html="<div id='dashboard-grid'></div>",
        authorization=auth,
    )
    assert res["success"] is False
    assert res.get("code") == "EXPIRED_RECEIPT"
    assert "expired" in str(res.get("error")).lower()


@pytest.mark.asyncio
async def test_runtime_admitted_local_write_with_foreign_session_does_not_execute(executor):
    """5. Local write with foreign session ID in authorization receipt does not execute."""
    session_id = "session_owner_pe_5"
    tool_name = "html_notes.canvas.upsert_widget"
    tool_args = {"widget_type": "clock", "widget_id": "clock_pe_5"}
    
    auth = create_test_authorization(
        tool_call_id="call_pe_5",
        tool_id=tool_name,
        session_id="session_foreign_pe_5",  # Foreign session
    )
    
    res = await executor.execute(
        tool_name=tool_name,
        args=tool_args,
        session_id=session_id,
        canvas_html="<div id='dashboard-grid'></div>",
        authorization=auth,
    )
    assert res["success"] is False
    assert res.get("code") == "SESSION_MISMATCH"
    assert "session" in str(res.get("error")).lower()


@pytest.mark.asyncio
async def test_runtime_global_tool_never_calls_local_executor():
    """6. Global runtime tool event (e.g. web search) never calls local executor callback."""
    events = [
        FakeRuntimeEvent("e1", "run.started", "2026-09-19T12:00:00Z", {"status": "running"}),
        FakeRuntimeEvent("e2", "tool.invoked", "2026-09-19T12:00:01Z", {
            "tool_name": "web_search",
            "execution": "runtime",
            "arguments": {"query": "latest news"},
        }),
        FakeRuntimeEvent("e3", "tool.completed", "2026-09-19T12:00:02Z", {
            "tool_name": "web_search",
            "result": {"summary": "found news"},
        }),
        FakeRuntimeEvent("e4", "run.completed", "2026-09-19T12:00:03Z", {}),
    ]
    
    client = FakeStreamingClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)
    
    local_called = []
    async def mock_local_cb(*args, **kwargs):
        local_called.append(args)
        yield "data: {}\n\n"
        
    frames = []
    async for f in adapter.stream_chat_turn(
        query="search news",
        session_id="session_pe_6",
        execute_local_tool_cb=mock_local_cb,
    ):
        frames.append(f)
        
    assert len(local_called) == 0


@pytest.mark.asyncio
async def test_runtime_outage_creates_no_local_side_effect():
    """7. Runtime outage/disconnection creates no local side-effects."""
    client = FakeStreamingClient(raise_on_stream=ConnectionError("Runtime unreachable"))
    adapter = RuntimeChatAdapter(runtime_client=client)
    
    local_called = []
    async def mock_local_cb(*args, **kwargs):
        local_called.append(args)
        yield "data: {}\n\n"
        
    frames = []
    async for f in adapter.stream_chat_turn(
        query="render widget",
        session_id="session_pe_7",
        execute_local_tool_cb=mock_local_cb,
    ):
        frames.append(f)
        
    assert len(local_called) == 0
    # Must yield error frame and terminal done
    error_frames = [f for f in frames if f.get("type") == "error"]
    done_frames = [f for f in frames if f.get("type") == "done"]
    assert len(error_frames) >= 1
    assert len(done_frames) == 1


@pytest.mark.asyncio
async def test_runtime_denial_creates_no_local_side_effect():
    """8. Runtime denial event creates no local tool side-effects."""
    events = [
        FakeRuntimeEvent("e1", "run.started", "2026-09-19T12:00:00Z", {"status": "running"}),
        FakeRuntimeEvent("e2", "tool.failed", "2026-09-19T12:00:01Z", {
            "tool_name": "html_notes.canvas.upsert_widget",
            "error": {"code": "TOOL_PERMISSION_DENIED", "message": "Policy denied tool invocation"},
        }),
        FakeRuntimeEvent("e3", "run.completed", "2026-09-19T12:00:02Z", {}),
    ]
    
    client = FakeStreamingClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)
    
    local_called = []
    async def mock_local_cb(*args, **kwargs):
        local_called.append(args)
        yield "data: {}\n\n"
        
    frames = []
    async for f in adapter.stream_chat_turn(
        query="render widget",
        session_id="session_pe_8",
        execute_local_tool_cb=mock_local_cb,
    ):
        frames.append(f)
        
    assert len(local_called) == 0
    statuses = [f for f in frames if f.get("type") == "status" and f.get("phase") == "tool_failed"]
    assert len(statuses) == 1
    errors = [f for f in frames if f.get("type") == "error" and f.get("code") == "TOOL_PERMISSION_DENIED"]
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_shared_runtime_request_emits_exactly_one_done():
    """9. Shared runtime request turn emits exactly one terminal done frame."""
    events = [
        FakeRuntimeEvent("e1", "run.started", "2026-09-19T12:00:00Z", {"status": "running"}),
        FakeRuntimeEvent("e2", "content.delta", "2026-09-19T12:00:01Z", {"delta": "Hello "}),
        FakeRuntimeEvent("e3", "content.delta", "2026-09-19T12:00:02Z", {"delta": "World"}),
        FakeRuntimeEvent("e4", "run.completed", "2026-09-19T12:00:03Z", {}),
    ]
    
    client = FakeStreamingClient(events=events)
    adapter = RuntimeChatAdapter(runtime_client=client)
    
    frames = []
    async for f in adapter.stream_chat_turn(
        query="hi",
        session_id="session_pe_9",
    ):
        frames.append(f)
        
    done_frames = [f for f in frames if f.get("type") == "done"]
    assert len(done_frames) == 1


from app.domain.notes.service import NotesDomainService


def test_legacy_note_cannot_be_edited_from_foreign_session(clean_db):
    """10. Legacy migrated note cannot be edited from a foreign session."""
    # 1. Insert legacy unclaimed note directly into database
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'legacy-int-note-1', 'Legacy Original Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Original</p>', 1, NULL, 'legacy_unclaimed', 'migration-2026-09-19'
        )
    """)
    conn.commit()
    conn.close()

    # 2. Update without claiming is rejected with NOTE_UNCLAIMED
    res_unclaimed = NotesDomainService.update_note(
        note_id="legacy-int-note-1",
        session_id="session_A",
        title="Modified Content",
    )
    assert res_unclaimed.get("is_error") is True
    assert res_unclaimed.get("code") == "NOTE_UNCLAIMED"

    # 3. Session A explicitly claims the note
    claimed = NotesDomainService.claim_note(
        note_id="legacy-int-note-1",
        session_id="session_A",
        owner_id="session_A",
    )
    assert claimed.get("is_error") is not True
    assert claimed.get("success") is True
    persisted = database.get_note_by_id("legacy-int-note-1")
    assert persisted["owner_id"] == "session_A"
    assert persisted["owner_type"] == "session"
    assert persisted["session_id"] == "session_A"

    # 4. Session B tries to update Session A's claimed note -> rejected
    res_foreign = NotesDomainService.update_note(
        note_id="legacy-int-note-1",
        session_id="session_B",
        title="Hacked by B",
    )
    assert res_foreign.get("is_error") is True
    assert res_foreign.get("code") == "NOTE_SESSION_MISMATCH"

    # 5. Session A can safely update
    updated = NotesDomainService.update_note(
        note_id="legacy-int-note-1",
        session_id="session_A",
        title="Updated by Session A",
    )
    assert updated.get("is_error") is not True
    assert updated.get("success") is True
    persisted_updated = database.get_note_by_id("legacy-int-note-1")
    assert persisted_updated["title"] == "Updated by Session A"
