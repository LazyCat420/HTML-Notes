"""All agent traffic must target lazy-agent-service, never prism (2026-10-15).

These tests exercise the real code paths (the health check and the /models
route) over respx, so they fail if anything points back at :7777.
"""
import os

import httpx
import pytest
import respx

os.environ.setdefault("DATABASE_URL", "data/test_lazy_agent_target.db")

import app.main as m
from app import main as main_mod
from app.config import LAZY_AGENT_URL
from app.routes import message as message_route


def test_no_prism_url_remains():
    from app import config
    assert not hasattr(config, "PRISM_URL"), "PRISM_URL must be gone from config"


@respx.mock
@pytest.mark.asyncio
async def test_agent_dependency_status_targets_lazy_agent():
    route = respx.get(f"{LAZY_AGENT_URL}/config").respond(200, json={})
    status = await m._agent_dependency_status()
    assert status["ok"] is True
    assert status["gateway"] == LAZY_AGENT_URL
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_agent_dependency_status_reports_unreachable_gateway():
    respx.get(f"{LAZY_AGENT_URL}/config").respond(status_code=503)
    status = await m._agent_dependency_status()
    assert status["ok"] is False
    assert "503" in status["error"]


@respx.mock
@pytest.mark.asyncio
async def test_models_route_fetches_config_from_lazy_agent():
    route = respx.get(f"{LAZY_AGENT_URL}/config").respond(
        200, json={"textToText": {"models": {}}})
    await message_route.get_models()
    assert route.called


def test_agent_turn_targets_lazy_agent_only():
    """The turn handler must not branch on a removed Prism mode."""
    from tests._sources import MESSAGE_SRC
    assert "PRISM_URL" not in MESSAGE_SRC
    assert "use_lazy_agent" not in MESSAGE_SRC
    assert "target_url = LAZY_AGENT_URL" in MESSAGE_SRC
