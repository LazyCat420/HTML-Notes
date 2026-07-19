# Handoff — 2026-07-19

Two waves: MCP registration ownership, then research-card quality.
Everything below is deployed and verified live unless marked OPEN.

## Wave 1 — lazy-tool-service registers itself with Prism

**Shipped:** `lazy-tool-service c2323e8`, `trading-service dd2dd74`.

`trading-service` used to write Prism's `mcp_servers` collection directly on
its own boot, for three scopes — including `html-notes-client`. Two problems:
html-notes' entire tool set depended on the trading bot booting, and nothing
reconnected the SSE link when lazy-tool-service redeployed.

The second one is what decided the design. When lazy-tool-service restarts, the
connection dies with it, and the only process guaranteed to run afterwards is
lazy-tool-service. **A consumer registering its own scope could never have
fixed this.**

Now: `lazy-tool-service/src/services/PrismRegistrationService.ts`, called from
`src/index.ts` inside `server.listen()`. Prism REST throughout — no Mongo
writes, no `PRISM_MONGO_URI` in consumer repos.

**Verified from the container's own logs after a real restart:**

```
[Prism-Reg] Announcing lazy-tool-service at http://10.0.0.16:5591/mcp/sse
[Prism-Reg] [coding/admin] MCP connected
[Prism-Reg] [vllm-trading-bot/lazy-trader] MCP connected
[Prism-Reg] [html-notes-client/lazycat] MCP connected
[Prism-Reg] [html-notes-client/lazycat] persona CUSTOM_HTML_NOTES_CANVAS verified (21 tools)
```

### Traps hit (all live)

- **`projects.json` is gitignored** and re-seeded from vault on deploy. The
  consumer list must be a code constant (`DEFAULT_CONSUMERS`); `PRISM_CONSUMERS`
  overrides. Declaring it only in `projects.json` ships nothing.
- **Import cycle**: `personas/index → personas/utils → ToolOrchestratorService →
  AgentPersonaRegistry → personas/index`. The registry builds its map at module
  top level, so **load order** decides whether it works — a lazy `await import()`
  does not help. Enter through `AgentPersonaRegistry`.
- **The persona had already drifted** — 18 tools in source, 21 live. Diff
  source-vs-live before making a file the source of truth, or an upsert silently
  un-scopes a working agent.

## Wave 2 — research cards

**Shipped:** `f65b324`, `c253602`, `c311098`. Suite 162 → 174.

Three bugs, each hiding the next. All seams — every component correct alone.

1. **No images.** The whole path existed (`render_data_card` renders
   `items[].image`, `summarised_items` sets it, `hero` picks one) and was
   starved: `web_search` returns no image, and `_enrich_news` — the only
   og:image extractor — was scoped to results whose *snippet* was empty. Most
   results have snippets, so most never got a picture. Now selects on "missing
   image OR missing snippet", and resolves og:image against the page URL.
2. **The card cited sources the answer never read.** `_ensure_data_card_quality`
   called `news_search()` for every sourceless card, so a product question got
   five Google News redirects badged "Source" — wrong corpus and a groundedness
   lie. The identical thumbnails were the tell. Now prefers the cached
   `search:<query>` results the model actually read.
3. **Markdown tables rendered as raw pipes.** The synthesis prompt asks for a
   table on comparisons; `_render_markdown` had no table branch.

### How they were found

`scratch/check_card_images.py` — playwright against the live canvas, reporting
each `<img>`'s **`naturalWidth > 0`**, not just its presence. A dead `src` looks
identical to a good one in the DOM. Bugs 2 and 3 were visible only in the
rendered card; the SSE stream carries tool events, and there is no server-side
canvas GET endpoint.

All 14 new guards were verified to **fail** against the pre-fix code, not pass
vacuously.

## OPEN

- The agent sends `config={'answer':…, 'news_topic':…}` for product asks instead
  of the documented `search_query`. The quality floor recovers, but the routing
  prompt and the model still disagree — worth reconciling at the source.
- The card is clipped by fixed tile height; sources need scrolling to see.
- Stale duplicate Prism row `lazy-agent-service` (id `6a419fe8063be887e67fabc3`,
  0 tools, `connected=false`, same URL) — not deleted.
- html-notes `/health/app` still reports ok while the MCP connection is dead.
- Naming: the GitHub repo really is `lazy-agent-service`; the local folder,
  package name, and MCP registration are `lazy-tool-service`. **Do not rename
  the registration** — the tool prefix `mcp__lazy-tool-service__*` derives from
  it, 161 references across 6 repos.
