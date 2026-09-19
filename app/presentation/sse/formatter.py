import json
from typing import Any, Dict


class SSEFormatter:
    """
    Standardizes Server-Sent Events (SSE) wire serialization for the browser client.
    """

    @staticmethod
    def format_event(data: Dict[str, Any], event_name: str = "") -> str:
        payload = json.dumps(data)
        if event_name:
            return f"event: {event_name}\ndata: {payload}\n\n"
        return f"data: {payload}\n\n"

    @staticmethod
    def status_frame(message: str, phase: str = "running", run_id: str = "") -> str:
        d = {"type": "status", "message": message, "phase": phase}
        if run_id:
            d["run_id"] = run_id
        return SSEFormatter.format_event(d)

    @staticmethod
    def chunk_frame(content: str) -> str:
        return SSEFormatter.format_event({"type": "chunk", "content": content})

    @staticmethod
    def tool_call_frame(tool: str, args: Dict[str, Any]) -> str:
        return SSEFormatter.format_event({"type": "tool_call", "tool": tool, "args": args})

    @staticmethod
    def canvas_diff_frame(widget_id: str, html: str, action: str = "upsert") -> str:
        return SSEFormatter.format_event({
            "type": "canvas_diff",
            "widget_id": widget_id,
            "action": action,
            "html": html
        })

    @staticmethod
    def receipt_frame(receipt: Dict[str, Any], evidence: list) -> str:
        return SSEFormatter.format_event({
            "type": "receipt",
            "receipt": receipt,
            "evidence": evidence
        })

    @staticmethod
    def done_frame() -> str:
        return SSEFormatter.format_event({"type": "done"})

    @staticmethod
    def error_frame(message: str, code: str = "ERROR") -> str:
        return SSEFormatter.format_event({"type": "error", "message": message, "code": code})

sse_formatter = SSEFormatter()
