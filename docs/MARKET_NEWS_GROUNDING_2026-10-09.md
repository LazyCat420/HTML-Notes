# Market News Grounding — 2026-10-09

**Commit `8c02c74`, deployed, verified live.** Fix for "stock market news
serves one Indian market story".

## Audit findings (why the card was bad)

Reproduced on the deployed container: "stock market news" rendered a card
with exactly ONE story — `thehindubusinessline.com` "Sensex Nifty50 today
stock market live updates". Four stacked causes, all in the general-finance
path of `build_news_card`:

1. **Keyword fetch, global scope.** The topic `"stock market"` is sent as a
   keyword query to news APIs; `gl=US` on Google News RSS affects the
   edition, not topic relevance. Result: analyst promos that merely contain
   the word "market" (Synopsys/Celestica Seeking Alpha pieces), plus
   finnews keyword hits — Sensex, Moscow Exchange, Indian pharma PR.
2. **No relevance gate for general asks.** `filter_items_by_relevance` runs
   only when a `subject` exists; general asks had none, so nothing ever
   rejected the junk.
3. **The editor then selected.** The finance summariser is told "OMIT a
   source if it is not about the topic" — with a mostly-off-topic result
   set it kept exactly one story: the worst one, an Indian live blog that
   IS about "the stock market".
4. **Zero user grounding.** trading-service data (watchlist `:8888/api/v1/
   watchlist`, positions `/api/v1/portfolio`) was wired for "my portfolio"/
   "my watchlist" asks but never touched by market news — even though the
   finnews *ticker* providers (finnhub, marketaux, …), the richest tier,
   were reachable all along.

## The fix (`app/config_builders.py`, `app/services/finance.py`)

- **Gate**: general finance asks now set `subject="the US stock market"`
  with negatives (non-US markets, price-target promos, listicles) so the
  existing fail-open LLM gate runs before the editor.
- **Market-directed fetch**: the general-ask finnews fan-out sends index
  tickers `SPY`/`QQQ` (ticker providers → US market movers) plus the query
  `"US stock market today"` (keyword providers). `_finnews_articles` now
  sends tickers AND query together (query used to be silently dropped when
  tickers were present) and its client timeout rose 10s→20s — the collector
  fans out to ~10 keyed APIs and the slow tier routinely lands past 10s,
  which aborted whole merged results.
- **Personalization**: `trading_service_tickers()` (best-effort, 3s, fails
  open) pulls watchlist + open positions; their finnews results are merged
  FIRST with badge `Your watchlist`, and the editor prompt says watchlist
  stories lead the write-up. Badge survives `_normalise_news_item`.
- **Editor no longer re-selects on general asks** — the gate owns relevance;
  the editor writes up every gated story. The old omit rule collapsed 13
  fetched stories to 1.

## Verified

- Live providers (local builder): gate logged `kept 12/14 (dropped 2
  off-subject)`; card = 6 genuine US-market stories with the user's CEG
  position leading under `Your watchlist`.
- Deployed SSE probe: 6 stories, `Your watchlist` badges present, zero
  thehindubusinessline.com.
- Tests: `tests/test_market_news_grounding.py` (gate subject + market-
  directed finnews args + personalization + fail-open); 45 news tests green.

## Watchlist

- Seeking Alpha single-stock analyst pieces can still survive the gate
  (they ARE US-market stories); if the owner finds them listicle-y, tighten
  the negatives wording in `build_news_card` step 2.
- `finnews` keyword providers still interpolate the query verbatim — a
  subject ask ("news about nvidia earnings") is fine, but garbage-in for
  nonsense queries is unchanged.
