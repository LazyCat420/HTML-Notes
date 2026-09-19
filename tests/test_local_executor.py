import pytest
from app.tooling.local_executor import local_tool_executor
from app import database

@pytest.mark.asyncio
async def test_local_executor_notes_crud():
    # 1. Create note
    create_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.create",
        args={
            "title": "Executor Unit Test Note",
            "rendered_html": "<article><p>Hello from local executor test!</p></article>",
            "tags": ["unit-test", "executor"]
        }
    )
    assert create_res["success"] is True
    assert "note_id" in create_res["result"]
    note_id = create_res["result"]["note_id"]

    # 2. Get note
    get_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.get",
        args={"note_id": note_id}
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
        }
    )
    assert update_res["success"] is True

    # 4. Search notes
    search_res = await local_tool_executor.execute(
        tool_name="html_notes.notes.search",
        args={"query": "Updated Test Note"}
    )
    assert search_res["success"] is True
    assert any(n["id"] == note_id for n in search_res["result"]["results"])

@pytest.mark.asyncio
async def test_local_executor_canvas_upsert_and_dom():
    # 1. Upsert widget (using canonical name)
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={
            "widget_type": "clock",
            "widget_id": "clock_test_1",
            "config": {"mode": "clock", "timezone": "UTC"}
        }
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
        }
    )
    assert res_legacy["success"] is True
    assert "stock_aapl_1" in res_legacy["result"]["html"]

    # 3. Canvas DOM read
    canvas_markup = "<div id='dashboard-grid'><div id='w1' class='widget-container' data-widget-type='clock'><h3>My Clock</h3></div></div>"
    read_res = await local_tool_executor.execute(
        tool_name="canvas_read_dom",
        args={},
        canvas_html=canvas_markup
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
        canvas_html=canvas_markup
    )
    assert mod_res["success"] is True
    assert mod_res["result"]["removed"] == "w1"

@pytest.mark.asyncio
async def test_local_executor_legacy_custom_widget_quarantine():
    # plan_widget
    plan_res = await local_tool_executor.execute(
        tool_name="plan_widget",
        args={"widgetType": "custom", "title": "My Custom Card"}
    )
    assert plan_res["success"] is True
    assert plan_res["result"].get("deprecated") is True

    # create_widget
    create_res = await local_tool_executor.execute(
        tool_name="create_widget",
        args={
            "widgetType": "custom",
            "title": "Custom Card",
            "htmlContent": "<div>Custom Content</div>"
        }
    )
    assert create_res["success"] is True
    assert create_res["result"].get("deprecated") is True

@pytest.mark.asyncio
async def test_local_executor_rejection_for_unadmitted_tools():
    res = await local_tool_executor.execute(
        tool_name="unauthorized_system_command",
        args={"cmd": "ls"}
    )
    assert res["success"] is False
    assert res["is_error"] is True
    assert "not permitted" in res["error"]
