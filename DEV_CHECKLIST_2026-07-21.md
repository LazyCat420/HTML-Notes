# Dev Handoff Checklist — post-audit backlog (2026-07-21)

Everything below is OPEN work left after the deep-audit fix wave (context:
`HANDOFF.md` top section, evidence: `AUDIT_2026-07-21.md`). Items are ordered
by priority within each section. Nothing here is broken-in-production; these
are the deliberately-deferred items.

## Before you start — process gotchas (read once, they will bite)

- [ ] **Container is python:3.11, local venv is 3.12.** A backslash inside an
      f-string expression passes local tests and CRASH-LOOPS the container.
      Before deploying: `docker run --rm -v "$PWD/app:/chk" python:3.11-slim
      python -c "import ast; ast.parse(open('/chk/main.py').read())"`.
- [ ] **Browser-verify any Alpine/localStorage persistence work.** Two bugs
      shipped past 528 green pytests and were only caught headless-Chromium
      driving the live app: (1) Alpine's reactive proxy aliases arrays — a
      baseline compared against `initialItems` mutates when the user edits;
      (2) inside an `x-for` row, `$el` is the cloned row element (no id), so
      `$el.id`-keyed storage writes miss the reads. Use
      `youtube-wallgarden/.venv/bin/python` (Playwright + Chromium installed).
- [ ] **Tool schema changes**: edit
      `lazy-agent-service/tool_schemas/html-notes/html-notes.json` (the
      SOURCE), then run `trading-service/scripts/build_tool_schemas.py` (flat
      `tool_schemas.json` files are build artifacts), then commit + deploy
      lazy-agent-service. Never deploy that repo with a dirty tree — parallel
      sessions leave WIP there; its Dockerfile does `COPY . .`.
- [ ] **New widget type wiring recipe** (all touchpoints, in order):
      1. renderer in `app/widgets/factory.py` + entry in `WIDGET_RENDERERS`
         (data-sig/type stamping is free via `generate_widget_html`);
      2. content keys in `_CONTENT_KEYS` / query keys in `_QUERY_ONLY_KEYS`
         + the `_widget_is_degenerate` type tuple (`app/main.py` ~line 1640);
      3. reuse policy: `REUSABLE_WIDGET_TYPES` / `TOPIC_SINGLETON_TYPES`
         (~line 2600) — decide explicitly, comment why;
      4. SYSTEM_PROMPT routing line (~line 7780) — name the EXACT type slug;
      5. tool-schema enum + description (see previous bullet);
      6. tests in `tests/test_widget_pack_v2.py` (follow existing patterns).
- [ ] Run `pytest tests/ -q` AND the node tests (`node tests/*.mjs`) before
      every deploy. Always commit → push → `./deploy.sh` → verify live at
      `:8035` → update `HANDOFF.md`.

## P1 — Security hardening

- [ ] **Isolate create_widget custom JS in a sandboxed iframe (srcdoc).**
      `jsContent` still executes in the app origin (it IS the custom-widget
      feature, so it wasn't removed). Move the whole custom-widget body+script
      into `<iframe srcdoc sandbox="allow-scripts">`; postMessage for resize.
      Touches: `app/main.py` create_widget handler (~line 8560), client
      `reviveScripts` (`index.js` ~1930), and eventually the DOMPurify
      `ADD_TAGS: ['script']` + force-keep-Alpine-attrs hook (`index.js` ~335,
      ~1300) — that config can only be tightened AFTER scripts stop living in
      the canvas. Acceptance: a custom widget still works interactively; its
      script cannot read `localStorage`/cookies of the app origin.
- [x] **Authenticate `POST /internal/execute`** — DONE 2026-07-21 (6f2c03a +
      lazy-agent 646c2f1): `x-internal-token` shared secret (constant-time
      compare, env-first/vault fallback via `_fetch_secret`), provisioned in
      both services' NAS `.env`; unset = compat mode with once-per-boot
      warning. `req.tool` checked against the complete 21-tool dispatch set
      before the elif chain. 528 pytest green; py3.11 AST ok.
- [ ] **Enforce enabledTools server-side.** Prism forces its core tools in
      regardless of the allowlist (`coreToolsLocked` unreachable for CUSTOM
      agents — documented at `app/main.py` ~8790). Add a server-side check in
      the SSE interceptor that logs (first) then rejects mutations from tools
      outside `enabled_tools`.
- [x] **TomTom key** — RESOLVED 2026-07-21 (6f2c03a). The finding was actually
      a LOG leak: httpx logged every request URL at INFO, exposing the `?key=`
      param on each proxied tile fetch. httpx logger now capped at WARNING.
      No key is committed anywhere; note `TOMTOM_API_KEY` is currently absent
      from env AND vault, so the traffic overlay serves blank tiles until a
      key is added (free: developer.tomtom.com).

## P2 — Consistency seams (all evidenced in AUDIT file with file:line)

- [ ] **Agent tier drops P3 role-prefix reuse** — traffic asks reaching tier 3
      stack a second traffic widget (the map/iframe_app fork).
      `_resolve_agent_widget_id` (~line 3300) never passes `id_prefix`; derive
      it from the model id (`traffic-*`/`map-*`/`weather-*` →
      `SINGLETON_ROLE_PREFIXES`) and forward to `_resolve_widget_target`.
- [ ] **Follow-up type-morph seam**: when the model's `widget_id` names a live
      widget of a DIFFERENT type, the id is rejected by the type gate then
      honored verbatim by `_add`'s replace (~line 8500) — the exact clobber
      the gate exists to stop. Decide: honor as an intentional in-place morph
      (skip `find_reuse_target`) OR mint a fresh id. Never reject-then-replace.
- [ ] **Generalize `data-req-seq`**: the out-of-order-commit guard only covers
      youtube/music (`_MEDIA_WIDGET_MARKERS`, ~line 2041). Weather/map/
      settings in-place `replace_with()` swaps can still commit out of order
      ("weather in SF" then "NYC" → slow SF commit clobbers NYC). Stamp seq on
      EVERY in-place replace of an existing id; skip when the live node's seq
      is newer.
- [ ] **Checklist agent-merge vs localStorage edits**: "add milk to my list"
      merges against the items BAKED in x-data, not the user's local edits, so
      a server rewrite drops locally-added tasks (client localStorage restore
      then loses to the changed baseline — by design, but lossy). Options:
      bake current items back into x-data during `getCleanedCanvasHtml`, or
      send checklist state alongside `current_canvas`.
- [ ] **Delete the dead legacy widget vocabulary** once nothing calls it:
      `app/tools_schema.py` (has a DEAD FILE header) + `app/templates.py`
      TEMPLATES + the `/internal/execute` `render_component` branch — remove
      together.

## P3 — Backlog widgets (full specs w/ config shapes + reuse pointers in AUDIT §display-gaps and below)

- [ ] **Stock volume underlay** (cheap, high value): `stock_snapshot` already
      returns per-candle `volumes` and the factory drops them
      (`factory.py` stock_card snapshot dict). Pass through + render in
      `stockCardWidget.drawChart` (`widgets.js`) as a second dataset:
      `{type:'bar', data: volumes, yAxisID:'vol'}`, low opacity, right axis
      scaled ~25% height.
- [ ] **Sports standings** (prompt promises it; nothing implements it): new
      `sports_standings` fetch against ESPN `.../standings` returning
      `[{rank, team, logo, w, l, pts, streak}]`, rendered via the NEW `table`
      widget (logo image cell needs a `type:'image'` column — small extension)
      or a standings mode on scoreboard. Split "standings" out of the scores
      routing line (`main.py` ~7723 + fast-path regex ~3775 + router spec).
- [ ] **Weather comparison**: resolver key (e.g. `compare_weather:
      ["Tokyo","London"]`) on `versus_card` — server fans out `get_weather`,
      builds temp/humidity/wind/hi-lo rows; or a `multi_chart` of daily highs.
      NB weather is a singleton — a compare must NOT retarget the existing
      weather widget (it's a different type, so it won't).
- [ ] **Map + ranked list split view**: Places ratings are flattened into a
      180-char popup string (`main.py` ~5990, `map_payload`). Keep
      rating/reviews structured through `map_payload`, add a list pane beside
      the iframe (rank badge ↔ numbered pin, postMessage to open popups).
- [ ] **gallery_facts** — per-entity photo + fact rows grid ("the 4 great ape
      species with size and habitat"). Clone the products grid skeleton
      (`factory.py` render_products), facts as mini rows per card. Images:
      reuse `build_products_config`'s search → og:image → vision-gate chain
      with index-mapped facts so a caption can never pair with the wrong
      picture. Agent emits `{gallery_query}`; server resolves.
- [ ] **video_shelf** — grid of 4-6 real video thumbnails (search results are
      fetched then discarded today; thumbnails derivable from verified
      video_id: `i.ytimg.com/vi/<id>/hqdefault.jpg`). Link-out v1, no client
      JS. Routing: "videos/several" → shelf, "a video" → player.
- [ ] **stat_card** — hero image + KPI grid composite for one subject ("how
      big is a blue whale"): data_card figure + enlarged weather stat cells +
      optional language-chart sparkline. Image via `build_image_config` only.
- [ ] **calendar** — month grid via stdlib `calendar.monthcalendar`, day
      cells styled like weather day tiles, emoji/intensity markers. Low
      priority: no tool returns per-day series directly.

## Verification bar for every item

- [ ] pytest + node green locally; py3.11 ast check for main.py/factory.py.
- [ ] Live E2E through `POST /session/message` (see the SSE-parsing snippets
      in this session's HANDOFF section) — assert the expected
      `data-widget-type` appears in a `component` frame.
- [ ] For anything touching client state: Playwright load → interact → reload
      → assert state, and zero console/page errors.
- [ ] Commit, push, `./deploy.sh`, verify `:8035/health/app`, update
      `HANDOFF.md`.
