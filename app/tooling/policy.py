import re
import logging
from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup
from app.tooling.html_notes_manifest import manifest_registry

logger = logging.getLogger(__name__)


class ToolPolicyViolation(ValueError):
    """Raised when a tool call violates admission policy or validation rules."""
    pass


class HTMLNotesToolPolicy:
    """
    Enforces authorization, effect constraints, scope validation, and safety rules.
    - Read: Read-only access, side-effect free.
    - Write: State mutations (notes CRUD, canvas DOM).
    - Destructive: High-risk mutations requiring explicit user confirmation.
    """

    def __init__(self, registry=manifest_registry):
        self.registry = registry
        self._allow_arbitrary_dom_mutation = False

    def check_admission(self, tool_name: str) -> Tuple[bool, Optional[str]]:
        """Verifies if the tool is in the application profile whitelist and not retired."""
        # 1. Check if alias has passed retirement date
        if self.registry.is_retired(tool_name):
            return False, f"Tool alias '{tool_name}' has been retired and is no longer available"

        profile = self.registry.get_profile()
        whitelist = set(profile.get("tool_policy", {}).get("whitelist", []))

        # Check direct match or legacy/canonical alias match
        tool_spec = self.registry.resolve_tool(tool_name)
        canonical_id = tool_spec.get("id") if tool_spec else None
        legacy_aliases = tool_spec.get("legacy_aliases", []) if tool_spec else []
        legacy_name = tool_spec.get("legacy_name") if tool_spec else None

        if tool_name in whitelist:
            return True, None
        if canonical_id and canonical_id in whitelist:
            return True, None
        if legacy_name and legacy_name in whitelist:
            return True, None
        if any(a in whitelist for a in legacy_aliases):
            return True, None

        return False, f"Tool '{tool_name}' is not permitted by profile '{profile.get('profile_id')}'"

    def validate_scope(
        self,
        tool_name: str,
        session_id: Optional[str] = None,
        app_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Validates that execution context satisfies the tool's required scopes."""
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec:
            return False, f"Tool '{tool_name}' missing manifest specification"

        effect = tool_spec.get("effect", "write")
        resource_type = tool_spec.get("resource_type")
        raw_required = tool_spec.get("required_scope")

        # Missing required_scope specification fails closed
        if not raw_required:
            return False, f"Tool '{tool_name}' lacks required_scope specification"

        if isinstance(raw_required, list):
            req_set = set(raw_required)
        elif isinstance(raw_required, dict):
            req_set = {k for k, v in raw_required.items() if bool(v)}
        else:
            req_set = set()

        # Invariants: write and destructive tools strictly require app_id and session_id
        if effect in ("write", "destructive"):
            req_set.add("app_id")
            req_set.add("session_id")
        elif effect == "read" and ("session" in str(resource_type).lower() or "session_id" in req_set):
            req_set.add("app_id")
            req_set.add("session_id")
        else:
            req_set.add("app_id")

        # Validate app_id
        if "app_id" in req_set:
            if not app_id:
                return False, f"Tool '{tool_name}' requires app_id"
            if app_id.replace("_", "-") != "html-notes":
                return False, f"Tool '{tool_name}' requires app_id 'html-notes', got '{app_id}'"

        # Validate session_id
        if "session_id" in req_set:
            if not session_id or not str(session_id).strip():
                return False, f"Tool '{tool_name}' requires session_id scope"

        return True, None

    def requires_authorization_receipt(self, tool_name: str) -> bool:
        """Determines if a tool call requires a valid runtime authorization receipt."""
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec:
            return True
        if "requires_authorization_receipt" in tool_spec:
            return bool(tool_spec.get("requires_authorization_receipt"))
        return tool_spec.get("effect") in ("write", "destructive")

    def requires_confirmation(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Determines if a tool call requires explicit user confirmation before execution."""
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec:
            return False

        # Destructive effect tools strictly require confirmation
        if tool_spec.get("effect") == "destructive":
            return True

        if bool(tool_spec.get("requires_confirmation", False)):
            return True

        # Special handling for portal app actions flagged destructive in spec
        canonical_id = tool_spec.get("id")
        if canonical_id in ("html_notes.apps.execute_action", "html_notes.portal.execute_action") or tool_name in ("html_notes_app_action", "execute_action"):
            app_id = args.get("app_id", "")
            action = args.get("action", "")
            try:
                from app.services.app_actions import get_action_spec
                spec = get_action_spec(app_id, action)
                if spec and spec.get("destructive"):
                    return True
            except ImportError:
                pass

        return False

    def validate_args(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validates parameters against tool manifest schema."""
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec:
            return True, None

        schema = tool_spec.get("input_schema") or tool_spec.get("parameters", {})
        required = schema.get("required", [])
        for field in required:
            if field not in args or args[field] is None:
                return False, f"Missing required parameter '{field}' for tool '{tool_name}'"

        # Check types for present properties
        properties = schema.get("properties", {})
        for prop, pdef in properties.items():
            if prop in args and args[prop] is not None:
                val = args[prop]
                expected_type = pdef.get("type")
                if expected_type == "string" and not isinstance(val, str):
                    return False, f"Parameter '{prop}' must be a string for tool '{tool_name}'"
                elif expected_type == "object" and not isinstance(val, dict):
                    return False, f"Parameter '{prop}' must be an object for tool '{tool_name}'"
                elif expected_type == "array" and not isinstance(val, list):
                    return False, f"Parameter '{prop}' must be a list for tool '{tool_name}'"

        return True, None

    def validate_safety(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Checks for untrusted script injection or unconstrained DOM operations."""
        tool_spec = self.registry.resolve_tool(tool_name)
        canonical_id = tool_spec.get("id") if tool_spec else tool_name

        # Reject arbitrary DOM mutation if not permitted
        if canonical_id in ("html_notes.canvas.mutate", "html_notes.canvas.modify_dom") or tool_name == "canvas_modify_dom":
            if not self._allow_arbitrary_dom_mutation:
                # Constrained to safe selectors (#id) and safe actions only
                selector = args.get("selector", "")
                if not selector.startswith("#"):
                    return False, "Arbitrary DOM mutation is not available by default; selector must be an exact #id"

        # Check for untrusted script tags or inline handlers in HTML content
        html_content = args.get("rendered_html") or args.get("html") or args.get("htmlContent") or ""
        if isinstance(html_content, str) and html_content:
            lower = html_content.lower()
            if "<script" in lower or "</script>" in lower or "javascript:" in lower:
                return False, "Untrusted JavaScript: <script> tags or javascript: URLs are forbidden"
            if re.search(r'\bon[a-z]+\s*=', lower):
                return False, "Untrusted JavaScript: inline event handlers are forbidden"

        return True, None

    def sanitize_html(self, html_content: str) -> str:
        """Sanitizes HTML content by stripping script tags and inline handlers."""
        if not html_content:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup.find_all(["script", "style", "iframe", "object", "embed"]):
            tag.decompose()
        for el in soup.find_all(True):
            attrs_to_remove = [attr for attr in el.attrs if attr.lower().startswith("on") or "javascript:" in str(el.attrs[attr]).lower()]
            for attr in attrs_to_remove:
                del el[attr]
        return str(soup)


tool_policy = HTMLNotesToolPolicy()
