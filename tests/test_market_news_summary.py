import pytest
from app import main as m
from app.config_builders import build_stock_news_config, build_news_config
from app.canvas_manager import _spoken_summary


@pytest.mark.asyncio
async def test_build_stock_news_config_has_answer_on_success(monkeypatch):
    async def mock_stock_news(q, limit=8):
        return {
            "news": [
                {"title": "S&P 500 hits record high", "publisher": "Reuters", "link": "https://reuters.com/article1", "providerPublishTime": 1700000000},
                {"title": "Tech stocks rally on earnings", "publisher": "Bloomberg", "link": "https://bloomberg.com/article2", "providerPublishTime": 1700000000},
            ]
        }
    async def mock_fetch_page(item):
        return "Markets surged today as tech earnings exceeded expectations."
    async def mock_llm_json(prompt, **kw):
        return {
            "overview": "U.S. markets gained as major tech companies posted strong quarterly earnings.",
            "items": [
                {"index": 0, "title": "S&P 500 Record High", "summary": "The S&P 500 reached a new all-time high driven by tech momentum."},
                {"index": 1, "title": "Tech Earnings Rally", "summary": "Tech giants reported earnings beating analyst expectations across the board."}
            ]
        }

    async def mock_empty_shared(q, limit=6):
        return []

    monkeypatch.setattr(m, "_shared_news_search", mock_empty_shared)
    monkeypatch.setattr(m, "stock_news", mock_stock_news)
    monkeypatch.setattr(m, "_fetch_news_page_text", mock_fetch_page)
    monkeypatch.setattr(m, "fast_llm_json", mock_llm_json)

    cfg = await build_stock_news_config("stock market news")
    assert cfg.get("answer") == "U.S. markets gained as major tech companies posted strong quarterly earnings."
    assert cfg.get("subtitle") == "U.S. markets gained as major tech companies posted strong quarterly earnings."
    assert len(cfg.get("items", [])) == 2
    assert cfg["items"][0]["title"] == "S&P 500 Record High"
    assert "https://reuters.com/article1" in cfg["items"][0]["url"]

    spoken = _spoken_summary("data_card", cfg, "stock market news")
    assert spoken == "U.S. markets gained as major tech companies posted strong quarterly earnings."


@pytest.mark.asyncio
async def test_build_stock_news_config_has_answer_on_degraded(monkeypatch):
    async def mock_stock_news(q, limit=8):
        return {
            "news": [
                {"title": "Markets slip on inflation report", "publisher": "WSJ", "link": "https://wsj.com/article1", "providerPublishTime": 1700000000},
                {"title": "Bond yields climb higher", "publisher": "CNBC", "link": "https://cnbc.com/article2", "providerPublishTime": 1700000000},
            ]
        }
    async def mock_fetch_page(item):
        return "Inflation metrics came in hotter than expected, causing cautious trading."
    async def mock_dead_llm(prompt, **kw):
        return None  # simulates vLLM outage / failure

    async def mock_empty_shared(q, limit=6):
        return []

    monkeypatch.setattr(m, "_shared_news_search", mock_empty_shared)
    monkeypatch.setattr(m, "stock_news", mock_stock_news)
    monkeypatch.setattr(m, "_fetch_news_page_text", mock_fetch_page)
    monkeypatch.setattr(m, "fast_llm_json", mock_dead_llm)

    cfg = await build_stock_news_config("stock market news")
    assert cfg.get("answer"), "Degraded news card must still return a non-empty fallback answer"
    assert len(cfg.get("items", [])) == 2
    assert "https://wsj.com/article1" in cfg["items"][0]["url"]

    spoken = _spoken_summary("data_card", cfg, "stock market news")
    assert spoken, "Spoken summary must not be empty"
    assert not spoken.startswith("Found 2, starting with"), "Must speak substantive content, not item counts"
