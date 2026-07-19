# HTML-Notes agentic system — roadmap

Source of truth for improving the routing/agent stack. Written 2026-07-19 after
the prism-routing wave. Every item below is justified by a **failure we actually
observed**, not a hypothetical.

## Where we are

Working today (all live-verified):

- Research asks run on **prism `:7777`** via the `CUSTOM_HTML_NOTES_CANVAS`
  persona, using the lazy-tool-service MCP research tools.
- **Tier split**: deterministic widgets build locally (0.9–5s); research goes to
  the agent (~30s). Was 60–90s for *everything*.
- **Follow-ups update in place** (same widget id, new content) with entrance /
  update animations.
- **Canvas control** (`close everything`) is deterministic and instant.
- All 7 repos attribute to prism correctly (`default` traffic 250 → 5).
- Suite: **162 passing, 0 failing**, incl. 38 routing regression guards.

## The structural problem

Three regressions this session — canvas-control, follow-ups, video/live — had
**one root cause**: routing knowledge is duplicated across four places that drift
independently.

| where | holds |
|---|---|
| regex cascade in `send_message` | deterministic triggers + exclusion guards |
| `route_with_llm` prompt | the widget catalog the classifier sees |
| `_AGENT_RESEARCH_TYPES` | the tier split |
| agent `SYSTEM_PROMPT` ROUTING section | per-intent tool recipes |

Move a boundary in one and the others silently disagree. Every failure was a
**seam failure**, invisible until the user hit it. Fixing the seams is worth more
than any individual feature below.

---

## Phase 1 — Kill the seams (highest leverage)

- [ ] **1.1 Golden routing suite** — table of `utterance → expected intent`,
      seeded with every failure from this session (`close everything`→clear_all,
      `cnn live news`→video(live), `live nba scores`→sports, `what about cheaper
      ones`→followup_refine, `sandals`→products, `5 minute timer`→timer).
      Deterministic rows assert exactly; classifier rows tolerate marked
      ambiguity. **Do this FIRST** — it locks current behaviour and becomes the
      safety net for 1.2.
- [ ] **1.2 Single intent registry** — one table: name, tier, deterministic
      triggers, exclusions, handler. Router walks it in priority order
      (control → media → lookup → research). Ordering and guards become
      *structural* instead of hand-maintained, which is what the AST ordering
      tests currently compensate for.
- [ ] **1.3 Generate the classifier catalog + agent ROUTING section from the
      registry.** These are hand-maintained twins today — that is exactly how
      news changed tier while a stale video rule sat in a prompt nobody re-read.
- [ ] **1.4 Constrain the classifier** — closed choice over registry names only;
      if it contradicts a deterministic trigger that already matched, the
      deterministic match wins and the disagreement is logged. The LLM handles
      the long tail, never the unambiguous cases.
- [ ] **1.5 Context resolver stage** — resolve deixis BEFORE routing ("what about
      cheaper ones" → subject + target widget id). Today the follow-up rewrite is
      patched in *after* routing, inside the agent path only.
- [ ] **1.6 Retire the AST ordering guards** once 1.2 makes them structurally
      impossible to violate. They pin implementation shape; the golden suite pins
      behaviour, which is the thing we actually care about.

## Phase 2 — Agent reliability

- [ ] **2.1 Guarantee a canvas mutation.** A turn that ends with zero mutations is
      a failure (observed: 219s, no widget). If the loop finishes clean, fall back
      to a server-side build rather than returning nothing.
- [ ] **2.2 Handle MCP flakiness.** `html_notes_read_page` / `web_search`
      intermittently return `Unknown tool error`, once burning 130s. Needs a
      bounded retry + a degrade path ("render what we have"), not an open loop.
- [ ] **2.3 Reconnect MCP on lazy-tool-service deploy.** Redeploying it kills
      prism's SSE connection and every tool fails until something reconnects.
      Add a post-deploy `POST /mcp-servers/:id/connect`.
- [ ] **2.4 Delete the dead `lazy-agent-service` MCP registration** (0 tools,
      connected=false) — the duplicate that made it look like the tools were
      missing at all.

## Phase 3 — Answer quality

- [ ] **3.1 Verify the advanced news brief** end-to-end (cross-source
      corroboration, disagreement flagged, absolute dates). Shipped but only
      smoke-tested; the one run I saw returned a technicals table rather than a
      corroborated brief.
- [ ] **3.2 Re-point the vision relevance gate.** `ground_query` + the
      gemma-4 image judge now only run on the legacy path; the agent path renders
      images with no relevance check at all.
- [ ] **3.3 Groundedness check on research cards** — cheap pass asserting claims
      trace to a fetched source, reusing the CriticGate now that it is alive.
- [ ] **3.4 Trim the prompt.** 63.7k input tokens/turn (down from 77k). Most of
      the remainder is tool schemas + the ROUTING section, which 1.3 can emit
      per-intent instead of wholesale.

## Phase 4 — html-notes' hidden dependencies on other services

Only items that can BREAK html-notes belong here. (Ecosystem chores that merely
touch prism are listed at the bottom, out of scope for this repo.)

- [ ] **4.1 html-notes' MCP tools are registered by TRADING-SERVICE.**
      `trading-service/app/services/boot_service.py:~596` registers and connects
      the `lazy-tool-service` MCP server in prism under three scopes — one of
      which is `html-notes-client`. So **the agent's entire tool set depends on
      the trading bot booting**, and nothing in this repo owns or re-establishes
      it. This is why the connection died when lazy-tool-service was redeployed:
      the thing that re-runs `/connect` is trading-service startup.
      **html-notes should ensure its own MCP connection at boot** (idempotent
      register + connect for its own project), rather than inheriting one from an
      unrelated service.
- [ ] **4.2 Persist the prism persona.** `CUSTOM_HTML_NOTES_CANVAS` was
      registered by hand via `POST /custom-agents`. Nothing recreates it if
      prism's Mongo is reset — the agent would silently fall back to an unscoped
      run (~79 tools) and start wandering again. Make it idempotent at boot,
      alongside 4.1.
- [ ] **4.3 Health check the dependency.** `/health/app` returns ok while the MCP
      connection is dead, so every research ask fails with `Unknown tool error`
      and the app still reports healthy. Surface tool-availability in the health
      check.

### Not this repo's problem (tracked here only so it isn't lost)

Found during the ecosystem-wide prism attribution work. Belongs to the owning
services, not html-notes:

- `vault-service/projects.json` pins `PRISM_PROJECT=vllm-trading-bot` GLOBALLY,
  so any new service silently inherits the trading bot's identity. **Needs a
  decision** — scope per-project or drop the global default.
- Clients mutating shared prism state: trading-service writes directly into
  prism's Mongo `mcp_servers`; music-player + trading-client overwrite shared
  `/custom-agents` personas on startup.
- trading-service attribution commit `e08c051` is committed but not deployed
  (its tree had a parallel session's in-flight work).

## Phase 5 — Interaction

- [ ] **5.1 Progressive render.** A research turn shows a spinner for ~30s. Stream
      a skeleton card immediately and fill it as tools return — the reconciler +
      animations already support in-place updates.
- [ ] **5.2 Voice.** Already wired end-to-end (mic → `/session/transcribe` →
      prism STT → the same pipeline). Unverified since the routing changes.
- [ ] **5.3 Clarification on genuine ambiguity.** `ground_query` already returns
      `ambiguous` + a question; nothing consumes it. Ask instead of guessing when
      confidence is low — per Horvitz, act only when expected value beats asking.

---

## Working agreements (learned the hard way this session)

- **Verify in a real browser.** API-level success hid a canvas that never
  painted, twice.
- **Check the client, not just the server.** `index.js` hardcoded
  `use_lazy_agent: true` and silently overrode the server default; my curl tests
  passed because they omitted the field.
- **An LLM classifier is a backstop, never a gate** for intents with unambiguous
  trigger words.
- **Never chain deploy behind commit** in one command — a test failure shipped
  before I saw it.
- **Keep the suite green.** A familiar red hides a new one.
