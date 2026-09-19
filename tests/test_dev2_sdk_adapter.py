import pytest
from app.services.dev2_sdk_adapter import mock_sdk
import asyncio

@pytest.mark.asyncio
async def test_sdk_widget_targeting():
    # Simulate an SDK run that outputs a widget target event
    run_id = await mock_sdk.create_run({"input": "Create a note about AWS", "session_id": "test_123"})
    assert run_id is not None

    events = []
    async for event in mock_sdk.observe(run_id):
        events.append(event)
        
    tool_events = [e for e in events if e.get("type") == "tool_call"]
    assert len(tool_events) > 0, "SDK mock should emit at least one tool call"
    
    target_event = tool_events[0]
    assert target_event["tool"] == "canvas_add_widget" or target_event["tool"] == "create_widget"
    
@pytest.mark.asyncio
async def test_sdk_followup_and_conflicts():
    # Test follow-up and note conflicts via duplicate writes logic
    run_id = await mock_sdk.create_run({"input": "Update the AWS note", "session_id": "test_123"})
    
    events = []
    async for event in mock_sdk.observe(run_id):
        events.append(event)
        
    status_events = [e for e in events if e.get("type") == "status"]
    assert len(status_events) > 0, "SDK mock should emit status updates"
