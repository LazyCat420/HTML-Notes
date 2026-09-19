import json
import os
import pathlib
import pytest
from app.services.profile_service import get_html_notes_profile

def test_isolated_schema_and_profile_loading():
    """Verify that HTML-Notes tool contracts and profile definitions load
    without any dependency on external or sibling filesystem repositories."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    schema_path = repo_root / "app" / "schemas" / "tool-contract-v1.json"
    
    assert schema_path.exists(), "Versioned tool contract schema must be present in app/schemas/"
    
    with open(schema_path, "r", encoding="utf-8") as f:
        tools = json.load(f)
        
    tool_names = {t["name"] for t in tools}
    assert "canvas_add_widget" in tool_names
    assert "canvas_modify_dom" in tool_names
    
    # Assert profile loads with local contract definitions
    profile = get_html_notes_profile()
    assert profile["persona"] == "HTML_NOTES_ASSISTANT"
    assert len(profile["tools"]) >= 2
    assert any(t["name"] == "canvas_add_widget" for t in profile["tools"])

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
