import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Dict, Any

logger = logging.getLogger(__name__)

class MockDev2SDK:
    def __init__(self):
        self.runs = {}
        self.canceled = set()

    async def create_run(self, request: Dict[str, Any]) -> str:
        """Create a new run based on the v1 contract."""
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        self.runs[run_id] = request
        logger.info(f"Created mocked run {run_id} with request: {request}")
        return run_id

    async def observe(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream events for a given run."""
        if run_id not in self.runs:
            yield {"type": "error", "error": "Run not found"}
            return

        request = self.runs[run_id]
        topic = request.get("input", "unknown topic")

        yield {"type": "status", "status": "started", "run_id": run_id}
        await asyncio.sleep(0.1)
        
        if run_id in self.canceled:
            yield {"type": "status", "status": "canceled"}
            return

        # Mocking a tool call event (Canvas mutation)
        yield {
            "type": "tool_call",
            "tool": "canvas_add_widget",
            "args": {
                "widget_type": "data_card",
                "config": {"topic": topic, "content": "Mocked research content"}
            }
        }
        await asyncio.sleep(0.1)
        
        if run_id in self.canceled:
            yield {"type": "status", "status": "canceled"}
            return

        # Mocking the final result event
        yield {
            "type": "result",
            "content": f"Completed research for {topic} using the new SDK."
        }
        yield {"type": "status", "status": "completed"}

    async def cancel_run(self, run_id: str):
        """Explicitly cancel a run."""
        if run_id in self.runs:
            self.canceled.add(run_id)
            logger.info(f"Canceled mocked run {run_id}")

mock_sdk = MockDev2SDK()
