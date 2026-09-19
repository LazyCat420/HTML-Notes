import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.runtime.models import (
    LocalToolAuthorization,
    ReplayCache,
    verify_local_authorization,
    verify_local_tool_scope,
    LocalExecutionContext,
)
from app.tooling.html_notes_manifest import manifest_registry
from app.tooling.local_executor import local_tool_executor
from app.tooling.policy import tool_policy


def make_valid_auth(
    tool_id: str = "html_notes.canvas.upsert_widget",
    session_id: str = "session_security_1",
    app_id: str = "html-notes",
    profile_id: str = "html-notes-canvas-v1",
    expires_delta_s: int = 300,
    signature: str = "sha256-valid-mock-signature",
) -> LocalToolAuthorization:
    now = datetime.now(timezone.utc)
    return LocalToolAuthorization(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
        canonical_tool_id=tool_id,
        profile_id=profile_id,
        app_id=app_id,
        session_id=session_id,
        issued_at=now,
        expires_at=now + timedelta(seconds=expires_delta_s),
        nonce=f"nonce_{uuid.uuid4().hex[:12]}",
        signature=signature,
    )


# 1. Scope Tests
def test_local_write_tool_missing_scope_is_rejected():
    ok, err = tool_policy.validate_scope(
        tool_name="html_notes.canvas.upsert_widget",
        session_id=None,
        app_id=None,
    )
    assert ok is False
    assert "requires" in err.lower()


def test_local_write_tool_missing_app_id_is_rejected():
    ok, err = tool_policy.validate_scope(
        tool_name="html_notes.canvas.upsert_widget",
        session_id="session_valid",
        app_id=None,
    )
    assert ok is False
    assert "requires app_id" in err.lower()

    # Wrong app_id
    ok_wrong, err_wrong = tool_policy.validate_scope(
        tool_name="html_notes.canvas.upsert_widget",
        session_id="session_valid",
        app_id="trading-client",
    )
    assert ok_wrong is False
    assert "html-notes" in err_wrong.lower()


def test_local_write_tool_missing_session_id_is_rejected():
    ok, err = tool_policy.validate_scope(
        tool_name="html_notes.canvas.upsert_widget",
        session_id=None,
        app_id="html-notes",
    )
    assert ok is False
    assert "requires session_id" in err.lower()


def test_local_read_session_resource_missing_scope_is_rejected():
    # Watches list or canvas state read are session-scoped read resources
    ok, err = tool_policy.validate_scope(
        tool_name="html_notes.watches.list",
        session_id=None,
        app_id="html-notes",
    )
    assert ok is False
    assert "requires session_id" in err.lower()


@pytest.mark.asyncio
async def test_missing_scope_never_reaches_domain_dispatch():
    with patch.object(local_tool_executor, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
        res = await local_tool_executor.execute(
            tool_name="html_notes.canvas.upsert_widget",
            args={"widget_type": "clock", "widget_id": "clk_1"},
            session_id=None,
            authorization=make_valid_auth(session_id="session_valid"),
        )
        assert res["success"] is False
        assert res["is_error"] is True
        assert res["code"] == "SCOPE_VIOLATION"
        mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_missing_scope_never_reaches_canvas_service():
    with patch("app.domain.canvas.service.canvas_service.upsert_widget") as mock_canvas:
        res = await local_tool_executor.execute(
            tool_name="html_notes.canvas.upsert_widget",
            args={"widget_type": "clock", "widget_id": "clk_1"},
            session_id=None,
            authorization=make_valid_auth(session_id="session_valid"),
        )
        assert res["success"] is False
        assert res["is_error"] is True
        mock_canvas.assert_not_called()


# 2. Authorization Receipt Tests
@pytest.mark.asyncio
async def test_local_write_requires_authorization_receipt():
    res = await local_tool_executor.execute(
        tool_name="html_notes.canvas.upsert_widget",
        args={"widget_type": "clock", "widget_id": "clk_1"},
        session_id="session_valid",
        authorization=None,
    )
    assert res["success"] is False
    assert res["is_error"] is True
    assert res["code"] == "MISSING_RECEIPT"


@pytest.mark.asyncio
async def test_local_read_requires_receipt_when_manifest_requires_it():
    # Mock a tool requiring receipt
    tool_spec = manifest_registry.resolve_tool("html_notes.canvas.read")
    assert tool_spec is not None

    with patch.object(manifest_registry, "resolve_tool") as mock_resolve:
        mock_spec = dict(tool_spec)
        mock_spec["requires_authorization_receipt"] = True
        mock_resolve.return_value = mock_spec

        res = await local_tool_executor.execute(
            tool_name="html_notes.canvas.read",
            args={},
            session_id="session_valid",
            authorization=None,
        )
        assert res["success"] is False
        assert res["is_error"] is True
        assert res["code"] == "MISSING_RECEIPT"


@pytest.mark.asyncio
async def test_missing_receipt_never_reaches_domain_dispatch():
    with patch.object(local_tool_executor, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
        res = await local_tool_executor.execute(
            tool_name="html_notes.canvas.upsert_widget",
            args={"widget_type": "clock", "widget_id": "clk_1"},
            session_id="session_valid",
            authorization=None,
        )
        assert res["success"] is False
        assert res["is_error"] is True
        mock_dispatch.assert_not_called()


def test_malformed_receipt_is_rejected():
    # Empty dictionary
    res_empty = verify_local_authorization(
        authorization={},
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
    )
    assert res_empty.valid is False
    assert res_empty.code in ("MALFORMED_RECEIPT", "MISSING_RUN_ID")

    # Missing tool call ID
    res_no_tcid = verify_local_authorization(
        authorization={
            "run_id": "run_1",
            "canonical_tool_id": "html_notes.canvas.upsert_widget",
            "app_id": "html-notes",
            "session_id": "session_1",
            "nonce": "nonce_1",
            "expires_at": "2030-01-01T00:00:00Z",
        },
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
    )
    assert res_no_tcid.valid is False
    assert res_no_tcid.code == "MISSING_TOOL_CALL_ID"


def test_receipt_tool_id_mismatch_is_rejected():
    auth = make_valid_auth(tool_id="html_notes.notes.create", session_id="session_1")
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
    )
    assert res.valid is False
    assert res.code == "TOOL_MISMATCH"


def test_receipt_profile_id_mismatch_is_rejected():
    auth = make_valid_auth(profile_id="unauthorized-profile", session_id="session_1")
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
        expected_profile_id="html-notes-canvas-v1",
    )
    assert res.valid is False
    assert res.code == "PROFILE_MISMATCH"


def test_receipt_app_id_mismatch_is_rejected():
    auth = make_valid_auth(app_id="trading-client", session_id="session_1")
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
    )
    assert res.valid is False
    assert res.code == "APP_MISMATCH"


def test_receipt_session_id_mismatch_is_rejected():
    auth = make_valid_auth(session_id="session_FOREIGN")
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_LOCAL",
    )
    assert res.valid is False
    assert res.code == "SESSION_MISMATCH"


def test_expired_receipt_is_rejected():
    past_time = datetime.now(timezone.utc) - timedelta(seconds=30)
    auth = make_valid_auth(session_id="session_1")
    auth.expires_at = past_time

    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
    )
    assert res.valid is False
    assert res.code == "EXPIRED_RECEIPT"


def test_replayed_receipt_is_rejected():
    cache = ReplayCache()
    auth = make_valid_auth(session_id="session_1")

    # 1. First execution passes
    res1 = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
        replay_cache=cache,
    )
    assert res1.valid is True

    # 2. Replayed receipt fails closed
    res2 = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
        replay_cache=cache,
    )
    assert res2.valid is False
    assert res2.code in ("REPLAYED_RECEIPT", "DUPLICATE_TOOL_CALL_ID")


def test_duplicate_tool_call_id_is_rejected():
    cache = ReplayCache()
    auth1 = make_valid_auth(session_id="session_1")
    auth2 = make_valid_auth(session_id="session_1")
    # Same tool_call_id, different nonce
    auth2.tool_call_id = auth1.tool_call_id

    res1 = verify_local_authorization(
        authorization=auth1,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
        replay_cache=cache,
    )
    assert res1.valid is True

    res2 = verify_local_authorization(
        authorization=auth2,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_1",
        replay_cache=cache,
    )
    assert res2.valid is False
    assert res2.code == "DUPLICATE_TOOL_CALL_ID"


# 3. Destructive Confirmation Tests
def test_destructive_tool_requires_confirmation():
    assert tool_policy.requires_confirmation("html_notes.apps.execute_action", {}) is True
    assert tool_policy.requires_confirmation("html_notes.canvas.upsert_widget", {}) is False


@pytest.mark.asyncio
async def test_destructive_tool_with_receipt_but_no_confirmation_is_rejected():
    auth = make_valid_auth(
        tool_id="html_notes.apps.execute_action",
        session_id="session_destruct",
    )
    res = await local_tool_executor.execute(
        tool_name="html_notes.apps.execute_action",
        args={"app_id": "test_app", "action": "purge_all"},
        session_id="session_destruct",
        authorization=auth,
        confirmation=False,
    )
    assert res["success"] is False
    assert res["is_error"] is True
    assert res["code"] == "CONFIRMATION_REQUIRED"
    assert res.get("confirmation_required") is True


# 4. Admission and Alias Invariants
@pytest.mark.asyncio
async def test_unknown_local_tool_is_rejected_before_dispatch():
    with patch.object(local_tool_executor, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
        res = await local_tool_executor.execute(
            tool_name="html_notes.unknown.nonexistent_tool",
            args={},
            session_id="session_1",
        )
        assert res["success"] is False
        assert res["is_error"] is True
        assert res["code"] in ("UNKNOWN_TOOL", "ADMISSION_DENIED")
        mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_retired_legacy_tool_returns_structured_retirement_error():
    res = await local_tool_executor.execute(
        tool_name="plan_widget",
        args={},
        session_id="session_1",
    )
    assert res["success"] is False
    assert res["is_error"] is True
    assert res["code"] == "TOOL_RETIRED"
    assert "retired" in res["error"].lower()


def test_legacy_alias_resolves_to_one_canonical_tool():
    canonical = manifest_registry.resolve_alias_to_canonical("canvas_add_widget")
    assert canonical == "html_notes.canvas.upsert_widget"

    canonical_dom = manifest_registry.resolve_alias_to_canonical("canvas_modify_dom")
    assert canonical_dom == "html_notes.canvas.mutate"


def test_all_local_manifest_tools_define_scope_and_receipt_policy():
    tools = manifest_registry.get_domain_tools_manifest()["tools"]
    for t in tools:
        t_id = t["id"]
        assert "required_scope" in t, f"Tool {t_id} missing required_scope"
        scope = t["required_scope"]
        assert isinstance(scope, (dict, list)), f"Tool {t_id} malformed required_scope"

        assert "requires_authorization_receipt" in t, f"Tool {t_id} missing requires_authorization_receipt"
        assert isinstance(t["requires_authorization_receipt"], bool), f"Tool {t_id} receipt flag not boolean"

        effect = t.get("effect")
        assert effect in ("read", "write", "destructive"), f"Tool {t_id} invalid effect {effect}"

        if effect in ("write", "destructive"):
            assert t["requires_authorization_receipt"] is True, f"Write tool {t_id} must require receipt"
            if isinstance(scope, dict):
                assert scope.get("session_id") is True, f"Write tool {t_id} missing session_id scope"
                assert scope.get("app_id") is True, f"Write tool {t_id} missing app_id scope"
            else:
                assert "session_id" in scope, f"Write tool {t_id} missing session_id scope"
                assert "app_id" in scope, f"Write tool {t_id} missing app_id scope"

        if effect == "destructive":
            assert t.get("requires_confirmation") is True, f"Destructive tool {t_id} must require confirmation"


def test_unsigned_receipt_is_rejected():
    auth = make_valid_auth()
    auth.signature = None
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
    )
    assert res.valid is False
    assert res.code == "UNSIGNED_RECEIPT"


def test_repeated_nonce_with_different_tool_call_id_is_rejected():
    cache = ReplayCache()
    auth1 = make_valid_auth(session_id="session_rep")
    auth2 = make_valid_auth(session_id="session_rep")
    # Same nonce, completely different tool_call_id
    auth2.nonce = auth1.nonce

    res1 = verify_local_authorization(
        authorization=auth1,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_rep",
        replay_cache=cache,
    )
    assert res1.valid is True

    res2 = verify_local_authorization(
        authorization=auth2,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_rep",
        replay_cache=cache,
    )
    assert res2.valid is False
    assert res2.code == "REPLAYED_RECEIPT"


def test_missing_profile_id_is_rejected():
    auth = make_valid_auth()
    auth.profile_id = ""
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
    )
    assert res.valid is False
    assert res.code == "MISSING_PROFILE"


def test_profile_id_mismatch_is_rejected():
    auth = make_valid_auth(profile_id="html-notes-canvas-v1")
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
        expected_profile_id="different-profile-v1",
    )
    assert res.valid is False
    assert res.code == "PROFILE_MISMATCH"


def test_run_id_mismatch_is_rejected():
    auth = make_valid_auth()
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
        expected_run_id="different_run_id_123",
    )
    assert res.valid is False
    assert res.code == "RUN_MISMATCH"


def test_tool_call_id_mismatch_is_rejected():
    auth = make_valid_auth()
    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
        expected_tool_call_id="different_tool_call_id_456",
    )
    assert res.valid is False
    assert res.code == "TOOL_CALL_MISMATCH"


def test_hmac_signature_verification_succeeds_with_matching_secret(monkeypatch):
    import hmac
    import hashlib
    import secrets
    test_secret = f"auth_{secrets.token_hex(16)}"
    monkeypatch.setenv("INTERNAL_EXECUTE_TOKEN", test_secret)

    auth = make_valid_auth()
    exp_iso_z = auth.expires_at.isoformat().replace("+00:00", "Z")
    payload = f"{auth.run_id}:{auth.tool_call_id}:{auth.canonical_tool_id}:{auth.app_id}:{auth.session_id}:{auth.profile_id}:{auth.nonce}:{exp_iso_z}"
    real_sig = "sha256-" + hmac.new(test_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    auth.signature = real_sig

    res = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
    )
    assert res.valid is True

    # Corrupt secret / forged signature fails
    auth.signature = "sha256-bad000000000000000000000000000000000000000000000000000000000dead"
    res_bad = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
    )
    assert res_bad.valid is False
    assert res_bad.code == "INVALID_SIGNATURE"


def test_real_runtime_receipt_shape_compatibility():
    """Validates that a nested authorization_receipt emitted by lazy-agent-service executes cleanly without MISSING_RUN_ID."""
    raw_runtime_event = {
        "tool_call_id": "call_rt_123",
        "tool_name": "html_notes.canvas.upsert_widget",
        "execution": "local",
        "effect": "write",
        "arguments": {"widget_type": "clock", "widget_id": "clk_1"},
        "authorization_receipt": {
            "receipt_id": "auth_rec_run_999_12345",
            "nonce": "nonce_abc123xyz",
            "run_id": "run_999",
            "tool_call_id": "call_rt_123",
            "tool_name": "html_notes.canvas.upsert_widget",
            "canonical_tool_id": "html_notes.canvas.upsert_widget",
            "arguments_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "app_id": "html-notes",
            "session_id": "session_rt_999",
            "profile_id": "html-notes-researcher-v1",
            "execution": "local",
            "effect": "write",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(),
            "signature": "sha256-valid-test-sig",
        },
        "required_scope": {
            "app_id": "html-notes",
            "session_id": "session_rt_999",
        },
    }

    # Pass nested authorization_receipt directly as adapter does
    nested_receipt = raw_runtime_event["authorization_receipt"]
    res = verify_local_authorization(
        authorization=nested_receipt,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id="session_rt_999",
        expected_profile_id="html-notes-researcher-v1",
        expected_run_id="run_999",
        expected_tool_call_id="call_rt_123",
    )
    assert res.valid is True
    assert res.code is None
    assert res.authorization is not None
    assert res.authorization.run_id == "run_999"
    assert res.authorization.tool_call_id == "call_rt_123"


def test_argument_tampering_is_rejected():
    """Validates that modifying tool arguments when arguments_hash is bound fails with ARGUMENTS_MISMATCH."""
    import hashlib
    import json

    original_args = {"widget_type": "clock", "widget_id": "clk_1"}
    sorted_orig = {k: original_args[k] for k in sorted(original_args.keys())}
    orig_hash = hashlib.sha256(json.dumps(sorted_orig, separators=(',', ':')).encode()).hexdigest()

    auth = make_valid_auth()
    auth.arguments_hash = orig_hash

    # Matching args passes
    res_ok = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
        expected_args=original_args,
    )
    assert res_ok.valid is True

    # Tampered args fails
    tampered_args = {"widget_type": "clock", "widget_id": "clk_FORGED"}
    res_tampered = verify_local_authorization(
        authorization=auth,
        expected_tool_id="html_notes.canvas.upsert_widget",
        expected_app_id="html-notes",
        expected_session_id=auth.session_id,
        expected_args=tampered_args,
    )
    assert res_tampered.valid is False
    assert res_tampered.code == "ARGUMENTS_MISMATCH"


def test_atomic_replay_cache_concurrency():
    """Validates thread-safe atomic check-and-add preventing concurrent race conditions for identical nonce."""
    import concurrent.futures
    cache = ReplayCache()
    test_key = "nonce:concurrent_race_test_1"

    success_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.check_and_add, test_key) for _ in range(20)]
        results = [f.result() for f in futures]
        success_count = sum(1 for r in results if r is True)

    # Exactly one thread must succeed in admitting the nonce
    assert success_count == 1


@pytest.mark.asyncio
async def test_link_notes_forwards_session_id():
    """Validates that executor dispatch for html_notes.notes.link passes session_id."""
    auth = make_valid_auth(tool_id="html_notes.notes.link", session_id="session_owner_1")
    with patch("app.domain.notes.service.notes_service.link_notes") as mock_link:
        mock_link.return_value = {"success": True, "source": "n1", "target": "n2"}
        res = await local_tool_executor.execute(
            tool_name="html_notes.notes.link",
            args={"source_note_id": "n1", "target_note_id": "n2"},
            session_id="session_owner_1",
            authorization=auth,
        )
        assert res["success"] is True
        mock_link.assert_called_once_with(
            source_note_id="n1",
            target_note_id="n2",
            session_id="session_owner_1",
        )
