import httpx
import logging
import re
import urllib.parse

# Needed up here, not in the import block further down: the helper functions
# below are defined before it, and their annotations are evaluated at def time.
from typing import Any, Dict, List, Optional

async def search_youtube_videos(query: str, limit: int = 5, order: str = "relevance") -> list:
    """Search YouTube and return video dicts with video_id/id, title, and channel.

    order="date" sorts results newest-first (for "latest video from <channel>" asks).
    order="live" applies YouTube's LIVE filter — the only reliable way to reach an
    actual stream. A plain search for "cnn live news" returns recorded clips; the
    filtered one returns "CNN Headlines: 24/7 Live News".
    """
    def _unescape(s: str) -> str:
        try:
            return json.loads('"' + s + '"')
        except Exception:
            return s

    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        if order == "date":
            url += "&sp=CAI%253D"
        elif order == "live":
            url += "&sp=EgJAAQ%253D%253D"
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
            html = resp.text
            results = []
            seen = set()
            for block in html.split('"videoRenderer":')[1:]:
                vid_match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', block)
                title_match = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]', block)
                channel_match = re.search(r'"longBylineText":\{"runs":\[\{"text":"(.*?)"', block)
                if not vid_match or not title_match:
                    continue
                vid = vid_match.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                results.append({
                    "video_id": vid,
                    "id": vid,
                    "title": _unescape(title_match.group(1)),
                    "channel": _unescape(channel_match.group(1)) if channel_match else None,
                })
                if len(results) >= limit:
                    break
            return results
    except Exception as e:
        logger.error(f"search_youtube_videos error: {e}")
    return []


# crawl4ai renders Brave's result page as markdown with the outbound hrefs
# intact, which is the only engine/target pair that survives bot detection:
# DuckDuckGo serves a CAPTCHA to every engine, Bing and Mojeek fail outright,
# and the http engine gets challenged. Brave's own chrome (favicons, thumbnails,
# nav) also appears as links, hence the noise-host filter.
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\((https?://[^\s)]+)\)')
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_SEARCH_NOISE_HOSTS = ("search.brave.com", "imgs.search.brave.com", "brave.com/download")


async def _scrape(url: str, engine: str = "crawl4ai", timeout: float = 90.0) -> str:
    """Fetch a URL through scraper-service. Returns page content ('' on failure)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{SCRAPER_SERVICE_URL}/scrape",
                json={"url": url, "engine": engine},
            )
            payload = resp.json()
            if payload.get("success"):
                return payload.get("content") or ""
            logger.warning(f"_scrape({engine}) failed for {url}: {payload.get('error')}")
    except Exception as e:
        logger.warning(f"_scrape({engine}) error for {url}: {e}")
    return ""


async def web_search(query: str, limit: int = 6) -> list:
    """Keyless web search via Brave + scraper-service. Returns [{title,url,snippet}]."""
    target = f"https://search.brave.com/search?q={urllib.parse.quote(query)}"

    markdown = await _scrape(target, engine="crawl4ai")
    results, seen = [], []

    for raw_title, href in _MD_LINK_RE.findall(markdown):
        if any(h in href for h in _SEARCH_NOISE_HOSTS):
            continue
        title = _MD_IMAGE_RE.sub("", raw_title).strip()
        if len(title) < 3:
            continue
        key = href.split("?utm")[0]
        if key in seen:
            continue
        seen.append(key)
        results.append({"title": title, "url": href, "snippet": ""})
        if len(results) >= limit:
            break

    # The prose between links is the result description; pair them up in order.
    plain = _MD_LINK_RE.sub("\n", _MD_IMAGE_RE.sub("", markdown))
    paragraphs = [p.strip() for p in plain.split("\n") if len(p.strip()) > 60]
    for i, result in enumerate(results):
        if i < len(paragraphs):
            result["snippet"] = paragraphs[i][:300]

    if results:
        return results

    # crawl4ai occasionally returns a link-less render; playwright still gives
    # readable result text, which is enough for the model to answer from.
    text = await _scrape(target, engine="playwright", timeout=60.0)
    if text:
        return [{"title": f"Search results for '{query}'", "url": target, "snippet": text[:1500]}]
    return []


STOCK_RANGES = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "5y", "10y", "max")

# Yahoo picks the candle size; too fine over a long window returns tens of
# thousands of points and a chart nobody can read.
_STOCK_INTERVALS = {
    "1d": "5m", "5d": "30m", "1mo": "1d", "3mo": "1d", "6mo": "1d",
    "1y": "1d", "5y": "1wk", "10y": "1wk", "max": "1mo",
}

_YAHOO_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_yahoo_crumb: dict = {"crumb": None, "cookies": None}


def _sma(values: list, window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return round(sum(values[-window:]) / window, 2)


def _rsi(values: list, period: int = 14) -> Optional[float]:
    """Wilder's RSI. <30 oversold, >70 overbought."""
    if len(values) <= period:
        return None
    gains, losses = [], []
    for prev, curr in zip(values[-(period + 1):-1], values[-period:]):
        delta = curr - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _annualized_volatility(values: list) -> Optional[float]:
    if len(values) < 20:
        return None
    returns = [(b - a) / a for a, b in zip(values[:-1], values[1:]) if a]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round((variance ** 0.5) * (252 ** 0.5) * 100, 1)


async def _yahoo_fundamentals(client: httpx.AsyncClient, symbol: str) -> dict:
    """Company fundamentals. Yahoo's quoteSummary rejects anonymous callers with
    "Invalid Crumb", so grab a cookie + crumb once and reuse it."""
    try:
        if not _yahoo_crumb["crumb"]:
            seed = await client.get("https://fc.yahoo.com", headers={"User-Agent": _YAHOO_UA})
            crumb_resp = await client.get(
                "https://query2.finance.yahoo.com/v1/test/getcrumb",
                headers={"User-Agent": _YAHOO_UA}, cookies=seed.cookies)
            if crumb_resp.status_code == 200 and crumb_resp.text.strip():
                _yahoo_crumb["crumb"] = crumb_resp.text.strip()
                _yahoo_crumb["cookies"] = seed.cookies

        if not _yahoo_crumb["crumb"]:
            return {}

        modules = "summaryDetail,defaultKeyStatistics,financialData,assetProfile"
        resp = await client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}"
            f"?modules={modules}&crumb={urllib.parse.quote(_yahoo_crumb['crumb'])}",
            headers={"User-Agent": _YAHOO_UA}, cookies=_yahoo_crumb["cookies"])
        result = (resp.json().get("quoteSummary", {}).get("result") or [None])[0]
        if not result:
            _yahoo_crumb["crumb"] = None  # expired — re-seed next call
            return {}

        detail = result.get("summaryDetail", {}) or {}
        stats = result.get("defaultKeyStatistics", {}) or {}
        financial = result.get("financialData", {}) or {}
        profile = result.get("assetProfile", {}) or {}

        def fmt(source: dict, key: str):
            value = source.get(key)
            return value.get("fmt") if isinstance(value, dict) else value

        return {
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "market_cap": fmt(detail, "marketCap"),
            "pe_ratio": fmt(detail, "trailingPE"),
            "forward_pe": fmt(detail, "forwardPE"),
            "eps": fmt(stats, "trailingEps"),
            "beta": fmt(detail, "beta"),
            "dividend_yield": fmt(detail, "dividendYield"),
            "profit_margin": fmt(financial, "profitMargins"),
            "revenue": fmt(financial, "totalRevenue"),
            "revenue_growth": fmt(financial, "revenueGrowth"),
            "analyst_target": fmt(financial, "targetMeanPrice"),
            "recommendation": financial.get("recommendationKey"),
        }
    except Exception as e:
        logger.warning(f"fundamentals({symbol}) failed: {e}")
        return {}


async def _yahoo_chart(client: httpx.AsyncClient, symbol: str, range_: str) -> Optional[dict]:
    interval = _STOCK_INTERVALS.get(range_, "1d")
    resp = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?range={urllib.parse.quote(range_)}&interval={interval}",
        headers={"User-Agent": _YAHOO_UA})
    payload = resp.json()
    return (payload.get("chart", {}).get("result") or [None])[0]


async def stock_snapshot(symbol: str, range_: str = "1mo") -> dict:
    """Full picture for a ticker: price series + technicals + fundamentals.

    Every tools-api market tool (get_stock, get_historical_prices,
    generate_chart) has a null endpoint in this deployment, so without this the
    model falls back to scraping the web for prices — slow, and it can't get
    clean numbers out of it anyway. Yahoo is keyless and answers in well under
    a second.

    Technicals are always computed from a 1y daily series, never from the
    displayed range: a 5-day window has no 200-day moving average, and an RSI
    taken over 5-minute candles is a different (and misleading) number from the
    daily RSI a reader expects. The two fetches run concurrently.
    """
    if range_ not in STOCK_RANGES:
        range_ = "1mo"

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            display, daily, fundamentals = await asyncio.gather(
                _yahoo_chart(client, symbol, range_),
                _yahoo_chart(client, symbol, "1y"),
                _yahoo_fundamentals(client, symbol),
                return_exceptions=True,
            )

        if not isinstance(display, dict) or not display:
            return {"error": f"No price data for '{symbol}'", "is_error": True}
        if not isinstance(fundamentals, dict):
            fundamentals = {}

        meta = display.get("meta", {})
        stamps = display.get("timestamp") or []
        quote = (display.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        # 5d is intraday too, but a bare "13:30" repeats once per day with nothing
        # to tell the days apart — it needs the weekday.
        fmt = {"1d": "%H:%M", "5d": "%a %H:%M"}.get(
            range_, "%b %Y" if range_ in ("5y", "10y", "max") else "%b %d")

        labels, values, vols = [], [], []
        for i, (stamp, close) in enumerate(zip(stamps, closes)):
            if close is None:
                continue
            moment = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
            labels.append(moment.strftime(fmt))
            values.append(round(float(close), 2))
            vols.append(volumes[i] if i < len(volumes) else None)

        if not values:
            return {"error": f"No price data for '{symbol}'", "is_error": True}

        # Technicals off the 1y daily series (falls back to the display series).
        daily_closes = values
        if isinstance(daily, dict) and daily:
            candidate = [c for c in ((daily.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
                         if c is not None]
            if candidate:
                daily_closes = [round(float(c), 2) for c in candidate]

        price = meta.get("regularMarketPrice", values[-1])
        first, last = values[0], values[-1]
        change_pct = round(((last - first) / first) * 100, 2) if first else 0.0
        sma50, sma200 = _sma(daily_closes, 50), _sma(daily_closes, 200)
        high52, low52 = meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow")

        technicals = {
            "sma_20": _sma(daily_closes, 20),
            "sma_50": sma50,
            "sma_200": sma200,
            "rsi_14": _rsi(daily_closes),
            "volatility": _annualized_volatility(daily_closes),
            "week52_high": round(high52, 2) if high52 else None,
            "week52_low": round(low52, 2) if low52 else None,
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "volume": meta.get("regularMarketVolume"),
            # What a reader actually wants to know: where does price sit relative
            # to trend, and how much of the 52-week band has it used up?
            "trend": ("bullish" if sma50 and sma200 and sma50 > sma200
                      else "bearish" if sma50 and sma200 else None),
            "vs_sma_50": (round(((price - sma50) / sma50) * 100, 1) if sma50 else None),
            "week52_position": (round(((price - low52) / (high52 - low52)) * 100)
                                if high52 and low52 and high52 > low52 else None),
        }

        return {
            "symbol": meta.get("symbol", symbol.upper()),
            "name": meta.get("longName") or meta.get("shortName") or symbol.upper(),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("fullExchangeName"),
            "price": price,
            "range": range_,
            "ranges": list(STOCK_RANGES),
            "change_pct": change_pct,
            "labels": labels,
            "values": values,
            "volumes": vols,
            "technicals": technicals,
            "fundamentals": fundamentals,
        }
    except Exception as e:
        logger.error(f"stock_snapshot({symbol}) error: {e}")
        return {"error": str(e), "is_error": True}


# Kept under the old name: it's what the registered tool schema calls.
stock_history = stock_snapshot


# ESPN's scoreboard API is keyless and covers every league we care about. Friendly
# names → ESPN paths, longest key first so "champions league" beats "league" and
# "college football" beats "football".
SPORTS_LEAGUES = {
    "fifa": "soccer/fifa.world", "world cup": "soccer/fifa.world",
    "premier league": "soccer/eng.1", "epl": "soccer/eng.1",
    "champions league": "soccer/uefa.champions", "ucl": "soccer/uefa.champions",
    "la liga": "soccer/esp.1", "serie a": "soccer/ita.1", "bundesliga": "soccer/ger.1",
    "mls": "soccer/usa.1", "soccer": "soccer/fifa.world",
    "ufc": "mma/ufc", "mma": "mma/ufc",
    "nba": "basketball/nba", "basketball": "basketball/nba",
    "wnba": "basketball/wnba",
    "college football": "football/college-football",
    "college basketball": "basketball/mens-college-basketball",
    "nfl": "football/nfl", "football": "football/nfl",
    "mlb": "baseball/mlb", "baseball": "baseball/mlb",
    "nhl": "hockey/nhl", "hockey": "hockey/nhl",
}
# Longest first so multi-word leagues win over the single word inside them.
_SPORTS_KEYS = sorted(SPORTS_LEAGUES, key=len, reverse=True)


def resolve_league(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    for key in _SPORTS_KEYS:
        if re.search(rf'\b{re.escape(key)}\b', lowered):
            return SPORTS_LEAGUES[key]
    return None


def _competitor(side: dict) -> dict:
    """A competitor is a team in team sports and an athlete in MMA/boxing — the
    'team' key is simply absent for a UFC fight."""
    team = side.get("team") or {}
    athlete = side.get("athlete") or {}
    return {
        "name": (team.get("displayName") or athlete.get("displayName")
                 or team.get("shortDisplayName") or athlete.get("shortName") or "TBD"),
        "abbrev": team.get("abbreviation") or athlete.get("shortName") or "",
        "logo": team.get("logo") or athlete.get("headshot") or "",
        "score": side.get("score"),
        "record": (side.get("records") or [{}])[0].get("summary", ""),
        "winner": bool(side.get("winner")),
    }


async def sports_scores(league: str) -> dict:
    """Fixtures and scores for a league. Keyless, sub-second.

    Without this, "fifa scores" had no tool at all: it fell to the agent, which
    web-searched (a 20-60s scrape) and tried to squeeze the result into a text
    card. A UFC event nests every fight under one event's `competitions`, so
    those get flattened into individual matchups alongside team-sport games.
    """
    path = resolve_league(league) or SPORTS_LEAGUES.get(league.lower().strip()) or league
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard",
                headers={"User-Agent": _YAHOO_UA})
            payload = resp.json()
    except Exception as e:
        logger.error(f"sports_scores({league}) error: {e}")
        return {"error": str(e), "is_error": True}

    info = (payload.get("leagues") or [{}])[0]
    matchups = []
    for event in payload.get("events", []):
        for comp in event.get("competitions", []):
            sides = comp.get("competitors") or []
            if len(sides) < 2:
                continue
            # ESPN puts home first for team sports; MMA has no home/away at all.
            away, home = ((sides[1], sides[0]) if sides[0].get("homeAway") == "home"
                          else (sides[0], sides[1]))
            status = (comp.get("status") or {}).get("type") or {}
            matchups.append({
                "home": _competitor(home),
                "away": _competitor(away),
                "state": status.get("state"),           # pre | in | post
                "detail": status.get("shortDetail"),    # "Final", "7/18 - 5:00 PM EDT", "62'"
                "completed": bool(status.get("completed")),
                "note": (comp.get("notes") or [{}])[0].get("headline", ""),
            })

    if not matchups:
        return {"error": f"No fixtures found for '{league}'. It may be the off-season.",
                "is_error": True}

    return {
        "league": info.get("abbreviation") or info.get("name") or league,
        "title": info.get("name") or league,
        "season": (payload.get("season") or {}).get("year"),
        "events": matchups,
    }


async def read_web_page(url: str, max_chars: int = 6000) -> dict:
    """Fetch and return the readable text of a page."""
    content = await _scrape(url, engine="crawl4ai")
    if not content:
        content = await _scrape(url, engine="playwright", timeout=60.0)
    if not content:
        return {"error": f"Could not fetch {url}", "is_error": True}
    return {"url": url, "content": content[:max_chars]}


from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app import database
from app.config import PORT, PRISM_URL, LAZY_AGENT_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL, TTS_SERVICE_URL, MUSIC_PLAYER_URL, SCRAPER_SERVICE_URL
import asyncio
import datetime
import itertools
import json
import os
import time
import uuid
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html


logging.basicConfig(level=logging.INFO)

import logging

logger = logging.getLogger(__name__)

# ── Canvas state & turn concurrency ─────────────────────────────────────────
#
# Turns run CONCURRENTLY (the DGX Spark serves several generations at once), but
# the canvas is shared mutable state, so every write is a locked read-modify-
# write against the server's copy. Two things make that safe:
#
#   1. A turn never mutates its own stale snapshot. It re-reads the live canvas
#      inside the lock at the moment it commits, so a widget added by a turn
#      that finished in the meantime is still there.
#   2. Every commit bumps a version. The client applies a canvas only if it is
#      newer than the one already on screen, so a slow turn's SSE event arriving
#      late can't roll the canvas back over a faster turn's widget.
#
# The old design held the lock for a whole turn, which was correct but pinned
# throughput to one generation at a time.
_session_canvas: Dict[str, str] = {}
_session_locks: Dict[str, asyncio.Lock] = {}
_session_inflight: Dict[str, int] = {}

# Canvas versions must never go BACKWARDS across a restart. A plain per-session
# counter starting at 0 looked fine until you redeploy: the browser tab is still
# open holding canvasVersion=7, the fresh container starts emitting 1, 2, 3 — the
# client drops every one as stale and the canvas stops painting. The tool ran and
# the model truthfully says it added the widget, but nothing appears. Seeding from
# epoch-ms makes every new version beat anything a live tab could be holding.
_version_counter = itertools.count(int(time.time() * 1000))
_session_canvas_version: Dict[str, int] = {}

# How many agent turns may generate at once. Beyond this, turns queue.
AGENT_CONCURRENCY = int(os.getenv("AGENT_CONCURRENCY", "4"))
_turn_semaphore = asyncio.Semaphore(AGENT_CONCURRENCY)

# canvas_read_dom / canvas_modify_dom arrive as separate HTTP calls from the
# gateway carrying no session id, so they resolve against the most recent one.
_last_active_session: str = ""


def _canvas_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def get_session_canvas(session_id: str) -> str:
    return _session_canvas.get(session_id, "")


def set_session_canvas(session_id: str, html: str) -> int:
    """Store the canvas and return its new (globally monotonic) version."""
    global _last_active_session
    _session_canvas[session_id] = html
    version = next(_version_counter)
    _session_canvas_version[session_id] = version
    _last_active_session = session_id
    return version


async def commit_canvas(session_id: str, mutate) -> Optional[str]:
    """Apply `mutate(soup)` to the live canvas under the session lock.

    `mutate` returns False to abort (e.g. its target selector matched nothing).
    Returns a ready-to-send SSE `component` line, or None if the mutation was a
    no-op. Reading the base INSIDE the lock is what lets turns run in parallel:
    each one commits against whatever is on the canvas right now.
    """
    async with _canvas_lock(session_id):
        soup = BeautifulSoup(get_session_canvas(session_id) or "", 'html.parser')
        if mutate(soup) is False:
            return None
        html = str(soup)
        version = set_session_canvas(session_id, html)
    return f'data: {json.dumps({"type": "component", "content": html, "version": version})}\n\n'


async def _run_turn(session_id: str, current_canvas: str, generator_factory):
    """Gate a turn on the concurrency semaphore and track it as in-flight.

    While no turn is running for a session, the client's canvas is authoritative
    (it may have dismissed a widget locally), so we adopt its snapshot. Once a
    turn is in flight the server's copy wins — a concurrent turn's client
    snapshot predates whatever just landed.
    """
    async with _turn_semaphore:
        async with _canvas_lock(session_id):
            if current_canvas and _session_inflight.get(session_id, 0) == 0:
                set_session_canvas(session_id, current_canvas)
            _session_inflight[session_id] = _session_inflight.get(session_id, 0) + 1
        try:
            async for chunk in generator_factory():
                yield chunk
        finally:
            async with _canvas_lock(session_id):
                _session_inflight[session_id] = max(0, _session_inflight.get(session_id, 0) - 1)

def get_canvas_summary(html: str) -> str:
    """Parses raw canvas HTML and extracts widget details into a tiny, token-efficient summary."""
    if not html or html.strip() == "" or html == "Canvas is empty.":
        return "Canvas is currently empty."
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        widgets = []
        for card in soup.select(".glass-card, .widget-container"):
            widget_id = card.get("id", "unknown")
            xdata = card.get("x-data", "")
            
            # Find title
            title_el = card.select_one(".glass-card-title, h3, h2")
            title = title_el.get_text(strip=True) if title_el else "Untitled"
            
            # Identify widget type
            wtype = "custom"
            classes = card.get("class", [])
            for cls in classes:
                if cls in ("checklist", "clock", "notes", "iframe_app", "mini_music_player", "youtube_player"):
                    wtype = cls
                    break
                if cls in ("data-card", "image-widget", "chart-widget"):
                    wtype = cls.replace("-widget", "").replace("-", "_")
                    break
            if wtype == "custom" and xdata:
                if "checklistWidget" in xdata: wtype = "checklist"
                elif "clockWidget" in xdata: wtype = "clock"
                elif "notesWidget" in xdata: wtype = "notes"
                elif "musicPlayerWidget" in xdata: wtype = "mini_music_player"
                elif "youtubePlayerWidget" in xdata: wtype = "youtube_player"
                
            widgets.append(f"- Widget ID: #{widget_id}, Type: {wtype}, Title: '{title}'")
        
        if not widgets:
            return "Canvas contains no recognizable widgets."
        return "\n".join(widgets)
    except Exception as e:
        logger.error(f"Error getting canvas summary: {e}")
        return html[:2000]


app = FastAPI(
    title="HTML-Notes Engine",
    description="Local-first AI knowledge journal with constrained HTML rendering"
)

# Request / Response Schemas

class MessageRequest(BaseModel):
    session_id: str
    message: str
    target_note_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    current_canvas: Optional[str] = None
    use_lazy_agent: bool = True

class CreateNoteRequest(BaseModel):
    title: str
    tags: List[str] = []
    links: List[str] = []
    canonical_blocks: List[Dict[str, Any]] = []
    rendered_html: str

class UpdateNoteRequest(BaseModel):
    note_id: str
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    links: Optional[List[str]] = None
    canonical_blocks: Optional[List[Dict[str, Any]]] = None
    rendered_html: Optional[str] = None

class LinkNotesRequest(BaseModel):
    source_note_id: str
    target_note_id: str

class TranscribeRequest(BaseModel):
    audio: str # Base64 audio payload

class TTSSynthesizeRequest(BaseModel):
    text: str

# API Endpoints

@app.get("/models")
async def get_models():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{PRISM_URL}/config?includeLocal=true")
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch models from Prism")
            
            data = resp.json()
            models_map = data.get("textToText", {}).get("models", {})
            
            flat_models = []
            for provider, provider_models in models_map.items():
                for model in provider_models:
                    flat_models.append({
                        "provider": provider,
                        "model": model.get("name"),
                        "label": model.get("label") or model.get("name")
                    })
            return JSONResponse(content={"models": flat_models})
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        raise HTTPException(status_code=500, detail=str(e))



def extract_youtube_query(text: str) -> str:
    text_lower = text.lower().strip()
    # Strip common trigger prefixes
    pattern = r'^(?:add|show|open|play|create|get)\s+(?:a\s+)?(?:youtube|yt)\s+(?:player\s+|widget\s+|video\s+)*(?:for\s+|with\s+|of\s+)*'
    cleaned = re.sub(pattern, '', text_lower)
    for prefix in ("youtube", "yt", "play"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    quote_match = re.search(r'["\'“]([^"\'”]+)["\'”]', cleaned)
    if quote_match:
        return quote_match.group(1).strip()
    return cleaned.strip()

def is_query_vague(query_text: str) -> bool:
    """
    Checks if a query text contains meaningful content or is just conversational filler / general widget spawn commands.
    """
    if not query_text:
        return True
    
    # Lowercase & strip punctuation
    text = query_text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    
    # Hybrid Catalog of filler words/synonyms
    filler_words = {
        # Action verbs
        "add", "show", "open", "play", "create", "pull", "up", "get", "find", "search", "insert", "inject", "spawn", "display",
        # Articles & prepositions
        "a", "an", "the", "some", "any", "to", "on", "for", "with", "in", "at",
        # Conversational filler
        "please", "now", "here", "thanks", "thank", "you", "would", "like", "want", "need", "can", "could", "me", "us",
        # Widget type descriptors (predefined categories)
        "widget", "player", "video", "youtube", "yt", "channel", "clip", "stream", "online", "notes", "notepad", "scratchpad", 
        "clock", "time", "checklist", "todo", "todolist", "task", "list", "music", "radio", "song", "audio", "lofi", "beats"
    }
    
    words = text.split()
    meaningful_words = [w for w in words if w not in filler_words]

    # Returns True if no meaningful words are left
    return len(meaningful_words) == 0

# Words that never describe a genre/artist in a music ask. Unlike the
# is_low_content catalog, genre terms like "lofi" and "beats" are kept.
MUSIC_FILLER_WORDS = {
    # Action verbs
    "add", "show", "open", "play", "create", "pull", "up", "get", "find", "search", "insert", "inject", "spawn", "display", "put", "start", "give",
    # Articles & prepositions
    "a", "an", "the", "some", "any", "to", "on", "for", "with", "in", "at", "of", "by",
    # Conversational filler
    "please", "now", "here", "thanks", "thank", "you", "would", "like", "want", "need", "can", "could", "me", "us", "hey", "hi",
    # Music/widget descriptors that aren't genres
    "widget", "player", "music", "radio", "song", "songs", "audio", "track", "tracks", "tune", "tunes", "playlist", "station",
}

def extract_music_genre(query_text: str) -> str:
    """Pull the requested genre/artist out of a music ask.
    "play reggae music" -> "reggae"; "open the music player" -> ""."""
    text = re.sub(r'[^\w\s]', '', (query_text or '').lower().strip())
    return " ".join(w for w in text.split() if w not in MUSIC_FILLER_WORDS)

# Media/data intent guards for the heuristic fast-path. When one of these
# matches, the request goes to the agent instead of keyword-spawning a widget
# ("clock for video" is a video ask, not a clock ask).
VIDEO_ASK_RE = re.compile(r'\b(youtube|video|videos|yt|watch|clip|clips|trailer|movie|highlights?)\b')

# A news video must be sorted by DATE. "fifa news video" searched by relevance
# returns whatever is most-watched for those words — a years-old recap — which is
# never what "news" means. Recency words flip the search to order='date'.
RECENCY_RE = re.compile(r'\b(news|latest|recent|recently|today|tonight|breaking|update|updates|current|new)\b')

# Words that describe the medium, not the subject. "fifa news video" should search
# YouTube for "fifa news", not for the literal word "video".
VIDEO_FILLER = {
    "video", "videos", "yt", "youtube", "clip", "clips", "watch", "pull", "up", "show",
    "me", "a", "an", "the", "of", "some", "play", "find", "get", "please", "for", "on",
    "give", "want", "see", "put", "add", "open", "stream", "livestream",
}


def clean_video_query(text: str) -> str:
    """Strip medium words so the search hits the subject.
    "pull up a fifa news video" → "fifa news"."""
    cleaned = re.sub(r'[^\w\s]', ' ', (text or '').lower())
    kept = [w for w in cleaned.split() if w not in VIDEO_FILLER]
    return " ".join(kept).strip() or (text or "").strip()


# Sports fixtures/scores. Without a tool these fell to the agent, which
# web-searched (20-60s) and tried to squeeze a scoreboard into a text card.
SCORE_ASK_RE = re.compile(
    r'\b(scores?|fixtures?|results?|standings?|schedule|matchups?|'
    r'who\'?s playing|whos playing|playing today|next (game|match|fight)|'
    r'card|bouts?|fights?)\b')
DATA_ASK_RE = re.compile(r'\b(news|headlines|weather|forecast|stock|price|chart|graph|image|images|picture|pictures|photo|photos)\b')

# "cnn live news" is a thing to WATCH, but it contains "news", so DATA_ASK_RE
# claimed it and the model was told to build a data_card — a text list of
# headlines, when what was asked for was a stream. Live intent has to outrank the
# data classification, and it needs YouTube's LIVE filter to land on an actual
# stream: a plain search for "cnn live news" returns recorded clips, the filtered
# one returns "CNN Headlines: 24/7 Live News".
LIVE_ASK_RE = re.compile(r'\b(live|livestream|live ?stream|streaming)\b')

# ── Widget intent routing ───────────────────────────────────────────────────
#
# A widget ask has two halves: WHICH widget, and WHAT CONTENT goes inside it.
# The old router only read the first half — it substring-matched a widget noun
# and spawned an empty shell. Two failures fell out of that:
#
#   "notes for grocery list for chicken soup" contains "notes", so it spawned a
#   blank notepad and stopped — the grocery list, which is the entire point of
#   the request, was never produced.
#
#   "give me a grocery list" matched nothing, so it fell through to the full
#   agentic loop: ~60s of tool-calling for content no tool can supply.
#
# So: decide the widget AND whether it needs content. List intent outranks notes
# intent ("notes for a grocery list" is a list, not a notepad). Content the model
# can simply write is filled by ONE direct completion (~2s) instead of the agent.
LIST_INTENT_RE = re.compile(
    r'\b(grocery|groceries|shopping|packing|checklist|to-?dos?|task list|'
    r'bucket list|reading list|watch ?list|wish ?list|ingredients?)\b|\blists?\b')
NOTES_INTENT_RE = re.compile(r'\b(notes?|notepad|scratch ?pad|memo|jot)\b')

# Strip the words that name the widget or frame the request; whatever survives is
# the subject. "notes for grocery list for chicken soup" → "chicken soup".
TOPIC_STOPWORDS = MUSIC_FILLER_WORDS | {
    "note", "notes", "notepad", "scratchpad", "scratch", "pad", "memo", "jot", "down",
    "list", "lists", "checklist", "todo", "todos", "task", "tasks", "item", "items",
    "grocery", "groceries", "shopping", "packing", "ingredient", "ingredients",
    "widget", "new", "my", "make", "build", "and", "it", "about", "write", "keep",
    # Adjectives that describe the widget, not its subject — "quick notes" is a
    # blank notepad, not a note about the topic "quick".
    "quick", "simple", "blank", "empty", "little", "small", "basic", "plain", "just",
}


def extract_topic(text: str) -> str:
    """The subject of the ask, with widget/filler words removed. Empty means the
    user wants a blank widget ("notes") rather than a filled one ("notes on X")."""
    cleaned = re.sub(r'[^\w\s]', ' ', (text or '').lower())
    return " ".join(w for w in cleaned.split() if w not in TOPIC_STOPWORDS).strip()


_fast_model = {"name": None}


async def fast_llm_json(instruction: str, max_tokens: int = 400) -> Optional[dict]:
    """One tool-free completion against the Spark, parsed as JSON.

    A grocery list needs no tools — the model just knows it. Routing that through
    the agentic loop costs ~60s of reasoning and tool-call churn; a direct
    completion answers in ~2s. Returns None on any failure so the caller can
    still spawn an empty widget rather than erroring.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            model = _fast_model["name"]
            if not model:
                resp = await client.get(f"{VLLM_URL}/v1/models")
                model = resp.json()["data"][0]["id"]
                _fast_model["name"] = model
            resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json={
                "model": model,
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": instruction}],
            })
            text = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r'\{.*\}', text, re.DOTALL)  # tolerate ``` fences / stray prose
        return json.loads(match.group(0)) if match else None
    except Exception as e:
        logger.warning(f"fast_llm_json failed: {e}")
        return None


async def build_list_config(message: str) -> dict:
    """Checklist config with real items, written by one direct completion."""
    data = await fast_llm_json(
        'Return ONLY a JSON object, no prose and no markdown fence:\n'
        '{"title": "<short title, max 4 words>", "items": ["<item>", ...]}\n'
        f'The user asked: "{message}"\n'
        'Produce the concrete list they want: 5-12 short, specific items. '
        'For a grocery list use buyable ingredients, for a to-do list use actionable tasks.'
    )
    if not data or not isinstance(data.get("items"), list):
        return {"title": "Checklist", "items": []}
    return {
        "title": str(data.get("title") or "Checklist")[:60],
        "items": [str(i)[:120] for i in data["items"] if str(i).strip()][:14],
    }


async def build_notes_config(message: str) -> dict:
    """Notes config with written content, for "notes about X" (not a bare notepad)."""
    data = await fast_llm_json(
        'Return ONLY a JSON object, no prose and no markdown fence:\n'
        '{"title": "<short title, max 4 words>", "content": "<the note body>"}\n'
        f'The user asked: "{message}"\n'
        'Write a concise, useful note (plain text, max ~120 words).'
    )
    if not data or not data.get("content"):
        return {}
    return {
        "title": str(data.get("title") or "Notes")[:60],
        "content": str(data["content"])[:2000],
    }

def is_valid_tool_args(tool_name: str, args: dict) -> bool:
    if not args:
        return False
    if tool_name == "mcp__lazy-tool-service__canvas_add_widget":
        return bool(args.get("widget_type"))
    if tool_name == "mcp__lazy-tool-service__canvas_modify_dom":
        return bool(args.get("css_selector") and args.get("action"))
    if tool_name == "mcp__lazy-tool-service__create_widget":
        return bool(args.get("widgetType") and args.get("htmlContent"))
    if tool_name == "mcp__lazy-tool-service__update_widget":
        return bool(args.get("widgetId"))
    return False

@app.post("/session/message")
async def send_message(req: MessageRequest):
    try:
        # Save user message
        user_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        database.save_chat_message(
            message_id=user_msg_id,
            session_id=req.session_id,
            role="user",
            content=req.message
        )

        
        text_lower = req.message.lower().strip()
        text_clean = text_lower.strip()

        def spawn_widget_stream(widget_type: str, id_prefix: str, config: dict = None,
                                config_builder=None, status: str = None):
            """Heuristic fast-path: append a prebuilt widget to the CURRENT canvas
            and stream back the full canvas — same contract as the agent path, so
            existing widgets always survive.

            `config_builder` is an async callable for widgets whose content has to
            be written first (a grocery list's items). It runs inside the stream so
            the user sees a status line while it works, and it still skips the
            agentic loop entirely.
            """
            async def stream():
                message = status or f"heuristic-path: spawning {widget_type} widget..."
                yield f'data: {json.dumps({"type": "status", "message": message})}\n\n'

                widget_config = dict(config or {})
                if config_builder:
                    built = await config_builder()
                    if built:
                        widget_config.update(built)

                widget_id = f"{id_prefix}-{uuid.uuid4().hex[:8]}"
                html_snippet = generate_widget_html(widget_type, widget_id, widget_config)

                def _append(soup):
                    target = soup.select_one('#dashboard-grid')
                    if target is None:
                        grid = BeautifulSoup(
                            '<div id="dashboard-grid" class="dashboard-grid"></div>', 'html.parser')
                        soup.append(grid)
                        target = soup.select_one('#dashboard-grid')
                    target.append(BeautifulSoup(html_snippet, 'html.parser'))

                event = await commit_canvas(req.session_id, _append)
                if event:
                    database.save_chat_message(
                        message_id=f"msg_{uuid.uuid4().hex[:8]}",
                        session_id=req.session_id,
                        role="assistant",
                        content=f"\n\n<!--CANVAS_HTML_START-->\n{get_session_canvas(req.session_id)}\n<!--CANVAS_HTML_END-->"
                    )
                    yield event
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream),
                media_type="text/event-stream",
            )

        # Removal/modification intents must reach the agent (canvas_modify_dom) —
        # never spawn a widget off keywords like "remove the clock".
        wants_removal = bool(re.search(
            r'\b(remove|delete|close|hide|clear|dismiss|drop|kill|stop)\b|get rid of', text_clean))

        # Media/data intents override widget-name keywords: "clock for video",
        # "video of a clock" or "news about checklists" must reach the agent,
        # not spawn a clock/checklist off a substring match.
        is_video_ask = bool(VIDEO_ASK_RE.search(text_clean))
        is_data_ask = bool(DATA_ASK_RE.search(text_clean))

        wants_music = bool(re.search(r'\b(music|radio|song|songs|playlist)\b', text_clean))
        league = resolve_league(text_clean)

        # 1. SPORTS SCORES — "fifa scores", "ufc card", "who's playing in the nba".
        #    A scoreboard is structured data, not prose: a text card of headlines
        #    cannot show you who is playing whom.
        if (league and not wants_removal and not is_video_ask
                and (SCORE_ASK_RE.search(text_clean) or text_clean in SPORTS_LEAGUES)):
            # Resolved before returning so an off-season/empty league falls through
            # to the agent instead of spawning an empty scoreboard.
            board = await sports_scores(league)
            if not board.get("is_error"):
                return spawn_widget_stream("scoreboard", "scores", board,
                                           status=f"pulling {board.get('title', 'scores')}...")

        # 2. NEWS VIDEO — "fifa news video". Searched by relevance this returns the
        #    most-watched clip for those words (a years-old recap); news means the
        #    NEWEST upload, so sort by date and drop the medium words from the query.
        if (is_video_ask and RECENCY_RE.search(text_clean)
                and not wants_removal and not LIVE_ASK_RE.search(text_clean)):
            query = clean_video_query(req.message)
            hits = await search_youtube_videos(query, limit=6, order="date")
            if not hits:
                hits = await search_youtube_videos(query, limit=6)
            if hits:
                top = hits[0]
                return spawn_widget_stream("youtube_player", "news-video", {
                    "video_id": top["video_id"],
                    "title": top.get("title") or query,
                    "query": query,
                    "candidates": [v["video_id"] for v in hits[1:] if v.get("video_id")],
                }, status=f"finding the latest '{query}' video...")

        # 3. LIVE STREAMS — checked before the data guard, because "cnn live news"
        #    contains "news" and was being sent down the data_card path (a text
        #    list of headlines) when the user wanted something to watch. "live
        #    music"/"live radio" still belongs to the music player, not YouTube.
        if LIVE_ASK_RE.search(text_clean) and not wants_removal and not wants_music:
            # Resolved here rather than in a config_builder so that a search miss
            # can still fall through to the agent instead of spawning a dead player.
            live_hits = await search_youtube_videos(req.message, limit=6, order="live")
            if not live_hits:
                live_hits = await search_youtube_videos(req.message, limit=6)
            if live_hits:
                top = live_hits[0]
                return spawn_widget_stream("youtube_player", "live", {
                    "video_id": top["video_id"],
                    "title": top.get("title") or req.message,
                    "query": req.message,
                    # Label-owned/geo-blocked streams refuse to embed; the player
                    # hops through these on an embed error.
                    "candidates": [v["video_id"] for v in live_hits[1:] if v.get("video_id")],
                }, status="finding a live stream...")

        if not wants_removal and not is_video_ask and not is_data_ask:
            is_searching = bool(re.search(r'\b(search|find|look for)\b', text_clean))
            topic = extract_topic(req.message)

            # 2. Clock (word boundary: "clockwork" must not match)
            is_clock = bool(re.search(r'\bclock\b', text_clean))
            has_timezone = any(tz in text_clean for tz in ("in ", "for ", "time ", "zone", "city", "york", "london", "tokyo", "paris", "sydney", "canada"))
            if is_clock and not has_timezone:
                return spawn_widget_stream("clock", "clock", {})

            # 3. Music
            has_custom_url = "http" in text_clean or "www" in text_clean
            if re.search(r'\b(music|player|radio)\b', text_clean) and not has_custom_url:
                genre = extract_music_genre(req.message) or "lofi"
                wants_playback = bool(re.search(r'\bplay(ing)?\b', text_clean))
                return spawn_widget_stream("mini_music_player", "music",
                                           {"genre": genre, "autoplay": wants_playback})

            # 4. LISTS — checked BEFORE notes, so "notes for a grocery list" is a
            #    list, not a blank notepad. A list always gets real items written
            #    into it; an empty checklist is never what anyone asked for.
            if LIST_INTENT_RE.search(text_clean) and not is_searching:
                return spawn_widget_stream(
                    "checklist", "checklist",
                    config_builder=lambda: build_list_config(req.message),
                    status="writing your list...",
                )

            # 5. NOTES — a bare "notes"/"notepad" means a blank surface to type on.
            #    "notes about X" means the note should already say something.
            if NOTES_INTENT_RE.search(text_clean) and not is_searching:
                if not topic:
                    return spawn_widget_stream("notes", "notes", {})
                return spawn_widget_stream(
                    "notes", "notes",
                    config_builder=lambda: build_notes_config(req.message),
                    status="writing your note...",
                )

        # Start loading history

        history = database.get_session_messages(req.session_id)

        # Adopting the client's snapshot is _run_turn's job now (it only does so
        # when no other turn is in flight, so a concurrent turn's stale snapshot
        # can't undo a widget that just landed).
        canvas_summary = get_canvas_summary(
            get_session_canvas(req.session_id) or req.current_canvas)

        # Build system prompt with canvas context
        SYSTEM_PROMPT = (
            "You are an agentic OS assistant that manages a live dashboard canvas.\n"
            "CRITICAL: You are a strict TOOL-ONLY JSON agent. You MUST NEVER output any conversational text, thinking process, or explanations. You MUST START your response immediately with the tool call.\n"
            "If you output any text that is not a tool call, the system will crash.\n\n"
            f"CURRENT CANVAS STATE:\n```markdown\n{canvas_summary}\n```\n\n"
            "STEP 1 — CLASSIFY INTENT. Every request maps to exactly one output shape:\n"
            "- Play/watch a VIDEO (mentions video, youtube, watch, clip — even combined with other nouns like 'clock video') → html_notes_youtube_search then canvas_add_widget(widget_type='youtube_player')\n"
            "- Watch something LIVE ('cnn live news', 'live stream of X', 'watch bbc live') → html_notes_youtube_search(query, order='live') then canvas_add_widget(widget_type='youtube_player'). This is a VIDEO request even though it says 'news' — the user wants a stream to watch, NOT a data_card of headlines. order='live' is required; a plain search returns recorded clips instead of the stream.\n"
            "- SPORTS scores/fixtures/results/standings ('fifa scores', 'ufc card', \"who's playing in the nba\") → mcp__lazy-tool-service__html_notes_sports_scores(league) then canvas_add_widget(widget_type='scoreboard', config=<THE WHOLE TOOL RESULT, verbatim>). league takes a friendly name: fifa, world cup, premier league, champions league, la liga, mls, ufc, mma, nba, nfl, mlb, nhl, college football. NEVER web-search for scores and never render them as a data_card — a scoreboard has to show who is playing whom.\n"
            "- A NEWS video ('fifa news video', 'latest X video') → html_notes_youtube_search(query, order='date') — order='date' is required, because a relevance search returns an old most-watched clip instead of the newest one. Drop words like 'video'/'watch' from the query: search 'fifa news', not 'fifa news video'.\n"
            "- Show DATA (news, recipes, weather, search results, facts, lists) → fetch with data tools, then canvas_add_widget(widget_type='data_card')\n"
            "- Show NUMBERS OVER TIME/CATEGORIES (stock price, trends, comparisons) → fetch data, then canvas_add_widget(widget_type='chart')\n"
            "- Show a PICTURE (what does X look like) → find an image URL, then canvas_add_widget(widget_type='image')\n"
            "- UTILITY widget (clock, checklist, notes, music, embedded app) → canvas_add_widget with that widget_type\n"
            "- REMOVE/CHANGE an existing widget → canvas_modify_dom targeting its ID from CURRENT CANVAS STATE\n\n"
            "STEP 2 — FETCH DATA FIRST (for data_card/chart/image): use the LIVE DATA TOOLS below, extract the real values, then bake them into the widget config. NEVER create a widget that says 'Loading...' and NEVER write JavaScript that fetches data — widget JS does not run. The config you pass IS the final rendered content.\n\n"
            "WIDGET CONTRACTS for canvas_add_widget(widget_type, widget_id, config):\n"
            "- data_card: config={title, subtitle?, icon?, image?, items:[{title, description?, image?, url?, badge?, meta?}]} — one item per headline/recipe/result. Include an item image URL whenever the source data has one. Use config.content (plain text) instead of items for prose answers.\n"
            "- chart: config={title, type:'line'|'bar'|'pie', labels:[...], values:[...]} — for generic numbers ONLY, never for a stock ticker\n"
            "- stock_card: config = the entire result of html_notes_stock_history, passed through unchanged\n"
            "- scoreboard: config = the entire result of html_notes_sports_scores, passed through unchanged\n"
            "- image: config={title, url, caption?} or {title, images:[{url, caption?}]}\n"
            "- youtube_player: config={video_id, title} — video_id MUST come from html_notes_youtube_search results\n"
            "- clock: config={timezone}. checklist: config={title, items:[str]}. notes: config={title, content}. iframe_app: config={url, title}. mini_music_player: config={genre, autoplay} e.g. {\"genre\": \"reggae\", \"autoplay\": true}\n"
            "Always provide a unique widget_id (e.g. 'news-abc12').\n\n"
            "CANVAS TOOLS:\n"
            "- Spawn a pre-built widget → mcp__lazy-tool-service__canvas_add_widget(widget_type, widget_id, config) — PREFERRED for everything\n"
            "- Inspect what's on screen → mcp__lazy-tool-service__canvas_read_dom()\n"
            "- Modify/remove an existing widget → mcp__lazy-tool-service__canvas_modify_dom(css_selector='#<widget-id>', action='replace' or 'remove')\n"
            "- Search YouTube → mcp__lazy-tool-service__html_notes_youtube_search(query, limit, order) — pass order='date' for the newest uploads from a channel\n"
            "- Notes → mcp__lazy-tool-service__html_notes_search_notes(query), html_notes_get_note(note_id), html_notes_update_note()\n"
            "- Custom one-off widgets (ONLY when no pre-built type fits) → plan_widget then create_widget(widgetType, title, htmlContent, cssContent, jsContent). All data must be baked into htmlContent — jsContent may only do cosmetic DOM work, never fetching.\n\n"
            "LIVE DATA TOOLS (these are the ONLY data tools that exist — never call any other):\n"
            "- Stock/crypto → mcp__lazy-tool-service__html_notes_stock_history(symbol, range) — range is 1d/5d/1mo/3mo/6mo/1y/5y/10y/max. Returns the FULL snapshot: {symbol, name, price, currency, change_pct, labels, values, technicals, fundamentals}. Answers in under a second.\n"
            "  Then call canvas_add_widget(widget_type='stock_card', config=<THE WHOLE TOOL RESULT, verbatim>). Pass the entire object through — do not pick out fields, do not rebuild it, and do NOT use widget_type='chart' for a ticker. The stock_card renders the chart, the technicals and the fundamentals, and its own range tabs re-fetch without you.\n"
            "  For ANY ticker, stock, share price or 'chart X' request use this — NEVER web-search for prices, it is slow and returns no usable numbers.\n"
            "- Web search → mcp__lazy-tool-service__html_notes_web_search(query, limit) — returns [{title, url, snippet}]. Use it for ANY question about facts, history, news, recipes, weather, or 'what/when/who/which is...'. It takes 20-60s; that is expected, so wait for it.\n"
            "- Read a page → mcp__lazy-tool-service__html_notes_read_page(url) — full text of a URL from a search result, when the snippet is not enough.\n\n"
            "RESEARCH RULE — THIS IS MANDATORY:\n"
            "If the user asks a question you cannot answer from the conversation itself, you MUST call html_notes_web_search BEFORE answering. Never reply that you cannot find, cannot access, or do not have information about something without having called html_notes_web_search at least once. Never answer a factual question from memory alone — search, then bake the real findings into a data_card.\n"
            "A question about the oldest/first/earliest video, song, or event is a SEARCH question: search the web for the answer, then (if it names a video) call html_notes_youtube_search for that specific title and show a youtube_player. Do not claim YouTube cannot be sorted by age — find the answer with the web search instead.\n\n"
            "RULES:\n"
            "1. WIDGETS COEXIST: The grid holds many widgets at once. Adding a widget NEVER removes or replaces the others — do not touch existing widgets unless the user explicitly asks to change or remove them.\n"
            "2. REMOVING: find the widget's ID in CURRENT CANVAS STATE and call canvas_modify_dom(css_selector='#<widget-id>', action='remove'). Do not add anything when removing.\n"
            "3. UPDATING ('update that news widget', 'add more detail'): target the specific widget's ID from CURRENT CANVAS STATE — replace it via canvas_add_widget with the same widget_id family or canvas_modify_dom action='replace'.\n"
            "4. VAGUE VIDEO REQUESTS: 'pull up a video' with no topic → pick a random fun search query and execute html_notes_youtube_search immediately. Do NOT ask for clarification.\n"
            "5. DO NOT explain your plan. Tool calls only.\n"
            "6. STOP WHEN THE WIDGET IS UP. canvas_add_widget returning success means it is ALREADY on the user's screen. Do not call it a second time for the same widget, do not verify it with canvas_read_dom, and do not re-plan. Finish with ONE short sentence (max 20 words) saying what you added — then stop. Every extra thought after the widget renders is time the user spends staring at a spinner for something already done."
        )

        # Ensure all possible tools are enabled
        enabled_tools = [
            "mcp__lazy-tool-service__html_notes_create_note",
            "mcp__lazy-tool-service__html_notes_update_note",
            "mcp__lazy-tool-service__html_notes_get_note",
            "mcp__lazy-tool-service__html_notes_search_notes",
            "mcp__lazy-tool-service__html_notes_link_notes",
            "mcp__lazy-tool-service__canvas_read_dom",
            "mcp__lazy-tool-service__canvas_add_widget",
            "mcp__lazy-tool-service__canvas_modify_dom",
            "mcp__lazy-tool-service__html_notes_youtube_search",
            "mcp__lazy-tool-service__create_widget",
            "mcp__lazy-tool-service__update_widget",
            "mcp__lazy-tool-service__validate_widget_html",
            "mcp__lazy-tool-service__list_widget_types",
            "mcp__lazy-tool-service__plan_widget",
            # Web research. These are served by HTML-Notes itself (via
            # scraper-service) because every tools-api data tool — search_web,
            # search_news, read_web_page, get_weather, get_time_in_timezone —
            # is registered in the gateway catalog with a null endpoint and its
            # python bridge has no interpreter in the image, so they all return
            # "Unknown tool". Enabling them just gave the model phantom tools to
            # fail against, which is why it kept answering "I couldn't get one".
            "mcp__lazy-tool-service__html_notes_web_search",
            "mcp__lazy-tool-service__html_notes_read_page",
            "mcp__lazy-tool-service__html_notes_stock_history",
            "mcp__lazy-tool-service__html_notes_sports_scores",
        ]

        # Build messages array — use system role at index 0.
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

        # Only include recent history to avoid context overflow (last 10 messages)
        recent_history = history[-3:]
        for h in recent_history:
            content = h["content"]

            # Compress large HTML chunks in history
            if h["role"] == "assistant":
                # Strip out the new wrapped HTML
                content = re.sub(r'<!--CANVAS_HTML_START-->.*?<!--CANVAS_HTML_END-->', '[Visual Component Rendered]', content, flags=re.DOTALL)
                # Fallback for old history: strip common classes
                content = re.sub(r'<div class="[^"]*(glass-card|canvas-element|rendered-component)[^"]*">.*?</div>', '[Component]', content, flags=re.DOTALL)
                
                # Truncate very long assistant messages just in case
                if len(content) > 2000:
                    content = content[:2000] + "... [truncated]"

            # Skip tool-only placeholder messages
            if content == "[tool-only turn]":
                continue

            messages.append({"role": h["role"], "content": content})

        # The lazy-tool-service gateway runs the agentic loop and executes the
        # mcp__lazy-tool-service__* widget tools; plain Prism has no such
        # catalog registered, so LazyAgent is the default.
        target_url = LAZY_AGENT_URL if req.use_lazy_agent else PRISM_URL

        # Build /agent payload — NO tools array (the gateway uses its own catalog)
        model_name = req.model
        if not model_name:
            # Discover a provider/model pair from the gateway's local catalog so
            # the provider instance and model always match (e.g. "vllm" serves a
            # different model than "vllm-2").
            try:
                # /config-local probes each vLLM instance and takes ~3s itself,
                # so the client timeout must sit well above that.
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(f"{target_url}/config-local")
                    if resp.status_code == 200:
                        local_models = resp.json().get("models", {})
                        for provider_id, provider_models in local_models.items():
                            for m in provider_models:
                                if m.get("modelType") == "conversation" and "Tool Calling" in (m.get("tools") or []):
                                    req.provider = provider_id
                                    model_name = m.get("name")
                                    break
                            if model_name:
                                break
            except Exception as e:
                logger.warning(f"Failed to fetch model catalog from {target_url}: {e}")
        if not model_name:
            # VLLM_URL is Gold Spark (10.0.0.141), registered as provider
            # "vllm-2" in the gateway — the pair must match or the gateway
            # 404s with "model does not exist" on the wrong instance.
            try:
                with httpx.Client(timeout=2.0) as client:
                    resp = client.get(f"{VLLM_URL}/v1/models")
                    if resp.status_code == 200:
                        models_data = resp.json().get("data", [])
                        if models_data:
                            model_name = models_data[0].get("id")
                            req.provider = "vllm-2"
            except Exception as e:
                logger.warning(f"Failed to fetch dynamic model from {VLLM_URL}: {e}")
            if not model_name:
                # Last resort: the Jetson instance/model pair.
                req.provider = "vllm"
                model_name = "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"
        # Sync the gateway's settings dynamically so MemoryExtractor doesn't crash on outdated models
        try:
            with httpx.Client(timeout=1.0) as client:
                client.put(f"{target_url}/settings", json={
                    "memory": {
                        "extractionModel": model_name
                    }
                })
        except Exception as e:
            logger.warning(f"Failed to sync memory extraction model to gateway: {e}")

        payload = {
            "provider": req.provider,
            "model": model_name,
            "workspaceRoot": "/home/lazycat/github/projects/sun/HTML-Notes",
            "workspaceEnabled": False,
            "enabledTools": enabled_tools,
            "messages": messages,
            "maxTokens": 4096,
            # Neither of these was set, so the gateway used its defaults: temperature
            # 0.7 and 25 iterations. A canvas task is not a creative one — it is
            # "pick the right tool, fill in the args" — and a warm model deliberates
            # its way there, then keeps narrating afterwards. Measured: the first
            # tool call landed at ~8s but the turn ran 70s, with 91-189 reasoning
            # events, most of them AFTER the widget was already on screen.
            "temperature": 0.15,
            "maxIterations": 6,
            "project": "html-notes-client",
            "username": "lazycat",
            "skipConversation": True,
            "autoApprove": True,
            "memoryEnabled": False
        }

        async def proxy_prism_sse():
            """
            Stream Prism's /agent SSE events to the frontend.
            Prism handles the full agentic loop (tool calls, execution, re-prompting).
            We just proxy events and extract render_component results for the canvas.
            """
            final_text = ""
            # Only a mirror of the last commit, for the DB write at the end of the
            # turn. It is never used as the base for a mutation — commit_canvas
            # re-reads the live canvas under the lock, so a sibling turn's widget
            # can't be lost.
            all_rendered_html = get_session_canvas(req.session_id) or req.current_canvas or ""
            executed_active_tool = False
            executed_mutations: set = set()

            async def execute_mutation(tool_name, tool_args):
                nonlocal all_rendered_html
                # The model routinely re-emits the same canvas_add_widget a second
                # time after it has already succeeded. executed_active_tool resets
                # whenever a new tool starts, so the repeat used to run again —
                # rebuilding the same widget and paying another full iteration
                # (~13k-token prefill at a 0% KV-cache hit) for nothing.
                signature = json.dumps({"t": tool_name, "a": tool_args}, sort_keys=True, default=str)
                if signature in executed_mutations:
                    logger.info(f"[WIDGET INJECTOR] Skipping duplicate {tool_name}")
                    return
                executed_mutations.add(signature)

                logger.info(f"[WIDGET INJECTOR] Executing mutation for {tool_name} with args: {tool_args}")
                yield f'data: {json.dumps({"type": "status", "message": f"executing {tool_name}..."})}\n\n'

                async def emit(mutate):
                    """Commit against the LIVE canvas, not this turn's snapshot —
                    a sibling turn may have added a widget since we started."""
                    nonlocal all_rendered_html
                    event = await commit_canvas(req.session_id, mutate)
                    if event:
                        all_rendered_html = get_session_canvas(req.session_id)
                    return event

                try:
                    if tool_name == "mcp__lazy-tool-service__canvas_modify_dom":
                        css_selector = tool_args.get("css_selector", "")
                        action = tool_args.get("action", "")
                        html_snippet = tool_args.get("html_snippet", "")

                        def _modify(soup):
                            target = soup.select_one(css_selector)
                            if not target:
                                return False
                            if action == "append":
                                target.append(BeautifulSoup(html_snippet, 'html.parser'))
                            elif action == "replace":
                                target.replace_with(BeautifulSoup(html_snippet, 'html.parser'))
                            elif action == "remove":
                                target.decompose()

                        event = await emit(_modify)
                        if event:
                            yield event

                        logger.info("[FAST LOOP] Terminating early after canvas_modify_dom to save latency")

                    elif tool_name == "mcp__lazy-tool-service__canvas_add_widget":
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {}

                        widget_type = tool_args.get("widget_type", "")
                        widget_id = tool_args.get("widget_id", f"widget-{uuid.uuid4().hex[:8]}")
                        config = tool_args.get("config", {})
                        # Some models pass config as a JSON string — normalize.
                        if isinstance(config, str):
                            try:
                                config = json.loads(config)
                            except Exception:
                                config = {}

                        # Bake alternate video ids into youtube players so the
                        # widget can hop to an embeddable video when the first
                        # one blocks embedding ("Video unavailable").
                        if widget_type == "youtube_player" and not config.get("candidates"):
                            search_q = config.get("query") or config.get("title") or ""
                            if search_q:
                                try:
                                    alts = await search_youtube_videos(search_q, limit=6)
                                    primary = config.get("video_id", "")
                                    config["candidates"] = [v["video_id"] for v in alts
                                                            if v.get("video_id") and v["video_id"] != primary]
                                    config.setdefault("query", search_q)
                                except Exception as se:
                                    logger.warning(f"candidate enrichment failed: {se}")
                        
                        def _add(soup):
                            replaced = False
                            if widget_type == "youtube_player":
                                # Swap only an existing YOUTUBE widget in place ("play X
                                # instead"). Matching on any iframe replaced unrelated
                                # widgets (music player, iframe apps) — keep it strict.
                                for div in soup.find_all("div", class_="widget-container"):
                                    xdata = div.get("x-data", "")
                                    div_id = div.get("id", "")
                                    if "youtubePlayerWidget" in xdata or "youtube" in div_id.lower():
                                        existing_id = div.get("id", widget_id)
                                        div.replace_with(BeautifulSoup(
                                            generate_widget_html(widget_type, existing_id, config), 'html.parser'))
                                        replaced = True
                                        break

                            # A retried tool call with the same widget_id must not
                            # duplicate the widget — replace it in place instead.
                            if not replaced:
                                existing = soup.find(id=widget_id)
                                if existing is not None:
                                    existing.replace_with(BeautifulSoup(
                                        generate_widget_html(widget_type, widget_id, config), 'html.parser'))
                                    replaced = True

                            if replaced:
                                logger.info("[WIDGET INJECTOR] Replaced existing widget in-place")
                                return

                            snippet = BeautifulSoup(
                                generate_widget_html(widget_type, widget_id, config), 'html.parser')
                            target = soup.select_one('#dashboard-grid')
                            if target:
                                target.append(snippet)
                            else:
                                soup.append(snippet)
                            logger.info(f"[WIDGET INJECTOR] Appended new {widget_type} widget")

                        event = await emit(_add)
                        if event:
                            yield event

                        logger.info("[FAST LOOP] Terminating early after canvas_add_widget to save latency")
                    elif tool_name == "mcp__lazy-tool-service__create_widget":
                        widget_type = tool_args.get("widgetType", "custom")
                        title = tool_args.get("title", "Widget")
                        html_content = tool_args.get("htmlContent", "")
                        css_content = tool_args.get("cssContent", "")
                        js_content = tool_args.get("jsContent", "")
                        
                        # Generate widget ID
                        widget_id = f"widget-{uuid.uuid4().hex[:8]}"
                        
                        # Scope CSS
                        scoped_css = ""
                        if css_content:
                            rules = []
                            for rule in css_content.split("}"):
                                if "{" in rule:
                                    sel, body = rule.split("{", 1)
                                    sel = sel.strip()
                                    if sel and not sel.startswith("@"):
                                        scoped_sel = ", ".join([f"#{widget_id} {s.strip()}" for s in sel.split(",")])
                                        rules.append(f"{scoped_sel} {{{body}}}")
                                    else:
                                        rules.append(rule + "}")
                            scoped_css = "\n".join(rules)
                            
                        # Wrap content
                        html_snippet = f"""
<div id="{widget_id}" class="glass-card canvas-widget" data-widget-type="{widget_type}">
    <div class="glass-card-title">{title}</div>
    <style>{scoped_css}</style>
    <div class="widget-body">{html_content}</div>
    <script>
    (function() {{
        const container = document.getElementById('{widget_id}');
        {js_content}
    }})();
    </script>
</div>
"""
                        def _create(soup):
                            snippet = BeautifulSoup(html_snippet, 'html.parser')
                            target = soup.select_one('#dashboard-grid')
                            if target:
                                target.append(snippet)
                            else:
                                soup.append(snippet)

                        event = await emit(_create)
                        if event:
                            yield event
                        logger.info(f"[WIDGET INJECTOR] Created and appended new {widget_type} widget")
                        logger.info("[FAST LOOP] Terminating early after create_widget to save latency")
                    elif tool_name == "mcp__lazy-tool-service__update_widget":
                        widget_id = tool_args.get("widgetId")
                        title = tool_args.get("title")
                        html_content = tool_args.get("htmlContent")
                        css_content = tool_args.get("cssContent")
                        js_content = tool_args.get("jsContent")
                        
                        def _update(soup):
                            widget_div = soup.find(id=widget_id)
                            if not widget_div:
                                return False
                            if title is not None:
                                title_el = widget_div.select_one(".glass-card-title")
                                if title_el:
                                    title_el.string = title
                            if html_content is not None:
                                body_el = widget_div.select_one(".widget-body")
                                if body_el:
                                    body_el.clear()
                                    body_el.append(BeautifulSoup(html_content, 'html.parser'))
                            if css_content is not None:
                                style_el = widget_div.select_one("style")
                                if style_el:
                                    rules = []
                                    for rule in css_content.split("}"):
                                        if "{" in rule:
                                            sel, body = rule.split("{", 1)
                                            sel = sel.strip()
                                            if sel and not sel.startswith("@"):
                                                scoped_sel = ", ".join([f"#{widget_id} {s.strip()}" for s in sel.split(",")])
                                                rules.append(f"{scoped_sel} {{{body}}}")
                                            else:
                                                rules.append(rule + "}")
                                    style_el.string = "\n".join(rules)
                            if js_content is not None:
                                script_el = widget_div.select_one("script")
                                if script_el:
                                    script_el.string = f"""
                                    (function() {{
                                        const container = document.getElementById('{widget_id}');
                                        {js_content}
                                    }})();
                                    """

                        event = await emit(_update)
                        if event:
                            yield event
                            logger.info(f"[WIDGET INJECTOR] Updated widget {widget_id} in-place")
                        logger.info("[FAST LOOP] Terminating early after update_widget to save latency")
                except Exception as ex:
                    logger.error(f"Failed to execute canvas mutation: {ex}")

            try:
                yield f'data: {json.dumps({"type": "status", "message": "connecting to agent..."})}\n\n'

                async with httpx.AsyncClient(timeout=600.0) as client:
                    async with client.stream(
                        "POST",
                        f"{target_url}/agent",
                        json=payload,
                        headers={"Accept": "text/event-stream"}
                    ) as resp:
                        if resp.status_code != 200:
                            error_body = ""
                            async for chunk in resp.aiter_text():
                                error_body += chunk
                            yield f'data: {json.dumps({"type": "error", "message": f"Prism error {resp.status_code}: {error_body[:500]}"})}\n\n'
                            return

                        buffer = ""
                        active_tool_name = None
                        active_tool_args = {}

                        async for chunk in resp.aiter_text():
                            buffer += chunk
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()

                                if not line.startswith("data: "):
                                    continue

                                try:
                                    event = json.loads(line[6:])
                                except json.JSONDecodeError:
                                    continue

                                event_type = event.get("type", "")
                                logger.info(f"[SSE_PROXY] Received event_type: '{event_type}'")

                                if event_type in ("chunk", "done") and active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget"):
                                    if not executed_active_tool and is_valid_tool_args(active_tool_name, active_tool_args):
                                        async for evt in execute_mutation(active_tool_name, active_tool_args):
                                            yield evt
                                        executed_active_tool = True
                                        active_tool_name = None
                                        active_tool_args = {}

                                if event_type == "chunk":
                                    # Text token from LLM
                                    token = event.get("content", "")
                                    final_text += token
                                    yield f'data: {json.dumps({"type": "chunk", "content": token})}\n\n'

                                elif event_type == "tool_execution":
                                    status = event.get("status", "")
                                    tool_info = event.get("tool", {})
                                    tool_name = tool_info.get("name", "unknown")
                                    args = tool_info.get("args", {})
                                    
                                    if active_tool_name != tool_name:
                                        active_tool_name = tool_name
                                        active_tool_args = {}
                                        executed_active_tool = False
                                        yield f'data: {json.dumps({"type": "tool_call", "tool": tool_name})}\n\n'
                                        yield f'data: {json.dumps({"type": "status", "message": f"preparing {tool_name}..."})}\n\n'
                                    
                                    active_tool_args = args

                                    # FAST PATH: Execute immediately when arguments are available!
                                    if active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget", "mcp__lazy-tool-service__create_widget", "mcp__lazy-tool-service__update_widget"):
                                        if not executed_active_tool and is_valid_tool_args(active_tool_name, active_tool_args) and status in ("calling", "done", "success"):
                                            async for evt in execute_mutation(active_tool_name, active_tool_args):
                                                yield evt
                                            executed_active_tool = True
                                            active_tool_name = None
                                            active_tool_args = {}
                                        elif status in ("calling", "done", "success", "error"):
                                            active_tool_name = None
                                            active_tool_args = {}
                                    elif status == "error":
                                        error_msg = event.get("result", "Unknown tool error")
                                        yield f'data: {json.dumps({"type": "status", "message": f"tool error: {tool_name}: {str(error_msg)[:200]}"})}\n\n'

                                elif event_type == "thinking":
                                    yield f'data: {json.dumps({"type": "status", "message": "reasoning..."})}\n\n'

                                elif event_type == "done":
                                    # Prism finished the full agentic loop
                                    pass

                                elif event_type == "error":
                                    yield f'data: {json.dumps({"type": "error", "message": event.get("message", "Agent error")})}\n\n'

            except Exception as e:
                logger.error(f"Prism SSE proxy error: {e}")
                yield f'data: {json.dumps({"type": "error", "message": f"Connection error: {str(e)}"})}\n\n'

            # Save assistant response to DB. Persist the LIVE canvas, not this
            # turn's mirror — a sibling turn may have committed after our last
            # mutation, and writing a stale snapshot here would resurrect the
            # pre-sibling canvas on the next page reload.
            asst_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
            saved_content = final_text
            live_canvas = get_session_canvas(req.session_id) or all_rendered_html
            if live_canvas:
                saved_content += f"\n\n<!--CANVAS_HTML_START-->\n{live_canvas}\n<!--CANVAS_HTML_END-->"

            if not saved_content.strip():
                saved_content = "[tool-only turn]"

            database.save_chat_message(
                message_id=asst_msg_id,
                session_id=req.session_id,
                role="assistant",
                content=saved_content
            )

            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(
            _run_turn(req.session_id, req.current_canvas or "", proxy_prism_sse),
            media_type="text/event-stream",
        )

    except Exception as e:
        logger.error(f"Error processing session message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/notes/create")
async def api_create_note(req: CreateNoteRequest):
    import uuid
    from app.agents.auditor import audit_html_fragment
    
    # Audit before manual creation
    audit_res = audit_html_fragment(req.rendered_html)
    if not audit_res["is_valid"]:
        raise HTTPException(
            status_code=400,
            detail=f"HTML content failed security audit: {', '.join(audit_res['errors'])}"
        )
        
    try:
        note_id = f"note_{uuid.uuid4().hex[:8]}"
        note = database.create_note(
            note_id=note_id,
            title=req.title,
            tags=req.tags,
            links=req.links,
            source_messages=["api-manual-create"],
            canonical_blocks=req.canonical_blocks,
            rendered_html=req.rendered_html
        )
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/notes/update")
async def api_update_note(req: UpdateNoteRequest):
    if req.rendered_html is not None:
        from app.agents.auditor import audit_html_fragment
        audit_res = audit_html_fragment(req.rendered_html)
        if not audit_res["is_valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"HTML content failed security audit: {', '.join(audit_res['errors'])}"
            )
            
    try:
        note = database.update_note(
            note_id=req.note_id,
            title=req.title,
            tags=req.tags,
            links=req.links,
            canonical_blocks=req.canonical_blocks,
            rendered_html=req.rendered_html,
            source_message="api-manual-update"
        )
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/notes/link")
async def api_link_notes(req: LinkNotesRequest):
    try:
        note_a = database.get_note_by_id(req.source_note_id)
        note_b = database.get_note_by_id(req.target_note_id)
        if not note_a or not note_b:
            raise HTTPException(status_code=404, detail="One or both notes not found")
            
        links = note_a.get("links", [])
        if req.target_note_id not in links:
            links.append(req.target_note_id)
            database.update_note(note_id=req.source_note_id, links=links)
            
        return {"status": "success", "detail": f"Linked {req.source_note_id} to {req.target_note_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/notes/{id}")
async def get_note(id: str):
    note = database.get_note_by_id(id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    history = database.get_note_history(id)
    return {"note": note, "history": history}

@app.get("/api/stock/{symbol}")
async def api_stock(symbol: str, range: str = "1mo"):
    """Backs the stock widget's range tabs — switching 1D/1M/1Y/10Y/MAX refetches
    here instead of going through the agent again."""
    return await stock_snapshot(symbol, range)


@app.get("/api/youtube/candidates")
async def api_youtube_candidates(query: str, limit: int = 6):
    """Multi-result YouTube search used by the player widget to recover from
    embed-blocked videos (it walks the list until one plays)."""
    results = await search_youtube_videos(query, limit=min(limit, 12))
    return {"results": results, "count": len(results)}

@app.get("/api/youtube/search")
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

@app.get("/search")
async def api_search(q: str):
    return database.search_notes(q)

@app.get("/graph")
async def get_graph():
    """
    Returns nodes and edges formatted for visual graph rendering.
    """
    try:
        notes = database.list_all_notes()
        nodes = []
        edges = []
        
        for n in notes:
            nodes.append({
                "data": {
                    "id": n["id"],
                    "label": n["title"],
                    "version": n["version"]
                }
            })
            for link_target in n.get("links", []):
                edges.append({
                    "data": {
                        "id": f"edge_{n['id']}_{link_target}",
                        "source": n["id"],
                        "target": link_target
                    }
                })
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/session/transcribe")
async def transcribe_audio(req: TranscribeRequest):
    """
    Proxies base64 audio transcription to Prism service STT endpoint.
    """
    try:
        url = f"{PRISM_URL}/audio-to-text"
        payload = {
            "provider": "openai",
            "audio": req.audio,
            "skipConversation": True,
            "project": "html-notes",
            "username": "lazycat"
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code != 200:
                logger.error(f"Prism STT failed with code {res.status_code}: {res.text}")
                raise HTTPException(status_code=500, detail=f"Prism transcription failed: {res.text}")
            return res.json()
    except Exception as e:
        logger.error(f"Failed proxying to STT service: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription service unavailable: {str(e)}")

@app.post("/tts/synthesize")
async def tts_synthesize(req: TTSSynthesizeRequest):
    """
    Proxies TTS synthesis request to tts-service.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{TTS_SERVICE_URL}/api/v1/tts/synthesize",
                json={"text": req.text}
            )
            if resp.status_code != 200:
                logger.error(f"TTS service returned status code {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=503, detail="TTS service failure")
            return Response(content=resp.content, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Failed proxying to TTS service: {e}")
        raise HTTPException(status_code=503, detail=f"TTS service unavailable: {str(e)}")

@app.get("/health/model")
async def health_model():
    """
    Pings local vLLM health metrics endpoint.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{VLLM_URL}/health")
            if res.status_code == 200:
                return {"status": "ok", "vllm": "healthy"}
            return {"status": "unhealthy", "code": res.status_code}
    except Exception as e:
        return {"status": "offline", "detail": str(e)}

@app.get("/health/app")
async def health_app():
    return {"status": "ok", "service": "html-notes"}

class InternalToolRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = {}

@app.post("/internal/execute")
async def internal_tool_execute(req: InternalToolRequest):
    """
    Internal tool dispatcher. Called by lazy-tool-service when the model
    fires an html_notes_* or render_component tool call.
    """
    t = req.tool
    a = req.args

    try:
        if t == "html_notes_create_note":
            from app.agents.auditor import audit_html_fragment
            audit = audit_html_fragment(a.get("rendered_html", ""))
            if not audit["is_valid"]:
                return {"error": f"HTML audit failed: {audit['errors']}", "is_error": True}
            note_id = f"note_{uuid.uuid4().hex[:8]}"
            note = database.create_note(
                note_id=note_id,
                title=a["title"],
                tags=a.get("tags", []),
                links=a.get("links", []),
                source_messages=["tool-call"],
                canonical_blocks=[],
                rendered_html=a["rendered_html"]
            )
            return {"success": True, "note_id": note["id"], "title": note["title"]}

        elif t == "html_notes_update_note":
            from app.agents.auditor import audit_html_fragment
            if "rendered_html" in a:
                audit = audit_html_fragment(a["rendered_html"])
                if not audit["is_valid"]:
                    return {"error": f"HTML audit failed: {audit['errors']}", "is_error": True}
            note = database.update_note(note_id=a["note_id"], **{k: v for k, v in a.items() if k != "note_id"})
            return {"success": True, "note_id": a["note_id"]} if note else {"error": "Note not found", "is_error": True}

        elif t == "html_notes_get_note":
            note = database.get_note_by_id(a["note_id"])
            return note if note else {"error": "Note not found", "is_error": True}

        elif t == "html_notes_search_notes":
            results = database.search_notes(a["query"])
            return {"results": results, "count": len(results)}

        elif t == "html_notes_link_notes":
            note_a = database.get_note_by_id(a["source_note_id"])
            if not note_a:
                return {"error": "Source note not found", "is_error": True}
            links = note_a.get("links", [])
            if a["target_note_id"] not in links:
                links.append(a["target_note_id"])
                database.update_note(note_id=a["source_note_id"], links=links)
            return {"success": True}

        elif t == "html_notes_modify_dom":
            # fetch note, apply BeautifulSoup DOM operation, update
            note = database.get_note_by_id(a["note_id"])
            if not note:
                return {"error": "Note not found", "is_error": True}
            soup = BeautifulSoup(note["rendered_html"], "html.parser")
            target = soup.select_one(a["css_selector"])
            if not target:
                return {"error": f"Selector '{a['css_selector']}' not found", "is_error": True}
            snippet_soup = BeautifulSoup(a["html_snippet"], "html.parser")
            action = a["action"]
            if action == "append":      target.append(snippet_soup)
            elif action == "prepend":   target.insert(0, snippet_soup)
            elif action == "insert_before": target.insert_before(snippet_soup)
            elif action == "insert_after":  target.insert_after(snippet_soup)
            elif action == "replace":   target.replace_with(snippet_soup)
            database.update_note(note_id=a["note_id"], rendered_html=str(soup))
            return {"success": True}

        elif t == "render_component":
            from app.templates import TEMPLATES
            ctype = a.get("component_type")
            data = a.get("data", {})
            if ctype in TEMPLATES:
                html = TEMPLATES[ctype](data)
            else:
                html = a.get("rendered_html", "")
            
            return {
                "success": True,
                "rendered_html": html,
                "component_type": ctype,
                "title": a.get("title", "Component")
            }

        elif t == "canvas_read_dom":
            canvas_html = a.get("canvas_html") or get_session_canvas(_last_active_session)
            css_selector = a.get("css_selector")
            
            if not canvas_html or canvas_html.strip() == "":
                return {"elements": [], "element_count": 0, "summary": "Canvas is empty."}
            
            soup = BeautifulSoup(canvas_html, "html.parser")
            
            if css_selector:
                # Return specific element(s)
                matches = soup.select(css_selector)
                if not matches:
                    return {"error": f"No elements matched selector '{css_selector}'", "matched": 0}
                return {
                    "matched": len(matches),
                    "elements": [
                        {
                            "tag": el.name,
                            "classes": el.get("class", []),
                            "text": el.get_text(strip=True)[:300],
                            "html": str(el)[:1000],
                            "children_count": len(list(el.children))
                        }
                        for el in matches[:10]
                    ]
                }
            
            # Full canvas summary
            components = []
            for card in soup.select(".glass-card"):
                title_el = card.select_one(".glass-card-title")
                classes = card.get("class", [])
                comp_type = "unknown"
                for cls in classes:
                    if cls != "glass-card":
                        comp_type = cls
                        break
                components.append({
                    "type": comp_type,
                    "title": title_el.get_text(strip=True) if title_el else "",
                    "text_preview": card.get_text(strip=True)[:200]
                })
            
            all_text = soup.get_text(strip=True)[:500]
            all_tags = [el.name for el in soup.find_all(True)]
            tag_counts = {}
            for tag in all_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            return {
                "element_count": len(all_tags),
                "tag_counts": tag_counts,
                "components": components,
                "component_count": len(components),
                "text_preview": all_text,
                "has_content": bool(all_text.strip())
            }

        elif t == "canvas_modify_dom":
            canvas_html = a.get("canvas_html") or get_session_canvas(_last_active_session)
            css_selector = a.get("css_selector")
            action = a.get("action")
            html_snippet = a.get("html_snippet", "")
            
            if not canvas_html:
                return {"error": "Canvas is empty, nothing to modify", "is_error": True}
            if not css_selector:
                return {"error": "css_selector is required", "is_error": True}
            if not action:
                return {"error": "action is required", "is_error": True}
            
            soup = BeautifulSoup(canvas_html, "html.parser")
            target = soup.select_one(css_selector)
            
            if not target:
                return {"error": f"No element matched selector '{css_selector}'", "is_error": True}
            
            if action == "remove":
                target.decompose()
            elif action in ("append", "prepend", "replace", "insert_before", "insert_after"):
                if not html_snippet:
                    return {"error": f"html_snippet is required for action '{action}'", "is_error": True}
                snippet_soup = BeautifulSoup(html_snippet, "html.parser")
                if action == "append":
                    target.append(snippet_soup)
                elif action == "prepend":
                    target.insert(0, snippet_soup)
                elif action == "replace":
                    target.replace_with(snippet_soup)
                elif action == "insert_before":
                    target.insert_before(snippet_soup)
                elif action == "insert_after":
                    target.insert_after(snippet_soup)
            else:
                return {"error": f"Unknown action: {action}", "is_error": True}
            
            return {
                "success": True,
                "rendered_html": str(soup),
                "action_performed": action,
                "selector": css_selector
            }

        elif t == "html_notes_add_youtube_widget":
            # Handled by the SSE interceptor on the streaming wrapper, but we return success here as well
            return {"success": True, "message": "Successfully added YouTube widget to canvas."}

        elif t == "create_widget":
            return {"success": True, "message": "Widget created successfully."}

        elif t == "update_widget":
            return {"success": True, "message": "Widget updated successfully."}

        elif t == "html_notes_youtube_search":
            query = a.get("query", "")
            limit = int(a.get("limit", 5))
            order = a.get("order", "relevance")
            results = await search_youtube_videos(query, limit=limit, order=order)
            return {"results": results, "count": len(results)}

        elif t == "html_notes_web_search":
            results = await web_search(a.get("query", ""), limit=int(a.get("limit", 6)))
            if not results:
                return {"results": [], "count": 0,
                        "message": "Search returned nothing. Retry with a shorter, simpler query."}
            return {"results": results, "count": len(results)}

        elif t == "html_notes_read_page":
            return await read_web_page(a.get("url", ""), max_chars=int(a.get("max_chars", 6000)))

        elif t == "html_notes_stock_history":
            return await stock_snapshot(a.get("symbol", ""), a.get("range", "1mo"))

        elif t == "html_notes_sports_scores":
            return await sports_scores(a.get("league", ""))

        elif t == "canvas_add_widget":
            # The actual injection to the frontend is handled by the SSE interceptor during 'calling' phase.
            # Here we just acknowledge success to the LLM so it doesn't think the tool failed.
            return {"success": True, "message": f"Successfully added {a.get('widget_type', 'widget')} to canvas."}

        else:
            return {"error": f"Unknown tool: {t}", "is_error": True}

    except Exception as e:
        logger.error(f"Internal tool execution error: {e}")
        return {"error": str(e), "is_error": True}

CANVAS_BLOCK_RE = re.compile(r'<!--CANVAS_HTML_START-->(.*?)<!--CANVAS_HTML_END-->', re.DOTALL)

@app.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    try:
        history = database.get_session_messages(session_id)
        # Persisted assistant messages embed the canvas snapshot; split it into
        # canvas_html so the client shows only text in the chat panel.
        messages = []
        for h in history:
            msg = dict(h)
            content = msg.get("content") or ""
            m = CANVAS_BLOCK_RE.search(content)
            if m:
                msg["canvas_html"] = m.group(1).strip()
                msg["content"] = CANVAS_BLOCK_RE.sub("", content).strip()
            messages.append(msg)
        return {"messages": messages}
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Mount UI static files at root
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    # Redirect base URL to static client UI
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")
