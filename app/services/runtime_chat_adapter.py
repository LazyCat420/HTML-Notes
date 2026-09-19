from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Set

import httpx
from lazycat.client import RuntimeClient, RuntimeClientError
from lazycat.models import CreateRunRequest, RunEvent

from app.adapters.runtime.config import (
    HTML_NOTES_CONTRACT_VERSION,
    HTML_NOTES_RUNTIME_PROFILE,
    LAZYCAT_RUNTIME_URL,
    RUNTIME_CONNECT_TIMEOUT_SECONDS,
    RUNTIME_READ_TIMEOUT_SECONDS,
    RUNTIME_MAX_CANVAS_CONTEXT_CHARS,
    is_contract_compatible,
)
from app.adapters.runtime.models import (
    EXPECTED_APP_ID,
    LocalExecutionContext,
    resolve_canonical_tool,
    verify_local_tool_scope,
)
from app.presentation.sse.formatter import sse_formatter

logger = logging.getLogger(__name__)

# Known local tool set that HTML-Notes executes locally
LOCAL_TOOLS: Set[str] = {
    "canvas_add_widget",
    "canvas_modify_dom",
    "create_widget",
    "update_widget",
    "plan_widget",
    "list_widget_types",
    "mcp__lazy-tool-service__canvas_add_widget",
    "mcp__lazy-tool-service__canvas_modify_dom",
    "mcp__lazy-tool-service__create_widget",
    "mcp__lazy-tool-service__update_widget",
    "html_notes.canvas.upsert_widget",
    "html_notes.canvas.remove_widget",
    "html_notes.canvas.modify_dom",
    "html_notes.canvas.mutate",
    "html_notes.canvas.read",
    "html_notes.notes.create",
    "html_notes.notes.get",
    "html_notes.notes.update",
    "html_notes.notes.search",
    "html_notes.notes.link",
    "html_notes.portal.open_app",
    "html_notes.portal.list_services",
    "html_notes.portal.list_actions",
    "html_notes.portal.execute_action",
    "html_notes.portal.curate_app",
}


def create_bounded_canvas_context(canvas_html: Optional[str], max_chars: int = RUNTIME_MAX_CANVAS_CONTEXT_CHARS) -> str:
    """Returns a bounded representation of canvas HTML for prompt context."""
    if not canvas_html:
        return ""
    if len(canvas_html) <= max_chars:
        return canvas_html
    return canvas_html[:max_chars] + "... [canvas truncated]"


class RuntimeChatAdapter:
    """
    Translates canonical agent runtime RunEvents into HTML-Notes SSE presentation frames.
    Preserves local canvas DOM mutations, intent selection, and session-specific context
    while offloading global agent execution, lifecycle, and receipts to the shared runtime.
    """

    def __init__(
        self,
        runtime_client: Optional[Any] = None,
        default_profile_id: Optional[str] = None,
        runtime_url: Optional[str] = None,
        connect_timeout: Optional[float] = None,
        read_timeout: Optional[float] = None,
    ):
        self.runtime_client = runtime_client
        self.default_profile_id = (
            default_profile_id
            or os.getenv("HTML_NOTES_RUNTIME_PROFILE")
            or HTML_NOTES_RUNTIME_PROFILE
        )
        self.runtime_url = runtime_url or os.getenv("LAZYCAT_RUNTIME_URL") or os.getenv("LAZY_AGENT_URL") or LAZYCAT_RUNTIME_URL
        self.connect_timeout = (
            connect_timeout
            if connect_timeout is not None
            else float(os.getenv("RUNTIME_CONNECT_TIMEOUT_SECONDS", str(RUNTIME_CONNECT_TIMEOUT_SECONDS)))
        )
        self.read_timeout = (
            read_timeout
            if read_timeout is not None
            else float(os.getenv("RUNTIME_READ_TIMEOUT_SECONDS", str(RUNTIME_READ_TIMEOUT_SECONDS)))
        )

    def _get_client(self) -> Any:
        if self.runtime_client is not None:
            return self.runtime_client
        timeout = httpx.Timeout(timeout=self.read_timeout, connect=self.connect_timeout)
        client = RuntimeClient(
            base_url=self.runtime_url,
            timeout=timeout,
            project="html-notes",
            username="lazycat",
        )
        client.connect_timeout = self.connect_timeout
        client.read_timeout = self.read_timeout
        return client

    async def stream_chat_turn(
        self,
        query: str,
        session_id: str,
        canvas_html: str = "",
        execute_local_tool_cb: Optional[Callable[[str, Dict[str, Any], Dict[str, Any], LocalExecutionContext], AsyncGenerator[str, None]]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        profile_id: Optional[str] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        runtime_overrides: Optional[Dict[str, Any]] = None,
        execute_mutation_cb: Optional[Callable[[str, Dict[str, Any]], AsyncGenerator[str, None]]] = None,
        request_context: Optional[LocalExecutionContext] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Coordinates a single chat turn through the shared runtime and yields SSE-ready dicts.
        """
        active_profile = profile_id or self.default_profile_id
        client = self._get_client()

        # Build or verify LocalExecutionContext
        if request_context is None:
            request_context = LocalExecutionContext(
                session_id=session_id,
                canvas_html=canvas_html,
                query=query,
            )

        # Preflight handshake check if client reports contract version
        reported_contract = getattr(client, "contract_version", None)
        if reported_contract and not is_contract_compatible(HTML_NOTES_CONTRACT_VERSION, reported_contract):
            err_msg = f"Incompatible contract version: required {HTML_NOTES_CONTRACT_VERSION}, runtime reports {reported_contract}"
            logger.error(f"[RUNTIME ADAPTER] {err_msg}")
            yield {"type": "error", "message": err_msg, "code": "INCOMPATIBLE_CONTRACT_VERSION"}
            yield {"type": "status", "message": "agent runtime version mismatch", "phase": "error"}
            yield {"type": "done"}
            return

        # Bounded context envelope
        bounded_canvas = create_bounded_canvas_context(canvas_html, RUNTIME_MAX_CANVAS_CONTEXT_CHARS)
        context_payload: Dict[str, Any] = {
            "session_id": session_id,
            "canvas_context": bounded_canvas,
        }
        if extra_context:
            context_payload.update(extra_context)

        # 1. Yield initial admission status frame
        yield {
            "type": "status",
            "message": "connecting to global agent runtime...",
            "phase": "routing",
        }

        from app.tooling.html_notes_manifest import manifest_registry
        local_tools = [{"name": t["id"], "description": t.get("description", ""),
                        "parameters": t.get("parameters", t.get("input_schema", {}))}
                       for t in manifest_registry.get_domain_tools_manifest()["tools"]]
        # Schemas describe app-owned tools; admission remains runtime profile controlled.
        # Build run request
        run_request = CreateRunRequest(
            profile_id=active_profile,
            input=query,
            stream=True,
            runtime_overrides={
                "context": context_payload,
                "local_tool_schemas": local_tools,
                **(runtime_overrides or {}),
            },
        )

        active_run_id: Optional[str] = None

        try:
            # Stream events from runtime client
            if hasattr(client, "stream_run"):
                try:
                    event_stream = client.stream_run(run_request)
                except TypeError:
                    # Compatibility with positional/kwarg mock clients in unit tests
                    event_stream = client.stream_run(
                        profile_id=active_profile,
                        input=query,
                        context=context_payload,
                    )
            else:
                raise RuntimeClientError("Configured runtime client does not implement stream_run")

            async for event in event_stream:
                # Normalize event properties across typed models and plain dicts
                event_type = getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else "")
                data = getattr(event, "data", None)
                if data is None and isinstance(event, dict):
                    data = event.get("data", {})
                data = data or {}

                # Capture active run_id immediately from the incoming event
                if not active_run_id:
                    active_run_id = getattr(event, "run_id", None) or (
                        event.get("run_id") if isinstance(event, dict) else None
                    ) or data.get("run_id")

                # Structured log with active run_id
                if active_run_id:
                    logger.debug(f"[RUNTIME ADAPTER] run_id={active_run_id} event_type={event_type}")

                # Check for cancellation before processing each event
                if cancel_event and cancel_event.is_set():
                    logger.info(f"[RUNTIME ADAPTER] Cancellation requested for run_id={active_run_id}")
                    if active_run_id and hasattr(client, "cancel_run"):
                        try:
                            await client.cancel_run(active_run_id)
                        except Exception as ce:
                            logger.warning(f"Error cancelling run {active_run_id}: {ce}")
                    yield {
                        "type": "status",
                        "message": "agent execution cancelled",
                        "phase": "cancelled",
                        "run_id": active_run_id,
                    }
                    yield {"type": "done"}
                    return

                # Handle canonical event types
                if event_type in ("run.admitted", "run.created"):
                    yield {
                        "type": "status",
                        "message": "connecting to global agent runtime...",
                        "phase": "admitted",
                        "run_id": active_run_id,
                    }

                elif event_type == "run.started":
                    yield {
                        "type": "status",
                        "message": "research agent started",
                        "phase": "running",
                        "run_id": active_run_id,
                    }

                elif event_type == "message.delta":
                    delta_text = data.get("delta") or data.get("content") or ""
                    if delta_text:
                        yield {
                            "type": "chunk",
                            "content": delta_text,
                        }

                elif event_type in ("tool.invoked", "tool.called", "tool_call"):
                    tool_name = (
                        data.get("tool_name")
                        or data.get("tool")
                        or (event.get("tool") if isinstance(event, dict) else "")
                        or ""
                    )
                    tool_args = (
                        data.get("arguments")
                        or data.get("args")
                        or (event.get("args") if isinstance(event, dict) else {})
                        or {}
                    )
                    tool_call_id = data.get("tool_call_id") or data.get("id") or ""
                    execution_loc = data.get("execution")
                    required_scope = data.get("required_scope")
                    auth_receipt = data.get("authorization_receipt") or {}
                    if isinstance(auth_receipt, dict):
                        auth_receipt = dict(auth_receipt)
                        if "run_id" not in auth_receipt and active_run_id:
                            auth_receipt["run_id"] = active_run_id
                        if "tool_call_id" not in auth_receipt and tool_call_id:
                            auth_receipt["tool_call_id"] = tool_call_id
                        if "profile_id" not in auth_receipt and active_profile:
                            auth_receipt["profile_id"] = active_profile
                        if "session_id" not in auth_receipt and session_id:
                            auth_receipt["session_id"] = session_id
                        if "app_id" not in auth_receipt:
                            auth_receipt["app_id"] = EXPECTED_APP_ID

                    logger.info(
                        f"[RUNTIME ADAPTER] tool.invoked: name={tool_name} run_id={active_run_id} execution={execution_loc}"
                    )

                    yield {
                        "type": "tool_call",
                        "tool": tool_name,
                        "args": tool_args,
                        "call_id": tool_call_id,
                    }
                    yield {
                        "type": "status",
                        "message": f"executing {tool_name}...",
                        "phase": "tool",
                    }

                    # Determine if tool is local vs shared
                    canonical_name, is_manifest_local = resolve_canonical_tool(tool_name)
                    is_local = (execution_loc == "local") or is_manifest_local or (tool_name in LOCAL_TOOLS)

                    if is_local:
                        # Validate scope
                        scope_valid, scope_err = verify_local_tool_scope(
                            required_scope,
                            request_context,
                            tool_name=canonical_name or tool_name,
                        )
                        if not scope_valid:
                            err_msg = scope_err or "Scope validation failed"
                            yield {
                                "type": "error",
                                "message": err_msg,
                                "code": "LOCAL_SCOPE_VIOLATION",
                            }
                            yield {
                                "type": "status",
                                "message": f"tool {tool_name} rejected: {err_msg}",
                                "phase": "tool_failed",
                            }
                            continue

                        # Execute locally via bridge callback
                        if execute_local_tool_cb:
                            rt_ctx = {
                                "run_id": active_run_id,
                                "tool_call_id": tool_call_id,
                                "profile_id": active_profile,
                                "contract_version": HTML_NOTES_CONTRACT_VERSION,
                            }
                            try:
                                cb_stream = execute_local_tool_cb(
                                    canonical_name, tool_args, auth_receipt, request_context, rt_ctx
                                )
                            except TypeError:
                                cb_stream = execute_local_tool_cb(
                                    canonical_name, tool_args, auth_receipt, request_context
                                )
                            async for frame_str in cb_stream:
                                yield {"type": "raw_sse", "frame": frame_str}
                        elif execute_mutation_cb and tool_name in LOCAL_TOOLS:
                            async for mutation_sse_frame in execute_mutation_cb(tool_name, tool_args):
                                yield {"type": "raw_sse", "frame": mutation_sse_frame}
                    else:
                        logger.info(
                            f"[RUNTIME ADAPTER] Tool '{tool_name}' executed in shared runtime; "
                            f"local executor will not be invoked."
                        )

                elif event_type in ("tool.completed", "tool.result"):
                    tool_name = data.get("tool_name") or data.get("tool") or ""
                    yield {
                        "type": "status",
                        "message": f"tool {tool_name} completed",
                        "phase": "tool_completed",
                    }

                elif event_type == "tool.failed":
                    tool_name = data.get("tool_name") or ""
                    err = data.get("error", {})
                    err_code = err.get("code", "TOOL_FAILED")
                    err_msg = err.get("message", f"Tool {tool_name} failed")

                    logger.warning(f"[RUNTIME ADAPTER] Tool failed: {tool_name} code={err_code} err={err_msg}")
                    yield {
                        "type": "status",
                        "message": f"tool {tool_name} failed: {err_msg}",
                        "phase": "tool_failed",
                        "error": err,
                    }
                    if err_code in ("TOOL_PERMISSION_DENIED", "POLICY_DENIED"):
                        yield {
                            "type": "error",
                            "message": err_msg,
                            "code": err_code,
                        }

                elif event_type == "worker.dispatched":
                    task = (
                        data.get("task")
                        or data.get("stage")
                        or data.get("plugin_name")
                        or "worker"
                    )
                    yield {
                        "type": "status",
                        "message": f"background worker active: {task}",
                        "phase": "worker",
                    }

                elif event_type == "worker.completed":
                    task = data.get("task") or data.get("stage") or "worker"
                    yield {
                        "type": "status",
                        "message": f"background worker completed: {task}",
                        "phase": "worker_completed",
                    }

                elif event_type == "run.cancelled":
                    yield {
                        "type": "status",
                        "message": "agent execution cancelled",
                        "phase": "cancelled",
                        "run_id": active_run_id,
                    }

                elif event_type == "run.failed":
                    err = data.get("error", {})
                    err_msg = err.get("message", "Run failed")
                    err_code = err.get("code", "RUN_ERROR")
                    yield {
                        "type": "error",
                        "message": err_msg,
                        "code": err_code,
                    }
                    yield {
                        "type": "status",
                        "message": f"agent run failed: {err_msg}",
                        "phase": "failed",
                        "run_id": active_run_id,
                    }

                elif event_type == "run.completed":
                    receipt = data.get("context_receipt") or {}
                    evidence = data.get("evidence_records") or []
                    usage = data.get("usage") or {}
                    if receipt or evidence:
                        yield {
                            "type": "receipt",
                            "receipt": receipt,
                            "evidence": evidence,
                            "usage": usage,
                        }
                    yield {
                        "type": "status",
                        "message": "agent turn completed",
                        "phase": "completed",
                        "run_id": active_run_id,
                    }

        except Exception as exc:
            logger.error(f"[RUNTIME ADAPTER] Error in agent runtime stream: {exc}")
            yield {
                "type": "error",
                "message": f"Shared agent runtime unavailable: {exc}",
                "code": getattr(exc, "code", "RUNTIME_UNAVAILABLE"),
            }
            yield {
                "type": "status",
                "message": "agent runtime unavailable",
                "phase": "error",
            }

        yield {"type": "done"}


runtime_chat_adapter = RuntimeChatAdapter()


async def ensure_terminal_sse(source):
    """Give a connected shared-runtime HTTP stream one terminal frame, even on persistence errors."""
    try:
        async for frame in source:
            # Each server frame contains a single JSON data event.
            if frame.startswith("data: "):
                try:
                    if json.loads(frame[6:].strip()).get("type") == "done":
                        continue
                except (ValueError, AttributeError):
                    pass
            yield frame
    except Exception:
        logger.exception("Shared runtime response stream failed")
        yield sse_formatter.error_frame("Shared runtime response failed", "RUNTIME_STREAM_FAILED")
    yield sse_formatter.done_frame()
