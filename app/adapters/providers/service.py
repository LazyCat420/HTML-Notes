import logging
from typing import Any, Dict, Optional
from app.services.location import get_weather
from app.services.sports import sports_scores
from app.services.finance import stock_snapshot, stock_news
from app.config_builders import build_news_config
from app.services.youtube_helpers import search_youtube_videos
from app.services.search import web_search_ex, read_web_page

logger = logging.getLogger(__name__)


class PresentationProviderAdapter:
    """
    Adapter bridging application domain presentation feeds and external providers:
    - Weather (Open-Meteo)
    - Sports (ESPN / FlashScore)
    - Finance & Tickers (Yahoo Finance)
    - News (GDELT / Google News)
    - YouTube Search
    - Standalone local web retrieval fallbacks
    """

    @staticmethod
    async def get_weather_data(location: str, units: str = "fahrenheit") -> Dict[str, Any]:
        return await get_weather(location, units=units)

    @staticmethod
    async def get_sports_scores(league: str) -> Dict[str, Any]:
        return await sports_scores(league)

    @staticmethod
    async def get_stock_snapshot(symbol: str, range_str: str = "1mo") -> Dict[str, Any]:
        return await stock_snapshot(symbol, range_str)

    @staticmethod
    async def get_stock_news(query: str, limit: int = 8) -> Dict[str, Any]:
        return await stock_news(query, limit=limit)

    @staticmethod
    async def get_news_config(topic: str) -> Dict[str, Any]:
        return await build_news_config(topic)

    @staticmethod
    async def search_youtube(query: str, limit: int = 5, form: str = "all") -> Dict[str, Any]:
        results = await search_youtube_videos(query, limit=limit, form=form)
        return {"results": results, "count": len(results)}

    @staticmethod
    async def fallback_web_search(query: str, limit: int = 6) -> Dict[str, Any]:
        results, engines_down = await web_search_ex(query, limit=limit)
        if engines_down:
            return {
                "results": [],
                "count": 0,
                "is_error": True,
                "message": "Web search backends unreachable. Standalone fallback failed."
            }
        return {"results": results, "count": len(results)}

    @staticmethod
    async def fallback_read_page(url: str, max_chars: int = 6000) -> Dict[str, Any]:
        return await read_web_page(url, max_chars=max_chars)

provider_adapter = PresentationProviderAdapter()
