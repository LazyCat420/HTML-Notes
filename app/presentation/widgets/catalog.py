from typing import Any, Dict, List, Optional
from app.tooling.html_notes_manifest import manifest_registry


class WidgetCatalogService:
    """
    Presentation helper for discovering and validating canvas widgets.
    """

    def __init__(self, registry=manifest_registry):
        self.registry = registry

    def get_all_widgets(self) -> List[Dict[str, Any]]:
        catalog = self.registry.get_widget_catalog()
        return catalog.get("widgets", [])

    def get_widget_spec(self, widget_type: str) -> Optional[Dict[str, Any]]:
        for w in self.get_all_widgets():
            if w.get("type") == widget_type:
                return w
        return None

    def is_singleton(self, widget_type: str) -> bool:
        spec = self.get_widget_spec(widget_type)
        return bool(spec and spec.get("is_singleton"))

widget_catalog = WidgetCatalogService()
