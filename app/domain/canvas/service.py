import logging
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html
from app.tooling.html_notes_manifest import manifest_registry

logger = logging.getLogger(__name__)


class CanvasDomainService:
    """
    Domain service for Canvas operations:
    - Widget synthesis and server-side rendering
    - Live DOM inspection and reading
    - Structured DOM mutations (append, prepend, replace, remove)
    """

    def __init__(self, registry=manifest_registry):
        self.registry = registry
        self._widget_sessions: Dict[str, str] = {}

    def upsert_widget(
        self,
        widget_type: str,
        widget_id: str,
        config: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        current_canvas_html: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Renders widget markup server-side and prepares canvas insertion/update.
        """
        config = config or {}
        catalog = self.registry.get_widget_catalog()
        
        # Check singleton constraint if configured
        is_singleton = False
        for w in catalog.get("widgets", []):
            if w.get("type") == widget_type and w.get("is_singleton"):
                is_singleton = True
                break

        # Check cross-session widget ownership
        if widget_id in self._widget_sessions and session_id and self._widget_sessions[widget_id] != session_id:
            return {
                "error": f"Unauthorized: widget '{widget_id}' belongs to another session",
                "is_error": True,
                "widget_type": widget_type,
                "widget_id": widget_id
            }

        # If singleton and current_canvas_html is present, discover existing singleton id to update in place
        target_id = widget_id
        if is_singleton and current_canvas_html:
            soup = BeautifulSoup(current_canvas_html, "html.parser")
            existing = soup.find(attrs={"data-widget-type": widget_type}) or soup.find(id=widget_id)
            if existing and existing.get("id"):
                target_id = existing.get("id")

        if session_id:
            self._widget_sessions[target_id] = session_id

        # Generate HTML from factory
        try:
            rendered_html = generate_widget_html(widget_type, target_id, config)
        except Exception as e:
            logger.exception(f"Failed to render widget {widget_type}:{target_id}: {e}")
            return {
                "error": f"Failed to render widget: {str(e)}",
                "is_error": True,
                "widget_type": widget_type,
                "widget_id": target_id
            }

        return {
            "success": True,
            "widget_type": widget_type,
            "widget_id": target_id,
            "is_singleton": is_singleton,
            "html": rendered_html,
            "config": config
        }

    def remove_widget(
        self,
        widget_id: Optional[str] = None,
        selector: Optional[str] = None,
        session_id: Optional[str] = None,
        current_canvas_html: str = ""
    ) -> Dict[str, Any]:
        """
        Dedicated scoped widget removal. Enforces exact #id selector.
        """
        sel = selector or (f"#{widget_id}" if widget_id else "")
        if not sel:
            return {"error": "Missing widget_id or selector for remove", "is_error": True}

        # Ambiguous selector check
        if not sel.startswith("#"):
            return {
                "error": f"Ambiguous remove: selector '{sel}' is not an exact #id.",
                "is_error": True
            }

        # Verify cross-session ownership if widget_id known
        wid = widget_id or sel.lstrip("#")
        if wid in self._widget_sessions and session_id and self._widget_sessions[wid] != session_id:
            return {
                "error": f"Unauthorized: widget '{wid}' belongs to another session",
                "is_error": True
            }

        return self.modify_dom(
            action="remove",
            selector=sel,
            widget_id=wid,
            current_canvas_html=current_canvas_html
        )

    def mutate(
        self,
        action: str,
        selector: str,
        html_snippet: str = "",
        widget_id: Optional[str] = None,
        current_canvas_html: str = "",
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Canonical DOM mutation method with safety checks.
        """
        return self.modify_dom(
            action=action,
            selector=selector,
            html_snippet=html_snippet,
            widget_id=widget_id,
            current_canvas_html=current_canvas_html
        )

    def modify_dom(
        self,
        action: str,
        selector: str,
        html_snippet: str = "",
        widget_id: Optional[str] = None,
        current_canvas_html: str = ""
    ) -> Dict[str, Any]:
        """
        Applies structured DOM mutation to canvas markup.
        Supported actions: append, prepend, insert_before, insert_after, replace, remove.
        """
        if not current_canvas_html:
            return {
                "success": True,
                "action": action,
                "selector": selector,
                "note": "Empty canvas html, operation acknowledged"
            }

        soup = BeautifulSoup(current_canvas_html, "html.parser")
        targets = soup.select(selector)

        if not targets:
            return {
                "error": f"Selector '{selector}' not found on canvas",
                "is_error": True
            }

        if action == "remove":
            if len(targets) > 1:
                ids = [t.get("id") for t in targets if t.get("id")]
                return {
                    "error": f"Ambiguous remove: selector '{selector}' matched {len(targets)} elements. Use an exact #id.",
                    "candidates": ids,
                    "is_error": True
                }
            removed_id = targets[0].get("id", selector)
            targets[0].decompose()
            return {
                "success": True,
                "action": "remove",
                "removed": removed_id,
                "canvas_html": str(soup)
            }

        target = targets[0]
        snippet_soup = BeautifulSoup(html_snippet or "", "html.parser")

        if action == "append":
            target.append(snippet_soup)
        elif action == "prepend":
            target.insert(0, snippet_soup)
        elif action == "insert_before":
            target.insert_before(snippet_soup)
        elif action == "insert_after":
            target.insert_after(snippet_soup)
        elif action == "replace":
            target.replace_with(snippet_soup)
        else:
            return {
                "error": f"Unsupported DOM action '{action}'",
                "is_error": True
            }

        return {
            "success": True,
            "action": action,
            "selector": selector,
            "canvas_html": str(soup)
        }

    def read_dom(
        self,
        current_canvas_html: str = "",
        selector: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Inspects live canvas HTML and returns element inventory and widget count.
        """
        if not current_canvas_html:
            return {
                "count": 0,
                "widgets": [],
                "summary": "Canvas is empty"
            }

        soup = BeautifulSoup(current_canvas_html, "html.parser")
        nodes = soup.select(".widget-container") or soup.find_all(attrs={"data-widget-type": True})

        widgets = []
        for n in nodes:
            wid = n.get("id") or "unknown"
            wtype = n.get("data-widget-type") or "unknown"
            title_el = n.find(["h2", "h3", "h4", "header"])
            title = title_el.get_text(strip=True) if title_el else ""
            widgets.append({
                "widget_id": wid,
                "widget_type": wtype,
                "title": title
            })

        matched_html = None
        if selector:
            target = soup.select_one(selector)
            if target:
                matched_html = str(target)

        return {
            "count": len(widgets),
            "widgets": widgets,
            "matched_html": matched_html
        }

canvas_service = CanvasDomainService()
