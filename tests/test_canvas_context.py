import os
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "data/test_notes_canvas.db"

from app.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_large_canvas_not_truncated():
    # Construct a canvas HTML that is larger than the old 3000 limit, but under the new 50000 limit.
    large_canvas = '<div id="dashboard-grid"><div class="widget-container checklist" id="widget-1"><h3>My Checklist</h3>' + ("<p>Some list item placeholder</p>\n" * 150) + "</div></div>"
    assert len(large_canvas) > 3000
    assert len(large_canvas) < 50000

    payload_captured = {}

    # We mock the stream context manager of httpx.AsyncClient to inspect the payload sent to Prism
    class MockStreamContext:
        def __init__(self, *args, **kwargs):
            nonlocal payload_captured
            payload_captured.update(kwargs.get("json", {}))

        async def __aenter__(self):
            mock_response = AsyncMock()
            mock_response.status_code = 200
            
            # An async generator that returns a basic SSE event
            async def mock_aiter_text():
                yield 'data: {"type": "chunk", "content": "mocked response"}\n'
                yield 'data: {"type": "done"}\n'
            
            mock_response.aiter_text = mock_aiter_text
            return mock_response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient.stream", side_effect=MockStreamContext):
        response = client.post("/session/message", json={
            "session_id": "test_session_large_canvas",
            "message": "Modify my dashboard tasks",
            "current_canvas": large_canvas
        })
        
        assert response.status_code == 200

        # Now check that the large_canvas was passed in full without truncation to "..."
        messages = payload_captured.get("messages", [])
        assert len(messages) > 0
        
        # The first message is our [SYSTEM INSTRUCTIONS] block
        system_instruction_msg = messages[0]["content"]
        assert "CURRENT CANVAS STATE:" in system_instruction_msg
        assert "Widget ID: #widget-1" in system_instruction_msg
        assert "..." not in system_instruction_msg[-100:]  # Make sure we didn't suffix with truncation indicator
