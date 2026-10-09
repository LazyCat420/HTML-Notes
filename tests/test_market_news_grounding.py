"""Market-news grounding: 'stock market news' must serve US-market stories.

Pins the fix for the "Indian equity benchmark Sensex" failure:
  - a general finance ask now passes a subject ("the US stock market") into
    the relevance gate, so keyword-matched non-US-market junk gets dropped
    instead of flowing straight into the editor (which then kept the single
    worst story — an Indian live blog literally titled 'stock market');
  - the finnews fan-out for a general finance ask routes at MARKET coverage
    (index tickers SPY/QQQ + an explicit US-market query), not the bare
    keyword "stock market";
  - the user's own trading-service tickers are fetched and their news leads
    the card with the 'Your watchlist' badge;
  - the editor writes up every gated story for a general ask (it no longer
    re-selects, which collapsed the card to 1 story).
"""

import asyncio

import pytest

import app.config_builders as cb
import app.services.finance as finance


@pytest.fixture
def fake_providers(monkeypatch):
    """Patch every network touchpoint build_news_card's finance path uses."""
    calls = {"news_search": [], "finnews": [], "trading_tickers": 0}

    async def fake_news_search(topic, limit=6, category="", country=""):
        calls["news_search"].append((topic, limit, category, country))
        return [
            {"title": "Sensex Nifty50 today stock market live updates",
             "url": "https://www.thehindubusinessline.com/markets/x",
             "meta": "The Hindu BusinessLine", "snippet": "Indian equity benchmark Sensex..."},
            {"title": "US stocks slip as 10-year yield tops 5%",
             "url": "https://www.cnbc.com/2026/10/09/yields.html",
             "meta": "CNBC", "snippet": "Treasury yields rose again..."},
        ]

    async def fake_finnews(query="", tickers=None, limit=12):
        calls["finnews"].append((query, tickers, limit))
        if tickers == ["SPY", "QQQ"]:
            return [{"title": "S&P 500 wobbles as yields climb",
                     "url": "https://example.com/spy", "publisher": "Finnhub",
                     "related_tickers": ["SPY"], "summary": "Index futures fell."}]
        if tickers:  # personal fetch
            return [{"title": "Constellation Energy inks 890 MW Google nuclear deal",
                     "url": "https://example.com/ceg", "publisher": "Yahoo",
                     "related_tickers": ["CEG"], "summary": "CEG surged on the deal."}]
        return []

    async def fake_trading_tickers(limit=5):
        calls["trading_tickers"] += 1
        return ["CEG", "AMZN"]

    # The relevance gate: mirror the real LLM gate's semantics — drop the
    # keyword-collision junk (the Sensex live blog), keep genuine US-market
    # stories including the user's own holdings.
    async def fake_gate(subject, negatives, items, keep=0, min_keep=1, hyde=""):
        assert subject == "the US stock market", subject
        kept = [it for it in items if "sensex" not in it["title"].lower()]
        return kept or items  # never empty → no escalation path

    monkeypatch.setattr(cb.main, "news_search", fake_news_search)
    monkeypatch.setattr(cb.main, "_finnews_articles", fake_finnews)
    monkeypatch.setattr(cb.main, "filter_items_by_relevance", fake_gate)
    monkeypatch.setattr(cb, "_finnews_articles", fake_finnews)
    monkeypatch.setattr(finance, "trading_service_tickers", fake_trading_tickers)
    # The editor pass would need an LLM; an empty return serves provider
    # snippets (the degraded path), which is enough to assert on items.
    async def fake_llm(*a, **k):
        return None
    monkeypatch.setattr(cb.main, "fast_llm_json", fake_llm)
    return calls


@pytest.mark.asyncio
async def test_stock_market_news_gates_and_personalizes(fake_providers):
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)

    # The gate ran with the US-market subject and dropped the Sensex story.
    titles = [it["title"] for it in cfg["items"]]
    assert not any("sensex" in t.lower() for t in titles)
    assert any("yield" in t.lower() or "S&P" in t for t in titles)
    assert any("Constellation" in t for t in titles)

    # The finnews fan-out was market-directed, not the bare keyword.
    assert ("US stock market today", ["SPY", "QQQ"], 8) in fake_providers["finnews"]

    # Personal tickers were fetched and lead the card with the badge.
    assert fake_providers["trading_tickers"] == 1
    assert cfg["items"][0]["badge"] == "Your watchlist"
    assert "CEG" in cfg["items"][0]["title"] or "Constellation" in cfg["items"][0]["title"]


@pytest.mark.asyncio
async def test_personalization_fails_open_when_trading_service_down(fake_providers, monkeypatch):
    async def dead_tickers(limit=5):
        return []
    monkeypatch.setattr(finance, "trading_service_tickers", dead_tickers)
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)
    assert cfg["items"], "a down trading-service must not empty the card"


def test_rank_finance_items_recency_and_watchlist():
    """Watchlist first, then dated newest-first, undated last."""
    from app.config_builders import _rank_finance_items
    items = [
        {"title": "old", "date": "2026-10-07 10:00 UTC"},
        {"title": "watch", "date": "2026-10-05 09:00 UTC", "badge": "Your watchlist"},
        {"title": "undated"},
        {"title": "fresh", "date": "2026-10-09 09:30 UTC"},
    ]
    out = _rank_finance_items(items)
    assert [it["title"] for it in out] == ["watch", "fresh", "old", "undated"]


@pytest.mark.asyncio
async def test_attach_article_bodies_fills_body_fail_open(monkeypatch):
    """Bodies attach in job order; on HTTP failure every item keeps its snippet."""
    from app.config_builders import _attach_article_bodies
    items = [{"title": "a", "url": "https://x.example/a"},
             {"title": "b", "url": "https://finnhub.io/api/news?id=z"}]

    class FakeResp:
        status_code = 200
        def json(self):
            return {"results": [
                {"url": "https://x.example/a", "success": True, "content": "FULL BODY TEXT " * 300},
                {"url": "https://finnhub.io/api/news?id=z", "success": False, "error": "timeout"},
            ]}

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            assert url.endswith("/scrape/batch")
            assert len(json["jobs"]) == 2
            return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    out = await _attach_article_bodies([dict(it) for it in items])
    assert out[0]["body"].startswith("FULL BODY TEXT")
    assert "body" not in out[1]

    class DeadClient(FakeClient):
        async def post(self, url, json=None):
            class R: status_code = 500
            return R()
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: DeadClient())
    out2 = await _attach_article_bodies([dict(it) for it in items])
    assert "body" not in out2[0]
