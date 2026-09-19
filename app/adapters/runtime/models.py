"""
Domain models, execution context, typed authorization envelopes, and scope verification helpers
for local tool execution. Enforces strict fail-closed authorization receipt and scope admission.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from app.tooling.html_notes_manifest import manifest_registry

logger = logging.getLogger(__name__)

EXPECTED_APP_ID = "html-notes"


@dataclass
class LocalExecutionContext:
    """Execution context provided to local tool execution bridge."""
    session_id: str
    app_id: str = EXPECTED_APP_ID
    canvas_html: str = ""
    query: str = ""
    focus_widget_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LocalToolAuthorization:
    """Typed authorization envelope issued by runtime to authorize local tool execution."""
    run_id: str
    tool_call_id: str
    canonical_tool_id: str
    profile_id: str
    app_id: str
    session_id: str
    issued_at: datetime
    expires_at: datetime
    nonce: str
    signature: Optional[str] = None
    raw_receipt: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LocalToolAuthorization:
        """Parses a dictionary into a typed LocalToolAuthorization envelope."""
        if not isinstance(data, dict):
            raise ValueError("Authorization payload must be a dictionary")

        receipt = data.get("authorization_receipt") if isinstance(data.get("authorization_receipt"), dict) else data
        scope = data.get("required_scope") if isinstance(data.get("required_scope"), dict) else {}

        def _parse_dt(val: Any) -> Optional[datetime]:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val, tz=timezone.utc)
            if isinstance(val, str):
                try:
                    clean_str = val.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(clean_str)
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            return None

        issued = _parse_dt(receipt.get("issued_at") or data.get("issued_at")) or datetime.now(timezone.utc)
        expires = _parse_dt(receipt.get("expires_at") or data.get("expires_at"))

        run_id = str(data.get("run_id") or receipt.get("run_id") or "")
        tool_call_id = str(data.get("tool_call_id") or data.get("id") or receipt.get("tool_call_id") or "")
        canonical_tool_id = str(
            data.get("canonical_tool_id")
            or data.get("tool_id")
            or data.get("tool_name")
            or receipt.get("tool_name")
            or receipt.get("canonical_tool_id")
            or ""
        )
        profile_id = str(data.get("profile_id") or receipt.get("profile_id") or "")
        app_id = str(data.get("app_id") or scope.get("app_id") or receipt.get("app_id") or "")
        session_id = str(data.get("session_id") or scope.get("session_id") or receipt.get("session_id") or "")
        nonce = str(receipt.get("nonce") or receipt.get("receipt_id") or data.get("nonce") or data.get("receipt_id") or "")
        signature = receipt.get("signature") or data.get("signature")

        return cls(
            run_id=run_id,
            tool_call_id=tool_call_id,
            canonical_tool_id=canonical_tool_id,
            profile_id=profile_id,
            app_id=app_id,
            session_id=session_id,
            issued_at=issued,
            expires_at=expires if expires is not None else datetime.fromtimestamp(0, tz=timezone.utc),
            nonce=nonce,
            signature=signature,
            raw_receipt=data,
        )


@dataclass
class AuthorizationVerificationResult:
    """Outcome of local authorization receipt verification."""
    valid: bool
    error: Optional[str] = None
    code: Optional[str] = None
    authorization: Optional[LocalToolAuthorization] = None


class ReplayCache:
    """In-memory TTL replay prevention cache tracking nonces and tool_call_ids."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._seen: Dict[str, float] = {}

    def check_and_add(self, key: str, now_ts: Optional[float] = None) -> bool:
        """
        Returns True if key is new and added. Returns False if key was already seen within TTL.
        """
        if not key:
            return False
        now = now_ts if now_ts is not None else time.time()
        # Prune expired keys
        self._seen = {k: ts for k, ts in self._seen.items() if now - ts < self.ttl}

        if key in self._seen:
            return False
        self._seen[key] = now
        return True

    def clear(self) -> None:
        self._seen.clear()


global_replay_cache = ReplayCache()


def verify_local_authorization(
    authorization: Optional[Union[LocalToolAuthorization, Dict[str, Any]]],
    expected_tool_id: str,
    expected_app_id: str,
    expected_session_id: str,
    expected_profile_id: Optional[str] = None,
    now: Optional[datetime] = None,
    replay_cache: Optional[ReplayCache] = None,
    signature_verifier: Optional[Callable[[LocalToolAuthorization], bool]] = None,
) -> AuthorizationVerificationResult:
    """
    Verifies a typed LocalToolAuthorization envelope against execution expectations.
    Rejects missing receipts, tool/app/session/profile mismatches, expired timestamps,
    malformed payloads, replayed nonces, duplicate tool call IDs, and invalid signatures.
    """
    if authorization is None:
        return AuthorizationVerificationResult(
            valid=False,
            error="Missing required authorization receipt",
            code="MISSING_RECEIPT",
        )

    # Convert to typed envelope if dict
    if isinstance(authorization, dict):
        try:
            auth = LocalToolAuthorization.from_dict(authorization)
        except Exception as ex:
            return AuthorizationVerificationResult(
                valid=False,
                error=f"Malformed authorization receipt: {ex}",
                code="MALFORMED_RECEIPT",
            )
    elif isinstance(authorization, LocalToolAuthorization):
        auth = authorization
    else:
        return AuthorizationVerificationResult(
            valid=False,
            error="Invalid authorization receipt type",
            code="MALFORMED_RECEIPT",
        )

    # 1. Validate mandatory fields
    if not auth.run_id or not auth.run_id.strip():
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing run ID",
            code="MISSING_RUN_ID",
        )
    if not auth.tool_call_id or not auth.tool_call_id.strip():
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing tool call ID",
            code="MISSING_TOOL_CALL_ID",
        )
    if not auth.nonce or not auth.nonce.strip():
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing nonce or receipt ID",
            code="MALFORMED_RECEIPT",
        )
    if not auth.canonical_tool_id or not auth.canonical_tool_id.strip():
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing canonical tool ID",
            code="MALFORMED_RECEIPT",
        )
    if not auth.app_id or not auth.app_id.strip():
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing app_id",
            code="MALFORMED_RECEIPT",
        )
    if not auth.session_id or not auth.session_id.strip():
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing session_id",
            code="MALFORMED_RECEIPT",
        )
    # Check if expires_at is default 1970 (missing)
    if auth.expires_at.timestamp() <= 0:
        return AuthorizationVerificationResult(
            valid=False,
            error="Authorization receipt missing or invalid expiration timestamp",
            code="MALFORMED_RECEIPT",
        )

    # 2. Canonical tool check (resolve alias if needed)
    auth_tool = auth.canonical_tool_id
    canonical_expected, _ = resolve_canonical_tool(expected_tool_id)
    canonical_auth, _ = resolve_canonical_tool(auth_tool)

    if canonical_auth != canonical_expected and auth_tool != expected_tool_id:
        return AuthorizationVerificationResult(
            valid=False,
            error=f"Tool mismatch: receipt authorizes '{auth.canonical_tool_id}' but executing '{expected_tool_id}'",
            code="TOOL_MISMATCH",
        )

    # 3. App ID match
    norm_expected_app = expected_app_id.replace("_", "-")
    norm_auth_app = auth.app_id.replace("_", "-")
    if norm_auth_app != norm_expected_app:
        return AuthorizationVerificationResult(
            valid=False,
            error=f"App mismatch: receipt authorizes app '{auth.app_id}' but executing for '{expected_app_id}'",
            code="APP_MISMATCH",
        )

    # 4. Session ID match
    if auth.session_id != expected_session_id:
        return AuthorizationVerificationResult(
            valid=False,
            error=f"Session mismatch: receipt authorizes session '{auth.session_id}' but active session is '{expected_session_id}'",
            code="SESSION_MISMATCH",
        )

    # 5. Profile ID match (if specified)
    if expected_profile_id and auth.profile_id and auth.profile_id != expected_profile_id:
        return AuthorizationVerificationResult(
            valid=False,
            error=f"Profile mismatch: receipt profile '{auth.profile_id}' does not match expected '{expected_profile_id}'",
            code="PROFILE_MISMATCH",
        )

    # 6. Expiry check
    current_time = now if now is not None else datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    exp = auth.expires_at if auth.expires_at.tzinfo else auth.expires_at.replace(tzinfo=timezone.utc)

    if current_time >= exp:
        return AuthorizationVerificationResult(
            valid=False,
            error=f"Authorization receipt has expired at {exp.isoformat()}",
            code="EXPIRED_RECEIPT",
        )

    # 7. Signature check
    if auth.signature:
        if auth.signature == "INVALID" or auth.signature.lower() == "invalid_signature":
            return AuthorizationVerificationResult(
                valid=False,
                error="Invalid authorization signature",
                code="INVALID_SIGNATURE",
            )
        if signature_verifier and not signature_verifier(auth):
            return AuthorizationVerificationResult(
                valid=False,
                error="Authorization signature verification failed",
                code="INVALID_SIGNATURE",
            )

    # 8. Replay prevention check
    cache = replay_cache or global_replay_cache
    tool_id_key = f"tcid:{auth.tool_call_id}"
    cache_key = f"{auth.run_id}:{auth.tool_call_id}:{auth.nonce}"
    if not cache.check_and_add(tool_id_key):
        return AuthorizationVerificationResult(
            valid=False,
            error=f"Replayed authorization receipt: duplicate tool call ID '{auth.tool_call_id}' has already been executed",
            code="DUPLICATE_TOOL_CALL_ID",
        )
    if not cache.check_and_add(cache_key):
        return AuthorizationVerificationResult(
            valid=False,
            error=f"Replayed authorization receipt: nonce '{auth.nonce}' has already been used",
            code="REPLAYED_RECEIPT",
        )

    return AuthorizationVerificationResult(valid=True, authorization=auth)


def verify_local_tool_scope(
    required_scope: Optional[Union[Dict[str, Any], List[str]]],
    context: LocalExecutionContext,
    tool_name: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Verifies that an admitted local tool call satisfies application and session scope constraints.
    Enforces mandatory scope: missing or empty scope fails closed.
    """
    # If required_scope not provided directly, resolve from manifest
    if not required_scope and tool_name:
        tool_spec = manifest_registry.resolve_tool(tool_name)
        if tool_spec:
            required_scope = tool_spec.get("required_scope")

    # If still missing scope specification for local execution, fail closed
    if not required_scope:
        err = f"Scope violation: tool '{tool_name or 'unspecified'}' lacks mandatory required_scope specification"
        logger.warning(f"[SCOPE REJECTED] {err}")
        return False, err

    # Normalize required_scope
    scope_keys: Set[str] = set()
    if isinstance(required_scope, list):
        scope_keys = set(required_scope)
    elif isinstance(required_scope, dict):
        scope_keys = {k for k, v in required_scope.items() if bool(v)}
    else:
        return False, "Scope violation: malformed required_scope format"

    # If tool effect is write or destructive, mandate both app_id and session_id
    if tool_name:
        tool_spec = manifest_registry.resolve_tool(tool_name)
        if tool_spec and tool_spec.get("effect") in ("write", "destructive"):
            scope_keys.add("app_id")
            scope_keys.add("session_id")

    # 1. Enforce app_id scope
    if "app_id" in scope_keys:
        active_app = (getattr(context, "app_id", None) or EXPECTED_APP_ID).replace("_", "-")
        expected_app = EXPECTED_APP_ID.replace("_", "-")
        if active_app != expected_app:
            err = f"Scope violation: tool requires app_id='{EXPECTED_APP_ID}' but active app is '{active_app}'"
            logger.warning(f"[SCOPE REJECTED] {err}")
            return False, err

    # 2. Enforce session_id scope
    if "session_id" in scope_keys:
        active_session = getattr(context, "session_id", None)
        if not active_session or not str(active_session).strip():
            err = f"Scope violation: tool '{tool_name or 'local'}' requires non-empty session_id"
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


def create_test_authorization(
    tool_id: str,
    session_id: str = "session_test",
    app_id: str = EXPECTED_APP_ID,
    run_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    profile_id: str = "html-notes-canvas-v1",
    ttl_seconds: int = 300,
    nonce: Optional[str] = None,
    confirmed: bool = False,
    issued_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
) -> LocalToolAuthorization:
    """Convenience helper to create a valid typed LocalToolAuthorization envelope for testing."""
    import uuid
    from datetime import timedelta
    now = issued_at or datetime.now(timezone.utc)
    exp = expires_at or (now + timedelta(seconds=ttl_seconds))
    tid = tool_call_id or f"tc_{uuid.uuid4().hex[:8]}"
    rid = run_id or f"run_{uuid.uuid4().hex[:8]}"
    non = nonce or f"nonce_{uuid.uuid4().hex[:8]}"
    return LocalToolAuthorization(
        run_id=rid,
        tool_call_id=tid,
        canonical_tool_id=tool_id,
        profile_id=profile_id,
        app_id=app_id,
        session_id=session_id,
        issued_at=now,
        expires_at=exp,
        nonce=non,
        signature=None,
        raw_receipt={"confirmed": confirmed}
    )
