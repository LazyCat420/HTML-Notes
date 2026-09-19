import json
import pathlib
from typing import Any, Dict, List, Optional

_MANIFEST_DIR = pathlib.Path(__file__).resolve().parent / "manifests"


class ManifestError(RuntimeError):
    """Raised when an application manifest is missing or malformed."""
    pass


class HTMLNotesManifestRegistry:
    """
    Loads, caches, and indexes application-owned manifests:
    - Domain tools: html_notes.domain-tools.json
    - Application profile: html_notes.profile.json
    - Widget catalog: html_notes.widget-catalog.json
    - Global capabilities reference: global_capabilities_reference.json
    """

    def __init__(self, manifest_dir: Optional[pathlib.Path] = None):
        self.manifest_dir = manifest_dir or _MANIFEST_DIR
        self._domain_tools: Optional[Dict[str, Any]] = None
        self._profile: Optional[Dict[str, Any]] = None
        self._widget_catalog: Optional[Dict[str, Any]] = None
        self._global_capabilities: Optional[Dict[str, Any]] = None
        self._tool_by_id: Dict[str, Dict[str, Any]] = {}
        self._tool_by_legacy_name: Dict[str, Dict[str, Any]] = {}

    def get_domain_tools_manifest(self) -> Dict[str, Any]:
        if self._domain_tools is None:
            path = self.manifest_dir / "html_notes.domain-tools.json"
            if not path.exists():
                raise ManifestError(f"Missing domain tools manifest at {path}")
            with open(path, "r", encoding="utf-8") as f:
                self._domain_tools = json.load(f)
            
            self._tool_by_id = {}
            self._tool_by_legacy_name = {}
            for tool in self._domain_tools.get("tools", []):
                t_id = tool.get("id")
                if t_id:
                    self._tool_by_id[t_id] = tool
                legacy = tool.get("legacy_name")
                if legacy:
                    self._tool_by_legacy_name[legacy] = tool
        return self._domain_tools

    def get_profile(self) -> Dict[str, Any]:
        if self._profile is None:
            path = self.manifest_dir / "html_notes.profile.json"
            if not path.exists():
                raise ManifestError(f"Missing profile manifest at {path}")
            with open(path, "r", encoding="utf-8") as f:
                self._profile = json.load(f)
        return self._profile

    def get_widget_catalog(self) -> Dict[str, Any]:
        if self._widget_catalog is None:
            path = self.manifest_dir / "html_notes.widget-catalog.json"
            if not path.exists():
                raise ManifestError(f"Missing widget catalog manifest at {path}")
            with open(path, "r", encoding="utf-8") as f:
                self._widget_catalog = json.load(f)
        return self._widget_catalog

    def get_global_capabilities(self) -> Dict[str, Any]:
        if self._global_capabilities is None:
            path = self.manifest_dir / "global_capabilities_reference.json"
            if not path.exists():
                raise ManifestError(f"Missing global capabilities reference at {path}")
            with open(path, "r", encoding="utf-8") as f:
                self._global_capabilities = json.load(f)
        return self._global_capabilities

    def resolve_tool(self, name_or_id: str) -> Optional[Dict[str, Any]]:
        """Resolves tool spec by either canonical namespaced id or legacy name."""
        self.get_domain_tools_manifest()
        return self._tool_by_id.get(name_or_id) or self._tool_by_legacy_name.get(name_or_id)

    def is_deprecated(self, name_or_id: str) -> bool:
        tool = self.resolve_tool(name_or_id)
        return bool(tool and tool.get("deprecated"))

    def get_effect(self, name_or_id: str) -> str:
        tool = self.resolve_tool(name_or_id)
        if tool:
            return tool.get("effect", "write")
        return "write"

manifest_registry = HTMLNotesManifestRegistry()
