import sys
import os as _os
import app.main as main
# Captured BEFORE the namespace adoption below: that update overwrites this
# module's __file__ with main's, so any later __file__-relative path silently
# resolves against app/main.py's directory tree instead of this file's.
_PORTAL_REGISTRY_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "portal_registry.json")
sys.modules[__name__].__dict__.update(main.__dict__)

import fnmatch

# ── Portal App Hub ───────────────────────────────────────────────────────────
# portal-service (:4001) is the runtime truth: which services exist, their URLs
# and health. This module turns its /services payload into the ONE normalized
# shape everything hub-related consumes — the app_grid widget, /api/services,
# and the html_notes_list_services / open_app / curate_app tools:
#
#   {id, name, description, icon, launch_url, status, latency_ms, project_type,
#    device, pinned, hidden, has_container, repo}
#
# `id` is the portal registry id and is stable forever — container churn never
# touches curation. Curation is layered on top, never written back to portal:
#   portal data  ⊕  app/portal_registry.json (git defaults)  ⊕  DB overlay
# with the DB overlay (runtime hide/pin, survives restarts, no redeploy)
# taking precedence over the file, and the file over portal.

_PORTAL_CACHE_TTL = 30.0
_PORTAL_TIMEOUT = 8.0
# Last-good payload: the hub must render (marked stale) when portal is down.
_portal_cache: Dict[str, Any] = {"at": 0.0, "raw": None, "stale": False}
_PORTAL_OVERRIDES_KEY = "portal:overrides"

# projectType → default emoji, mirroring portal-client's projectType→icon map
# (constants.ts) in spirit; portal's API carries no icon field at all.
_PORTAL_TYPE_ICONS = {
    "Service": "🛠️", "Client": "🖥️", "Bot": "🤖", "Database": "🗄️",
    "Store": "📦", "Library": "📚", "Kit": "🚀", "Tool": "🔧",
    "Infrastructure": "⚙️",
}


def _load_portal_registry() -> dict:
    """The git-versioned curation defaults. Missing/broken file → empty defaults
    (the hub still works, just uncurated)."""
    try:
        with open(_PORTAL_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[PORTAL] portal_registry.json unreadable: {e}")
        return {}


def _load_portal_overrides() -> dict:
    """Runtime hide/pin toggles from the DB (set via ✕/📌 on the widget or the
    html_notes_curate_app tool). {app_id: {hidden?: bool, pinned?: bool}}."""
    try:
        raw = database.get_widget_state(_PORTAL_OVERRIDES_KEY)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[PORTAL] overrides unreadable: {e}")
        return {}


def set_portal_override(app_id: str, hidden: Optional[bool] = None,
                        pinned: Optional[bool] = None) -> dict:
    """Merge one app's runtime curation into the DB overlay and return the
    stored overlay. Passing None leaves that flag as it was."""
    overrides = _load_portal_overrides()
    entry = dict(overrides.get(app_id) or {})
    if hidden is not None:
        entry["hidden"] = bool(hidden)
    if pinned is not None:
        entry["pinned"] = bool(pinned)
    overrides[app_id] = entry
    database.set_widget_state(_PORTAL_OVERRIDES_KEY, json.dumps(overrides))
    return overrides


async def _fetch_portal_raw() -> Optional[dict]:
    """The cached /services payload, or the last-good one (marked stale) when
    portal is unreachable. None only when portal has never answered."""
    now = time.time()
    if _portal_cache["raw"] is not None and now - _portal_cache["at"] < _PORTAL_CACHE_TTL:
        return _portal_cache["raw"]
    try:
        async with httpx.AsyncClient(timeout=_PORTAL_TIMEOUT) as client:
            resp = await client.get(f"{PORTAL_SERVICE_URL}/services")
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("services"), list):
            _portal_cache.update({"at": now, "raw": data, "stale": False})
            return data
        logger.warning("[PORTAL] /services returned an unexpected shape")
    except Exception as e:
        logger.warning(f"[PORTAL] fetch failed ({e}); serving last-good")
    if _portal_cache["raw"] is not None:
        _portal_cache["stale"] = True
        _portal_cache["at"] = now  # don't hammer a dead portal every call
    return _portal_cache["raw"]


def _normalize_portal_service(svc: dict) -> dict:
    """One portal service row → a PortalApp. Launch convention mirrors
    portal-client: https://{domain} when a domain exists, else the raw url.
    NOTE: portal's `restartable` is inverted (true = NOT containerized) — the
    honest containerized signal is dockerProject."""
    domain = svc.get("domain") or ""
    launch = f"https://{domain}" if domain else (svc.get("url") or "")
    checked = svc.get("checkedAt")
    status = ("unknown" if not checked
              else ("healthy" if svc.get("healthy") else "unhealthy"))
    ptype = svc.get("projectType") or "Service"
    return {
        "id": svc.get("id") or "",
        "name": svc.get("name") or svc.get("id") or "?",
        "description": svc.get("description") or "",
        "icon": _PORTAL_TYPE_ICONS.get(ptype, "🌐"),
        "launch_url": launch,
        "status": status,
        "latency_ms": svc.get("responseTimeMs"),
        "project_type": ptype,
        "device": svc.get("device") or "",
        "pinned": False,
        "hidden": False,
        "has_container": bool(svc.get("dockerProject")),
        "repo": svc.get("repo") or "",
        # Extra names a human might use ("trading bot"). Portal has no such
        # field — they come from portal_registry.json curation.
        "aliases": [],
    }


def _apply_curation(app: dict, entry: dict) -> None:
    """One registry-file/overlay entry onto a PortalApp, in place."""
    for key in ("icon", "name", "launch_url", "description"):
        if entry.get(key):
            app[key] = entry[key]
    if isinstance(entry.get("aliases"), list):
        # Union, not replace: the DB overlay must be able to ADD an alias
        # without wiping the git-versioned defaults.
        merged = list(app.get("aliases") or [])
        merged += [a for a in entry["aliases"]
                   if isinstance(a, str) and a and a not in merged]
        app["aliases"] = merged
    if "pinned" in entry:
        app["pinned"] = bool(entry["pinned"])
    if "hidden" in entry:
        app["hidden"] = bool(entry["hidden"])


async def get_portal_apps(include_hidden: bool = False) -> dict:
    """The curated PortalApp list: portal inventory ⊕ registry file ⊕ DB
    overlay. Pinned first, then A-Z. {apps, stale, count} — never raises."""
    registry = _load_portal_registry()
    reg_apps: dict = registry.get("apps") or {}
    defaults: dict = registry.get("defaults") or {}
    raw = await _fetch_portal_raw()

    apps: Dict[str, dict] = {}
    rows = list((raw or {}).get("services") or [])
    if defaults.get("include_infrastructure"):
        rows += list((raw or {}).get("infrastructure") or [])
    for svc in rows:
        if not isinstance(svc, dict) or not svc.get("id"):
            continue
        apps[svc["id"]] = _normalize_portal_service(svc)

    hide_patterns = [p for p in (defaults.get("hide_patterns") or [])
                     if isinstance(p, str) and p]
    for app in apps.values():
        if any(fnmatch.fnmatch(app["id"], pat) for pat in hide_patterns):
            app["hidden"] = True

    for app_id, entry in reg_apps.items():
        if not isinstance(entry, dict):
            continue
        if app_id not in apps:
            # Synthetic launch target: curated but unknown to portal (a plain
            # website, a desktop link, a not-yet-registered service).
            apps[app_id] = {
                "id": app_id, "name": app_id, "description": "", "icon": "🌐",
                "launch_url": "", "status": "unknown", "latency_ms": None,
                "project_type": "Link", "device": "", "pinned": False,
                "hidden": False, "has_container": False, "repo": "",
                "aliases": [],
            }
        _apply_curation(apps[app_id], entry)

    for app_id, entry in _load_portal_overrides().items():
        if app_id in apps and isinstance(entry, dict):
            _apply_curation(apps[app_id], entry)

    result = [a for a in apps.values()
              if (include_hidden or not a["hidden"]) and a["launch_url"]]
    result.sort(key=lambda a: (not a["pinned"], a["name"].lower()))
    return {"apps": result,
            "stale": bool(_portal_cache["stale"]) or raw is None,
            "count": len(result)}


async def build_apps_prompt_block(limit: int = 25) -> str:
    """A compact 'YOUR APPS (live)' block for SYSTEM_PROMPT, or "".

    Without this the agent had NO idea which words name the user's own
    containers — "open trading bot" fell through the fast lane, reached the
    agent, and produced a junk research card because nothing in its context
    said trading-client existed. Never raises and never blocks a turn: a
    portal outage yields the last-good list, or an empty string."""
    try:
        data = await get_portal_apps()
    except Exception as e:                      # pragma: no cover - defensive
        logger.warning(f"[PORTAL] prompt block failed: {e}")
        return ""
    rows = []
    for a in data.get("apps", [])[:limit]:
        alias = ", ".join((a.get("aliases") or [])[:4])
        rows.append(f"- {a['id']} — {a['name']}" + (f" (aka {alias})" if alias else ""))
    if not rows:
        return ""
    return ("\n\nYOUR APPS (live inventory of the user's own containers; "
            "these names are APPS, not widgets):\n" + "\n".join(rows) + "\n")


def _norm_key(text: str) -> str:
    """'Trading Client' / 'trading-client' / 'trading  client' → 'tradingclient'.
    Exact matching has to survive the difference between how a service is
    REGISTERED (id `trading-client`, label `Trading Client`) and how a human
    types it, so compare on alphanumerics alone."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def is_backend_app(app: dict) -> bool:
    """A backend/API container rather than something a human opens in a tab.

    The user's rule: an open ALWAYS means the client; a repo with no `-client`
    sibling is one container that IS the app.

    Keyed on the `-service` NAME SUFFIX, deliberately NOT on `projectType`.
    Checked against the live registry 2026-08-16: portal reports
    `projectType: "Service"` for **music-player, html-notes and
    youtube-wallgarden** — all three are openable front ends with no client
    sibling. Reading projectType here would have hidden exactly the app whose
    misrouting prompted this fix."""
    return (app.get("id", "").lower().endswith("-service")
            or app.get("name", "").lower().endswith(" service"))


def prefer_clients(matches: List[dict]) -> List[dict]:
    """Drop backends from a match set that also contains a non-backend.

    'open trading' matches trading-client AND trading-service; the user only
    ever means the client, so this collapses the tie instead of asking. When
    EVERY match is a backend (the user typed a service name), nothing is
    dropped — the ask really was about the backend."""
    non_backend = [a for a in matches if not is_backend_app(a)]
    return non_backend if non_backend else matches


def resolve_portal_app(query: str, apps: List[dict], strict: bool = False,
                       exact_only: bool = False) -> tuple:
    """Resolve a user phrase ('the music thing', 'trading client') to ONE app.
    Returns (app, candidates): app set iff exactly one confident match;
    otherwise candidates carries the plausible ones for the caller to offer.
    Never guesses between near-ties — opening the wrong app in a tab is worse
    than asking.

    Matching is three-tier:
      1. NORMALIZED EXACT on id / name / alias — 'music player' IS the app,
         regardless of any widget the same words might name. This tier is why
         'open the music player' opens the site instead of spawning the
         mini-player widget.
      2. all-words partial (id+name+aliases; also descriptions when not strict).
      3. clients-first collapse, then uniqueness.

    strict=True is the no-LLM fast lane's contract: never match on
    description (a description mentioning 'notes' must not claim 'open my
    notes') and require EVERY query word to hit."""
    q = (query or "").strip().lower()
    if not q:
        return None, []

    # ── Tier 1: normalized exact (id / name / alias) ────────────────────
    qk = _norm_key(q)
    if qk:
        exact = [a for a in apps
                 if qk in ({_norm_key(a["id"]), _norm_key(a["name"])}
                           | {_norm_key(x) for x in (a.get("aliases") or [])})]
        exact = prefer_clients(exact)
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact[:4]

    # exact_only is the BARE-NAME contract: a message with no open-verb ("music
    # player") may only ever match a whole app name/alias. Partial matching
    # without a verb would let ordinary prose ("trading is down today") launch
    # a tab.
    if exact_only:
        return None, []

    # ── Tier 2: all-words partial ───────────────────────────────────────
    words = [w for w in re.split(r"[^a-z0-9]+", q) if len(w) > 2]
    if strict and not words:
        return None, []
    scored = []
    for a in apps:
        hay = f"{a['id']} {a['name']} {' '.join(a.get('aliases') or [])}".lower()
        if not strict:
            hay += f" {a['description']}".lower()
        score = sum(1 for w in words if w in hay)
        if strict and score < len(words):
            continue
        if score:
            scored.append((score, a))

    if strict:
        # Every word hit, so all survivors are equally good — collapse
        # client-vs-service ties before judging uniqueness.
        finalists = prefer_clients([a for _, a in scored])
        return (finalists[0], []) if len(finalists) == 1 else (None, finalists[:4])

    scored.sort(key=lambda s: -s[0])
    if scored:
        top = scored[0][0]
        finalists = prefer_clients([a for s, a in scored if s == top])
        if len(finalists) == 1:
            rest = [a for s, a in scored if s != top or a not in finalists]
            return finalists[0], rest[:3]
        return None, finalists[:4]
    return None, []


__all__ = [k for k in globals().keys() if not k.startswith('__')]
