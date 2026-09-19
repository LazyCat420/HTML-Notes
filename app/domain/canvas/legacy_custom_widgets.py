import logging
import warnings
from typing import Any, Dict

logger = logging.getLogger(__name__)


class LegacyCustomWidgetService:
    """
    Quarantined implementation of legacy custom widget tools:
    - create_widget
    - plan_widget
    - update_widget
    - list_widget_types

    These tools are DEPRECATED in favor of canvas_add_widget / html_notes.canvas.upsert_widget
    using pre-built server-rendered catalog widgets.
    """

    @staticmethod
    def log_deprecation_warning(tool_name: str) -> None:
        msg = (
            f"[DEPRECATION] Tool '{tool_name}' is deprecated. "
            f"Use 'canvas_add_widget' / 'html_notes.canvas.upsert_widget' for canonical server-rendered widgets."
        )
        logger.warning(msg)
        warnings.warn(msg, DeprecationWarning, stacklevel=2)

    def plan_widget(self, widget_type: str, title: str, description: str = "") -> Dict[str, Any]:
        self.log_deprecation_warning("plan_widget")
        return {
            "success": True,
            "status": "planned",
            "widgetType": widget_type,
            "title": title,
            "description": description,
            "deprecated": True
        }

    def create_widget(
        self,
        widget_type: str,
        title: str,
        html_content: str,
        css_content: str = "",
        js_content: str = "",
        widget_id: str = "custom_widget_1"
    ) -> Dict[str, Any]:
        self.log_deprecation_warning("create_widget")
        return {
            "success": True,
            "widget_id": widget_id,
            "widget_type": widget_type,
            "title": title,
            "deprecated": True,
            "message": "Custom widget created in quarantined legacy sandbox."
        }

    def update_widget(
        self,
        widget_id: str,
        title: str = "",
        html_content: str = "",
        css_content: str = "",
        js_content: str = ""
    ) -> Dict[str, Any]:
        self.log_deprecation_warning("update_widget")
        return {
            "success": True,
            "widget_id": widget_id,
            "deprecated": True,
            "message": f"Custom widget '{widget_id}' updated in quarantined legacy sandbox."
        }

    def list_widget_types(self) -> Dict[str, Any]:
        self.log_deprecation_warning("list_widget_types")
        return {
            "types": [
                "checklist", "clock", "notes", "iframe_app",
                "mini_music_player", "youtube_player", "custom"
            ],
            "deprecated": True,
            "recommended": "Refer to html_notes.widget-catalog.json for authoritative widget list."
        }

legacy_custom_widgets = LegacyCustomWidgetService()
