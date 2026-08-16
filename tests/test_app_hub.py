"""App Hub: portal-service normalization, curation layering, name resolution,
the fast-lane regex, and the app_grid renderer.

All portal I/O is stubbed — these tests must pass with the NAS unplugged.
"""
import json
import os

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_notes_apphub.db")

import app.main as m
from app.services import portal
from app.widgets.factory import WIDGET_RENDERERS, render_app_grid


def _svc(**over):
    base = {
        "id": "music-player", "name": "Music Player",
        "description": "Music frontend", "projectType": "Service",
        "url": "http://10.0.0.16:3232", "port": 3232, "domain": None,
        "device": "Server", "healthy": True, "responseTimeMs": 12,
        "checkedAt": "2026-08-16T12:00:00Z", "dockerProject": "music-player",
        "repo": "https://github.com/x/y",
        # portal's `restartable` is INVERTED (true = NOT containerized); the
        # normalizer must never read it.
        "restartable": False,
    }
    base.update(over)
    return base


# ── normalization ────────────────────────────────────────────────────────────

def test_normalize_launch_url_prefers_domain():
    app = portal._normalize_portal_service(_svc(domain="music.example.com"))
    assert app["launch_url"] == "https://music.example.com"


def test_normalize_launch_url_falls_back_to_url():
    app = portal._normalize_portal_service(_svc(domain=None))
    assert app["launch_url"] == "http://10.0.0.16:3232"


def test_normalize_status_tristate():
    assert portal._normalize_portal_service(_svc())["status"] == "healthy"
    assert portal._normalize_portal_service(_svc(healthy=False))["status"] == "unhealthy"
    # checkedAt=None means NEVER checked — that is "unknown", not "unhealthy".
    assert portal._normalize_portal_service(_svc(checkedAt=None, healthy=False))["status"] == "unknown"


def test_normalize_container_signal_is_dockerproject_not_restartable():
    # restartable=True + dockerProject set would be portal's inverted reading;
    # has_container must come from dockerProject alone.
    app = portal._normalize_portal_service(_svc(restartable=True))
    assert app["has_container"] is True
    app = portal._normalize_portal_service(_svc(dockerProject=None, restartable=False))
    assert app["has_container"] is False


# ── curation layering ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_curation_precedence_overlay_beats_registry_file(monkeypatch):
    async def fake_raw():
        return {"services": [_svc()], "infrastructure": []}
    monkeypatch.setattr(portal, "_fetch_portal_raw", fake_raw)
    monkeypatch.setattr(portal, "_load_portal_registry", lambda: {
        "apps": {"music-player": {"pinned": True, "icon": "🎵"}},
        "defaults": {},
    })
    monkeypatch.setattr(portal, "_load_portal_overrides", lambda: {
        "music-player": {"pinned": False},
    })
    data = await portal.get_portal_apps()
    app = next(a for a in data["apps"] if a["id"] == "music-player")
    assert app["icon"] == "🎵"          # file layer applied
    assert app["pinned"] is False       # DB overlay wins over the file


@pytest.mark.asyncio
async def test_hidden_apps_dropped_unless_requested(monkeypatch):
    async def fake_raw():
        return {"services": [_svc(), _svc(id="lupos-bot", name="Lupos")],
                "infrastructure": []}
    monkeypatch.setattr(portal, "_fetch_portal_raw", fake_raw)
    monkeypatch.setattr(portal, "_load_portal_registry", lambda: {"apps": {}, "defaults": {}})
    monkeypatch.setattr(portal, "_load_portal_overrides",
                        lambda: {"lupos-bot": {"hidden": True}})
    shown = await portal.get_portal_apps()
    assert {a["id"] for a in shown["apps"]} == {"music-player"}
    everything = await portal.get_portal_apps(include_hidden=True)
    assert {a["id"] for a in everything["apps"]} == {"music-player", "lupos-bot"}


@pytest.mark.asyncio
async def test_infrastructure_excluded_by_default(monkeypatch):
    async def fake_raw():
        return {"services": [_svc()],
                "infrastructure": [_svc(id="mongodb", name="MongoDB",
                                        projectType="Database")]}
    monkeypatch.setattr(portal, "_fetch_portal_raw", fake_raw)
    monkeypatch.setattr(portal, "_load_portal_registry", lambda: {"apps": {}, "defaults": {}})
    monkeypatch.setattr(portal, "_load_portal_overrides", lambda: {})
    data = await portal.get_portal_apps()
    assert all(a["id"] != "mongodb" for a in data["apps"])


@pytest.mark.asyncio
async def test_portal_down_serves_nothing_but_flags_stale(monkeypatch):
    async def fake_raw():
        return None
    monkeypatch.setattr(portal, "_fetch_portal_raw", fake_raw)
    monkeypatch.setattr(portal, "_load_portal_registry", lambda: {"apps": {}, "defaults": {}})
    monkeypatch.setattr(portal, "_load_portal_overrides", lambda: {})
    data = await portal.get_portal_apps()
    assert data["stale"] is True and data["apps"] == []


# ── name resolution ──────────────────────────────────────────────────────────

_APPS = [
    {"id": "trading-client", "name": "Trading Client", "description": "UI"},
    {"id": "trading-service", "name": "Trading Service", "description": "engine"},
    {"id": "music-player", "name": "Music Player", "description": "music frontend"},
]


def test_resolve_exact_and_fuzzy():
    app, cands = portal.resolve_portal_app("music-player", _APPS)
    assert app["id"] == "music-player" and not cands
    app, cands = portal.resolve_portal_app("the music thing", _APPS)
    assert app["id"] == "music-player"


def test_resolve_refuses_ambiguity_with_candidates():
    app, cands = portal.resolve_portal_app("trading", _APPS)
    assert app is None
    assert {c["id"] for c in cands} == {"trading-client", "trading-service"}


def test_resolve_unknown_returns_nothing():
    app, cands = portal.resolve_portal_app("kerbal space program", _APPS)
    assert app is None and cands == []


# ── fast-lane regex ──────────────────────────────────────────────────────────

def test_app_hub_regex_positive_and_negative_controls():
    for text in ("show my apps", "app hub", "open the hub", "my services",
                 "what's running", "pull up the launcher", "my containers"):
        assert m.APP_HUB_INTENT_RE.search(text), text
    # "dashboard" means the CANVAS in this app; product-research asks must
    # never spawn the hub. (A miss here once hijacked "Modify my dashboard
    # tasks" away from the agent entirely.)
    for text in ("Modify my dashboard tasks", "add a task to my dashboard",
                 "best launcher for android", "best apps for photo editing",
                 "what services does AWS offer"):
        assert not m.APP_HUB_INTENT_RE.search(text), text


# ── renderer + registries ────────────────────────────────────────────────────

def test_app_grid_registered_everywhere():
    assert "app_grid" in WIDGET_RENDERERS
    assert m._CANVAS_CLASS_TYPE["app-grid-widget"] == "app_grid"
    assert m._CANVAS_XDATA_TYPE["appGridWidget"] == "app_grid"
    assert "app_grid" in m.SINGLETON_WIDGET_TYPES
    assert "app_grid" in m.REUSABLE_WIDGET_TYPES
    assert m._WIDGET_TYPE_ALIASES["app_hub"] == "app_grid"
    assert "apps" in m._CONTENT_KEYS
    assert {"html_notes_list_services", "html_notes_open_app",
            "html_notes_curate_app"} <= m._INTERNAL_EXECUTE_TOOLS
    assert "app_grid" in m.ROUTER_WIDGETS


def test_render_app_grid_smoke():
    html = render_app_grid("app-hub-t1", {"apps": [
        {"id": "a", "name": "A", "icon": "🅰", "launch_url": "http://a",
         "status": "healthy", "pinned": False, "hidden": False, "description": ""},
    ]})
    assert 'id="app-hub-t1"' in html
    assert "app-grid-widget" in html
    assert "appGridWidget(" in html
    # The baked config must round-trip through the x-data attribute escaping.
    assert "&quot;launch_url&quot;" in html
