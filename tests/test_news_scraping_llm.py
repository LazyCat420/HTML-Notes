import pytest
import respx
import httpx
from app import main as m
from app.services.search import _enrich_news, _shared_news_search, _is_generic_news_desc
from app.config_builders import build_stock_news_config, build_news_config
from app.llm import fast_llm_json


def test_is_generic_news_desc_identifies_google_boilerplate():
    boilerplate = "Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News."
    assert _is_generic_news_desc(boilerplate) is True
    assert _is_generic_news_desc("Visit the post for more") is True
    assert _is_generic_news_desc("Access to this page has been denied") is True
    assert _is_generic_news_desc("Short") is True
    assert _is_generic_news_desc("U.S. inflation cooled in July, paving the way for the Federal Reserve to cut interest rates next month.") is False


@pytest.mark.asyncio
@respx.mock
async def test_enrich_news_rejects_google_news_boilerplate():
    url = "https://news.google.com/rss/articles/CBMi12345"
    html = '''<html><head>
    <meta property="og:description" content="Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News.">
    <meta property="og:image" content="https://lh3.googleusercontent.com/generic-logo.png">
    </head><body>Redirecting...</body></html>'''
    respx.get(url).respond(200, text=html)

    items = [{"title": "Flooding in Tibet", "url": url, "snippet": "", "image": ""}]
    await _enrich_news(items)

    assert items[0]["snippet"] == "", "Boilerplate description must be discarded"
    assert items[0]["image"] == "", "Generic logo must be discarded"


@pytest.mark.asyncio
@respx.mock
async def test_shared_news_search_defaults_empty_topic():
    respx.post("http://10.0.0.16:5591/execute/news_search").respond(
        200,
        json={"items": [{"title": "Global Tech Rally", "url": "https://reuters.com/tech", "snippet": "Tech shares rallied today."}]}
    )
    items = await _shared_news_search("", limit=5)
    assert len(items) == 1
    assert items[0]["title"] == "Global Tech Rally"


@pytest.mark.asyncio
@respx.mock
async def test_fast_llm_json_extracts_from_reasoning_models():
    # Simulates Qwen / DeepSeek-R1 emitting reasoning with length finish and content in reasoning
    respx.get("http://10.0.0.30:8000/v1/models").respond(200, json={"data": [{"id": "qwen3.6-35b"}]})
    respx.post("http://10.0.0.30:8000/v1/chat/completions").respond(
        200,
        json={
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning": "Thinking...\nDraft JSON:\n{\"overview\": \"Markets gained on tech momentum.\", \"items\": []}"
                },
                "finish_reason": "length"
            }]
        }
    )
    res = await fast_llm_json("Summarize market news")
    assert res is not None
    assert res.get("overview") == "Markets gained on tech momentum."


@pytest.mark.asyncio
async def test_build_stock_news_config_prioritizes_shared_news(monkeypatch):
    async def mock_shared(query, limit=6):
        return [
            {"title": "Tech Stocks Rally on AI Demand", "url": "https://seekingalpha.com/article1", "source": "Seeking Alpha", "snippet": "AI chips surge."}
        ]
    async def mock_stock_news_fail(query, limit=10):
        pytest.fail("stock_news (Yahoo) should not be called when shared_news succeeds")
    async def mock_llm_json(prompt, **kw):
        return {
            "overview": "Tech stocks moved higher today.",
            "items": [{"index": 0, "title": "Tech Stocks Rally on AI Demand", "summary": "AI chips surge."}]
        }

    monkeypatch.setattr(m, "_shared_news_search", mock_shared)
    monkeypatch.setattr(m, "stock_news", mock_stock_news_fail)
    monkeypatch.setattr(m, "_fetch_news_page_text", lambda n: "")
    monkeypatch.setattr(m, "fast_llm_json", mock_llm_json)

    cfg = await build_stock_news_config("stock market news")
    assert cfg is not None
    assert len(cfg.get("items", [])) == 1
    assert cfg["items"][0]["title"] == "Tech Stocks Rally on AI Demand"
    assert "https://seekingalpha.com/article1" in cfg["items"][0]["url"]
