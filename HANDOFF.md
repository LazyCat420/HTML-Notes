# Handoff — 2026-07-20 (debug wave)

**Current:** html-notes `main@14f34e8`, deployed to synology 2026-07-20T01:33:16Z.
Suite **249 passing** (was 211).
**OPEN:** Prism MCP registry is empty — research asks can't do real research.

Audited a 5-bug debug plan against the code and the live system: **2 hypotheses
confirmed, 3 refuted, 3 bugs found that weren't in the plan.** Full audit in
`DEBUG_WAVE_PLAN.md`.

## Refuted — don't re-investigate these

- **The `canvasVersion` guard is not dropping widgets.** The guard is real, but
  every dropped frame is a strict *subset* of what's already painted (commit order
  == version order, and `commit_canvas` re-reads the base inside the lock). No
  widget is lost to it.
- **The vault key is fine.** `TOMTOM_API_KEY` returns HTTP 200 from inside the
  container. Traffic was failing for a completely different reason.
- **`getCleanedCanvasHtml` stripping and `seedWidgetSnapshots` ordering are both
  already correct.** Test D/F in the plan describe non-bugs.
- **`mcplazy-tool-servicehtml_notes_web_search` is not a name mismatch** — it's
  markdown eating the `__` in `mcp__lazy-tool-service__`.

## The big one: agent turns that answer but show nothing

Three layers:

1. **Prism forces its core/system tools past `enabledTools`.**
   `AgenticToolResolver.ts:293-322` — `coreToolsLocked` defaults `true`, and
   prism's `AgentPersonaRegistry.registerCustom` (`:94-154`) never *reads*
   `coreToolsLocked`/`blockedTools` from a custom-agent doc. **For a CUSTOM agent
   the persona guardrail is structurally unreachable.** `execute_python` /
   `create_skill` arrive via `CORE_AGENTIC_TOOLS`, `create_artifact` via
   `systemTools`.
2. **The persona's `availableTools` is dead code for our requests** — only
   consulted when `enabledTools` is absent, and we always send it.
3. **Our SSE loop had a four-name whitelist and no `else`.** An unhandled tool
   emitted a spinner, committed nothing, never reset `active_tool_name` (wedging
   the deferred-flush check for the rest of the turn), and never set
   `canvas_settled` — so the turn ran to the iteration cap streaming text. **No
   text-to-widget fallback existed in the prism path at all.**

Layers 1-2 are prism-side and read-only for us. Layer 3 is ours and is fixed:
unhandled tools are logged/counted/reset, and **a turn that answers but commits
nothing now renders its answer as a `data_card`**.

## Everything that shipped

| Fix | Where |
|---|---|
| Unhandled-tool `else` — log, count, reset | `main.py` SSE loop |
| Safety net: no-widget turn renders its answer as a card | `_text_answer_card_config` |
| Thin replies (<40 chars) get an honest "couldn't answer" card | same |
| Deictic-place denylist — never geocode `here`/`current`/`my area` | `is_deictic_place` |
| Router asks "which city?" instead of building nothing | `build_router_widget` |
| Adoption no longer mints a version | `set_session_canvas(bump_version=False)` |
| TTS cleared only when this is the sole running turn | `index.js runChatTurn` |
| `relayoutOnMediaSettle` on both history-restore paths | `index.js loadHistory` |
| Boot check that shouts when the research path is down | `main.py` lifespan |
| `scripts/sse_probe.py` | new |

## Bugs the plan didn't have

1. **Deictic location words were geocoded literally.** The LLM router fills
   `query` with a placeholder like `"Current"` when the user names no place — and
   every such word is *also* a real place: `Current` → Eleuthera, Bahamas;
   `here` → Somalia; `my location` → Rwanda. "How is the traffic" rendered a
   confident traffic map of the Bahamas. **This is what "the traffic widget is
   failing" actually was.**
2. **A bare traffic ask built nothing on the router path.**
3. **Canvas adoption desynchronized the client permanently** — it minted a version
   nothing ever told the client about, so every later snapshot (including widget
   dismissals) was refused as stale until a reload.

## Gotchas

- **`build_traffic_widget` returning `None` is load-bearing.** The FAST path uses
  it to fall through to a travel-time answer card. My first fix changed the shared
  helper and `test_edge_case_fixes::test_traffic_widget_fallbacks_without_tomtom_key`
  caught it. Fix the router branch, not the helper.
- **`_DIR_STRIP_RE` runs before `is_deictic_place`**, so "traffic in my area"
  arrives as the bare word `area`. The denylist needs the bare heads too.
- **A safety-net card can be worse than no card.** First live run rendered a card
  whose body was `"..."` because the agent streamed 5 characters. A card that
  looks like a result but says nothing reads as a real answer — hence the 40-char
  floor.

## Declined: Tailwind CDN → local build

**It would break the app.** `create_widget` takes `htmlContent` — raw
model-authored HTML. The agent invents Tailwind utility classes at *runtime*,
which cannot be in a build-time content scan, so a static build would purge them
and model-authored widgets would render unstyled. The CDN's runtime JIT is
load-bearing here, not an oversight.

The legitimate concern (third-party dependency, offline resilience, CSP) is best
addressed by **self-hosting the JIT runtime** — vendor the script to `/static`.
That keeps runtime compilation. It does not remove the console warning, which the
script emits about itself. Worth doing as its own change with a visual pass over
all 14 widget types.

## Verified live

- Boot log now emits `[BOOT] RESEARCH PATH DOWN: ...` naming the outage.
- "how is the traffic" → asks which city (was: a map of the Bahamas).
- "how is the traffic in seattle" → `traffic-be2ad4c9`, 1.1s.
- "best waterproof hiking sandals" → agent committed nothing;
  fallback rendered `data_card #widget-62f43b99` reading *"I couldn't put together
  an answer for this one — the research tools didn't return anything usable."*
  Before this wave that turn produced an empty canvas.

## STILL OPEN

**Prism's MCP registry is empty** (`GET :7777/mcp-servers` → `[]`), so
`mcp_connected: false, tool_count: 0`. lazy-tool-service is healthy on `:5591`; it
self-registers on boot, so a prism restart dropped it. Pre-existing.

Research asks now degrade honestly instead of silently, but they still can't do
real research until this is restored. Likely just a lazy-tool-service restart —
not done unilaterally because other sessions deploy these services concurrently.

Deeper fix is prism-side parity: teach `registerCustom` to read
`coreToolsLocked`/`blockedTools` as the lazy-agent fork already does
(`lazy-agent-service/src/services/AgentPersonaRegistry.ts:139-144`). Rod's
codebase — out of scope for us to edit.
