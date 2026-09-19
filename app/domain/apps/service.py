import json
import logging
from typing import Any, Dict, Optional
from app.services.portal import (
    get_portal_apps,
    resolve_portal_app,
    set_portal_override
)
from app.services.app_actions import (
    list_app_actions,
    get_action_spec,
    park_pending_action,
    execute_app_action,
    build_action_confirm_config
)

logger = logging.getLogger(__name__)


class AppsHubDomainService:
    """
    Domain service for portal registry queries, app launching, curation,
    and action execution (with confirmation parking for destructive operations).
    """

    @staticmethod
    async def list_services(
        query: str = "",
        status: str = "",
        include_hidden: bool = False
    ) -> Dict[str, Any]:
        data = await get_portal_apps(include_hidden=include_hidden)
        apps = data.get("apps", [])
        q = (query or "").strip().lower()
        if q:
            apps = [
                x for x in apps
                if q in f"{x.get('id', '')} {x.get('name', '')} {x.get('description', '')}".lower()
            ]
        stat = (status or "").strip().lower()
        if stat in ("healthy", "unhealthy", "unknown"):
            apps = [x for x in apps if x.get("status") == stat]

        slim = [
            {
                k: x.get(k)
                for k in ("id", "name", "description", "status", "launch_url", "pinned", "project_type", "device")
            }
            for x in apps
        ]
        return {"apps": slim, "count": len(slim), "stale": data.get("stale", False)}

    @staticmethod
    async def open_app(app_id: str = "", query: str = "") -> Dict[str, Any]:
        data = await get_portal_apps()
        search_key = app_id or query or ""
        app_match, candidates = resolve_portal_app(search_key, data.get("apps", []))
        if app_match:
            return {
                "success": True,
                "opened": {
                    "id": app_match["id"],
                    "name": app_match["name"],
                    "url": app_match["launch_url"]
                },
                "message": f"Opening {app_match['name']} in a new tab."
            }
        if candidates:
            return {
                "error": "Ambiguous app — ask user to clarify. Do not guess.",
                "candidates": [
                    {"id": c["id"], "name": c["name"], "url": c["launch_url"]}
                    for c in candidates
                ],
                "is_error": True
            }
        return {
            "error": f"No app matches '{search_key}'. Call html_notes_list_services to see valid ids.",
            "is_error": True
        }

    @staticmethod
    def list_actions(app_id: str = "") -> Dict[str, Any]:
        actions = list_app_actions(app_id or "")
        return {
            "actions": actions,
            "hint": "Run one with html_notes_app_action(app_id, action, params)."
        }

    @staticmethod
    async def execute_action(app_id: str, action: str, params: Optional[Any] = None) -> Dict[str, Any]:
        app_id = (app_id or "").strip()
        action = (action or "").strip()
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        elif not isinstance(params, dict):
            params = {}

        spec = get_action_spec(app_id, action)
        if not spec:
            return {
                "error": f"No action '{action}' on '{app_id}'.",
                "available": [f"{r['app_id']}.{r['action']}" for r in list_app_actions()],
                "is_error": True
            }

        if spec.get("destructive"):
            pending_id = park_pending_action(app_id, action, params)
            confirm_config = build_action_confirm_config(app_id, action, params, pending_id)
            return {
                "success": True,
                "confirmation_required": True,
                "pending_id": pending_id,
                "confirm_config": confirm_config,
                "message": (
                    f"{app_id}.{action} needs confirmation — a confirm card is on the canvas. "
                    "Tell the user to click Run it."
                )
            }

        return await execute_app_action(app_id, action, params)

    @staticmethod
    async def curate_app(app_id: str, hidden: Optional[bool] = None, pinned: Optional[bool] = None) -> Dict[str, Any]:
        data = await get_portal_apps(include_hidden=True)
        app_id = (app_id or "").strip()
        all_ids = {x.get("id") for x in data.get("apps", [])}
        if app_id not in all_ids:
            app_match, candidates = resolve_portal_app(app_id, data.get("apps", []))
            if not app_match:
                return {
                    "error": f"Unknown app_id '{app_id}'",
                    "candidates": [c["id"] for c in candidates],
                    "is_error": True
                }
            app_id = app_match["id"]

        set_portal_override(app_id, hidden=hidden, pinned=pinned)
        return {
            "success": True,
            "app_id": app_id,
            "hidden": hidden,
            "pinned": pinned
        }

apps_hub_service = AppsHubDomainService()
