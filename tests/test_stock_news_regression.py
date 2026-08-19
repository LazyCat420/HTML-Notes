import pytest
import asyncio
import app.main as main
import app.config_builders as cb

def test_fetch_news_page_text_exported():
    """Ensure _fetch_news_page_text is properly defined and exported in module namespace."""
    assert hasattr(main, '_fetch_news_page_text'), "_fetch_news_page_text must be exported by app.services.search to app.main"
    assert hasattr(cb, '_fetch_news_page_text'), "_fetch_news_page_text must be accessible in app.config_builders"
    assert not hasattr(main, '_fetch_news_fetch_news_page_text'), "Duplicate naming typo must not exist"

@pytest.mark.asyncio
async def test_build_stock_news_config_runs():
    """Verify build_stock_news_config runs without NameError."""
    res = await cb.build_stock_news_config("stock market news")
    assert res is None or isinstance(res, dict)
    if isinstance(res, dict):
        assert "items" in res
