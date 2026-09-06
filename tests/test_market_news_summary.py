import pytest
from app import main as m
from app.config_builders import build_stock_news_config, build_news_config
from app.canvas_manager import _spoken_summary


async def _none(*a, **k):
    return []


@pytest.mark.asyncio
async def test_build_stock_news_config_has_answer_on_success(patch_server):
    """The card's prose is the overview; the header subtitle is provenance.

    These used to be asserted EQUAL — the test pinned the duplicate-render
    defect (the same sentence painted in the header bar and as the body). The
    fixture's overview also has to be concrete: an overview that names nothing
    from its own sources is rejected as generic by design."""
    async def mock_stock_news(q, limit=8):
        return {"news": [
            {"title": "S&P 500 hits record high", "publisher": "Reuters",
             "link": "https://reuters.com/article1", "providerPublishTime": 1700000000},
            {"title": "Tech stocks rally on earnings", "publisher": "Bloomberg",
             "link": "https://bloomberg.com/article2", "providerPublishTime": 1700000000},
        ]}
    async def mock_llm_json(prompt, **kw):
        return {
            "overview": "The S&P 500 hit a record high as tech companies posted strong quarterly earnings.",
            "items": [
                {"index": 0, "title": "S&P 500 Record High", "summary": "The S&P 500 reached a new all-time high driven by tech momentum."},
                {"index": 1, "title": "Tech Earnings Rally", "summary": "Tech giants reported earnings beating analyst expectations across the board."},
            ]}
    # The primary tiers come back empty so the Yahoo fallback is what serves.
    patch_server("news_search", _none)
    patch_server("_finnews_articles", _none)
    patch_server("stock_news", mock_stock_news)
    patch_server("fast_llm_json", mock_llm_json)

    cfg = await build_stock_news_config("stock market news")
    assert cfg["answer"] == "The S&P 500 hit a record high as tech companies posted strong quarterly earnings."
    assert cfg["subtitle"] != cfg["answer"], "subtitle is provenance, not the overview again"
    assert cfg["subtitle"].startswith("2 stories") and "Reuters" in cfg["subtitle"]
    assert len(cfg["items"]) == 2
    assert cfg["items"][0]["title"] == "S&P 500 Record High"
    assert "https://reuters.com/article1" in cfg["items"][0]["url"]
    assert cfg["items"][0]["meta"].startswith("Reuters"), "publisher must survive normalisation"
    # _spoken_summary normalises for TTS ("S&P" -> "S and P"), so compare the
    # substance rather than the bytes: it must speak the overview, not a count.
    spoken = _spoken_summary("data_card", cfg, "stock market news")
    assert "record high" in spoken and not spoken.startswith("Found")


@pytest.mark.asyncio
async def test_build_stock_news_config_has_answer_on_degraded(patch_server):
    async def mock_stock_news(q, limit=8):
        return {"news": [
            {"title": "Markets slip on inflation report", "publisher": "WSJ",
             "link": "https://wsj.com/article1", "providerPublishTime": 1700000000},
            {"title": "Bond yields climb higher", "publisher": "CNBC",
             "link": "https://cnbc.com/article2", "providerPublishTime": 1700000000},
        ]}
    async def mock_dead_llm(prompt, **kw):
        return None  # vLLM outage

    patch_server("news_search", _none)
    patch_server("_finnews_articles", _none)
    patch_server("stock_news", mock_stock_news)
    patch_server("fast_llm_json", mock_dead_llm)

    cfg = await build_stock_news_config("stock market news")
    assert cfg.get("answer"), "Degraded news card must still return a non-empty fallback answer"
    assert len(cfg["items"]) == 2
    assert "https://wsj.com/article1" in cfg["items"][0]["url"]
    spoken = _spoken_summary("data_card", cfg, "stock market news")
    assert spoken and not spoken.startswith("Found 2, starting with")
