# Handoff — 2026-07-21 (crypto wave 3: real holder FLOW graph — who sent what to whom)

**Deployed :8035 `4292257`.** User asked whether "top holders" gives a graph of
what's connected / who transferred what to where. It didn't — the graph drew
nodes but the edge logic (two already-top-50 holders transferring in the token's
last 100 txs) ≈never fired (PEPE/MOG both 0 edges). Rewrote it to trace real
flows.

For the top 15 holders (ETH, bounded), `eth_holder_flows` fetches each holder's
own transfers of the token (`getAddressHistory`, semaphore=4 + TTL cache so the
freekey survives) and connects them. Edges carry summed **amount + count +
direction**; tap an edge → "X → Y, N tokens over K transfers". A counterparty
that isn't a top holder but links **≥2 whales** is promoted to a red **"shared
source"** node (the coordinated-seeding / pump-and-dump tell) and escalates the
verdict at ≥3 clustered whales. New metrics edge_count/source_count/
clustered_whales; a red "Linked ⚠" chip shows only when coordination exists.
Node/edge tap handlers + hot-edge styling added in index.js.

**Verified live on the NAS:** "top holders of pepe" → 57 nodes, **23 edges**, 1
shared source (e.g. Binance 0xf977 → 0x5a52, 55.98T tokens ×3). Edges populate
even under partial freekey rate-limiting; a single user won't burst like the test
did. Solana stays nodes-only (per-wallet history isn't keyless). Still not
browser-click-tested (cytoscape paint / edge-tap toast).

---

# Handoff — 2026-07-21 (crypto wave 2: keyless charts for ANY token — DexScreener + GeckoTerminal)

**Deployed :8035 `d5d5a49`.** Follow-up to the crypto feature below. User's test:
"jimothy" (a microcap pump.fun memecoin) returned no chart — CoinGecko only lists
canonical coins, so the whole card came back empty. Decision: **keyless only, no
keys ever** (user's explicit choice).

**Fix — two keyless sources added to `app/crypto.py`, both no-key/no-signup:**
- **DexScreener** (`api.dexscreener.com`) — indexes every DEX *pair* across chains
  with a name search, so it resolves anything with a live pool ("jimothy" → the
  raccoon token) + live price/liquidity/mcap/24h. NOTE: DexScreener has **no
  holder data** (pairs, not wallets) — confirmed, so it does NOT help the whale
  graph, only price/identity.
- **GeckoTerminal** (`api.geckoterminal.com`, CoinGecko's on-chain arm) — OHLCV
  candles for a DEX pool, so an unlisted memecoin gets a real price chart.

**Resolution is now tiered** (`resolve_crypto`): MAJORS map (top ~35 coins →
canonical CoinGecko id, bulletproof, no search) → CoinGecko name/symbol-EXACT →
**DexScreener fallback** (the long tail) → CoinGecko fuzzy. A CoinGecko coin whose
chart comes back empty (rate-limit / thin coverage) **also** falls back to the
GeckoTerminal pool chart (`_gt_chart_for_contract`), so a chart renders no matter
what. `ref` is the routable id: a coin id, or `dexs:<chainId>:<pool>` →
`_dexs_snapshot` / `/api/crypto` both dispatch on the `dexs:` prefix.

**Query-hygiene fixes (found via live testing):** `_clean_crypto_query` strips
filler ("price of X", "who holds X") to token words before searching;
`_best_pair` scores name/symbol word matches and returns None on no-match instead
of grabbing an unrelated high-liquidity whale (fixed "jimothy the raccoon" → wrong
$17B "00" token, "who holds pepe" → "MinnowHolds" $48 scam). 429-aware retry on
CoinGecko. Microcap cards show liquidity + DEX instead of ATH/high/low.

**Verified live on the NAS:** `/api/crypto/jimothy-the-raccoon` → JIMOTHY $0.027,
$27.1M mcap, 115 chart points; "price of jimothy" and "jimothy the raccoon chart"
both render the card with a chart. dogecoin/solana/bitcoin/pepe/$WIF all resolve
correct. **Solana holder GRAPH still degrades to the price card** when the public
RPC rate-limits (the accepted keyless-only limit — a free Helius key in the vault
would fix it, no code change, but user chose no keys).

---

# Handoff — 2026-07-21 (crypto + wallet-graph feature: tokens, holders, whales)

**New capability:** the canvas now answers crypto asks — a token's price/chart,
a written token report, a single wallet's holdings, and the headline feature: a
**holder-network graph** that shows the top wallets holding a token as nodes
(sized by % of supply) with transfer edges between them, plus a concentration
read that calls out whether a few whales control supply (pump-and-dump / rug
risk) or it's fairly distributed.

**New module `app/crypto.py`** (self-contained, no main.py deps, unit-testable):
keyless-first data sources, each degrading to `{}`/`[]` on failure —
- **CoinGecko** (no key): token search/lookup by name/symbol/**contract address**,
  price, market cap, price chart. All chains. `resolve_crypto()` is the identity
  resolver — note→symbol→contract, with a scam-guard (search for "bitcoin"
  returns a memecoin whose *symbol* is literally "BITCOIN" at rank ~5000
  alongside real BTC at rank 1; we match name-exact then symbol-exact, each
  sorted by market-cap rank, so impostors lose).
- **Ethplorer `freekey`** (no signup, rate-limited): ETH ERC-20 top holders
  (with `share` = % of supply, exactly what the graph needs), transfers between
  them (graph edges), and single-address holdings.
- **Solana RPC** `getTokenLargestAccounts` + owner resolution (nodes +
  concentration only — no edges on Solana v1; RPC too heavy keyless). Public
  endpoint 429s constantly → uses a **`HELIUS_API_KEY`** from the vault when
  present, else degrades honestly.
- `build_holder_graph()` is **pure** (holders+transfers → cytoscape elements +
  metrics), so it tests without the network. Classifies the biggest holders from
  a curated `KNOWN_EVM` label map (Binance/Coinbase/Kraken/OKX hot wallets, burn
  addrs, DEX routers, Wintermute) — a token whose top 3 "holders" are exchange
  custody is custodial, not whale-controlled. Metrics: top-10 **real-holder**
  concentration (ex-CEX/burn/LP), CEX/burn/LP share, whale count, HHI, Gini,
  edge count, and a plain-English verdict + tone (bad/warn/ok).

**Four new widgets** (builders in `main.py` right after `build_stock_report_config`;
the stock builders are the template throughout):
- `crypto_card` — price header + Chart.js chart + range tabs + key stats +
  tap-to-copy contract-address chips. Range tabs refetch `/api/crypto/<coin_id>`
  directly (no agent turn), exactly like the stock card. Alpine
  `cryptoCardWidget` in `widgets.js`.
- `wallet_graph` — verdict banner + metric chips + **cytoscape** graph +
  legend + explorer link. `render_wallet_graph` bakes a hidden
  `<pre><code class="language-graph">` block that `index.js` hydrates into an
  interactive concentric graph (node color by kind, size by √share, tap a node →
  copies its address + toasts its %), **mirroring the language-chart→Chart.js
  hydration**. Cytoscape was already bundled at `static/lib/cytoscape.min.js` and
  is now loaded in `index.html`.
- `crypto_report` / `wallet` — render via the existing `render_data_card`
  (markdown brief / holdings list), no new renderer.

**Routing.** `ROUTER_WIDGETS` gains `crypto`/`crypto_report`/`wallet_graph`/
`wallet` (LLM-router specs) + dispatch in `build_router_widget`. A **conservative
fast lane** (regexes near `STOCK_WORD_RE`) fires only on strong signals — a named
coin/`CRYPTO_WORD_RE`, a `$CASHTAG`, or a `0x…` address — and is placed **BEFORE
the stock branches on purpose**: `STOCK_WORD_RE` matches "crypto", so without
ordering a crypto ask would be stolen by stock-news/stock-report. Softer asks
fall through to the LLM router (which now knows the crypto types). Fast-lane
builds are pre-resolved (fast + cached) so an unresolvable mention falls through
instead of rendering an empty shell. 14/14 routing test cases correct, no
collision with stock/weather/products asks.

**Verified locally (python3.11 compile clean, app boots, route registered):**
- Resolution: bitcoin/btc/ethereum/eth/solana/sol/pepe/dogecoin all → correct id.
- BTC card: $66.3K, $1.33T mcap, rank 1, 121 chart points.
- PEPE holder graph: 50 nodes (4 CEX / 14 whales / 1 burn / 31 holders), 18.2%
  exchange custody flagged, top-10 real ~25% → "moderately distributed". Renders
  with the language-graph block + verdict.
- Empty/unresolved configs degrade to a readable data_card (no blank shells).

**NOT click-tested in a live browser** — the cytoscape hydration, node-tap toast,
graph persistence round-trip (serialize strips `.graph-canvas`, keeps the config
block — same contract as charts) and the range-tab refetch are built to the
existing chart pattern but were verified by construction + unit render, not by
driving the UI. First live check: ask "who holds pepe" and confirm the graph
paints + a node tap copies the address.

**Known limits / next:** Ethplorer `freekey` is shared and 429s under rapid
repeat (softened by a 60s TTL cache in `crypto.py`; a paid Etherscan/Ethplorer
key would remove it). Solana needs a `HELIUS_API_KEY` in the vault for reliable
holder data — without it the public RPC usually rate-limits and the graph
degrades to the price card. Transfer **edges** only appear when top holders have
moved supply to *each other* in the recent 100-tx window — sparse for large-cap
tokens (correct: fair large-caps show few whale-to-whale edges; manipulated
microcaps show a dense cluster). Blockscout (multi-chain EVM holders) was coded
around but is unreachable from the dev sandbox; Ethplorer is the ETH path.

---

# Handoff — 2026-07-21 (video-by-time wave 2: "newest" made idempotent)

**Deployed:** synology `:8035` (`7096dab`) — 561 pytest green (21 in
`tests/test_video_recency.py`). Follow-up to the wave below: user retested with
"newest paul barron video" (no "Network") and got a DIFFERENT video on every
identical ask. Three holes in wave 1, all reproduced live before fixing:

1. **The bidirectional name gate rejected the REAL channel.** Subject
   "paul barron" vs channel "Paul Barron Network" → bwd 0.67 < 0.7 → feed path
   never ran for the natural phrasing (users drop trailing words). Gate is now
   forward-containment ≥0.7 + backward sanity floor ≥0.5 (still rejects
   "top gun maverick" → "Top Gun" on fwd 0.67, and "paul barron" → a long fan-
   channel title on the floor). Single-word subjects bind ONLY on exact equality
   with the TOP channel result ("fireship" → Fireship; "bitcoin" never binds).
2. **The unseen-first filter ROTATED the feed** — repeat asks served feed[1],
   feed[2]… "Newest" is a factual ask with ONE right answer: already-shown
   exclusion removed from the strict path; repeats return the same latest upload.
3. **Fallback tiers lost strictness.** A consent-walled primary scrape fell to
   the relevance-ordered scraper pool ("newest"→"popular" silently).
   `_youtube_results_via_scraper` now takes `strict_recency` (date-sorted fetch,
   age-sorted output, NO diversity reshuffle), and the builder age-sorts
   relevance-fallback hits before picking.

**Publish-time verification is now observable:** the picked video's `published`
timestamp rides in the widget config and the server logs
`[YOUTUBE] newest via channel feed: <id> published <ts> (~N min ago)` — grep
that to answer "did it verify the time" from the container log. Verified live
with the exact failing phrasing: 3 identical runs → same video (published
2026-07-21 20:14 UTC) = feed[0] of the resolved Paul Barron Network channel.

**Rule of thumb this wave encodes:** deterministic asks (newest/live/named
channel) must never pass through `pick_varied_video` OR the shown-this-session
exclusion — both exist for *variety* asks only.

---

# Handoff — 2026-07-21 (video-by-time: newest video + named-channel feed)

**Deployed:** synology `:8035` (`cd2ae92`) — 558 pytest green (18 new in
`tests/test_video_recency.py`). Server-only (`app/main.py`), no schema change.

**The bug (user report):** "newest Paul Barron Network video" returned a days-old
popular clip instead of the upload from 42 min ago. The engine parses publish age
(minute precision, `youtube_search.py:_parse_age_days`) but threw the ordering
away. THREE compounding causes, all fixed:

1. **"newest" never triggered date mode.** `RECENCY_RE` lacked it (`\bnew\b`
   doesn't match "newest"). Added it, plus a stronger **`NEWEST_RE`** (newest /
   most recent / latest / just posted / N mins ago / this morning / past hour) for
   STRICT recency.
2. **The blended scorer overrode date order.** freshness is a 0.4-weight,
   year-scaled axis (`1 - age_days/365`) — it can't separate 42 min from 3 days,
   while authority (log-views) favors the older video that had time to rack up
   views, so a fresh upload ALWAYS lost the blend (proved: 3-day/60k-view scored
   2.2584 vs 42-min/800-view at 2.1893). Added **`strict_recency`** to
   `search_youtube_videos`/`_search_youtube_scrape`: sort purely by `age_days`,
   bypassing the scorer, channel-diversity cap AND rerank.
3. **`pick_varied_video` randomised the final pick** "for variety". A strict
   newest ask now takes `hits[0]` deterministically.

**The real fix for named creators.** A keyword search for "Paul Barron Network"
returns that channel's clips mixed with everyone else's in popularity order — so
search alone can't answer "newest <creator>". Added `_resolve_youtube_channel`
(channel-only results filter `sp=EgIQAg%3D%3D` + parse `channelRenderer` +
**strict bidirectional name-match gate** `_yt_channel_name_match` ≥0.7 both ways,
so "top gun maverick" won't bind to a fan channel "Top Gun"; single-word subjects
skip resolution) → `_youtube_channel_uploads` reads the keyless, strictly
reverse-chronological uploads RSS (`youtube.com/feeds/videos.xml?channel_id=…`),
so `feed[0]` IS the latest upload. The video builder ([main.py] `build_router_widget`
video branch) tries this FIRST for a `want_newest` ask, falls through to strict
keyword date search, then to relevance. MCP `html_notes_youtube_search` honors
`order=date` / a "newest" query as strict too. **Verified live:** builder returns
the Paul Barron Network upload from ~145 min ago, matching `feed[0]`.

**Seams / gotchas:** the legacy fast-path cascade (`main.py` ~7705, `use_lazy_agent`
mode) is SKIPPED in prism mode — the live path is `build_router_widget`, which is
what I patched. `_yt_fetch_html` = httpx-first (a browser UA clears the
results/feed pages) with `_scrape` real-browser fallback. `_unescape` is now
imported from youtube_search as `_yt_unescape` (channelRenderer titles are
JSON-escaped, not HTML). The scorer in `youtube_search.py` is UNCHANGED (the strict
path bypasses it) so the `bench/` numbers don't move — if you ever want freshness
to matter on the *blended* path too, steepen its curve there and re-run the bench.

---

# Handoff — 2026-07-21 (stock discovery: index scoping + provenance tagging)

**Deployed:** synology `:8035` (`8ff0cd4`) — 540 pytest green. No lazy-agent
schema change (server-only logic in `app/main.py`).

**The bug (user report):** "top 5 stocks in the s&p" returned CPHI/VIVK/NBIS/
CBRS — micro-caps, not S&P members — then a follow-up "why are these trending?"
routed to the agent, which ran `stock_news` per ticker, found nothing, and said
"the news doesn't talk about them." Two root causes: (1) the "s&p" qualifier was
ignored — the list came from Yahoo's site-wide `trending/US` most-viewed+momentum
feed, filtered to nothing; (2) turns weren't context-aware — the follow-up had no
record of WHERE the list came from (momentum, not news), so it hunted for news
that never existed.

**Fix 1 — accuracy (index scoping).** `build_trending_compare_config` now calls
`_universe_from_message` (matches s&p / sp500 / spx). When an index is named it
pulls a large ranked screener pool (`day_gainers` for a bare "top" ask, else the
kind word) and keeps ONLY members via `_index_constituents("sp500")` — cached 24h
from the datahub S&P-500 CSV (`_INDEX_SOURCES`), in screener-rank order. Empty
set = "unknown", NEVER filter on it (would drop every ticker); degrades to the
unscoped feed and says so in provenance. Verified live: the exact bug query now
returns SNDK/WDC/TER/MU — all real S&P members.

**Fix 2 — context-awareness (provenance tag).** Builders can set
`config['provenance']` = HOW the data was gathered + each item's move.
`_widget_detail` now = `_widget_content_gist` (renamed old body) + provenance,
provenance LAST so anaphora names aren't clipped at the 200-char ledger budget.
That rides into the session ledger → `build_turn_context` RECENT TURNS → agent
system prompt, so a follow-up sees "picked via S&P 500 gainers today … DHR +4.2%"
and can answer from momentum instead of an empty news search. The mechanism is
generic — any builder can tag provenance now; only the trending path uses it yet.

**Seams / extend here:** provenance is the reusable context-tagging hook — wire
it into `build_stock_news_config`, `build_news_config`, `build_answer_config`
next so every follow-up knows its source. `_INDEX_SOURCES` only has sp500; add
nasdaq100/dow the same way (keyless CSV, Symbol in col 1). Yahoo's `day_gainers`
screener caps at ~25 rows, so an S&P intersection can yield <N (query returned 4,
not 5) — acceptable, title reflects actual n. Tests: `tests/test_trending_stocks.py`
(31, +8 new: universe regex, scoped filter, unscoped provenance, degrade path,
`_widget_detail` provenance).

---

# Handoff — 2026-07-21 (deep-audit fix wave + widget pack v2)

**Deployed:** synology `:8035` (d8ed34a) + lazy-agent-service schema
(fc617e6) — new widget_type enum verified live in the container. 528 pytest +
16 node green (34 new in `tests/test_widget_pack_v2.py`). Full verified audit:
`AUDIT_2026-07-21.md` (48-agent adversarially-verified deep-research pass —
40 findings, 0 refuted). Live E2E: products 15s (router-local in prism mode),
profile_card 37-56s, versus_card 146s w/ winner highlighting; browser-verified
chart survives serialize→adopt→reload and checklist edits survive reload.

Post-deploy browser verification caught TWO client bugs pytest could not:
(1) Alpine reactive-proxy aliasing — `this.items = initialItems` made the
done-toggle mutate the restore baseline too; snapshot the baseline as JSON
before assigning. (2) inside an x-for row, `$el` is the cloned row element
(no id), so `$el.id`-keyed localStorage saved under the 'x' fallback while
init() read the root-id key — `widgetStorageId()` now climbs to
`.widget-container` (applied to checklist/notes/reminder keys).

## New premade widget types (7) — for faster/richer data display
All registered in `WIDGET_RENDERERS`, stamped with data-sig/type, routed in the
SYSTEM_PROMPT and in the live canvas_add_widget tool schema
(lazy-agent-service `tool_schemas/html-notes/html-notes.json`, flat artifacts
rebuilt for lazy-agent-service / trading-service / trading-client):
- `versus_card` — 2-4 entities side-by-side, aligned stat rows, per-row winner
  highlighting, verdict strip ("AAPL vs MSFT", "compare these laptops").
- `multi_chart` — generic multi-series contract `{labels, series:[{label,
  values}], normalize?, unit?}` (also accepted by `chart`); distinct slug so
  coerce_widget_type can never hijack it into a stock_card. Non-ticker
  comparisons ("rainfall Seattle vs Portland") are now ONE chart.
- `table` — typed columns (`format: number|currency|percent` right-align +
  format), server-side sort, 50-row cap with "+N more" note; accepts legacy
  {headers, rows} shape.
- `kpi_row` — 2-8 big-number tiles with colored deltas (`good: up|down` sets
  polarity) and inline-SVG sparklines (no canvas/script needed).
- `timeline` — dated events on a rail, date-sorted server-side; agent emits
  `{timeline_query}` and `build_timeline_config` researches news and maps
  events to sources (server attaches image+url; hand-built events get model
  image URLs STRIPPED).
- `profile_card` — person/company infobox; agent emits `{profile_query}`,
  `build_profile_config` fetches the Wikipedia summary + thumbnail (never a
  model URL) and structures facts via one fast_llm_json pass; degrades to the
  answer card when no article exists.
- `progress` — labeled goal/percentage bars (value/target or pct).

## Fixes (see AUDIT_2026-07-21.md for evidence + line refs)
Silent-success class: agent tier no longer reports success on failed/no-op
mutations (commit tracking in execute_mutation); update_widget rejects factory
widgets + aborts when nothing matched; canvas_modify_dom/update_widget bump
data-sig so the client actually repaints; reconcileCanvas now paints/removes
create_widget glass-cards; chart config block preserved (chart survives
serialize→adopt→reload); data-revived stripped on serialize; renderError no
longer wipes the canvas (transient banner); checklist edits persist via
localStorage (notesWidget pattern — factory template now calls
toggleTask/persist).

Guardrails: create_widget title escaped + htmlContent audited
(audit_html_fragment; failed audit renders as text); iframe_app title/icon
escaped, sandbox loses allow-same-origin, embeddable check is host-parsed;
SSRF guard `_is_public_http_url` on read_web_page + /widgets/embed;
build_answer_config sources fenced as untrusted data + today's date injected.

Routing: prompt names real types (music→mini_music_player, embed→iframe_app)
+ `_WIDGET_TYPE_ALIASES` rescue map; "make it green" with a focus widget no
longer hijacked by the theme intercept; settings turns persist to DB (survive
reload); router video branch date-sorts recency asks; products builds locally
in prism mode again; router defers appearance asks; reminder reuses the open
countdown.

## Known-open (deliberate, in AUDIT file)
- create_widget jsContent still executes (product feature) — srcdoc isolation
  is the real fix; DOMPurify stays permissive by design (Alpine).
- /internal/execute unauthenticated + prism enabledTools fail-open.
- Agent tier still lacks P3 traffic-prefix reuse; follow-up type-morph seam
  (verbatim-id bypass); data-req-seq only guards media swaps.
- Backlog widgets: stock volume underlay, map+list split, weather compare
  resolver, sports standings tool, gallery_facts/video_shelf/calendar.

---

# Handoff — 2026-07-20 (trending-stocks discovery)

**Deployed:** synology `:8035`. 480 pytest green (19 new in
`tests/test_trending_stocks.py`). Live-verified: the exact failing ask now
renders a 5-series compare chart.

"compare the top trending stocks this last month, top 5 on a chart" broke
because it is a DISCOVERY ask: tier-1 regexes miss it, the tier-2 router
classified it `stock`, and the stock builder's `_resolve_ticker("top trending
stocks")` gets nothing from Yahoo symbol search → all builds empty → degraded
answer card (no chart, LLM-guessed table). It never reached the agent — and a
direct probe of prism `/agent` on this ask showed the agent takes ~189s and
invents its candidate list (ranked 10 mega-caps from memory; the real trending
list was AMC/IREN/ACHR…), so agent routing was the wrong fix anyway.

Fix (deterministic, ~2-10s): Yahoo's KEYLESS discovery feeds →
`build_trending_compare_config` → the existing normalized multi-series chart.
- `trending/US` for "trending/hottest"; predefined screeners `day_gainers` /
  `day_losers` / `most_actives` for gainers/losers/actives (`_trend_kind`).
- "top N" honored (cap `_COMPARE_MAX_TICKERS`=8, default 5); overfetch +3 so
  snapshot failures don't thin the chart; crypto/futures/indices filtered.
- `_range_from_message`: today→1d, week→5d, quarter→3mo, year→1y, month→1mo
  (default 1mo). Ordered longest-window-first.
- Routing: new `stock_trending` ROUTER_WIDGETS type, PLUS a
  `TRENDING_STOCK_RE` guard inside the `stock` router branch so a plain
  `stock` classification still lands in discovery. Explicitly typed tickers
  ("top performers: NVDA vs SPY") beat the feed. Works in prism AND lazy mode
  (both go through the tier-2 router for this shape).

Console-log trap found during the audit: `index.js` prints
`route: fast-path → undefined` for ANY non-agent route — a router-path debug
event has `widgets`, not `widget_type`. "fast-path → undefined" means TIER-2
ROUTER, not a tier-1 heuristic.

---

# Handoff — 2026-07-21 (new widgets: converter, reminder, notes-v2 + Obsidian)

**Deployed:** synology `:8035`. 461 pytest + 16 node green. All three
live-verified (browser-driven + on-disk vault file inspected).

Three widgets added from the brainstorm; each is a full widget_type (renderer +
Alpine component + router catalog + SYSTEM_PROMPT line + fast-path), following
the recipe in the theme handoff below.

## 1. Converter (`converter`)
Interactive calculator + unit + currency, all client-side (instant, no agent
turn per calc). Server `build_converter_config` only seeds the tab from the
phrasing; the widget computes. Currency via keyless open.er-api.com
(`GET /api/fx/{base}`, cached ~5m). Fast-path `CONVERT_INTENT_RE` (guarded off
"X vs Y" stock-compare).
GOTCHA fixed twice: a `<select x-model>` whose `<option>`s come from `x-for`
shows the FIRST option, not the bound value, when the model is set during
`init()` (options don't exist yet). Fix: set select-bound fields to `''` then
restore them in `$nextTick`. Reassigning the same value does NOT re-sync — you
must clear first. This bites any select+x-for+x-model widget.

## 2. Reminder (`reminder`)
Counts down to a relative ("in 20 min") or absolute ("at 3pm", "tomorrow at
9am") time, then fires a browser Notification + a WebAudio beep. Server parses
time+label; absolute times resolve CLIENT-side (user's timezone). Target
persists in localStorage (survives reload); snooze/+5/+10, dismiss. Client-only
firing — works while the tab is open (server-push reminders would need a
different mechanism). Fast-path `REMINDER_INTENT_RE` (before the clock/timer
branch, since a reminder carries a time too).

## 3. Notes v2 (`notes`) + Obsidian vault — the big one
Rewrote the plain-textarea notes widget into a markdown editor:
- Edit ⇄ Preview (marked + DOMPurify), **interactive GFM checklists** (clicking
  a box in preview toggles the `- [ ] / - [x]` source line via
  `toggleTask`/`onPreviewClick`), tables, headings, tags.
- **Save to Obsidian vault**: `POST /api/notes/save` writes `<slug>.md` with
  YAML frontmatter (title/tags/created/updated/source) to `OBSIDIAN_VAULT_DIR`.
  Upsert preserves `created`. `GET /api/notes/list` + `/api/notes/load` back
  reopening. Slugs are path-safe (`_note_path` refuses anything that escapes the
  vault). Frontmatter is hand-rolled (no pyyaml dep) — write via
  `_yaml_frontmatter`, read via `_parse_frontmatter`.
- **Persistence subtlety**: a textarea's typed value is NOT in serialized
  `innerHTML`, so canvas serialization drops note edits. The widget autosaves to
  localStorage keyed by widget id and restores on init — BUT only when the
  server content is unchanged from the saved baseline (`s.base === cfg.content`);
  if the agent rewrote the note, the server content wins. This is the fix for
  "typed notes vanish on reload" that the old widget had.

### Vault location (IMPORTANT for the Obsidian piggyback)
`OBSIDIAN_VAULT_DIR` defaults to `data/vault` → `/app/data/vault` in the
container → `./data/vault` on the NAS (host-mounted, works out of the box; that
is where `weekend-plans.md` landed in the live test). **To use a REAL Obsidian
vault**, mount it in docker-compose (writable, uid 1001) and set
`OBSIDIAN_VAULT_DIR` to the mount path — commented example is in
`docker-compose.yml`. Needs a compose edit + redeploy on the NAS; the app code
already writes valid `.md` the moment the dir points at the vault.

## Not done / watchlist
- Reminders don't fire when the tab is closed (client-only). A durable
  server-side reminder would need push or a poll-on-load sweep.
- Notes: no in-widget "open a saved note" picker yet — `/api/notes/list` +
  `/load` exist, but the widget only SAVES. A "browse vault" affordance is the
  natural next step. No conflict handling if the same note is edited in Obsidian
  and the canvas simultaneously (last-write-wins).
- FX cache is the shared 5-min tool cache; fine for currency.

---

# Handoff — 2026-07-21 (agentic theme system + settings widget)

**Deployed:** `deabfee` → synology `:8035`. 416 pytest + 16 node green. All 8
themes + the settings panel + the agentic flow screenshot/flow-verified live.

## What shipped
Ask the agent for a look ("dark mode", "forest theme", "make it pastel", "egg
colors") and it applies the closest of 8 palettes and pops a settings panel;
the panel's swatches also switch themes on click. Fully agentic per the user's
direction — theme change is a UI-control action, so it also has a deterministic
fast-path so a bare "dark mode" is instant.

**Themes** are `<html data-theme="…">` selecting a palette in hud-theme.css.
6 dark (hud=default cyan, midnight=dark-mode, forest, ember, grape, mono) +
2 light (egg=warm cream, pastel=cool). Each is a 3-tone palette (bg / panel /
accent) + text; texture (grid/scanlines/brackets) is opacity-controlled so
light themes drop the cockpit noise.

## Key implementation facts / gotchas
- **The HUD's hardcoded cyan literals were converted to vars** (sed +
  `--hud-accent-rgb`, `--hud-title`, etc.), so a palette is just ~15 var
  overrides. If you add accent CSS, use the vars or it won't theme. A test
  (`test_hud_literals_were_converted_to_vars`) guards against new raw literals.
- **Light themes needed a text-contrast fix**: index.css drives default widget
  text off ITS OWN `--text-color`/`--text-dim` (light blue) and never remaps
  `text-white`. On cream/light both vanish. The egg/pastel blocks override
  those index vars dark AND force `[class*="text-white"]`/slate-900/100/200 to
  the palette ink. Any NEW light theme must do the same.
- **Charts can't read CSS vars** (they draw on canvas). `window.HN.chartColors()`
  feeds them the palette ink/line; `hn:theme` event redraws live charts. Series
  colors stay vivid (readable on both). Both chart paths covered: language-chart
  blocks (index.js) register via `window.HN.registerChart`; the stock card
  redraws on the `hn:theme` event.
- **Pre-paint**: an inline `<head>` script applies the saved theme before first
  paint (no flash). The full engine (`window.HN.applyTheme`, persist, chart
  sync) is in index.js.
- **Alpine `:style` with a STRING replaces the style attribute** (wiped the
  swatch bars' width → invisible). Use the object form. And in an f-string the
  object braces must be doubled `{{ }}`. Both bit me; both fixed.
- **Settings is a singleton** (`widget_id='settings-panel'`), so repeated theme
  changes update the same panel. It also carries the voice-reply toggle (wired
  to the real mute via `window.HN.setMuted`) and reset-layout.

## Server
`THEME_CATALOG` (name/label/swatch/keywords) + `pick_theme(text)` — fuzzy,
typo-tolerant, light/dark tiebreak. Fast-path `THEME_INTENT_RE` intercept (with
a media guard so "dark ambient music" is untouched) → `_stream_settings`. The
agent path knows the same route via SYSTEM_PROMPT for mixed phrasing.

## Adding a theme (recipe)
1. `:root[data-theme="X"]` block in hud-theme.css (copy an existing one; set the
   ~15 vars; light themes also set `--text-color/--text-dim/--ice` + zero the
   texture opacities). 2. A `THEME_CATALOG` entry in main.py (name matching the
   CSS, label, 3 swatch hexes, keywords). 3. That's it — settings renders it,
   pick_theme matches it, tests assert the pairing.

## Not done / watchlist
- Chart SERIES colors are palette-independent (vivid defaults). Fine on all 8,
  but a truly monochrome "mono" chart would want series from the accent ramp.
- Themes recolor structure + text; a few deeply-baked Tailwind widget accents
  (semantic green/red for up/down, the image-card white matte) are intentionally
  left — they're meaning, not theme.
- The next brainstorm (missing basic widgets) is tracked separately.

---

# Handoff — 2026-07-20 (multi-ticker compare + x-for round-trip + tie-break)

**Deployed:** `b585494` → synology `:8035`. 379 pytest + 16 node green.
Live-verified in one run: "NVDA vs SPY vs TSM" → ONE chart
(`stock-compare-*`, "— 6mo % change", 3 datasets, legend), then two
stock-card turns with canvas round-trips → ZERO Alpine errors.

## 1. Multi-ticker comparison
"NVDA vs SPY vs TSM" used to fan into one stock_card per ticker (router
prose said "never open a second stock" — prose can't outvote sampling) and
each failed. Now: `build_stock_compare_config` (app/main.py, after
stock_snapshot) fetches N snapshots concurrently, drops failures, aligns on
the common tail, normalizes each series to % change from range start, and
renders through the EXISTING chart widget — Chart.js draws N datasets +
legend natively, zero client work. Cap 8 tickers.

Wired in three places:
- Router stock builder: compare phrasing (`_COMPARE_SPLIT_RE`) → explicit
  uppercase tickers, else per-segment `_resolve_ticker` (names like
  "nvidia vs taiwan semi").
- Route cleaner: N stock specs collapse into one joined "A vs B" query.
- Agent path: `widget_type='chart'` + `config.compare_symbols=[...]` →
  injector rebuilds server-side; SYSTEM_PROMPT teaches the route ("To add
  a ticker, call again with the SAME widget_id and the full symbol list" —
  follow-ups like "add AMD" work through the normal update path).

GOTCHA: `coerce_widget_type` force-converts chart→stock_card when a cached
symbol appears in the title/id — which is EXACTLY what a compare chart's
title looks like. It now skips configs with `compare_symbols` or >1
dataset. Don't remove that guard.

## 2. The Alpine "r is not defined" storm (stock card)
`getCleanedCanvasHtml` serialized Alpine-EXPANDED `<template x-for>` output
(range buttons, metric rows); the server adopted that HTML as canonical;
the next `Alpine.initTree` evaluated loop-scoped bindings outside any
x-for scope — hundreds of console errors + duplicated nodes on
re-expansion. Fix in index.js: strip every `template[x-for]`'s generated
siblings before persisting — walk siblings until the first with `x-show` /
`x-for` / `.close-widget-btn` (audited invariant: every static sibling
after an x-for in factory.py carries one of those). Covers the stock card
and every future x-for widget; the older youtube/checklist hand-rolled
strips remain for their extra rules.

## 3. Jimothy double-box (ambiguous-tie targeting)
"tell me more about jimothy" scored 1.00 against TWO cards (his own AND a
reddit-lawsuit card whose gist mentioned him); first-in-DOM-order won and
the lawsuit card got overwritten with meme-coin content. `_followup_target_id`
now collects contenders within 0.1 of the best score: the recency focus
wins ties (the thread the user is in), else the most recent contender.

---

# Handoff — 2026-07-20 (stacking in-place updates)

**Deployed:** `5497273` → synology `:8035`. 372 pytest + 16 node green.

In-place data_card updates no longer hard-replace: the new answer renders on
top, previous content survives under an "**Earlier**" rule, and once the card
passes `_STACK_WORD_BUDGET` (800 words — middle of the user's requested
500-1000 range) the oldest words roll off the bottom. Sources/items
accumulate too (dedupe by url, newest first, cap 8). Implementation:
`_stack_data_card_update` + `_session_widget_configs` (per-session in-memory
config store, same lifetime as the turn ledger), wired into the agent
`canvas_add_widget` path just before render.

Guards: data_cards only (stock/map/clock are stateful displays → always
replace); only genuine in-place updates (id already on canvas); substring
check so a model that rewrote WITH history doesn't get it duplicated back;
no stub-stacking when the new answer alone fills the budget.

Live-verified: three costco follow-ups grew one card 35 → 223 → 545 words
with the Earlier section visible; `[WIDGET STACK]` log line shows the merge.

Note: the router/fast-lane path does NOT stack (agent path only) — if a
fast-lane build updates the same card id, the remembered config refreshes
but no merge happens. Extend there if it ever matters.

---

# Handoff — 2026-07-20 (anaphora: "tell me more about Miku" = the restaurant)

**Deployed:** `3d9ce6e` → synology `:8035`. 361 pytest + 16 node green. The
exact live scenario (sushi card → clones video → "tell me more about Miku")
re-driven twice; final run resolves Miku to the Vancouver restaurant, updates
the sushi card in place, video untouched.

## The failure
"Tell me more about Miku" right after a sushi card listing "Miku, Tojo,
Shizen" produced a Hatsune Miku YouTube video in the video widget. The model's
world-knowledge prior won because the session context never reached it.

## The fix — three layers (each exposed by re-driving live)
1. **Ledger gist keeps distinctive names** (`_widget_detail`): it kept the
   first 160 chars of the answer — which truncated exactly before "…include
   Miku, Tojo, and Shizen". Now: 150-char prefix + up to 8 proper-noun-ish
   tokens from the REST of the text, budget 200 (and `record_turn`'s clip
   raised to match — 160 there silently re-amputated the names).
2. **Body-scan targeting tier** (`_followup_target_id`): the live sushi answer
   was ~2000 chars dense with names, so the 8-name cap filled before "Miku" —
   gists can't hold every entity. New tier between gist scoring and the
   deictic fallback: scan every widget's full rendered text on the canvas,
   require FULL subject-token coverage, accept a UNIQUE hit only (two body
   matches = ambiguous = don't guess).
3. **Anchored directive + prompt rule**: the follow-up directive/rewrite now
   include "currently showing: <title — gist — …±window around the referenced
   name…>" via `_widget_showing(session, wid, message)` — the model sees
   "…options include Miku, Tojo…" next to the ask. SYSTEM_PROMPT gained
   "NAMES RESOLVE AGAINST THE CONVERSATION FIRST" (canvas meaning beats the
   famous meaning).

Verified decision trail in container logs:
`[WIDGET TARGET] follow-up subject found in the BODY of #vancouver-sushi-trip
— beats recency #video-...` → agent searched "Miku Vancouver restaurant".

## Answer to "do we have a context-around-the-word system?"
We do now, at three ranges: ledger gists (cheap, per-turn), full-body scan
(exact, targeting only), and the quoted ±window in the directive (what the
model actually reads). All session-scoped — nothing persists past the
conversation, per the current scope.

## Tests
SEAM E in tests/test_followup_targeting.py (gist keeps names; body scan;
unique-hit rule; anchor window; record_turn budget). Anchor/prompt pins in
tests/test_agent_guardrails.py.

## Typo tolerance (follow-up commit `c6ff64f`)
All three matching ranges are now fuzzy via bounded Levenshtein
(`_fuzzy_hit`), scaled by token length: <4 exact-only, 4-7 distance 1
("mikku"→"miku"), 8+ distance 2 ("birkenstok"→"birkenstock"). Bounds exist
because loose matching IS the historical bug class — substrings and
half-word rewrites stay dead ("john"≠"johnny", "cost"≠"costco",
"jass"≠"jazz"), each pinned by a negative test in SEAM F. Live-verified:
"tell me more about Mikku" scored 1.00 against the sushi card and updated
it in place.

## Watchlist
- `_widget_showing` body parse runs twice per follow-up turn (directive +
  rewrite) — cheap, but could be memoized if canvases get huge.

---

# Handoff — 2026-07-20 (agent guardrails: image trust + follow-up targeting)

**Deployed:** `6aa0414` → synology `:8035`. Tests: 352 pytest + 16 node, green.
Both reported failures re-driven live end-to-end and confirmed fixed.

## The three live failures and their fixes

**1. "Compare birkenstock shoes to other shoes" → pasta captioned as a
Birkenstock sandal.** The image injector's guard was
`not config.get("images")` — a model-supplied images ARRAY bypassed
build_image_config, the vision gate, and URL verification entirely. Both URLs
loaded fine, so liveness checks can't catch a wrong-content pair: the model
paired its own captions with unrelated remembered URLs. Fix (main.py, image
injector branch): fires on TYPE alone now. Re-sources via `build_image_config`
(captions become the search query when nothing else names the subject); model
URLs survive only as a last resort, individually `_image_url_loads`-verified
AND `filter_images_by_relevance`-vision-gated (note its signature:
`(subject, negatives, items)` with items keyed `'image'`, not `'url'`).
Prompt-side: the image routing line now tells the agent it has NO image tool
and must NEVER write url/images entries; new COMPARE rule routes comparisons
to a data_card table first, images only as an explicit add-on.
Live re-test: agent emitted `config={'image_query': ...}` only — no URLs —
and the widget rendered one real, loading, on-subject image.

**2. "Tell me more about the deals at costco" (right after a Birkenstock card)
edited the SANDALS widget.** The follow-up directive/rewrite hard-targeted
`focus_id` — pure recency — whenever the (loose) refinement regex fired,
pre-empting the topical scorer. Fixed in THREE layers, each caught by
re-driving the scenario live after the previous fix:
- `_followup_target_id` (new, main.py ~2680): scores the message against
  every canvas widget (title + ledger gist, across types); a ≥0.5 winner
  beats recency; subject-free deictic asks keep recency. Resolved ONCE per
  turn so the directive and the message rewrite can never disagree.
- Fresh-subject guard, both in `_followup_target_id` AND in
  `find_reuse_target`'s deictic fallback: ≥2 subject tokens matching nothing
  on canvas = new topic = no forced reuse. Without the second one,
  "find me MORE info on birkenstock arizona" (trips `more\b`) got retargeted
  by the RESOLVER even after the directive stood down — the model honestly
  asked for a new id and `_resolve_agent_widget_id` overrode it.
- `_SUBJECT_STOP` additions: "happened/next/info/…" (deictic narrative) and
  "anything/related/…" (vague scope). "anything hardware related" diluted the
  overlap to 0.4 and missed the 0.5 threshold purely on filler.
Live re-test (scratch `target_check.py`): costco card → birkenstock gets its
OWN card → the costco follow-up updates the COSTCO card in place, sandals
untouched.

**3. Music widget console error** (`null.currentTime`): destroy() nulls
`this.audio` but queued events still fire; `src=''` in destroy itself fires a
spurious error event. Handlers now guard.

## Testing pattern worth keeping
The unit suite was green after fix #1 of bug 2 — only re-driving the real
3-turn scenario in a browser exposed layers 2 and 3. For targeting changes,
run the live scenario (`scripts/`-style playwright, 3 real agent turns),
then check `[AGENT TURN]` / `[WIDGET TARGET]` container logs for the
decision trail. New tests: SEAM D in tests/test_followup_targeting.py,
tests/test_agent_guardrails.py (source pins on the injector predicates,
prompt rules, directive wiring), fresh-subject case in test_followup_reuse.py.

## Watchlist
- The compare ask renders ONE image (vision gate is strict) — fine, but "a
  couple pictures" asks could source more candidates before gating.
- Cross-type association (image follow-up attaching to a data_card thread) is
  still unbuilt; new-widget is the designed behavior there.
- The `canvas_add_widget` tool DOC in lazy-agent-service
  (tool_schemas/html-notes/html-notes.json) still documents url/images
  configs; the server now ignores them, but tightening that doc would save
  the model wasted tokens. Needs a lazy-agent-service deploy.

---

# Handoff — 2026-07-20 (frontend: restrained sci-fi HUD theme)

**Deployed:** HTML-Notes `5ba6cf8` → synology `:8035`. Health 200; live `/`
serves `hud-theme.css?v=906b6d82f9` + the Share Tech Mono font; `/static/hud-theme.css`
= 200 (18.4 KB). Live empty-state + local widget renders screenshot-verified.
**Scope:** CSS-only overlay. No JS/HTML structure or Python logic changed except
one additive line in `app/main.py` (asset-fingerprint list — landed via `05d9454`).

## What shipped

A futuristic-HUD skin, added as a **final override layer** rather than a rewrite:
`app/static/hud-theme.css`, linked in `index.html` **after** `index.css` so it wins
over the tail "CANVAS REDESIGN" calm-neutral block — the same layering trick that
block used on the neon rules above it. The two prior themes in `index.css` are
untouched and still present underneath.

- **Backdrop:** faint masked grid + very soft STATIC scanlines (no animation).
- **Panels:** clip-path "cut" corners (top-right/bottom-left notch), thin cyan
  hairline + soft glow, drawn L-brackets on the other two corners via a single
  `::before` (leaves `.is-updating::after` sweep from index.css intact).
- **Headers/labels:** Share Tech Mono, uppercase, wide-tracked; a quiet status
  pip that slow-breathes (`hud-breathe`, 3.4s) — never a hard blink.
- **Readouts:** clock / `.metric-value` / `.font-mono` → mono + tabular + soft cyan.
- **Command strip, turn-status bars, activity log:** re-cut to match; status-bar
  shimmer slowed (2.6s) so long turns don't strobe.
- **Music player:** purple→cyan retint by targeting its Tailwind classes
  (`from-fuchsia-500`, `from-purple-400`, `bg-purple-300`, …) — its Alpine twin
  in `index.js` is NOT touched, so no template-drift risk with the music commit.

Per the brief: glow/colors kept soft, exactly one gentle looping pulse, and every
loop is killed under `prefers-reduced-motion`. Cuts/brackets shrink < 520px.

## Gotchas / notes for next session

- **The app did NOT look like the neon HUD on GitHub.** `index.css` stacks THREE
  themes now: neon (top) → calm-neutral "CANVAS REDESIGN" (tail, was live) → this
  HUD overlay (separate file, now on top). Edit the layer that actually wins.
- **`main.py` fingerprint edit was swept into `05d9454`** (a parallel session's
  `git add`), not my commit — it's in HEAD either way. `_CACHE_BUSTED_ASSETS` now
  lists `hud-theme.css`; bumping the file's mtime re-versions it automatically, so
  the hardcoded `?v=1.0` in index.html is only a fallback.
- **The Phase-1 "layout column bug" in the original plan does not exist** — the
  activity log is `position:fixed; display:none`, never a grid sibling. No fix made.
- Deeper plan items needing JS (widget size-tiers, per-widget action buttons, a
  60px single-row music strip) were left out to keep this a safe CSS-only pass and
  avoid colliding with the concurrent music-widget work. Resize/move handles for
  widgets already exist (`.widget-resize-handle` / `.widget-move-handle`).

---

# Handoff — 2026-07-20 (music widget → music-player service, real queue)

**Deployed:** HTML-Notes `05d9454` → synology `:8035` (a parallel session then
deployed `5ba6cf8`, the HUD theme, which includes this commit — served
widgets.js verified byte-identical after both).
**Tests:** 340 pytest + 16 node, all green. Live E2E 7/7.

## What shipped

"Play jungle music" fetched nothing from YouTube and "jungle house music"
played the local library. Root cause: the mini music player decided
genre-vs-artist with a hardcoded client-side `KNOWN_MUSIC_GENRES` set —
"jungle" wasn't on it, so both routed as artist search and fell through to
local-library matching. The music-player service (`:8002`) already handles
arbitrary genres (LLM + MusicBrainz artist discovery, strict YouTube
verification, 7-day cache); the widget just never asked it.

The widget is now a thin client over that service:

- **SSE mix consumption** (`/api/youtube/mix/<term>/stream`): playback starts
  on the first artist's tracks instead of racing a timeout against the ~18s
  cold pipeline. `type=artist` is served by the same endpoint (single
  tracks+done pair) — one code path for both kinds. **Zero music-player
  changes were needed.**
- **`kind` classified at routing time** (app/main.py): fast-path "X
  music/radio" phrasing defaults `kind=genre`; the LLM router catalog teaches
  `modifiers: {"kind": genre|artist}` for named acts; builder sanitizes.
- **Failover ladder** (widgets.js `failover()`): genre miss → artist mix
  (one-shot) → plain search → honest "Couldn't find X" error. The local
  library survives ONLY for bare no-query widgets; a named genre never dumps
  it (the "smooth jazz played Burzum" class of bug). The ~7MB `/api/tracks`
  fetch is gone from the mix path.
- **Real queue**: visible upcoming list (queue_music button; click-to-jump,
  hover-remove), dedupe by video id across SSE batches, background refill via
  `refresh=true` when <5 tracks remain (90s floor — the mix endpoint is
  rate-limited 10/min service-side).

## Where the code is

- `app/static/js/widgets.js` — `musicPlayerWidget` rewritten. Takes an options
  object `{genre, kind, autoplay, base}` with a legacy-positional shim.
- `app/widgets/factory.py` `render_mini_music_player` — object x-data, queue
  panel, streamStatus subtitle, height binds to `showQueue` (280↔420px).
- `app/main.py` — fast-path spawn + router catalog + builder pass `kind`.
- `app/static/index.js` ~1906 — self-heal template synced (it is a
  hand-maintained twin of the factory template; both carry cross-ref comments).

## Gotchas found along the way

- **EventSource auto-reconnects after a server close.** An unclosed stream
  re-runs the entire discovery pipeline in a loop against the 10/min rate
  limit. `closeStream()` is mandatory on `done`, `error`, AND `destroy()` —
  the .mjs suite pins all three. `destroy()` also covers the media-singleton
  swap (server replaces the div → Alpine destroy fires).
- **"jungle house" legitimately has no discoverable artists** — the genre
  pipeline SSEs an `error` event. The failover then gets 30+ real
  jungle-house tracks from the artist mix. Verified end-to-end in the browser:
  genre stream → error → artist stream → playing, zero library requests.
- The old test suite pinned the deleted heuristics and was green while the
  feature was broken — the rewrite pins the service contract instead
  (`tests/test_music_genre_routing.mjs`).
- `_content_sig` hashes the whole config, so `kind` participates for free: a
  genre→artist correction re-renders, a same-config re-ask leaves the playing
  widget alone (pinned in `tests/test_widgets.py`).
- Deploy note: the working tree had another session's in-progress HUD work, so
  this was deployed from a pinned sibling worktree
  (`git worktree add ../html-notes-deploy <sha>` → deploy.sh → remove), per
  the trading-service recipe. `../deploy-kit` and `../lazycat-sdk` resolve
  because the worktree sits in the sun root.

## Verification

- `node --test tests/` — 16 pass (13 in the rewritten music suite: SSE
  contract, close-on-done/error/destroy, /api/tracks absence, failover order,
  enqueue dedupe, removeAt index math, refill gating, routing kind).
- `pytest tests/` — 340 pass (new: x-data carries kind/base/queue UI; kind in
  the content signature).
- `scripts/music_widget_check.py` (live, needs NAS stack): "play jungle music"
  → one genre SSE request, zero `/api/tracks`, audio from
  `/api/youtube/stream/`, queue rows render, row-click jumps, no orphaned
  EventSource. 7/7.
- Scratch live check for "jungle house music": genre→artist failover chain
  observed in network log, 30 tracks queued, playing, no library.

## Not done / watchlist

- The queue is per-widget, in-memory — no persistence across widget swaps
  (matches the music-player frontend's own localStorage-only design; theirs
  isn't shared either).
- `POST /api/radio/preference` (thumbs up/down feeding the service's artist
  weighting) is unused by the widget — natural next step if mix quality needs
  user steering.
- If music-player's `AUTH_ENABLED` ever flips on, both audio and SSE need the
  stream-ticket flow; browser-direct `:8002` calls would need revisiting.
