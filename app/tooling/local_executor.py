import logging
import time
from typing import Any, Dict, Optional, Union
from app.tooling.html_notes_manifest import manifest_registry
from app.tooling.policy import tool_policy
from app.domain.notes.service import notes_service
from app.domain.canvas.service import canvas_service
from app.domain.canvas.legacy_custom_widgets import legacy_custom_widgets
from app.domain.apps.service import apps_hub_service
from app.domain.watches.service import watches_service
from app.adapters.providers.service import provider_adapter
from app.presentation.widgets.catalog import widget_catalog
from app.adapters.runtime.models import (
    LocalToolAuthorization,
    verify_local_authorization,
    AuthorizationVerificationResult,
    LocalExecutionContext,
)

logger = logging.getLogger(__name__)


class LocalToolExecutor:
    """
    Modular local tool executor for HTML-Notes application-owned domain operations.
    Decouples domain execution from monolithic HTTP routes.
    Enforces manifest schema, scope isolation, confirmation gates, and safety rules.
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
        authorization: Optional[Union[LocalToolAuthorization, Dict[str, Any]]] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
        context: Optional[Any] = None,
        allow_quarantined: bool = True,
        confirmation: Optional[bool] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Executes an application-owned domain tool call through the mandatory admission pipeline:
        1. Resolve alias to canonical ID (structured error if retired).
        2. Load manifest entry (reject unknown).
        3. Validate tool is local execution.
        4. Validate admission whitelist.
        5. Validate safety rules.
        6. Validate argument schema.
        7. Require correct scope (fails closed if missing).
        8. Validate authorization receipt.
        9. Enforce confirmation policy.
        10. Dispatch to approved domain handler.
        """
        # 1. Resolve alias to canonical tool ID & check retirement
        canonical_id = self.registry.resolve_alias_to_canonical(tool_name) or tool_name
        if self.registry.is_retired(tool_name) or self.registry.is_retired(canonical_id):
            return {
                "success": False,
                "is_error": True,
                "error": f"Tool alias '{tool_name}' has been retired and is no longer available",
                "code": "TOOL_RETIRED",
                "tool": tool_name
            }

        # 2. Load manifest entry
        tool_spec = self.registry.resolve_tool(tool_name)
        if not tool_spec and not tool_name.startswith("global."):
            return {
                "success": False,
                "is_error": True,
                "error": f"Tool '{tool_name}' is not permitted (unknown tool)",
                "code": "UNKNOWN_TOOL",
                "tool": tool_name
            }

        if tool_spec:
            canonical_id = tool_spec.get("id", canonical_id)

            # 3. Validate tool is local
            if tool_spec.get("execution") != "local":
                return {
                    "success": False,
                    "is_error": True,
                    "error": f"Tool '{tool_name}' is not a local execution tool (execution={tool_spec.get('execution')})",
                    "code": "NON_LOCAL_TOOL",
                    "tool": tool_name
                }

        # 4. Check admission policy
        admitted, admission_err = self.policy.check_admission(tool_name)
        if not admitted:
            return {
                "success": False,
                "is_error": True,
                "error": admission_err,
                "code": "ADMISSION_DENIED",
                "tool": tool_name
            }

        # 5. Check safety rules (prevent arbitrary script injection)
        safe, safety_err = self.policy.validate_safety(tool_name, args)
        if not safe:
            return {
                "success": False,
                "is_error": True,
                "error": safety_err,
                "code": "SAFETY_VIOLATION",
                "tool": tool_name
            }

        # 6. Validate argument schema
        valid, val_err = self.policy.validate_args(tool_name, args)
        if not valid:
            return {
                "success": False,
                "is_error": True,
                "error": val_err,
                "code": "INVALID_ARGUMENTS",
                "tool": tool_name
            }

        # 7. Resolve context (app_id, session_id)
        app_id = "html-notes"
        if context:
            session_id = session_id or getattr(context, "session_id", None)
            if isinstance(context, dict):
                session_id = session_id or context.get("session_id")
                app_id = context.get("app_id", app_id)
            else:
                app_id = getattr(context, "app_id", app_id)
        if runtime_context:
            app_id = runtime_context.get("app_id", app_id)
            session_id = session_id or runtime_context.get("session_id")

        # 8. Validate mandatory scope requirements (fails closed if missing)
        scope_ok, scope_err = self.policy.validate_scope(tool_name, session_id=session_id, app_id=app_id)
        if not scope_ok:
            return {
                "success": False,
                "is_error": True,
                "error": scope_err,
                "code": "SCOPE_VIOLATION",
                "tool": tool_name
            }

        # 9. Validate authorization receipt
        requires_receipt = self.policy.requires_authorization_receipt(tool_name)
        if requires_receipt or authorization is not None:
            expected_profile = (runtime_context or {}).get("profile_id")
            expected_run = (runtime_context or {}).get("run_id")
            expected_call = (runtime_context or {}).get("tool_call_id") or args.get("tool_call_id") or args.get("id")
            auth_res = verify_local_authorization(
                authorization=authorization,
                expected_tool_id=canonical_id,
                expected_app_id=app_id,
                expected_session_id=session_id or "",
                expected_profile_id=expected_profile,
                expected_run_id=expected_run,
                expected_tool_call_id=expected_call,
                expected_args=args,
            )
            if not auth_res.valid:
                return {
                    "success": False,
                    "is_error": True,
                    "error": auth_res.error or "Authorization verification failed",
                    "code": auth_res.code or "UNAUTHORIZED",
                    "tool": tool_name
                }

        # 10. Check confirmation requirement
        if self.policy.requires_confirmation(tool_name, args):
            is_confirmed = bool(
                confirmation is True
                or args.get("confirmed") is True
                or (isinstance(authorization, dict) and authorization.get("confirmed") is True)
                or (isinstance(authorization, LocalToolAuthorization) and (authorization.raw_receipt or {}).get("confirmed") is True)
            )
            if not is_confirmed:
                return {
                    "success": False,
                    "is_error": True,
                    "confirmation_required": True,
                    "error": f"Tool '{tool_name}' is destructive and requires explicit user confirmation",
                    "code": "CONFIRMATION_REQUIRED",
                    "tool": tool_name
                }
            if canonical_id in ("html_notes.apps.execute_action", "html_notes.portal.execute_action") or tool_name in ("html_notes_app_action", "execute_action"):
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

        # 11. Dispatch execution to domain services
        try:
            result = await self._dispatch(canonical_id, tool_name, args, session_id, canvas_html)
            is_err = isinstance(result, dict) and bool(result.get("is_error"))
            out = {
                "success": not is_err,
                "is_error": is_err,
                "result": result,
                "tool": tool_name
            }
            if is_err and isinstance(result, dict) and "error" in result:
                out["error"] = result["error"]
            return out
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
                links=args.get("links"),
                session_id=session_id
            )
        elif canonical_id == "html_notes.notes.update":
            note_args = {k: v for k, v in args.items() if k != "note_id"}
            return notes_service.update_note(note_id=args.get("note_id", ""), session_id=session_id, **note_args)
        elif canonical_id == "html_notes.notes.get":
            return notes_service.get_note(note_id=args.get("note_id", ""))
        elif canonical_id == "html_notes.notes.search":
            return notes_service.search_notes(query=args.get("query", ""))
        elif canonical_id == "html_notes.notes.link":
            fn = getattr(notes_service, "link_notes")
            try:
                return fn(args.get("source_note_id", ""), args.get("target_note_id", ""), session_id)
            except TypeError:
                return fn(args.get("source_note_id", ""), args.get("target_note_id", ""))
        elif canonical_id == "html_notes.notes.claim":
            return notes_service.claim_note(
                note_id=args.get("note_id", ""),
                session_id=session_id,
                owner_id=args.get("owner_id"),
            )

        # Canvas Domain
        elif canonical_id == "html_notes.canvas.upsert_widget":
            return canvas_service.upsert_widget(
                widget_type=args.get("widget_type", ""),
                widget_id=args.get("widget_id", ""),
                config=args.get("config", {}),
                session_id=session_id,
                current_canvas_html=canvas_html
            )
        elif canonical_id == "html_notes.canvas.remove_widget":
            return canvas_service.remove_widget(
                widget_id=args.get("widget_id"),
                selector=args.get("selector"),
                session_id=session_id,
                current_canvas_html=canvas_html or ""
            )
        elif canonical_id in ("html_notes.canvas.mutate", "html_notes.canvas.modify_dom"):
            return canvas_service.mutate(
                action=args.get("action", ""),
                selector=args.get("selector", ""),
                html_snippet=args.get("html", ""),
                widget_id=args.get("widget_id"),
                current_canvas_html=canvas_html or "",
                session_id=session_id
            )
        elif canonical_id == "html_notes.canvas.read":
            return canvas_service.read_dom(
                current_canvas_html=canvas_html or "",
                selector=args.get("selector")
            )

        # Widgets Catalog
        elif canonical_id == "html_notes.widgets.list_catalog":
            return {"widgets": widget_catalog.get_all_widgets()}

        # Apps / Portal Domain
        elif canonical_id in ("html_notes.apps.list", "html_notes.portal.list_services"):
            return await apps_hub_service.list_services(
                query=args.get("query", ""),
                status=args.get("status", ""),
                include_hidden=bool(args.get("include_hidden", False))
            )
        elif canonical_id in ("html_notes.apps.open", "html_notes.portal.open_app"):
            return await apps_hub_service.open_app(
                app_id=args.get("app_id", ""),
                query=args.get("query", "")
            )
        elif canonical_id in ("html_notes.apps.list_actions", "html_notes.portal.list_actions"):
            return apps_hub_service.list_actions(app_id=args.get("app_id", ""))
        elif canonical_id in ("html_notes.apps.execute_action", "html_notes.portal.execute_action"):
            return await apps_hub_service.execute_action(
                app_id=args.get("app_id", ""),
                action=args.get("action", ""),
                params=args.get("params")
            )
        elif canonical_id in ("html_notes.apps.curate", "html_notes.portal.curate_app"):
            return await apps_hub_service.curate_app(
                app_id=args.get("app_id", ""),
                hidden=args.get("hidden"),
                pinned=args.get("pinned")
            )

        # Watches Domain
        elif canonical_id == "html_notes.watches.create":
            return watches_service.create_watch(
                session_id=session_id or "",
                kind=args.get("kind", ""),
                spec=args.get("spec", {}),
                label=args.get("label", ""),
                interval_s=args.get("interval_s")
            )
        elif canonical_id == "html_notes.watches.list":
            return watches_service.list_watches(session_id=session_id or "")
        elif canonical_id == "html_notes.watches.cancel":
            return watches_service.cancel_watch(
                watch_id=args.get("watch_id", ""),
                session_id=session_id or ""
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
