import asyncio
import os
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.runtime.config import (
    RuntimeReadinessResult,
    check_runtime_readiness,
    is_contract_compatible,
    load_local_profile_manifest,
    parse_semver,
    should_enforce_readiness,
    validate_capabilities,
    validate_profile_against_manifest,
    validate_runtime_url,
)
from app.services.runtime_chat_adapter import RuntimeChatAdapter


def test_runtime_client_uses_configured_base_url(monkeypatch):
    test_url = "http://10.0.0.99:8888"
    monkeypatch.setenv("LAZYCAT_RUNTIME_URL", test_url)
    adapter = RuntimeChatAdapter()
    client = adapter._get_client()
    assert client.base_url.startswith(test_url)
    assert adapter.runtime_url == test_url


def test_runtime_client_uses_configured_connect_timeout(monkeypatch):
    monkeypatch.setenv("RUNTIME_CONNECT_TIMEOUT_SECONDS", "12.5")
    adapter = RuntimeChatAdapter()
    client = adapter._get_client()
    assert client.connect_timeout == 12.5
    assert getattr(client.timeout, "connect", None) == 12.5


def test_runtime_client_uses_configured_read_timeout(monkeypatch):
    monkeypatch.setenv("RUNTIME_READ_TIMEOUT_SECONDS", "120.0")
    adapter = RuntimeChatAdapter()
    client = adapter._get_client()
    assert client.read_timeout == 120.0
    assert getattr(client.timeout, "read", None) == 120.0


@pytest.mark.asyncio
async def test_invalid_runtime_url_returns_readiness_failure():
    res = await check_runtime_readiness(runtime_url="not-a-url")
    assert not res.is_ready
    assert "Invalid runtime URL" in (res.error or "")
    assert res.details.get("phase") == "url_validation"


def test_feature_flag_false_does_not_require_runtime_readiness(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "false")
    assert not should_enforce_readiness()


def test_feature_flag_true_requires_runtime_readiness(monkeypatch):
    monkeypatch.setenv("USE_SHARED_RUNTIME", "true")
    assert should_enforce_readiness()


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_unreachable_runtime():
    # Use an unroutable port/ip
    res = await check_runtime_readiness(
        runtime_url="http://127.0.0.1:59999",
        connect_timeout=0.1,
        read_timeout=0.1,
        manifest_override={"profile_id": "test-prof", "allowed_global_capabilities": ["global.web.search", "global.web.read_page"]},
        profile_id="test-prof"
    )
    assert not res.is_ready
    assert "unreachable" in (res.error or "").lower() or "connection" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_contract_major_mismatch():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"version": "2.0.0"}
    mock_resp.headers = {"x-contract-version": "2.0.0"}
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = await check_runtime_readiness(
        runtime_url="http://fake-runtime:8080",
        profile_id="html-notes-canvas-v1",
        required_contract_version="1.2.0",
        manifest_override={"profile_id": "html-notes-canvas-v1", "allowed_global_capabilities": ["global.web.search", "global.web.read_page"]},
        client=mock_client
    )
    assert not res.is_ready
    assert "major version" in (res.error or "").lower()
    assert res.details.get("phase") == "contract_major_mismatch"


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_older_minor_contract():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"version": "1.1.0"}
    mock_resp.headers = {"x-contract-version": "1.1.0"}
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = await check_runtime_readiness(
        runtime_url="http://fake-runtime:8080",
        profile_id="html-notes-canvas-v1",
        required_contract_version="1.2.0",
        manifest_override={"profile_id": "html-notes-canvas-v1", "allowed_global_capabilities": ["global.web.search", "global.web.read_page"]},
        client=mock_client
    )
    assert not res.is_ready
    assert "older" in (res.error or "").lower() or "minor version" in (res.error or "").lower()
    assert res.details.get("phase") == "contract_minor_mismatch"


@pytest.mark.asyncio
async def test_runtime_preflight_accepts_compatible_contract_version():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"version": "1.2.0"}
    mock_resp.headers = {"x-contract-version": "1.2.0"}
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = await check_runtime_readiness(
        runtime_url="http://fake-runtime:8080",
        profile_id="html-notes-canvas-v1",
        required_contract_version="1.2.0",
        manifest_override={"profile_id": "html-notes-canvas-v1", "allowed_global_capabilities": ["global.web.search", "global.web.read_page"]},
        client=mock_client
    )
    assert res.is_ready
    assert res.contract_version == "1.2.0"


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_missing_profile():
    res = await check_runtime_readiness(profile_id="")
    assert not res.is_ready
    assert "Missing profile_id" in (res.error or "")


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_unregistered_profile():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "version": "1.2.0",
        "registered_profiles": ["other-profile-v1"]
    }
    mock_resp.headers = {"x-contract-version": "1.2.0"}
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = await check_runtime_readiness(
        runtime_url="http://fake-runtime:8080",
        profile_id="unregistered-canvas-prof",
        required_contract_version="1.2.0",
        manifest_override={"profile_id": "unregistered-canvas-prof", "allowed_global_capabilities": ["global.web.search", "global.web.read_page"]},
        client=mock_client
    )
    assert not res.is_ready
    assert "not registered" in (res.error or "").lower()
    assert res.details.get("phase") == "unregistered_profile"


def test_configured_profile_matches_local_manifest():
    manifest = load_local_profile_manifest()
    assert manifest is not None
    declared_id = manifest.get("profile_id")
    ok, err = validate_profile_against_manifest(declared_id, manifest)
    assert ok
    assert err is None


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_profile_manifest_mismatch():
    manifest = {"profile_id": "html-notes-canvas-v1"}
    res = await check_runtime_readiness(
        profile_id="mismatched-profile-id",
        manifest_override=manifest
    )
    assert not res.is_ready
    assert "does not match declared profile" in (res.error or "")
    assert res.details.get("phase") == "profile_manifest_mismatch"


@pytest.mark.asyncio
async def test_runtime_preflight_reports_missing_capability():
    # Manifest missing global.web.read_page
    incomplete_manifest = {
        "profile_id": "test-prof",
        "tool_policy": {
            "whitelist": ["global.web.search"]
        }
    }
    res = await check_runtime_readiness(
        profile_id="test-prof",
        manifest_override=incomplete_manifest
    )
    assert not res.is_ready
    assert "missing required capability" in (res.error or "").lower()
    assert "global.web.read_page" in (res.error or "")
    assert res.details.get("phase") == "capability_check"
