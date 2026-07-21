"""Trending/gainers stock discovery — the "compare the top trending stocks"
ask that used to resolve zero tickers and degrade to a sourceless answer card.
The candidate list must come from the live discovery feeds, never from symbol-
searching the phrase (empty) or an LLM's memory of famous tickers."""
import os
os.environ.setdefault("DATABASE_URL", "data/test_notes.db")
import asyncio

import pytest

import app.main as m


# ── Intent regex ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "compare the top trending stocks this last month. compare the top 5 on a chart.",
    "top trending stocks",
    "biggest gainers today",
    "show me today's gainers",
    "worst performing stocks this week",
    "hottest stocks right now",
    "most active tickers",
])
def test_trending_re_matches_discovery_asks(text):
    assert m.TRENDING_STOCK_RE.search(text)


@pytest.mark.parametrize("text", [
    "NVDA vs SPY",                 # explicit compare, no discovery word
    "apple stock price",           # single-ticker ask
    "find me movers in Seattle",   # furniture movers — "movers" is not standalone
    "best budget laptop",          # shopping, no stock noun
    "what's the weather today",
])
def test_trending_re_ignores_non_discovery_asks(text):
    assert not m.TRENDING_STOCK_RE.search(text)


# ── Kind / range / count parsing ─────────────────────────────────────────────
def test_trend_kind_mapping():
    assert m._trend_kind("top trending stocks") == "trending"
    assert m._trend_kind("biggest gainers today") == "day_gainers"
    assert m._trend_kind("worst performing stocks") == "day_losers"
    assert m._trend_kind("most active stocks") == "most_actives"


def test_range_from_message():
    assert m._range_from_message("top stocks this last month") == "1mo"
    assert m._range_from_message("biggest gainers today") == "1d"
    assert m._range_from_message("best stocks this week") == "5d"
    assert m._range_from_message("over the past 3 months") == "3mo"
    assert m._range_from_message("top stocks this year") == "1y"
    assert m._range_from_message("trending stocks") == "1mo"  # default


# ── Builder (feeds mocked) ───────────────────────────────────────────────────
def test_build_trending_compare_uses_feed_symbols(monkeypatch):
    feed = ["AMC", "IREN", "ACHR", "NBIS", "BABA", "CIFR", "HUT", "CLSK"]
    seen = {}

    async def fake_feed(kind="trending", limit=10):
        seen["kind"], seen["limit"] = kind, limit
        return feed[:limit]

    async def fake_compare(symbols, range_="6mo"):
        seen.setdefault("calls", []).append((list(symbols), range_))
        return {"title": "x", "compare_symbols": list(symbols), "range": range_,
                "chart": {}}

    monkeypatch.setattr(m, "_trending_symbols", fake_feed)
    monkeypatch.setattr(m, "build_stock_compare_config", fake_compare)

    cfg = asyncio.run(m.build_trending_compare_config(
        "compare the top trending stocks this last month. compare the top 5 on a chart."))
    assert cfg is not None
    # "top 5" → exactly 5 series tried first, from the FEED's list, 1mo range.
    assert seen["calls"][0] == (feed[:5], "1mo")
    assert cfg["compare_symbols"] == feed[:5]
    assert cfg["title"].startswith("Top 5 trending stocks")


def test_build_trending_compare_none_when_feeds_down(monkeypatch):
    async def empty_feed(kind="trending", limit=10):
        return []
    monkeypatch.setattr(m, "_trending_symbols", empty_feed)
    assert asyncio.run(m.build_trending_compare_config("top trending stocks")) is None


# ── Accuracy: naming an index actually scopes the pull ───────────────────────
@pytest.mark.parametrize("text", [
    "top 5 stocks that were in the s&p",
    "s&p 500 gainers today",
    "biggest movers in the S and P",
    "sp500 losers this week",
    "spx most active",
])
def test_universe_re_matches_sp500(text):
    assert m._universe_from_message(text) == "sp500"


@pytest.mark.parametrize("text", [
    "top trending stocks",
    "biggest gainers today",
    "most active tech stocks",  # "active" ≠ index; no s&p qualifier
])
def test_universe_re_ignores_unscoped_asks(text):
    assert m._universe_from_message(text) == ""


def _compare_stub(labels_by_sym):
    async def fake_compare(symbols, range_="6mo"):
        syms = list(symbols)
        return {
            "title": "x", "compare_symbols": syms, "range": range_,
            "chart": {"data": {"datasets": [
                {"label": labels_by_sym.get(s, f"{s}  +1.0%")} for s in syms]}},
        }
    return fake_compare


def test_build_trending_scopes_to_index_and_tags_provenance(monkeypatch):
    """'top 5 stocks in the s&p' keeps ONLY S&P members, in screener rank order,
    and tags provenance so a follow-up knows where the list came from."""
    members = frozenset({"DHR", "MMM", "MSCI", "AAPL", "NVDA"})
    calls = {}

    async def fake_members(name):
        calls["universe"] = name
        return members

    async def fake_feed(kind="trending", limit=10):
        calls["feed"] = (kind, limit)
        # A realistic day_gainers pool: mostly non-members (the old bug's junk).
        return ["CPHI", "VIVK", "DHR", "NBIS", "MMM", "CBRS", "MSCI", "AAPL"]

    monkeypatch.setattr(m, "_index_constituents", fake_members)
    monkeypatch.setattr(m, "_trending_symbols", fake_feed)
    monkeypatch.setattr(m, "build_stock_compare_config",
                        _compare_stub({"DHR": "DHR  +4.2%", "MMM": "MMM  +3.1%"}))

    cfg = asyncio.run(m.build_trending_compare_config("top 5 stocks that were in the s&p"))
    assert cfg is not None
    # S&P qualifier resolved, and a bare "top stocks" scoped ask pulls the ranked
    # gainers screener (not the site-wide trending feed) at pool width.
    assert calls["universe"] == "sp500"
    assert calls["feed"] == ("day_gainers", 250)
    # The micro-caps that aren't in the index are gone; only members survive, in
    # the screener's rank order.
    assert cfg["compare_symbols"] == ["DHR", "MMM", "MSCI", "AAPL"]
    assert "CPHI" not in cfg["compare_symbols"]
    assert cfg["title"].startswith("Top 4 S&P 500 gainers stocks")
    prov = cfg["provenance"]
    assert prov.startswith("tickers picked via S&P 500 gainers today")
    assert "DHR  +4.2%" in prov  # per-ticker move rides along for the follow-up


def test_build_trending_unscoped_flags_unfiltered_provenance(monkeypatch):
    async def fake_feed(kind="trending", limit=10):
        return ["CPHI", "VIVK", "NBIS", "DHR", "CBRS"]
    monkeypatch.setattr(m, "_trending_symbols", fake_feed)
    monkeypatch.setattr(m, "build_stock_compare_config", _compare_stub({}))

    cfg = asyncio.run(m.build_trending_compare_config("top trending stocks"))
    assert cfg is not None
    assert "NOT filtered to any index" in cfg["provenance"]
    assert cfg["title"].startswith("Top 5 trending stocks")


def test_build_trending_degrades_when_index_filter_empties_pool(monkeypatch):
    """If none of the ranked names are members, don't dead-end — fall back to the
    unscoped feed and DROP the index scope from the title/provenance."""
    async def fake_members(name):
        return frozenset({"DHR"})  # a member that won't appear in the pool

    seen = []

    async def fake_feed(kind="trending", limit=10):
        seen.append((kind, limit))
        return ["CPHI", "VIVK", "NBIS", "AMC"]  # zero members

    monkeypatch.setattr(m, "_index_constituents", fake_members)
    monkeypatch.setattr(m, "_trending_symbols", fake_feed)
    monkeypatch.setattr(m, "build_stock_compare_config", _compare_stub({}))

    cfg = asyncio.run(m.build_trending_compare_config("top s&p gainers today"))
    assert cfg is not None
    # First the ranked pool (filtered), then the fallback feed at count width.
    assert seen[0] == ("day_gainers", 250)
    assert "S&P 500" not in cfg["title"]
    assert "filtered to index members" not in cfg["provenance"]


def test_widget_detail_appends_provenance(monkeypatch):
    """The ledger gist carries provenance LAST so anaphora names aren't clipped."""
    detail = m._widget_detail({"answer": "Apple beat on earnings.",
                               "provenance": "tickers picked via S&P 500 gainers today"})
    assert "Apple beat on earnings" in detail
    assert "S&P 500 gainers" in detail
    assert detail.index("Apple") < detail.index("S&P 500")


# ── Router integration: a 'stock' classification still lands in discovery ────
def test_router_stock_spec_reroutes_discovery_ask(monkeypatch):
    async def fake_trend_cfg(message):
        return {"title": "Top 5 trending stocks — 1mo % change",
                "compare_symbols": ["AMC", "IREN", "ACHR", "NBIS", "BABA"],
                "range": "1mo", "chart": {}}
    monkeypatch.setattr(m, "build_trending_compare_config", fake_trend_cfg)

    out = asyncio.run(m.build_router_widget(
        {"type": "stock", "query": "top trending stocks"},
        "sess-x", "compare the top trending stocks this last month."))
    assert out is not None
    wtype, prefix, cfg = out
    assert (wtype, prefix) == ("chart", "stock-trending")
    assert cfg["compare_symbols"][0] == "AMC"


def test_router_stock_trending_type_builds_chart(monkeypatch):
    async def fake_trend_cfg(message):
        return {"title": "t", "compare_symbols": ["A", "B"], "range": "1d", "chart": {}}
    monkeypatch.setattr(m, "build_trending_compare_config", fake_trend_cfg)
    out = asyncio.run(m.build_router_widget(
        {"type": "stock_trending", "query": "biggest gainers today"}, "sess-x",
        "biggest gainers today"))
    assert out and out[0] == "chart" and out[1] == "stock-trending"


def test_router_explicit_tickers_beat_discovery(monkeypatch):
    """'top performers: NVDA vs SPY' compares the user's tickers, not the feed."""
    async def fail_trend(message):
        raise AssertionError("discovery must not run when tickers are explicit")
    monkeypatch.setattr(m, "build_trending_compare_config", fail_trend)

    async def fake_compare(symbols, range_="6mo"):
        return {"title": "x", "compare_symbols": list(symbols), "range": range_,
                "chart": {}}
    monkeypatch.setattr(m, "build_stock_compare_config", fake_compare)

    out = asyncio.run(m.build_router_widget(
        {"type": "stock", "query": "top performers: NVDA vs SPY"}, "sess-x",
        "top performers: NVDA vs SPY"))
    assert out is not None
    assert set(out[2]["compare_symbols"]) == {"NVDA", "SPY"}
