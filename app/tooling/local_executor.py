import logging
from typing import Any, Dict, Optional
from app.tooling.html_notes_manifest import manifest_registry
from app.tooling.policy import tool_policy
from app.domain.notes.service import notes_service
from app.domain.canvas.service import canvas_service
from app.domain.canvas.legacy_custom_widgets import legacy_custom_widgets
from app.domain.apps.service import apps_hub_service
from app.adapters.providers.service import provider_adapter

logger = logging.getLogger(__name__)


class LocalToolExecutor:
    """
    Modular local tool executor for HTML-Notes application-owned domain operations.
    Decouples domain execution from monolithic HTTP routes.
    Supports dual-dispatch for canonical namespaced IDs and legacy aliases.
    """

    def __init__(self, registry=manifest_registry, policy=tool_policy):
        self.registry = registry
        self.policy = policy

    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        session_id: Optional[str] = None,
        canvas_html: Optional[str] = None,
        allow_quarantined: bool = True
    ) -> Dict[str, Any]:
        """
        Executes an application-owned domain tool call.
        """
        # 1. Normalize tool specification
        tool_spec = self.registry.resolve_tool(tool_name)
        canonical_id = tool_spec.get("id") if tool_spec else tool_name
        is_quarantined = bool(tool_spec and tool_spec.get("deprecated"))

        # 2. Check admission policy
        admitted, admission_err = self.policy.check_admission(tool_name)
        if not admitted:
            if is_quarantined and allow_quarantined:
                pass  # Permitted through legacy custom widget fallback
            else:
                return {
                    "success": False,
                    "is_error": True,
                    "error": admission_err,
                    "tool": tool_name
                }

        # 3. Validate arguments
        valid, val_err = self.policy.validate_args(tool_name, args)
        if not valid:
            return {
                "success": False,
                "is_error": True,
                "error": val_err,
                "tool": tool_name
            }

        # 4. Check confirmation requirement
        if self.policy.requires_confirmation(tool_name, args):
            if canonical_id == "html_notes.portal.execute_action" or tool_name == "html_notes_app_action":
                res = await apps_hub_service.execute_action(
                    app_id=args.get("app_id", ""),
                    action=args.get("action", ""),
                    params=args.get("params")
                )
                return {
                    "success": True,
                    "confirmation_required": True,
                    "result": res,
                    "tool": tool_name
                }

        # 5. Dispatch execution to domain services
        try:
            result = await self._dispatch(canonical_id, tool_name, args, session_id, canvas_html)
            is_err = isinstance(result, dict) and bool(result.get("is_error"))
            return {
                "success": not is_err,
                "is_error": is_err,
                "result": result,
                "tool": tool_name
            }
        except Exception as e:
            logger.exception(f"Execution failed for tool '{tool_name}': {e}")
            return {
                "success": False,
                "is_error": True,
                "error": str(e),
                "tool": tool_name
            }

    async def _dispatch(
        self,
        canonical_id: str,
        tool_name: str,
        args: Dict[str, Any],
        session_id: Optional[str],
        canvas_html: Optional[str]
    ) -> Any:
        # Notes Domain
        if canonical_id == "html_notes.notes.create":
            return notes_service.create_note(
                title=args.get("title", ""),
                rendered_html=args.get("rendered_html", ""),
                tags=args.get("tags"),
                links=args.get("links")
            )
        elif canonical_id == "html_notes.notes.update":
            note_args = {k: v for k, v in args.items() if k != "note_id"}
            return notes_service.update_note(note_id=args.get("note_id", ""), **note_args)
        elif canonical_id == "html_notes.notes.get":
            return notes_service.get_note(note_id=args.get("note_id", ""))
        elif canonical_id == "html_notes.notes.search":
            return notes_service.search_notes(query=args.get("query", ""))
        elif canonical_id == "html_notes.notes.link":
            return notes_service.link_notes(
                source_note_id=args.get("source_note_id", ""),
                target_note_id=args.get("target_note_id", "")
            )

        # Canvas Domain
        elif canonical_id == "html_notes.canvas.upsert_widget":
            return canvas_service.upsert_widget(
                widget_type=args.get("widget_type", ""),
                widget_id=args.get("widget_id", ""),
                config=args.get("config", {}),
                session_id=session_id
            )
        elif canonical_id == "html_notes.canvas.modify_dom":
            return canvas_service.modify_dom(
                action=args.get("action", ""),
                selector=args.get("selector", ""),
                html_snippet=args.get("html", ""),
                widget_id=args.get("widget_id"),
                current_canvas_html=canvas_html or ""
            )
        elif canonical_id == "html_notes.canvas.read":
            return canvas_service.read_dom(
                current_canvas_html=canvas_html or "",
                selector=args.get("selector")
            )

        # Apps / Portal Domain
        elif canonical_id == "html_notes.portal.list_services":
            return await apps_hub_service.list_services(
                query=args.get("query", ""),
                status=args.get("status", ""),
                include_hidden=bool(args.get("include_hidden", False))
            )
        elif canonical_id == "html_notes.portal.open_app":
            return await apps_hub_service.open_app(
                app_id=args.get("app_id", ""),
                query=args.get("query", "")
            )
        elif canonical_id == "html_notes.portal.list_actions":
            return apps_hub_service.list_actions(app_id=args.get("app_id", ""))
        elif canonical_id == "html_notes.portal.execute_action":
            return await apps_hub_service.execute_action(
                app_id=args.get("app_id", ""),
                action=args.get("action", ""),
                params=args.get("params")
            )
        elif canonical_id == "html_notes.portal.curate_app":
            return await apps_hub_service.curate_app(
                app_id=args.get("app_id", ""),
                hidden=args.get("hidden"),
                pinned=args.get("pinned")
            )

        # Presentation Providers
        elif canonical_id == "html_notes.weather.get":
            return await provider_adapter.get_weather_data(
                location=args.get("location", ""),
                units=args.get("units", "fahrenheit")
            )
        elif canonical_id == "html_notes.sports.scores":
            return await provider_adapter.get_sports_scores(league=args.get("league", ""))
        elif canonical_id == "html_notes.finance.stock_history":
            return await provider_adapter.get_stock_snapshot(
                symbol=args.get("symbol", ""),
                range_str=args.get("range", "1mo")
            )
        elif canonical_id == "html_notes.finance.stock_news":
            return await provider_adapter.get_stock_news(
                query=args.get("query", ""),
                limit=int(args.get("limit", 8))
            )
        elif canonical_id == "html_notes.news.get_news":
            topic = (args.get("topic") or args.get("query") or "").strip()
            return await provider_adapter.get_news_config(topic)
        elif canonical_id == "html_notes.media.youtube_search":
            return await provider_adapter.search_youtube(
                query=args.get("query", ""),
                limit=int(args.get("limit", 5)),
                form=args.get("format", "all")
            )

        # Quarantined Legacy Custom Widgets
        elif canonical_id == "html_notes.canvas.plan_custom_widget" or tool_name == "plan_widget":
            return legacy_custom_widgets.plan_widget(
                widget_type=args.get("widgetType", ""),
                title=args.get("title", ""),
                description=args.get("description", "")
            )
        elif canonical_id == "html_notes.canvas.create_custom_widget" or tool_name == "create_widget":
            return legacy_custom_widgets.create_widget(
                widget_type=args.get("widgetType", ""),
                title=args.get("title", ""),
                html_content=args.get("htmlContent", ""),
                css_content=args.get("cssContent", ""),
                js_content=args.get("jsContent", ""),
                widget_id=args.get("widgetId", "custom_1")
            )
        elif canonical_id == "html_notes.canvas.update_custom_widget" or tool_name == "update_widget":
            return legacy_custom_widgets.update_widget(
                widget_id=args.get("widgetId", ""),
                title=args.get("title", ""),
                html_content=args.get("htmlContent", ""),
                css_content=args.get("cssContent", ""),
                js_content=args.get("jsContent", "")
            )
        elif canonical_id == "html_notes.canvas.list_widget_types" or tool_name == "list_widget_types":
            return legacy_custom_widgets.list_widget_types()

        # Standalone Global Capability Fallbacks
        elif canonical_id == "global.web.search" or tool_name == "html_notes_web_search":
            return await provider_adapter.fallback_web_search(
                query=args.get("query", ""),
                limit=int(args.get("limit", 6))
            )
        elif canonical_id == "global.web.read_page" or tool_name == "html_notes_read_page":
            return await provider_adapter.fallback_read_page(
                url=args.get("url", ""),
                max_chars=int(args.get("max_chars", 6000))
            )

        else:
            return {"error": f"Unknown tool: '{tool_name}'", "is_error": True}

local_tool_executor = LocalToolExecutor()
