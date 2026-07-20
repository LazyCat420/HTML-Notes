# Research reliability — plan

**Root cause found: web search has been returning zero results for every query,
because DuckDuckGo is unreachable from the NAS.**

Everything else people have been reporting about research asks — the 18-call
loops, the minutes-long turns, the narration instead of a widget, the empty
canvas — is downstream of that one fact.

## The evidence chain

```
$ curl .../internal/execute -d '{"tool":"html_notes_web_search","args":{"query":"sandals"}}'
{"results":[],"count":0,"message":"Search returned nothing. Retry with a shorter, simpler query."}
```

Zero results for *every* query tried, including trivial single words — so it is
not query-dependent. From inside the container:

| host | result |
|---|---|
| google.com | HTTP 200 |
| en.wikipedia.org | HTTP 200 |
| **duckduckgo.com** | **ConnectTimeout** |
| **lite.duckduckgo.com** | **ConnectTimeout** |

Outbound internet is fine. **DuckDuckGo specifically is unreachable.** And
`web_search` (`main.py:358`) tries exactly two engines — `ddg-lite` and
`ddg-collector` — *both DuckDuckGo*. There is no non-DDG fallback, so the whole
search capability fails closed.

Then the failure message tells the model **"Retry with a shorter, simpler
query."** So it retries. 10-18 times. Doing precisely what it was told.

**The repeat loop is not model misbehaviour, and a loop guard would have masked
this.** That is why this plan leads with the search backend.

## Verified fix available

`BRAVE_SEARCH_API_KEY` is **already in the vault**, and the Brave Search API
works from the container today:

```
HTTP 200
 - 7 Best Water Hiking Sandals in 2026 | RunRepeat
 - The 6 Best Hiking Sandals of 2026: Tested | REI Expert Advice
 - 10 Best Sandals of 2026 | Tested & Ranked | OutdoorGearLab
```

Note: memory records "Brave is CAPTCHA-walled" — that referred to *scraping*
`search.brave.com`. The Brave **Search API** is a keyed API and a different thing.

---

## P0 — Restore web search  ⬅ do this first, alone, and measure

- [ ] **0a.** Add a Brave Search API engine to `web_search`, keyed from the vault
      via `_fetch_secret("BRAVE_SEARCH_API_KEY")`.
- [ ] **0b.** Make it the **primary**; keep both DDG engines as fallbacks so this
      self-heals if DDG becomes reachable again.
- [ ] **0c.** Normalize Brave's response into the same result shape the callers
      already expect — no call-site changes.
- [ ] **0d.** Handle Brave's rate limit (free tier is ~1 req/s) with a small
      spacing guard, the same shape as the news providers.
- [ ] **0e.** Stop telling the model to retry when the failure is an ENGINE
      outage rather than a bad query. "Search is unavailable" must not read as
      "rephrase and try again" — that instruction is what produced the loops.
- [ ] **0f.** Log which engine served each search, so a silent backend swap is
      visible in the logs next time.

## P1 — Never let a dead backend look like a bad query

The deeper lesson: a tool returned a *successful-looking* empty payload with
retry advice, and nothing anywhere noticed for an unknown length of time.

- [ ] **1a.** `web_search` distinguishes "no results" from "all engines failed"
      and returns `is_error` for the latter.
- [ ] **1b.** Boot check extends to a live search probe — the existing
      `[BOOT] research path OK` line is currently a lie when search is dead.
- [ ] **1c.** `/health/app` reports search-engine status alongside MCP status.
- [ ] **1d.** Guard: no tool may return retry advice on an engine/transport
      failure.

## P2 — Defensive loop guard (still worth having, no longer the fix)

With P0 done the loop should disappear. Keep a cap so a *future* broken tool
cannot burn 18 calls and several minutes again.

- [ ] **2a.** Repeat ledger on `(tool, canonical_args)`; log the 2nd identical call.
- [ ] **2b.** Stop forwarding after 3 identical calls; emit a visible `status`.
- [ ] **2c.** Cap total research calls per turn (start 8) and wall-clock (start 90s);
      past either, go to the render step with what we have.

## P3 — Make the render step happen

Re-measure after P0 — the "ends narrating" behaviour may largely be a symptom of
having no data to render. Do not build these until that is known.

- [ ] **3a.** Re-run the 5× benchmark after P0 and record the new render rate.
- [ ] **3b.** Only if still failing: repeat the "every turn ends in
      `canvas_add_widget`" rule at the END of the prompt (recency beats
      lost-in-the-middle).
- [ ] **3c.** Only if still failing: inject a short user-role nudge after the
      research cap trips.
- [ ] **3d.** Clarify what `maxIterations: 9` actually counts — 18 tool calls were
      observed under it.

## P4 — Preserve research that was already done

- [ ] **4a.** VERIFY: does the `/agent` SSE path carry `tool.result` on successful
      `tool_execution` events? Prism's websocket path does
      (`src/websocket/index.ts:667`); the SSE path is unconfirmed. **Gate 4b-4c on
      this.**
- [ ] **4b.** If yes, buffer research results per turn.
- [ ] **4c.** Fallback chain: real card from captured research → cleaned text
      answer → honest "couldn't answer" card. Reuse `build_answer_config` /
      `_synthesize_answer_from_items`; do not write a second synthesizer.

## P5 — MCP self-healing

The outage that started this was one un-dialled connection, invisible for hours,
fixed by a single POST.

- [ ] **5a.** Boot check auto-attempts `POST /mcp-servers/<id>/connect` once when
      it finds `connected: false`, then re-checks and logs.
- [ ] **5b.** Periodic re-check (start 5 min); reconnect on transition to
      disconnected. Log every attempt; never retry hot.
- [ ] **5c.** Note the scoped-headers gotcha in the health output:
      `GET /mcp-servers` returns `[]` for ANY state without
      `x-project`/`x-username`, which is what made this look like an empty registry.

## P6 — Deferred, with reasons

- [ ] **6a.** Self-host the Tailwind JIT runtime (vendor to `/static`). Removes the
      third-party dependency and CSP/offline exposure; does **not** remove the
      console warning, which the script emits about itself. A static *build* stays
      off the table — `create_widget` renders model-authored `htmlContent`, so the
      agent invents utility classes at runtime and a content scan would purge them.
- [ ] **6b.** Prism-side parity: teach `registerCustom` to read
      `coreToolsLocked`/`blockedTools` as the lazy-agent fork already does
      (`AgentPersonaRegistry.ts:139-144`). The only real fix for
      `search_web`/`read_url`/`create_artifact` being forced into the tool set.
      **Rod's codebase — his call, not ours to edit.**

## Verification

- [ ] **V1.** Every fix gets a guard verified to FAIL first.
- [ ] **V2.** Run the same research question **5×** and record the render rate.
      **Baseline today is 1/3.** The failure is intermittent, so one passing run
      proves nothing. This number is what the work is judged on.
- [ ] **V3.** Assert turn wall-clock drops (currently 280s+ on looping turns).
- [ ] **V4.** Confirm a genuinely unanswerable ask still degrades honestly rather
      than inventing a card.
- [ ] **V5.** Confirm search results actually reach the rendered widget — not just
      that the API returned 200.

## Sequencing

**P0 alone, first, then re-measure.** It is the root cause, and P2/P3 are
guesswork until the search backend is real. P1 prevents this class of silent
failure recurring. P4/P5 are independent. P6 is a separate conversation.
