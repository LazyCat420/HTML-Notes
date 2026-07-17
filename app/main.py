import httpx
import logging
import random
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from html import unescape as _html_unescape

# Enriched YouTube layer (views/duration/age/verified/live signals + language +
# 4-axis heuristic scorer). Shared with the bench harness; the live scraper below
# is a thin wrapper that fetches, scores, and diversifies on top of it.
from app.youtube_search import (
    fetch_videos as _yt_fetch_videos,
    score_videos as _yt_score_videos,
    detect_language as _yt_detect_language,
    clean_query as _yt_clean_query,
    Intent as _YtIntent,
)

# Needed up here, not in the import block further down: the helper functions
# below are defined before it, and their annotations are evaluated at def time.
from typing import Any, Dict, List, Optional

def _diversify_by_channel(videos: list, per_channel: int = 2) -> list:
    """Cap how many hits from one channel survive, preserving score order. A broad
    ask ("cooking videos") otherwise returns five clips from the same channel — the
    literal 'same videos over and over' complaint. Keeps the best `per_channel` per
    channel, then appends the rest so the pool never shrinks below what was asked."""
    kept, overflow, counts = [], [], {}
    for v in videos:
        ch = (v.get("channel") or "").lower()
        if ch and counts.get(ch, 0) >= per_channel:
            overflow.append(v)
            continue
        counts[ch] = counts.get(ch, 0) + 1
        kept.append(v)
    return kept + overflow


# Queries where the heuristic scorer structurally misses and a one-shot LLM rerank
# earns its ~1s (per the bench): intent-ambiguous format words + explicit-language
# asks. "nba highlights" heuristically picks a recent awards clip; the LLM picks
# actual game highlights.
_HARD_VIDEO_RE = re.compile(
    r'\b(highlights?|news|best|top\s*\d*|vs\.?|versus|review|tutorial|how\s?to|'
    r'recap|explained|comparison|ranked)\b', re.IGNORECASE)


async def _llm_rerank_videos(query: str, videos: list) -> list:
    """One-shot LLM rerank of the top candidates for a 'hard' query. Returns them
    reordered best-first; on ANY failure returns the input unchanged, so the
    heuristic order always stands as a floor."""
    top = videos[:8]
    if len(top) < 2:
        return videos
    lines = []
    for i, v in enumerate(top):
        meta = []
        if v.get("channel"):
            meta.append(v["channel"])
        if v.get("duration_sec"):
            meta.append(f"{v['duration_sec'] // 60}m")
        if v.get("age_days") is not None:
            meta.append(f"{int(v['age_days'])}d old")
        if v.get("is_live"):
            meta.append("LIVE")
        lines.append(f'[{i}] {v.get("title","")} ({", ".join(meta)})')
    data = await fast_llm_json(
        'You pick the best YouTube results for a request. Return ONLY JSON:\n'
        '{"order": [<candidate indices, best first>]}\n'
        f'REQUEST: "{query}"\n\nCANDIDATES:\n' + "\n".join(lines) + '\n\n'
        "Judge by how well each title matches the REQUEST's real intent (a "
        '"highlights" ask wants game highlights not an awards show; a language '
        "request wants that language; a review wants a review). Prefer real, "
        "watchable videos over clickbait. List every index once, best first.",
        max_tokens=200,
    )
    order = (data or {}).get("order")
    if isinstance(order, list):
        idxs = [i for i in order if isinstance(i, int) and 0 <= i < len(top)]
        if idxs:
            picked = [top[i] for i in idxs]
            rest = [v for j, v in enumerate(top) if j not in idxs] + videos[8:]
            return picked + rest
    return videos


async def search_youtube_videos(query: str, limit: int = 5, order: str = "relevance",
                                rerank: bool = False) -> list:
    """Enriched, scored YouTube search. Returns dicts with the SAME keys the old
    scraper did (video_id, id, title, channel) PLUS the parsed signals (views,
    duration_sec, age_days, verified, is_live, is_short, score), best-first.

    Over the old title-only scrape it: parses each result's real signals, ranks on
    intent/authority/freshness/watchability (app/youtube_search.py), blends in
    date-sorted results for a recency ask, and caps per-channel so a broad query
    stops returning the same handful of clips. order="date"/"live" pass through.

    `rerank=True` adds a one-shot LLM rerank, but ONLY on 'hard' queries (ambiguous
    format words / explicit language) where the bench showed it helps — clear
    queries keep the zero-latency heuristic order.
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        lang, explicit = _yt_detect_language(q)
        cleaned = _yt_clean_query(q, explicit_lang=explicit) or q
        want_fresh = bool(RECENCY_RE.search(q.lower()))
        intent = _YtIntent(query=cleaned, lang=lang, want_fresh=want_fresh,
                           want_live=(order == "live"), explicit_lang=explicit)
        # Fetch a deeper pool than requested so scoring + channel-diversity have
        # room to work; a recency ask also blends in date-sorted hits.
        pool = await _yt_fetch_videos(cleaned, limit=max(limit * 3, 15),
                                      order=order, lang=lang)
        if want_fresh and order == "relevance":
            pool += await _yt_fetch_videos(cleaned, limit=10, order="date", lang=lang)
        seen, deduped = set(), []
        for v in pool:
            if v.video_id and v.video_id not in seen:
                seen.add(v.video_id)
                deduped.append(v)
        scored = _yt_score_videos(deduped, intent)
        # Live asks keep YouTube's own order (the LIVE filter already did the work).
        ranked = [v.to_dict() for v in scored]
        if order != "live":
            ranked = _diversify_by_channel(ranked, per_channel=2)
        # Escalate to the LLM only where it pays off — a hard query with real choice.
        if rerank and order != "live" and (explicit or _HARD_VIDEO_RE.search(q)):
            ranked = await _llm_rerank_videos(q, ranked)
        return ranked[:max(limit, 1)]
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
            thumbs = (item.get("thumbnail") or {}).get("resolutions") or []
            news.append({
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "published": (datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
                              .strftime("%Y-%m-%d %H:%M UTC") if stamp else None),
                "url": item.get("link"),
                "image": (thumbs[-1].get("url") or "") if thumbs else "",
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


# Keys that carry only an unresolved QUERY/topic — not real content. A data-ish
# widget holding only these never rehydrated from its data source, so rendering it
# produces the raw key/value dump the user sees as a broken empty card
# ("NEWS_TOPIC | stock market").
_QUERY_ONLY_KEYS = {"news_topic", "topic", "search_query", "map_query", "query",
                    "symbol", "ticker", "location", "url"}
# Keys that DO carry real, renderable content.
_CONTENT_KEYS = ("items", "sources", "answer", "content", "values", "markers",
                 "events", "articles", "results", "price", "technicals", "image")


def _widget_is_degenerate(widget_type: str, config: dict) -> bool:
    """True when a data-ish widget carries a topic/query but no real content, so
    it would render as an empty shell / raw-config dump. Scoped to the widget
    types whose renderers lack their own graceful empty state (weather, map,
    image, checklist, youtube all handle empty results themselves)."""
    if not isinstance(config, dict) or widget_type not in (
            "data_card", "stock_card", "scoreboard", "chart"):
        return False
    if any(config.get(k) for k in _CONTENT_KEYS):
        return False
    meaningful = [k for k, v in config.items()
                  if v and k not in ("title", "subtitle", "icon")]
    return not meaningful or all(k in _QUERY_ONLY_KEYS for k in meaningful)


def _graceful_fallback_config(config: dict) -> dict:
    """A readable 'couldn't load this' card — never a raw key dump or blank."""
    topic = ""
    for k in ("news_topic", "topic", "search_query", "map_query", "query",
              "symbol", "ticker", "title"):
        v = config.get(k) if isinstance(config, dict) else None
        if v:
            topic = str(v).strip()
            break
    icon = (config.get("icon") if isinstance(config, dict) else None) or "info"
    return {
        "title": topic.title() if topic else "No results",
        "icon": icon,
        "content": (f"Couldn't pull up **{topic or 'that'}** right now — the "
                    f"source came back empty. Try rephrasing, or ask again in a moment."),
    }


def render_widget(widget_type: str, widget_id: str, config: dict) -> str:
    """Single choke point for widget HTML: coerce the type, then render."""
    widget_type, config = coerce_widget_type(widget_type, widget_id, config)
    # Universal safety net: a data widget with only a topic/query and no content
    # would render as a raw key/value dump — a card that looks broken. Downgrade
    # it to a readable fallback so the canvas never shows an empty shell, no
    # matter how the config arrived (fast-path, agent, or cache-miss rehydration).
    if _widget_is_degenerate(widget_type, config):
        logger.info(
            "[WIDGET GUARD] degenerate %s (%s) keys=%s -> fallback card",
            widget_type, widget_id,
            list(config.keys()) if isinstance(config, dict) else config)
        widget_type, config = "data_card", _graceful_fallback_config(config)
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
from app.config import PORT, PRISM_URL, LAZY_AGENT_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL, TTS_SERVICE_URL, MUSIC_PLAYER_URL, SCRAPER_SERVICE_URL, VAULT_SERVICE_URL, VAULT_SERVICE_TOKEN
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
from app.widgets.factory import generate_widget_html, _host_of, map_document_html, map_payload, _render_markdown, esc
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

# Container 2nd-class → the widget_type the fast lane / router / add_widget uses.
# Keeps the summary's type names identical to canvas_add_widget's widget_type so a
# reuse decision ("there's already a map") maps straight to an id the caller can
# pass back. Without this, maps/weather/products/stock were all reported as
# "custom", so the router couldn't tell one was already open and spawned a second.
_CANVAS_CLASS_TYPE = {
    "map-widget": "map", "weather-widget": "weather", "data-card": "data_card",
    "image-widget": "image", "products-widget": "products", "chart-widget": "chart",
    "scoreboard": "scoreboard",
}
_CANVAS_XDATA_TYPE = {
    "checklistWidget": "checklist", "clockWidget": "clock", "notesWidget": "notes",
    "musicPlayerWidget": "mini_music_player", "youtubePlayerWidget": "youtube_player",
    "stockCardWidget": "stock_card",
}


def _classify_canvas_widget(card) -> str:
    """The widget_type of a canvas node, from its container class then its x-data.
    Returns 'custom' only for genuinely hand-built widgets."""
    for cls in card.get("class", []):
        if cls in _CANVAS_CLASS_TYPE:
            return _CANVAS_CLASS_TYPE[cls]
    xdata = card.get("x-data", "") or ""
    for marker, wtype in _CANVAS_XDATA_TYPE.items():
        if marker in xdata:
            return wtype
    return "custom"


def _iter_canvas_widgets(html: str):
    """Yield (widget_id, widget_type, title) for every widget on the canvas, most
    recent last (DOM order). Shared by the summary and the reuse lookup."""
    if not html or not html.strip() or html == "Canvas is empty.":
        return
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select(".glass-card, .widget-container"):
        title_el = card.select_one(".glass-card-title, h3, h2, h4")
        title = title_el.get_text(strip=True) if title_el else ""
        yield card.get("id", "unknown"), _classify_canvas_widget(card), title


def find_existing_widget(session_id: str, widget_type: str) -> Optional[str]:
    """The id of the most-recent widget of `widget_type` on the session canvas, or
    None. Lets a singleton ask (a second map, a refreshed weather) UPDATE the widget
    in place — passing its id back re-renders it rather than stacking a duplicate."""
    found = None
    try:
        for wid, wtype, _title in _iter_canvas_widgets(get_session_canvas(session_id)):
            if wtype == widget_type and wid and wid != "unknown":
                found = wid  # last match wins — the most recently added
    except Exception as e:
        logger.warning(f"find_existing_widget failed: {e}")
    return found


# Types that should exist at most once on the canvas: a new ask UPDATES the open
# one instead of adding a second. Media (video/music) already swap via
# _place_media_widget; these are the data widgets that were stacking duplicates.
SINGLETON_WIDGET_TYPES = {"map", "weather"}


def get_canvas_summary(html: str) -> str:
    """Raw canvas HTML → a tiny, token-efficient inventory the router/agent reads to
    stay DOM-aware. Each line names the widget's id, its real widget_type, and what
    it shows, so a follow-up can reuse an id instead of spawning a duplicate."""
    if not html or html.strip() == "" or html == "Canvas is empty.":
        return "Canvas is currently empty."
    try:
        widgets = []
        for wid, wtype, title in _iter_canvas_widgets(html):
            desc = f' "{title}"' if title else ""
            widgets.append(f"- #{wid} · {wtype}{desc}")
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
    "give", "want", "see", "put", "add", "open", "stream", "streams", "streaming",
    "live", "livestream", "livestreams",
}


def clean_video_query(text: str) -> str:
    """Strip medium words so the search hits the subject.
    "pull up a fifa news video" → "fifa news"."""
    cleaned = re.sub(r'[^\w\s]', ' ', (text or '').lower())
    kept = [w for w in cleaned.split() if w not in VIDEO_FILLER]
    return " ".join(kept).strip() or (text or "").strip()


def pick_varied_video(hits: list, k: int = 5, exclude_ids: set = None):
    """Choose a video from the top-`k` results at random, for VARIETY.

    A broad ask ("a cookie recipe video", "funny cats") always returned the same
    #1 YouTube hit, which is boring on repeat. Picking from the top handful keeps
    the result relevant (they're all strong matches for the query) while making a
    second identical ask land on something different. Returns
    (chosen_hit, other_ids) where other_ids are the remaining results IN RELEVANCE
    ORDER — so the widget's embed-error fallback still hops to the next-best video,
    not another random one.

    `exclude_ids` drops videos already shown this session so a repeat ask ("another
    one") lands on something genuinely new; if EVERY hit was already shown we ignore
    the exclusion rather than return nothing.

    Deterministic callers (live streams, a named channel) must NOT use this: they
    want the single canonical result, so they keep indexing hits[0] directly.
    """
    if not hits:
        return None, []
    fresh = [h for h in hits if h.get("video_id") not in exclude_ids] if exclude_ids else hits
    fresh = fresh or hits  # all already seen → fall back to the full list
    pool = fresh[: max(1, k)]
    chosen = random.choice(pool)
    cid = chosen.get("video_id")
    # Fallback candidates: fresh ones first (in order), so an embed error also hops
    # to something unseen before falling back to the rest.
    others = [v["video_id"] for v in fresh if v.get("video_id") and v.get("video_id") != cid]
    others += [v["video_id"] for v in hits
               if v.get("video_id") and v.get("video_id") != cid and v["video_id"] not in others]
    return chosen, others


# ── Persistent video/channel dislikes ───────────────────────────────────────
# Backed by the agent_memory table so a block survives sessions and restarts.
# Loaded once into in-memory sets so the hot search path never touches the DB;
# both are updated together when a new block is added.
_BLOCKED_VIDEO_CAT = "blocked_video"
_BLOCKED_CHANNEL_CAT = "blocked_channel"
_blocked_video_ids: set = set()
_blocked_channels: set = set()   # stored lowercased for case-insensitive match


def _load_blocklists() -> None:
    try:
        _blocked_video_ids.update(
            m["key"] for m in database.list_agent_memory(_BLOCKED_VIDEO_CAT))
        _blocked_channels.update(
            (m["key"] or "").lower() for m in database.list_agent_memory(_BLOCKED_CHANNEL_CAT))
    except Exception as e:
        logger.warning(f"could not load video blocklists: {e}")


def block_video(video_id: str, reason: str = "") -> None:
    if not video_id:
        return
    _blocked_video_ids.add(video_id)
    database.add_agent_memory(_BLOCKED_VIDEO_CAT, video_id, reason or None)


def block_channel(channel: str, reason: str = "") -> None:
    if not channel:
        return
    _blocked_channels.add(channel.lower())
    database.add_agent_memory(_BLOCKED_CHANNEL_CAT, channel, reason or None)


def filter_blocked_videos(hits: list) -> list:
    """Drop any hit the user has permanently disliked (by video or by channel).
    If EVERY hit is blocked, returns the originals rather than nothing — a video
    the user half-dislikes still beats an empty player."""
    if not hits:
        return hits
    kept = [h for h in hits
            if h.get("video_id") not in _blocked_video_ids
            and (h.get("channel") or "").lower() not in _blocked_channels]
    return kept or hits


# The last video shown per session, so a follow-up ("this one sucks, find
# another", "this channel sucks") knows what to exclude and what query to
# re-run. In-memory is fine: it only needs to survive within a conversation,
# while the block itself is what persists in the DB.
_session_current_video: Dict[str, dict] = {}

# A rolling window of video ids already shown this session, so a repeat ask lands
# on something new instead of recycling the same top hit. Bounded so it can't grow
# without limit; oldest ids age out and can reappear later (fine — variety, not a
# permanent block, which is what the DB-backed blocklist is for).
_session_shown_videos: Dict[str, deque] = {}
_SHOWN_HISTORY = 24


def _shown_video_ids(session_id: str) -> set:
    """Video ids already shown this session — pass to pick_varied_video as
    exclude_ids so a follow-up doesn't repeat one."""
    return set(_session_shown_videos.get(session_id) or ())


def _remember_current_video(session_id: str, hit_or_cfg: dict, query: str) -> None:
    if not session_id:
        return
    vid = hit_or_cfg.get("video_id")
    _session_current_video[session_id] = {
        "video_id": vid,
        "channel": hit_or_cfg.get("channel"),
        "query": query or "",
    }
    if vid:
        hist = _session_shown_videos.setdefault(session_id, deque(maxlen=_SHOWN_HISTORY))
        if vid not in hist:
            hist.append(vid)


_load_blocklists()


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
# "market news" without a stock word — still a finance-news ask, routed with them.
MARKET_WORD_RE = re.compile(r'\bmarkets?\b')

# TRIP planning — "plan a trip to Japan", "3 days in Rome", "Kyoto itinerary",
# "things to do in Lisbon". High-precision: requires an explicit trip/itinerary word
# or an "N days in <place>" shape, so it never steals a plain "map of X".
TRIP_ASK_RE = re.compile(
    r'\bitinerary\b'
    r'|\b(?:plan|planning)\b[^.?!]{0,40}\b(?:trip|vacation|holiday|getaway)\b'
    r'|\b(?:trip|vacation|getaway)\s+(?:to|for|in)\s+\w'
    r'|\b\d+\s*(?:day|days|week|weeks)\s+(?:in|trip)\b'
    r'|\bthings\s+to\s+do\s+in\b',
    re.IGNORECASE)

# SHOPPING / product recommendations — "good outdoor shoes", "best budget laptop",
# "where to buy a tent", "gift for a hiker". High-precision: an explicit buy/shop
# phrase, or a quality adjective sitting near a product-category noun. Keeps
# "best restaurants in NYC" (a map/POI) and "stock price" out.
SHOP_ASK_RE = re.compile(
    r'\b(?:shop(?:ping)?\s+for|where\s+(?:can\s+i\s+|to\s+)buy|looking\s+to\s+buy'
    r'|want\s+to\s+buy|gift\s+(?:for|idea))\b'
    r'|\b(?:best|good|top|great|recommend(?:ed)?|budget|cheap|affordable|quality|decent)\b'
    r'[^.?!]{0,30}\b(?:shoes|boots|sneakers|sandals|laptops?|headphones|earbuds|'
    r'phones?|smartphones?|watch|smartwatch|backpacks?|jackets?|coats?|cameras?|tvs?|'
    r'monitors?|keyboards?|mouse|mattress|chairs?|desks?|bikes?|bicycles?|tents?|'
    r'sleeping\s+bags?|cookware|blender|vacuum|gifts?|gear|sunglasses|wallets?|'
    r'luggage|suitcases?|speakers?|drones?)\b',
    re.IGNORECASE)

# "cnn live news" is a thing to WATCH, but it contains "news", so DATA_ASK_RE
# claimed it and the model was told to build a data_card — a text list of
# headlines, when what was asked for was a stream. Live intent has to outrank the
# data classification, and it needs YouTube's LIVE filter to land on an actual
# stream: a plain search for "cnn live news" returns recorded clips, the filtered
# one returns "CNN Headlines: 24/7 Live News".
LIVE_ASK_RE = re.compile(r'\b(live|livestreams?|live ?streams?|streaming)\b')

# "this one sucks, find another" — swap the CURRENT video for a different one and
# remember not to show the disliked video again. Gated on a video actually being
# on screen (see _session_current_video), so a bare "another one" only means
# "another video" when a video is what's playing.
ANOTHER_VIDEO_RE = re.compile(
    r'\b(another|different|other|next)\b.*\b(one|video|clip)\b'
    r'|\b(this|that)\s+(one|video|clip)?\s*(sucks?|is bad|is boring|is terrible)'
    r'|\b(hate|don\'?t like|do not like|dislike)\s+(this|that)\b'
    r'|\bnot\s+(this|that)\s+(one|video|clip)?\b'
    r'|\b(something|anything)\s+else\b'
    r'|\b(skip|change)\s+(this|the)?\s*(one|video|clip)?\b'
    r'|\bfind\s+(me\s+)?another\b')
# Escalation: the user is rejecting the CHANNEL, not just this video. Blocks the
# whole channel forever. Checked first — if it matches, we block the channel;
# otherwise a generic "another one" blocks just the single video.
CHANNEL_DISLIKE_RE = re.compile(
    r'\bchannel\b.*\b(sucks?|bad|boring|terrible|stop|no more|enough)\b'
    r'|\b(not|hate|block|ban|stop|no more)\b.*\bchannel\b'
    r'|\bdifferent\s+channel\b')

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
# "add greek salad TO THE grocery list", "also add milk", "put eggs on the list".
# An EDIT of an existing checklist, not a request for a new one — distinguished
# from "add a grocery list" (create) by the "to/onto/on the ... list" / "also"
# shape. Merged into the existing widget in place; see _extract_existing_checklist.
LIST_EDIT_RE = re.compile(
    r'\b(add|append|include|put|throw in|toss in)\b.*\b(to|onto|on)\b.*\blists?\b'
    r'|\balso\s+(add|include|put|need|get|grab)\b'
    r'|\b(add|put|append|include)\b.*\bto (the|my|our|this|that)\b'
    # Conversational follow-ups after a list already exists: "oh and add steak",
    # "and throw in some milk", "add eggs too/as well". The route only fires when
    # a checklist is actually on the canvas, so these can't misfire into a new list.
    r'|\b(oh )?and\s+(add|include|put|append|throw in|toss in)\b'
    r'|\b(add|include|put|append)\b[^.]*\b(as well|too)\b')
# "delete the veggies FROM the grocery list" / "cross milk off the list" — an
# in-place item edit, NOT removing the whole widget. Distinguished from "delete
# the list" by the "…from/off/out of the … list" container shape (an item named
# between the verb and the list). Must be intercepted before wants_removal funnels
# it to the agent (which would decompose the entire checklist).
LIST_ITEM_REMOVE_RE = re.compile(
    r'\b(delete|remove|drop|take|cross|clear|get rid of|scratch)\b.*'
    r'\b(from|off|out of)\b.*\blists?\b'
    r'|\b(uncheck|untick)\b')
# "bring back my grocery list", "restore the list", "reopen my list", "that list
# again" — restore a previously-saved list from persistent state instead of
# regenerating a fresh one. Guarded so an "add X to my list again" (an edit) and
# item-removal don't get swallowed here.
LIST_RESTORE_RE = re.compile(
    r'\b(bring|get|put|pull|give)\b[^.]*\bback\b'
    r'|\brestore\b|\breopen\b'
    r'|\blists?\b[^.]*\bagain\b|\bagain\b[^.]*\blists?\b'
    r'|\bback\b[^.]*\blists?\b')
# "close out everything", "clear the whole canvas", "get rid of all the widgets",
# "wipe it", "start over" — clear the ENTIRE canvas in one server call. The agent
# path can only remove one widget per iteration and stops after the first commit,
# so it could never close them all.
CLEAR_ALL_RE = re.compile(
    r'\b(wipe|nuke)\b|\bstart (over|fresh|again)\b|\bclean slate\b'
    r'|\b(close|clear|remove|delete|hide|dismiss|get rid of|kill)\b[^.]*'
    r'\b(everything|every widget|all (the |my )?(widgets?|cards?)|all of (it|them)|'
    r'the (whole |entire )?(canvas|dashboard|screen)|it all)\b')
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
# Travel-time / directions / traffic. There is no routing widget yet, so rather
# than a blank markers map (which looks broken) these resolve to a synthesised
# answer card with the actual guidance. Kept off MAP_ASK_RE — "how long to the
# airport" has no geo token and would otherwise fall through to a wall of links.
DIRECTIONS_ASK_RE = re.compile(
    r'\b(directions?|how (long|far)|travel time|drive time|commute|'
    r'traffic|route to|way to|get to|getting to|how do i get)\b')
# Wikipedia — "open a random wikipedia page", "wikipedia article about X".
WIKI_ASK_RE = re.compile(r'\bwiki(pedia)?\b')
# Framing words stripped so a Wikipedia ask reduces to a page title.
_WIKI_STRIP_RE = re.compile(
    r'\b(open|show|me|a|an|the|random|wikipedia|wiki|page|pages|article|'
    r'articles|about|on|of|for|please|pull up|look up|lookup|find|get)\b',
    re.IGNORECASE)

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


async def build_wikipedia_config(message: str) -> dict:
    """Fetch a Wikipedia article (random, or a named topic) via the REST summary
    API and render it as a readable data_card. NOT an iframe — en.wikipedia.org
    sends X-Frame-Options and refuses to embed, which rendered a black window."""
    lower = (message or "").lower()
    is_random = "random" in lower
    topic = _WIKI_STRIP_RE.sub(" ", message or "")
    topic = re.sub(r'[^\w\s]', " ", topic)
    topic = " ".join(topic.split()).strip()
    if not topic:
        is_random = True
    try:
        headers = {"User-Agent": "html-notes/1.0 (canvas widget)"}
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True,
                                     headers=headers) as client:
            if is_random:
                r = await client.get(
                    "https://en.wikipedia.org/api/rest_v1/page/random/summary")
            else:
                slug = urllib.parse.quote(topic.replace(" ", "_"))
                r = await client.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning(f"[WIKI] fetch failed: {e}")
        return {"title": "Wikipedia", "icon": "menu_book",
                "content": "Couldn't reach Wikipedia right now — try again in a moment."}

    page_title = data.get("title") or topic.title() or "Wikipedia"
    extract = data.get("extract") or ""
    page_url = (((data.get("content_urls") or {}).get("desktop") or {}).get("page")
                or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(page_title.replace(' ', '_'))}")
    thumb = (data.get("thumbnail") or {}).get("source", "")
    if not extract:
        return {"title": page_title, "icon": "menu_book",
                "content": f"Couldn't load a summary. [Read on Wikipedia ↗]({page_url})"}
    return {
        "title": page_title,
        "subtitle": data.get("description") or "Wikipedia",
        "icon": "menu_book",
        "image": thumb,
        "answer": f"{extract}\n\n[Read the full article on Wikipedia ↗]({page_url})",
    }


_fast_model = {"name": None}


async def fast_llm_json(instruction: str, max_tokens: int = 400) -> Optional[dict]:
    """One tool-free completion against the Spark, parsed as JSON.

    A grocery list needs no tools — the model just knows it. Routing that through
    the agentic loop costs ~60s of reasoning and tool-call churn; a direct
    completion answers in ~2s. Returns None on any failure so the caller can
    still spawn an empty widget rather than erroring.

    Timeout is sized for the WORST consumer, not the typical one: the news/answer
    passes emit 500-1000 tokens and the backend generates ~20 tok/s under load,
    so a 30s cap silently killed every news summary (httpx timeouts stringify to
    "" — the logs just said "failed:") and the cards degraded to snippet items.
    Fast callers still return in seconds; the ceiling only matters on the slow ones.
    """
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
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
        # type name matters: httpx timeout exceptions stringify to ""
        logger.warning(f"fast_llm_json failed: {type(e).__name__}: {e}")
        return None


# Place name → IANA timezone, so "time in Tokyo" resolves server-side instead of
# depending on the LLM to emit "Asia/Tokyo" (it usually didn't, so the clock
# silently showed LOCAL time labelled as the city).
_TZ_BY_PLACE = {
    "new york": "America/New_York", "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "portland": "America/Los_Angeles",
    "chicago": "America/Chicago", "austin": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix",
    "toronto": "America/Toronto", "canada": "America/Toronto",
    "mexico city": "America/Mexico_City", "sao paulo": "America/Sao_Paulo",
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
    "paris": "Europe/Paris", "berlin": "Europe/Berlin", "madrid": "Europe/Madrid",
    "rome": "Europe/Rome", "amsterdam": "Europe/Amsterdam", "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai", "india": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "singapore": "Asia/Singapore",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul", "bangkok": "Asia/Bangkok",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "australia": "Australia/Sydney", "auckland": "Pacific/Auckland",
    "utc": "UTC", "gmt": "UTC",
}


def _resolve_timezone(text: str) -> str:
    """IANA timezone for a place named in the message, or "" if none is found."""
    t = (text or "").lower()
    for place in sorted(_TZ_BY_PLACE, key=len, reverse=True):  # "new york" before "york"
        if re.search(r'\b' + re.escape(place) + r'\b', t):
            return _TZ_BY_PLACE[place]
    return ""


def _parse_duration_seconds(text: str) -> int:
    """Total seconds from "5 minutes", "1 hour 30 min", "90 seconds", "2h". 0 if none."""
    total = 0
    for num, unit in re.findall(
            r'(\d+)\s*(h(?:ours?|rs?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)\b',
            (text or "").lower()):
        n = int(num)
        total += n * 3600 if unit.startswith("h") else n * 60 if unit.startswith("m") else n
    return total


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
    result = {
        "title": str(data.get("title") or "Checklist")[:60],
        "items": [str(i)[:120] for i in data["items"] if str(i).strip()][:14],
    }
    _persist_list_state(result)
    return result


def _extract_existing_checklist(session_id: str):
    """Find the most-recent checklist on the session canvas and return
    (widget_id, title, items[{text,done}]) or None. Items are baked into the
    widget's x-data="checklistWidget(<title>, <items>)" attribute; BeautifulSoup
    decodes the html-escaped JSON back to real JSON when it parses the canvas."""
    html_str = get_session_canvas(session_id)
    if not html_str or "checklistWidget(" not in html_str:
        return None
    soup = BeautifulSoup(html_str, "html.parser")
    found = None
    for div in soup.find_all("div", class_="widget-container"):
        if (div.get("x-data") or "").strip().startswith("checklistWidget("):
            found = div  # last one wins — the most recently added list
    if found is None:
        return None
    m = re.search(r'checklistWidget\(\s*(".*?"|\'.*?\')\s*,\s*(\[.*\])\s*\)\s*$',
                  found.get("x-data") or "", re.DOTALL)
    if not m:
        return None
    try:
        title = json.loads(m.group(1))
    except Exception:
        title = "Checklist"
    try:
        raw_items = json.loads(m.group(2))
    except Exception:
        return None
    items = []
    for it in raw_items:
        if isinstance(it, str) and it.strip():
            items.append({"text": it.strip(), "done": False})
        elif isinstance(it, dict) and str(it.get("text", "")).strip():
            items.append({"text": str(it["text"]).strip(), "done": bool(it.get("done"))})
    return found.get("id"), (title or "Checklist"), items


async def build_list_add_config(message: str, existing_title: str,
                                existing_items: list) -> dict:
    """Merge the newly-requested items INTO an existing checklist, preserving the
    prior items (and their done flags). Used to edit a list in place instead of
    spawning a second one for "add greek salad to the grocery list"."""
    data = await fast_llm_json(
        'Return ONLY a JSON object, no prose and no markdown fence:\n'
        '{"items": ["<item>", ...]}\n'
        f'There is already a checklist titled "{existing_title}". The user now '
        f'asked: "{message}"\n'
        'List ONLY the NEW items to add (do not repeat existing ones). Use buyable '
        'ingredients for a grocery list, actionable tasks otherwise. If they named '
        'a dish (e.g. "greek salad"), expand it into its ingredients. 1-12 items.'
    )
    merged = list(existing_items)
    seen = {i["text"].strip().lower() for i in merged if i.get("text")}
    if data and isinstance(data.get("items"), list):
        for raw in data["items"]:
            t = str(raw).strip()[:120]
            if t and t.lower() not in seen:
                merged.append({"text": t, "done": False})
                seen.add(t.lower())
    result = {"title": existing_title, "items": merged[:40]}
    _persist_list_state(result)
    return result


async def build_list_remove_config(message: str, existing_title: str,
                                   existing_items: list) -> dict:
    """Remove the item(s) the user named from an existing checklist, keeping the
    rest. Powers "delete the veggies from the grocery list" as an in-place edit
    instead of the whole widget being removed."""
    data = await fast_llm_json(
        'Return ONLY a JSON object, no prose and no markdown fence:\n'
        '{"remove": ["<exact existing item text to remove>", ...]}\n'
        f'The checklist "{existing_title}" currently has these items:\n'
        f'{json.dumps([i.get("text", "") for i in existing_items])}\n'
        f'The user asked: "{message}"\n'
        'Return the EXACT texts (copied from the list above) of the items to '
        'remove. A category word like "veggies"/"dairy" means remove every item '
        'in the list that fits it. Return an empty list if nothing matches.'
    )
    to_remove = set()
    if data and isinstance(data.get("remove"), list):
        to_remove = {str(x).strip().lower() for x in data["remove"] if str(x).strip()}
    remaining = [i for i in existing_items
                 if i.get("text", "").strip().lower() not in to_remove]
    # If the model matched nothing (or everything by mistake would empty it),
    # keep what we have rather than nuking the list.
    result = {"title": existing_title,
              "items": remaining if remaining else existing_items}
    _persist_list_state(result)
    return result


def _list_slug(title: str) -> str:
    """A stable key from a list title: 'Grocery List' -> 'grocery-list'."""
    s = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return s or "checklist"


def _persist_list_state(config: dict) -> None:
    """Save a checklist's items so it can be restored after it's closed. Keyed by
    the list's title slug (overwrite-in-place), plus a 'list:__last__' pointer to
    the most recent one so a bare 'bring my list back' resolves. Best-effort."""
    try:
        title = config.get("title") or "Checklist"
        items = config.get("items") or []
        if not items:
            return
        payload = json.dumps({"title": title, "items": items})
        database.set_widget_state(f"list:{_list_slug(title)}", payload)
        database.set_widget_state("list:__last__", payload)
    except Exception as e:
        logger.warning(f"[WIDGET STATE] persist failed: {e}")


def _resolve_restorable_list(message: str) -> Optional[dict]:
    """Find the stored checklist the user wants back. Prefers a stored list whose
    slug shares a meaningful word with the request ('grocery' -> list:grocery-list);
    falls back to the most recently saved list. Returns {title, items} or None."""
    try:
        states = database.list_widget_states("list:")
    except Exception as e:
        logger.warning(f"[WIDGET STATE] restore lookup failed: {e}")
        return None
    named = [s for s in states if s["key"] != "list:__last__"]
    stop = {"list", "lists", "checklist", "the", "my", "our", "that", "this",
            "back", "bring", "again", "get", "give", "show", "put", "pull",
            "restore", "reopen", "a", "an", "please", "me", "it", "up", "want"}
    words = {w for w in re.findall(r'[a-z]+', (message or "").lower()) if w not in stop}
    for s in named:
        slug_words = set(s["key"][len("list:"):].split("-"))
        if words & slug_words:
            try:
                return json.loads(s["value"])
            except Exception:
                pass
    last = database.get_widget_state("list:__last__")
    if last:
        try:
            return json.loads(last)
        except Exception:
            pass
    return None


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


# ── Persistent user profile: capture "I'm from Seattle" / "my name is Alex" ──
_NAME_CAP_RE = re.compile(r"\bmy name(?:'?s| is)\s+([A-Za-z][\w .'-]{1,38})", re.I)
_CALLME_RE = re.compile(r"\bcall me\s+([A-Za-z][\w .'-]{1,38})", re.I)
_FROM_CAP_RE = re.compile(
    r"\bi(?:'?m| am)?\s+(?:from|based in|live in|living in|located in)\s+"
    r"([A-Za-z][\w .,'-]{1,38})", re.I)
_LIKE_CAP_RE = re.compile(
    r"\bremember (?:that )?i (?:really )?(?:like|love|enjoy|prefer|am a fan of)\s+"
    r"([\w][\w .,'&-]{1,48})", re.I)


def _clean_fact(s: str) -> str:
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    s = re.split(r'\b(?:and|but|so|because|although|though|please)\b', s, maxsplit=1)[0]
    # Trailing temporal/filler ("Boston now", "Seattle currently") isn't part of
    # the fact.
    s = re.sub(r'\s+(now|today|currently|these days|right now|at the moment|anymore)\s*$',
               '', s, flags=re.I)
    s = s.strip(" .,'\"!?")
    return (s.title() if s.islower() else s)[:60]


def capture_user_facts(message: str) -> dict:
    """Persist first-person profile facts from a message. Returns {key: value} for
    whatever was captured (empty if nothing). Overwrites in place (a new city
    replaces the old)."""
    captured = {}
    for rx in (_NAME_CAP_RE, _CALLME_RE):
        mm = rx.search(message or "")
        if mm:
            name = _clean_fact(mm.group(1))
            if name:
                database.set_user_fact("name", name)
                captured["name"] = name
            break
    mm = _FROM_CAP_RE.search(message or "")
    if mm:
        loc = _clean_fact(mm.group(1))
        if loc:
            database.set_user_fact("location", loc)
            captured["location"] = loc
    mm = _LIKE_CAP_RE.search(message or "")
    if mm:
        like = _clean_fact(mm.group(1))
        if like:
            facts = database.get_user_facts()
            existing = [x.strip() for x in facts.get("likes", "").split(",") if x.strip()]
            if like.lower() not in [e.lower() for e in existing]:
                existing.append(like)
                database.set_user_fact("likes", ", ".join(existing[:12]))
            captured["likes"] = like
    return captured


def _user_facts_prompt() -> str:
    """An 'ABOUT THE USER' line for the agent system prompt, or '' if unknown."""
    facts = database.get_user_facts()
    parts = []
    if facts.get("name"):
        parts.append(f"their name is {facts['name']}")
    if facts.get("location"):
        parts.append(f"they are based in {facts['location']}")
    if facts.get("likes"):
        parts.append(f"they like {facts['likes']}")
    if not parts:
        return ""
    return ("ABOUT THE USER: " + "; ".join(parts) + ". Use this when relevant — "
            "default any location request (weather, maps, 'near me') to their city "
            "unless they name another place.\n\n")


def extract_location(message: str) -> str:
    """Pull a place name out of a weather ask. 'weather in San Francisco' → 'San
    Francisco'; 'tokyo weather' → 'tokyo'; bare 'weather' → the user's remembered
    city, else 'New York'."""
    m = (message or "").strip()
    match = re.search(r'\b(?:in|for|at|near)\s+([A-Za-zÀ-ɏ .,\'-]+)', m, re.IGNORECASE)
    if match:
        loc = match.group(1).strip(" .,")
        if loc:
            return loc
    cleaned = re.sub(r'[^\w\s]', ' ', m.lower())
    words = [w for w in cleaned.split() if w not in _LOCATION_STOPWORDS]
    remaining = " ".join(words).strip()
    if remaining:
        return remaining
    return database.get_user_facts().get("location") or "New York"


# Strip stray inline citation markers ("[0]", "[1, 2]", "[0, 2, 3]") that a
# summariser sometimes leaves in the answer prose despite being told to list source
# indices separately. Only bracketed runs of digits/commas/spaces are removed, so
# real markdown like "[label](url)" and "[1] Do the thing" checklists survive.
_CITATION_RE = re.compile(r'\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]')


def _strip_citation_markers(text: str) -> str:
    return _CITATION_RE.sub("", text or "").strip()


# Preposition/filler words to strip when pulling a destination out of a trip ask.
_TRIP_STOPWORDS = {
    "plan", "planning", "trip", "vacation", "holiday", "getaway", "itinerary",
    "travel", "traveling", "travelling", "visit", "visiting", "tour", "journey",
    "me", "my", "a", "an", "the", "to", "for", "in", "of", "please", "help",
    "week", "weekend", "day", "days", "long", "some", "go", "going",
}


def extract_trip_destination(message: str) -> str:
    """Pull the destination out of a trip ask. 'plan me a trip to japan' → 'japan';
    'kyoto 5 day itinerary' → 'kyoto'. Prefers an explicit 'to/in/for X' clause,
    else falls back to the residual words after stripping trip-planning filler."""
    m = (message or "").strip()
    match = re.search(r'\b(?:to|in|for|around|through|across)\s+([A-Za-zÀ-ɏ .,\'-]+)',
                      m, re.IGNORECASE)
    if match:
        loc = match.group(1).strip(" .,")
        # Drop a trailing duration clause: "japan for 5 days" already handled by the
        # 'to' capture, but "japan in spring" shouldn't keep "in spring".
        loc = re.split(r'\bfor\s+\d|\b\d+\s*(?:day|week)', loc, flags=re.IGNORECASE)[0].strip(" .,")
        if loc and loc.lower() not in _TRIP_STOPWORDS:
            return loc
    cleaned = re.sub(r'[^\w\s]', ' ', m.lower())
    words = [w for w in cleaned.split() if w not in _TRIP_STOPWORDS and not w.isdigit()]
    return " ".join(words).strip()


# Words extract_topic keeps but that shouldn't survive into a news/market topic —
# "news about AI" → topic "ai", not a doubled "News: News Ai" title.
_NEWSY = {"news", "headline", "headlines", "latest", "recent", "breaking",
          "update", "updates", "today", "story", "stories", "about"}


async def build_news_config(message: str) -> dict:
    """A news data_card of current stories: photo + tightened headline + a 2-3
    sentence summary per item.

    Pulls real headlines with images via news_search (GDELT → Google News RSS →
    web search), then a single local-LLM pass rewrites the snippets into
    summaries mapped back to their sources. Falls back to the raw items if the
    model call fails, so the card is never a wall of links.
    """
    raw = extract_topic(message)
    topic = " ".join(w for w in raw.split() if w not in _NEWSY).strip()
    # "what's going on in the news", "anything interesting", "what's happening"
    # are GENERAL asks — fetch TOP stories, don't literal-search "whats going".
    # If every surviving word is question/filler, treat the topic as empty.
    _GENERAL = {"whats", "what", "s", "going", "on", "happening", "up", "new",
                "current", "events", "event", "anything", "something", "interesting",
                "any", "the", "is", "are", "in", "world", "lately", "now", "right",
                "hows", "how", "things", "there", "out", "cool", "hey", "so",
                "tell", "me", "show", "give", "whatsup", "sup", "good"}
    if topic and all(w in _GENERAL for w in topic.split()):
        topic = ""
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


async def build_stock_news_config(message: str) -> dict:
    """Market/stock news with the same summarizing treatment as build_news_config —
    the general news fast-path deliberately excludes stock words, and the agent's
    stock_news tool returns bare title+publisher rows the model can only render as
    a link list.

    Yahoo's finance search has no snippets, so the top article pages are read for
    real body text (build_answer_config's enrichment pattern, same 12s cap) before
    the news-editor LLM pass writes the per-story summaries. Falls back to
    headline items when the LLM pass fails, and to the general news chain when
    Yahoo returns nothing — never bare links.
    """
    raw = extract_topic(message)
    topic = " ".join(w for w in raw.split() if w not in _NEWSY).strip()
    # Bare stock-words mean a GENERAL market ask ("stock market news") — Yahoo's
    # search wants a real query, so default to the market itself.
    _MARKETY = {"stock", "stocks", "market", "markets", "share", "shares", "price",
                "prices", "ticker", "tickers", "equities", "equity", "finance",
                "financial", "trading", "the"}
    is_general = not topic or all(w in _MARKETY for w in topic.split())
    query = "stock market" if is_general else topic
    display = "the market" if is_general else topic
    data0 = await stock_news(query, limit=8)
    news = [n for n in (data0.get("news") or []) if n.get("title")]
    if not news:
        # Yahoo struck out (obscure company, crypto slang) → general news chain,
        # which searches GDELT/Google News with the full topic.
        return await build_news_config(message)

    # Yahoo's finance search returns NO snippet, and its linked article pages are
    # JS-heavy/paywalled so read_web_page often comes back empty — which left the
    # editor pass with only a headline and forced it into one-line restatements
    # ("never gave me a summary"). Enrich the URLs with og:description first: a
    # cheap 6s meta-fetch that yields a real 1-2 sentence blurb per story even when
    # the full page won't scrape, giving the editor real material AND a fallback.
    enrich_items = [{"url": n.get("url", ""), "snippet": "", "image": n.get("image", "")}
                    for n in news[:6]]
    try:
        await asyncio.wait_for(_enrich_news(enrich_items, timeout=6.0), timeout=7.0)
    except asyncio.TimeoutError:
        pass
    for n, e in zip(news[:6], enrich_items):
        if e.get("snippet"):
            n["og_desc"] = e["snippet"]
        if not n.get("image") and e.get("image"):
            n["image"] = e["image"]

    def raw_items(summaries: dict = None):
        out = []
        for i, n in enumerate(news[:6]):
            tickers = ", ".join(n.get("related_tickers") or [])
            # Fallback chain for the description: the LLM summary, else the og:desc
            # blurb, else the headline — never an empty body.
            desc = ((summaries or {}).get(i) or n.get("og_desc") or "")[:500]
            out.append({
                "title": (n.get("title") or "")[:140],
                "description": desc,
                "url": n.get("url", ""),
                "image": n.get("image", ""),
                "meta": " · ".join(x for x in [n.get("publisher"), n.get("published")] if x),
                "badge": tickers[:24] or "Markets",
            })
        return out

    # Yahoo gives headlines only — read the top pages so the summaries have real
    # material instead of restated headlines. Best-effort, hard-capped.
    async def _page_text(n):
        url = n.get("url", "")
        if not url:
            return ""
        try:
            page = await read_web_page(url, max_chars=2000)
            return "" if page.get("is_error") else (page.get("content") or "")
        except Exception:
            return ""
    try:
        page_texts = await asyncio.wait_for(
            asyncio.gather(*[_page_text(n) for n in news[:3]]), timeout=12.0)
    except asyncio.TimeoutError:
        logger.info(f"build_stock_news_config: page reads timed out for {query!r}")
        page_texts = []

    source_lines = []
    for i, n in enumerate(news[:6]):
        # Prefer the scraped body; fall back to the og:description blurb so the
        # editor has real prose to summarise even when the page didn't scrape.
        body = (page_texts[i] if i < len(page_texts) else "") or n.get("og_desc") or ""
        tickers = ", ".join(n.get("related_tickers") or [])
        head = f'[{i}] {n.get("title","")} ({n.get("publisher","")}, {n.get("published","")})'
        if tickers:
            head += f" [tickers: {tickers}]"
        source_lines.append(head + ("\n" + body[:1500] if body else ""))

    data = await fast_llm_json(
        'You are a financial news editor. Return ONLY a JSON object, no prose, no '
        'markdown fence:\n'
        '{"overview": "<one-sentence read on what is moving and why>", '
        '"items": [{"index": <the [N] number of the source>, "title": "<tightened headline>", '
        '"summary": "<2-3 sentence plain-English summary: what happened and why it matters>"}]}\n'
        f'Topic: "{display}"\n\nSOURCES:\n' + "\n\n".join(source_lines) + '\n\n'
        'Write one entry per distinct story (max 6). Base every summary ONLY on that '
        "source's text — never invent numbers, prices, or moves not present in it. If a "
        "source is only a headline, keep its summary to a faithful one-line restatement.",
        max_tokens=1000,
    )
    title = ("Market News" if is_general else f"Market News: {display}").title()[:60]
    if not data or not isinstance(data.get("items"), list) or not data["items"]:
        # LLM pass failed → article excerpts (scrape or og:desc) as descriptions,
        # not bare links. raw_items already falls back to og_desc per story.
        logger.info(f"[DEGRADED] stock_news editor pass empty for {query!r} — "
                    "serving og:desc/excerpt items")
        excerpts = {i: t[:220] for i, t in enumerate(page_texts) if t}
        return {"title": title, "icon": "trending_up", "items": raw_items(excerpts)}
    summaries = {it.get("index"): (it.get("summary") or "").strip()
                 for it in data["items"] if isinstance(it.get("index"), int)}
    titles = {it.get("index"): (it.get("title") or "").strip()
              for it in data["items"] if isinstance(it.get("index"), int)}
    items = raw_items(summaries)
    for i, item in enumerate(items):
        if titles.get(i):
            item["title"] = titles[i][:140]
    return {
        "title": title,
        "subtitle": (data.get("overview") or "")[:120],
        "icon": "trending_up",
        "items": items,
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

    # The results NOT read in full (index >= read_top) reach the LLM as their SERP
    # snippet, which DDG-lite often leaves thin/empty. Backfill those with
    # og:description (cheap 5s meta-fetch) so the synthesis has real material for
    # every source, not just the top two — the same og-first lever that fixed the
    # stock-news summaries.
    thin = [r for r in results[read_top:6] if not (r.get("snippet") or "").strip()]
    if thin:
        try:
            await asyncio.wait_for(_enrich_news(thin, timeout=5.0), timeout=6.0)
        except asyncio.TimeoutError:
            pass

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
        logger.info(f"[DEGRADED] answer synthesis empty for {q!r} — serving summarised sources")
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
        "answer": _strip_citation_markers((data.get("answer") or "").strip()),
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


# Runtime secret cache (vault-backed). Populated lazily by _fetch_secret.
_secret_cache: Dict[str, str] = {}
# A secret that RESOLVED to a value is cached for the process lifetime. A secret
# that came back EMPTY is cached only briefly, so a key added to the vault after
# boot (the "I added the TOMTOM key but traffic still fails" case) gets picked up
# on the next request instead of needing a container restart.
_secret_miss_at: Dict[str, float] = {}
_SECRET_MISS_TTL = 60.0

# A map ask that wants BUSINESS/POI pins ("coffee shops in Seattle", "pharmacies
# near me") rather than a hazard/event map ("where are the fires"). These resolve
# via Google Places (real listings with coordinates) instead of the LLM-guess +
# city geocoder, which can't find individual shops.
POI_MAP_RE = re.compile(
    r'\b(\w*shops?|\w*stores?|restaurants?|cafe?s?|coffee|bars?|pubs?|breweries|brewery|'
    r'pharmac(y|ies)|grocer(y|ies)|supermarkets?|gyms?|hotels?|motels?|banks?|atms?|'
    r'gas ?stations?|petrol|parks?|museums?|librar(y|ies)|hospitals?|clinics?|'
    r'dentists?|doctors?|salons?|barbers?|bakeries|bakery|malls?|markets?|'
    r'dispensar(y|ies)|clubs?|theat(er|re)s?|cinemas?|diners?|delis?|'
    r'places to (eat|drink|stay|shop|visit|go)|things to do|points? of interest)\b'
    r'|\bnear ?(me|by)\b|\bnearby\b')

# Hazard/event geo queries stay on the web+geocode path — Places would just search
# for businesses literally named after the event.
_NON_POI_GEO_RE = re.compile(
    r'\b(fires?|wildfires?|earthquakes?|floods?|flooding|hurricanes?|tornado(es)?|'
    r'storms?|outages?|protests?|war|conflict|border)\b')

# Hunger/meal intent that names NO POI noun (so POI_MAP_RE misses it) and NO geo
# token (so MAP_ASK_RE misses it), yet clearly wants nearby food places on a map:
# "where can I get food", "somewhere to eat", "grab lunch", "food bank near me".
# These used to fall through to the slow agent path, which web-searched "food
# assistance", couldn't geocode the national directories it found, and rendered a
# blank whole-US map. Routed to the Google Places pin path instead (see build_map_config).
EAT_MAP_RE = re.compile(
    r'\bwhere (can|could|should|do|to)\b[^?.!]*\b(eat|food|meal|lunch|dinner|breakfast|brunch|coffee|drinks?|takeout|bite|groceries)\b'
    r'|\bwhere to eat\b'
    r'|\b(somewhere|someplace|any\s?where|a place|any place|good places?|best places?|spots?) to (eat|drink|dine|grab)\b'
    r'|\b(get|grab|order|buy|want|need|find) (me )?(some ?)?(food|lunch|dinner|breakfast|brunch|coffee|takeout|a bite|groceries|a meal)\b'
    r'|\bfood ?(banks?|pantr(?:y|ies)|trucks?)\b'
    r'|\bsoup ?kitchens?\b'
    r'|\b(i\'?m |im |feeling |really )?hungry\b')

# "near me"/"nearby"/"close by" — a POI ask with no explicit anchor city.
_NEAR_ME_RE = re.compile(
    r'\bnear ?(me|by)\b|\bnearby\b|\baround (me|here)\b|\bclose by\b', re.I)
# A preposition followed by a real place token ("in Seattle", "near Chicago") —
# used to tell "coffee in Seattle" (already anchored) from "where can I get food"
# (needs the user's city appended). The negative lookahead skips filler followers
# ("in the airport", "near me") that aren't a place name.
_EXPLICIT_PLACE_RE = re.compile(
    r'\b(in|near|around|at|by|within|close to)\s+'
    r'(?!(?:me|by|here|my|the|a|an|you|us|home)\b)[a-z0-9]', re.I)


def poi_query_has_location(query: str) -> bool:
    """True when we can search Places somewhere the user actually meant: the query
    names a place ("food in Brooklyn") OR the user has a saved city. False means the
    only anchor left would be the SERVER's IP region — the caller must ask the user
    where they are instead of quietly mapping the datacenter's neighborhood."""
    if _EXPLICIT_PLACE_RE.search((query or "").lower()):
        return True
    return bool((database.get_user_facts().get("location") or "").strip())


def anchor_places_query(query: str) -> str:
    """Give Google Places a location to search. A bare POI/eat ask ("where can I
    get food", "food bank") or a "near me" ask is anchored to the user's remembered
    city so a New York user doesn't get the server region's results; a query that
    already names a place ("tacos in Austin") is left untouched. Only called once
    poi_query_has_location() is True, so a saved city always exists here when the
    query itself names no place."""
    q = query or ""
    city = (database.get_user_facts().get("location") or "").strip()
    if _NEAR_ME_RE.search(q.lower()):
        q = _NEAR_ME_RE.sub(f"in {city}" if city else "nearby", q)
    elif city and not _EXPLICIT_PLACE_RE.search(q.lower()):
        q = f"{q} in {city}"
    return q


def build_location_prompt_config(query: str) -> dict:
    """A POI/eat map ask with no place named and no saved city → ask the user where
    they are rather than mapping the server region. Rendered as a data_card."""
    return {
        "title": "Which city?",
        "icon": "location_on",
        "answer": (
            "I can map the closest spots, but I need to know **where you are** — "
            "I won't guess from the server's location.\n\n"
            "Try again with a place, or tell me where you are:\n\n"
            "- **\"where can I get food in Brooklyn\"**\n"
            "- **\"food banks near downtown Chicago\"**\n"
            "- or say **\"I'm in Seattle\"** once and I'll remember it."
        ),
    }


# "from A to B" (a route) vs a single place. The route form gets Google's
# traffic-aware directions; a single place gets a map of that area.
_DIR_FROM_TO_RE = re.compile(r'\bfrom\s+(.+?)\s+to\s+(.+?)(?:[.?!]|$)', re.I)
# Strip the framing words so what's left is the place. Keeps "in/near/at" OUT
# (they're stripped) so "traffic in Seattle" → "Seattle".
_DIR_STRIP_RE = re.compile(
    r'\b(directions?|routes?|traffic|navigate|navigation|drive|driving|way|ways|'
    r'hows?|long|far|travel|time|commute|to|from|in|on|for|around|at|near|by|'
    r'get|getting|the|my|our|is|it|whats|there|show|me|tell|give|please|a|an|s|'
    r'what\'?s?)\b', re.I)
# A directions ask that specifically wants the MAP (traffic/route/navigation),
# not just the travel-time number — the latter stays on the answer card.
TRAFFIC_MAP_RE = re.compile(
    r'\b(traffic|directions?|routes?|navigate|navigation)\b|\bfrom\s+.+\s+to\s+', re.I)


def _extract_directions_place(message: str) -> str:
    cleaned = re.sub(r'[^\w\s]', ' ', message or '')
    cleaned = _DIR_STRIP_RE.sub(' ', cleaned)
    cleaned = ' '.join(cleaned.split()).strip()
    return cleaned[:60] if len(cleaned) >= 2 else ''


async def build_traffic_widget(message: str) -> tuple[str, Optional[dict]]:
    """A traffic/directions ask → (widget_type, config) for the best widget we can
    actually deliver.

    'from A to B' → Google's keyless directions embed (iframe_app) — its route
    line is still congestion-coloured. A single-place TRAFFIC ask → OUR Leaflet
    `map` widget with a TomTom traffic-flow tile overlay: Google's classic
    `layer=t` embed param is DEAD (the legacy URL is server-redirected to the
    modern /maps/embed?pb= endpoint, which silently drops the layer token —
    verified 2026-07-15 with byte-identical screenshots ±layer=t), so the old
    embed rendered a plain map. Degrades to that plain embed when TOMTOM_API_KEY
    is missing or geocoding misses, and to (type, None) when no place can be
    pulled out, so the caller falls back to the travel-time answer card."""
    msg = (message or "").strip()
    city = (database.get_user_facts().get("location") or "").strip()
    is_traffic = bool(re.search(r'\btraffic\b', msg, re.I))
    m = _DIR_FROM_TO_RE.search(msg)
    if m:
        saddr, daddr = m.group(1).strip()[:60], m.group(2).strip()[:60]
        url = "https://maps.google.com/maps?" + urllib.parse.urlencode(
            {"saddr": saddr, "daddr": daddr, "output": "embed"})
        return "iframe_app", {"url": url, "title": f"{saddr} → {daddr}"[:60], "icon": "🚗"}
    place = _extract_directions_place(msg) or city
    if not place:
        return "iframe_app", None
    if is_traffic:
        key = await _fetch_secret("TOMTOM_API_KEY")
        geo = await geocode_place(place) or await geocode_nominatim(place)
        if geo:
            base = {
                "center": {"lat": geo["lat"], "lon": geo["lon"]},
                "zoom": 13,
                "markers": [{"lat": geo["lat"], "lon": geo["lon"],
                             "label": geo["resolved"], "emoji": "🚦"}],
            }
            if key:
                return "map", {**base, "title": f"Traffic: {geo['resolved']}"[:60],
                               "subtitle": "live flow · green moving · red jammed",
                               "traffic": True}
            # No key → be honest instead of a plain map falsely labelled "Traffic":
            # show our themed map of the area and say the live layer needs setup.
            logger.warning("[TRAFFIC] TOMTOM_API_KEY not in env/vault — showing the "
                           "area without a live overlay (free key: developer.tomtom.com)")
            return "map", {**base, "title": f"{geo['resolved']}"[:60],
                           "subtitle": "live traffic needs a TomTom key — showing the area"}
        logger.warning(f"[TRAFFIC] geocode miss for {place!r} — plain embed fallback")
    url = "https://maps.google.com/maps?" + urllib.parse.urlencode(
        {"q": place, "z": "12", "output": "embed"})
    label = "Traffic" if is_traffic else "Directions"
    return "iframe_app", {"url": url, "title": f"{label}: {place}"[:60],
                          "icon": "🚦" if is_traffic else "🧭"}


async def _fetch_secret(name: str) -> str:
    """One secret by name: env var first, then vault-service (bearer token).
    A resolved value is cached for the process lifetime; an empty result is cached
    only for _SECRET_MISS_TTL seconds, so a key added to the vault after boot is
    picked up without a restart. Returns "" when unavailable so callers degrade."""
    if name in _secret_cache:
        return _secret_cache[name]
    if time.time() - _secret_miss_at.get(name, 0.0) < _SECRET_MISS_TTL:
        return ""
    val = os.getenv(name, "") or ""
    if not val and VAULT_SERVICE_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get(f"{VAULT_SERVICE_URL}/secrets",
                                params={"keys": name},
                                headers={"Authorization": f"Bearer {VAULT_SERVICE_TOKEN}"})
                if r.status_code == 200:
                    val = str((r.json() or {}).get(name, "") or "")
        except Exception as e:
            logger.warning(f"[VAULT] fetch {name!r} failed: {e}")
    if val:
        _secret_cache[name] = val
        _secret_miss_at.pop(name, None)
    else:
        _secret_miss_at[name] = time.time()
    return val


# Google Places primaryType → a category emoji for the map pin. Longest/most
# specific handled first via substring match in _emoji_for_place_type. Keeps the
# map readable at a glance instead of a field of identical dots.
_PLACE_TYPE_EMOJI = {
    "coffee": "☕", "cafe": "☕", "bakery": "🥐", "bar": "🍸", "pub": "🍺",
    "night_club": "🎶", "restaurant": "🍽️", "meal_takeaway": "🥡", "meal_delivery": "🥡",
    "food": "🍴", "supermarket": "🛒", "grocery": "🛒", "convenience": "🏪",
    "lodging": "🏨", "hotel": "🏨", "campground": "⛺",
    "tourist_attraction": "📸", "museum": "🏛️", "art_gallery": "🖼️",
    "park": "🌳", "zoo": "🦁", "aquarium": "🐠", "amusement": "🎢", "stadium": "🏟️",
    "beach": "🏖️", "church": "⛪", "hindu_temple": "🛕", "mosque": "🕌", "synagogue": "🕍",
    "place_of_worship": "⛩️", "school": "🏫", "university": "🎓", "library": "📚",
    "hospital": "🏥", "pharmacy": "💊", "doctor": "🩺", "dentist": "🦷",
    "gym": "🏋️", "spa": "💆", "beauty_salon": "💇", "hair_care": "💈",
    "shoe_store": "👟", "clothing_store": "👕", "jewelry_store": "💍", "book_store": "📖",
    "electronics_store": "🔌", "hardware_store": "🔧", "furniture_store": "🛋️",
    "shopping_mall": "🛍️", "store": "🛍️", "gas_station": "⛽", "car_repair": "🔧",
    "parking": "🅿️", "bank": "🏦", "atm": "🏧", "airport": "✈️", "train_station": "🚉",
    "bus_station": "🚌", "subway_station": "🚇", "movie_theater": "🎬", "casino": "🎰",
    "police": "🚓", "fire_station": "🚒", "post_office": "📮", "veterinary": "🐾",
}


def _emoji_for_place_type(primary_type: str, types: list) -> str:
    """Best category emoji for a Places result, from its primaryType then its
    type list. Falls back to a generic pin so every marker still gets an icon."""
    candidates = [primary_type or ""] + list(types or [])
    for t in candidates:
        t = (t or "").lower()
        if t in _PLACE_TYPE_EMOJI:
            return _PLACE_TYPE_EMOJI[t]
    # Substring pass — "italian_restaurant", "book_store" etc.
    for t in candidates:
        t = (t or "").lower()
        for key, emo in _PLACE_TYPE_EMOJI.items():
            if key in t:
                return emo
    return "📍"


async def google_places_search(query: str, limit: int = 12) -> list:
    """Real business/POI pins from Google Places API (New) searchText. Returns a
    markers list in render_map's shape ([{lat, lon, label, detail, color, emoji}]),
    or [] when the key is missing or the search fails (caller falls back)."""
    key = await _fetch_secret("GOOGLE_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": ("places.displayName,places.location,"
                                         "places.formattedAddress,places.rating,"
                                         "places.userRatingCount,places.primaryType,"
                                         "places.types"),
                },
                json={"textQuery": query, "maxResultCount": min(max(limit, 1), 20)})
        r.raise_for_status()
        out = []
        for p in (r.json().get("places") or []):
            loc = p.get("location") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if lat is None or lon is None:
                continue
            name = (p.get("displayName") or {}).get("text") or "Place"
            addr = p.get("formattedAddress") or ""
            rating = p.get("rating")
            reviews = p.get("userRatingCount")
            if rating:
                detail = f"★ {rating}" + (f" ({reviews})" if reviews else "")
                detail += f" · {addr}" if addr else ""
            else:
                detail = addr
            out.append({"lat": lat, "lon": lon, "label": name[:90],
                        "detail": detail[:180], "color": "#8b5cf6",
                        "emoji": _emoji_for_place_type(p.get("primaryType"), p.get("types"))})
        return out
    except Exception as e:
        logger.warning(f"[PLACES] search failed for {query!r}: {e}")
        return []


async def build_map_config(query: str) -> dict:
    """Turn a geo query into a `map` widget. A business/POI ask ("coffee shops in
    Seattle") gets real pins from Google Places; a hazard/event or "where is X"
    ask ("where are the fires in California") searches the web, has the LLM PULL
    the place names out of the results, geocodes them, and drops markers.

    Degrades to a region-centred (marker-less) map so the card is never blank.
    """
    q0 = (query or "").strip()
    # Business/POI pins via Google Places — POI nouns ("coffee shops in Seattle")
    # OR bare hunger/meal intent ("where can I get food", "food bank"), but NOT
    # hazard/event maps, which the web+geocode path handles better.
    ql = q0.lower()
    if ((POI_MAP_RE.search(ql) or EAT_MAP_RE.search(ql))
            and not _NON_POI_GEO_RE.search(ql)):
        # No place named and no saved city → don't map the server's region; hand
        # back a prompt asking where they are (the fast-path gates this too, but the
        # agent path also lands here). Reuses the data_card via a `prompt` marker.
        if not poi_query_has_location(q0):
            cfg = build_location_prompt_config(q0)
            cfg["prompt_for_location"] = True
            return cfg
        # "coffee near me" / bare "where can I get food" have no anchor city —
        # resolve to the user's remembered location so Places searches locally.
        pq = anchor_places_query(q0)
        place_markers = await google_places_search(pq)
        if place_markers:
            n = len(place_markers)
            return {
                "title": q0[:70] or "Places",
                "subtitle": f"{n} place{'s' if n != 1 else ''} found",
                "markers": place_markers,
                "center": None,
            }
        # else: no key / no results → fall through to the web+geocode flow.
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
        '"label": "<short marker label>", "detail": "<one short line: what/when/how big>", '
        '"emoji": "<ONE emoji for what this marker is: 🔥 fire, ⛈️ storm, 🏔️ peak, '
        '🏛️ museum, ☕ cafe, 🍽️ restaurant, 📸 attraction, 🏨 hotel, 🌳 park — pick the '
        'fitting one>"}]}\n\n'
        f'QUERY: "{q}"\n\nSOURCES:\n' + "\n\n".join(src) + '\n\n'
        'Give up to 12 locations. CRITICAL: `place` must be a NAMED TOWN or CITY that a '
        'geocoder can find (e.g. "Chico, California") — NOT a county ("Butte County"), NOT '
        'the event name ("Park Fire"), NOT a highway or park. If the source only names a '
        'county or region, pick the largest town in it. Put the descriptive name in `label` '
        'instead. Base everything on the SOURCES; do not invent places. One place → one location.',
        max_tokens=1200,
    )

    locs = (data or {}).get("locations") or []
    locs = [l for l in locs if isinstance(l, dict) and (l.get("place") or "").strip()][:12]
    # Pass 1: Open-Meteo concurrently (fast, city-level).
    geocoded = await asyncio.gather(*[geocode_place(l.get("place", "")) for l in locs]) if locs else []

    def _marker(l, g):
        return {"lat": g["lat"], "lon": g["lon"],
                "label": (l.get("label") or l.get("place") or g["resolved"])[:90],
                "detail": (l.get("detail") or "")[:180], "color": "#ef4444",
                "emoji": (l.get("emoji") or "")[:4]}

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
        logger.info(f"[DEGRADED] map for {q!r} pinned 0/{len(locs)} places — "
                    "showing the region only")
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


# ── AGENTIC ROUTER ───────────────────────────────────────────────────────────
# The regex cascade in the handler is the fast lane: unambiguous, edge-case-
# hardened asks (timers, weather, video, the traffic route logic, list edits,
# media swaps) resolve there with zero LLM latency. Everything it doesn't catch
# used to drop into the full agentic loop, whose failure mode is a 30-60s
# reasoning spin that often lands a wall-of-links data_card.
#
# The router replaces that fall-through for the common case: ONE ~200-token
# classify pass picks the right server-side builder — the SAME builders the fast
# lane uses — or several of them for a composite ask ("plan my Saturday in
# Seattle" → weather + map + things-to-do). We then build and spawn directly, no
# agent loop. The full agent still owns what needs real tools (removals, DOM
# edits, note CRUD, custom hand-built widgets); the router returns
# {"defer": true} for those and for anything it isn't confident about, so nothing
# it can't do well is forced through it.

# type → (id_prefix, one-line spec for the classify prompt). The prompt text is
# what the model sees; the id_prefix is the widget id stem we spawn with.
ROUTER_WIDGETS = {
    "weather":    ("weather",   'current conditions / forecast. query = the place ("Tokyo")'),
    "news":       ("news",      'general current headlines. query = the topic (empty for top stories)'),
    "stock_news": ("stock-news", 'stock / market / company NEWS. query = ticker, company, or "stock market"'),
    "stock":      ("stock",     'a ticker\'s price + chart + technicals. query = company or symbol ("Apple", "TSLA")'),
    "sports":     ("scores",    'scores / fixtures / standings. query = the league or team ("nba", "arsenal")'),
    "map":        ("map",       'where something IS / a map of places. query = the subject ("fires in California")'),
    "traffic":    ("traffic",   'live traffic or directions. query = the place, or "from A to B"'),
    "video":      ("video",     'something to WATCH. query = the subject ("cookie recipe")'),
    "image":      ("image",     'a PICTURE of something. query = the subject ("golden retriever puppy")'),
    "music":      ("music",     'background music / radio. query = a genre ("lofi", "jazz") or empty'),
    "answer":     ("answer",    'a fact / recipe / how-to / definition / comparison / explanation. query = the question'),
    "products":   ("products",  'shopping / product recommendations to BUY or compare ("good outdoor shoes", "best budget laptop", "gift for a hiker"). query = the product ask. Renders a grid of picture cards linking to sources'),
    "trip":       ("trip",      'plan a TRIP / vacation / multi-day itinerary to a place ("plan a trip to Japan", "3 days in Rome"). query = the destination + any duration. Renders an itinerary card + a map of the spots'),
    "wikipedia":  ("wikipedia", 'an explicit Wikipedia-article request. query = the subject'),
    "list":       ("checklist", 'a checklist / to-do / shopping list to CREATE. query = the whole request'),
    "notes":      ("notes",     'a notepad, optionally pre-filled. query = the whole request'),
    "clock":      ("clock",     'a clock / world clock / timer / countdown / stopwatch. query = the whole request'),
}


async def route_with_llm(message: str, canvas_summary: str) -> Optional[dict]:
    """Classify an ask the fast lane missed into one or more server-buildable
    widgets, or a deferral. Returns:
      {"widgets": [{"type", "query", "modifiers"}], "reason"} to build+spawn, or
      {"defer": true} to hand off to the full agent (removals, edits, note
      dictation, custom widgets, small talk), or
      None on any model failure — the caller then falls back to the agent, so the
    router is a pure latency/quality optimization and never a hard gate."""
    catalog = "\n".join(f"- {name}: {spec}" for name, (_p, spec) in ROUTER_WIDGETS.items())
    data = await fast_llm_json(
        "You are the router for a live dashboard. Choose the widget(s) that best "
        "serve the user and the search query for each. Return ONLY a JSON object, "
        "no prose, no code fence:\n"
        '{"widgets": [{"type": "<type>", "query": "<query>", "modifiers": {}}], '
        '"reason": "<=8 words"}\n'
        "Rules:\n"
        "- ONE widget for one need. Use MULTIPLE only when the ask genuinely spans "
        'them (e.g. "plan my Saturday in Seattle" -> weather + map + things-to-do; '
        '"tesla stock and news" -> stock + stock_news). Max 4.\n'
        "- For a traffic ask add modifiers {\"traffic\": true}.\n"
        "- REUSE what's open. If a widget of the SAME kind is already on the canvas "
        "(see the list below) and the ask refines it (another place on the map, a "
        "refreshed forecast), pick that same type — the server updates the existing "
        "one in place. Do NOT try to open a second map/weather/stock of the same kind.\n"
        "- If the ask is to REMOVE / close / clear / edit an EXISTING widget, to "
        "take dictation into a note, to build a custom/one-off widget, or is small "
        'talk with no widget need, return {"defer": true} instead of widgets.\n'
        "- Never invent a type. Use only the types listed.\n\n"
        "WIDGET TYPES:\n" + catalog +
        (f"\n\nWIDGETS ALREADY ON THE CANVAS:\n{canvas_summary[:700]}" if canvas_summary else "") +
        f'\n\nUSER: "{message}"',
        max_tokens=400,
    )
    if not isinstance(data, dict):
        return None
    if data.get("defer"):
        return {"defer": True, "reason": data.get("reason", "")}
    widgets = data.get("widgets")
    if not isinstance(widgets, list):
        return None
    clean = []
    for w in widgets[:4]:
        if not isinstance(w, dict):
            continue
        wtype = str(w.get("type", "")).strip().lower()
        if wtype not in ROUTER_WIDGETS:
            continue
        clean.append({"type": wtype,
                      "query": str(w.get("query", "") or "").strip(),
                      "modifiers": w.get("modifiers") if isinstance(w.get("modifiers"), dict) else {}})
    if not clean:
        return None
    return {"widgets": clean, "reason": str(data.get("reason", ""))[:80]}


async def build_image_config(query: str) -> Optional[dict]:
    """A picture-of-X widget, built server-side: web-search the subject, then pull
    real photos from the results' og:images (reusing the news enricher). Returns
    None when nothing usable is found, so the caller skips the widget rather than
    rendering an empty frame."""
    q = (query or "").strip()
    if not q:
        return None
    results = await web_search(q, limit=6)
    if not results:
        return None
    # Keep each result's title/host so the picture carries a caption (context)
    # instead of a bare frame — the "no naked image" contract on the widget side.
    items = [{"url": r.get("url", ""), "image": r.get("image", ""),
              "title": r.get("title", "")} for r in results if r.get("url")]
    await _enrich_news(items, timeout=5.0)
    images = [{"url": it["image"],
               "caption": (it.get("title") or _host_of(it.get("url", "")) or "")[:90]}
              for it in items if it.get("image")][:4]
    if not images:
        return None
    return {"title": q[:70].title(), "images": images}


async def build_products_config(query: str) -> dict:
    """Shopping / recommendation grid: web-search the product ask, enrich each
    result with its og:image (the REFERENCE PHOTO) and og:description, then one LLM
    pass tightens each into a product name + one-line "why" + price if stated.

    Every card keeps its own image and links to its own source, so the user sees
    what each thing looks like and clicks the picture to go buy/read more — the
    exact shape asked for by "find good outdoor shoes ... show pictures I can click
    that take me to the source". Falls back to enriched results if the LLM pass
    fails; never a wall of naked links.
    """
    q = (query or "").strip()
    if not q:
        return {"title": "Recommendations", "icon": "shopping_bag", "items": []}
    results = await web_search(q, limit=10)
    if not results:
        return {"title": q[:60].title(), "icon": "shopping_bag",
                "subtitle": "No results", "items": []}
    items = [{"title": r.get("title", ""), "snippet": r.get("snippet", ""),
              "url": r.get("url", ""), "image": r.get("image", "")}
             for r in results if r.get("url")]
    # og:image / og:description give every card a real reference photo + blurb.
    try:
        await asyncio.wait_for(_enrich_news(items, timeout=6.0), timeout=7.0)
    except asyncio.TimeoutError:
        pass
    # A visual grid is the whole point — prefer results that actually have a photo.
    with_img = [it for it in items if it.get("image")]
    pool = (with_img or items)[:8]

    numbered = "\n".join(
        f'[{i}] {it.get("title","")} — {(it.get("snippet") or "")[:200]} ({_host_of(it.get("url",""))})'
        for i, it in enumerate(pool))
    data = await fast_llm_json(
        'You are a shopping assistant turning search results into a clean product '
        'recommendation grid. Return ONLY JSON, no prose, no fence:\n'
        '{"overview": "<one sentence on what to look for>", '
        '"items": [{"index": <the [N] of the source>, "name": "<tight product or pick name>", '
        '"why": "<one short line: what it is best for / why it is a good pick>", '
        '"price": "<a price like \\"$120\\" ONLY if clearly stated in the source, else empty>"}]}\n\n'
        f'QUERY: "{q}"\n\nSOURCES:\n' + numbered + '\n\n'
        'One entry per distinct source (max 8). Base names and prices ONLY on the '
        'source text — never invent a price. If a source is a "best of" listicle, name '
        'the overall guide and say what it covers.',
        max_tokens=900,
    )

    def _fallback_items():
        return [{"title": (it.get("title") or "")[:100],
                 "description": (it.get("snippet") or "")[:200],
                 "image": it.get("image", ""), "url": it.get("url", ""),
                 "meta": _host_of(it.get("url", ""))} for it in pool]

    if not data or not isinstance(data.get("items"), list) or not data["items"]:
        return {"title": q[:60].title(), "icon": "shopping_bag",
                "subtitle": "Top picks", "items": _fallback_items()}

    out = []
    for it in data["items"]:
        idx = it.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(pool)):
            continue
        src = pool[idx]
        out.append({
            "title": (it.get("name") or src.get("title") or "")[:100],
            "description": (it.get("why") or src.get("snippet") or "")[:220],
            "price": (it.get("price") or "")[:16],
            "image": src.get("image", ""),
            "url": src.get("url", ""),
            "meta": _host_of(src.get("url", "")),
        })
    if not out:
        out = _fallback_items()
    return {"title": q[:60].title(), "icon": "shopping_bag",
            "subtitle": (data.get("overview") or "")[:120], "items": out}


async def build_trip_widgets(query: str) -> list:
    """Plan a trip to a place as TWO widgets built from ONE research pass: a real
    day-by-day itinerary card AND a map pinned with the actual places named in it.

    "plan me a trip to japan" previously composed a generic how-to answer card + a
    country-centred (marker-less) map, because build_answer_config just summarises a
    "how to plan a trip" web search and the map only geocoded "japan". Here we search
    for the destination's real attractions, have the LLM WRITE an itinerary and pull
    the named places out of it, geocode those, and drop markers — so the user gets
    usable, place-specific data instead of a paragraph.

    Returns [(widget_type, id_prefix, config), ...] to spawn together. Degrades to
    just the itinerary card if geocoding yields nothing, and to a plain answer card
    if the research pass fails — never an empty turn.
    """
    place = extract_trip_destination(query) or query.strip()
    if not place:
        return [("data_card", "trip", await build_answer_config(query))]

    results = await web_search(f"top attractions and 5 day itinerary in {place}", limit=8)
    if not results:
        return [("data_card", "trip", await build_answer_config(query))]

    async def _page_text(r):
        url = r.get("url", "")
        if not url:
            return ""
        try:
            page = await read_web_page(url, max_chars=2500)
            return "" if page.get("is_error") else (page.get("content") or "")
        except Exception:
            return ""
    try:
        page_texts = await asyncio.wait_for(
            asyncio.gather(*[_page_text(r) for r in results[:3]]), timeout=14.0)
    except asyncio.TimeoutError:
        page_texts = []

    src = []
    for i, r in enumerate(results[:6]):
        body = page_texts[i] if i < len(page_texts) else ""
        src.append(f'[{i}] {r.get("title","")}\n{(body or r.get("snippet") or "")[:1600]}')

    data = await fast_llm_json(
        'You are a travel planner. Write a concrete, usable trip plan and list the '
        'real places it names so they can be mapped. Return ONLY JSON, no fence:\n'
        '{"title": "<e.g. \\"5 Days in Japan\\">", '
        '"overview": "<one sentence>", '
        '"answer": "<the itinerary in GitHub-flavored Markdown: a short intro, then '
        '\\"## Day 1\\" ... headings with 2-4 bulleted specifics each (neighbourhoods, '
        'sights, food), plus a short \\"## Getting Around\\" and \\"## Tips\\" section. '
        'Name REAL places, not generic advice>", '
        '"places": [{"place": "<a specific, geocodable place: \\"Fushimi Inari, Kyoto, Japan\\">", '
        '"label": "<short marker label>", "detail": "<one line: what it is>", '
        '"emoji": "<ONE fitting emoji: ⛩️ shrine, 🏛️ museum, 🍜 food, 🏯 castle, '
        '🌸 garden, 📸 sight, 🏨 hotel, 🛍️ shopping>"}]}\n\n'
        f'DESTINATION: "{place}"\nUSER ASKED: "{query}"\n\nSOURCES:\n' + "\n\n".join(src) + '\n\n'
        'RULES:\n'
        '- WRITE A REAL ITINERARY grounded in the SOURCES — specific neighbourhoods, '
        'landmarks, and dishes, not "research the best time to visit".\n'
        '- Do NOT put bracketed source numbers like [0] or [1,2] in the answer text.\n'
        '- `places` = up to 10 specific, mappable spots you mention, each "Place, City, Country".',
        max_tokens=1900,
    )

    if not data or not (data.get("answer") or "").strip():
        return [("data_card", "trip", await build_answer_config(query))]

    answer_cfg = {
        "title": (data.get("title") or f"Trip to {place}")[:70],
        "subtitle": (data.get("overview") or "")[:140],
        "icon": "luggage",
        "answer": _strip_citation_markers((data.get("answer") or "").strip()),
        "items": [{"title": r.get("title", ""), "description": (r.get("snippet") or "")[:200],
                   "url": r.get("url", ""), "image": r.get("image", ""),
                   "meta": _host_of(r.get("url", "")), "badge": "Source"} for r in results[:4]],
    }
    widgets = [("data_card", "trip", answer_cfg)]

    # Geocode the named places → real markers. Same two-pass flow as build_map_config.
    locs = [l for l in (data.get("places") or [])
            if isinstance(l, dict) and (l.get("place") or "").strip()][:10]
    if locs:
        geocoded = await asyncio.gather(*[geocode_place(l.get("place", "")) for l in locs])
        markers, misses = [], []
        for l, g in zip(locs, geocoded):
            if g:
                markers.append({"lat": g["lat"], "lon": g["lon"],
                                "label": (l.get("label") or l.get("place") or g["resolved"])[:90],
                                "detail": (l.get("detail") or "")[:180], "color": "#8b5cf6",
                                "emoji": (l.get("emoji") or "📍")[:4]})
            else:
                misses.append(l)
        for l in misses[:5]:
            g = await geocode_nominatim(l.get("place", ""))
            if g:
                markers.append({"lat": g["lat"], "lon": g["lon"],
                                "label": (l.get("label") or l.get("place"))[:90],
                                "detail": (l.get("detail") or "")[:180], "color": "#8b5cf6",
                                "emoji": (l.get("emoji") or "📍")[:4]})
        if markers:
            widgets.append(("map", "trip-map", {
                "title": f"{place.title()} — the map"[:70],
                "subtitle": f"{len(markers)} spot{'s' if len(markers) != 1 else ''} from your plan",
                "markers": markers, "center": None,
            }))
    return widgets


async def _resolve_ticker(query: str) -> str:
    """Company name / free text → a ticker symbol. A bare ticker ('TSLA') passes
    through; anything else is resolved via Yahoo's search matches ('Apple' -> AAPL)."""
    q = (query or "").strip()
    if not q:
        return ""
    if re.fullmatch(r"[A-Za-z.\-]{1,6}", q) and q.upper() == q:
        return q.upper()
    data = await stock_news(q, limit=1)
    for m in (data.get("matches") or []):
        if m.get("symbol"):
            return m["symbol"]
    # Uppercase single token is very likely already a symbol ('tsla' typed lower).
    if re.fullmatch(r"[A-Za-z.\-]{1,6}", q):
        return q.upper()
    return ""


async def build_router_widget(spec: dict, session_id: str, message: str) -> Optional[tuple]:
    """One router widget spec -> (widget_type, id_prefix, config) ready to spawn,
    by calling the same builders the fast lane uses. Returns None when the spec
    can't be built (unknown type, or a data pull came back empty) so the caller
    skips it. Never raises — a builder error degrades to None."""
    wtype = spec.get("type", "")
    query = (spec.get("query") or "").strip()
    mods = spec.get("modifiers") or {}
    id_prefix = ROUTER_WIDGETS.get(wtype, (wtype, ""))[0]
    try:
        if wtype == "weather":
            w = await get_weather(extract_location(query or message))
            return None if w.get("is_error") else ("weather", "weather", w)

        if wtype == "news":
            return ("data_card", "news", await build_news_config(query or message))

        if wtype == "stock_news":
            return ("data_card", "stock-news", await build_stock_news_config(query or message))

        if wtype == "stock":
            sym = await _resolve_ticker(query or message)
            if not sym:
                return None
            snap = await stock_snapshot(sym)
            return None if snap.get("is_error") else ("stock_card", "stock", snap)

        if wtype == "sports":
            board = await sports_scores(resolve_league(query) or query or message)
            # Off-season / empty → a synthesized answer card, never an empty board.
            if board.get("is_error"):
                return ("data_card", "sports-answer", await build_answer_config(query or message))
            return ("scoreboard", "scores", board)

        if wtype == "map":
            cfg = await build_map_config(query or message)
            if cfg.get("prompt_for_location"):
                return ("data_card", "askloc",
                        {k: v for k, v in cfg.items() if k != "prompt_for_location"})
            return ("map", "map", cfg)

        if wtype == "traffic":
            twtype, tcfg = await build_traffic_widget(query or message)
            return None if not tcfg else (twtype, "traffic", tcfg)

        if wtype == "video":
            vq = clean_video_query(query or message)
            hits = filter_blocked_videos(await search_youtube_videos(vq, limit=10, rerank=True))
            top, cands = pick_varied_video(hits, k=5, exclude_ids=_shown_video_ids(session_id))
            if not top:
                return None
            _remember_current_video(session_id, top, vq)
            return ("youtube_player", "video", {
                "video_id": top["video_id"], "title": top.get("title") or vq,
                "query": vq, "candidates": cands})

        if wtype == "image":
            cfg = await build_image_config(query or message)
            return None if not cfg else ("image", "image", cfg)

        if wtype == "music":
            genre = extract_music_genre(query or message) or (query.strip() or "lofi")
            return ("mini_music_player", "music", {"genre": genre, "autoplay": True})

        if wtype == "answer":
            return ("data_card", "answer", await build_answer_config(query or message))

        if wtype == "products":
            return ("products", "products", await build_products_config(query or message))

        if wtype == "trip":
            # Returns a LIST of widgets (itinerary card + map); the caller flattens.
            return await build_trip_widgets(query or message)

        if wtype == "wikipedia":
            return ("data_card", "wikipedia", await build_wikipedia_config(query or message))

        if wtype == "list":
            return ("checklist", "checklist", await build_list_config(query or message))

        if wtype == "notes":
            return ("notes", "notes", await build_notes_config(query or message))

        if wtype == "clock":
            text = (query or message).lower()
            if re.search(r"\b(timer|countdown|pomodoro)\b", text):
                secs = _parse_duration_seconds(query or message) or (
                    25 * 60 if "pomodoro" in text else 60)
                return ("clock", "clock", {"mode": "countdown", "duration_seconds": secs})
            if "stopwatch" in text:
                return ("clock", "clock", {"mode": "stopwatch"})
            tz = _resolve_timezone(query or message)
            return ("clock", "clock", {"timezone": tz} if tz else {})
    except Exception as e:
        logger.warning(f"[ROUTER] build {wtype!r} failed for {query!r}: {type(e).__name__}: {e}")
        return None
    return None


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

        # Remember first-person profile facts ("I'm from Seattle", "my name is
        # Alex") so later turns can use them (default weather/map location, etc).
        try:
            capture_user_facts(req.message)
        except Exception as e:
            logger.warning(f"[USER PROFILE] capture failed: {e}")

        def spawn_widget_stream(widget_type: str, id_prefix: str, config: dict = None,
                                config_builder=None, status: str = None,
                                widget_id: str = None):
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
                # Debug breadcrumb for the browser console: which route fired and
                # what widget it chose. The client console.logs this so a misroute
                # ("grocery list" → map) is visible in DevTools without server logs.
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "fast-path", "widget_type": widget_type,
                    "id_prefix": id_prefix, "query": req.message}) + '\n\n')
                yield f'data: {json.dumps({"type": "status", "message": message})}\n\n'

                widget_config = dict(config or {})
                if config_builder:
                    built = await config_builder()
                    if built:
                        widget_config.update(built)

                resolved_id = widget_id or f"{id_prefix}-{uuid.uuid4().hex[:8]}"

                def _append(soup):
                    # Media widgets (video, music) are players: a new one replaces
                    # whatever's already playing instead of stacking a second one —
                    # but only if this request is newer than whatever placed it, so
                    # a slower older request can't overwrite it.
                    if _place_media_widget(soup, widget_type, resolved_id, widget_config, req_seq):
                        return

                    target = soup.select_one('#dashboard-grid')
                    if target is None:
                        grid = BeautifulSoup(
                            '<div id="dashboard-grid" class="dashboard-grid"></div>', 'html.parser')
                        soup.append(grid)
                        target = soup.select_one('#dashboard-grid')
                    node = BeautifulSoup(
                        render_widget(widget_type, resolved_id, widget_config), 'html.parser')
                    _stamp_media_seq(node, widget_type, req_seq)
                    # An explicit widget_id that already exists is an EDIT (e.g.
                    # merging into a checklist): replace it in place instead of
                    # appending a duplicate.
                    existing = soup.find(id=resolved_id)
                    if existing is not None:
                        existing.replace_with(node)
                    else:
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

        def _stream_clear_canvas(status: str = "closing everything..."):
            """Clear the ENTIRE canvas in one commit and stream it back. Bypasses
            the agent, which can only remove one widget per iteration and stops
            after the first mutation — so it could never close them all."""
            async def stream():
                yield f'data: {json.dumps({"type": "status", "message": status})}\n\n'

                def _clear(soup):
                    grid = soup.select_one('#dashboard-grid')
                    if grid is not None:
                        grid.clear()
                    else:
                        for c in soup.select('.glass-card, .widget-container'):
                            c.decompose()

                event = await commit_canvas(req.session_id, _clear)
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

        def spawn_router_stream(specs: list, reason: str = None):
            """Build the router's chosen widget(s) and append them to the canvas in
            ONE commit. Building happens inside the stream (with a status line) so
            a slow pull — news summarising, page reads — shows progress instead of
            a dead spinner. Widgets that fail to build are skipped; if EVERY one
            fails, we degrade to an answer card on the original ask so the user
            still gets something rather than an empty turn."""
            async def stream():
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "router", "widgets": [s.get("type") for s in specs],
                    "reason": reason or "", "query": req.message}) + '\n\n')
                label = (", ".join(s.get("type", "") for s in specs)
                         if len(specs) > 1 else (specs[0].get("type", "widget") if specs else "widget"))
                yield f'data: {json.dumps({"type": "status", "message": f"building {label}..."})}\n\n'

                built = await asyncio.gather(
                    *[build_router_widget(s, req.session_id, req.message) for s in specs],
                    return_exceptions=True)
                # A spec builds to one widget (tuple) OR several (list of tuples, e.g.
                # a trip → itinerary card + map). Flatten both shapes into one list.
                good = []
                for b in built:
                    if isinstance(b, list):
                        good.extend(x for x in b if isinstance(x, tuple) and x)
                    elif isinstance(b, tuple) and b:
                        good.append(b)
                if not good:
                    logger.info("[ROUTER] all builds empty — degrading to an answer card")
                    good = [("data_card", "answer", await build_answer_config(req.message))]

                def _append(soup):
                    target = soup.select_one('#dashboard-grid')
                    if target is None:
                        grid = BeautifulSoup(
                            '<div id="dashboard-grid" class="dashboard-grid"></div>', 'html.parser')
                        soup.append(grid)
                        target = soup.select_one('#dashboard-grid')
                    for wtype, id_prefix, wcfg in good:
                        # Singleton types (map, weather) UPDATE the open one instead
                        # of stacking a second — reuse its id so the commit replaces
                        # it in place. This is what stops "two maps" when a follow-up
                        # ask lands on the router.
                        reuse = (find_existing_widget(req.session_id, wtype)
                                 if wtype in SINGLETON_WIDGET_TYPES else None)
                        rid = reuse or f"{id_prefix}-{uuid.uuid4().hex[:8]}"
                        # Media widgets (video, music) swap the current player in
                        # place; everything else appends.
                        if _place_media_widget(soup, wtype, rid, wcfg or {}, req_seq):
                            continue
                        node = BeautifulSoup(render_widget(wtype, rid, wcfg or {}), 'html.parser')
                        _stamp_media_seq(node, wtype, req_seq)
                        existing = soup.find(id=rid)
                        if existing is not None:
                            existing.replace_with(node)
                        else:
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
        # A grocery/shopping/checklist ask ("add potato salad to the grocery list")
        # names "grocery", which is ALSO a POI noun in POI_MAP_RE — so the map
        # branch used to steal it and (with no city) show the "which city?" prompt.
        # Any query that names a *list* is list-management, never a store map.
        is_list_ask = bool(re.search(r'\b(list|checklist|to-?dos?)\b', text_clean)
                           or LIST_EDIT_RE.search(text_clean)
                           or LIST_ITEM_REMOVE_RE.search(text_clean))

        wants_music = bool(re.search(r'\b(music|radio|song|songs|playlist)\b', text_clean))
        league = resolve_league(text_clean)

        # ── High-priority canvas-control intercepts (run before the removal
        #    funnel and the widget fast-paths) ────────────────────────────────

        # LIST ITEM REMOVE — "delete the veggies from the grocery list" edits the
        # list in place. Checked before CLEAR_ALL and before wants_removal sends it
        # to the agent (which would decompose the whole widget).
        if LIST_ITEM_REMOVE_RE.search(text_clean) and not is_video_ask:
            existing_list = _extract_existing_checklist(req.session_id)
            if existing_list:
                ex_id, ex_title, ex_items = existing_list
                return spawn_widget_stream(
                    "checklist", "checklist",
                    config_builder=lambda: build_list_remove_config(
                        req.message, ex_title, ex_items),
                    status=f"updating “{ex_title}”...",
                    widget_id=ex_id)

        # CLOSE ALL — one server call, no agent. Guarded so a "…from the list" item
        # edit (which can contain "all") never triggers a full wipe.
        if CLEAR_ALL_RE.search(text_clean) and not LIST_ITEM_REMOVE_RE.search(text_clean):
            return _stream_clear_canvas()

        # LIST RESTORE — "bring back my grocery list": restore saved items instead
        # of regenerating. Guarded against edits/removals so those still route
        # normally; only fires when a matching saved list actually exists.
        if (LIST_RESTORE_RE.search(text_clean)
                and not LIST_EDIT_RE.search(text_clean)
                and not LIST_ITEM_REMOVE_RE.search(text_clean)
                and not is_video_ask and not is_data_ask):
            restored = _resolve_restorable_list(req.message)
            if restored and restored.get("items"):
                return spawn_widget_stream(
                    "checklist", "checklist",
                    config={"title": restored.get("title") or "Checklist",
                            "items": restored["items"]},
                    status="bringing your list back...")

        # 0. "THIS ONE SUCKS, FIND ANOTHER" — swap the current video and remember
        #    the dislike forever. Checked before every other video path so a
        #    "find me another video" isn't misread as a fresh search for the
        #    literal words. Only fires when a video is actually on screen.
        current_vid = _session_current_video.get(req.session_id)
        if (current_vid and not wants_removal and not wants_music
                and (ANOTHER_VIDEO_RE.search(text_clean) or CHANNEL_DISLIKE_RE.search(text_clean))):
            vquery = current_vid.get("query") or clean_video_query(req.message)
            disliked_channel = current_vid.get("channel")
            if CHANNEL_DISLIKE_RE.search(text_clean) and disliked_channel:
                block_channel(disliked_channel, reason=f"disliked via: {req.message[:80]}")
                status = f"got it — never showing {disliked_channel} again. finding another..."
                logger.info(f"[VIDEO PREF] blocked channel {disliked_channel!r}")
            else:
                block_video(current_vid.get("video_id"), reason=f"disliked via: {req.message[:80]}")
                status = "finding you a different one..."
                logger.info(f"[VIDEO PREF] blocked video {current_vid.get('video_id')!r}")

            async def _find_another():
                hits = await search_youtube_videos(vquery, limit=12)
                hits = filter_blocked_videos(hits)
                top, cands = pick_varied_video(hits, k=5, exclude_ids=_shown_video_ids(req.session_id))
                if not top:
                    return None
                _remember_current_video(req.session_id, top, vquery)
                return {
                    "video_id": top["video_id"],
                    "title": top.get("title") or vquery,
                    "query": vquery,
                    "candidates": cands,
                }
            return spawn_widget_stream("youtube_player", "video",
                                       config_builder=_find_another, status=status)

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
            hits = filter_blocked_videos(hits)
            # Vary among the top few NEWEST clips: still recent (that's what "news"
            # means), but not the identical video on every repeat ask.
            top, cands = pick_varied_video(hits, k=3, exclude_ids=_shown_video_ids(req.session_id))
            if top:
                _remember_current_video(req.session_id, top, query)
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
            # Search the CLEANED subject, not the raw message: "dunkey livestreams"
            # must search "dunkey" under YouTube's live filter (the filter, not the
            # word, is what finds a stream). Searching the literal "dunkey
            # livestreams" returned nothing, so live asks silently died while the
            # plain "dunkey videos" path worked.
            lquery = clean_video_query(req.message)
            live_hits = filter_blocked_videos(
                await search_youtube_videos(lquery, limit=6, order="live"))
            if live_hits:
                top = live_hits[0]
                _remember_current_video(req.session_id, top, lquery)
                return spawn_widget_stream("youtube_player", "live", {
                    "video_id": top["video_id"],
                    "title": top.get("title") or lquery,
                    "query": lquery,
                    # Label-owned/geo-blocked streams refuse to embed; the player
                    # hops through these on an embed error.
                    "candidates": [v["video_id"] for v in live_hits[1:] if v.get("video_id")],
                }, status=f"finding a '{lquery}' live stream...")
            # Nobody's live right now: fall back to a regular (varied) video of the
            # same subject rather than falling through to the agent (which broke).
            # "dunkey livestreams" when dunkey is offline → a dunkey video.
            vod_hits = filter_blocked_videos(
                await search_youtube_videos(lquery, limit=10))
            top, cands = pick_varied_video(vod_hits, k=5, exclude_ids=_shown_video_ids(req.session_id))
            if top:
                _remember_current_video(req.session_id, top, lquery)
                return spawn_widget_stream("youtube_player", "video", {
                    "video_id": top["video_id"],
                    "title": top.get("title") or lquery,
                    "query": lquery,
                    "candidates": cands,
                }, status=f"no live stream right now — pulling a '{lquery}' video...")

        # 3b. GENERAL VIDEO — a plain "show me a video of X" / "X video" that is
        #     neither a live stream (handled above, deterministic — bloomberg live
        #     is always THE stream) nor a dated news clip. Broad topic asks like
        #     "a cookie recipe video" used to fall through to the agent, which
        #     picked the #1 hit every time, so a repeat ask replayed the identical
        #     video. Fast-path it AND vary among the top handful so it stays
        #     interesting. Music videos keep going to the player, not here.
        if is_video_ask and not wants_removal and not wants_music:
            vquery = clean_video_query(req.message)
            vhits = await search_youtube_videos(vquery, limit=10, rerank=True)
            vhits = filter_blocked_videos(vhits)
            top, cands = pick_varied_video(vhits, k=5, exclude_ids=_shown_video_ids(req.session_id))
            if top:
                _remember_current_video(req.session_id, top, vquery)
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
                    status=f"checking the weather in {weather.get('location', '')}...",
                    widget_id=find_existing_widget(req.session_id, "weather"))
            # An unresolved place falls through to the agent instead of a dead card.

        # 5-pre. STOCK/MARKET NEWS — branch 5 deliberately excludes stock words,
        #    which used to drop these asks to the agent, whose stock_news tool
        #    returns bare title+publisher rows → a wall of links. Same
        #    gather→summarize treatment as general news, sourced from Yahoo
        #    finance search instead of GDELT.
        if (NEWS_ASK_RE.search(text_clean)
                and (STOCK_WORD_RE.search(text_clean) or MARKET_WORD_RE.search(text_clean))
                and not wants_removal and not is_video_ask):
            return spawn_widget_stream(
                "data_card", "stock-news",
                config_builder=lambda: build_stock_news_config(req.message),
                status="gathering and summarizing market news...")

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

        # 5a. WIKIPEDIA — "open a random wikipedia page", "wikipedia about X".
        #     Rendered as a data_card from the REST summary API, NOT an iframe:
        #     en.wikipedia.org sends X-Frame-Options and refuses to embed, which
        #     rendered a solid-black "App Window".
        if (WIKI_ASK_RE.search(text_clean)
                and not wants_removal and not is_video_ask):
            return spawn_widget_stream(
                "data_card", "wikipedia",
                config_builder=lambda: build_wikipedia_config(req.message),
                status="opening a Wikipedia article...")

        # 5b. DIRECTIONS / TRAVEL-TIME — "how long to the airport", "traffic to
        #     downtown", "directions to X". No routing widget yet, so synthesise a
        #     real answer card instead of a blank map. Checked BEFORE the map fast
        #     path (a travel question isn't a "where is X" marker map) and OUTSIDE
        #     the is_data_ask gate so a "weather AND how long to..." still resolves.
        if (DIRECTIONS_ASK_RE.search(text_clean)
                and not EAT_MAP_RE.search(text_clean)
                and not wants_removal and not is_video_ask):
            # "traffic in X", "directions to Y", "from A to B" → a real Google Maps
            # embed (keyless, client-side, traffic-aware) instead of a text card.
            # A pure travel-TIME ask ("how long to the airport") keeps the answer
            # card, which gives the actual minutes.
            if TRAFFIC_MAP_RE.search(text_clean):
                traffic_widget, traffic_cfg = await build_traffic_widget(req.message)
                if traffic_cfg:
                    return spawn_widget_stream(
                        traffic_widget, "traffic", config=traffic_cfg,
                        status="pulling up the map and live traffic...")
            return spawn_widget_stream(
                "data_card", "directions",
                config_builder=lambda: build_answer_config(req.message),
                status="checking the route and travel time...")

        # 5c. TRIP PLANNING — "plan a trip to Japan", "3 days in Rome". Builds a real
        #     day-by-day itinerary card AND a map pinned with the actual places it
        #     names (via build_trip_widgets). Checked BEFORE the map branch so
        #     "trip to japan" isn't grabbed as a bare place map, and reuses
        #     spawn_router_stream so both widgets land in one commit.
        if (TRIP_ASK_RE.search(text_clean)
                and not wants_removal and not is_video_ask and not is_list_ask):
            return spawn_router_stream(
                [{"type": "trip", "query": req.message}], reason="planning your trip")

        # 5d. SHOPPING / PRODUCT PICKS — "good outdoor shoes", "best budget laptop".
        #     A grid of reference-photo cards, each linking to its source, instead of
        #     a wall of links. Checked before the map/POI branch (a product ask names
        #     no place) and outside the is_data_ask gate.
        if (SHOP_ASK_RE.search(text_clean)
                and not wants_removal and not is_video_ask and not is_list_ask):
            return spawn_widget_stream(
                "products", "products",
                config_builder=lambda: build_products_config(req.message),
                status="finding recommendations with photos...")

        # 6. MAP — geo/location queries, and business/POI asks ("coffee shops in
        #    Seattle") which have NO map/where token so MAP_ASK_RE misses them and
        #    they fell through to the agent (a wall-of-links data_card). Pulled OUT
        #    of the is_data_ask gate so "weather in X and a map of Y" still draws a
        #    map; the POI branch stays gated so "restaurant news" still goes to news.
        if ((MAP_ASK_RE.search(text_clean)
             or ((POI_MAP_RE.search(text_clean) or EAT_MAP_RE.search(text_clean))
                 and not is_data_ask and not is_list_ask))
                and not wants_removal and not is_video_ask):
            # A POI/eat ask ("where can I get food", "coffee near me") with no place
            # named and no saved city would otherwise map the SERVER's region. Ask
            # the user where they are instead of guessing. Hazard/"where is X" asks
            # (MAP_ASK_RE only) name their own places and skip this.
            _is_poi = bool((POI_MAP_RE.search(text_clean) or EAT_MAP_RE.search(text_clean))
                           and not _NON_POI_GEO_RE.search(text_clean))
            if _is_poi and not poi_query_has_location(req.message):
                return spawn_widget_stream(
                    "data_card", "askloc",
                    config=build_location_prompt_config(req.message),
                    status="one sec — which city are you in?")
            return spawn_widget_stream(
                "map", "map",
                config_builder=lambda: build_map_config(req.message),
                status="finding the places and building your map...",
                widget_id=find_existing_widget(req.session_id, "map"))

        # 6b. LIST EDIT — "add greek salad to the grocery list", "also add milk".
        #     Merge into the EXISTING checklist (reuse its widget_id) rather than
        #     spawning a second list. Only fires when a checklist is actually on
        #     the canvas; otherwise falls through to the normal create path.
        if (LIST_EDIT_RE.search(text_clean)
                and not wants_removal and not is_video_ask):
            existing_list = _extract_existing_checklist(req.session_id)
            if existing_list:
                ex_id, ex_title, ex_items = existing_list
                return spawn_widget_stream(
                    "checklist", "checklist",
                    config_builder=lambda: build_list_add_config(
                        req.message, ex_title, ex_items),
                    status=f"adding to “{ex_title}”...",
                    widget_id=ex_id)

        if not wants_removal and not is_video_ask and not is_data_ask:
            is_searching = bool(re.search(r'\b(search|find|look for)\b', text_clean))
            topic = extract_topic(req.message)

            # 2. TIME / CLOCK / TIMER — broadened from bare "clock", which missed
            #    every "time"-phrased ask ("what time is it", "time in tokyo") so
            #    they fell to the agent and "just broke". Also handles timers and
            #    stopwatches, and resolves a place name to an IANA timezone.
            if (re.search(r'\bclock\b|\bwhat\'?s? ?(the )?time\b|\b(the|current) time\b'
                          r'|\btime in\b|\btimer\b|\bcountdown\b|\bstopwatch\b|\bpomodoro\b',
                          text_clean) and not is_searching):
                if re.search(r'\b(timer|countdown|pomodoro)\b', text_clean):
                    secs = _parse_duration_seconds(req.message) or (
                        25 * 60 if "pomodoro" in text_clean else 60)
                    return spawn_widget_stream("clock", "clock",
                        {"mode": "countdown", "duration_seconds": secs},
                        status=f"starting a {secs//60 or secs}-{'min' if secs>=60 else 'sec'} timer...")
                if "stopwatch" in text_clean:
                    return spawn_widget_stream("clock", "clock", {"mode": "stopwatch"})
                tz = _resolve_timezone(req.message)
                return spawn_widget_stream("clock", "clock",
                                           {"timezone": tz} if tz else {})

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

            # (MAP now runs earlier, outside the is_data_ask gate — see above.)

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

        # Adopting the client's snapshot is _run_turn's job now (it only does so
        # when no other turn is in flight, so a concurrent turn's stale snapshot
        # can't undo a widget that just landed).
        canvas_summary = get_canvas_summary(
            get_session_canvas(req.session_id) or req.current_canvas)

        # ── AGENTIC ROUTER (steps 2 & 3) ─────────────────────────────────────
        # Nothing in the fast lane matched. Before dropping into the ~30-60s agent
        # loop (whose miss mode is a wall-of-links card), try a cheap LLM classify
        # → server-built widget(s). Skipped for removals/DOM edits, which need the
        # agent's canvas_modify_dom; the router's own {"defer": true} sends note
        # dictation, custom widgets and small talk to the agent too. A None result
        # (model hiccup) also falls through — the router is never a hard gate.
        if not wants_removal:
            router_plan = await route_with_llm(req.message, canvas_summary)
            if router_plan and not router_plan.get("defer") and router_plan.get("widgets"):
                logger.info(f"[ROUTER] {[w['type'] for w in router_plan['widgets']]} "
                            f"— {router_plan.get('reason','')}")
                return spawn_router_stream(router_plan["widgets"], router_plan.get("reason"))
            logger.info(f"[ROUTER] deferring to agent ({(router_plan or {}).get('reason','no plan')})")

        # Start loading history
        history = database.get_session_messages(req.session_id)

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
            "- stock/company/market NEWS, or 'find me stocks' (no specific ticker yet) → mcp__lazy-tool-service__html_notes_stock_news; its 'matches' array gives you tickers to feed into html_notes_stock_history. To show the news, call canvas_add_widget(widget_type='data_card', config={'stock_news_query': '<same query>'}) — the server re-pulls the stories and WRITES a summary per story; do NOT hand-build items from the raw title+link rows. Never use html_notes_stock_history for news (prices only) or html_notes_web_search for stock news (this is cleaner).\n"
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
            + _user_facts_prompt()
            + f"CURRENT CANVAS:\n```markdown\n{canvas_summary}\n```"
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
                        elif (widget_type == "data_card" and not config.get("items")
                              and config.get("stock_news_query")):
                            # Stock/market news: the model names the query; the
                            # server re-pulls Yahoo headlines, reads the top pages
                            # and WRITES the per-story summaries — never a wall of
                            # raw title+link rows (the stock_news tool result has
                            # no snippets to summarize from).
                            sq = str(config.get("stock_news_query", "")).strip()
                            stock_cfg = await build_stock_news_config(sq)
                            logger.info(f"[WIDGET INJECTOR] Synthesised stock-news data_card for {sq!r}")
                            config = {**stock_cfg, **{k: v for k, v in config.items()
                                                      if v and k != "stock_news_query"}}
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
                            if map_cfg.get("prompt_for_location"):
                                # POI/eat ask with no place and no saved city — render
                                # the "which city?" card, not a blank server-region map.
                                logger.info(f"[WIDGET INJECTOR] Map for {mq!r} needs a location — asking")
                                widget_type = "data_card"
                                config = {k: v for k, v in map_cfg.items()
                                          if k != "prompt_for_location"}
                            else:
                                logger.info(f"[WIDGET INJECTOR] Built map for {mq!r} ({len(map_cfg.get('markers', []))} markers)")
                                config = {**map_cfg, **{k: v for k, v in config.items()
                                                        if v and k != "map_query"}}

                        # Bake alternate video ids into youtube players so the
                        # widget can hop to an embeddable video when the first
                        # one blocks embedding ("Video unavailable").
                        if widget_type == "youtube_player":
                            search_q = config.get("query") or config.get("title") or ""
                            primary = config.get("video_id", "")
                            channel = None
                            try:
                                alts = await search_youtube_videos(search_q, limit=8) if search_q else []
                                # Resolve the primary's channel from the search hits
                                # (the model doesn't supply it) so a later "this
                                # channel sucks" has something to block.
                                for v in alts:
                                    if v.get("video_id") == primary:
                                        channel = v.get("channel")
                                        break
                                # Honor the persistent blocklist even on the agent
                                # path: if the model picked a disliked video/channel,
                                # hop to the best non-blocked alternative.
                                if primary in _blocked_video_ids or (channel or "").lower() in _blocked_channels:
                                    kept = filter_blocked_videos(alts)
                                    alt_top, alt_cands = pick_varied_video(
                                        kept, k=5, exclude_ids=_shown_video_ids(req.session_id))
                                    if alt_top:
                                        primary = alt_top["video_id"]
                                        channel = alt_top.get("channel")
                                        config["video_id"] = primary
                                        config["title"] = config.get("title") or alt_top.get("title")
                                        config["candidates"] = alt_cands
                                if not config.get("candidates") and alts:
                                    config["candidates"] = [v["video_id"] for v in alts
                                                            if v.get("video_id") and v["video_id"] != primary]
                                config.setdefault("query", search_q)
                            except Exception as se:
                                logger.warning(f"candidate enrichment failed: {se}")
                            _remember_current_video(
                                req.session_id, {"video_id": primary, "channel": channel}, search_q)

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
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "agent",
                    "note": "no fast-path matched — falling back to the LLM agent",
                    "query": req.message}) + '\n\n')
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
async def widget_map(d: str = ""):
    """Standalone Leaflet page for the map widget's <iframe>. The marker data
    rides in `d` as base64url JSON (built by factory.render_map). Rendered as its
    own document so its <script> runs — the canvas DOMPurify strips inline scripts,
    which is why the map is an iframe rather than inline markup. A payload with
    traffic=true gets the TomTom flow-tile overlay when the key is available."""
    payload = {"center": {"lat": 39.5, "lon": -98.35}, "zoom": 4, "markers": []}
    if d:
        try:
            raw = _base64.urlsafe_b64decode(d.encode("ascii"))
            payload = map_payload(json.loads(raw))  # re-sanitise; never trust the URL
        except Exception as e:
            logger.warning(f"/widgets/map bad payload: {e}")
    # Only turn on the overlay layer when a key actually exists, so a keyless map
    # doesn't fire a screenful of doomed tile requests. The tiles themselves are
    # proxied (see /widgets/map/traffic) so the key stays server-side.
    tiles_url = ""
    if payload.get("traffic") and await _fetch_secret("TOMTOM_API_KEY"):
        tiles_url = "/widgets/map/traffic/{z}/{x}/{y}.png"
    return HTMLResponse(map_document_html(payload, traffic_tiles_url=tiles_url),
                        headers={"Cache-Control": "public, max-age=3600"})


# Transparent 1×1 PNG — served when a traffic tile can't be fetched, so the base
# map shows through cleanly instead of a broken-image frame.
_BLANK_PNG = _base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
# TomTom probe result is logged once per state-change so the reason traffic is/ isn't
# showing is visible in the container logs without spamming every tile.
_tomtom_last_status: Dict[str, int] = {}


@app.get("/widgets/map/traffic/{z}/{x}/{y}.png", include_in_schema=False)
async def widget_traffic_tile(z: int, x: int, y: int):
    """Server-side proxy for TomTom traffic-flow tiles. Holds the key so it never
    reaches the browser (no referrer-restricted-key 403 through the sandboxed map
    iframe), and logs TomTom's HTTP status once per change so a failing key is
    diagnosable from the logs. Falls back to a transparent tile so the base map
    stays clean when the key is missing or a tile errors."""
    key = await _fetch_secret("TOMTOM_API_KEY")
    if not key:
        if _tomtom_last_status.get("_") != -1:
            _tomtom_last_status["_"] = -1
            logger.warning("[TRAFFIC] no TOMTOM_API_KEY — serving blank tiles "
                           "(add it to the vault; free key: developer.tomtom.com)")
        return Response(content=_BLANK_PNG, media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    # No `thickness` param: raster flow tiles reject it with a 400 ("thickness
    # param supported only for style s0/s1/..."), verified live 2026-07-16.
    url = (f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/"
           f"{z}/{x}/{y}.png?key={urllib.parse.quote(key)}&tileSize=256")
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url)
        if _tomtom_last_status.get("_") != r.status_code:
            _tomtom_last_status["_"] = r.status_code
            if r.status_code == 200:
                logger.info("[TRAFFIC] TomTom tiles OK (200)")
            else:
                logger.warning(f"[TRAFFIC] TomTom returned {r.status_code} "
                               f"({r.text[:160]!r}) — check the key is valid and has "
                               "the Traffic API enabled, and is not IP/referrer-locked")
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return Response(content=r.content, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=120"})
    except Exception as e:
        if _tomtom_last_status.get("_") != -2:
            _tomtom_last_status["_"] = -2
            logger.warning(f"[TRAFFIC] TomTom tile fetch failed: {e}")
    return Response(content=_BLANK_PNG, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/widgets/embed", include_in_schema=False)
async def widget_embed(u: str = ""):
    """Reader view for the App Window iframe. External sites send X-Frame-Options
    / Cloudflare bot walls and refuse to embed ("Max challenge attempts exceeded"),
    so we fetch the page server-side (read_web_page's Playwright fallback gets past
    the wall) and serve its readable content from THIS origin — the iframe is then
    same-origin and always renders."""
    url = urllib.parse.unquote(u or "").strip()
    title = url or "App Window"
    if not re.match(r'^https?://', url):
        body = "<p class='muted'>No valid URL was provided.</p>"
    else:
        try:
            page = await read_web_page(url, max_chars=12000)
        except Exception as e:
            logger.warning(f"/widgets/embed fetch failed for {url!r}: {e}")
            page = {"is_error": True}
        if page.get("is_error"):
            body = (f"<p class='muted'>Couldn't load this page in the reader.</p>"
                    f"<p><a href='{esc(url)}' target='_blank' rel='noopener'>"
                    f"Open it in a new tab ↗</a></p>")
        else:
            body = (f"<div class='src'><a href='{esc(url)}' target='_blank' rel='noopener'>"
                    f"{esc(_host_of(url))} ↗</a></div>"
                    + _render_markdown(page.get("content") or ""))
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:20px 24px; background:#0b1020; color:#e6e9f2;
    font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
  a {{ color:#a78bfa; }} .muted {{ color:#94a3b8; }}
  .src {{ font-size:12px; margin-bottom:14px; opacity:.8; }}
  img {{ max-width:100%; height:auto; border-radius:8px; }}
  h1,h2,h3 {{ line-height:1.25; }} hr {{ border:none; border-top:1px solid rgba(255,255,255,.1); }}
  pre {{ overflow:auto; background:#111827; padding:12px; border-radius:8px; }}
  code {{ background:#111827; padding:2px 5px; border-radius:4px; }}
</style></head><body>{body}</body></html>"""
    return HTMLResponse(doc, headers={"Cache-Control": "public, max-age=600"})


@app.get("/user/memory", include_in_schema=False)
async def get_user_memory():
    """The persistent user profile the agent remembers (name/location/likes)."""
    return database.get_user_facts()


@app.delete("/user/memory", include_in_schema=False)
async def forget_user():
    """Wipe the persistent user profile — the 'Forget me' settings control."""
    try:
        n = database.wipe_user_facts()
        return {"ok": True, "forgotten": n}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
