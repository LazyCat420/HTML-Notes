import pytest
from app.tooling.policy import tool_policy, HTMLNotesToolPolicy
from app.tooling.html_notes_manifest import manifest_registry


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
    assert manifest_registry.get_effect("html_notes.notes.get") == "read"
    assert manifest_registry.get_effect("html_notes.notes.create") == "write"
    assert manifest_registry.get_effect("html_notes.apps.execute_action") == "destructive"
    assert manifest_registry.get_effect("html_notes.canvas.upsert_widget") == "write"
    assert manifest_registry.get_effect("html_notes.widgets.list_catalog") == "read"


def test_destructive_confirmation_requirement():
    # Read/regular write does not require confirmation
    assert tool_policy.requires_confirmation("html_notes.notes.create", {}) is False
    assert tool_policy.requires_confirmation("html_notes.canvas.upsert_widget", {}) is False

    # Destructive action requires confirmation
    assert tool_policy.requires_confirmation("html_notes.apps.execute_action", {}) is True


def test_argument_validation():
    # Valid arguments
    valid, err = tool_policy.validate_args("html_notes.notes.create", {"title": "Test", "rendered_html": "<p>Hi</p>"})
    assert valid is True
    assert err is None

    # Missing required argument 'title'
    valid, err = tool_policy.validate_args("html_notes.notes.create", {"rendered_html": "<p>Hi</p>"})
    assert valid is False
    assert "Missing required parameter 'title'" in err


def test_removed_alias_is_rejected_after_retirement_date():
    # plan_widget is retired after 2026-09-01
    assert manifest_registry.is_retired("plan_widget") is True
    admitted, err = tool_policy.check_admission("plan_widget")
    assert admitted is False
    assert "retired" in err.lower()


def test_unknown_alias_is_rejected():
    resolved = manifest_registry.resolve_alias_to_canonical("unknown_bogus_alias_xyz")
    assert resolved is None

    admitted, err = tool_policy.check_admission("unknown_bogus_alias_xyz")
    assert admitted is False
    assert "not permitted" in err.lower()


def test_tool_schema_validation_fails_before_domain_execution():
    # Missing required fields
    valid, err = tool_policy.validate_args("html_notes.canvas.upsert_widget", {"widget_type": "clock"})
    assert valid is False
    assert "widget_id" in err

    # Wrong data type for parameter
    valid, err = tool_policy.validate_args("html_notes.canvas.upsert_widget", {"widget_type": 123, "widget_id": "w1"})
    assert valid is False
    assert "must be a string" in err


def test_arbitrary_dom_mutation_is_not_available_by_default():
    # Unconstrained selector like 'body' or '.container' should fail
    safe, err = tool_policy.validate_safety("html_notes.canvas.mutate", {"action": "replace", "selector": "div.content", "html": "<p>hi</p>"})
    assert safe is False
    assert "exact #id" in err or "Arbitrary DOM mutation is not available by default" in err

    # Exact #id selector passes
    safe, err = tool_policy.validate_safety("html_notes.canvas.mutate", {"action": "replace", "selector": "#target_widget", "html": "<p>hi</p>"})
    assert safe is True


def test_untrusted_html_is_sanitized():
    dirty = "<article><h1>Notes Title</h1><script>alert('xss')</script><p onerror='malicious()'>Safe paragraph</p></article>"
    clean = tool_policy.sanitize_html(dirty)
    assert "<script" not in clean
    assert "onerror" not in clean
    assert "Notes Title" in clean
    assert "Safe paragraph" in clean


def test_untrusted_javascript_is_rejected_or_quarantined():
    # Script tag injection in note HTML
    safe, err = tool_policy.validate_safety("html_notes.notes.create", {
        "title": "Malicious",
        "rendered_html": "<article><script>alert('pwn')</script></article>"
    })
    assert safe is False
    assert "Untrusted JavaScript" in err

    # Inline event handler injection
    safe, err = tool_policy.validate_safety("html_notes.notes.create", {
        "title": "Malicious",
        "rendered_html": "<article><img src=x onerror=alert(1) /></article>"
    })
    assert safe is False
    assert "inline event handlers" in err


def test_app_action_destructive_operation_requires_confirmation():
    # Spec-level destructive tool
    assert tool_policy.requires_confirmation("html_notes.apps.execute_action", {"app_id": "test_app", "action": "any"}) is True
