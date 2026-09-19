import json
from typing import Any, AsyncGenerator, Dict, Optional
from bs4 import BeautifulSoup
from app.canvas_manager import commit_canvas


class SSEFormatter:
    """
    Standardizes Server-Sent Events (SSE) wire serialization for the browser client.
    Handles component frames, status frames, error frames, and local tool execution results.
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
    def tool_call_frame(tool: str, args: Dict[str, Any], call_id: str = "") -> str:
        d: Dict[str, Any] = {"type": "tool_call", "tool": tool, "args": args}
        if call_id:
            d["call_id"] = call_id
        return SSEFormatter.format_event(d)

    @staticmethod
    def canvas_diff_frame(widget_id: str, html: str, action: str = "upsert") -> str:
        return SSEFormatter.format_event({
            "type": "canvas_diff",
            "widget_id": widget_id,
            "action": action,
            "html": html,
        })

    @staticmethod
    def receipt_frame(receipt: Dict[str, Any], evidence: list, usage: Optional[Dict[str, Any]] = None) -> str:
        d = {
            "type": "receipt",
            "receipt": receipt,
            "evidence": evidence,
        }
        if usage:
            d["usage"] = usage
        return SSEFormatter.format_event(d)

    @staticmethod
    def done_frame() -> str:
        return SSEFormatter.format_event({"type": "done"})

    @staticmethod
    def error_frame(message: str, code: str = "ERROR") -> str:
        return SSEFormatter.format_event({"type": "error", "message": message, "code": code})

    async def from_local_result(
        self,
        result: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Translates a LocalToolExecutor execution result into formatted SSE frames.
        Commits canvas markup mutations to the live session canvas atomically.
        """
        tool_name = result.get("tool", "local_tool")

        # 1. Handle error cases
        if not result.get("success") or result.get("is_error"):
            err_msg = str(result.get("error") or "Tool execution failed")
            yield self.error_frame(message=err_msg, code="LOCAL_TOOL_ERROR")
            yield self.status_frame(message=f"tool {tool_name} failed: {err_msg}", phase="tool_failed")
            return

        # 2. Handle user confirmation requirements
        if result.get("confirmation_required"):
            yield self.status_frame(message="confirmation required for action", phase="confirmation")

        payload = result.get("result", {})

        # 3. Canvas Widget Synthesis & Upsert
        if isinstance(payload, dict) and "html" in payload and payload.get("html"):
            widget_html = payload["html"]
            widget_id = payload.get("widget_id", "")

            if session_id:
                def _mutate_upsert(soup):
                    existing = soup.find(id=widget_id) if widget_id else None
                    if existing:
                        existing.replace_with(BeautifulSoup(widget_html, "html.parser"))
                    else:
                        grid = soup.find(id="dashboard-grid") or soup
                        grid.insert(0, BeautifulSoup(widget_html, "html.parser"))

                component_frame = await commit_canvas(session_id, _mutate_upsert)
                if component_frame:
                    yield component_frame
                else:
                    yield self.canvas_diff_frame(widget_id=widget_id, html=widget_html, action="upsert")
            else:
                yield self.canvas_diff_frame(widget_id=widget_id, html=widget_html, action="upsert")

        # 4. Canvas DOM Mutation (modify_dom)
        elif isinstance(payload, dict) and "canvas_html" in payload and payload.get("canvas_html"):
            canvas_html = payload["canvas_html"]
            if session_id:
                def _mutate_replace(soup):
                    soup.clear()
                    soup.append(BeautifulSoup(canvas_html, "html.parser"))

                component_frame = await commit_canvas(session_id, _mutate_replace)
                if component_frame:
                    yield component_frame
                else:
                    yield self.canvas_diff_frame(widget_id="canvas-root", html=canvas_html, action="replace")
            else:
                yield self.canvas_diff_frame(widget_id="canvas-root", html=canvas_html, action="replace")

        # 5. Apps Hub Open URL
        elif isinstance(payload, dict) and payload.get("launch_url"):
            yield self.format_event({
                "type": "open_url",
                "url": payload["launch_url"],
                "name": payload.get("app", {}).get("name") or payload.get("name", ""),
            })

        # 6. Notes Created/Updated
        elif isinstance(payload, dict) and "title" in payload and "id" in payload:
            note_title = payload.get("title", "")
            yield self.status_frame(message=f"Note '{note_title}' saved", phase="tool_completed")

        # Generic completion status
        yield self.status_frame(message=f"tool {tool_name} completed", phase="tool_completed")


sse_formatter = SSEFormatter()
