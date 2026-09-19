import pytest
from app.tooling.html_notes_manifest import manifest_registry

def test_manifest_registry_loads_all_manifests():
    domain_tools = manifest_registry.get_domain_tools_manifest()
    assert domain_tools["namespace"] == "html_notes"
    assert len(domain_tools["tools"]) >= 20

    profile = manifest_registry.get_profile()
    assert profile["profile_id"] == "html-notes-canvas-v1"
    assert profile["role"] == "canvas-research-coordinator"

    catalog = manifest_registry.get_widget_catalog()
    assert len(catalog["widgets"]) >= 25

    globals_ref = manifest_registry.get_global_capabilities()
    assert any(c["id"] == "global.web.search" for c in globals_ref["capabilities"])
    assert any(c["id"] == "global.web.read_page" for c in globals_ref["capabilities"])

def test_domain_tools_effect_and_domain_invariants():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    valid_effects = {"read", "write", "destructive"}
    valid_domains = {"notes", "canvas", "portal", "presentation_provider", "canvas_custom_quarantined"}

    for tool in tools:
        assert "id" in tool, f"Tool missing id: {tool}"
        assert tool["id"].startswith("html_notes."), f"Tool id not namespaced: {tool['id']}"
        assert "effect" in tool, f"Tool missing effect: {tool['id']}"
        assert tool["effect"] in valid_effects, f"Invalid effect {tool['effect']} on {tool['id']}"
        assert tool["domain"] in valid_domains, f"Invalid domain {tool['domain']} on {tool['id']}"
        assert "parameters" in tool, f"Tool missing parameters: {tool['id']}"
        assert "description" in tool, f"Tool missing description: {tool['id']}"

def test_profile_conforms_to_agent_profile_spec():
    profile = manifest_registry.get_profile()
    
    required_keys = [
        "profile_id", "version", "role", "description", "system_prompt",
        "model_constraints", "tool_policy", "budget_limits", "retention_class"
    ]
    for k in required_keys:
        assert k in profile, f"Profile missing required key: {k}"

    assert profile["model_constraints"]["default_model"]
    assert len(profile["model_constraints"]["allowed_models"]) > 0
    assert profile["tool_policy"]["mode"] == "STRICT_WHITELIST"
    assert len(profile["tool_policy"]["whitelist"]) > 10
    assert profile["budget_limits"]["max_tokens"] > 0
    assert profile["budget_limits"]["max_tool_calls"] > 0
    assert profile["retention_class"] in ["EPHEMERAL", "AUDITED_SESSION", "PERMANENT_RECORD"]

def test_manifest_tool_resolution_dual_dispatch():
    # Canonical resolution
    spec1 = manifest_registry.resolve_tool("html_notes.notes.create")
    assert spec1 is not None
    assert spec1["legacy_name"] == "html_notes_create_note"

    # Legacy resolution
    spec2 = manifest_registry.resolve_tool("html_notes_create_note")
    assert spec2 is not None
    assert spec2["id"] == "html_notes.notes.create"

    # Deprecation detection
    assert manifest_registry.is_deprecated("create_widget")
    assert not manifest_registry.is_deprecated("canvas_add_widget")
