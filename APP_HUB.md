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

## Open ROUTING rules (2026-08-16, after live use)

Three rules decide what an "open X" ask does. They exist because the first
cut got the precedence backwards: **"open music player" spawned the
mini-player widget instead of opening the music-player site**, and "open
trading bot" fell through to the agent — which had no catalog in its prompt
— and rendered a junk card.

1. **An app name beats a widget.** A normalized EXACT match on an app's
   id / name / alias (case, spaces and dashes ignored: `trading-client` ==
   "Trading Client") opens the app, full stop. Widget nouns are irrelevant at
   this tier. Only the fuzzy PARTIAL tier still defers to widgets, so
   "open my notes" is the notepad while "open html notes" is the app.
2. **Clients-first — a human open never means a backend.** `prefer_clients()`
   drops every `*-service` from a match set that also contains a non-backend,
   so "open trading" → Trading Client and "open portal" → Portal Client with
   no prompt. A backend is reachable only by its full name ("open trading
   service"). ⚠️ **This is keyed on the `-service` NAME SUFFIX, never on
   `projectType`** — portal reports `projectType: "Service"` for
   **music-player, html-notes and youtube-wallgarden**, all of which are
   openable front ends. Believing that field would re-break the exact app
   this fix was written for.
3. **Ambiguity asks instantly, never slowly.** A surviving client-vs-client
   tie returns a zero-LLM chat reply listing the matches as markdown links
   (`_stream_open_candidates`); `index.js` already forces `target="_blank"`
   on bubble links, so each is a real new-tab launcher. No widget spawns and
   no agent turn runs.

**Aliases** live in `app/portal_registry.json` (`aliases: [...]` per app,
unioned with the DB overlay so a runtime addition never wipes the git
defaults). They are matched at the EXACT tier, which is why they must stay
app-specific — a bare widget word like "music" or "notes" would hijack
"play music". A test (`test_registry_aliases_are_not_bare_widget_words`)
enforces that against `_OPEN_APP_WIDGET_NOUNS`.

**The agent gets the catalog too.** Every turn's SYSTEM_PROMPT carries a
`YOUR APPS (live)` block (`build_apps_prompt_block`, ~15ms cached, capped at
25 rows, "" on a portal outage) listing `id — name (aka aliases)`, plus a
routing line telling it to check that list FIRST and prefer clients. Whatever
the fast lane doesn't catch now reaches the agent already knowing which words
name the user's containers.

Measured after the fix (local, 2026-08-16): "open music player" → Music
Player 0.06s · "open trading bot" → Trading Client 0.02s · "open trading" →
Trading Client 0.02s · "open trading service" → the backend 0.02s ·
"play some lofi hip hop" → music widget (unchanged) · "open my notes" →
notepad (unchanged) · "show my apps" → hub grid (unchanged).

## Open fast lane (added same day)

Measured layers: portal `GET /services` ~13ms, `/api/services` ~12ms, tool
call via gateway ~17ms — the fetch was never the cost; the AGENT LOOP around
it was (15-40s of LLM). So a short open-imperative ("open the trading
client", "launch drift king") now resolves against the catalog **strictly**
(id+name only, every word must hit, exactly one match) and emits `open_url`
with zero LLM: **0.08s measured**, `path:"fast-path", widget_type:"open_app"`.

Guards, in order (`extract_open_app_target` in `app/main.py`):
- ≤70 chars, anchored `open|launch|start|pull up|bring up` imperative;
- widget-noun words (music/notes/map/timer/…) fall through UNLESS an explicit
  app marker was said and stripped ("…app", "…in a new tab", "…site") —
  "open the music player" stays the mini-player widget, "open the music
  player app" opens the tab;
- ambiguous ("open portal" → portal-client/portal-service) or unknown names
  fall through to the widget lanes/agent unchanged, which can ask.

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
3. **"open music player"** → the music-player SITE opens in a new tab.
   **Bug if: the mini music-player widget appears** (that was the reported
   defect).
4. **"open trading bot"** → Trading Client :3030 opens. Bug if: a data_card
   or any widget appears, or it thinks for 30s.
5. **"open trading"** → Trading Client opens directly (no prompt — clients
   beat services). **"open trading service"** → the backend :3031.
6. **"play some lofi hip hop"** → the mini music player widget, as always.
   **"open my notes"** → the notepad widget, NOT the html-notes app. Bug if
   either opens a browser tab.
4. Hover a tile → ✕/📌; hide one, reload the page, "show my apps" again →
   still hidden. Bug if: it returns.
5. Stop any container on the NAS, wait ~2 min → its dot goes red WITHOUT a
   page reload (45s poll + portal's 60s sweep). Bug if: needs reload.
6. Play music, then pin/hide tiles → audio must not stutter.
