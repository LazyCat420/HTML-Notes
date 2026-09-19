import json
import pathlib
from typing import Any, Dict, List, Optional

_MANIFEST_DIR = pathlib.Path(__file__).resolve().parent / "manifests"


class ManifestError(RuntimeError):
    """Raised when an application manifest is missing or malformed."""
    pass


class AliasRetiredError(ValueError):
    """Raised when an alias has passed its retirement date."""
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
        self._tool_by_alias: Dict[str, Dict[str, Any]] = {}
        self._canonical_by_alias: Dict[str, str] = {}

    def get_domain_tools_manifest(self) -> Dict[str, Any]:
        if self._domain_tools is None:
            path = self.manifest_dir / "html_notes.domain-tools.json"
            if not path.exists():
                raise ManifestError(f"Missing domain tools manifest at {path}")
            with open(path, "r", encoding="utf-8") as f:
                self._domain_tools = json.load(f)

            self._tool_by_id = {}
            self._tool_by_alias = {}
            self._canonical_by_alias = {}

            for tool in self._domain_tools.get("tools", []):
                t_id = tool.get("id")
                if t_id:
                    self._tool_by_id[t_id] = tool

                aliases = list(tool.get("legacy_aliases") or [])
                legacy = tool.get("legacy_name")
                if legacy and legacy not in aliases:
                    aliases.append(legacy)

                for alias in aliases:
                    self._tool_by_alias[alias] = tool
                    if t_id:
                        self._canonical_by_alias[alias] = t_id

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
        """Resolves tool spec by canonical namespaced id or registered alias."""
        self.get_domain_tools_manifest()
        return self._tool_by_id.get(name_or_id) or self._tool_by_alias.get(name_or_id)

    def resolve_alias_to_canonical(self, alias: str) -> Optional[str]:
        """Resolves a legacy alias to its single canonical tool ID."""
        self.get_domain_tools_manifest()
        if alias in self._tool_by_id:
            return alias
        return self._canonical_by_alias.get(alias)

    def is_retired(self, name_or_id: str, as_of_date: str = "2026-09-19") -> bool:
        """Checks if a tool or alias has passed its retirement date."""
        tool = self.resolve_tool(name_or_id)
        if not tool:
            return False
        if tool.get("retired"):
            return True
        retired_after = tool.get("retired_after")
        if retired_after and retired_after < as_of_date:
            return True
        return False

    def is_deprecated(self, name_or_id: str) -> bool:
        tool = self.resolve_tool(name_or_id)
        return bool(tool and tool.get("deprecated"))

    def get_effect(self, name_or_id: str) -> str:
        tool = self.resolve_tool(name_or_id)
        if tool:
            return tool.get("effect", "write")
        return "write"

    def get_required_scope(self, name_or_id: str) -> List[str]:
        tool = self.resolve_tool(name_or_id)
        if tool:
            return tool.get("required_scope", ["app_id"])
        return ["app_id"]

    def get_resource_type(self, name_or_id: str) -> Optional[str]:
        tool = self.resolve_tool(name_or_id)
        if tool:
            return tool.get("resource_type")
        return None


manifest_registry = HTMLNotesManifestRegistry()
