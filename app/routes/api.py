from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

router = APIRouter()

@router.get("/api/stock/{symbol}")
async def api_stock(symbol: str, range: str = "1mo"):
    """Backs the stock widget's range tabs — switching 1D/1M/1Y/10Y/MAX refetches
    here instead of going through the agent again."""
    return await stock_snapshot(symbol, range)


@router.get("/api/fx/{base}")
async def api_fx(base: str):
    """Backs the converter's currency tab — latest rates for `base` (keyless,
    cached). Empty {} degrades to 'Rates unavailable' client-side."""
    return await fetch_fx_rates(base)


@router.get("/api/crypto/{coin_id}")
async def api_crypto(coin_id: str, range: str = "30d"):
    """Backs the crypto card's range tabs — switching 1D/7D/30D/1Y/MAX refetches
    the price series here instead of going through the agent again."""
    return await _crypto_snapshot(coin_id, range)


@router.get("/api/youtube/candidates")
async def api_youtube_candidates(query: str, limit: int = 6,
                                 form: Optional[str] = None):
    """Multi-result YouTube search used by the player widget to recover from
    embed-blocked videos (it walks the list until one plays). `form` keeps the
    replacement the same KIND as what was playing (a Short hops to a Short)."""
    results = await search_youtube_videos(query, limit=min(limit, 12), form=form)
    return {"results": results, "count": len(results)}


@router.get("/api/youtube/search")
async def api_youtube_search(query: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MUSIC_PLAYER_URL}/api/youtube/search", params={"query": query}, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except Exception as e:
        logger.error(f"Failed to proxy YouTube search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


