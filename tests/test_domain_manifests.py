import inspect
import pytest
from app.tooling.html_notes_manifest import manifest_registry
from app.tooling.local_executor import local_tool_executor


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
    valid_domains = {"notes", "canvas", "portal", "apps", "watches", "widgets", "presentation_provider", "canvas_custom_quarantined"}

    for tool in tools:
        assert "id" in tool, f"Tool missing id: {tool}"
        assert tool["id"].startswith("html_notes."), f"Tool id not namespaced: {tool['id']}"
        assert "effect" in tool, f"Tool missing effect: {tool['id']}"
        assert tool["effect"] in valid_effects, f"Invalid effect {tool['effect']} on {tool['id']}"
        assert tool["domain"] in valid_domains, f"Invalid domain {tool['domain']} on {tool['id']}"
        assert "parameters" in tool or "input_schema" in tool, f"Tool missing parameters/schema: {tool['id']}"
        assert "description" in tool, f"Tool missing description: {tool['id']}"


def test_every_local_tool_has_owner_execution_effect_scope_and_version():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        assert t.get("owner") == "html-notes", f"{t['id']} owner is not html-notes"
        assert t.get("execution") == "local", f"{t['id']} execution is not local"
        assert t.get("effect") in ("read", "write", "destructive"), f"{t['id']} invalid effect"
        assert isinstance(t.get("required_scope"), (list, dict)) and len(t["required_scope"]) > 0, f"{t['id']} missing required_scope"
        assert t.get("version"), f"{t['id']} missing version"


def test_every_local_write_tool_requires_session_scope():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        if t.get("effect") in ("write", "destructive"):
            scope = t.get("required_scope")
            if isinstance(scope, dict):
                assert scope.get("session_id") is True, f"Write tool {t['id']} missing session_id in required_scope"
            else:
                assert "session_id" in scope, f"Write tool {t['id']} missing session_id in required_scope"


def test_all_local_manifest_tools_define_scope_and_receipt_policy():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        assert "required_scope" in t and len(t["required_scope"]) > 0, f"Tool {t['id']} missing required_scope"
        assert "requires_authorization_receipt" in t, f"Tool {t['id']} missing requires_authorization_receipt"
        if t.get("effect") in ("write", "destructive"):
            assert t["requires_authorization_receipt"] is True, f"Write/destructive tool {t['id']} must require receipt"


def test_every_destructive_tool_requires_confirmation():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        if t.get("effect") == "destructive":
            assert t.get("requires_confirmation") is True, f"Destructive tool {t['id']} must require confirmation"


def test_legacy_alias_resolves_to_exactly_one_canonical_tool():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        for alias in t.get("legacy_aliases", []):
            canonical = manifest_registry.resolve_alias_to_canonical(alias)
            assert canonical == t["id"], f"Alias {alias} resolved to {canonical}, expected {t['id']}"


def test_custom_widget_tool_is_not_exposed_in_default_profile():
    profile = manifest_registry.get_profile()
    whitelist = set(profile.get("tool_policy", {}).get("whitelist", []))
    forbidden = {
        "create_widget", "update_widget", "plan_widget",
        "html_notes.canvas.create_custom_widget",
        "html_notes.canvas.update_custom_widget",
        "html_notes.canvas.plan_custom_widget"
    }
    for tool in forbidden:
        assert tool not in whitelist, f"Custom widget tool '{tool}' must not be in default profile"


def test_manifest_tool_ids_match_executor_dispatch_table():
    source = inspect.getsource(local_tool_executor._dispatch)
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        t_id = t["id"]
        assert f'"{t_id}"' in source or f"'{t_id}'" in source, f"Tool {t_id} missing in LocalToolExecutor._dispatch"


def test_manifest_tool_ids_match_profile_permissions():
    profile = manifest_registry.get_profile()
    whitelist = profile.get("tool_policy", {}).get("whitelist", [])
    for perm in whitelist:
        if perm.startswith("global."):
            continue
        spec = manifest_registry.resolve_tool(perm)
        assert spec is not None, f"Profile permission '{perm}' is not defined in domain tools manifest"


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
