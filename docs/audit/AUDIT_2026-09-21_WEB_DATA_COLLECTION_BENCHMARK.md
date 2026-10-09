# AUDIT 2026-09-21 — Web Data Collection & Summarization (with Eval Benchmark Plan)

**Mode**: Audit (read-only). No code, config, DB, or deploy changes were made.
**Scope**: `html-notes` retrieval → acquisition → summarization pipeline, plus existing bench infrastructure.
**Method**: Three parallel read-only audits (search paths; extraction/summarization; bench inventory) + direct reading of `ARCHITECTURE.md` (v2.1.0, 2026-09-19) and `RESEARCH_RELIABILITY_PLAN.md`.

---

## 1. Architecture as found (CONFIRMED, evidence-cited)

```
query → web_search_ex ── ddg-lite (search.py:54-100)
                      └─ ddg-collector via scraper-service (search.py:102-127)
       → news_search   1) lazy-tool-service shared news_search (search.py:404-471)
                       2) Google News RSS (search.py:239-303)
                       3) scraper-service DDG '{topic} news' (473-500, topic only)
                       4) GDELT Doc API, 6s cap (173-236, topic only)
                       5) generic web_search reshaped (548-552, topic only)
       → news_pipeline.build_news_response (news_pipeline.py:812-873):
         normalize → collect → validate/dedupe → verify (scrape, 6s) →
         diversify → fast_llm_json rank → render
       → summarization: deterministic news card (URL + tier meta, news_pipeline.py:782-794)
         and agentic lane (nemotron vLLM, profile max_tool_calls=15,
         html_notes.profile.json:86-90) with citation rules (message.py:1590-1594)
```

Outage vs zero-results: `web_search_ex` (search.py:129-157) returns `all_engines_failed=True` only when every engine *raises*; any engine answering (even 0 hits) counts as "backend alive". Gateway refuses retry advice on outage (routes/internal.py:371-375). Call caps: 4 identical calls / 12 research calls per turn (main.py:423-424, message.py:3231-3250).

## 2. Findings

| # | Finding | Class | Evidence |
|---|---|---|---|
| F1 | **No independent web-search provider.** `web_search` has exactly two engines, both DDG (ddg-lite direct, ddg-collector via scraper). RESEARCH_RELIABILITY_PLAN P0 (Brave, key in vault, verified working 2026-09) is **still unimplemented**. Single-geo correlated failure remains the top availability risk. | CONFIRMED | search.py:53-58; RESEARCH_RELIABILITY_PLAN.md:26-49 |
| F2 | **Soft failures masquerade as zero results.** An engine returning `[]` on a soft failure (scraper `/collect` 200-empty, shared news_search non-200 → `[]`) counts as "backend alive" → classified "no results", no outage signal. Only raised exceptions count. | CONFIRMED | search.py:139-157 |
| F3 | **Health check issues live searches.** `/health/app` runs a real `web_search_ex('test')`; docker-compose hits it every 30s ⇒ ~2,880 live searches/day against the keyless DDG endpoints — its own comments flag rate-limit risk. | CONFIRMED | routes/health.py:25-49; docker-compose.yml |
| F4 | **Undated news items bypass recency entirely.** `published_at=None` items skip the stale check (`if cand.published_at:`); only RFC-2822/ISO parsed; no relative-date ("2 hours ago") or body-date extraction. Rendered card never shows article dates. | CONFIRMED | news_pipeline.py:169-188, 472-476, 789 |
| F5 | **Dedupe is URL + title-prefix[:80] only.** No syndication/wire clustering across outlets; different headline ⇒ duplicate story survives. Conversely, long headlines differing only after char 80 collide as false dups. Cross-outlet "consensus" is a pass-through integer from one provider. | CONFIRMED | news_pipeline.py:141-166, 426-455, 452, 588 |
| F6 | **Verification can be snippet-only.** Verify scrape timeout is 6s vs read_web_page's 25s; slow pages fail verify and fall through to "tier-1 primary outlet pass with snippet", which still earns a Verified badge. | CONFIRMED | news_pipeline.py:505, 516-557, 588-597, 789 |
| F7 | **Extraction is head-only.** `read_web_page` truncates at 6,000 chars, no chunking; no publish-date/author extraction from body. | CONFIRMED | search.py:599-627 |
| F8 | **Existing bench is YouTube-only, not web-pipeline.** `bench/` = video-selection bake-off (LLM-in-loop A/B/C, LLM judge, 20 queries) with solid method (blind judge, token/latency accounting); `bench/news/` = 6 news-relevance strategies, 29 queries, blind set-level judge with calibration controls — but **no retrieval-layer (Recall@K/nDCG) eval, no acquisition-success eval, no answer-quality eval with citations, no outage simulation, no provider latency profiles**. `bench/README.md` says 15 queries vs 20 actual (stale doc). | CONFIRMED | bench/run_bench.py, bench/news/*, AUDIT_2026-09-05 |
| F9 | Recorded prior result: news relevance ceiling was **upstream provider quality** (A 2.67 / B 4.00 / C 4.80) — benchmarking the ranking layer alone cannot fix provider-level problems. | CONFIRMED | AUDIT_2026-09-05_RELEVANCE_AND_LATENCY.md:171-182,246 |
| F10 | Budget ceilings live in three uncoordinated layers (profile `max_tool_calls=15`, research `budget.py` max_llm_calls=2, fast-path news builders bounded only by gather timeouts). A benchmark must record which layer served a run. | CONFIRMED | profile:86-90; budget.py:11-135; config_builders.py:1354-1380 |
| F11 | Historical 10-18 retry loop: root cause fixed in code (outage is_error + no-retry-advice + caps), but **no regression test replays the DDG-total-outage scenario end-to-end against the live model** — regression set exists only as prose. | CONFIRMED fix / UNVERIFIED regression | internal.py:371-375; RESEARCH_RELIABILITY_PLAN.md:33 |

## 3. What the benchmark must add (gap-closure plan)

Existing assets to build on: `bench/news/` blind judge + calibration controls (reuse as the answer-quality judge harness), `provider_audit.py` per-provider relevance, `queries.jsonl` format.

**Phase B1 — Retrieval layer (new).** Fixed 50–100 query corpus across news-fresh / evergreen / technical / adversarial / no-answer categories. Per provider (ddg-lite, ddg-collector, Google News RSS, GDELT, shared news_search, plus free candidates from §4) capture raw responses, then compute Recall@5/10, Precision@5, nDCG@5 (0–3 graded), duplicate-cluster rate, provider p50/p95, and error class (timeout vs zero vs malformed). Store with `run_id`/`query_id`/`git_sha`.

**Phase B2 — Acquisition layer (new).** For the top-10 URLs of B1 winners: extraction success rate, text length distribution, junk-gate hit rate, publish-date extraction yield. Directly measures F6/F7 (snippet-only "verified", 6k truncation).

**Phase B3 — Answer layer (extend bench/news judge).** Fixed evidence packets → score task completion, citation entailment, unsupported-claim rate, citation-fabrication rate, freshness labeling. Reuse blind 4-axis judge; add the hard-fail counters.

**Phase B4 — Reliability (new, highest decision value).** Simulate: DDG-family total outage (F1), soft-empty masquerade (F2), rate-limit from health probes (F3). Acceptance: outage classified correctly, ≤3 identical calls, honest "search unavailable", independent provider serves the turn. This is the regression set F11 lacks.

**Decision gates** (mirrors the benchmark proposal): no fabrication on gate set; independent non-DDG fallback for production; health check de-rated to cached/cheap probe; publish scorecard before any routing change.

## 4. Highest-value improvements (ranked, pending benchmark data)

1. ~~Wire Brave Search API as primary~~ **REJECTED by user decision 2026-09-21: Brave killed its free query tier in Feb 2026; prepaid $5/1,000 requests with $5 free credits/month (~1,000 searches) and automatic overage billing — any $ is not acceptable.** Free-fee independent-provider candidates instead: (a) **self-hosted SearXNG** container in docker-compose — one keyless meta-search endpoint aggregating DDG, Bing, Mojeek, Startpage, Qwant etc., with JSON output; correlated-failure-resistant because engines are heterogeneous; (b) **Mojeek API** free tier if still offered (verify limits at benchmark time); (c) deepen the already-free news stacks (Google News RSS, GDELT) and publisher RSS feeds for research intent. Resolves F1 without cost; SearXNG is the recommended benchmark candidate.
2. Classify soft-empty as `unknown` (distinct from `zero_results` and `outage`) — F2.
3. Date normalization: relative-date parsing + body-date extraction; always surface article dates in rendered cards — F4.
4. Syndication clustering (title-skeleton / near-dup across outlets) at pipeline level, not provider-only — F5.
5. Health probe: cache result 5–10 min or use a synthetic local probe — F3.
6. Longer/segmented extraction for top-3 reads with date+author capture — F7/F6.

**Cost addendum (2026-09-21)**: Brave Search API pricing verified from its official page — Search plan $5.00/1,000 requests, "free $5 in credits every month", 50 req/s; the 2,000–5,000-query free tier was removed ~Feb 2026. Metered overage bills automatically once credits exhaust. Decision: do not adopt any paid-metered provider for routine search.

## 5. Verification status

All findings are static-code-evidence based (file:line). Nothing was executed against the live services; latency and rate figures from docs are LIKELY and are precisely what Phases B1–B4 measure. **STOP here — no IMPLEMENT/FIX performed.** Say `IMPLEMENT` (build bench harness) or `FIX` (apply improvement #1/#2) to proceed.
