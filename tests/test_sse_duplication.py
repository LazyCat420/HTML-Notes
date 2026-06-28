import os
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

# Ensure test DB is used
os.environ["DATABASE_URL"] = "data/test_notes.db"

from app.main import app
from app import database

client = TestClient(app)

class MockAsyncResponse:
    def __init__(self, status_code, chunks):
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

@patch("httpx.AsyncClient.stream")
def test_sse_no_duplicate_widget(mock_stream):
    session_id = "test-session-sse-duplication"
    
    # Initialize DB schema
    database.init_db()
    
    # Initialize DB connection and clear old test data
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    cursor.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)", (session_id, "SSE Test", "2026-06-27T11:00:00Z"))
    conn.commit()
    conn.close()

    # Define mock SSE stream returned by Prism
    mock_events = [
        'data: {"type": "tool_execution", "status": "preparing", "tool": {"name": "mcp__lazy-tool-service__canvas_add_widget", "args": {"widget_type": "mini_music_player", "widget_id": "widget-music-player-1", "config": {"autoplay": true, "genre": "jazz"}}}}\n',
        'data: {"type": "chunk", "content": "I "}\n',
        'data: {"type": "chunk", "content": "have "}\n',
        'data: {"type": "chunk", "content": "added "}\n',
        'data: {"type": "chunk", "content": "the "}\n',
        'data: {"type": "chunk", "content": "widget. "}\n',
        'data: {"type": "done"}\n'
    ]

    mock_stream.return_value = MockAsyncResponse(200, mock_events)

    # Call the endpoint
    res = client.post("/session/message", json={
        "session_id": session_id,
        "message": "Add a music widget please",
        "provider": "vllm",
        "model": "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit",
        "current_canvas": '<div id="dashboard-grid" class="dashboard-grid"></div>'
    })

    assert res.status_code == 200
    
    # Parse SSE output from response
    lines = res.text.split("\n")
    events = []
    for line in lines:
        if line.strip().startswith("data: "):
            try:
                events.append(json.loads(line.strip()[6:]))
            except Exception:
                pass

    # Extract all 'component' event payloads
    component_events = [e for e in events if e.get("type") == "component"]
    print("Received events:", [e.get("type") for e in events])
    print("Component count:", len(component_events))
    
    # Assert that there is EXACTLY ONE component event containing the widget injection
    assert len(component_events) == 1, f"Expected exactly 1 component event, but got {len(component_events)}"
    
    component_html = component_events[0]["content"]
    occurrences = component_html.count("widget-music-player-1")
    print(f"Occurrences of widget-music-player-1 in component content: {occurrences}")
    
    # The ID occurs multiple times in the HTML template (for class bindings/controls),
    # but the widget container itself should only be appended once.
    # In generate_widget_html, the container element has id="widget-music-player-1".
    container_count = component_html.count('id="widget-music-player-1"')
    print(f"Container element count: {container_count}")
    assert container_count == 1, f"Expected widget container to be appended exactly once, got {container_count}"

@patch("httpx.AsyncClient.stream")
def test_youtube_widget_in_place_replacement(mock_stream):
    session_id = "test-session-youtube-replacement"
    
    database.init_db()
    
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    cursor.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)", (session_id, "Youtube Test", "2026-06-27T11:00:00Z"))
    conn.commit()
    conn.close()

    # Stream adding a youtube widget when one already exists with a different ID
    mock_events = [
        'data: {"type": "tool_execution", "status": "preparing", "tool": {"name": "mcp__lazy-tool-service__canvas_add_widget", "args": {"widget_type": "youtube_player", "widget_id": "fireship-video-latest", "config": {"video_id": "new_video_id", "title": "New Video Title"}}}}\n',
        'data: {"type": "chunk", "content": "Updated "}\n',
        'data: {"type": "done"}\n'
    ]

    mock_stream.return_value = MockAsyncResponse(200, mock_events)

    # Note the custom ID 'widget-pokemon-30th' and the iframe
    existing_canvas = '''
    <div id="dashboard-grid" class="dashboard-grid">
        <div class="widget-container col-span-2 relative overflow-hidden h-[380px]" id="widget-pokemon-30th" x-data="youtubePlayerWidget('old_video_id', 'Old Title')">
            <iframe src="https://www.youtube.com/embed/old_video_id"></iframe>
        </div>
    </div>
    '''

    res = client.post("/session/message", json={
        "session_id": session_id,
        "message": "Add latest video",
        "provider": "vllm",
        "model": "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit",
        "current_canvas": existing_canvas
    })

    assert res.status_code == 200
    
    lines = res.text.split("\n")
    events = []
    for line in lines:
        if line.strip().startswith("data: "):
            try:
                events.append(json.loads(line.strip()[6:]))
            except Exception:
                pass

    component_events = [e for e in events if e.get("type") == "component"]
    assert len(component_events) == 1
    
    component_html = component_events[0]["content"]
    
    # Verify the old ID was reused/replaced, and the new details are injected
    assert "widget-pokemon-30th" in component_html
    assert "fireship-video-latest" not in component_html
    assert "new_video_id" in component_html
    assert "New Video Title" in component_html
    
    # And there shouldn't be two widget containers
    container_count = component_html.count('id="widget-pokemon-30th"')
    assert container_count == 1, f"Expected exactly 1 widget with ID widget-pokemon-30th, got {container_count}"
