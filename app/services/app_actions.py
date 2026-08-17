import sys
import os as _os
import app.main as main
# Captured BEFORE the namespace adoption below, which overwrites __file__ with
# main's (same trap as portal.py — a later __file__-relative path would resolve
# against app/main.py's directory instead of this file's).
_ACTIONS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "app_actions.json")
sys.modules[__name__].__dict__.update(main.__dict__)

# ── Canvas control plane ─────────────────────────────────────────────────────
# One generic tool (html_notes_app_action) drives every container, backed by
# app_actions.json. The point of a registry rather than one tool per repo: the
# gateway caches tool schemas at STARTUP, so a per-repo tool means a schema
# rebuild + a lazy-agent redeploy for every new action. Here a new capability
# is a JSON edit, live on the next html-notes deploy.
#
# Safety model: every action declares `destructive`. Safe actions run inline.
# A destructive one is NEVER executed by the model — it is parked here and the
# canvas renders a confirm card; only the user's click calls run_pending().

_ACTION_TIMEOUT_DEFAULT = 20.0
# Pending destructive actions, id -> {app_id, action, params, created}. In
# memory on purpose: a pending action must not survive a restart, or a stale
# card could fire a trading cycle the user asked for an hour ago.
_pending_actions: Dict[str, dict] = {}
_PENDING_TTL = 900.0


def load_action_registry() -> dict:
    """The capability registry, or {} when unreadable (control plane simply
    offers nothing rather than breaking the turn)."""
    try:
        with open(_ACTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[ACTIONS] app_actions.json unreadable: {e}")
        return {}


def list_app_actions(app_id: str = "") -> list:
    """Every registered action as flat rows the model can scan:
    {app_id, action, description, destructive, params}."""
    reg = (load_action_registry().get("actions") or {})
    rows = []
    for aid, actions in reg.items():
        if app_id and aid != app_id:
            continue
        if not isinstance(actions, dict):
            continue
        for name, spec in actions.items():
            if not isinstance(spec, dict):
                continue
            rows.append({
                "app_id": aid,
                "action": name,
                "description": spec.get("description", ""),
                "destructive": bool(spec.get("destructive")),
                "params": spec.get("params") or {},
            })
    return rows


def get_action_spec(app_id: str, action: str) -> Optional[dict]:
    return ((load_action_registry().get("actions") or {})
            .get(app_id, {}) or {}).get(action)


def _render_body(template, params: dict):
    """Fill a body template from params.

    '$name'  -> params['name'], and the KEY IS DROPPED when absent, so an
                omitted optional never lands as a literal '$name' or a null
                that an API would treat as an explicit value.
    '$name!' -> required; raises ValueError when missing.
    anything else is sent literally.
    """
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            if isinstance(v, str) and v.startswith("$"):
                required = v.endswith("!")
                key = v[1:-1] if required else v[1:]
                if key in params and params[key] is not None:
                    out[k] = params[key]
                elif required:
                    raise ValueError(f"missing required parameter '{key}'")
                # else: drop the key entirely
            else:
                out[k] = _render_body(v, params) if isinstance(v, (dict, list)) else v
        return out
    if isinstance(template, list):
        return [_render_body(v, params) for v in template]
    return template


def _dig(payload, path: str):
    """Follow a dotted path into a JSON response; None if it isn't there."""
    cur = payload
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


async def execute_app_action(app_id: str, action: str, params: dict = None) -> dict:
    """Run ONE registered action. Assumes the caller already handled the
    destructive gate — run_pending() and the safe path both come through here."""
    params = params or {}
    spec = get_action_spec(app_id, action)
    if not spec:
        return {"error": f"Unknown action '{action}' for app '{app_id}'.",
                "is_error": True}

    url = spec.get("url") or ""
    try:
        # Path placeholders ({ticker}) come from params too.
        url = url.format(**{k: v for k, v in params.items() if v is not None})
    except KeyError as e:
        return {"error": f"missing url parameter {e}", "is_error": True}

    method = (spec.get("method") or "GET").upper()
    timeout = float(spec.get("timeout") or _ACTION_TIMEOUT_DEFAULT)
    try:
        body = _render_body(spec.get("body"), params) if spec.get("body") else None
    except ValueError as e:
        return {"error": str(e), "is_error": True}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=body)
    except Exception as e:
        logger.warning(f"[ACTIONS] {app_id}.{action} transport error: {e}")
        return {"error": f"{app_id} did not respond: {e}", "is_error": True}

    try:
        payload = resp.json()
    except Exception:
        payload = {"text": (resp.text or "")[:500]}

    ok = resp.status_code < 400
    result = {"success": ok, "app_id": app_id, "action": action,
              "status_code": resp.status_code, "result": payload}
    if spec.get("summary"):
        result["summary"] = _dig(payload, spec["summary"])
    if not ok:
        # A 409 from run-cycle means "already running" — a real answer, so it
        # is reported as a status rather than swallowed as a transport failure.
        result["error"] = f"{app_id}.{action} returned HTTP {resp.status_code}"
        result["is_error"] = True
    logger.info(f"[ACTIONS] {app_id}.{action} -> {resp.status_code}")
    return result


def park_pending_action(app_id: str, action: str, params: dict) -> str:
    """Store a destructive action awaiting the user's click; returns its id."""
    now = time.time()
    for k, v in list(_pending_actions.items()):      # opportunistic sweep
        if now - v.get("created", 0) > _PENDING_TTL:
            _pending_actions.pop(k, None)
    pid = uuid.uuid4().hex[:12]
    _pending_actions[pid] = {"app_id": app_id, "action": action,
                             "params": params or {}, "created": now}
    return pid


def get_pending_action(pending_id: str) -> Optional[dict]:
    entry = _pending_actions.get(pending_id)
    if not entry:
        return None
    if time.time() - entry.get("created", 0) > _PENDING_TTL:
        _pending_actions.pop(pending_id, None)
        return None
    return entry


async def run_pending_action(pending_id: str) -> dict:
    """Execute a parked action ONCE. Consumed on use so a double-click cannot
    start two trading cycles."""
    entry = _pending_actions.pop(pending_id, None)
    if not entry:
        return {"error": "This confirmation has expired — ask again.",
                "is_error": True}
    if time.time() - entry.get("created", 0) > _PENDING_TTL:
        return {"error": "This confirmation has expired — ask again.",
                "is_error": True}
    return await execute_app_action(entry["app_id"], entry["action"],
                                    entry["params"])


def build_action_confirm_config(app_id: str, action: str, params: dict,
                                pending_id: str) -> dict:
    """Config for the action_confirm widget."""
    spec = get_action_spec(app_id, action) or {}
    return {
        "title": f"Confirm: {app_id}",
        "app_id": app_id,
        "action": action,
        "description": spec.get("description", ""),
        "params": params or {},
        "pending_id": pending_id,
    }


async def build_actions_prompt_block(limit: int = 40) -> str:
    """A compact 'YOU CAN DO' block for SYSTEM_PROMPT listing every registered
    action, so the agent knows the canvas can DRIVE these apps rather than only
    open them. Cheap: a local file read, no network."""
    rows = list_app_actions()[:limit]
    if not rows:
        return ""
    lines = []
    for r in rows:
        flag = " [CONFIRM REQUIRED]" if r["destructive"] else ""
        lines.append(f"- {r['app_id']}.{r['action']}{flag} — {r['description'][:150]}")
    return ("\n\nYOU CAN DO (run these with "
            "mcp__lazy-tool-service__html_notes_app_action(app_id, action, params)):\n"
            + "\n".join(lines) + "\n")


__all__ = [k for k in globals().keys() if not k.startswith('__')]
