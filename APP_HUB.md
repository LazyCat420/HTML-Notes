# App Hub — portal-service-driven launcher (2026-08-16)

The canvas is now the central hub for the whole ecosystem: an `app_grid`
singleton widget shows every service portal-service knows about as a clickable
tile (live status dot, pinned-first), each opening in a new browser tab, and
the agent can list/open/curate apps by name.

Shipped in `e542d57` + `a083795` + `c7634fe`, deployed 2026-08-16.

## The data path

```
vault-service projects.json  (NAS: /volume1/docker/vault-service/projects.json,
        |                     watchFile-hot-reloaded; local repo copy is gitignored data)
        v
portal-service :4001  GET /services   (no auth on LAN; health re-swept every 60s)
        |
        v  app/services/portal.py — 30s TTL cache, last-good stale fallback
PortalApp contract: {id, name, description, icon, launch_url, status,
                     latency_ms, project_type, device, pinned, hidden,
                     has_container, repo}
        |            curation layering: portal < app/portal_registry.json (git)
        |                                       < DB overlay (widget_state 'portal:overrides')
        v
- app_grid widget (factory.render_app_grid + appGridWidget in widgets.js)
- GET /api/services  +  POST /api/services/{id}/override
- tools: html_notes_list_services / html_notes_open_app / html_notes_curate_app
```

## Contract gotchas (portal-service is Rod's — READ ONLY)

- **Launch URL** = `https://{domain}` when `domain` set, else `url` (mirrors
  portal-client).
- **`restartable` is INVERTED** (`true` = NOT containerized). Never read it;
  `has_container` comes from `dockerProject != null`.
- **`checkedAt == null` means "never checked" → status `unknown`**, not
  unhealthy.
- The `infrastructure` array (mongo/redis/etc) is excluded by default —
  `defaults.include_infrastructure` in `portal_registry.json` opts in.
- No icon field exists in the API; icons are emoji from `portal_registry.json`
  overrides, else a projectType default map in `app/services/portal.py`.

## Adding a NEW app to the hub

One JSON entry, zero code: append to the NAS
`/volume1/docker/vault-service/projects.json` `projects` array
(`id/label/port/healthPath/dockerProject/...` — copy an existing entry), then
`POST http://10.0.0.16:4001/services/reload` (or wait ≤5 min). The tile
appears on the next 45s widget poll. Keep the local repo copy
(`vault-service/projects.json`, gitignored) in sync. `html-notes` itself was
registered this way on 2026-08-16 (backup taken:
`projects.json.bak-0816-*`).

Curation (icon/pin/hide defaults) goes in `app/portal_registry.json`;
runtime hide/pin (tile ✕/📌 buttons, `html_notes_curate_app`) lands in the
DB overlay and survives restarts without a redeploy.

## What was proved (live, 2026-08-16)

- `GET :8035/api/services` → 17 apps, pins first, lupos-bot/tools-service
  correctly red, `stale:false`; with portal down it serves last-good with
  `stale:true`.
- Hide→gone→restore round-trip via the override route; overlay survives in
  `data/notes.db`.
- "show my apps" → SSE `path:"fast-path"`, one `app_grid` widget; repeat ask
  reuses the SAME widget id (singleton).
- "open the trading client in a new tab" → agent calls
  `html_notes_open_app`, exactly ONE `open_url` SSE frame
  (`http://10.0.0.16:3030`) — the model re-emits the tool call after its ack,
  so the interceptor dedupes per turn (`emitted_open_apps`).
- Ambiguity is refused: "trading" returns candidates
  (trading-client/trading-service), never a guess. Only approved catalog ids
  can open — the tool never accepts a raw URL.
- `tests/test_app_hub.py` (14 tests) + full suite: failure set byte-identical
  to the pre-change baseline (124 pre-existing failures on clean main,
  independent of this work).

## Agent-path repairs this exposed (caller-side, in this repo)

1. **prism mode is live-broken**: prism's `/config-local` returns ZERO local
   providers, so model discovery fell to the `vllm-2` fallback and prism
   rejected every tier-3 turn (`Unknown provider "vllm-2"`). `use_lazy_agent`
   now defaults **True** (gateway :5591, which has vllm/vllm-2/vllm-3
   registered). Flip back only after prism's instance registry is populated.
2. **The gateway persona path is broken** in the newly ported agentic-loop
   harness: any request with `agent:"HTML_NOTES"` returns an empty stream
   (0 input tokens, no shim POST, "Empty model output on iteration 1";
   bisected live — identical payload minus the field works). The gateway is
   called **persona-less** for now; `enabledTools` + SYSTEM_PROMPT still
   scope the run. Open item filed in lazy-agent-service HANDOFF.md.

## Traps for the next reader

- `app/services/*` modules adopt main's namespace
  (`update(main.__dict__)`) — that **overwrites the module's `__file__`**, so
  any `__file__`-relative path silently resolves against `app/main.py`.
  `portal.py` captures `_PORTAL_REGISTRY_PATH` before the adoption line.
- `APP_HUB_INTENT_RE` deliberately excludes "dashboard" ("modify my dashboard
  tasks" means the CANVAS) and bare "launcher"; a draft regex with
  `my dashboards?` hijacked a canvas-edit test turn away from the agent.
- `window.open` from an SSE callback has no user gesture → popup-blocked;
  the client falls back to a clickable toast. Tile clicks are real
  `<a target="_blank">` anchors and need no JS.
- A new widget type touches SEVEN registries (WIDGET_RENDERERS,
  `_CANVAS_CLASS_TYPE`, `_CANVAS_XDATA_TYPE`, singleton/reusable sets,
  aliases, `_CONTENT_KEYS`, the `canvas_add_widget` enum in
  lazy-agent-service `tool_schemas/html-notes/html-notes.json`) — and the
  schema change needs `build_tool_schemas.py` + a **lazy-agent-service
  redeploy** (schemas cached at startup).

## Player test list (UNVERIFIED — needs a human in a real browser)

1. Open http://10.0.0.16:8035, type "show my apps" → App Hub grid with your
   real services and colored dots. Bug if: mongo/postgres/redis tiles appear.
2. Click the Trading Client tile → NEW tab on :3030. Bug if: same-tab.
3. Ask "open the music player app" → ONE new tab (or one clickable toast if
   popup-blocked). Bug if: two tabs, or the mini music player widget spawns
   instead when you say "app".
4. Hover a tile → ✕/📌; hide one, reload the page, "show my apps" again →
   still hidden. Bug if: it returns.
5. Stop any container on the NAS, wait ~2 min → its dot goes red WITHOUT a
   page reload (45s poll + portal's 60s sweep). Bug if: needs reload.
6. Play music, then pin/hide tiles → audio must not stutter.
