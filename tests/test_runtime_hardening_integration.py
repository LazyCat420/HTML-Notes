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
        self.submitted_tool_results = []

    async def submit_tool_result(self, run_id, tool_call_id, *, result, is_error=False, authorization_receipt=None):
        self.submitted_tool_results.append({
            "run_id": run_id,
            "tool_call_id": tool_call_id,
            "result": result,
            "is_error": is_error,
            "authorization_receipt": authorization_receipt,
        })
        return {"ok": True}

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


@pytest.mark.asyncio
async def test_adversarial_forged_or_missing_receipt_via_http(clean_db, monkeypatch):
    """Adversarial: Forged or missing receipt via runtime stream results in admission failure and no mutation."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")
    from app.main import app
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from app.services.runtime_chat_adapter import RuntimeChatAdapter

    # Mock runtime client that emits a tool call with a forged signature
    events = [
        FakeRuntimeEvent("e1", "run.started", "2026-09-19T12:00:00Z", {"status": "running"}, run_id="run_adv_forged"),
        FakeRuntimeEvent("e2", "tool.invoked", "2026-09-19T12:00:01Z", {
            "tool": "html_notes.notes.create",
            "arguments": {"title": "Forged Note", "rendered_html": "<p>Forged</p>"},
            "id": "call_adv_forged",
            "execution": "local",
            "authorization_receipt": {
                "run_id": "run_adv_forged",
                "tool_call_id": "call_adv_forged",
                "canonical_tool_id": "html_notes.notes.create",
                "profile_id": "html-notes-canvas-v1",
                "app_id": "html-notes",
                "session_id": "session_adv_forged",
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                "nonce": "nonce_adv_forged",
                "signature": "sha256-invalidforgedsignature00000000000000000000000000000000000000000"
            }
        }, run_id="run_adv_forged"),
        FakeRuntimeEvent("e3", "run.completed", "2026-09-19T12:00:02Z", {}, run_id="run_adv_forged"),
    ]
    fake_client = FakeStreamingClient(events=events)
    fake_adapter = RuntimeChatAdapter(runtime_client=fake_client)

    client = TestClient(app)
    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=fake_adapter):
        resp = client.post("/session/message", json={
            "session_id": "session_adv_forged",
            "message": "render custom card",
            "current_canvas": "<div id='dashboard-grid'></div>",
        })
    assert resp.status_code == 200
    body = resp.text

    # Verify error frame emitted for signature verification
    assert '"code": "INVALID_SIGNATURE"' in body
    # Verify no note was created in the database
    notes = database.list_all_notes()
    assert len(notes) == 0


@pytest.mark.asyncio
async def test_adversarial_context_mismatch_run_call_profile(executor):
    """Adversarial: Receipts with mismatched run_id, tool_call_id, or profile_id are rejected."""
    session_id = "session_adv_ctx"
    now = datetime.now(timezone.utc)
    base_auth = create_test_authorization(
        run_id="run_expected",
        tool_call_id="call_expected",
        tool_id="html_notes.notes.get",
        session_id=session_id,
        profile_id="html-notes-canvas-v1",
    )

    # 1. Run ID mismatch
    res_run = await executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": "any"},
        session_id=session_id,
        authorization=base_auth,
        runtime_context={"run_id": "run_WRONG", "profile_id": "html-notes-canvas-v1", "tool_call_id": "call_expected"},
    )
    assert res_run["success"] is False
    assert res_run.get("code") == "RUN_MISMATCH" or "Run mismatch" in res_run.get("error", "")

    # 2. Tool call ID mismatch
    res_call = await executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": "any"},
        session_id=session_id,
        authorization=base_auth,
        runtime_context={"run_id": "run_expected", "profile_id": "html-notes-canvas-v1", "tool_call_id": "call_WRONG"},
    )
    assert res_call["success"] is False
    assert res_call.get("code") == "TOOL_CALL_MISMATCH" or "Tool call mismatch" in res_call.get("error", "")

    # 3. Profile ID mismatch
    res_prof = await executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": "any"},
        session_id=session_id,
        authorization=base_auth,
        runtime_context={"run_id": "run_expected", "profile_id": "WRONG_PROFILE", "tool_call_id": "call_expected"},
    )
    assert res_prof["success"] is False
    assert res_prof.get("code") == "PROFILE_MISMATCH" or "Profile mismatch" in res_prof.get("error", "")


@pytest.mark.asyncio
async def test_adversarial_repeated_nonce_rejected_across_calls(executor):
    """Adversarial: Replaying the same nonce with a different tool call is rejected."""
    session_id = "session_adv_nonce"
    shared_nonce = f"fixed_nonce_{datetime.now(timezone.utc).timestamp()}"

    auth1 = create_test_authorization(
        run_id="run_1",
        tool_call_id="call_1",
        tool_id="html_notes.notes.get",
        session_id=session_id,
        nonce=shared_nonce,
    )
    auth2 = create_test_authorization(
        run_id="run_2",
        tool_call_id="call_2",
        tool_id="html_notes.notes.get",
        session_id=session_id,
        nonce=shared_nonce,
    )

    # First call succeeds admission
    res1 = await executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": "nonexistent"},
        session_id=session_id,
        authorization=auth1,
        runtime_context={"run_id": "run_1", "profile_id": "html-notes-canvas-v1", "tool_call_id": "call_1"},
    )
    assert res1.get("code") != "REPLAYED_RECEIPT"

    # Second call with same nonce must be rejected with REPLAYED_RECEIPT
    res2 = await executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": "nonexistent"},
        session_id=session_id,
        authorization=auth2,
        runtime_context={"run_id": "run_2", "profile_id": "html-notes-canvas-v1", "tool_call_id": "call_2"},
    )
    assert res2["success"] is False
    assert res2.get("code") == "REPLAYED_RECEIPT" or "Replayed authorization receipt" in res2.get("error", "")


def test_adversarial_concurrent_claim_protection_via_http(clean_db, monkeypatch):
    """Adversarial: Concurrent claim operations on the same note permit only one winner (409 on second)."""
    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Insert unclaimed note
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'concurrent-note', 'Concurrent Test', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Concurrent</p>', 1, NULL, 'legacy_unclaimed', 'mig'
        )
    """)
    conn.commit()
    conn.close()

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    original_claim = database.claim_note
    def racing_claim(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_claim(*args, **kwargs)
    monkeypatch.setattr(database, "claim_note", racing_claim)
    def claim(session):
        return TestClient(app).post("/notes/claim", json={"note_id": "concurrent-note", "session_id": session})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(claim, ["session_one", "session_two"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json()["session_id"] for r in responses if r.status_code == 200)
    assert database.get_note_by_id("concurrent-note")["session_id"] == winner



@pytest.mark.asyncio
async def test_adversarial_readiness_probe_fails_on_unreachable_runtime(clean_db, monkeypatch):
    """Adversarial: /health/agent returns 503 when shared runtime is unreachable or reports invalid contract."""
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")
    from app.main import app
    from fastapi.testclient import TestClient
    from app.adapters.runtime.config import RuntimeReadinessResult
    import app.routes.health as health_module

    # Mock check_runtime_readiness returning unready
    async def mock_unready(*args, **kwargs):
        return RuntimeReadinessResult(
            is_ready=False,
            error="Runtime unreachable on port 5591",
            details={"phase": "reachability"}
        )

    monkeypatch.setattr("app.adapters.runtime.config.check_runtime_readiness", mock_unready)

    client = TestClient(app)
    resp = client.get("/health/agent")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unavailable"
    assert "Runtime unreachable" in data["error"]


@pytest.mark.asyncio
async def test_adversarial_exactly_one_terminal_sse_event_all_cases():
    """Adversarial: Every stream turn (completed, error, cancelled) emits exactly one done frame."""
    from app.services.runtime_chat_adapter import RuntimeChatAdapter

    # Case A: completed turn
    events_ok = [
        FakeRuntimeEvent("e1", "run.started", "2026-09-19T12:00:00Z", {"status": "running"}),
        FakeRuntimeEvent("e2", "run.completed", "2026-09-19T12:00:01Z", {}),
    ]
    adapter_ok = RuntimeChatAdapter(runtime_client=FakeStreamingClient(events=events_ok))
    frames_ok = [f async for f in adapter_ok.stream_chat_turn(query="q", session_id="s")]
    assert len([f for f in frames_ok if f.get("type") == "done"]) == 1

    # Case B: error/exception turn
    adapter_err = RuntimeChatAdapter(runtime_client=FakeStreamingClient(raise_on_stream=RuntimeError("Outage")))
    frames_err = [f async for f in adapter_err.stream_chat_turn(query="q", session_id="s")]
    assert len([f for f in frames_err if f.get("type") == "done"]) == 1
    assert any(f.get("type") == "error" for f in frames_err)

    # Case C: cancelled turn
    import asyncio
    cancel_event = asyncio.Event()
    cancel_event.set()
    adapter_cancel = RuntimeChatAdapter(runtime_client=FakeStreamingClient(events=events_ok))
    frames_cancel = [f async for f in adapter_cancel.stream_chat_turn(query="q", session_id="s", cancel_event=cancel_event)]
    assert len([f for f in frames_cancel if f.get("type") == "done"]) == 1
    assert any(f.get("phase") == "cancelled" for f in frames_cancel)


def _signed_wire_receipt(tool_args, session_id, call_id="call-wire", nonce=None, **changes):
    """Node's wire representation, including millisecond timestamps and UTF-8 JSON."""
    import hashlib, hmac, os, secrets
    now = datetime.now(timezone.utc)
    wire_args = json.dumps(tool_args, separators=(",", ":"), ensure_ascii=False)
    receipt = {
        "run_id": "run-wire", "tool_call_id": call_id, "tool_name": "html_notes.notes.create",
        "app_id": "html-notes", "session_id": session_id, "profile_id": "html-notes-canvas-v1",
        "nonce": nonce or secrets.token_hex(16),
        "issued_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "arguments_json": wire_args, "arguments_hash": hashlib.sha256(wire_args.encode()).hexdigest(),
    }
    receipt.update(changes)
    payload = ":".join(receipt[k] for k in ("run_id", "tool_call_id", "tool_name", "arguments_hash", "app_id", "session_id", "profile_id", "nonce", "expires_at"))
    receipt["signature"] = "hmac-sha256-" + hmac.new(os.environ["RUNTIME_AUTH_SECRET"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    return receipt


@pytest.mark.asyncio
async def test_persistent_mutation_journal_replays_original_result_after_process_cache_loss(clean_db, monkeypatch):
    """A duplicate signed write after restart returns the first outcome without dispatching again."""
    from app.adapters.runtime.models import global_replay_cache
    from app.tooling.local_executor import LocalToolExecutor
    args = {"title": "Durable replay", "rendered_html": "<article><p>once</p></article>"}
    session_id = "session-durable-replay"
    receipt = _signed_wire_receipt(args, session_id, call_id="call-durable")
    context = {"run_id": "run-wire", "profile_id": "html-notes-canvas-v1", "tool_call_id": "call-durable"}
    executor = LocalToolExecutor()

    first = await executor.execute("html_notes.notes.create", args, session_id=session_id,
                                   authorization=receipt, runtime_context=context)
    assert first["success"] is True
    global_replay_cache.clear()  # model a fresh process; SQLite remains authoritative
    second = await LocalToolExecutor().execute("html_notes.notes.create", args, session_id=session_id,
                                               authorization=receipt, runtime_context=context)

    assert second == first
    assert len(database.list_all_notes()) == 1


@pytest.mark.asyncio
async def test_pending_mutation_after_restart_fails_unknown_without_redispatch(clean_db):
    """A crash window is at-most-once: uncertain mutations are never repeated."""
    from app.tooling.execution_journal import execution_journal
    from app.tooling.local_executor import LocalToolExecutor
    args = {"title": "Uncertain", "rendered_html": "<article><p>do not repeat</p></article>"}
    session_id = "session-pending-replay"
    receipt = _signed_wire_receipt(args, session_id, call_id="call-pending")
    claim = execution_journal.claim(
        app_id="html-notes", session_id=session_id, run_id="run-wire", tool_call_id="call-pending",
        nonce=receipt["nonce"], tool_id="html_notes.notes.create", arguments_hash=receipt["arguments_hash"],
    )
    assert claim.state == "claimed"

    result = await LocalToolExecutor().execute(
        "html_notes.notes.create", args, session_id=session_id, authorization=receipt,
        runtime_context={"run_id": "run-wire", "profile_id": "html-notes-canvas-v1", "tool_call_id": "call-pending"},
    )
    assert result["success"] is False
    assert result["code"] == "EXECUTION_OUTCOME_UNKNOWN"
    assert database.list_all_notes() == []


@pytest.mark.parametrize("case,code", [
    ("valid", None), ("unsigned", "UNSIGNED_RECEIPT"), ("forged", "INVALID_SIGNATURE"),
    ("run", "RUN_MISMATCH"), ("call", "TOOL_CALL_MISMATCH"), ("profile", "PROFILE_MISMATCH"),
    ("session", "SESSION_MISMATCH"), ("arguments", "ARGUMENTS_MISMATCH"),
    ("stripped_hash", "INVALID_SIGNATURE"), ("replay", "REPLAYED_RECEIPT"),
])
def test_signed_receipts_through_http_to_persistence(case, code, clean_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")
    session_id = "session-wire"
    args = {"title": "Café receipt", "rendered_html": "<article><p>Signed content</p></article>"}
    receipt = _signed_wire_receipt(args, session_id)
    if case in {"run", "call", "profile", "session"}:
        key = {"run": "run_id", "call": "tool_call_id", "profile": "profile_id", "session": "session_id"}[case]
        receipt = _signed_wire_receipt(args, session_id, **{key: "foreign-context"}) if case != "session" else _signed_wire_receipt(args, "foreign-context")
    elif case == "unsigned":
        receipt.pop("signature")
    elif case == "forged":
        import secrets
        receipt["signature"] = "hmac-sha256-" + secrets.token_hex(32)
    elif case == "arguments":
        args = {**args, "title": "Tampered"}
    elif case == "stripped_hash":
        receipt.pop("arguments_hash")
        receipt.pop("arguments_json")
    def event(auth, call="call-wire"):
        return FakeRuntimeEvent("tool", "tool.invoked", "2026-09-19T12:00:00Z", {
            "tool_name": "html_notes.notes.create", "tool_call_id": call, "arguments": args,
            "execution": "local", "required_scope": {"app_id": "html-notes", "session_id": session_id},
            "authorization_receipt": auth,
        }, run_id="run-wire")
    events = [event(receipt)]
    if case == "replay":
        events.append(event(_signed_wire_receipt(args, session_id, call_id="call-second", nonce=receipt["nonce"]), "call-second"))
    events.append(FakeRuntimeEvent("done", "run.completed", "2026-09-19T12:00:01Z", {}, run_id="run-wire"))
    adapter = RuntimeChatAdapter(runtime_client=FakeStreamingClient(events))
    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter", return_value=adapter):
        response = TestClient(app).post("/session/message", json={"session_id": session_id, "message": "render custom card"})
    assert response.status_code == 200
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert sum(f.get("type") == "done" for f in frames) == 1
    errors = [f for f in frames if f.get("type") == "error"]
    if code:
        assert any(f.get("code") == code for f in errors), errors
    else:
        assert not errors
    notes = database.list_all_notes()
    assert len(notes) == (1 if case in {"valid", "replay"} else 0)
    if notes:
        assert notes[0]["title"] == "Café receipt"
        assert database.get_note_by_id(notes[0]["id"])["session_id"] == session_id


def test_http_preflight_failure_never_starts_runtime(clean_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.adapters.runtime import config
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")
    monkeypatch.setattr(config, "check_runtime_readiness", AsyncMock(return_value=config.RuntimeReadinessResult(False, error="unreachable")))
    with patch("app.services.runtime_chat_adapter.RuntimeChatAdapter") as adapter:
        response = TestClient(app).post("/session/message", json={"session_id": "preflight", "message": "render custom card"})
    adapter.assert_not_called()
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [f["type"] for f in frames] == ["error", "done"]
    assert frames[0]["code"] == "RUNTIME_NOT_READY"
    assert database.list_all_notes() == []


@pytest.mark.asyncio
async def test_stream_finalization_failure_still_terminates():
    from app.services.runtime_chat_adapter import ensure_terminal_sse
    async def broken():
        yield 'data: {"type": "done"}\n\n'
        raise RuntimeError("persistence unavailable")
    frames = [json.loads(f[6:]) async for f in ensure_terminal_sse(broken())]
    assert [f["type"] for f in frames] == ["error", "done"]


@pytest.mark.parametrize("session_id", [None, "", "   "])
def test_sessionless_creation_is_rejected_by_http_and_domain(clean_db, session_id):
    from fastapi.testclient import TestClient
    from app.main import app
    result = NotesDomainService.create_note("Unowned", "<p>Unowned</p>", session_id=session_id)
    assert result["code"] == "SESSION_REQUIRED"
    response = TestClient(app).post("/notes/create", json={"title": "Unowned", "rendered_html": "<p>Unowned</p>", "session_id": session_id})
    assert response.status_code in {401, 422}
    assert database.list_all_notes() == []


def test_internal_http_rejects_foreign_note_update(clean_db, monkeypatch, patch_server):
    from fastapi.testclient import TestClient
    from app.main import app
    import secrets
    credential = secrets.token_hex(32)
    patch_server("_fetch_secret", AsyncMock(return_value=credential))
    created = NotesDomainService.create_note("Owned", "<p>Owned</p>", session_id="owner-http")
    response = TestClient(app).post("/internal/execute", headers={"x-internal-token": credential}, json={
        "tool": "html_notes_update_note", "session_id": "foreign-http", "args": {"note_id": created["note_id"], "title": "Changed"}})
    assert response.status_code == 200
    assert response.json()["code"] == "NOTE_SESSION_MISMATCH"
    assert database.get_note_by_id(created["note_id"])["title"] == "Owned"
