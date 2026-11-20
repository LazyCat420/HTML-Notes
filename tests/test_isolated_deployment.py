import json
import os
import pathlib
from app.tooling.html_notes_manifest import manifest_registry

def test_isolated_schema_and_profile_loading():
    """Verify that HTML-Notes tool contracts and local manifests load
    without any dependency on external or sibling filesystem repositories."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    schema_path = repo_root / "app" / "schemas" / "tool-contract-v1.json"

    assert schema_path.exists(), "Versioned tool contract schema must be present in app/schemas/"

    with open(schema_path, "r", encoding="utf-8") as f:
        tools = json.load(f)

    tool_names = {t["name"] for t in tools}
    assert "canvas_add_widget" in tool_names
    assert "canvas_modify_dom" in tool_names

    profile = manifest_registry.get_profile()
    domain_tools = manifest_registry.get_domain_tools_manifest()
    assert profile.get("profile_id") or profile.get("id"), "local profile manifest must declare id"
    assert len(domain_tools.get("tools") or []) >= 2

def test_isolated_environment_no_sibling_access(monkeypatch):
    """Ensure that setting an empty or non-existent LAZY_AGENT_SERVICE_DIR
    does not break schema or profile resolution."""
    monkeypatch.delenv("LAZY_AGENT_SERVICE_DIR", raising=False)
    
    from tests.test_tool_schema_enum import _flat_schema_path, _live_enum
    
    schema_file = _flat_schema_path()
    assert schema_file is not None
    assert "lazy-agent-service" not in str(schema_file.resolve()).split(os.sep) or "app/schemas" in str(schema_file)
    
    enum, path = _live_enum()
    assert "notes" in enum
    assert "data_card" in enum
