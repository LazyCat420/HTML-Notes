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


@pytest.fixture(autouse=True)
def _stub_docker_containers(monkeypatch):
    """Offline test isolation: portal._fetch_docker_containers_raw defaults to empty list."""
    async def _empty_docker():
        return []
    monkeypatch.setattr(portal, "_fetch_docker_containers_raw", _empty_docker)


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


def test_resolve_prefers_the_client_over_its_service_sibling():
    """Superseded the old 'trading is ambiguous' assertion (2026-08-16): a
    human open ALWAYS means the client, so a client-vs-service tie resolves
    instead of prompting. The backend is still reachable by full name."""
    app, _ = portal.resolve_portal_app("trading", _APPS)
    assert app["id"] == "trading-client"
    app, _ = portal.resolve_portal_app("trading service", _APPS)
    assert app["id"] == "trading-service"


def test_resolve_refuses_ambiguity_between_two_clients():
    apps = _APPS + [
        {"id": "trading-lab", "name": "Trading Lab", "description": "research UI"},
    ]
    app, cands = portal.resolve_portal_app("trading", apps)
    assert app is None
    assert {c["id"] for c in cands} == {"trading-client", "trading-lab"}


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


# ── open-app fast lane ───────────────────────────────────────────────────────

def test_extract_open_app_target_reports_signals_without_rejecting():
    # Signature is (name, explicit_marker, has_widget_noun). The extractor no
    # longer REJECTS widget-noun names — precedence is the caller's job, which
    # is what lets an exact app name beat a widget.
    assert m.extract_open_app_target("open the trading client") == ("trading client", False, False)
    assert m.extract_open_app_target("open the music player app") == ("music player", True, True)
    assert m.extract_open_app_target("open the music player") == ("music player", False, True)
    assert m.extract_open_app_target("open my notes") == ("notes", False, True)


def test_extract_open_app_target_shape_guards():
    assert m.extract_open_app_target("what is the trading client") is None
    assert m.extract_open_app_target(
        "open the door to a discussion about how trading clients work in general and why") is None
    assert m.extract_open_app_target("open") is None
    assert m.extract_open_app_target("play some jazz") is None


# ── resolution tiers ─────────────────────────────────────────────────────────

_CATALOG = [
    {"id": "trading-client", "name": "Trading Client", "description": "UI",
     "project_type": "Client", "aliases": ["trading bot"], "launch_url": "http://x:3030"},
    {"id": "trading-service", "name": "Trading Service", "description": "engine",
     "project_type": "Service", "aliases": [], "launch_url": "http://x:3031"},
    {"id": "music-player", "name": "Music Player", "description": "music frontend",
     # NOTE: portal really does report projectType "Service" for music-player —
     # this fixture preserves that so the clients-first filter is tested
     # against the real shape, not a convenient one.
     "project_type": "Service", "aliases": ["music app"], "launch_url": "http://x:3232"},
    {"id": "portal-client", "name": "Portal Client", "description": "dashboard",
     "project_type": "Client", "aliases": [], "launch_url": "http://x:4000"},
    {"id": "portal-service", "name": "Portal Service", "description": "inventory",
     "project_type": "Service", "aliases": [], "launch_url": "http://x:4001"},
]


def test_exact_name_beats_everything_music_player():
    """The bug that prompted this fix: 'music player' is an APP name, so it
    must resolve even though 'music' is also a widget word."""
    app, _ = portal.resolve_portal_app("music player", _CATALOG, strict=True)
    assert app["id"] == "music-player"


def test_exact_match_is_normalized():
    for q in ("Trading Client", "trading-client", "trading  client"):
        app, _ = portal.resolve_portal_app(q, _CATALOG, strict=True)
        assert app["id"] == "trading-client", q


def test_alias_resolves_trading_bot():
    app, _ = portal.resolve_portal_app("trading bot", _CATALOG, strict=True)
    assert app["id"] == "trading-client"
    # and via the fuzzy path the agent tool uses
    app, _ = portal.resolve_portal_app("the trading bot", _CATALOG, strict=False)
    assert app["id"] == "trading-client"


def test_clients_first_collapses_client_service_ties():
    # "trading" matches BOTH trading-client and trading-service; the user
    # never means the backend, so this must resolve, not ask.
    app, _ = portal.resolve_portal_app("trading", _CATALOG, strict=True)
    assert app["id"] == "trading-client"
    app, _ = portal.resolve_portal_app("portal", _CATALOG, strict=True)
    assert app["id"] == "portal-client"


def test_backend_still_reachable_by_full_name():
    app, _ = portal.resolve_portal_app("trading service", _CATALOG, strict=True)
    assert app["id"] == "trading-service"


def test_is_backend_ignores_projecttype():
    """Negative control for the trap that would have re-broken music-player:
    portal marks it projectType 'Service', but it is a standalone front end."""
    music = next(a for a in _CATALOG if a["id"] == "music-player")
    assert music["project_type"] == "Service"      # the real, misleading value
    assert portal.is_backend_app(music) is False   # ...and we do not believe it
    assert portal.is_backend_app(
        next(a for a in _CATALOG if a["id"] == "trading-service")) is True


def test_ambiguity_survives_only_for_client_vs_client():
    catalog = _CATALOG + [
        {"id": "prism-client", "name": "Prism Client", "description": "",
         "project_type": "Client", "aliases": [], "launch_url": "http://x:3333"},
        {"id": "prism-console", "name": "Prism Console", "description": "",
         "project_type": "Client", "aliases": [], "launch_url": "http://x:3334"},
    ]
    app, cands = portal.resolve_portal_app("prism", catalog, strict=True)
    assert app is None
    assert {c["id"] for c in cands} == {"prism-client", "prism-console"}


def test_strict_still_refuses_description_matches():
    app, _ = portal.resolve_portal_app("engine", _CATALOG, strict=True)
    assert app is None


@pytest.mark.asyncio
async def test_apps_prompt_block_lists_ids_and_aliases(monkeypatch):
    async def fake_apps(include_hidden=False):
        return {"apps": _CATALOG[:2], "stale": False, "count": 2}
    monkeypatch.setattr(portal, "get_portal_apps", fake_apps)
    block = await portal.build_apps_prompt_block()
    assert "YOUR APPS" in block
    assert "trading-client — Trading Client (aka trading bot)" in block


@pytest.mark.asyncio
async def test_apps_prompt_block_never_raises(monkeypatch):
    async def boom(include_hidden=False):
        raise RuntimeError("portal down")
    monkeypatch.setattr(portal, "get_portal_apps", boom)
    assert await portal.build_apps_prompt_block() == ""


def test_registry_aliases_are_not_bare_widget_words():
    """A greedy alias like 'music' or 'notes' would steal every widget ask."""
    reg = portal._load_portal_registry()
    for app_id, entry in (reg.get("apps") or {}).items():
        for alias in entry.get("aliases") or []:
            assert alias.lower() not in m._OPEN_APP_WIDGET_NOUNS, f"{app_id}: {alias}"


# ── bare app names (no verb) ─────────────────────────────────────────────────

def test_extract_bare_app_name_accepts_plain_names():
    assert m.extract_bare_app_name("music player") == "music player"
    assert m.extract_bare_app_name("trading bot") == "trading bot"
    assert m.extract_bare_app_name("the portal") == "portal"
    assert m.extract_bare_app_name("my trading client") == "trading client"


def test_extract_bare_app_name_rejects_prose_and_questions():
    """The shape guard. A bare phrase can launch a browser tab, so anything
    that reads like conversation, a question or another intent must not
    reach the catalog at all."""
    for text in ("play some lofi hip hop", "is trading down?",
                 "what is the trading client", "show my apps",
                 "add milk to the list", "how does the music player work",
                 "tell me about prism", "close the music player",
                 "the trading client keeps dropping connections mid cycle"):
        assert m.extract_bare_app_name(text) is None, text


def test_bare_name_requires_a_whole_name_match():
    """exact_only: a bare phrase may only match a FULL app name/alias.
    'trading' alone must not open a tab when no verb was typed — otherwise
    prose containing a partial name would launch things."""
    app, _ = portal.resolve_portal_app("trading", _CATALOG, strict=True, exact_only=True)
    assert app is None
    app, _ = portal.resolve_portal_app("music player", _CATALOG, strict=True, exact_only=True)
    assert app["id"] == "music-player"
    app, _ = portal.resolve_portal_app("trading bot", _CATALOG, strict=True, exact_only=True)
    assert app["id"] == "trading-client"


def test_registry_overrides_the_dead_music_domain():
    """Regression guard for a REAL outage: portal hands out
    https://music.braindeadbot.com for music-player (vault sets that domain
    and portal prefers domain over url), but the domain has no DNS record —
    every open landed on a dead tab. The curation layer pins the reachable
    :3232 instead."""
    reg = portal._load_portal_registry()
    mp = (reg.get("apps") or {}).get("music-player") or {}
    assert mp.get("launch_url") == "http://10.0.0.16:3232"
    assert "braindeadbot" not in mp.get("launch_url", "")


# ── control plane (app_action) ───────────────────────────────────────────────

from app.services import app_actions as A


def test_registry_loads_and_every_action_is_well_formed():
    rows = A.list_app_actions()
    assert rows, "registry must not be empty"
    for r in rows:
        spec = A.get_action_spec(r["app_id"], r["action"])
        assert spec.get("url", "").startswith("http"), r
        assert spec.get("method", "GET").upper() in ("GET", "POST", "PUT", "DELETE"), r
        assert r["description"], f"{r['app_id']}.{r['action']} needs a description"


def test_money_and_job_actions_are_marked_destructive():
    """The confirm gate is only as good as these flags. A cycle places orders;
    a stop interrupts one; strain enrichment is a heavy job."""
    for app_id, action in (("trading-client", "start_cycle"),
                           ("trading-client", "stop_cycle"),
                           ("treesearch-service", "enrich_strains")):
        assert A.get_action_spec(app_id, action)["destructive"] is True, (app_id, action)
    # ...and reads must NOT be, or every status check would nag for a click.
    for app_id, action in (("trading-client", "cycle_status"),
                           ("youtube-wallgarden", "suggest_videos"),
                           ("youtube-wallgarden", "user_state"),
                           ("music-player", "record_play")):
        assert A.get_action_spec(app_id, action)["destructive"] is False, (app_id, action)


def test_body_template_drops_optionals_and_enforces_required():
    tpl = {"a": "$opt", "b": "$req!", "c": "literal"}
    assert A._render_body(tpl, {"req": 1}) == {"b": 1, "c": "literal"}
    assert A._render_body(tpl, {"req": 1, "opt": 2}) == {"a": 2, "b": 1, "c": "literal"}
    with pytest.raises(ValueError):
        A._render_body(tpl, {})


def test_pending_action_runs_once_then_is_consumed():
    """A double-click must not start two trading cycles."""
    pid = A.park_pending_action("trading-client", "start_cycle", {"tickers": ["NVDA"]})
    assert A.get_pending_action(pid)["action"] == "start_cycle"
    A._pending_actions.pop(pid)              # simulate the run consuming it
    assert A.get_pending_action(pid) is None


def test_expired_pending_action_is_refused():
    pid = A.park_pending_action("trading-client", "start_cycle", {})
    A._pending_actions[pid]["created"] -= (A._PENDING_TTL + 10)
    assert A.get_pending_action(pid) is None


@pytest.mark.asyncio
async def test_unknown_action_never_reaches_the_network():
    res = await A.execute_app_action("trading-client", "delete_everything", {})
    assert res["is_error"] and "Unknown action" in res["error"]


@pytest.mark.asyncio
async def test_actions_prompt_block_flags_destructive():
    block = await A.build_actions_prompt_block()
    assert "YOU CAN DO" in block
    assert "trading-client.start_cycle [CONFIRM REQUIRED]" in block
    assert "cycle_status [CONFIRM REQUIRED]" not in block


def test_action_confirm_registered_everywhere():
    from app.widgets.factory import WIDGET_RENDERERS
    assert "action_confirm" in WIDGET_RENDERERS
    assert m._CANVAS_XDATA_TYPE["actionConfirmWidget"] == "action_confirm"
    assert m._CANVAS_CLASS_TYPE["action-confirm-widget"] == "action_confirm"
    assert {"html_notes_app_action", "html_notes_list_actions"} <= m._INTERNAL_EXECUTE_TOOLS


# ── dynamic docker container discovery & opening ─────────────────────────────

def test_normalize_docker_container_with_web_port():
    ctr = {
        "name": "pinball-knight",
        "state": "running",
        "device": "server",
        "ports": [{"publicPort": 5173, "type": "tcp"}],
    }
    app = portal._normalize_docker_container(ctr, default_host="10.0.0.16")
    assert app is not None
    assert app["id"] == "pinball-knight"
    assert app["name"] == "Pinball Knight"
    assert app["launch_url"] == "http://10.0.0.16:5173"
    assert app["status"] == "healthy"
    assert app["has_container"] is True
    assert "pinball knight" in [a.lower() for a in app["aliases"]]


def test_normalize_docker_container_strips_compose_prefix_and_replica():
    ctr = {
        "name": "sun_drift-king-service_1",
        "state": "running",
        "device": "server",
        "ports": [{"publicPort": 5580, "type": "tcp"}],
    }
    app = portal._normalize_docker_container(ctr, default_host="10.0.0.16")
    assert app is not None
    assert app["id"] == "drift-king-service"
    assert app["name"] == "Drift King Service"
    assert app["launch_url"] == "http://10.0.0.16:5580"
    assert "drift-king" in app["aliases"] or "drift king" in app["aliases"]


def test_normalize_docker_container_port_priority():
    ctr = {
        "name": "multi-port-app",
        "state": "running",
        "device": "server",
        "ports": [
            {"publicPort": 2222, "type": "tcp"},
            {"publicPort": 8080, "type": "tcp"},
        ],
    }
    app = portal._normalize_docker_container(ctr, default_host="10.0.0.16")
    assert app is not None
    assert app["launch_url"] == "http://10.0.0.16:8080"


def test_normalize_docker_container_without_web_port():
    ctr = {
        "name": "postgres-service",
        "state": "running",
        "device": "server",
        "ports": [],
    }
    app = portal._normalize_docker_container(ctr, default_host="10.0.0.16")
    assert app is not None
    assert app["id"] == "postgres-service"
    assert app["launch_url"] == ""


@pytest.mark.asyncio
async def test_get_portal_apps_merges_docker_containers(monkeypatch):
    async def fake_services():
        return {"services": [_svc()], "infrastructure": []}
    async def fake_docker():
        return [
            {"name": "pinball-knight", "state": "running", "device": "server",
             "ports": [{"publicPort": 5173, "type": "tcp"}]},
        ]
    monkeypatch.setattr(portal, "_fetch_portal_raw", fake_services)
    monkeypatch.setattr(portal, "_fetch_docker_containers_raw", fake_docker)
    monkeypatch.setattr(portal, "_load_portal_registry", lambda: {"apps": {}, "defaults": {}})
    monkeypatch.setattr(portal, "_load_portal_overrides", lambda: {})

    data = await portal.get_portal_apps()
    app_ids = {a["id"] for a in data["apps"]}
    assert "music-player" in app_ids
    assert "pinball-knight" in app_ids

    pk = next(a for a in data["apps"] if a["id"] == "pinball-knight")
    assert pk["launch_url"] == "http://10.0.0.16:5173"
    assert pk["has_container"] is True


def test_resolve_dynamic_docker_container():
    catalog = [
        {"id": "pinball-knight", "name": "Pinball Knight", "description": "",
         "project_type": "Client", "aliases": ["pinball knight"], "launch_url": "http://10.0.0.16:5173"},
        {"id": "drift-king-service", "name": "Drift King Service", "description": "",
         "project_type": "Service", "aliases": ["drift-king", "drift king"], "launch_url": "http://10.0.0.16:5580"},
    ]
    # Kebab-case exact match
    app, _ = portal.resolve_portal_app("pinball-knight", catalog, strict=True)
    assert app["id"] == "pinball-knight"

    # Spaced words match
    app, _ = portal.resolve_portal_app("pinball knight", catalog, strict=True)
    assert app["id"] == "pinball-knight"

    # Stripped service suffix match
    app, _ = portal.resolve_portal_app("drift king", catalog, strict=True)
    assert app["id"] == "drift-king-service"


def test_extract_open_app_target_with_container_keyword():
    assert m.extract_open_app_target("open container pinball-knight") == ("pinball-knight", False, False)
    assert m.extract_open_app_target("launch container drift-king") == ("drift-king", False, False)
    assert m.extract_open_app_target("open the pinball-knight container") == ("pinball-knight", True, False)
    assert m.extract_bare_app_name("container pinball-knight") == "pinball-knight"


@pytest.mark.anyio
async def test_portal_registry_icon_url_and_office_client_curation():
    reg = portal._load_portal_registry()
    apps = reg.get("apps", {})
    assert "trading-client" in apps
    assert apps["trading-client"].get("icon_url") == "icons/trading-client.png"
    assert apps["drift-king"].get("icon_url") == "icons/drift-king.png"
    assert apps["music-player"].get("icon_url") == "icons/music-player.png"
    assert apps["html-notes"].get("icon_url") == "icons/html-notes.png"
    assert apps["braindeadbot-client"].get("icon_url") == "icons/braindeadbot-client.png"
    assert apps["office-client"].get("hidden") is True
    assert apps["office-client"].get("client") is False


@pytest.mark.asyncio
async def test_apply_curation_preserves_icon_url(monkeypatch):
    async def fake_portal_raw():
        return {
            "services": [
                {"id": "trading-client", "name": "Trading Bot", "url": "http://10.0.0.16:3030",
                 "healthy": True, "checkedAt": "2026-08-16T12:00:00Z", "projectType": "Client",
                 "dockerProject": "trading-client"},
                {"id": "office-client", "name": "Office Client", "url": "http://10.0.0.16:3000",
                 "healthy": True, "checkedAt": "2026-08-16T12:00:00Z", "projectType": "Client",
                 "dockerProject": "office-client"},
            ],
            "infrastructure": [],
        }
    async def fake_docker():
        return []
    async def fake_probe(app_id, url):
        return True

    monkeypatch.setattr(portal, "_fetch_portal_raw", fake_portal_raw)
    monkeypatch.setattr(portal, "_fetch_docker_containers_raw", fake_docker)
    monkeypatch.setattr(portal, "_probe_serves_html", fake_probe)
    monkeypatch.setattr(portal, "_load_portal_overrides", lambda: {})

    data = await portal.get_portal_apps()
    app_ids = {a["id"] for a in data["apps"]}
    assert "trading-client" in app_ids
    assert "office-client" not in app_ids  # hidden by default in portal_registry.json

    tc = next(a for a in data["apps"] if a["id"] == "trading-client")
    assert tc["icon_url"] == "icons/trading-client.png"

