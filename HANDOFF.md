# Handoff — 2026-07-19

Three waves: MCP registration ownership, research-card quality, then the
prompt/server seams behind "sources and text with no images".
Everything below is deployed and verified live unless marked OPEN.

**Current:** html-notes `main@dccb543`, lazy-tool-service `main@9d7e8fe`.
Suite **185 passing**. Prism MCP connected, 75 tools, both scopes.

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

## Wave 3 — the prompt promised what the server didn't do

**Shipped:** html-notes `93b1935`, `f16f394`, `dccb543`;
lazy-tool-service `9d7e8fe`. Suite 174 → 185.

Five bugs, one shape: an **agent-facing document** (the SYSTEM_PROMPT or the MCP
tool schema) advertised behaviour the server never implemented. Both halves work
in isolation — they just disagree — so the user gets a quietly degraded widget
and nothing logs an error.

**Why images were missing specifically.** The routing prompt advertised photos on
exactly one branch: news (`news_topic` "attaches the sourced stories *with their
photos*"). The research branch said only "attaches the pages as sources". A model
trying to build a good visual card was therefore **steered into the news pipeline
for product questions** — which is the Wave-2 OPEN item, now root-caused rather
than papered over. The research path now advertises photos (and delivers them),
and `news_topic` is scoped explicitly to current events. Live: "best espresso
machines under $500" now arrives as `{'search_query': …}`.

- **`content` vs `answer`.** Prompt says `answer`; the tool schema said
  `content`. The render chain is `if answer / elif items / elif content`, so a
  model following the schema *and* supplying sources had its prose dropped —
  card showed headlines only. The quality floor was blind to it too (read only
  `answer`). `content` is now an alias on both.
- **`canvas_modify_dom` advertised six actions, implemented three.** `prepend` /
  `insert_before` / `insert_after` fell off the if-chain and returned `None`;
  `commit_canvas` only aborts on an explicit `False`, so it committed the
  **unchanged** canvas, bumped the version, and reported `{"success": true}`.
  "Put a header above the chart" did nothing, forever, silently.
- **The image widget had no agent-reachable path.** `image` is in
  `_AGENT_RESEARCH_TYPES` so every picture ask is deferred to the agent, but the
  agent's 21-tool scope has no image-search tool and the injector had no branch.
  `build_image_config` (og:image + vision relevance gate) was simply unreachable.
- **`news_topic` was the only injector key with no builder fallback**, while its
  siblings call theirs unconditionally.

### Two bugs only a real browser caught

Both passed API-level checks. Same lesson as Wave 2, twice more.

- **A model-supplied image URL was never looked up.** "Picture of a red panda"
  rendered a broken frame: the model passed a plausible Wikimedia thumb path that
  returns **400**. The agent has no image-search tool, so any URL it emits is
  *recalled, not fetched*. My first fix was guarded on `not config.get("url")`,
  which **inverted** it — standing down in exactly the case it exists for. Now
  the builder runs whenever `images` is empty regardless of `url`, and a model
  URL survives only if `_image_url_loads` confirms it serves image bytes.
- **Six identical "photos" per news card.** The DOM check passed — six `<img>`,
  all loading — because they were six copies of the same *valid* Google News
  logo. A Google News `/rss/articles/` link doesn't redirect to the publisher (it
  200s on news.google.com with a JS body) and serves a constant `og:image`. The
  code comment asserted the opposite. Now rejected; items fall through to the
  publisher favicon (verified: six distinct outlets).

### Verified live, in a browser

| Ask | Result |
|---|---|
| `best espresso machines under $500` | `search_query` path, synthesised card + real images |
| `best noise cancelling headphones under $300` | 4 images, **4 loaded**, 0 fallbacks, 3 sources, Markdown table |
| `show me a picture of a red panda` | broken frame → **3 real photos, all loading** |
| `latest news on the James Webb telescope` | 6 identical Google logos → **6 distinct publisher favicons** |

`/health/app` now reports the dependency rather than hiding it, and immediately
earned its keep: it caught that html-notes' MCP row was registered under username
`lazycat` (inherited from trading-service's old config) while html-notes sends
`admin`. Tools still resolved — Prism serves them globally once connected — so
nothing *looked* broken; its own scope just showed zero servers.

## Wave 4 — news cites the publisher, not news.google.com

**Shipped:** lazy-tool-service `ba58b02`+`4a20798`, html-notes `672c188`.
Suite 185 → 187.

Wave 3 stopped the news card *lying* about having photos. It could not give it
real ones: a Google News `/rss/articles/` link is a redirect stub that never
resolves to the publisher, so there is no article page to read a photo from and
the citation reads `news.google.com` rather than the outlet.

**Measured before choosing** (this is the part I got wrong first — I recommended
GDELT from memory, then measured it):

| source | time | result |
|---|---|---|
| GDELT | **15.6s / 14.9s on success**, then 429 | real URLs + photos, 1 req/5s, throttled replies still cost 11–16s |
| gnews | **0.5s** | 8/8 with images, real publisher URLs |
| worldnewsapi | **1.1s** | 8/8 with images, best relevance |
| newsapi | 0.2s | 8/8, but recency-sorted → weak relevance |
| thenewsapi | 1.2s | free tier caps at 3 |

So GDELT was the wrong answer; the keyed providers (whose keys were already in
the vault) are ~15× faster and solve both halves at once.

`lazy-tool-service/src/services/NewsSearchService.ts` is **the first tool this
service implements rather than proxies** — everything else in `LocalToolRouter`
forwards to html-notes or trading-service. It rotates providers, skipping any
that is keyless, cooling down after a failure, or past its daily budget. The
rotation is not theoretical: during the local probe gnews served query 1, **429'd
on query 2**, and worldnewsapi took over with 6/6 images in 1.3s.

html-notes tries it first and keeps the whole old chain as fallback, so
lazy-tool-service being down costs nothing.

**Verified in a browser:** six *distinct* article photos (2560×1440, 1280×720,
1200×675, 1200×630, 1500×1200, 1536×1536), six *real publisher* links
(livescience.com, timesofindia, cnbctv18, economictimes, phys.org ×2), and a
brief that attributes inline to named outlets instead of "news.google.com".

## OPEN
- The card is clipped by fixed tile height; sources need scrolling to see.
  The image widget's grid also clips slightly with 3 images. Cosmetic.
- Stale duplicate Prism row `lazy-agent-service` (id `6a419fe8063be887e67fabc3`,
  0 tools, `connected=false`, same URL) — not deleted.
- **Prism's critic gate is dead**: `agents.criticModel` and friends pointed at
  `embeddinggemma`, an *embedding* model on text tasks. Repointed via
  `PUT /settings`, but Prism caches config at boot and **was not restarted**
  (shared service — needs the user's say-so).
- Naming — **RESOLVED.** The repo really is `lazy-agent-service` on GitHub; I
  claimed otherwise earlier from `git remote -v`, which still spelled the old
  name because GitHub 301-redirects it. Only a push reveals it:
  `remote: This repository moved`.

  The local side now matches: directory `sun/lazy-agent-service`, `package.json`
  name, and the git remote URL. Deliberately NOT renamed — the **MCP
  registration** (`mcp__lazy-tool-service__*` derives from it, 161 refs across 6
  repos including the live persona's 21 tool names; it is a protocol identifier,
  not a label), the container/image name and the `http://lazy-tool-service:7778`
  docker DNS defaults, and the telemetry `service_source` label.

  Four hardcoded paths would have broken and are now name-tolerant (new name
  first, old accepted): `build_tool_schemas.py` (would exit FATAL, taking both
  deploys with it), `trading-client/deploy.sh` (would ship a stale schema),
  `test_multi_repo_audit.py` (would silently SKIP, quietly disabling the parity
  check), and `lazy-agent-service/deploy.sh` (was copying tool_schemas.json onto
  itself through a `../lazy-tool-service` round-trip).
