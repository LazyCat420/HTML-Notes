import httpx
import logging
import random
import re
import urllib.parse
import xml.etree.ElementTree as ET
from html import unescape as _html_unescape

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


async def _backfill_snippets(results: list, top_n: int = 3, min_len: int = 80) -> None:
    """Fetch real page text for top results whose SERP snippet came back thin.

    Brave does not render a description for every result, and a result that reaches
    the model as title+url can only be rendered as a naked hyperlink — the one thing
    the user should never have to click through to read. Reading the page is the only
    way to recover actual prose, so pay for it on the few results that need it.
    """
    thin = [r for r in results[:top_n] if len(r["snippet"]) < min_len]
    if not thin:
        return

    pages = await asyncio.gather(
        *(_scrape(r["url"], engine="crawl4ai", timeout=25.0) for r in thin),
        return_exceptions=True,
    )
    for result, page in zip(thin, pages):
        if isinstance(page, BaseException) or not page:
            continue
        # Keep link text, drop the link targets, collapse whitespace.
        text = _MD_LINK_RE.sub(r"\1", _MD_IMAGE_RE.sub("", page))
        text = " ".join(text.split())
        if len(text) > len(result["snippet"]):
            result["snippet"] = text[:500]


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


async def _search_duckduckgo(query: str, limit: int) -> list:
    """DuckDuckGo's lite endpoint: a plain HTML table, no JS, no bot wall.

    Fetched directly rather than through scraper-service — it is static markup, so
    the crawl4ai round trip bought nothing but 20-60s of latency.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": _BROWSER_UA},
            )
            resp.raise_for_status()
            markup = resp.text
    except Exception as e:
        logger.warning(f"ddg lite search failed for {query!r}: {e}")
        return []

    soup = BeautifulSoup(markup, "html.parser")
    results = []
    for row in soup.find_all("tr"):
        # DDG seeds the table with Microsoft ads; they carry snippets too, so they
        # look exactly like results unless we drop them by row class.
        if "sponsored" in " ".join(row.get("class") or []):
            continue

        link = row.find("a", class_="result-link")
        if link is not None:
            href = link.get("href") or ""
            if href.startswith("//"):
                href = "https:" + href
            # Organic links are wrapped as /l/?uddg=<percent-encoded target>.
            target = urllib.parse.parse_qs(
                urllib.parse.urlparse(href).query).get("uddg", [""])[0]
            href = target or href
            title = link.get_text(" ", strip=True)
            if title and href.startswith("http"):
                results.append({"title": title, "url": href, "snippet": ""})
            continue

        cell = row.find("td", class_="result-snippet")
        if cell is not None and results and not results[-1]["snippet"]:
            results[-1]["snippet"] = cell.get_text(" ", strip=True)[:500]

    return results[:limit]


async def _search_brave(query: str, limit: int) -> list:
    """Brave SERP scraped through scraper-service. Kept as a fallback.

    A result's description is the prose that physically follows its link in the
    markdown. An earlier version instead stripped every link out of the whole page,
    kept the long paragraphs, and zipped paragraph N to result N — a pairing with no
    association behind it, so result 2 would get the tail of result 1's paragraph and
    most results arrived with an empty snippet.
    """
    target = f"https://search.brave.com/search?q={urllib.parse.quote(query)}"
    markdown = _MD_IMAGE_RE.sub("", await _scrape(target, engine="crawl4ai"))
    links = list(_MD_LINK_RE.finditer(markdown))
    results, seen = [], set()

    for i, match in enumerate(links):
        href = match.group(2)
        if any(h in href for h in _SEARCH_NOISE_HOSTS):
            continue
        title = match.group(1).strip()
        if len(title) < 3:
            continue
        key = href.split("?utm")[0]
        if key in seen:
            continue
        seen.add(key)

        tail_end = links[i + 1].start() if i + 1 < len(links) else len(markdown)
        snippet = " ".join(markdown[match.end():tail_end].split())
        results.append({"title": title, "url": key, "snippet": snippet[:500]})
        if len(results) >= limit:
            break
    return results


async def web_search(query: str, limit: int = 6) -> list:
    """Keyless web search. Returns [{title, url, snippet}].

    DuckDuckGo lite first, Brave second. Brave was the only engine that still got
    through bot detection when this was written, and as of 2026-07-14 it no longer
    does: crawl4ai gets zero bytes from it and playwright gets "Verifying you're not
    a bot", so every search fell through to a synthetic single result whose snippet
    was the text of the CAPTCHA page. DDG's lite endpoint is static HTML, is not
    walled, answers in about a second, and ships a real description per result — the
    thing a data_card needs in order to show the news instead of a link to the news.
    """
    for engine, search in (("ddg", _search_duckduckgo), ("brave", _search_brave)):
        try:
            results = await search(query, limit)
        except Exception as e:
            logger.warning(f"{engine} search error for {query!r}: {e}")
            continue
        if results:
            if engine != "ddg":
                logger.info(f"[SEARCH] ddg returned nothing; served {query!r} from {engine}")
            await _backfill_snippets(results)
            return results

    logger.error(f"[SEARCH] every engine failed for {query!r}")
    return []


# ─────────────────────────── NEWS ───────────────────────────
# Real, current headlines with real photos. A bare "news" ask used to run
# web_search("top stories news"), and DuckDuckGo answers that with news-org
# HOMEPAGES — nbcnews.com / nytimes.com / news.google.com landing pages whose
# "snippet" describes the organization, never today's stories. That is exactly
# the static-looking card the user complained about.
#
# GDELT's keyless Doc API is built for this: it returns each story's REAL
# article URL, its social image (the article's og:image), the publisher domain,
# and a timestamp — so a card can show a photo and link to the actual story.
# Google News RSS is the fallback (real dated headlines, but its links are
# redirects that never resolve to the article, so no article photo is reachable
# — the publisher favicon stands in). The old web_search is the last resort.

_GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\']'
    r'[^>]+content=["\']([^"\']+)', re.I)


def _norm_title_key(title: str) -> str:
    """Collapse a headline to a dedupe key. GDELT returns the same wire story
    (AP/Reuters) syndicated across a dozen outlets; keying on the lowercased
    alphanumeric words keeps a single copy."""
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))[:90]


async def _gdelt_news(topic: str, limit: int) -> list:
    """GDELT Doc API → [{title, url, image, meta, snippet, date}]. Keyless.
    A blank topic becomes a broad top-stories query."""
    topic = (topic or "").strip()
    if not topic:
        base = "(breaking OR politics OR world OR business)"
    elif len(topic.split()) >= 2:
        # Phrase-match multi-word topics ("artificial intelligence"); a bare
        # AND of the words pulls in articles that merely mention each somewhere.
        base = f'"{topic}"'
    else:
        base = topic
    query = f"{base} sourcelang:english"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(_GDELT_DOC, params={
                "query": query, "mode": "ArtList", "maxrecords": str(limit * 4),
                "format": "json", "sort": "DateDesc", "timespan": "3d",
            }, headers={"User-Agent": _BROWSER_UA})
        # A rate-limited GDELT answers 200/429 with a plain-text body, not JSON.
        if not resp.text.strip().startswith("{"):
            return []
        articles = resp.json().get("articles") or []
    except Exception as e:
        logger.warning(f"gdelt news({topic!r}) failed: {e}")
        return []

    items, seen = [], set()
    for a in articles:
        title = (a.get("title") or "").strip()
        url = a.get("url") or ""
        if not title or not url:
            continue
        key = _norm_title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title,
            "url": url,
            "image": a.get("socialimage") or "",
            "meta": a.get("domain") or _host_of(url),
            "snippet": "",
            "date": a.get("seendate") or "",
        })
        if len(items) >= limit:
            break
    return items


async def _google_news_rss(topic: str, limit: int) -> list:
    """Fallback headlines from Google News RSS. Real and dated, but the links are
    Google redirects that don't resolve to the article, so there is no article
    photo — the publisher favicon is used as a thumbnail instead."""
    if topic and topic.strip():
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(topic.strip()) + "&hl=en-US&gl=US&ceid=US:en")
    else:
        url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _BROWSER_UA})
            resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        logger.warning(f"google news rss({topic!r}) failed: {e}")
        return []

    items, seen = [], set()
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_el = it.find("{*}source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        # Google suffixes the title with " - <Source>". Peel it off so the headline
        # reads cleanly and the source name lands in the meta, not the title.
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        elif not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        src_url = source_el.get("url") if source_el is not None else ""
        host = _host_of(src_url) if src_url else ""
        key = _norm_title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title.strip(),
            "url": link,
            "image": (f"https://www.google.com/s2/favicons?domain={host}&sz=128"
                      if host else ""),
            "meta": source or host,
            "snippet": "",
            "date": it.findtext("pubDate") or "",
        })
        if len(items) >= limit:
            break
    return items


async def _enrich_news(items: list, timeout: float = 5.0) -> None:
    """Fill each item's summary (og:description) and, when GDELT had no social
    image, its photo (og:image), by fetching the real article. Concurrent and
    best-effort — a slow or blocking site just leaves that item with its title
    as the summary."""
    async def one(client: httpx.AsyncClient, item: dict) -> None:
        try:
            resp = await client.get(item["url"], headers={"User-Agent": _BROWSER_UA})
            html = resp.text
        except Exception:
            return
        if not item.get("snippet"):
            m = _OG_DESC_RE.search(html)
            if m:
                item["snippet"] = _html_unescape(m.group(1)).strip()[:400]
        if not item.get("image"):
            m = _OG_IMAGE_RE.search(html)
            if m:
                item["image"] = _html_unescape(m.group(1)).strip()

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        await asyncio.gather(*(one(client, it) for it in items),
                             return_exceptions=True)


async def news_search(topic: str, limit: int = 6) -> list:
    """Current headlines with real photos and summaries.

    GDELT first (real article URLs + social images), Google News RSS as a
    headline-only fallback, generic web search last. Returns
    [{title, url, image, meta, snippet, date}].
    """
    items, source = await _gdelt_news(topic, limit), "gdelt"
    if not items:
        items, source = await _google_news_rss(topic, limit), "google-news"
    if not items:
        raw = await web_search(f"{topic} news" if topic else "top news headlines", limit)
        items = [{"title": r.get("title", ""), "url": r.get("url", ""), "image": "",
                  "meta": _host_of(r.get("url", "")), "snippet": r.get("snippet", ""),
                  "date": ""} for r in raw]
        source = "web"
    # GDELT / DDG expose real article URLs, so enrich them with og:description and
    # og:image. Google News links are redirects that don't resolve — skip those.
    if items and source in ("gdelt", "web"):
        await _enrich_news(items)
    logger.info(f"[NEWS] {topic!r} → {len(items)} items via {source}")
    return items


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


async def stock_news(query: str, limit: int = 8) -> dict:
    """Stock/company news headlines + ticker matches for a free-text query.

    Yahoo's search endpoint is keyless like the chart endpoint above and
    answers both jobs stock_snapshot can't: "what's the news on X" and
    "find me stocks" (the quotes array is ticker discovery — the model can
    feed those symbols straight into html_notes_stock_history).
    """
    query = (query or "").strip()
    if not query:
        return {"error": "stock_news needs a query (ticker, company name, or topic).", "is_error": True}
    limit = max(1, min(int(limit or 8), 15))

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://query1.finance.yahoo.com/v1/finance/search"
                f"?q={urllib.parse.quote(query)}&newsCount={limit}&quotesCount=6",
                headers={"User-Agent": _YAHOO_UA})
            payload = resp.json()

        news = []
        for item in payload.get("news") or []:
            stamp = item.get("providerPublishTime")
            news.append({
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "published": (datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
                              .strftime("%Y-%m-%d %H:%M UTC") if stamp else None),
                "url": item.get("link"),
                "related_tickers": item.get("relatedTickers") or [],
            })

        matches = [
            {
                "symbol": q.get("symbol"),
                "name": q.get("longname") or q.get("shortname"),
                "exchange": q.get("exchDisp"),
                "type": q.get("quoteTypeDisp") or q.get("quoteType"),
            }
            for q in (payload.get("quotes") or [])
            if q.get("symbol")
        ]

        if not news and not matches:
            return {"news": [], "matches": [], "count": 0,
                    "message": "Nothing found. Retry with a ticker symbol or a shorter company name."}
        return {"news": news, "matches": matches, "count": len(news)}
    except Exception as e:
        logger.error(f"stock_news({query}) error: {e}")
        return {"error": str(e), "is_error": True}


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


# Results of the data tools, so a widget can reference data the model just
# fetched instead of re-typing it.
#
# stock_card's config IS the whole snapshot — labels, values, volumes, technicals,
# fundamentals — and the model was hand-typing all ~2000 tokens of it back into
# canvas_add_widget's arguments. That single re-emission was most of the turn: the
# data tool answered at ~10s and the widget call didn't land until ~65s. Now the
# model passes {"symbol": "AMZN"} and the server rehydrates the rest.
#
# Keyed by content (not session) so it stays correct with concurrent turns.
_TOOL_RESULT_TTL = 300.0
_tool_results: Dict[str, tuple] = {}


def cache_tool_result(key: str, value: dict) -> None:
    now = time.time()
    for k, (stamp, _) in list(_tool_results.items()):
        if now - stamp > _TOOL_RESULT_TTL:
            _tool_results.pop(k, None)
    _tool_results[key.lower().strip()] = (now, value)


def get_cached_tool_result(key: str) -> Optional[dict]:
    entry = _tool_results.get((key or "").lower().strip())
    if not entry:
        return None
    stamp, value = entry
    return value if time.time() - stamp <= _TOOL_RESULT_TTL else None


def cached_stock_symbols() -> list:
    """Every ticker whose snapshot we fetched recently, newest first."""
    now = time.time()
    hits = [
        (stamp, value.get("symbol"))
        for key, (stamp, value) in _tool_results.items()
        if key.startswith("stock:") and now - stamp <= _TOOL_RESULT_TTL
        and isinstance(value, dict) and value.get("symbol")
    ]
    return [symbol for _, symbol in sorted(hits, reverse=True)]


def coerce_widget_type(widget_type: str, widget_id: str, config: dict) -> tuple:
    """Send a ticker to stock_card even when the model asked for 'chart'.

    'chart' is a bare Chart.js line — no price header, no range tabs, no technicals,
    no fundamentals. 'chart' and 'stock_card' sit next to each other in one enum, so
    which one a ticker gets was decided by sampling, and the answer changed between
    identical requests. Both the tool description and the system prompt said "never
    chart a ticker" and neither held, because a rule in prose cannot outvote a token
    distribution. Deciding it here makes it deterministic.

    The model has to call html_notes_stock_history before it can chart a price, so a
    cached snapshot whose symbol appears in the config, title or widget_id is a
    positive ID for the ticker it is trying to draw.
    """
    if widget_type != "chart" or not isinstance(config, dict):
        return widget_type, config

    symbol = config.get("symbol") or config.get("ticker") or ""
    if not symbol:
        haystack = f"{widget_id} {config.get('title', '')}".upper()
        symbol = next(
            (s for s in cached_stock_symbols() if s.upper() in haystack),
            "",
        )
    if not symbol:
        return widget_type, config

    logger.info(f"[WIDGET COERCE] chart -> stock_card for {symbol}")
    return "stock_card", {"symbol": str(symbol).upper()}


def render_widget(widget_type: str, widget_id: str, config: dict) -> str:
    """Single choke point for widget HTML: coerce the type, then render."""
    widget_type, config = coerce_widget_type(widget_type, widget_id, config)
    return generate_widget_html(widget_type, widget_id, config)


# Media widgets (video, audio) are players, not data: the canvas can only ever
# play one thing at a time, so a new one replaces whatever's already playing.
# Data widgets (stock_card, scoreboard, notes, ...) coexist — a new one adds
# alongside the rest unless it shares a widget_id with an existing one.
_MEDIA_WIDGET_MARKERS = {
    "youtube_player": "youtubePlayerWidget",
    "mini_music_player": "musicPlayerWidget",
}


def find_singleton_media_widget(soup, widget_type: str):
    """Return the existing widget-container div for this media type, if any."""
    marker = _MEDIA_WIDGET_MARKERS.get(widget_type)
    if not marker:
        return None
    for div in soup.find_all("div", class_="widget-container"):
        if marker in div.get("x-data", ""):
            return div
    return None


async def read_web_page(url: str, max_chars: int = 6000) -> dict:
    """Fetch and return the readable text of a page."""
    content = await _scrape(url, engine="crawl4ai")
    if not content:
        content = await _scrape(url, engine="playwright", timeout=60.0)
    if not content:
        return {"error": f"Could not fetch {url}", "is_error": True}
    return {"url": url, "content": content[:max_chars]}


# WMO weather-interpretation codes → (label, material-symbols icon, emoji).
# Open-Meteo returns this integer as `weather_code`. https://open-meteo.com/en/docs
_WMO_CODES = {
    0: ("Clear", "clear_day", "☀️"),
    1: ("Mainly clear", "partly_cloudy_day", "🌤️"),
    2: ("Partly cloudy", "partly_cloudy_day", "⛅"),
    3: ("Overcast", "cloud", "☁️"),
    45: ("Fog", "foggy", "🌫️"), 48: ("Rime fog", "foggy", "🌫️"),
    51: ("Light drizzle", "rainy", "🌦️"), 53: ("Drizzle", "rainy", "🌦️"),
    55: ("Heavy drizzle", "rainy", "🌦️"),
    56: ("Freezing drizzle", "rainy", "🌧️"), 57: ("Freezing drizzle", "rainy", "🌧️"),
    61: ("Light rain", "rainy", "🌧️"), 63: ("Rain", "rainy", "🌧️"),
    65: ("Heavy rain", "rainy", "🌧️"),
    66: ("Freezing rain", "rainy", "🌧️"), 67: ("Freezing rain", "rainy", "🌧️"),
    71: ("Light snow", "weather_snowy", "🌨️"), 73: ("Snow", "weather_snowy", "❄️"),
    75: ("Heavy snow", "weather_snowy", "❄️"), 77: ("Snow grains", "weather_snowy", "🌨️"),
    80: ("Light showers", "rainy", "🌦️"), 81: ("Showers", "rainy", "🌧️"),
    82: ("Violent showers", "thunderstorm", "⛈️"),
    85: ("Snow showers", "weather_snowy", "🌨️"), 86: ("Snow showers", "weather_snowy", "❄️"),
    95: ("Thunderstorm", "thunderstorm", "⛈️"),
    96: ("Thunderstorm, hail", "thunderstorm", "⛈️"), 99: ("Thunderstorm, hail", "thunderstorm", "⛈️"),
}


def _wmo(code) -> tuple:
    try:
        return _WMO_CODES.get(int(code), ("Unknown", "help", "❓"))
    except (TypeError, ValueError):
        return ("Unknown", "help", "❓")


async def geocode_location(name: str) -> Optional[dict]:
    """City name → {name, label, latitude, longitude} via Open-Meteo's keyless
    geocoding API. Returns None if nothing matches."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": name, "count": 1, "language": "en", "format": "json"},
            )
            results = resp.json().get("results") or []
    except Exception as e:
        logger.warning(f"geocode({name!r}) failed: {e}")
        return None
    if not results:
        return None
    r = results[0]
    label = r.get("name", name)
    parts = [p for p in (label, r.get("admin1"), r.get("country_code")) if p]
    # dict.fromkeys dedupes "Singapore, Singapore" → "Singapore".
    return {
        "name": label,
        "label": ", ".join(dict.fromkeys(parts)),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
    }


async def get_weather(location: str, units: str = "fahrenheit") -> dict:
    """Current conditions + 5-day forecast for a place, via Open-Meteo (keyless).

    Geocodes the name, then one forecast call. The returned dict is exactly the
    weather widget's config; {is_error: True} on any failure so the caller can
    fall through to the agent instead of rendering a dead card.
    """
    place = await geocode_location(location)
    if not place or place.get("latitude") is None:
        return {"error": f"Couldn't find a place called '{location}'.", "is_error": True}

    fahrenheit = units != "celsius"
    unit_sym = "°F" if fahrenheit else "°C"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
                "wind_speed_unit": "mph" if fahrenheit else "kmh",
                "timezone": "auto",
                "forecast_days": 5,
            })
            data = resp.json()
    except Exception as e:
        logger.warning(f"weather({location!r}) failed: {e}")
        return {"error": f"Couldn't fetch weather for {place['label']}.", "is_error": True}

    def _round(v):
        return round(v) if isinstance(v, (int, float)) else None

    cur = data.get("current") or {}
    cur_label, cur_icon, cur_emoji = _wmo(cur.get("weather_code"))
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    days = []
    for i, d in enumerate(dates[:5]):
        label, icon, emoji = _wmo(codes[i] if i < len(codes) else None)
        try:
            y, m, dd = (int(x) for x in d.split("-"))
            day_name = "Today" if i == 0 else datetime.date(y, m, dd).strftime("%a")
        except Exception:
            day_name = "Today" if i == 0 else d[5:]
        days.append({
            "day": day_name,
            "hi": _round(highs[i]) if i < len(highs) else None,
            "lo": _round(lows[i]) if i < len(lows) else None,
            "condition": label, "icon": icon, "emoji": emoji,
        })

    return {
        "location": place["label"],
        "unit": unit_sym,
        "current": {
            "temp": _round(cur.get("temperature_2m")),
            "feels_like": _round(cur.get("apparent_temperature")),
            "humidity": cur.get("relative_humidity_2m"),
            "wind": _round(cur.get("wind_speed_10m")),
            "wind_unit": "mph" if fahrenheit else "km/h",
            "condition": cur_label, "icon": cur_icon, "emoji": cur_emoji,
        },
        "daily": days,
    }


from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app import database
from app.config import PORT, PRISM_URL, LAZY_AGENT_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL, TTS_SERVICE_URL, MUSIC_PLAYER_URL, SCRAPER_SERVICE_URL
import asyncio
import contextvars
import datetime
import hashlib
import itertools
import json
import os
import pathlib
import time
import uuid
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html, _host_of, map_document_html, map_payload
import base64 as _base64


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

# Request ARRIVAL order. A media singleton (video/music) swap is a locked
# read-modify-write, so the last turn to COMMIT wins — but two video asks fired
# seconds apart can commit out of order (their YouTube scrapes finish at different
# speeds), and then the OLDER request's video overwrites the newer one ("the video
# doesn't change until I refresh"). Stamping each media widget with the arrival
# seq and refusing to replace a widget placed by a NEWER request fixes the order
# without touching the coexist path (which correctly relies on last-commit-wins).
# Epoch-ms seeded (like _version_counter) so a container restart still hands out
# seqs higher than any data-req-seq a live tab could be holding from before it.
_request_counter = itertools.count(int(time.time() * 1000))


def _stamp_media_seq(node, widget_type: str, req_seq: int) -> None:
    """Stamp a freshly-appended media widget with the request arrival seq so a
    later-committing OLDER request can't overwrite it (see _place_media_widget)."""
    if req_seq and widget_type in _MEDIA_WIDGET_MARKERS:
        root = node.find("div", class_="widget-container")
        if root is not None:
            root["data-req-seq"] = str(req_seq)


def _place_media_widget(soup, widget_type: str, widget_id: str, config: dict, req_seq: int) -> bool:
    """Swap the singleton media widget of this type in place, honouring request
    arrival order. Returns True if it handled the widget (caller should stop),
    False if there is no existing media widget to replace (caller appends)."""
    media_div = find_singleton_media_widget(soup, widget_type)
    if media_div is None:
        return False
    try:
        existing_seq = int(media_div.get("data-req-seq") or 0)
    except (TypeError, ValueError):
        existing_seq = 0
    if req_seq and existing_seq and req_seq < existing_seq:
        # A newer request already placed a media widget here — don't clobber it
        # with this older (slower-committing) request's video/track.
        return True
    existing_id = media_div.get("id", widget_id)
    new_node = BeautifulSoup(render_widget(widget_type, existing_id, config), "html.parser")
    root = new_node.find("div", class_="widget-container")
    if root is not None and req_seq:
        root["data-req-seq"] = str(req_seq)
    media_div.replace_with(new_node)
    return True

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
    logger.info(f"[CANVAS] committed v{version} ({len(html)} bytes) session={session_id[:8]} "
                f"— emitting component frame to client")
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


def pick_varied_video(hits: list, k: int = 5):
    """Choose a video from the top-`k` results at random, for VARIETY.

    A broad ask ("a cookie recipe video", "funny cats") always returned the same
    #1 YouTube hit, which is boring on repeat. Picking from the top handful keeps
    the result relevant (they're all strong matches for the query) while making a
    second identical ask land on something different. Returns
    (chosen_hit, other_ids) where other_ids are the remaining results IN RELEVANCE
    ORDER — so the widget's embed-error fallback still hops to the next-best video,
    not another random one.

    Deterministic callers (live streams, a named channel) must NOT use this: they
    want the single canonical result, so they keep indexing hits[0] directly.
    """
    if not hits:
        return None, []
    pool = hits[: max(1, k)]
    chosen = random.choice(pool)
    cid = chosen.get("video_id")
    others = [v["video_id"] for v in hits if v.get("video_id") and v.get("video_id") != cid]
    return chosen, others


# Sports fixtures/scores. Without a tool these fell to the agent, which
# web-searched (20-60s) and tried to squeeze a scoreboard into a text card.
SCORE_ASK_RE = re.compile(
    r'\b(scores?|fixtures?|results?|standings?|schedule|matchups?|'
    r'who\'?s playing|whos playing|playing today|next (game|match|fight)|'
    r'card|bouts?|fights?)\b')
DATA_ASK_RE = re.compile(r'\b(news|headlines|weather|forecast|stock|price|chart|graph|image|images|picture|pictures|photo|photos)\b')

# Fast-path triggers for the two data widgets that now have real sources+renderers.
WEATHER_ASK_RE = re.compile(r'\b(weather|forecast|temperature)\b')
NEWS_ASK_RE = re.compile(r'\b(news|headlines?)\b')
# Stock news has its own cleaner path (html_notes_stock_news); keep it off the
# general news fast-path.
STOCK_WORD_RE = re.compile(r'\b(stock|stocks|ticker|shares|share price|crypto|nasdaq|equities?)\b')

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
# A question/how-to/recipe that wants a SYNTHESISED answer (summary + sources),
# not a widget noun and not a data feed. Routed to build_answer_config so the
# user gets the actual recipe/steps/definition instead of the ~30-60s agent loop.
ANSWER_ASK_RE = re.compile(
    r'\b(recipe|recipes|how to|how do|how does|how can|tutorial|guide|'
    r'what is|what are|whats|what\'s|who is|who are|who was|when is|when was|'
    r'why is|why do|why does|explain|difference between|'
    r'vs\.?|versus|meaning of|definition of|instructions?|steps to)\b')
# A geo/location query that wants a MAP with markers, not a text answer. Checked
# before ANSWER so "where are the fires in California" pulls up a map. Kept to
# strong geo signals so a conceptual "why is X" never gets a map.
MAP_ASK_RE = re.compile(
    r'\b(map|maps|on a map|where are|where is|where\'?s|located|location of|'
    r'near me|nearby|fires? in|wildfires?|earthquakes?|flooding|floods?|'
    r'hurricanes?|tornado(es)?|show me where|whereabouts)\b')

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


_LOCATION_STOPWORDS = {
    "weather", "forecast", "temperature", "temp", "climate", "the", "whats", "what",
    "is", "in", "for", "at", "near", "today", "tomorrow", "current", "currently",
    "right", "now", "like", "hows", "how", "s", "a", "me", "show", "get",
}


def extract_location(message: str) -> str:
    """Pull a place name out of a weather ask. 'weather in San Francisco' → 'San
    Francisco'; 'tokyo weather' → 'tokyo'; bare 'weather' → 'New York' default."""
    m = (message or "").strip()
    match = re.search(r'\b(?:in|for|at|near)\s+([A-Za-zÀ-ɏ .,\'-]+)', m, re.IGNORECASE)
    if match:
        loc = match.group(1).strip(" .,")
        if loc:
            return loc
    cleaned = re.sub(r'[^\w\s]', ' ', m.lower())
    words = [w for w in cleaned.split() if w not in _LOCATION_STOPWORDS]
    return " ".join(words).strip() or "New York"


async def build_news_config(message: str) -> dict:
    """A news data_card of current stories: photo + tightened headline + a 2-3
    sentence summary per item.

    Pulls real headlines with images via news_search (GDELT → Google News RSS →
    web search), then a single local-LLM pass rewrites the snippets into
    summaries mapped back to their sources. Falls back to the raw items if the
    model call fails, so the card is never a wall of links.
    """
    # extract_topic drops widget/filler words; also drop news-y words so
    # "news about AI" → topic "ai", not a doubled "News: News Ai" title.
    _NEWSY = {"news", "headline", "headlines", "latest", "recent", "breaking",
              "update", "updates", "today", "story", "stories", "about"}
    raw = extract_topic(message)
    topic = " ".join(w for w in raw.split() if w not in _NEWSY).strip()
    display = topic or "top stories"
    results = await news_search(topic, limit=6)
    if not results:
        return {"title": f"News: {display}".title()[:60], "icon": "newspaper", "items": []}

    def raw_items():
        return [{
            "title": r.get("title", ""),
            "description": (r.get("snippet") or "")[:300],
            "url": r.get("url", ""),
            "image": r.get("image", ""),
            "meta": r.get("meta") or _host_of(r.get("url", "")),
            "badge": "News",
        } for r in results[:6]]

    source_lines = [
        f'[{i}] {r.get("title","")}\n{(r.get("snippet") or "")[:400]}'
        for i, r in enumerate(results[:6])
    ]
    data = await fast_llm_json(
        'You are a news editor. Return ONLY a JSON object, no prose, no markdown fence:\n'
        '{"overview": "<one-sentence summary of the whole topic>", '
        '"items": [{"index": <the [N] number of the source>, "title": "<tightened headline>", '
        '"summary": "<2-3 sentence plain-English summary of what happened>"}]}\n'
        f'Topic: "{display}"\n\nSOURCES:\n' + "\n\n".join(source_lines) + '\n\n'
        'Write one entry per distinct story (max 6). Base every summary ONLY on that '
        "source's text — never invent facts, names, or numbers not present in it. If a "
        "source is only a headline with no body text, keep its summary to a faithful "
        "one-line restatement of that headline.",
        max_tokens=1000,
    )
    if not data or not isinstance(data.get("items"), list) or not data["items"]:
        return {"title": f"News: {display}".title()[:60], "icon": "newspaper", "items": raw_items()}

    items = []
    for it in data["items"][:6]:
        idx = it.get("index")
        src = results[idx] if isinstance(idx, int) and 0 <= idx < len(results) else {}
        summary = (it.get("summary") or "").strip()
        items.append({
            "title": (it.get("title") or src.get("title") or "")[:140],
            "description": summary[:500] or (src.get("snippet") or "")[:300],
            "url": src.get("url", ""),
            "image": src.get("image", ""),
            "meta": src.get("meta") or (_host_of(src.get("url", "")) if src.get("url") else ""),
            "badge": "News",
        })
    return {
        "title": f"News: {display}".title()[:60],
        "subtitle": (data.get("overview") or "")[:120],
        "icon": "newspaper",
        "items": items or raw_items(),
    }


# Map the LLM-chosen answer `format` to a header icon. The model picks the format;
# this is just the visual affordance for it. Unknown formats fall back to "article".
_ANSWER_ICONS = {
    "recipe": "restaurant", "howto": "checklist", "how-to": "checklist",
    "steps": "checklist", "definition": "menu_book", "fact": "lightbulb",
    "comparison": "compare_arrows", "list": "format_list_bulleted",
    "article": "article", "explainer": "article", "answer": "lightbulb",
}


async def build_answer_config(query: str, results: Optional[list] = None,
                              read_top: int = 2) -> dict:
    """Turn a general web query (recipe, how-to, fact, "what/who is X", comparison)
    into a SYNTHESISED data_card: a readable Markdown answer up top, with the pages
    it drew from demoted to a "Sources" list — instead of a wall of links.

    Pipeline mirrors build_news_config but for arbitrary questions:
      search -> read the top `read_top` pages for real body text (a recipe's
      ingredients/steps live in the page, not the snippet) -> ONE local-LLM pass
      that WRITES the answer as Markdown and PICKS the format itself (agentic:
      a recipe becomes ingredients+steps, a definition a short paragraph, a
      comparison a table). The model only ever fills a structured schema the
      server renders — it never hand-builds HTML — so it stays reliable.

    Degrades gracefully: if the LLM pass fails, returns a summarised item list so
    the card is still useful and never a wall of naked links.
    """
    q = (query or "").strip()
    if results is None:
        results = await web_search(q, limit=6)
    if not results:
        return {"title": (q or "Search")[:60].title(), "icon": "search",
                "answer": f"I couldn't find anything for **{q}**. Try rephrasing the question.",
                "items": []}

    # Read the top pages concurrently for real content — snippets alone can't carry
    # a recipe's ingredients or a how-to's steps. Bounded + best-effort; a scrape
    # miss just means that source contributes its snippet instead.
    async def _page_text(r):
        url = r.get("url", "")
        if not url:
            return ""
        try:
            page = await read_web_page(url, max_chars=2500)
            return "" if page.get("is_error") else (page.get("content") or "")
        except Exception:
            return ""
    top = results[:max(0, read_top)]
    # Hard cap the enrichment: read_web_page falls back to a 60s Playwright scrape,
    # and this runs on a "fast" path — a slow page must never hang the card. On
    # timeout we synthesise from snippets alone.
    try:
        page_texts = (await asyncio.wait_for(
            asyncio.gather(*[_page_text(r) for r in top]), timeout=12.0)) if top else []
    except asyncio.TimeoutError:
        logger.info(f"build_answer_config: page reads timed out for {q!r}, using snippets")
        page_texts = []

    source_blocks = []
    for i, r in enumerate(results[:6]):
        body = page_texts[i] if i < len(page_texts) else ""
        chunk = (body or r.get("snippet") or "")[:1800]
        source_blocks.append(f'[{i}] {r.get("title","")} ({_host_of(r.get("url",""))})\n{chunk}')

    data = await fast_llm_json(
        'You are a research assistant writing a single, self-contained answer card. '
        'Return ONLY a JSON object (no prose, no markdown fence):\n'
        '{"format": "<recipe|howto|definition|fact|comparison|explainer>", '
        '"title": "<short card title>", '
        '"overview": "<one plain sentence summarising the answer>", '
        '"answer": "<the full answer in GitHub-flavored Markdown>", '
        '"sources": [<the [N] index numbers of the sources you actually used>]}\n\n'
        f'QUESTION: "{q}"\n\nSOURCES:\n' + "\n\n".join(source_blocks) + '\n\n'
        'RULES:\n'
        '- WRITE THE ANSWER, do not list links. The user should get what they asked for '
        'directly in `answer`.\n'
        '- Choose the format that fits: a recipe -> "## Ingredients" (bulleted) then '
        '"## Steps" (numbered); a how-to -> numbered steps; a definition/fact -> a tight '
        'paragraph or two; a comparison -> a Markdown table.\n'
        '- Use Markdown: ##/### headings, - bullets, 1. numbered lists, **bold**, and '
        '[label](url) links. Keep it scannable.\n'
        '- Ground every claim in the SOURCES text above. Never invent quantities, names, '
        'dates or numbers that are not present. If sources conflict, say so briefly.\n'
        '- `sources` lists only the indices you drew from.',
        max_tokens=1400,
    )

    def summarised_items(indices=None):
        pool = results[:6] if indices is None else [results[i] for i in indices
                                                    if isinstance(i, int) and 0 <= i < len(results)]
        return [{
            "title": r.get("title", ""),
            "description": (r.get("snippet") or "")[:240],
            "url": r.get("url", ""),
            "image": r.get("image", ""),
            "meta": _host_of(r.get("url", "")),
            "badge": "Source",
        } for r in (pool or results[:6])]

    hero = next((r.get("image") for r in results if r.get("image")), "")

    if not data or not (data.get("answer") or "").strip():
        # LLM pass failed — still give a useful, summarised card (not naked links).
        return {"title": (q or "Answer")[:60].title(), "icon": "search",
                "subtitle": "Top results", "image": hero, "items": summarised_items()}

    used = data.get("sources")
    used = used if isinstance(used, list) else None
    fmt = str(data.get("format", "")).lower()
    return {
        "title": (data.get("title") or q or "Answer")[:70],
        "subtitle": (data.get("overview") or "")[:140],
        "icon": _ANSWER_ICONS.get(fmt, "article"),
        "image": hero,
        "answer": (data.get("answer") or "").strip(),
        "items": summarised_items(used),
    }


async def geocode_place(name: str) -> Optional[dict]:
    """Place name -> {lat, lon, resolved} via Open-Meteo's keyless geocoder.
    Fast and clean but CITY/TOWN-level only — it misses counties, landmarks and
    event-ish strings. Returns None on a miss; build_map_config retries those
    through Nominatim (geocode_place_flex)."""
    name = (name or "").strip()
    if len(name) < 2:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": name, "count": 1, "language": "en", "format": "json"})
            hits = (r.json() or {}).get("results") or []
            if hits:
                g = hits[0]
                return {"lat": g["latitude"], "lon": g["longitude"], "resolved": g.get("name", name)}
    except Exception as e:
        logger.warning(f"geocode(open-meteo) failed for {name!r}: {e}")
    return None


async def geocode_nominatim(query: str) -> Optional[dict]:
    """Fallback geocoder (OSM Nominatim) for what Open-Meteo can't resolve —
    counties, landmarks, 'Butte County California'. Keyless but needs a real
    User-Agent and is rate-limited to ~1 req/s, so build_map_config calls it
    SEQUENTIALLY and only for the Open-Meteo misses."""
    query = (query or "").strip()
    if len(query) < 2:
        return None
    try:
        async with httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": "html-notes-map/1.0 (dashboard widget)"}) as c:
            r = await c.get("https://nominatim.openstreetmap.org/search",
                            params={"q": query, "format": "json", "limit": 1})
            arr = r.json() if r.status_code == 200 else []
            if arr:
                return {"lat": float(arr[0]["lat"]), "lon": float(arr[0]["lon"]),
                        "resolved": (arr[0].get("display_name", query) or query).split(",")[0][:40]}
    except Exception as e:
        logger.warning(f"geocode(nominatim) failed for {query!r}: {e}")
    return None


async def build_map_config(query: str) -> dict:
    """Turn a geo query ("where are the fires in California", "map of X") into a
    `map` widget: search the web, have the LLM PULL the place names out of the
    results, geocode them, and drop markers. Fully agentic on the data side — the
    model decides WHICH places matter; the server renders the template.

    Degrades to a region-centred (marker-less) map so the card is never blank.
    """
    q = (query or "").strip()
    results = await web_search(q, limit=6)

    async def _page_text(r):
        url = r.get("url", "")
        if not url:
            return ""
        try:
            page = await read_web_page(url, max_chars=2200)
            return "" if page.get("is_error") else (page.get("content") or "")
        except Exception:
            return ""
    try:
        page_texts = await asyncio.wait_for(
            asyncio.gather(*[_page_text(r) for r in results[:2]]), timeout=12.0)
    except asyncio.TimeoutError:
        page_texts = []

    src = []
    for i, r in enumerate(results[:6]):
        body = page_texts[i] if i < len(page_texts) else ""
        src.append(f'[{i}] {r.get("title","")}\n{(body or r.get("snippet") or "")[:1500]}')

    data = await fast_llm_json(
        'You extract mappable locations from search results. Return ONLY JSON:\n'
        '{"title": "<short map title>", "overview": "<one sentence>", '
        '"region": "<a broad geocodable area to centre on, e.g. \'California\'>", '
        '"locations": [{"place": "<the nearest NAMED TOWN or CITY, plus state/country>", '
        '"label": "<short marker label>", "detail": "<one short line: what/when/how big>"}]}\n\n'
        f'QUERY: "{q}"\n\nSOURCES:\n' + "\n\n".join(src) + '\n\n'
        'Give up to 12 locations. CRITICAL: `place` must be a NAMED TOWN or CITY that a '
        'geocoder can find (e.g. "Chico, California") — NOT a county ("Butte County"), NOT '
        'the event name ("Park Fire"), NOT a highway or park. If the source only names a '
        'county or region, pick the largest town in it. Put the descriptive name in `label` '
        'instead. Base everything on the SOURCES; do not invent places. One place → one location.',
        max_tokens=1100,
    )

    locs = (data or {}).get("locations") or []
    locs = [l for l in locs if isinstance(l, dict) and (l.get("place") or "").strip()][:12]
    # Pass 1: Open-Meteo concurrently (fast, city-level).
    geocoded = await asyncio.gather(*[geocode_place(l.get("place", "")) for l in locs]) if locs else []

    def _marker(l, g):
        return {"lat": g["lat"], "lon": g["lon"],
                "label": (l.get("label") or l.get("place") or g["resolved"])[:90],
                "detail": (l.get("detail") or "")[:180], "color": "#ef4444"}

    markers, misses = [], []
    for l, g in zip(locs, geocoded):
        (markers.append(_marker(l, g)) if g else misses.append(l))

    # Pass 2: Nominatim for the misses — sequential (rate limit), bounded, and it
    # resolves the counties/landmarks Open-Meteo rejected so yield stays high.
    for l in misses[:6]:
        g = await geocode_nominatim(l.get("place", ""))
        if g:
            markers.append(_marker(l, g))

    center = None
    if not markers:
        region = (data or {}).get("region") or q
        g = await geocode_place(region) or await geocode_nominatim(region)
        if g:
            center = {"lat": g["lat"], "lon": g["lon"]}

    return {
        "title": ((data or {}).get("title") or q or "Map")[:70],
        "subtitle": ((data or {}).get("overview") or (f"{len(markers)} location"
                     + ("s" if len(markers) != 1 else "") if markers else ""))[:120],
        "markers": markers,
        "center": center,
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
        # Stamp this request's arrival order so a slower-committing older video
        # can't overwrite a newer one (see _place_media_widget). Captured as a
        # local so both nested closures (fast-path _append, agent injector _add)
        # read the same value regardless of context propagation.
        req_seq = next(_request_counter)
        # Audit trail: every query is logged at ingress with its req_seq so a
        # failed-to-render query can be correlated with whether a canvas commit
        # (logged in commit_canvas as [CANVAS]) was ever emitted for it. If a
        # [QUERY] has a matching [CANVAS] emit but nothing appeared on screen,
        # the loss is client-side (SSE framing); if there is no [CANVAS], the
        # turn produced no widget server-side.
        logger.info(f"[QUERY] seq={req_seq} session={req.session_id[:8]} msg={req.message!r}")
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

                def _append(soup):
                    # Media widgets (video, music) are players: a new one replaces
                    # whatever's already playing instead of stacking a second one —
                    # but only if this request is newer than whatever placed it, so
                    # a slower older request can't overwrite it.
                    if _place_media_widget(soup, widget_type, widget_id, widget_config, req_seq):
                        return

                    target = soup.select_one('#dashboard-grid')
                    if target is None:
                        grid = BeautifulSoup(
                            '<div id="dashboard-grid" class="dashboard-grid"></div>', 'html.parser')
                        soup.append(grid)
                        target = soup.select_one('#dashboard-grid')
                    node = BeautifulSoup(
                        render_widget(widget_type, widget_id, widget_config), 'html.parser')
                    _stamp_media_seq(node, widget_type, req_seq)
                    target.append(node)

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
            # Off-season / no fixtures today: ESPN has nothing to put on a
            # scoreboard, so rather than silently falling through to the slow
            # agent loop (which for "fifa game current stats" rendered NOTHING),
            # synthesize an answer card — standings, upcoming fixtures, recent
            # results — from a web search. The user always gets something.
            return spawn_widget_stream(
                "data_card", "sports-answer",
                config_builder=lambda: build_answer_config(req.message),
                status="the league is between games — finding the latest...")

        # 2. NEWS VIDEO — "fifa news video". Searched by relevance this returns the
        #    most-watched clip for those words (a years-old recap); news means the
        #    NEWEST upload, so sort by date and drop the medium words from the query.
        if (is_video_ask and RECENCY_RE.search(text_clean)
                and not wants_removal and not LIVE_ASK_RE.search(text_clean)):
            query = clean_video_query(req.message)
            hits = await search_youtube_videos(query, limit=6, order="date")
            if not hits:
                hits = await search_youtube_videos(query, limit=6)
            # Vary among the top few NEWEST clips: still recent (that's what "news"
            # means), but not the identical video on every repeat ask.
            top, cands = pick_varied_video(hits, k=3)
            if top:
                return spawn_widget_stream("youtube_player", "news-video", {
                    "video_id": top["video_id"],
                    "title": top.get("title") or query,
                    "query": query,
                    "candidates": cands,
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

        # 3b. GENERAL VIDEO — a plain "show me a video of X" / "X video" that is
        #     neither a live stream (handled above, deterministic — bloomberg live
        #     is always THE stream) nor a dated news clip. Broad topic asks like
        #     "a cookie recipe video" used to fall through to the agent, which
        #     picked the #1 hit every time, so a repeat ask replayed the identical
        #     video. Fast-path it AND vary among the top handful so it stays
        #     interesting. Music videos keep going to the player, not here.
        if is_video_ask and not wants_removal and not wants_music:
            vquery = clean_video_query(req.message)
            vhits = await search_youtube_videos(vquery, limit=8)
            top, cands = pick_varied_video(vhits, k=5)
            if top:
                return spawn_widget_stream("youtube_player", "video", {
                    "video_id": top["video_id"],
                    "title": top.get("title") or vquery,
                    "query": vquery,
                    "candidates": cands,
                }, status=f"finding a '{vquery}' video...")

        # 4. WEATHER — "weather in Tokyo", "forecast for London", "sf weather".
        #    A real Open-Meteo pull (keyless) rendered as the dedicated weather
        #    widget, not a text card of search results about weather.
        if WEATHER_ASK_RE.search(text_clean) and not wants_removal and not is_video_ask:
            weather = await get_weather(extract_location(req.message))
            if not weather.get("is_error"):
                return spawn_widget_stream(
                    "weather", "weather", weather,
                    status=f"checking the weather in {weather.get('location', '')}...")
            # An unresolved place falls through to the agent instead of a dead card.

        # 5. NEWS — "news about X", "latest headlines". Search + one summarizing
        #    LLM pass so each item reads as a story, not a bare link. Stock news
        #    keeps its own dedicated path.
        if (NEWS_ASK_RE.search(text_clean) and not wants_removal
                and not is_video_ask and not STOCK_WORD_RE.search(text_clean)
                and not LIVE_ASK_RE.search(text_clean)):
            return spawn_widget_stream(
                "data_card", "news",
                config_builder=lambda: build_news_config(req.message),
                status="gathering and summarizing the news...")

        if not wants_removal and not is_video_ask and not is_data_ask:
            is_searching = bool(re.search(r'\b(search|find|look for)\b', text_clean))
            topic = extract_topic(req.message)

            # 2. Clock (word boundary: "clockwork" must not match)
            is_clock = bool(re.search(r'\bclock\b', text_clean))
            has_timezone = any(tz in text_clean for tz in ("in ", "for ", "time ", "zone", "city", "york", "london", "tokyo", "paris", "sydney", "canada"))
            if is_clock and not has_timezone:
                return spawn_widget_stream("clock", "clock", {})

            # 3. Music — asking for a music widget always means "start playing
            # it", so autoplay unconditionally instead of requiring the word
            # "play" in the request.
            has_custom_url = "http" in text_clean or "www" in text_clean
            if re.search(r'\b(music|player|radio)\b', text_clean) and not has_custom_url:
                genre = extract_music_genre(req.message) or "lofi"
                return spawn_widget_stream("mini_music_player", "music",
                                           {"genre": genre, "autoplay": True})

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

            # 6. MAP — geo/location queries ("where are the fires in California",
            #    "map of X") get an interactive map with markers geocoded from a
            #    web search, not a text card.
            if MAP_ASK_RE.search(text_clean):
                return spawn_widget_stream(
                    "map", "map",
                    config_builder=lambda: build_map_config(req.message),
                    status="finding the locations and building your map...",
                )

            # 7. ANSWER — recipes, how-tos, definitions, "what/who/when is X".
            #    Synthesised into a readable answer card (Markdown answer + demoted
            #    sources) instead of dumping the user into the ~30-60s agent loop
            #    that returns a wall of links.
            if ANSWER_ASK_RE.search(text_clean):
                return spawn_widget_stream(
                    "data_card", "answer",
                    config_builder=lambda: build_answer_config(req.message),
                    status="researching and writing your answer...",
                )

        # Start loading history

        history = database.get_session_messages(req.session_id)

        # Adopting the client's snapshot is _run_turn's job now (it only does so
        # when no other turn is in flight, so a concurrent turn's stale snapshot
        # can't undo a widget that just landed).
        canvas_summary = get_canvas_summary(
            get_session_canvas(req.session_id) or req.current_canvas)

        # Build system prompt with canvas context.
        #
        # Kept deliberately short and free of internal contradictions. The previous
        # version was ~9.3k chars and opened with "NEVER output any conversational
        # text ... the system will crash", then closed by requiring "ONE short
        # sentence saying what you added" — a model asked to reconcile those two
        # spends the turn deciding whether it is allowed to speak, and often resolves
        # it by talking. Instruction-following also decays with instruction count
        # (arXiv 2507.11538), and what gets dropped is whatever sits in the middle,
        # so the act-now rules go first and the per-widget config contracts have been
        # moved into the canvas_add_widget tool description, where the model reads
        # them at the point of use instead of carrying them through the whole turn.
        SYSTEM_PROMPT = (
            "You run a live dashboard canvas. You act by calling tools. You never describe what you would do.\n\n"
            "HOW TO ACT\n"
            "1. Call the tool immediately. Write no preamble, no plan, no 'let me...' before a tool call.\n"
            "2. Never ask for clarification. Take the most reasonable reading and go — 'pull up a video' with no topic means pick one and search.\n"
            "3. Fetch the data before you render it. The config you pass IS the finished content: it renders server-side, so never write 'Loading...' and never write JavaScript that fetches.\n"
            "4. Stop when the widget is up. canvas_add_widget returning success means it is already on the user's screen — do not call it again, do not verify with canvas_read_dom, do not re-plan.\n"
            "5. Then write ONE sentence (max 20 words) saying what you added. That sentence is the only prose you write all turn.\n\n"
            "ROUTING — pick one and execute it:\n"
            "- stock, share price, ticker, crypto → mcp__lazy-tool-service__html_notes_stock_history, then canvas_add_widget(widget_type='stock_card')\n"
            "- stock/company/market NEWS, or 'find me stocks' (no specific ticker yet) → mcp__lazy-tool-service__html_notes_stock_news; its 'matches' array gives you tickers to feed into html_notes_stock_history. Never use html_notes_stock_history for news (prices only) or html_notes_web_search for stock news (this is cleaner).\n"
            "- sports scores, fixtures, standings → mcp__lazy-tool-service__html_notes_sports_scores, then canvas_add_widget(widget_type='scoreboard')\n"
            "- video, watch, clip, live stream → mcp__lazy-tool-service__html_notes_youtube_search, then canvas_add_widget(widget_type='youtube_player'). order='live' for a live stream, order='date' for latest news. 'cnn live news' is a video request, not headlines.\n"
            "- weather, forecast, temperature → mcp__lazy-tool-service__html_notes_get_weather(location='<city>'), then canvas_add_widget(widget_type='weather', config={'location':'<city>'}) — config is JUST the location; the server fills in the conditions and 5-day forecast. Never render weather as a data_card and never web-search for it.\n"
            "- news, headlines, 'top stories' → mcp__lazy-tool-service__html_notes_news(topic='<topic>', or topic='' for top stories). It returns a ready data_card config of current stories, each with a photo, a tightened headline and a written summary. Then call canvas_add_widget(widget_type='data_card', config={'news_topic': '<same topic>'}) — the server rehydrates the stories, so do NOT re-type them. Do NOT use html_notes_web_search for news (it returns news-site homepages, not stories, and no photos).\n"
            "- facts, recipes, how-tos, 'what/who/when is X', comparisons → mcp__lazy-tool-service__html_notes_web_search(query='<the question>'), then canvas_add_widget(widget_type='data_card', config={'search_query': '<the SAME query>'}). The server reads the top pages, WRITES a summarised Markdown answer (a recipe becomes ingredients+steps, a definition a short paragraph) and attaches the pages as sources. Do NOT hand-build items and do NOT re-type the results — just pass the query back.\n"
            "- WHERE something is / a map / locations ('where are the fires in California', 'map of X') → canvas_add_widget(widget_type='map', config={'map_query': '<the query>'}). The server web-searches, geocodes the places and drops the markers — do NOT type coordinates.\n"
            "- picture of X → canvas_add_widget(widget_type='image')\n"
            "- clock, checklist, notes, music, embedded app → canvas_add_widget with that widget_type\n"
            "- timer, countdown, pomodoro → canvas_add_widget(widget_type='clock', config={'mode':'countdown','duration_seconds':N}); stopwatch → config={'mode':'stopwatch'}; 'time in <city>' → config={'mode':'clock','timezone':'<IANA tz>'}. NEVER spawn a plain clock for a timer request.\n"
            "- EDIT an existing widget (change a timer's duration, a clock's timezone, a chart's data, swap the stock) → call canvas_add_widget AGAIN with the SAME widget_id from CURRENT CANVAS and the full updated config. It re-renders that widget in place — no duplicate. This is the ONLY way to change a clock/timer/stock/scoreboard/chart: canvas_modify_dom CANNOT rebuild these (they are server-rendered) and will break them. Example: to set the timer #clock-1 to 30s → canvas_add_widget(widget_type='clock', widget_id='clock-1', config={'mode':'countdown','duration_seconds':30}).\n"
            "- REMOVE something, or tweak a hand-built custom widget → mcp__lazy-tool-service__canvas_modify_dom(css_selector='#<widget-id>', action='remove'|'replace') using an id from CURRENT CANVAS\n\n"
            "ANSWER FROM DATA, NEVER FROM MEMORY\n"
            "You know nothing current. If the answer is not already in this conversation, call html_notes_web_search before answering — never claim you cannot find or cannot access something without having searched first.\n"
            "For a data_card, prefer the search_query path above: pass config={'search_query': '<query>'} and let the server write the summarised answer with sources. Only hand-build config.items when you have specific structured rows that no search summary would capture — and then every item still needs a 'description' with the real information, never just a title and a link.\n\n"
            "WIDGETS COEXIST. Adding one never removes the others. The exceptions are youtube_player and mini_music_player: only one of each can play, so a new one automatically swaps out the old — just add it, do not remove first.\n\n"
            f"CURRENT CANVAS:\n```markdown\n{canvas_summary}\n```"
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
            "mcp__lazy-tool-service__html_notes_news",
            "mcp__lazy-tool-service__html_notes_stock_history",
            "mcp__lazy-tool-service__html_notes_stock_news",
            "mcp__lazy-tool-service__html_notes_sports_scores",
            "mcp__lazy-tool-service__html_notes_get_weather",
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
            # Tailor-made persona in the gateway (personas/clients/
            # HtmlNotesPersona.ts): scopes the run to the widget tool set with
            # no forced core/orchestrator tools in the system prompt, and
            # defaults thinking off. Without it this ran as the generic "Omni
            # Agent" — ~35 unrelated tools documented into every turn and ~180
            # <think> chunks streamed before the first tool call.
            "agent": "HTML_NOTES",
            # Belt and braces with the persona's thinkingDefault: a widget
            # router gains nothing from chain-of-thought and the user is
            # watching a spinner while it streams.
            "thinkingEnabled": False,
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

            # Once the widget is on screen the turn is, from the user's point of
            # view, over. Measured: the widget landed at ~8s and the model then
            # spent another 60s reasoning and re-adding it. So we stop reading the
            # agent's stream as soon as a canvas mutation commits.
            #
            # The exception is a request that asks for more than one thing ("a
            # clock and a chart") — closing after the first widget would drop the
            # second, so those are allowed to run the loop out.
            wants_multiple = bool(re.search(r'\band\b|\balso\b|,|\bthen\b', text_clean))
            canvas_settled = False

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

                        # Rehydrate data-heavy widgets from the tool result the
                        # model just fetched. It only has to name the subject —
                        # {"symbol": "AMZN"} — instead of hand-typing the whole
                        # snapshot back to us, which was costing ~55s a turn.
                        if widget_type == "stock_card" and not config.get("values"):
                            cached = get_cached_tool_result(f"stock:{config.get('symbol', '')}")
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated stock_card for {config.get('symbol')}")
                                config = {**cached, **{k: v for k, v in config.items() if v}}
                        elif widget_type == "scoreboard" and not config.get("events"):
                            cached = (get_cached_tool_result(f"scores:{config.get('league', '')}")
                                      or get_cached_tool_result(f"scores:{config.get('title', '')}"))
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated scoreboard for {config.get('league')}")
                                config = {**cached, **{k: v for k, v in config.items() if v}}
                        elif widget_type == "weather" and not config.get("current"):
                            cached = get_cached_tool_result(f"weather:{str(config.get('location', '')).lower()}")
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated weather for {config.get('location')}")
                                # Cache wins: it carries the resolved place label + full forecast,
                                # config only carried the bare location string the model typed.
                                config = {**config, **cached}
                        elif (widget_type == "data_card" and not config.get("items")
                              and ("news_topic" in config or "topic" in config)):
                            # A news data_card from html_notes_news: the model names
                            # the topic, the server supplies the summarized stories
                            # (with photos) it already fetched and cached.
                            topic = str(config.get("news_topic", config.get("topic", ""))).strip()
                            cached = get_cached_tool_result(f"news:{topic}")
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated news data_card for {topic!r}")
                                config = {**cached, **{k: v for k, v in config.items()
                                                       if v and k not in ("news_topic", "topic")}}
                        elif (widget_type == "data_card" and not config.get("answer")
                              and not config.get("items")
                              and (config.get("search_query") or config.get("answer_query"))):
                            # A general answer data_card: the model names the query,
                            # the server reads the top pages, WRITES a summarised
                            # Markdown answer and attaches the pages as sources — the
                            # model never hand-builds the wall-of-links card. Reuses
                            # the html_notes_web_search result cache when present.
                            aq = str(config.get("search_query") or config.get("answer_query") or "").strip()
                            cached_hits = get_cached_tool_result(f"search:{aq}")
                            answer_cfg = await build_answer_config(aq, results=cached_hits)
                            logger.info(f"[WIDGET INJECTOR] Synthesised answer data_card for {aq!r}")
                            config = {**answer_cfg, **{k: v for k, v in config.items()
                                                       if v and k not in ("search_query", "answer_query")}}
                        elif (widget_type == "map" and not config.get("markers")
                              and config.get("map_query")):
                            # A map from a query: the server searches, geocodes the
                            # places the LLM pulled out, and bakes the markers — the
                            # model never hand-types coordinates.
                            mq = str(config.get("map_query", "")).strip()
                            map_cfg = await build_map_config(mq)
                            logger.info(f"[WIDGET INJECTOR] Built map for {mq!r} ({len(map_cfg.get('markers', []))} markers)")
                            config = {**map_cfg, **{k: v for k, v in config.items()
                                                    if v and k != "map_query"}}

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

                        # A music widget is always meant to be listened to —
                        # don't rely on the model remembering to set autoplay.
                        if widget_type == "mini_music_player":
                            config["autoplay"] = True

                        def _add(soup):
                            replaced = False
                            # Media widgets (video, music) are players: a new one
                            # replaces whatever's already playing instead of stacking
                            # up a second player — but request-order-safe, so a slower
                            # older ask can't overwrite a newer one.
                            if _place_media_widget(soup, widget_type, widget_id, config, req_seq):
                                replaced = True

                            # A retried tool call with the same widget_id must not
                            # duplicate the widget — replace it in place instead.
                            if not replaced:
                                existing = soup.find(id=widget_id)
                                if existing is not None:
                                    existing.replace_with(BeautifulSoup(
                                        render_widget(widget_type, widget_id, config), 'html.parser'))
                                    replaced = True

                            if replaced:
                                logger.info("[WIDGET INJECTOR] Replaced existing widget in-place")
                                return

                            snippet = BeautifulSoup(
                                render_widget(widget_type, widget_id, config), 'html.parser')
                            _stamp_media_seq(snippet, widget_type, req_seq)
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

                                if canvas_settled:
                                    break

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
                                        if not wants_multiple:
                                            canvas_settled = True
                                            break

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
                                            if not wants_multiple:
                                                canvas_settled = True
                                                break
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

                            # The `break` above only escapes the inner line loop —
                            # without this the outer chunk loop keeps pulling the
                            # agent's stream and the turn runs to completion anyway.
                            if canvas_settled:
                                break

            except Exception as e:
                logger.error(f"Prism SSE proxy error: {e}")
                yield f'data: {json.dumps({"type": "error", "message": f"Connection error: {str(e)}"})}\n\n'

            if canvas_settled:
                logger.info("[FAST LOOP] Closed agent stream after canvas commit")
                # We cut the model off before it wrote its closing line, so the chat
                # bubble would otherwise be empty. The widget IS the answer here.
                if not final_text.strip():
                    final_text = "Added it to your canvas."
                    yield f'data: {json.dumps({"type": "chunk", "content": final_text})}\n\n'

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
            query = a.get("query", "")
            results = await web_search(query, limit=int(a.get("limit", 6)))
            if not results:
                return {"results": [], "count": 0,
                        "message": "Search returned nothing. Retry with a shorter, simpler query."}
            # Cache the raw hits so a following canvas_add_widget(data_card,
            # {search_query: query}) can synthesise the answer WITHOUT re-searching.
            cache_tool_result(f"search:{query.strip()}", results)
            return {"results": results, "count": len(results),
                    "hint": "To show this as a card, call canvas_add_widget(widget_type='data_card', "
                            f"config={{'search_query': '{query}'}}). The server will read the top pages, "
                            "write a summarised answer and attach these as sources — do not re-type them."}

        elif t == "html_notes_read_page":
            return await read_web_page(a.get("url", ""), max_chars=int(a.get("max_chars", 6000)))

        elif t == "html_notes_news":
            # Returns a ready-to-render data_card config (photo + headline +
            # summary per story). Cached so canvas_add_widget can rehydrate from
            # just {"news_topic": "..."} instead of the model re-typing every item.
            topic = (a.get("topic") or a.get("query") or "").strip()
            config = await build_news_config(topic)
            cache_tool_result(f"news:{topic}", config)
            return config

        elif t == "html_notes_stock_history":
            result = await stock_snapshot(a.get("symbol", ""), a.get("range", "1mo"))
            if not result.get("is_error"):
                cache_tool_result(f"stock:{a.get('symbol', '')}", result)
            return result

        elif t == "html_notes_stock_news":
            return await stock_news(a.get("query", ""), limit=int(a.get("limit", 8)))

        elif t == "html_notes_get_weather":
            location = a.get("location", "")
            result = await get_weather(location, units=a.get("units", "fahrenheit"))
            if not result.get("is_error"):
                # Key on the location the model typed so the widget injector can
                # rehydrate config={"location": "<same string>"} without a re-fetch.
                cache_tool_result(f"weather:{str(location).lower()}", result)
            return result

        elif t == "html_notes_sports_scores":
            result = await sports_scores(a.get("league", ""))
            if not result.get("is_error"):
                cache_tool_result(f"scores:{a.get('league', '')}", result)
            return result

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

# The raw static/index.html hard-codes its asset refs as `index.js?v=3.0`,
# `js/widgets.js?v=1.4` etc. Only the `/` route (read_root) rewrites those to a
# per-deploy mtime fingerprint; a browser that loads `/static/index.html`
# directly (an old bookmark from when the UI was served from /static/) gets the
# frozen `?v=3.0` URLs, so it reuses its long-cached index.js FOREVER — even on
# refresh — and runs frontend code from before the reconcile/SSE/paint fixes.
# The visible symptom is "widgets don't update live, only after a refresh".
# Funnel every UI entry point through `/` so the fingerprinted assets are the
# only ones a browser ever sees. Registered BEFORE the /static mount so it wins.
@app.get("/static/index.html", include_in_schema=False)
@app.get("/static/", include_in_schema=False)
def _redirect_stale_ui_entrypoint():
    # 307 (not 301) + no-store so browsers never cache the redirect itself and a
    # stranded tab is rescued the next time it loads.
    return RedirectResponse(url="/", status_code=307,
                            headers={"Cache-Control": "no-store"})


@app.get("/widgets/map", include_in_schema=False)
def widget_map(d: str = ""):
    """Standalone Leaflet page for the map widget's <iframe>. The marker data
    rides in `d` as base64url JSON (built by factory.render_map). Rendered as its
    own document so its <script> runs — the canvas DOMPurify strips inline scripts,
    which is why the map is an iframe rather than inline markup."""
    payload = {"center": {"lat": 39.5, "lon": -98.35}, "zoom": 4, "markers": []}
    if d:
        try:
            raw = _base64.urlsafe_b64decode(d.encode("ascii"))
            payload = map_payload(json.loads(raw))  # re-sanitise; never trust the URL
        except Exception as e:
            logger.warning(f"/widgets/map bad payload: {e}")
    return HTMLResponse(map_document_html(payload),
                        headers={"Cache-Control": "public, max-age=3600"})


# Mount UI static files at root
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_CACHE_BUSTED_ASSETS = ("index.js", "index.css", "js/widgets.js")
_ASSET_QUERY_RE = re.compile(r'(index\.js|index\.css|js/widgets\.js)\?v=[^"\']*')


def _asset_version() -> str:
    """Fingerprint the frontend assets by mtime.

    The ?v= tokens in index.html were hand-written and never got bumped, so a
    returning browser kept running whichever index.js it had cached from an
    earlier deploy — new server behaviour talking to old client code. Deriving
    the token from the files themselves means it can't drift again.
    """
    stamps = []
    for name in _CACHE_BUSTED_ASSETS:
        try:
            stamps.append(str(os.path.getmtime(os.path.join("app/static", name))))
        except OSError:
            pass
    return hashlib.sha1("|".join(stamps).encode()).hexdigest()[:10]


@app.get("/")
def read_root():
    html = pathlib.Path("app/static/index.html").read_text()
    html = _ASSET_QUERY_RE.sub(rf'\1?v={_asset_version()}', html)
    # index.html references its assets relatively ("index.js", "lib/…"), which
    # resolved fine when the page was served from /static/. Serving it at / makes
    # those resolve to /index.js and 404 — no JS, no app. <base> repoints every
    # relative URL back at /static/ without touching the markup. Absolute paths
    # (/session/message, /models) are unaffected.
    html = html.replace("<head>", '<head>\n    <base href="/static/">', 1)
    # The shell itself must never be cached, or the browser keeps serving an old
    # copy carrying the old ?v= tokens and the whole scheme is pointless.
    return Response(content=html, media_type="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})
