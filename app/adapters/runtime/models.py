"""
Domain models, execution context, and scope verification helpers for local tool execution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from app.tooling.html_notes_manifest import manifest_registry

logger = logging.getLogger(__name__)

EXPECTED_APP_ID = "html-notes"


@dataclass
class LocalExecutionContext:
    """Execution context provided to local tool execution bridge."""
    session_id: str
    canvas_html: str = ""
    query: str = ""
    focus_widget_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def verify_local_tool_scope(
    required_scope: Optional[Dict[str, Any]],
    context: LocalExecutionContext,
) -> Tuple[bool, Optional[str]]:
    """
    Verifies that an admitted local tool call satisfies application and session scope constraints.
    Prevents cross-session and cross-application tool invocation.
    """
    if not required_scope:
        return True, None

    # Check app_id scope
    app_id = required_scope.get("app_id")
    if app_id and app_id != EXPECTED_APP_ID:
        err = f"Scope violation: tool requires app_id='{app_id}' but active app is '{EXPECTED_APP_ID}'"
        logger.warning(f"[SCOPE REJECTED] {err}")
        return False, err

    # Check session_id scope
    target_session = required_scope.get("session_id")
    if target_session and target_session != context.session_id:
        err = (
            f"Scope violation: tool requires session_id='{target_session}' "
            f"but active session is '{context.session_id}'"
        )
        logger.warning(f"[SCOPE REJECTED] {err}")
        return False, err

    return True, None


def resolve_canonical_tool(tool_name: str) -> Tuple[str, bool]:
    """
    Normalizes a tool name using the application manifest registry:
    - Resolves legacy aliases (e.g. canvas_add_widget -> html_notes.canvas.upsert_widget)
    - Emits deprecation warnings for legacy names
    - Returns (canonical_tool_id, is_local)
    """
    tool_spec = manifest_registry.resolve_tool(tool_name)
    if not tool_spec and tool_name.startswith("mcp__"):
        clean_name = tool_name.split("__")[-1]
        tool_spec = manifest_registry.resolve_tool(clean_name)

    if tool_spec:
        canonical_id = tool_spec.get("id", tool_name)
        is_deprecated = bool(tool_spec.get("deprecated") or (tool_name != canonical_id))
        if is_deprecated:
            logger.warning(
                f"[DEPRECATION] Tool alias '{tool_name}' resolved to canonical '{canonical_id}'. "
                f"Please update callers to use the canonical ID."
            )
        is_local = (tool_spec.get("execution") == "local") or canonical_id.startswith("html_notes.")
        return canonical_id, is_local

    # If not found in manifest, determine by prefix
    is_local = (
        tool_name.startswith("html_notes.")
        or tool_name.startswith("canvas_")
        or "canvas_" in tool_name
    )
    return tool_name, is_local
