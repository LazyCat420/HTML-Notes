# Debug wave — audited plan and outcomes

The original plan proposed 18 tests across 5 bugs. Auditing each hypothesis
against the code and the live system **confirmed 2, refuted 3, and found 3 bugs
the plan didn't contain**. What follows is the corrected version.

## Verdicts

| Plan item | Verdict | Reality |
|---|---|---|
| Bug 1 — `canvasVersion` guard silently drops widgets | **Refuted** | The guard is real, but every dropped frame is a strict *subset* of what's painted. No widget is ever lost to it. |
| Bug 1 — TTS | **Confirmed, worse** | `clearSpeechQueue()` was the unconditional first line of `runChatTurn` — turn 2 cut off turn 1's answer mid-sentence. |
| Bug 2 — masonry span timing / `getCleanedCanvasHtml` | **Refuted** | Stripping is correct. The real cause: `relayoutOnMediaSettle` was never called in the `loadHistory` restore path. |
| Bug 2 — Test F, `seedWidgetSnapshots` ordering | **Already correct** | It is already called after `WidgetLayout.apply()`. No bug. |
| Bug 3 — vault API key misconfigured | **Refuted** | Vault returns HTTP 200 with `TOMTOM_API_KEY` present. Verified from inside the container. |
| Bug 4 — agent calls tools, no widget | **Confirmed, root cause found** | Not a name-mangling issue. Prism *structurally* forces core tools past our allowlist. |
| Bug 4 — Test L, `mcplazy-tool-service` name mismatch | **Refuted** | An artifact of markdown eating the `__` in `mcp__lazy-tool-service__`. The real name is correct. |
| Bug 5 — observability | **Confirmed** | Adopted. |
| Test R — Tailwind CDN → local build | **Declined** | Architecturally wrong here. See below. |

## Bugs the plan didn't have

1. **Deictic location words are geocoded literally.** The LLM router fills
   `query` with a placeholder like `"Current"` when the user names no place. Every
   such word is *also* a real place name: `Current` → 25.408, −76.784 (a
   settlement on Eleuthera, Bahamas); `here` → Somalia; `my location` → Rwanda.
   So "how is the traffic" rendered a confident traffic map of the Bahamas.
   This is what "the traffic widget is failing" actually was.
2. **A bare traffic ask produced no widget at all.** `build_traffic_widget`
   returns `None` when no place is known. The *fast* path relies on that to fall
   through to a travel-time answer card, but the *router* branch read `None` as
   "build nothing" and left the canvas empty.
3. **Canvas adoption desynchronized the client permanently.** Adopting the
   client's snapshot called `set_session_canvas`, which minted a new version — but
   nothing emits a `component` event for an adoption, so the client never learned
   the number. Every later request then looked stale, and the client's snapshots
   (including the user's widget dismissals) were refused for the rest of the
   session, until a reload reseeded the version from `/history`.

## Bug 4 — the real mechanism

Three layers, each survivable alone:

1. **Prism forces its core/system tools past `enabledTools`.**
   `AgenticToolResolver.ts:293-322` — `coreToolsLocked` defaults to `true`, and
   prism's `AgentPersonaRegistry.registerCustom` (`:94-154`) never *reads*
   `coreToolsLocked` or `blockedTools` from a custom-agent doc. So for a CUSTOM
   agent the persona guardrail is structurally unreachable. `execute_python` and
   `create_skill` arrive via `CORE_AGENTIC_TOOLS`; `create_artifact` via
   `systemTools` (an invariant test asserts the artifact trio is always
   `system: true`).
2. **The persona's `availableTools` is dead code for our requests.** It is only
   consulted when `enabledTools` is absent, and html-notes always sends it.
3. **Our SSE loop has a four-name whitelist and no `else`.** An unhandled tool
   emitted a `tool_call` frame (so the user saw a spinner), committed nothing,
   never reset `active_tool_name` (wedging the deferred-flush check for the rest
   of the turn), and never set `canvas_settled` — so the turn ran to the
   iteration cap streaming text. **No text-to-widget fallback existed anywhere in
   the prism path.**

Net effect: the model picks `create_artifact` ("make a document") over
`canvas_add_widget` on a research-shaped ask, and the turn is invisible — spoken
answer, streamed text, empty canvas.

Layers 1-2 live in prism-service, which is read-only for us. **Layer 3 is ours
and is where the fix goes.**

## What shipped

| Fix | Where |
|---|---|
| Unhandled-tool `else`: log the name, count it, reset state | `main.py` SSE loop |
| **Safety net** — a turn that answers but commits nothing renders its answer as a `data_card` | `main.py`, `_text_answer_card_config` |
| Deictic-place denylist; never geocode `here`/`current`/`my area` | `main.py`, `is_deictic_place` |
| Router asks "which city?" instead of building nothing | `build_router_widget` traffic branch |
| Adoption no longer mints a version | `set_session_canvas(bump_version=False)` |
| TTS only cleared when this is the sole running turn | `index.js runChatTurn` |
| `relayoutOnMediaSettle` on both history-restore paths | `index.js loadHistory` |
| Boot-time check that shouts when the research path is down | `main.py` lifespan |
| `scripts/sse_probe.py` — dump every SSE event of a turn | new |

31 new guards in `tests/test_debug_wave_fixes.py`. Suite 211 → 243.

## Declined: Test R (Tailwind CDN → local build)

**This would break the app.** `create_widget` takes `htmlContent` — raw
model-authored HTML. The agent invents Tailwind utility classes at *runtime*,
which by definition cannot be in a build-time content scan, so a static build
would purge them and model-authored widgets would render unstyled. The CDN's
runtime JIT is not an oversight here; it is load-bearing.

The legitimate part of the concern is the third-party dependency (offline
resilience, CSP, latency). The right fix for *that* is to **self-host the JIT
runtime** — vendor the script to `/static` and point at it — which keeps runtime
compilation. It does not remove the console warning, which the script emits about
itself. Worth doing, but as its own change: it swaps a load-bearing asset and
wants a visual pass over all 14 widget types, which shouldn't be entangled with
five behavioural fixes the user is about to test.

## Still open

**Prism's MCP registry is empty** (`GET :7777/mcp-servers` → `[]`), so
`mcp_connected: false, tool_count: 0`. lazy-tool-service is healthy; it
self-registers on boot, so a prism restart dropped it. Pre-existing and unrelated
to these fixes. Tier-3 research asks will now at least produce a text card
(instead of nothing) — but they still can't run real research until this is
restored. Likely just needs a lazy-tool-service restart; not done unilaterally
because other sessions deploy these services concurrently.

The deeper fix is prism-side parity: teach `registerCustom` to read
`coreToolsLocked`/`blockedTools` the way the lazy-agent fork already does
(`lazy-agent-service/src/services/AgentPersonaRegistry.ts:139-144`). That is
Rod's codebase and out of scope for us to edit.
