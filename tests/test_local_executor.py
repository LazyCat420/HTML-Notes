import uuid
from datetime import datetime, timedelta, timezone
import pytest
from app.tooling.local_executor import local_tool_executor
from app.adapters.runtime.models import LocalToolAuthorization
from app import database


def make_auth(tool_name: str, session_id: str, app_id: str = "html-notes") -> LocalToolAuthorization:
    now = datetime.now(timezone.utc)
    return LocalToolAuthorization(
        run_id="run_test_local_executor",
        tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
        canonical_tool_id=tool_name,
        profile_id="html-notes-canvas-v1",
        app_id=app_id,
        session_id=session_id,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        nonce=f"nonce_{uuid.uuid4().hex[:12]}",
        signature="sha256-valid-test-sig",
    )


@pytest.mark.asyncio
async def test_local_executor_notes_crud():
    session_id = "session_test_crud"
    # 1. Create note
    create_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.create",
        args={
            "title": "Executor Unit Test Note",
            "rendered_html": "<article><p>Hello from local executor test!</p></article>",
            "tags": ["unit-test", "executor"]
        },
        session_id=session_id,
        authorization=make_auth("html_notes.notes.create", session_id=session_id)
    )
    assert create_res["success"] is True
    assert "note_id" in create_res["result"]
    note_id = create_res["result"]["note_id"]

    # 2. Get note
    get_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": note_id},
        session_id=session_id
    )
    assert get_res["success"] is True
    assert get_res["result"]["title"] == "Executor Unit Test Note"

    # 3. Update note (using legacy name)
    update_res = await local_tool_executor.execute(
        tool_name="html_notes_update_note",
        args={
            "note_id": note_id,
            "title": "Updated Test Note Title",
            "rendered_html": "<article><p>Updated content</p></article>"
        },
        session_id=session_id,
        authorization=make_auth("html_notes.notes.update", session_id=session_id)
    )
    assert update_res["success"] is True

    # 4. Search notes
    search_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.search",
        args={"query": "Updated Test Note"},
        session_id=session_id
    )
    assert search_res["success"] is True
    assert any(n["id"] == note_id for n in search_res["result"]["results"])


@pytest.mark.asyncio
async def test_note_update_rejects_cross_session_or_unauthorized_note():
    # 1. Create note in session_A
    create_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.create",
        args={
            "title": "Session A Private Note",
            "rendered_html": "<article><p>Confidential</p></article>"
        },
        session_id="session_A",
        authorization=make_auth("html_notes.notes.create", session_id="session_A")
    )
    assert create_res["success"] is True
    note_id = create_res["result"]["note_id"]

    # 2. Attempt update from session_B
    bad_update = await local_tool_executor.execute(
        tool_name="html_notes.notes.update",
        args={
            "note_id": note_id,
            "title": "Compromised Title"
        },
        session_id="session_B",
        authorization=make_auth("html_notes.notes.update", session_id="session_B")
    )
    assert bad_update["success"] is False
    assert bad_update["is_error"] is True
    assert "another session" in bad_update["error"] or "Unauthorized" in bad_update["error"]


@pytest.mark.asyncio
async def test_local_executor_canvas_upsert_and_dom():
    session_id = "session_canvas_1"
    # 1. Upsert widget (using canonical name)
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "clock",
            "widget_id": "clock_test_1",
            "config": {"mode": "clock", "timezone": "UTC"}
        },
        session_id=session_id,
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id=session_id)
    )
    assert res["success"] is True
    assert "clock_test_1" in res["result"]["html"]

    # 2. Upsert widget (using legacy alias)
    res_legacy = await local_tool_executor.execute(
        tool_name="canvas_add_widget",
        args={
            "widget_type": "stock_card",
            "widget_id": "stock_aapl_1",
            "config": {"symbol": "AAPL"}
        },
        session_id=session_id,
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id=session_id)
    )
    assert res_legacy["success"] is True
    assert "stock_aapl_1" in res_legacy["result"]["html"]

    # 3. Canvas DOM read
    canvas_markup = "<div id='dashboard-grid'><div id='w1' class='widget-container' data-widget-type='clock'><h3>My Clock</h3></div></div>"
    read_res = await local_tool_executor.execute(
        tool_name="canvas_read_dom",
        args={},
        canvas_html=canvas_markup,
        session_id=session_id
    )
    assert read_res["success"] is True
    assert read_res["result"]["count"] == 1
    assert read_res["result"]["widgets"][0]["widget_id"] == "w1"

    # 4. Canvas DOM modify
    mod_res = await local_tool_executor.execute(
        tool_name="canvas_modify_dom",
        args={
            "action": "remove",
            "selector": "#w1"
        },
        canvas_html=canvas_markup,
        session_id=session_id,
        authorization=make_auth("html_notes.canvas.mutate", session_id=session_id)
    )
    assert mod_res["success"] is True
    assert mod_res["result"]["removed"] == "w1"


@pytest.mark.asyncio
async def test_widget_update_rejects_cross_session_widget_id():
    res1 = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "clock",
            "widget_id": "session_x_clock",
            "config": {}
        },
        session_id="session_X",
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id="session_X")
    )
    assert res1["success"] is True

    # Cross session update with same widget ID
    res2 = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "clock",
            "widget_id": "session_x_clock",
            "config": {}
        },
        session_id="session_Y",
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id="session_Y")
    )
    assert res2["success"] is False
    assert res2["is_error"] is True
    assert "belongs to another session" in res2["error"]


@pytest.mark.asyncio
async def test_widget_remove_rejects_ambiguous_selector():
    session_id = "session_remove"
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.remove_widget",
        args={"selector": ".widget-container"},
        session_id=session_id,
        authorization=make_auth("html_notes.canvas.remove_widget", session_id=session_id)
    )
    assert res["success"] is False
    assert "Ambiguous remove" in res["error"]


@pytest.mark.asyncio
async def test_widget_upsert_preserves_singleton_rule():
    session_id = "session_singleton"
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "weather",
            "widget_id": "weather_test_1",
            "config": {"location": "Tokyo"}
        },
        session_id=session_id,
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id=session_id)
    )
    assert res["success"] is True
    assert res["result"]["is_singleton"] is True


@pytest.mark.asyncio
async def test_weather_widget_singleton_updates_in_place():
    session_id = "session_weather_in_place"
    canvas_html = '<div id="dashboard-grid"><div id="weather_primary" class="widget-container" data-widget-type="weather"></div></div>'
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "weather",
            "widget_id": "weather_spawned_duplicate",
            "config": {"location": "London"}
        },
        session_id=session_id,
        canvas_html=canvas_html,
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id=session_id)
    )
    assert res["success"] is True
    assert res["result"]["widget_id"] == "weather_primary"
    assert "London" in res["result"]["html"]


@pytest.mark.asyncio
async def test_map_widget_singleton_updates_in_place():
    session_id = "session_map_in_place"
    canvas_html = '<div id="dashboard-grid"><div id="map_primary" class="widget-container" data-widget-type="map"></div></div>'
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "map",
            "widget_id": "map_spawned_duplicate",
            "config": {"map_query": "Paris"}
        },
        session_id=session_id,
        canvas_html=canvas_html,
        authorization=make_auth("html_notes.canvas.upsert_widget", session_id=session_id)
    )
    assert res["success"] is True
    assert res["result"]["widget_id"] == "map_primary"


@pytest.mark.asyncio
async def test_watch_create_requires_session_and_expiry():
    # Without session_id -> scope failure
    res_no_session = await local_tool_executor.execute(
        tool_name="html_notes.watches.create",
        args={"kind": "price_alert", "spec": {"symbol": "AAPL", "condition": {"field": "price", "op": ">=", "value": 200}}}
    )
    assert res_no_session["success"] is False
    assert "requires session_id" in res_no_session["error"]

    # With session_id and valid authorization -> success and valid expiry timestamp
    session_id = f"session_watch_create_{uuid.uuid4().hex[:8]}"
    res = await local_tool_executor.execute(
        tool_name="html_notes.watches.create",
        args={"kind": "price_alert", "spec": {"symbol": "AAPL", "condition": {"field": "price", "op": ">=", "value": 200}}},
        session_id=session_id,
        authorization=make_auth("html_notes.watches.create", session_id=session_id)
    )
    assert res["success"] is True
    assert res["result"]["expires"] is not None


@pytest.mark.asyncio
async def test_watch_cancel_rejects_foreign_watch():
    # Create in session_1
    s1 = f"session_watch_1_{uuid.uuid4().hex[:8]}"
    s2 = f"session_watch_2_{uuid.uuid4().hex[:8]}"
    res = await local_tool_executor.execute(
        tool_name="html_notes.watches.create",
        args={"kind": "price_alert", "spec": {"symbol": "NVDA", "condition": {"field": "price", "op": ">=", "value": 150}}},
        session_id=s1,
        authorization=make_auth("html_notes.watches.create", session_id=s1)
    )
    assert res["success"] is True
    watch_id = res["result"]["watch_id"]

    # Cancel from session_2 -> rejected
    cancel_res = await local_tool_executor.execute(
        tool_name="html_notes.watches.cancel",
        args={"watch_id": watch_id},
        session_id=s2,
        authorization=make_auth("html_notes.watches.cancel", session_id=s2)
    )
    assert cancel_res["success"] is False
    assert "belongs to another session" in cancel_res["error"] or "Unauthorized" in cancel_res["error"]


@pytest.mark.asyncio
async def test_local_executor_rejection_for_unadmitted_tools():
    res = await local_tool_executor.execute(
        tool_name="unauthorized_system_command",
        args={"cmd": "ls"}
    )
    assert res["success"] is False
    assert res["is_error"] is True
    assert "not permitted" in res["error"].lower()
