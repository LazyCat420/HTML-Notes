# Handoff — 2026-07-20 (research reliability)

**Current:** html-notes `main@79667f6`, deployed to synology 2026-07-20T03:32:36Z.
Suite **272 passing**. Research path healthy: 79 MCP tools, web search reachable.

## Headline

**Web search had been returning zero results for every query.** Everything people
were reporting about research asks — 18-call loops, multi-minute turns, narration
instead of a widget, an empty canvas — was downstream of that one fact.

| metric | before | after |
|---|---|---|
| real widget rendered | **1/3** | **5/5** |
| tool calls per turn | 18 | 3 |
| identical repeats | 17 | 1 |
| turn time | 280s+ | median **44s** |

Measured with `scripts/render_rate.py` (5 runs, same question, both times).

## Root cause

DuckDuckGo is unreachable from the NAS. From inside the container:

| host | result |
|---|---|
| google.com | HTTP 200 |
| en.wikipedia.org | HTTP 200 |
| **duckduckgo.com** | **ConnectTimeout** |
| **lite.duckduckgo.com** | **ConnectTimeout** |

Outbound is fine — DDG specifically is blocked. `web_search` tried `ddg-lite` then
`ddg-collector`, **both DuckDuckGo**, so it failed closed. The tool then told the
model *"Retry with a shorter, simpler query"* — so it retried, 10-18 times a turn.

**The repeat loop was the model doing exactly what the tool asked.** A loop guard
built first would have capped the retries and left research quietly broken.

## What shipped

| | Fix |
|---|---|
| **P0** | Brave Search API as the PRIMARY engine (`BRAVE_SEARCH_API_KEY` was already in the vault). Both DDG engines stay behind it, so this self-heals if DDG returns. Response normalized to the existing `{title,url,snippet}` shape — no call site changed. `<strong>` highlight markup stripped, ~1 req/s spacing for the free tier. |
| **P1** | `web_search_ex` returns `(results, all_engines_failed)`. An engine that answers at all — even with zero hits — proves the backend is alive. On a real outage the tool returns `is_error` and says **DO NOT RETRY**; a genuine miss allows exactly one reword. Boot check and `/health/app` both probe search. |
| **P2** | Runaway guard: 4 identical calls or 12 research calls ends the turn and drops to the fallback card. Keyed on `(tool, canonical_args)`. |
| **P5** | MCP self-heal: boot reconnects when the dependency check fails; a watchdog re-checks every 5 min (`MCP_WATCHDOG_SECONDS`). |
| — | `scripts/render_rate.py`, `scripts/sse_probe.py` |

## Deliberately NOT built, with evidence

- **P3 (prompt recency / nudge)** — its own gate was "only if still failing". Render
  rate is 5/5, so the narration behaviour was a *symptom of having no data*, not a
  prompting problem. Building it would have been cargo-cult.
- **P4b/c (buffer research results into the fallback card)** — **P4a is verified
  true**: `tool_execution` events DO carry `tool.result` (prism's own
  `BenchmarkService.ts:445-453` reads it that way from the standard agentic path).
  So it is feasible. But the fallback now fires ~never, so this optimizes a path
  that doesn't run. Revisit if the fallback starts appearing in logs.

## Gotchas

- **`GET /mcp-servers` is SCOPED BY HEADERS.** Without `x-project`/`x-username` it
  returns `[]` for ANY state. That is what made a single un-dialled connection look
  like an empty registry and sent an hour of debugging the wrong way. The real
  failure shape was `connected: false, toolCount: 0, lastError: null` — fixed by
  one `POST /mcp-servers/<id>/connect`. `/reconnect`, `/refresh`, `/reload` are 404.
- **Brave: API ≠ scraping.** Memory said "Brave is CAPTCHA-walled" — that was about
  scraping `search.brave.com`. `api.search.brave.com` is a keyed API, unrelated,
  and works fine.
- **`asyncio`/`time` are imported around line 1850**, well below the search code at
  ~280. A module-level `asyncio.Lock()` up there NameErrors on import; create it
  lazily.
- **Don't patch `m.asyncio.sleep` with a lambda that calls `asyncio.sleep`** — same
  module object, so it calls itself. Capture the real one first.
- **Use `asyncio.run` in tests, not `get_event_loop().run_until_complete`** — the
  latter inherits a closed loop from earlier async tests, so tests pass alone and
  fail in the suite.
- **`render_rate.py`'s "repeats" counts by tool NAME**, so legitimate deep research
  (5 different `read_page` URLs) shows as repeats. The runaway guard keys on
  **args**, which is why it correctly does not fire there.

## Known characteristics (not bugs)

A harder question ("best budget espresso machine") runs 9-19 tool calls and
90-180s — one search plus several `read_page` calls on different URLs. It renders
**3/3 REAL**. Slower, but that is real research, and the guard correctly leaves it
alone.

## Still open

- **Prism forces its core tools** (`search_web`, `read_url`, `create_artifact`) past
  our `enabledTools`, because `coreToolsLocked` defaults true and prism's
  `registerCustom` never reads it for a CUSTOM agent. Only fixable prism-side —
  parity with the lazy-agent fork (`AgentPersonaRegistry.ts:139-144`). **Rod's
  codebase, his call.**
- **Tailwind CDN self-hosting** — vendor the JIT runtime to `/static`. A static
  build stays off the table: `create_widget` renders model-authored `htmlContent`,
  so the agent invents utility classes at runtime and a content scan would purge
  them. Wants its own visual pass over all 14 widget types.
