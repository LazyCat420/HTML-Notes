import logging
from typing import Any, Dict, Optional, Tuple
from app.tooling.html_notes_manifest import manifest_registry

logger = logging.getLogger(__name__)


class ToolPolicyViolation(ValueError):
    """Raised when a tool call violates admission policy or validation rules."""
    pass


class HTMLNotesToolPolicy:
    """
    Enforces authorization, effect constraints, and safety rules for tool calls.
    - Read: Read-only access, side-effect free.
    - Write: State mutations (notes CRUD, canvas DOM).
    - Destructive: High-risk mutations requiring explicit user confirmation.
    """

    def __init__(self, registry=manifest_registry):
        self.registry = registry

    def check_admission(self, tool_name: str) -> Tuple[bool, Optional[str]]:
        """Verifies if the tool is in the application profile whitelist."""
        profile = self.registry.get_profile()
        whitelist = set(profile.get("tool_policy", {}).get("whitelist", []))

        # Check direct match or legacy/canonical alias match
        tool_spec = self.registry.resolve_tool(tool_name)
        canonical_id = tool_spec.get("id") if tool_spec else None
        legacy_name = tool_spec.get("legacy_name") if tool_spec else None

        if tool_name in whitelist:
            return True, None
        if canonical_id and canonical_id in whitelist:
            return True, None
        if legacy_name and legacy_name in whitelist:
            return True, None

        return False, f"Tool '{tool_name}' is not permitted by profile '{profile.get('profile_id')}'"

    def requires_confirmation(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Determines if a tool call requires explicit user confirmation before execution."""
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec:
            return False

        # Destructive effect tools require confirmation
        if tool_spec.get("effect") == "destructive":
            return True

        # Special handling for portal app actions flagged destructive in spec
        if tool_spec.get("id") == "html_notes.portal.execute_action" or tool_name == "html_notes_app_action":
            app_id = args.get("app_id", "")
            action = args.get("action", "")
            try:
                from app.services.app_actions import get_action_spec
                spec = get_action_spec(app_id, action)
                if spec and spec.get("destructive"):
                    return True
            except ImportError:
                pass

        return bool(tool_spec.get("requires_confirmation", False))

    def validate_args(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Basic required parameters validation against tool manifest."""
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec:
            return True, None  # Allow pass-through for unknown tools to be rejected at admission

        params_schema = tool_spec.get("parameters", {})
        required = params_schema.get("required", [])
        for field in required:
            if field not in args:
                return False, f"Missing required parameter '{field}' for tool '{tool_name}'"
        return True, None

tool_policy = HTMLNotesToolPolicy()
