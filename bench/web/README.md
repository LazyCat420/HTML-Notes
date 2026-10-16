# bench/web — retrieval provider benchmark

Implements Phases B1 (retrieval) and B4 (outage classification) of
`docs/audit/AUDIT_2026-09-21_WEB_DATA_COLLECTION_BENCHMARK.md`.

## Providers
| id | kind | status (2026-09-21, this network) |
|---|---|---|
| `searxng` | self-hosted SearXNG JSON API (`http://127.0.0.1:8888`), engines: brave-scrape/google/bing serving; ddg CAPTCHA'd upstream | **recommended** — 100% success, ndcg@5 0.933, p50 ~0.8s, $0 |
| `ddg_lite` | direct DDG Lite scrape (app's current primary) | **unusable keyless**: 15× CAPTCHA (HTTP 202 challenge) + 15× empty per run |
| `google_news_rss` | news-only RSS | works (93% success), all dates supplied; `unique_source_rate` not meaningful — links are news.google.com redirects |
| `gdelt` | GDELT Doc API | unreliable from this network (24/30 transport errors; known 429-throttle behavior, matches repo comment) |
| mojeek | — | **rejected**: keyless HTML times out; API is contact-gated, no self-serve free tier |
| brave api | — | **rejected by user decision**: paid metering ($5/1k, overage auto-bills) |

## Usage
```bash
# SearXNG (limiter off, JSON enabled, local only):
docker run -d --name searxng-bench --rm -p 8888:8080 \
  -v $PWD/bench/web/searxng/settings.yml:/etc/searxng/settings.yml:ro searxng/searxng:latest

python3 -m bench.web.run                       # full corpus, all providers
python3 -m bench.web.run --providers searxng   # one provider
python3 -m bench.web.simulate_outage           # outage/soft-empty classification gates
```

## Artifacts
`results/<run_id>/raw/<provider>/<query_id>.json` (full response incl. error class),
`rows.jsonl` (per-query metrics), `scorecard.json` (per-provider aggregate + git_sha).

## Known limits
- Grading is heuristic (must-cover term overlap), not human judgment — treat
  ndcg/precision as directional until the human rubric pass (audit B3).
- SearXNG engines can be temporarily suspended upstream (one earlier run had a
  6-query soft-empty burst; next run was clean). Error classes make this visible.
- GDELT `outage` at 0ms = immediate transport refusal; throttled slow responses
  (>6s) appear as soft_empty/timeouts. Both match the known GDELT throttle profile.
