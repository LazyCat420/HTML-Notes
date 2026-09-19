import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

class RuntimeChatAdapter:
    """
    Translates canonical agent runtime RunEvents into HTML-Notes SSE presentation frames.
    Preserves local canvas DOM mutations, intent selection, and session-specific context
    while offloading global agent execution, lifecycle, and receipts to the shared runtime.
    """

    def __init__(self, runtime_client: Optional[Any] = None):
        self.runtime_client = runtime_client

    async def stream_chat_turn(
        self,
        query: str,
        session_id: str,
        canvas_html: str,
        execute_mutation_cb: Optional[Callable[[str, Dict[str, Any]], AsyncGenerator[str, None]]] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Coordinates a single chat turn through the shared runtime and yields SSE-ready dicts.
        """
        profile_id = "html_notes_canvas_v1"
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        
        # 1. Yield admission status
        yield {
            "type": "status",
            "message": "connecting to global agent runtime...",
            "phase": "routing",
            "run_id": run_id
        }

        # 2. Stream events from runtime client if provided, else fallback to mock/local simulation
        if self.runtime_client and hasattr(self.runtime_client, "stream_run"):
            event_stream = self.runtime_client.stream_run(
                profile_id=profile_id,
                input=query,
                context={"session_id": session_id, "canvas_html": canvas_html}
            )
        else:
            event_stream = self._mock_runtime_stream(run_id, query)

        async for event in event_stream:
            if cancel_event and cancel_event.is_set():
                logger.info(f"Cancellation received for run {run_id}")
                if self.runtime_client and hasattr(self.runtime_client, "cancel_run"):
                    await self.runtime_client.cancel_run(run_id)
                yield {"type": "status", "message": "agent execution cancelled", "phase": "cancelled"}
                break

            event_type = event.get("type")
            data = event.get("data", {})

            if event_type == "run.started":
                yield {
                    "type": "status",
                    "message": "research agent started",
                    "phase": "running"
                }

            elif event_type == "message.delta":
                delta_text = data.get("delta") or data.get("content") or ""
                if delta_text:
                    yield {
                        "type": "chunk",
                        "content": delta_text
                    }

            elif event_type in ("tool.invoked", "tool_call"):
                tool_name = data.get("tool_name") or data.get("tool") or event.get("tool") or ""
                tool_args = data.get("arguments") or data.get("args") or event.get("args") or {}

                yield {
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": tool_args
                }
                yield {
                    "type": "status",
                    "message": f"executing {tool_name}...",
                    "phase": "tool"
                }

                # If the tool is a local canvas mutation, delegate to the local mutation handler
                if execute_mutation_cb and tool_name in (
                    "canvas_add_widget",
                    "canvas_modify_dom",
                    "create_widget",
                    "update_widget",
                    "mcp__lazy-tool-service__canvas_add_widget",
                    "mcp__lazy-tool-service__canvas_modify_dom"
                ):
                    async for mutation_sse_frame in execute_mutation_cb(tool_name, tool_args):
                        yield {"type": "raw_sse", "frame": mutation_sse_frame}

            elif event_type == "worker.dispatched":
                stage = data.get("stage", "stage")
                yield {
                    "type": "status",
                    "message": f"background worker active: {stage}",
                    "phase": "worker"
                }

            elif event_type == "run.completed":
                receipt = data.get("context_receipt") or {}
                evidence = data.get("evidence_records") or []
                yield {
                    "type": "receipt",
                    "receipt": receipt,
                    "evidence": evidence
                }
                yield {
                    "type": "status",
                    "message": "agent turn completed",
                    "phase": "completed"
                }

            elif event_type == "run.failed":
                err = data.get("error", {})
                yield {
                    "type": "error",
                    "message": err.get("message", "Run failed"),
                    "code": err.get("code", "RUN_ERROR")
                }

        yield {"type": "done"}

    async def _mock_runtime_stream(self, run_id: str, query: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Local event generator implementing canonical v1 events for testing and fallback."""
        yield {
            "id": "evt_0",
            "run_id": run_id,
            "type": "run.started",
            "timestamp": "2026-09-19T12:00:00Z",
            "data": {"profile_id": "html_notes_canvas_v1"}
        }
        await asyncio.sleep(0.01)

        # Emit preliminary delta
        yield {
            "id": "evt_1",
            "run_id": run_id,
            "type": "message.delta",
            "timestamp": "2026-09-19T12:00:01Z",
            "data": {"delta": f"Synthesizing notes for: {query}..."}
        }
        await asyncio.sleep(0.01)

        # Emit tool call for canvas widget
        yield {
            "id": "evt_2",
            "run_id": run_id,
            "type": "tool.invoked",
            "timestamp": "2026-09-19T12:00:02Z",
            "data": {
                "tool_name": "canvas_add_widget",
                "arguments": {
                    "widget_type": "data_card",
                    "widget_id": "card_res_01",
                    "config": {"title": "Research Result", "query": query}
                }
            }
        }
        await asyncio.sleep(0.01)

        # Emit run completed with receipt
        yield {
            "id": "evt_3",
            "run_id": run_id,
            "type": "run.completed",
            "timestamp": "2026-09-19T12:00:03Z",
            "data": {
                "context_receipt": {"status": "verified"},
                "evidence_records": [{"id": "ev_01", "source": "notes_db"}],
                "usage": {"total_tokens": 142, "duration_ms": 120}
            }
        }

runtime_chat_adapter = RuntimeChatAdapter()
