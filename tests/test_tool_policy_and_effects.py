import pytest
from app.tooling.policy import tool_policy, HTMLNotesToolPolicy

def test_tool_admission_whitelisted_and_unwhitelisted():
    # Canonical name
    admitted, err = tool_policy.check_admission("html_notes.notes.create")
    assert admitted is True
    assert err is None

    # Legacy name
    admitted, err = tool_policy.check_admission("canvas_add_widget")
    assert admitted is True
    assert err is None

    # Global capability
    admitted, err = tool_policy.check_admission("global.web.search")
    assert admitted is True
    assert err is None

    # Disallowed / Unknown tool
    admitted, err = tool_policy.check_admission("arbitrary_bash_exec")
    assert admitted is False
    assert "not permitted" in err

def test_tool_effect_classification():
    assert tool_policy.registry.get_effect("html_notes.notes.get") == "read"
    assert tool_policy.registry.get_effect("html_notes.notes.create") == "write"
    assert tool_policy.registry.get_effect("html_notes.portal.execute_action") == "write"

def test_destructive_confirmation_requirement():
    # Read/regular write does not require confirmation
    assert tool_policy.requires_confirmation("html_notes.notes.create", {}) is False
    assert tool_policy.requires_confirmation("canvas_add_widget", {}) is False

    # Spec for execute_action requires confirmation
    spec = tool_policy.registry.resolve_tool("html_notes.portal.execute_action")
    assert spec["requires_confirmation"] is True

def test_argument_validation():
    # Valid arguments
    valid, err = tool_policy.validate_args("html_notes.notes.create", {"title": "Test", "rendered_html": "<p>Hi</p>"})
    assert valid is True
    assert err is None

    # Missing required argument 'title'
    valid, err = tool_policy.validate_args("html_notes.notes.create", {"rendered_html": "<p>Hi</p>"})
    assert valid is False
    assert "Missing required parameter 'title'" in err
