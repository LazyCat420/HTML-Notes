import base64
import difflib
import html as html_lib
import httpx
import logging
import random
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import asynccontextmanager
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
    _unescape as _yt_unescape,
    _token_overlap as _yt_token_overlap,
    Freshness,
    parse_freshness,
    filter_by_age,
    parse_video_form,
    filter_by_form,
    NEWEST_PATTERN as _YT_NEWEST_PATTERN,
    RECENCY_PATTERN as _YT_RECENCY_PATTERN,
)

# Crypto/on-chain layer: CoinGecko (price/chart/identity), Ethplorer (ETH holders
# + transfer edges) and Solana RPC (SPL top holders). Self-contained; the config
# builders below orchestrate it into card / report / holder-graph widgets.
from app import crypto as cryptolib

# Needed up here, not in the import block further down: the helper functions
# below are defined before it, and their annotations are evaluated at def time.
from typing import Any, Dict, List, Optional

from app.youtube_service import *

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

    # `auto` (http→playwright→vision), not crawl4ai: crawl4ai returns "Oops,
    # something went wrong" nav chrome for many article pages (Yahoo etc.) — a
    # non-empty junk body that then became the result's snippet.
    pages = await asyncio.gather(
        *(_scrape(r["url"], engine="auto", timeout=20.0) for r in thin),
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


# Brave's free tier is ~1 request/second; a burst gets 429s that look exactly like
# "no results" to every caller. Space our calls instead of discovering that later.
_BRAVE_MIN_INTERVAL = 1.1
_brave_last_call = 0.0
# Created on first use, not at module scope: asyncio/time are imported further
# down this file, and a module-level asyncio.Lock() here NameErrors on import.
_brave_lock = None


async def _search_brave_api(query: str, limit: int) -> list:
    """Brave's Search API — the PRIMARY web-search engine.

    Added 2026-07-20 after DuckDuckGo became unreachable from this host:
    `duckduckgo.com` and `lite.duckduckgo.com` both ConnectTimeout from inside the
    container while google.com and wikipedia.org return 200. Both previous engines
    were DuckDuckGo, so search failed closed and returned zero for EVERY query —
    and the caller then told the model to retry, which is what produced turns with
    10-18 identical searches.

    NOTE: this is the keyed API (api.search.brave.com), NOT scraping
    search.brave.com — that is bot-walled and was removed for good reason. They
    are unrelated services."""
    global _brave_last_call, _brave_lock
    key = await _fetch_secret("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    if _brave_lock is None:
        _brave_lock = asyncio.Lock()
    async with _brave_lock:
        wait = _BRAVE_MIN_INTERVAL - (time.time() - _brave_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _brave_last_call = time.time()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max(1, min(int(limit), 20))},
                headers={"X-Subscription-Token": key,
                         "Accept": "application/json"},
            )
        if resp.status_code == 429:
            logger.warning(f"[SEARCH] brave rate-limited on {query!r}")
            return []
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning(f"brave api search failed for {query!r}: {e}")
        return []
    out = []
    for it in (payload.get("web", {}).get("results") or [])[:limit]:
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        if title and url.startswith("http"):
            # Brave puts the summary in `description`, occasionally with <strong>
            # highlight markup around the matched terms.
            snippet = re.sub(r"<[^>]+>", "", it.get("description") or "")
            out.append({"title": title, "url": url, "snippet": snippet[:500]})
    return out


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


async def _search_scraper_ddg(query: str, limit: int) -> list:
    """Second web-search engine: scraper-service's DuckDuckGo collector.

    Replaces the old Brave fallback, which has been fully bot-walled since
    2026-07-14 (crawl4ai gets zero bytes, playwright gets a CAPTCHA) — it never
    returned a result, only cost a scrape round-trip. This collector hits DDG's
    `/html/` endpoint with a Playwright fallback on block/captcha, so it's a
    genuinely independent engine from our direct `/lite/` call: if lite starts
    failing, this can still come back with results.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{SCRAPER_SERVICE_URL}/collect",
                json={"source": "duckduckgo", "query": query, "limit": limit},
            )
            payload = resp.json()
    except Exception as e:
        logger.warning(f"scraper ddg collector failed for {query!r}: {e}")
        return []
    out = []
    for it in (payload.get("items") or [])[:limit]:
        url = it.get("url") or ""
        title = (it.get("title") or "").strip()
        if title and url.startswith("http"):
            out.append({"title": title, "url": url,
                        "snippet": (it.get("snippet") or "").strip()[:500]})
    return out


_SEARCH_ENGINES = (
    # Brave's keyed API first: it is the only engine currently reachable from this
    # host. The two DDG engines stay behind it — keyless and free, so if DDG
    # becomes reachable again this self-heals with no code change.
    ("brave-api", lambda q, n: _search_brave_api(q, n)),
    ("ddg-lite", lambda q, n: _search_duckduckgo(q, n)),
    ("ddg-collector", lambda q, n: _search_scraper_ddg(q, n)),
)


async def web_search_ex(query: str, limit: int = 6) -> tuple:
    """Web search returning (results, all_engines_failed).

    The flag is the point. Before this, an engine OUTAGE and a genuinely obscure
    query were indistinguishable — both returned `[]` — so the caller told the
    model to "retry with a shorter, simpler query" while the real problem was that
    every backend was unreachable. The model obliged, 10-18 times per turn. Retry
    advice must never be given for a transport failure."""
    reached_any = False
    for engine, search in _SEARCH_ENGINES:
        try:
            results = await search(query, limit)
        except Exception as e:
            logger.warning(f"{engine} search error for {query!r}: {e}")
            continue
        # An engine that answers at all — even with zero hits — proves the backend
        # is alive, so this is a real "no results" rather than an outage.
        reached_any = True
        if results:
            logger.info(f"[SEARCH] {engine} served {query!r} ({len(results)} hits)")
            await _backfill_snippets(results)
            return results, False
    if reached_any:
        logger.info(f"[SEARCH] no engine had hits for {query!r} (backends alive)")
        return [], False
    logger.error(f"[SEARCH] EVERY ENGINE UNREACHABLE for {query!r} — "
                 f"research is down, not the query")
    return [], True


async def web_search(query: str, limit: int = 6) -> list:
    """Web search. Returns [{title, url, snippet}]. See web_search_ex for the
    outage-vs-no-results distinction."""
    results, _failed = await web_search_ex(query, limit)
    return results


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
        # 6s, not 15s: GDELT is now only a FALLBACK behind Google News RSS, and it
        # frequently 429s (1 req/5s limit) with the throttle body itself taking
        # 10-14s. A tight cap means a throttled GDELT gives up fast for the web
        # fallback instead of stalling the news card.
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            resp = await client.get(_GDELT_DOC, params={
                "query": query, "mode": "ArtList", "maxrecords": str(limit * 4),
                "format": "json", "sort": "DateDesc", "timespan": "3d",
            }, headers={"User-Agent": _BROWSER_UA})
        # A rate-limited GDELT answers 200/429 with a plain-text body, not JSON.
        if not resp.text.strip().startswith("{"):
            # Log it: this silently dropped to the Google-News fallback (favicon
            # thumbnails, headline-only summaries) with no signal about WHY the
            # news card suddenly lost its photos and real summaries.
            logger.warning(f"gdelt throttled for {topic!r} (non-JSON body) "
                           "— falling back to Google News RSS")
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
            # Leave image EMPTY so _enrich_news can try for a real og:image.
            # NOTE: a Google News /rss/articles/ link does NOT redirect to the
            # publisher — it 200s on news.google.com with a JS-driven body — and
            # its og:image is Google's constant News logo, the same URL for every
            # story. _is_generic_news_thumb rejects it, so these items normally
            # end up on the favicon fallback below. (An earlier note here said
            # these URLs serve a real article image; they do not.)
            "image": "",
            "favicon": (f"https://www.google.com/s2/favicons?domain={host}&sz=128"
                        if host else ""),
            "meta": source or host,
            "snippet": "",
            "date": it.findtext("pubDate") or "",
        })
        if len(items) >= limit:
            break
    return items


_GENERIC_NEWS_THUMB_HOSTS = ("lh3.googleusercontent.com", "lh4.googleusercontent.com",
                             "lh5.googleusercontent.com", "lh6.googleusercontent.com")


def _is_generic_news_thumb(url: str) -> bool:
    """Is this og:image Google's stock News logo rather than an article photo?

    Google News redirect pages serve a constant lh3.googleusercontent.com image
    for every story, so a card built from them shows N identical tiles. Treat
    those as no image at all.
    """
    if not url:
        return False
    try:
        return urllib.parse.urlparse(url).netloc.lower() in _GENERIC_NEWS_THUMB_HOSTS
    except Exception:
        return False


async def _enrich_news(items: list, timeout: float = 5.0) -> None:
    """Fill each item's summary (og:description) and, when GDELT had no social
    image, its photo (og:image), by fetching the real article. Concurrent and
    best-effort — a slow or blocking site just leaves that item with its title
    as the summary."""
    stats = {"ok": 0, "fail": 0}

    async def one(client: httpx.AsyncClient, item: dict) -> None:
        try:
            resp = await client.get(item["url"], headers={"User-Agent": _BROWSER_UA})
            html = resp.text
        except Exception:
            stats["fail"] += 1
            return
        if not item.get("snippet"):
            m = _OG_DESC_RE.search(html)
            if m:
                item["snippet"] = _html_unescape(m.group(1)).strip()[:400]
        if not item.get("image"):
            m = _OG_IMAGE_RE.search(html)
            if m:
                raw = _html_unescape(m.group(1)).strip()
                # og:image is routinely protocol-relative ("//cdn/x.jpg") or
                # site-relative ("/img/x.jpg"). Handed to <img src> as-is those
                # render broken, which looks identical to having no image at
                # all. Resolve against the page URL — urljoin leaves absolute
                # URLs untouched, so this only ever repairs.
                try:
                    resolved = urllib.parse.urljoin(str(resp.url), raw) if raw else ""
                except Exception:
                    resolved = raw
                # A Google News redirect page does NOT resolve to the publisher
                # (it 200s on news.google.com and the article link is JS-driven),
                # and the og:image it serves is Google's own generic News logo —
                # the SAME lh3.googleusercontent.com URL for every story. Taking
                # it at face value produced a news card whose six "photos" were
                # six identical 300x300 tiles. An earlier comment here claimed
                # these URLs serve a real article image; they serve a constant.
                # Leave the image empty so the publisher favicon fallback runs
                # instead of dressing the card in decorative duplicates.
                item["image"] = "" if _is_generic_news_thumb(resolved) else resolved
        stats["ok"] += 1

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        await asyncio.gather(*(one(client, it) for it in items),
                             return_exceptions=True)
    # One aggregate line so a SYSTEMIC enrichment outage (e.g. every article 403s
    # the UA) is distinguishable from "these pages had no og:description".
    if items and stats["fail"] > stats["ok"]:
        logger.info(f"[ENRICH] {stats['ok']}/{len(items)} enriched "
                    f"({stats['fail']} fetch failures)")


async def _shared_news_search(topic: str, limit: int) -> list:
    """The shared multi-provider news tool in lazy-tool-service.

    PRIMARY source, ahead of the Google News RSS path below, because it returns
    what that path structurally cannot: the PUBLISHER's own URL and THAT STORY's
    photo. A Google News /rss/articles/ link is a redirect stub — it does not
    resolve server-side, and the og:image it serves is Google's News logo, the
    same picture for every story (which is how a six-story card ended up with
    six identical tiles). GDELT has real URLs and photos but measured 15s on
    success and 1 req/5s, so it cannot front this path either.

    lazy-tool-service rotates across keyed providers (gnews, worldnewsapi,
    currentsapi, ...) and answers in about a second. Empty result or an outage
    just falls through to the chain below, so news still works if it is down.
    """
    if not topic or not topic.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{LAZY_TOOL_SERVICE_URL}/execute/news_search",
                json={"topic": topic.strip(), "limit": limit},
            )
        if resp.status_code != 200:
            logger.warning(f"[news] shared news_search HTTP {resp.status_code}")
            return []
        payload = resp.json()
        # ExecuteRoutes may wrap the tool result; accept either shape.
        body = payload.get("result", payload) if isinstance(payload, dict) else {}
        rows = body.get("items") or []
    except Exception as e:
        logger.warning(f"[news] shared news_search unavailable: {e}")
        return []

    items = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("title") or not r.get("url"):
            continue
        items.append({
            "title": str(r.get("title", "")).strip(),
            "url": str(r.get("url", "")).strip(),
            # A real article photo. _enrich_news only fills what is MISSING, so
            # supplying it here means we never re-fetch the page for a picture.
            "image": str(r.get("image") or ""),
            "meta": str(r.get("source") or _host_of(str(r.get("url", "")))),
            "snippet": str(r.get("snippet") or ""),
            "date": str(r.get("date") or ""),
        })
    if items:
        with_img = sum(1 for i in items if i["image"])
        logger.info(f"[news] shared news_search -> {len(items)} items "
                    f"({with_img} with real photos) for {topic!r}")
    return items


async def news_search(topic: str, limit: int = 6) -> list:
    """Current headlines with real photos and summaries. Returns
    [{title, url, image, meta, snippet, date}].

    Order, and why:

    1. The SHARED news_search tool in lazy-tool-service (~1s). The only source
       here that returns the PUBLISHER's own URL and THAT STORY's photo.
    2. Google News RSS (~0.1s, ~100 deduped headlines). Reliable and fast, but
       its links are redirect stubs: they do not resolve server-side, and every
       one serves Google's News logo as og:image — so a card built from these
       shows N copies of the same picture and cites news.google.com rather than
       the outlet. Fine as a headline source, poor as a sourcing one.
       (An earlier note here claimed these URLs serve a real article image.
       They do not — measured.)
    3. GDELT. Real URLs and photos, but measured 15.6s / 14.9s on SUCCESS and
       1 req/5s, with throttled replies still taking 11-16s. Fast-fail only.
    4. Generic web search.
    """
    items, source = await _shared_news_search(topic, limit), "lazy-tool"
    if not items:
        items, source = await _google_news_rss(topic, limit), "google-news"
    if not items:
        items, source = await _gdelt_news(topic, limit), "gdelt"
    if not items:
        raw = await web_search(f"{topic} news" if topic else "top news headlines", limit)
        items = [{"title": r.get("title", ""), "url": r.get("url", ""), "image": "",
                  "meta": _host_of(r.get("url", "")), "snippet": r.get("snippet", ""),
                  "date": ""} for r in raw]
        source = "web"
    # Enrich EVERY source with og:image + og:description from the article page.
    # GDELT items keep their socialimage (enrich only fills what's missing);
    # Google News items get a real photo in place of the favicon placeholder.
    if items:
        await _enrich_news(items)
        # Any Google News item enrichment couldn't reach falls back to its favicon
        # so it isn't left imageless.
        for it in items:
            if not it.get("image") and it.get("favicon"):
                it["image"] = it["favicon"]
            it.pop("favicon", None)
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
        # Clear the crumb on ANY failure, not just the None-result branch above:
        # a 401/429/timeout here left a stale crumb cached for the process
        # lifetime, so every later fundamentals call kept failing until restart
        # (stock cards permanently missing sector/PE/margins after one blip).
        _yahoo_crumb["crumb"] = None
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


# ── Themes ───────────────────────────────────────────────────────────────────
# The palettes live in hud-theme.css as :root[data-theme="…"] blocks. This
# catalog is the SERVER's view: a name, a human label, the swatch colours the
# settings widget renders, and the keywords the matcher scores a request
# against. Keep the names in sync with the CSS. The settings widget applies the
# theme client-side by name; the server only ever RESOLVES a fuzzy request
# ("forest vibe", "dark mode") to the closest catalog name.
THEME_CATALOG = [
    {"name": "hud", "label": "HUD Cyan", "swatch": ["#050a12", "#0a1626", "#55d6ff"],
     "dark": True, "keywords": ["hud", "cyan", "default", "blue", "tech", "sci-fi",
                                 "scifi", "cyberpunk", "cockpit", "teal", "aqua"]},
    {"name": "midnight", "label": "Midnight", "swatch": ["#070b16", "#141a2e", "#818cf8"],
     "dark": True, "keywords": ["dark", "midnight", "night", "black", "indigo",
                                 "navy", "deep", "space", "moody"]},
    {"name": "forest", "label": "Forest", "swatch": ["#06120c", "#0e2218", "#4ade80"],
     "dark": True, "keywords": ["forest", "green", "nature", "woods", "emerald",
                                 "jungle", "moss", "pine", "earth", "outdoors"]},
    {"name": "ember", "label": "Ember", "swatch": ["#130b06", "#281a10", "#fb923c"],
     "dark": True, "keywords": ["ember", "sunset", "orange", "warm", "fire",
                                 "amber", "autumn", "rust", "copper", "flame", "lava"]},
    {"name": "grape", "label": "Grape", "swatch": ["#0f0616", "#221230", "#e879f9"],
     "dark": True, "keywords": ["grape", "purple", "magenta", "synthwave", "vaporwave",
                                 "violet", "neon", "plum", "berry", "retro", "pink"]},
    {"name": "mono", "label": "Slate", "swatch": ["#0e1116", "#1e242c", "#94a3b8"],
     "dark": True, "keywords": ["mono", "monochrome", "slate", "gray", "grey",
                                 "minimal", "neutral", "steel", "clean", "muted"]},
    # Pastel before egg: on a bare "light mode" both tie, and pastel is the
    # cleaner/cooler default most people mean by it; egg still wins on its own
    # warm words (cream, beige, latte) and its name.
    {"name": "pastel", "label": "Pastel", "swatch": ["#eef0f9", "#fbfaff", "#a78bfa"],
     "dark": False, "keywords": ["pastel", "soft", "light", "lavender", "mint",
                                 "gentle", "calm", "airy", "white", "bright",
                                 "day", "light mode"]},
    {"name": "egg", "label": "Eggshell", "swatch": ["#f1e8d5", "#fbf6ec", "#b5701f"],
     "dark": False, "keywords": ["egg", "eggshell", "cream", "beige", "tan",
                                 "sand", "latte", "warm", "paper", "vanilla",
                                 "coffee", "wheat"]},
]
_THEME_NAMES = {t["name"] for t in THEME_CATALOG}
_THEME_STOP = {"theme", "themed", "mode", "color", "colour", "colors", "colours",
               "palette", "make", "change", "switch", "set", "want", "the", "it",
               "to", "a", "please", "give", "me", "my", "look", "style", "into",
               "everything", "canvas", "use", "turn", "go"}


def pick_theme(text: str) -> Optional[str]:
    """Resolve a free-text appearance request to the closest catalog theme name.

    Scores each theme by how many request tokens hit its keywords (fuzzy, so
    "forset"/"pastal" still land), with an exact theme-name mention winning
    outright. A light/dark hint ("light mode", "dark") breaks ties toward the
    matching family. Returns None when nothing scores — the caller then just
    opens settings without changing the theme, letting the user pick."""
    if not text:
        return None
    low = text.lower()
    for name in _THEME_NAMES:
        if re.search(rf"\b{name}\b", low):
            return name
    toks = [w for w in re.findall(r"[a-z]+", low)
            if w not in _THEME_STOP and len(w) > 2]
    if not toks:
        return None
    best, best_score = None, 0.0
    for t in THEME_CATALOG:
        kw = set()
        for k in t["keywords"]:
            kw.update(k.split())
        score = sum(1 for w in toks if _fuzzy_hit(w, kw))
        # A family hint is a light nudge, never enough on its own.
        if ("light" in toks or "bright" in toks or "day" in toks) and not t["dark"]:
            score += 0.5
        if ("dark" in toks or "night" in toks) and t["dark"]:
            score += 0.5
        if score > best_score:
            best, best_score = t["name"], score
    return best if best_score > 0 else None


# ── Multi-ticker comparison ──────────────────────────────────────────────────
# "NVDA vs SPY vs TSM" used to fan out into one stock_card PER ticker (the
# router classified each as its own stock spec, and nothing merged them). A
# comparison is ONE question, so it renders as ONE chart: every series
# normalized to % change from the range start — the only scale on which a
# $900 stock and a $60 ETF are comparable.

_COMPARE_MAX_TICKERS = 8   # practical cap; the ask says "unlimited", Chart.js
                           # and the legend say otherwise past this
_COMPARE_COLORS = ["#4fc3f7", "#f472b6", "#a3e635", "#fbbf24",
                   "#c084fc", "#fb923c", "#34d399", "#f87171"]

_COMPARE_SPLIT_RE = re.compile(r"\bvs\.?\b|\bversus\b|\bagainst\b|\band\b|,|/", re.I)
# Uppercase words that LOOK like tickers but never are, in compare phrasing.
_TICKER_STOP = {"VS", "AND", "THE", "ETF", "YTD", "SHOW", "PLOT", "CHART",
                "STOCK", "PRICE", "GRAPH", "INDEX", "COMPARE", "OVER", "TO",
                "OF", "ME", "ON", "IN", "FOR", "WITH", "A", "I", "VERSUS"}


def _extract_compare_tickers(text: str) -> list:
    """Ticker symbols from a compare-shaped ask, in mention order.

    Explicit uppercase tokens win ("NVDA vs SPY vs TSM" — users type tickers
    uppercase). If the phrasing is compare-shaped but yields fewer than two,
    the caller falls back to name resolution per segment."""
    out = []
    for tok in re.findall(r"\$?\b[A-Z]{1,5}(?:\.[A-Z])?\b", text or ""):
        sym = tok.lstrip("$")
        if sym not in _TICKER_STOP and sym not in out:
            out.append(sym)
    return out




# ── Trending / gainers discovery ─────────────────────────────────────────────
# "compare the top trending stocks", "biggest gainers today" is a DISCOVERY
# ask: there is no ticker in the text to resolve, so the old path
# (_resolve_ticker on the whole phrase) got nothing back from Yahoo's symbol
# search, every build came up empty, and the turn degraded to a sourceless
# answer card. The candidate list has to come from a real discovery feed —
# Yahoo's keyless trending/screener endpoints — never from an LLM's memory of
# famous tickers (the agent, probed on this exact ask, ranked ten mega-caps it
# already knew and answered META/AAPL/MSFT while the actual trending list was
# AMC/IREN/ACHR).

# A superlative/discovery word within reach of a stock noun. "gainers"/"losers"
# also stand alone ("show me today's gainers") — they are finance-only words.
# "movers" deliberately is NOT standalone ("find me movers in Seattle").
TRENDING_STOCK_RE = re.compile(
    r'\b(trending|hottest|most (?:active|traded|popular)|top|best[\s-]?performing|'
    r'biggest|largest|worst[\s-]?performing)\b'
    r'[^.?!]{0,40}\b(stocks?|tickers?|equities|shares|movers?|gainers?|losers?)\b'
    r'|\b(gainers?|losers?)\b', re.I)

_TREND_KINDS = (
    (re.compile(r'\b(losers?|worst)\b', re.I), "day_losers"),
    (re.compile(r'\b(most (?:active|traded)|volume)\b', re.I), "most_actives"),
    (re.compile(r'\b(gainers?|best[\s-]?perform\w*|top[\s-]?perform\w*)\b', re.I),
     "day_gainers"),
)

# Ordered longest-window-first so "3 months" never half-matches "month".
_RANGE_WORDS = (
    (re.compile(r'\btoday\b|\b(?:1|one) ?day\b|\b24 ?h', re.I), "1d"),
    (re.compile(r'\bweek\b', re.I), "5d"),
    (re.compile(r'\bquarter\b|\b(?:3|three) ?months?\b', re.I), "3mo"),
    (re.compile(r'\b(?:6|six) ?months?\b|\bhalf a year\b', re.I), "6mo"),
    (re.compile(r'\byear\b|\bytd\b|\b12 ?months?\b', re.I), "1y"),
    (re.compile(r'\bmonth\b', re.I), "1mo"),
)


def _trend_kind(text: str) -> str:
    for rx, kind in _TREND_KINDS:
        if rx.search(text or ""):
            return kind
    return "trending"


def _range_from_message(text: str, default: str = "1mo") -> str:
    for rx, range_ in _RANGE_WORDS:
        if rx.search(text or ""):
            return range_
    return default


async def _trending_symbols(kind: str = "trending", limit: int = 10) -> list:
    """Real candidate tickers from Yahoo's keyless discovery feeds: trending/US
    for 'trending', the predefined screeners for gainers/losers/most-actives.
    Crypto (BTC-USD), futures (ES=F) and indices (^GSPC) are dropped — the ask
    said stocks. Empty trending falls back to most_actives; [] means the feeds
    are down and the caller should degrade."""
    if kind == "trending":
        url = "https://query1.finance.yahoo.com/v1/finance/trending/US?count=25"
    else:
        url = ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/"
               f"saved?scrIds={kind}&count=25")
    syms: list = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": _YAHOO_UA})
            result = (resp.json().get("finance", {}).get("result") or [{}])[0] or {}
            for q in result.get("quotes") or []:
                s = str((q or {}).get("symbol") or "").upper()
                if re.fullmatch(r"[A-Z][A-Z0-9]{0,4}(?:\.[A-Z])?", s) and s not in syms:
                    syms.append(s)
                if len(syms) >= limit:
                    break
    except Exception as e:
        logger.warning(f"[TRENDING] {kind} feed failed: {e}")
    if not syms and kind == "trending":
        return await _trending_symbols("most_actives", limit)
    logger.info(f"[TRENDING] {kind} -> {syms}")
    return syms


# ── Index universe (accuracy filter) ─────────────────────────────────────────
# "top 5 stocks in the s&p" used to ignore "s&p" entirely: the candidate list
# came from Yahoo's site-wide trending/US feed, which is most-VIEWED + momentum
# micro-caps (CPHI, VIVK, NBIS…), almost none of them S&P members. Naming an
# index has to actually SCOPE the pull — otherwise the answer is confidently
# wrong and a follow-up ("why are these trending?") finds no news because the
# tickers were never newsworthy, just volatile.
_INDEX_SOURCES = {
    # Keyless, community-maintained constituent lists (CSV, Symbol in col 1).
    "sp500": "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
             "main/data/constituents.csv",
}
# name -> (fetched_at, frozenset[symbols]); one day is well within membership churn.
_index_cache: Dict[str, tuple] = {}
_INDEX_TTL = 86400.0

# "s&p", "s and p", "s&p 500", "sp500", "spx" — the qualifiers that mean the
# S&P 500 universe. Kept deliberately tight so "top stocks" (no index) stays
# unscoped and behaves as before.
_UNIVERSE_RE = (
    (re.compile(r'\bs\s*(?:&|and)\s*p\s*(?:500)?\b|\bsp\s?500\b|\bspx\b', re.I),
     "sp500"),
)
_UNIVERSE_LABEL = {"sp500": "S&P 500"}


def _universe_from_message(text: str) -> str:
    """The named index a discovery ask is scoped to ('sp500'), or '' for none."""
    for rx, name in _UNIVERSE_RE:
        if rx.search(text or ""):
            return name
    return ""


async def _index_constituents(name: str) -> frozenset:
    """Cached membership set for a stock index. Empty set means 'unknown' — the
    caller must NOT filter on an empty set (that would drop every ticker), it
    should degrade to the unscoped feed and say so in the provenance."""
    name = (name or "").lower()
    url = _INDEX_SOURCES.get(name)
    if not url:
        return frozenset()
    cached = _index_cache.get(name)
    if cached and time.time() - cached[0] <= _INDEX_TTL:
        return cached[1]
    syms = set()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": _YAHOO_UA})
            for line in resp.text.splitlines()[1:]:          # skip CSV header
                sym = line.split(",", 1)[0].strip().upper()
                if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", sym):
                    syms.add(sym)
    except Exception as e:
        logger.warning(f"[INDEX] {name} constituents fetch failed: {e}")
    if syms:
        _index_cache[name] = (time.time(), frozenset(syms))
        logger.info(f"[INDEX] {name} -> {len(syms)} members")
        return _index_cache[name][1]
    # Fetch failed: keep serving a stale set rather than dropping the filter.
    return cached[1] if cached else frozenset()




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


async def _finnews_articles(query: str = "", tickers: Optional[list] = None,
                            limit: int = 12) -> list:
    """Multi-provider financial news via scraper-service's finnews collector.

    Yahoo's search returns only a handful of hits from one source; finnews fans
    out across ~10 keyed financial-news APIs (finnhub, marketaux, polygon,
    newsapi, ...) and returns ticker-tagged, provider-SUMMARISED articles. Pass
    `tickers` to reach the ticker-based providers (finnhub etc., the richest),
    else `query` for the keyword providers. Normalised to the stock_news item
    shape; `og_desc` is seeded from the provider summary so the editor has real
    material even when the article page won't scrape. Best-effort — [] on failure.
    """
    payload: dict = {"source": "finnews", "days_back": 7}
    if tickers:
        payload["tickers"] = [t for t in tickers if t][:5]
    elif query:
        payload["query"] = query
    else:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{SCRAPER_SERVICE_URL}/collect", json=payload)
            data = resp.json()
    except Exception as e:
        logger.warning(f"finnews fetch failed for {tickers or query!r}: {e}")
        return []
    out = []
    for a in (data.get("items") or []):
        title = (a.get("title") or "").strip()
        url = a.get("url") or ""
        if not title or not url:
            continue
        pub = a.get("published_at") or ""
        published = (pub[:16].replace("T", " ") + " UTC") if len(pub) >= 16 else None
        out.append({
            "title": title,
            "publisher": a.get("publisher") or a.get("provider") or "",
            "published": published,
            "url": url,
            "image": "",
            "related_tickers": a.get("tickers") or [],
            "og_desc": (a.get("summary") or "").strip()[:400],
        })
        if len(out) >= limit:
            break
    return out


def _merge_news(*lists: list) -> list:
    """Merge news-item lists, deduped by normalised title, preserving order
    (Yahoo first, then finnews). Prefers the first occurrence but backfills an
    empty image/og_desc from a later duplicate that has one."""
    merged, by_key = [], {}
    for lst in lists:
        for n in lst:
            key = _norm_title_key(n.get("title") or "")
            if not key:
                continue
            if key in by_key:
                keep = by_key[key]
                keep["image"] = keep.get("image") or n.get("image")
                keep["og_desc"] = keep.get("og_desc") or n.get("og_desc")
                continue
            by_key[key] = n
            merged.append(n)
    return merged


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


# Research tools whose finished result can be shown IMMEDIATELY as a
# provisional widget, before the agent composes its final answer. The value is
# (args -> tool-cache key, widget_type). Only tools whose cached result is
# already a renderable config belong here — html_notes_news caches a full
# data_card config; raw web-search hits do NOT qualify (wall-of-links).
_PROVISIONAL_TOOLS = {
    "mcp__lazy-tool-service__html_notes_news": (
        lambda a: f"news:{str(a.get('topic') or a.get('query') or '').strip()}",
        "data_card",
    ),
}


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




# Keys that carry only an unresolved QUERY/topic — not real content. A data-ish
# widget holding only these never rehydrated from its data source, so rendering it
# produces the raw key/value dump the user sees as a broken empty card
# ("NEWS_TOPIC | stock market").
_QUERY_ONLY_KEYS = {"news_topic", "topic", "search_query", "map_query", "query",
                    "symbol", "ticker", "location", "url",
                    "profile_query", "timeline_query"}
# Keys that DO carry real, renderable content.
_CONTENT_KEYS = ("items", "sources", "answer", "content", "values", "markers",
                 "events", "articles", "results", "price", "technicals", "image",
                 "rows", "series", "metrics", "stats", "entities", "facts",
                 "sections")




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


def _data_card_quality_gap(config: dict) -> str:
    """SYNC classifier for the two quality symptoms. Returns 'bare_links' (a
    list-mode card whose linked items mostly have no summary), 'no_sources' (an
    answer card with no supporting items), or '' (fine). Used to decide whether
    the async enrichment pass below is worth running."""
    if not isinstance(config, dict):
        return ""
    # `content` counts as an answer here for the same reason render_data_card
    # treats it as one: the MCP tool schema documents `content` as the prose key
    # while the SYSTEM_PROMPT says `answer`, so the model uses either. Reading
    # only `answer` made a content-bearing card look prose-less, so the floor
    # would try to "repair" a card that was already fine.
    answer = ((config.get("answer") or "") or (config.get("content") or "")).strip()
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    if answer and not items:
        return "no_sources"
    if not answer and items:
        linky = [it for it in items if isinstance(it, dict)
                 and (it.get("url") or it.get("link"))]
        bare = [it for it in linky if not (it.get("description")
                or it.get("summary") or it.get("snippet"))]
        # ANY bare link is unacceptable: "if it shows links it must summarise them".
        # (Was ">= half"; a single naked link is still a wall-of-links to the user.)
        if linky and bare:
            return "bare_links"
    return ""


def _linky_items(config: dict) -> list:
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    return [it for it in items if isinstance(it, dict) and (it.get("url") or it.get("link"))]


def _bare_items(items: list) -> list:
    return [it for it in items if not (it.get("description")
            or it.get("summary") or it.get("snippet"))]


async def _synthesize_answer_from_items(config: dict, query_hint: str = "") -> str:
    """Last-resort prose so a card is NEVER just links: write a short overview from
    the item titles/descriptions we already have. Faithful (no new facts beyond the
    rows) and cheap. Returns '' only if the model call fails."""
    items = _linky_items(config)
    if not items:
        return ""
    rows = "\n".join(
        f'- {it.get("title","")}'
        + (f': {(it.get("description") or it.get("summary") or it.get("snippet") or "")[:160]}'
           if (it.get("description") or it.get("summary") or it.get("snippet")) else "")
        for it in items[:8])
    q = (query_hint or config.get("title") or "").strip()
    data = await fast_llm_json(
        "You write a 2-3 sentence plain-language overview that ties together the "
        "items below into a direct answer. Use ONLY what the items state — invent "
        "no facts. Return ONLY JSON: {\"answer\": \"...\"}\n\n"
        + (f"QUESTION: {q}\n\n" if q else "") + "ITEMS:\n" + rows,
        max_tokens=400)
    if isinstance(data, dict):
        return (data.get("answer") or "").strip()
    return ""


def _favicon_for(url: str) -> str:
    """Google's favicon service for any host. A site's own mark is a far better
    tile than a monogram letter — it identifies the SOURCE at a glance — and it
    resolves for essentially every real domain, which is what makes near-total
    image coverage cheap. Was previously built only inside the Google-News RSS
    path; it generalises to any URL."""
    try:
        host = urllib.parse.urlparse(url).netloc
    except Exception:
        return ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128" if host else ""


def _items_of(config: dict) -> list:
    """items/sources, normalised to a list of dicts."""
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    return [it for it in items if isinstance(it, dict)]


def _items_missing_images(config: dict) -> list:
    """Linked items with no picture. These are what the backfill targets."""
    return [it for it in _items_of(config)
            if (it.get("url") or it.get("link"))
            and not (it.get("image") or it.get("thumbnail"))]


async def _backfill_item_images(items: list, timeout: float = 6.0) -> int:
    """Give every linked item a picture: real og:image where the page has one,
    the site's favicon otherwise.

    Image population used to be a SIDE EFFECT of description repair — it ran only
    on items that were also missing a summary, and only when a quality gap had
    already fired. A well-written card with good descriptions and no pictures was
    therefore 'fine' by the floor and rendered as a column of monogram letters.
    Pictures are now their own concern, applied to any linked item that lacks one.
    """
    targets = [it for it in items
               if (it.get("url") or it.get("link"))
               and not (it.get("image") or it.get("thumbnail"))]
    if not targets:
        return 0
    probe = [{"url": it.get("url") or it.get("link"), "snippet": "x", "image": ""}
             for it in targets]
    try:
        # snippet is pre-filled above: _enrich_news only fills falsy fields, so a
        # non-empty snippet keeps this pass strictly about images and stops it
        # overwriting descriptions the card already has.
        await asyncio.wait_for(_enrich_news(probe, timeout=timeout), timeout=timeout + 1.0)
    except (asyncio.TimeoutError, Exception):
        pass  # best-effort; the favicon fallback below still applies
    filled = 0
    for it, p in zip(targets, probe):
        img = (p.get("image") or "").strip() or _favicon_for(it.get("url") or it.get("link"))
        if img:
            it["image"] = img
            filled += 1
    return filled


async def _ensure_data_card_quality(config: dict, query_hint: str = "") -> dict:
    """Quality floor for data_cards (async — enriches, so not usable inside the
    sync render path). Guarantees the user never gets a wall of bare links or a
    sourceless answer, no matter how the config arrived (esp. the agent path,
    where a hand-built config bypasses every rehydration branch).

    - bare_links: backfill each summary-less linked item from its og:description
      (cheap meta fetch); any still-bare rows get one faithful LLM one-liner pass;
      if rows are STILL bare after that, synthesize a top-level answer so the card
      renders as prose-over-sources rather than naked links.
    - no_sources: fetch a few real sources for the answer's subject and attach.
    Fails SAFE, not open: on any error a card with links but no summary at least
    gets a minimal answer stitched from its titles, so links never render alone.
    """
    gap = _data_card_quality_gap(config)
    # Missing pictures are a gap in their OWN right. Returning early on `not gap`
    # meant a card with solid descriptions but no images skipped enrichment
    # entirely and rendered as a column of monogram letters.
    if not gap:
        if _items_missing_images(config):
            n = await _backfill_item_images(_items_of(config))
            if n:
                logger.info(f"[QUALITY] backfilled {n} item image(s) on an otherwise-fine card")
        return config
    try:
        if gap == "bare_links":
            items = config.get("items") or config.get("sources") or []
            if isinstance(items, dict):
                items = [items]
            bare = [it for it in items if isinstance(it, dict)
                    and (it.get("url") or it.get("link"))
                    and not (it.get("description") or it.get("summary") or it.get("snippet"))]
            enrich = [{"url": it.get("url") or it.get("link"), "snippet": "",
                       "image": it.get("image", "")} for it in bare]
            try:
                await asyncio.wait_for(_enrich_news(enrich, timeout=6.0), timeout=7.0)
            except asyncio.TimeoutError:
                pass
            for it, e in zip(bare, enrich):
                if e.get("snippet"):
                    it["description"] = e["snippet"][:400]
                if not it.get("image") and e.get("image"):
                    it["image"] = e["image"]
            still = [it for it in bare if not it.get("description")]
            if still:
                lines = "\n".join(f'[{i}] {it.get("title","")}' for i, it in enumerate(still))
                data = await fast_llm_json(
                    'You are a news editor. For each headline below write a faithful '
                    'one-sentence summary of what it is about — do NOT invent facts '
                    'beyond the headline. Return ONLY JSON: '
                    '{"items":[{"index":<N>,"summary":"..."}]}\n\nHEADLINES:\n' + lines,
                    max_tokens=600)
                if data and isinstance(data.get("items"), list):
                    by_i = {d.get("index"): (d.get("summary") or "").strip()
                            for d in data["items"] if isinstance(d.get("index"), int)}
                    for i, it in enumerate(still):
                        if by_i.get(i):
                            it["description"] = by_i[i][:300]
            config["items"] = items
            # Backstop: if any row is STILL bare (enrichment + editor both failed),
            # synthesize a top-level answer so the card is prose-over-sources, never
            # a naked link list. Only when there's no answer already.
            if _bare_items(_linky_items(config)) and not (config.get("answer") or "").strip():
                ans = await _synthesize_answer_from_items(config, query_hint)
                if ans:
                    config["answer"] = ans
                    logger.info("[QUALITY] synthesized a summary for an otherwise link-only card")
        elif gap == "no_sources":
            q = (query_hint or config.get("title") or "").strip()
            if q:
                # Cite what the answer was actually WRITTEN FROM.
                #
                # This used to call news_search() for every sourceless card, so
                # "best espresso machines under $500" — answered by the model
                # from html_notes_web_search results — got five unrelated Google
                # News articles stapled on under a "Sources" badge. Wrong corpus
                # for a non-news question, and worse, a groundedness lie: the
                # card cited pages the answer had never seen. (They also all
                # resolved to the same Google News og:image, so every thumbnail
                # was the same picture.)
                #
                # The real sources are already in hand — html_notes_web_search
                # caches under "search:<query>", so the model's own reading list
                # is a dict lookup away. Fall back to a fresh web search for the
                # right corpus, and reach for news only when the ask is actually
                # news-shaped.
                hits = get_cached_tool_result(f"search:{q}")
                origin = "cached web_search"
                if not hits:
                    searcher = news_search if NEWS_ASK_RE.search(q) else web_search
                    origin = searcher.__name__
                    try:
                        hits = await asyncio.wait_for(searcher(q, limit=5), timeout=8.0)
                    except asyncio.TimeoutError:
                        hits = []
                hits = (hits or [])[:5]
                # Same starve-the-image-path problem as build_answer_config: these
                # carry no og:image until something fetches for one.
                if hits:
                    try:
                        await asyncio.wait_for(_enrich_news(hits, timeout=5.0), timeout=6.0)
                    except asyncio.TimeoutError:
                        pass
                srcs = [{"title": (h.get("title") or "")[:120],
                         "description": (h.get("snippet") or "")[:240],
                         "url": h.get("url", ""), "image": h.get("image", ""),
                         "meta": h.get("publisher") or _host_of(h.get("url", "")),
                         "badge": "Source"}
                        for h in hits if h.get("url")][:5]
                if srcs:
                    config["items"] = srcs
                    logger.info(f"[QUALITY] attached {len(srcs)} sources "
                                f"({origin}) to a sourceless answer for {q!r}")
    except Exception as e:
        logger.warning(f"_ensure_data_card_quality ({gap}) failed: {e}")
    # Fail SAFE: whatever happened above, a card that still shows links with neither
    # a top-level answer nor any per-item description must not go out naked. Give the
    # bare rows a minimal honest description derived from their own title.
    try:
        if not (config.get("answer") or "").strip():
            for it in _bare_items(_linky_items(config)):
                t = (it.get("title") or "").strip()
                if t:
                    it["description"] = t
    except Exception:
        pass
    # Final image sweep. The gap branches above fill images only for the subset
    # they happened to touch (bare items, or freshly-fetched sources), so anything
    # they left alone still lands here and gets at least a favicon.
    try:
        if _items_missing_images(config):
            n = await _backfill_item_images(_items_of(config))
            if n:
                logger.info(f"[QUALITY] backfilled {n} item image(s) after {gap} repair")
    except Exception as e:
        logger.warning(f"[QUALITY] image backfill failed: {e}")
    return config


# Near-miss type names the model plausibly emits → the real renderer key. The
# SYSTEM_PROMPT's routing line used to say "music, embedded app → … with that
# widget_type", and an unknown type silently degrades to an inert data_card
# (generate_widget_html's fallback) stamped with the bogus type — which then
# dodges the media-singleton swap and id reuse. Resolve the obvious aliases
# instead of letting them fall through.
_WIDGET_TYPE_ALIASES = {
    "music": "mini_music_player", "music_player": "mini_music_player",
    "radio": "mini_music_player",
    "embedded_app": "iframe_app", "embed": "iframe_app", "app": "iframe_app",
    "iframe": "iframe_app", "website": "iframe_app",
    "video": "youtube_player", "youtube": "youtube_player",
    "todo": "checklist", "todo_list": "checklist", "list": "checklist",
    "kpi": "kpi_row", "metrics": "kpi_row", "stat_row": "kpi_row",
    "versus": "versus_card", "comparison": "versus_card", "compare_card": "versus_card",
    "profile": "profile_card", "infobox": "profile_card",
    "data_table": "table",
    # Crypto / on-chain
    "crypto": "crypto_card", "token": "crypto_card", "coin": "crypto_card",
    "token_card": "crypto_card",
    "holder_graph": "wallet_graph", "wallet_network": "wallet_graph",
    "holder_network": "wallet_graph", "whale_graph": "wallet_graph",
}




# Below this, a "reply" is not an answer — it's the model trailing off ("...",
# "Sure!", "Let me look"). Rendering that as an answer card is worse than saying
# nothing went wrong, because it looks like a real result.
_MIN_ANSWER_CHARS = 40

# Runaway-tool thresholds. A healthy research turn measures 3 tool calls with 1
# repeat (5/5 runs, 2026-07-20), so these sit far above normal and should only
# ever fire when something is genuinely broken. Do NOT tune them down toward
# normal — a research turn legitimately repeats a search once.
_MAX_IDENTICAL_TOOL_CALLS = 4
_MAX_RESEARCH_CALLS = 12


def _tool_repeat_key(tool_name: str, args) -> str:
    """Stable key for 'the same call again'. Args are canonicalized so key order
    can't disguise a repeat as a new call."""
    try:
        blob = json.dumps(args, sort_keys=True, default=str)[:400]
    except Exception:
        blob = str(args)[:400]
    return f"{tool_name}|{blob}"


# The in-flight progress vocabulary the browser renders while a turn runs. The
# client keys off `phase`, never off the prose in `message` — so a status line
# can be reworded without silently changing what the canvas shows, and a status
# we forget to tag simply leaves the phase where it was rather than misreporting.
_PHASE_ROUTING     = "routing"
_PHASE_RESEARCHING = "researching"
_PHASE_READING     = "reading"
_PHASE_COMPOSING   = "composing"

# Which phase a tool call puts the turn in, keyed on the BARE tool name (the
# mcp__lazy-tool-service__ prefix is stripped first). Unlisted means research:
# the only tools that aren't research are the canvas/notes mutators, and every
# one of those is named here, so the default is safe rather than merely likely.
_TOOL_PHASES = {
    "html_notes_read_page":  _PHASE_READING,
    "canvas_read_dom":       _PHASE_READING,
    "canvas_add_widget":     _PHASE_COMPOSING,
    "canvas_modify_dom":     _PHASE_COMPOSING,
    "create_widget":         _PHASE_COMPOSING,
    "update_widget":         _PHASE_COMPOSING,
    "validate_widget_html":  _PHASE_COMPOSING,
    "plan_widget":           _PHASE_COMPOSING,
    "list_widget_types":     _PHASE_COMPOSING,
}

# The argument values that say what a call is DOING. Everything else in an args
# dict is plumbing (widget ids, html bodies, limits) and would only bloat a
# frame that now rides on EVERY tool call — a canvas_add_widget `config` alone
# runs to several KB, and this travels the same SSE stream as the canvas HTML.
_TOOL_DETAIL_KEYS = ("query", "q", "url", "topic", "ticker", "symbol",
                     "location", "league", "title")
_MAX_DETAIL_CHARS = 80


def _summarize_tool_args(args) -> dict:
    """The one or two argument values worth showing a human, small enough to
    send with every tool_call.

    The browser has always read `data.args` on this event — it was the server
    that never sent it, so a research turn printed the bare MCP tool name and
    nothing about what was actually being searched for."""
    if not isinstance(args, dict):
        return {}
    out = {}
    for key in _TOOL_DETAIL_KEYS:
        val = args.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        out[key] = val.strip()[:_MAX_DETAIL_CHARS]
        if len(out) >= 2:
            break
    return out


def _phase_for_tool(tool_name: str) -> str:
    """Phase implied by a tool call. Split on the MCP prefix rather than a
    prefix-strip so a bare name (the fork serves some tools unprefixed) keys the
    same as its mcp__lazy-tool-service__ form."""
    return _TOOL_PHASES.get((tool_name or "").rsplit("__", 1)[-1], _PHASE_RESEARCHING)


# The model narrating its plan instead of answering. Observed verbatim on a live
# research turn: it ran 18 research calls, then said "Now I have comprehensive
# data from three major review sources. Let me build a data_card with the best
# waterproof hiking sandals" — and ended the turn WITHOUT calling
# canvas_add_widget. Rendering that sentence as the answer produces a card whose
# body is a promise to make a card.
# Matched per SENTENCE, not against the whole reply: narration usually arrives as
# its own sentence appended to (or instead of) the answer, so an anchored
# whole-string pattern misses "…sources. Let me build a data_card." entirely.
_NARRATION_SENTENCE_RE = re.compile(
    r"^\s*(?:"
    r"(?:now|next|first|then|so|okay|ok|alright|great|perfect)[,!.]?\s+)?"
    r"(?:i\s*(?:'ll|'ve)?\s*(?:have|will|am|can|need|should|shall|"
    r"going\s+to|now\s+have)?|let\s+me|let's|i'll|i've)\b",
    re.I,
)
# Tool/mechanism talk: the model describing the machinery instead of the answer.
_TOOL_TALK_RE = re.compile(
    r"\b(?:data_card|canvas_add_widget|canvas_modify_dom|widget_type|"
    r"stock_card|scoreboard|a\s+widget|the\s+canvas)\b", re.I)


def _strip_agent_narration(text: str) -> str:
    """Drop the model's process commentary and stray ellipsis filler, leaving the
    substantive answer (if any). Used only by the no-widget fallback card, where
    the alternative is showing narration to the user as though it were the result.

    Observed verbatim on a live research turn: after 18 successful research calls
    the whole reply was `"... ... ... Now I have comprehensive data from three
    major review sources. Let me build a data_card with the best sandals."` —
    every word of it narration, and not one word of the answer it had gathered.
    That must become the honest "couldn't answer" card, not a card whose body is
    a promise to make a card."""
    body = (text or "").strip()
    # Runs of "..." are what a tool-only turn streams between calls.
    body = re.sub(r"(?:^|\s)\.{2,}(?=\s|$)", " ", body)
    kept = [
        s for s in re.split(r"(?<=[.!?])\s+", body)
        if s.strip()
        and not _NARRATION_SENTENCE_RE.match(s)
        and not _TOOL_TALK_RE.search(s)
    ]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def _text_answer_card_config(question: str, answer: str) -> dict:
    """A data_card carrying a prose answer, for the agent turn that answered but
    never touched the canvas. The alternative was a blank canvas plus a spoken
    answer — the failure that reads as "it called tools but no widget appeared".

    Titled from the QUESTION rather than the answer: the question is short and
    already scoped, whereas the answer's first line is often a full sentence.

    When the reply is too thin to BE an answer, say so plainly instead of
    rendering a card whose body is "...". Observed live with the MCP research
    tools down: the agent streamed 5 characters and committed nothing."""
    q = re.sub(r"\s+", " ", (question or "").strip())
    title = (q[:57] + "...") if len(q) > 60 else (q or "Answer")
    body = _strip_agent_narration(answer)
    if len(body) < _MIN_ANSWER_CHARS:
        return {
            "title": title[:60],
            "answer": ("I couldn't put together an answer for this one — the "
                       "research tools didn't return anything usable.\n\n"
                       "Try rephrasing, or ask something more specific."),
            "source_note": "no result — the agent finished without an answer",
        }
    return {
        "title": title[:60],
        "answer": body[:4000],
        "source_note": "answered without a canvas tool — rendered from the reply",
    }


# Media widgets (video, audio) are players, not data: the canvas can only ever
# play one thing at a time, so a new one replaces whatever's already playing.
# Data widgets (stock_card, scoreboard, notes, ...) coexist — a new one adds
# alongside the rest unless it shares a widget_id with an existing one.
_MEDIA_WIDGET_MARKERS = {
    "youtube_player": "youtubePlayerWidget",
    "mini_music_player": "musicPlayerWidget",
}




# Junk a scraper returns with success=True but zero real article text: stealth-
# browser error interstitials and consent/bot walls. crawl4ai in particular hands
# back "Oops, something went wrong" nav-chrome for Yahoo Finance's /m/ syndication
# pages (4-5KB of skip-links, NOT the article) — non-empty, so the old crawl4ai→
# playwright fallback never escalated and fed that garbage straight to the editor.
_SCRAPE_JUNK_SIGNATURES = (
    "oops, something went wrong",
    "please enable javascript",
    "enable cookies and javascript",
    "verify you are human",
    "pardon our interruption",
    "access to this page has been denied",
)


def _looks_like_junk_page(text: str) -> bool:
    if not text or len(text.strip()) < 120:
        return True
    head = text[:600].lower()
    return any(sig in head for sig in _SCRAPE_JUNK_SIGNATURES)


def _is_public_http_url(url: str) -> bool:
    """True only for http(s) URLs whose host resolves to PUBLIC addresses.

    read_web_page / the /widgets/embed reader fetch server-side (via
    scraper-service) with no host validation, so a crafted URL could probe
    loopback, RFC-1918 services or the cloud metadata endpoint from the
    scraper's network vantage point (SSRF). Resolve the host and refuse
    private/loopback/link-local/reserved ranges before fetching."""
    import ipaddress
    import socket
    try:
        parsed = urllib.parse.urlparse(url or "")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname
        if host.lower() in ("localhost", "metadata.google.internal"):
            return False
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return bool(infos)
    except Exception:
        return False


async def read_web_page(url: str, max_chars: int = 6000) -> dict:
    """Fetch and return the readable text of a page.

    Uses scraper-service's `auto` engine, which runs http (trafilatura article
    extraction) → playwright → vision with block detection, rather than the old
    crawl4ai-first path. crawl4ai's stealth browser returned "Oops, something
    went wrong" chrome for Yahoo Finance article pages while a plain http GET
    returns the full clean body — so the summaries were being written off nav
    junk. `auto` gets the real text (usually on the fast http phase) and its
    escalation still handles genuinely JS-heavy pages. A junk-content gate makes
    any remaining error interstitial fall through instead of being served.
    """
    # Bounded timeouts: the raw web-page tool and the embed reader call this
    # WITHOUT an outer wait_for, so an un-capped auto (90s default) + crawl4ai
    # fallback could hang a widget build ~135s. auto's fast http phase usually
    # answers in seconds; 25s + 20s caps the worst case near 45s.
    if not _is_public_http_url(url):
        logger.warning(f"[SSRF GUARD] refused non-public URL {url!r}")
        return {"error": f"URL refused: {url}", "is_error": True}
    content = await _scrape(url, engine="auto", timeout=25.0)
    if _looks_like_junk_page(content):
        # Last-ditch: crawl4ai occasionally renders a page auto's chain can't
        # (heavy SPA behind no bot wall). Only worth it when auto came back junk.
        alt = await _scrape(url, engine="crawl4ai", timeout=20.0)
        if not _looks_like_junk_page(alt):
            content = alt
    if _looks_like_junk_page(content):
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



async def _fetch_news_fetch_news_page_text(n):
    url = n.get("url", "")
    if not url:
        return ""
    try:
        page = await read_web_page(url, max_chars=4000)
        return "" if page.get("is_error") else (page.get("content") or "")
    except Exception:
        return ""


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
        results = []
    if not results:
        # Open-Meteo only knows cities/towns; it misses landmarks, neighborhoods,
        # small towns and misspellings ("Lake Tahoe", "Silicon Valley"), which
        # then rendered "Couldn't find a place called X." Fall through to the same
        # Nominatim geocoder the map path uses — it resolves those.
        alt = await geocode_nominatim(name)
        if alt and alt.get("lat") is not None:
            return {
                "name": alt.get("resolved") or name,
                "label": alt.get("resolved") or name,
                "latitude": alt["lat"],
                "longitude": alt["lon"],
            }
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
from app.config import PORT, PRISM_URL, LAZY_AGENT_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL, TTS_SERVICE_URL, MUSIC_PLAYER_URL, SCRAPER_SERVICE_URL, VAULT_SERVICE_URL, VAULT_SERVICE_TOKEN, OBSIDIAN_VAULT_DIR
import asyncio
import contextvars
import datetime
import hashlib
import itertools
import json
import os

# Scope every agent turn is attributed to on prism. Sent BOTH as x-project /
# x-username HEADERS (what prism actually reads) and in the payload body. The
# username must be `admin` so html-notes turns show up under admin in
# prism-client — with no headers prism recorded them as "anonymous", which is
# why they looked like they were never arriving.
AGENT_PROJECT = os.getenv("AGENT_PROJECT", "html-notes-client")
AGENT_USERNAME = os.getenv("AGENT_USERNAME", "admin")

# Persona id per gateway. The :5591 fork ships HtmlNotesPersona as a built-in
# ("HTML_NOTES"); canonical prism has no such built-in, so the equivalent is
# registered there as a CUSTOM agent via POST /custom-agents — prism derives the
# id from the name, hence CUSTOM_HTML_NOTES_CANVAS. Without a persona the run is
# UNSCOPED: prism hands the model ~79 tools (execute_python, create_skill, ...)
# and ~25k tokens of tool schemas, so it wanders off mid-turn — a follow-up was
# observed calling execute_python + read_page for 219s and never touching the
# canvas. The persona scopes the run to the widget tool set.
PRISM_AGENT_ID = os.getenv("PRISM_AGENT_ID", "CUSTOM_HTML_NOTES_CANVAS")
# The MCP server name lazy-tool-service registers itself under in Prism. The
# tool prefix (mcp__lazy-tool-service__*) derives from it, so it is load-bearing
# — see lazy-tool-service/src/services/PrismRegistrationService.ts.
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "lazy-tool-service")
FORK_AGENT_ID = os.getenv("FORK_AGENT_ID", "HTML_NOTES")
import pathlib
import time
import uuid
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html, _host_of, map_document_html, map_payload, _render_markdown, esc
import base64 as _base64


logging.basicConfig(level=logging.INFO)

import logging

# httpx logs every request URL at INFO — including query strings, which is how
# the TomTom key (a ?key= param on every proxied tile fetch) ended up in the
# logs in plaintext. Secrets travel in URLs in several integrations, so cap
# httpx at WARNING globally rather than redacting call sites one by one.
logging.getLogger("httpx").setLevel(logging.WARNING)

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





# How many agent turns may generate at once. Beyond this, turns queue.
AGENT_CONCURRENCY = int(os.getenv("AGENT_CONCURRENCY", "4"))
_turn_semaphore = asyncio.Semaphore(AGENT_CONCURRENCY)

# canvas_read_dom / canvas_modify_dom arrive as separate HTTP calls from the
# gateway carrying no session id, so they resolve against the most recent one.
_last_active_session: str = ""











# Container 2nd-class → the widget_type the fast lane / router / add_widget uses.
# Keeps the summary's type names identical to canvas_add_widget's widget_type so a
# reuse decision ("there's already a map") maps straight to an id the caller can
# pass back. Without this, maps/weather/products/stock were all reported as
# "custom", so the router couldn't tell one was already open and spawned a second.
_CANVAS_CLASS_TYPE = {
    "map-widget": "map", "weather-widget": "weather", "data-card": "data_card",
    "image-widget": "image", "products-widget": "products", "chart-widget": "chart",
    "scoreboard": "scoreboard",
    "crypto-card": "crypto_card", "wallet-graph-widget": "wallet_graph",
}
_CANVAS_XDATA_TYPE = {
    "checklistWidget": "checklist", "clockWidget": "clock", "notesWidget": "notes",
    "musicPlayerWidget": "mini_music_player", "youtubePlayerWidget": "youtube_player",
    "stockCardWidget": "stock_card", "converterWidget": "converter",
    "cryptoCardWidget": "crypto_card",
    "reminderWidget": "reminder", "settingsWidget": "settings",
}










# Types that should exist at most once on the canvas: a new ask UPDATES the open
# one instead of adding a second. Media (video/music) already swap via
# _place_media_widget; these are the data widgets that were stacking duplicates.
SINGLETON_WIDGET_TYPES = {"map", "weather"}

# Roles that should exist at most once, identified by their widget id PREFIX
# rather than their widget_type. Needed because a role's rendered type is not
# stable: traffic renders as `map` or `iframe_app` depending on whether the place
# geocoded, so type-keyed reuse silently stacked a duplicate whenever consecutive
# asks landed on different sides of that fork. Deliberately narrow — a shared
# prefix means "same slot on the canvas", which is only true for these roles;
# widening it to e.g. "answer" would merge two unrelated cards into one.
SINGLETON_ROLE_PREFIXES = {"traffic", "map", "weather"}

# Answer-style cards where a *follow-up on the same thread* should refine the open
# card in place instead of stacking a new one — but a genuinely NEW subject still
# gets its own card. Unlike SINGLETON_WIDGET_TYPES (always one), reuse here is
# conditional: the ask must read as a follow-up (deictic phrasing) or share a
# subject with the open card. This is the deterministic half of the "stop making a
# fresh widget for every follow-up" fix; the model-driven target decision is P2.
TOPIC_SINGLETON_TYPES = {"data_card", "scoreboard", "stock_card",
                         "profile_card", "timeline", "crypto_card", "wallet_graph"}

# Every type a follow-up may UPDATE IN PLACE. The factory renders 24 types but
# only a subset is reuse-eligible, so "only the waterproof ones" against a
# products grid or "make it a bar chart" against a chart could NEVER edit the
# open widget — it stacked a duplicate, by construction rather than by bug.
# Deliberately excluded: mini_music_player / youtube_player (media swap already
# goes through _place_media_widget), clock / notes / iframe_app / converter
# (user-owned state — a mistargeted follow-up would destroy content the user
# typed or reset a running timer). reminder IS reusable: "actually make that 30
# minutes" must retarget the open countdown, not stack a second alarm.
REUSABLE_WIDGET_TYPES = (
    SINGLETON_WIDGET_TYPES | TOPIC_SINGLETON_TYPES
    | {"products", "chart", "multi_chart", "checklist", "image", "reminder",
       "table", "kpi_row", "versus_card", "progress"}
)

# Overlap at or above this counts as "this ask is about that widget". 0.5 was
# measured against real follow-ups: it clears "what about cheaper sandals?" vs
# "Best Waterproof Sandals" (0.50) while leaving a genuinely new subject at 0.
_REUSE_SCORE_THRESHOLD = 0.5

# Deictic follow-up phrasing: the ask points back at the current thread rather than
# opening a new subject. "tell me about X" is deliberately NOT here (it's a fresh
# topic); "what about X", "wait...", "tell me more", a leading pronoun, etc. are.
_FOLLOWUP_RE = re.compile(
    r"^\s*(?:wait|hold on|hmm+|ok(?:ay)?|so|but|actually|and|also|oh)\b"
    r"|(?:what|how)\s+about\b"
    r"|what\s+(?:happened|else|other)\b"
    r"|tell\s+me\s+more\b|(?:more|go\s+deeper|expand|elaborate|dig\s+in)\b"
    r"|^\s*(?:it|its|it's|that|this|those|these|they|them|their|he|she|his|her)\b"
    r"|^\s*(?:why|how\s+come|and\s+then|what\s+next)\b",
    re.I,
)

# Refinement openers _FOLLOWUP_RE misses. "only show waterproof ones" / "just the
# cheap ones" name no pronoun and open no new subject — they narrow what is
# already on screen. Measured: with a widget open, the agent answered these in
# prose and called NO tools, so the canvas never changed; phrasing the SAME ask
# explicitly ("update the existing widget to only show waterproof ones") made it
# call canvas_add_widget correctly. So detect them and make the ask explicit.
_REFINE_RE = re.compile(
    r"^\s*(?:only|just|filter|narrow|limit|sort|order|group|rank|"
    r"show\s+(?:only|just|me\s+only)|make\s+it|change\s+it|turn\s+it|"
    r"swap|replace|instead|drop\s+the|remove\s+the|add\s+the|without)\b",
    re.I,
)


# Refinement markers that are NOT sentence openers, so the ^-anchored _REFINE_RE
# misses them: "waterproof only please", "cheaper ones", "under $50". These carry
# no subject of their own — an anaphor ("ones"), a comparative, or a bare
# constraint — so they can only mean "narrow what is already on screen".
_REFINE_MARKERS_RE = re.compile(
    r"\b(?:ones?|instead|version|variant|option)\b"
    r"|\b(?:cheap|expensive|big|small|fast|slow|new|old|close|short|long|"
    r"high|low|good|bad|light|heavy|wide|narrow)(?:er|est)\b"
    r"|\b(?:under|over|below|above)\s*\$?\d"
    r"|\b(?:less|more|fewer|cheaper)\s+than\b"
    r"|\bonly\b|\bjust\b",
    re.I,
)


def _is_refining_followup(message: str) -> bool:
    """True when the ask reads as a refinement of what is already on screen —
    deictic ("what about the cheaper ones", "tell me more"), a narrowing opener
    ("only show waterproof ones"), or a non-opener narrowing marker ("cheaper
    ones", "under $50"). Callers must ALSO require that a focus widget actually
    exists before acting on this."""
    m = message or ""
    return bool(_FOLLOWUP_RE.search(m) or _REFINE_RE.search(m)
                or _REFINE_MARKERS_RE.search(m))


# Tokens that carry no subject signal — dropped before measuring topic overlap.
_SUBJECT_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "what", "whats", "how", "about", "me", "my", "i", "need",
    "you", "please", "tell", "show", "give", "with", "get", "want", "some",
    "latest", "news", "update", "updates", "now", "today", "current", "recent",
    # Filler that dilutes the overlap coefficient. "is teva any good?" against a
    # card listing "Teva, Chaco, Keen" scored 0.33 (1 of 3 query tokens) and
    # missed the 0.5 threshold purely because "any"/"good" carry no subject —
    # the distinctive proper-noun hit is the whole signal.
    "any", "all", "good", "bad", "best", "worst", "one", "ones", "thing",
    "things", "like", "know", "see", "look", "much", "many", "really", "very",
    "still", "even", "also", "just", "only", "more", "less", "other", "another",
    "same", "different", "kind", "sort", "type", "stuff", "there", "here",
    # Deictic narrative filler: "what happened next?" is a pure follow-up, but
    # "happened"/"next" counted as subject tokens and made the fresh-subject
    # guard read it as a new topic.
    "happened", "next", "then", "else", "again", "info", "information",
    # Vague-scope filler. Live: "tell me more about the deals at costco
    # anything hardware related?" scored 2/5 against the costco card — under
    # the 0.5 threshold purely because "anything"/"related" diluted the
    # overlap. The subject is {deals, costco, hardware}; these words scope it,
    # they don't name it.
    "anything", "something", "everything", "related", "specifically", "etc",
}


def _subject_tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _SUBJECT_STOP and len(w) > 2}


def _tok_edit_within(a: str, b: str, cap: int) -> bool:
    """Levenshtein(a, b) <= cap, with early exits. Tiny inputs only (tokens)."""
    if abs(len(a) - len(b)) > cap:
        return False
    if a == b:
        return True
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[-1])
        if best > cap:
            return False
        prev = cur
    return prev[-1] <= cap


def _fuzzy_hit(tok: str, toks: set) -> bool:
    """Does `tok` appear in `toks`, allowing a typo's worth of edit distance?

    Word-boundary EXACT matching exists because loose matching caused real
    wrong-widget edits ("john" ⊂ "Johnny"). Fuzziness here is bounded so it
    can only absorb misspellings, never substrings or different words:
      len < 4  → exact only ("map"/"mop" must not match)
      len 4-7  → edit distance 1 ("mikku"→"miku", "jass"→"jazz")
      len 8+   → edit distance 2 ("birkenstok"→"birkenstock")
    """
    if tok in toks:
        return True
    cap = 0 if len(tok) < 4 else (1 if len(tok) < 8 else 2)
    if not cap:
        return False
    return any(_tok_edit_within(tok, t, cap) for t in toks)


def _subject_overlap(a: str, b: str) -> float:
    """Overlap coefficient of the content words in two short strings (0..1).
    Robust to length differences (a 2-word query vs a longer widget title).
    Typo-tolerant via _fuzzy_hit — "birkenstok" still counts against a
    Birkenstock card."""
    ta, tb = _subject_tokens(a), _subject_tokens(b)
    if not ta or not tb:
        return 0.0
    hits = sum(1 for t in ta if _fuzzy_hit(t, tb))
    return hits / min(len(ta), len(tb))




def _ledger_details(session_id: str) -> Dict[str, str]:
    """widget_id -> its most recent content gist from the turn ledger. This is
    what _widget_detail was always recorded FOR ("what about the taco bell
    one?"); until now nothing consumed it for targeting."""
    out: Dict[str, str] = {}
    try:
        for entry in _session_turn_ledger.get(session_id, []):
            for w in entry.get("widgets", []):
                if w.get("id") and w.get("detail"):
                    out[w["id"]] = w["detail"]
    except Exception as e:
        logger.warning(f"_ledger_details failed: {e}")
    return out




# ── Stacking in-place updates ────────────────────────────────────────────────
# A follow-up that updates a widget used to hard-REPLACE its content, so two
# quick follow-ups on the same thread wiped data the user was still reading
# (live: a market-news card replaced itself twice in a row and the middle
# update was never seen). Same-widget data_card updates now STACK: newest
# content on top, previous content kept under an "Earlier" rule, until the
# card reaches a word budget — then the oldest text rolls off the bottom.
# In-memory per session, same lifetime as the turn ledger.
_session_widget_configs: Dict[str, Dict[str, dict]] = {}
_STACK_WORD_BUDGET = 800     # user-tuned: "500-1000 words" — split the range
_STACK_MIN_KEEP = 60         # don't bother stacking a stub smaller than this

_EARLIER_RULE = "\n\n---\n\n**Earlier**\n\n"




def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))




# ── Conversation self-awareness: the turn ledger ─────────────────────────────
# A compact per-session record of what each turn did — the message, the route,
# and the widgets it produced with a short gist of their content. This is the
# substrate that lets every routing tier reason about the thread ("3 turns ago I
# built a news card about military health; this follow-up refines it") instead of
# re-reading canvas HTML or a message log with widget contents stripped out.
# In-memory: same lifetime as the canvas version / media cursors — it only needs
# to survive within a conversation.
_session_turn_ledger: Dict[str, List[dict]] = {}
_LEDGER_MAX_TURNS = 10














def _followup_target_id(session_id: str, focus_id: Optional[str],
                        message: str) -> Optional[str]:
    """The widget a refining follow-up should edit.

    focus_id is pure RECENCY (the last widget built), and the follow-up
    directive/rewrite used to hard-target it. That's right for deictic asks
    ("tell me more", "make it a table") but wrong the moment the follow-up
    NAMES a subject: live, "tell me more about the deals at costco" arrived
    right after a Birkenstock card was built, tripped the "tell me more"
    trigger, and the directive ordered an in-place rewrite of the SANDALS
    widget. The topical scorer that would have picked the Costco card
    (find_reuse_target) never ran — the directive pre-empted it.

    So: score the message against EVERY canvas widget (title + ledger gist,
    the same two signals find_reuse_target uses — but across ALL types, since
    the directive can't know which widget_type the model will pick). A
    confident topical winner beats recency; a subject-free deictic ask scores
    0 everywhere and keeps the recency focus."""
    scored = []
    canvas_html = ""
    try:
        canvas_html = get_session_canvas(session_id)
        details = _ledger_details(session_id)
        for order, (wid, _wtype, title) in enumerate(_iter_canvas_widgets(canvas_html)):
            if not wid or wid == "unknown":
                continue
            scored.append((_score_widget_for_query(message, title or "",
                                                   details.get(wid, "")), order, wid))
    except Exception as e:
        logger.warning(f"_followup_target_id scoring failed: {e}")
    top = [c for c in scored if c[0] >= _REUSE_SCORE_THRESHOLD]
    if top:
        best = max(s for s, _, _ in top)
        # Contenders within epsilon of the best. A name can legitimately live
        # in several gists — live, "tell me more about jimothy" scored 1.00
        # against BOTH the jimothy card and a reddit-lawsuit card whose gist
        # mentioned him, and first-in-DOM-order won: the lawsuit card got
        # overwritten with meme-coin content. On a tie the recency focus wins
        # (it's the thread the user is in); otherwise the most recent match.
        contenders = [c for c in top if best - c[0] <= 0.1]
        if focus_id and any(wid == focus_id for _, _, wid in contenders):
            return focus_id
        _score, _order, best_id = max(contenders, key=lambda c: (c[0], c[1]))
        if best_id != focus_id:
            logger.info(f"[WIDGET TARGET] follow-up names a subject — topical "
                        f"#{best_id} (score {_score:.2f}, {len(contenders)} "
                        f"contender(s)) beats recency #{focus_id}")
        return best_id
    # Title+gist missed. Before falling back, look INSIDE the widgets: the
    # canvas holds every card's full rendered text, and the name a follow-up
    # references is usually in a card BODY the ≤200-char gist can't cover.
    # Live: a 2000-char sushi card listed "Miku" ~700 chars in — gist blind,
    # so "tell me more about Miku" targeted the newest widget (a video) and
    # became Hatsune Miku. Require EVERY subject token to appear in the body
    # (full coverage — the tightest signal available), and take a UNIQUE hit
    # only; two body matches mean the name is ambiguous on canvas, and
    # guessing between them is how wrong-widget edits happen.
    msg_toks = _subject_tokens(message)
    if msg_toks and canvas_html:
        try:
            soup = BeautifulSoup(canvas_html, "html.parser")
            hits = []
            for card in soup.select(".glass-card, .widget-container"):
                wid = card.get("id", "")
                if not wid or wid == "unknown":
                    continue
                body_toks = _subject_tokens(card.get_text(" ", strip=True))
                if all(_fuzzy_hit(t, body_toks) for t in msg_toks):
                    hits.append(wid)
            if len(hits) == 1:
                logger.info(f"[WIDGET TARGET] follow-up subject found in the BODY "
                            f"of #{hits[0]} — beats recency #{focus_id}")
                return hits[0]
        except Exception as e:
            logger.warning(f"_followup_target_id body scan failed: {e}")
    # No widget matches. If the message CARRIES a subject anyway, it's a new
    # topic that happened to trip the (loose) refinement regex — live, "find
    # me MORE info on birkenstock arizona" matched `more\b` right after the
    # costco card was built, and the recency fallback ordered birkenstock
    # content INTO the costco card. Two or more content words that match
    # nothing on canvas mean a fresh subject: no directive, let the agent
    # open a new widget. One or zero content words ("tell me more", "what
    # about the cheaper ones") is genuinely deictic — keep the recency focus.
    if len(_subject_tokens(message)) >= 2:
        logger.info(f"[WIDGET TARGET] follow-up phrasing but fresh subject "
                    f"(no canvas match) — not forcing an in-place update")
        return None
    return focus_id












@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _warn_if_research_is_down()
    watchdog = asyncio.create_task(_mcp_watchdog())
    try:
        yield
    finally:
        watchdog.cancel()


app = FastAPI(
    title="HTML-Notes Engine",
    description="Local-first AI knowledge journal with constrained HTML rendering",
    lifespan=_lifespan,
)


async def _try_reconnect_mcp() -> bool:
    """Ask prism to dial our MCP server, returning True if it came up.

    The outage behind the 2026-07-20 debug wave was exactly one un-dialled
    connection: registration present, `enabled: true`, the :5591/mcp/sse endpoint
    live and serving — prism had simply never connected, with `lastError: null`.
    It was invisible for hours and fixed by a single POST. So do that POST
    ourselves rather than waiting for someone to notice.

    GOTCHA worth keeping: `GET /mcp-servers` is SCOPED BY HEADERS. Without
    x-project/x-username it returns `[]` for ANY state, which reads as "nothing is
    registered" and sends you looking in the wrong place entirely."""
    headers = {"x-project": AGENT_PROJECT, "x-username": AGENT_USERNAME}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            servers = (await client.get(f"{PRISM_URL}/mcp-servers",
                                        headers=headers)).json()
            servers = servers if isinstance(servers, list) else servers.get("servers", [])
            mine = next((s for s in servers if s.get("name") == MCP_SERVER_NAME), None)
            if not mine:
                logger.error(f"[MCP] {MCP_SERVER_NAME} is not registered for "
                             f"{AGENT_PROJECT}/{AGENT_USERNAME} — cannot reconnect; "
                             f"lazy-tool-service registers itself on ITS boot.")
                return False
            if mine.get("connected") and int(mine.get("toolCount") or 0) > 0:
                return True
            sid = mine.get("id") or mine.get("_id")
            logger.warning(f"[MCP] {MCP_SERVER_NAME} registered but not connected "
                           f"(toolCount={mine.get('toolCount')}) — asking prism to dial it")
            await client.post(f"{PRISM_URL}/mcp-servers/{sid}/connect", headers=headers)
            await asyncio.sleep(3)
            servers = (await client.get(f"{PRISM_URL}/mcp-servers",
                                        headers=headers)).json()
            servers = servers if isinstance(servers, list) else servers.get("servers", [])
            mine = next((s for s in servers if s.get("name") == MCP_SERVER_NAME), None) or {}
            tools = int(mine.get("toolCount") or 0)
            if mine.get("connected") and tools > 0:
                logger.info(f"[MCP] reconnected — {tools} tools")
                return True
            logger.error(f"[MCP] reconnect did not take (connected="
                         f"{mine.get('connected')}, toolCount={tools})")
            return False
    except Exception as e:
        logger.warning(f"[MCP] reconnect attempt failed: {e}")
        return False


async def _mcp_watchdog() -> None:
    """Re-check the MCP link periodically and reconnect on a drop.

    Never retries hot: one attempt per interval, and every attempt is logged so a
    flapping link is visible rather than silently papered over."""
    while True:
        try:
            await asyncio.sleep(_MCP_WATCHDOG_INTERVAL)
            status = await _agent_dependency_status()
            if not status.get("ok"):
                logger.warning(f"[MCP] watchdog saw a degraded research path: "
                               f"{status.get('error')}")
                await _try_reconnect_mcp()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[MCP] watchdog iteration failed: {e}")


_MCP_WATCHDOG_INTERVAL = int(os.getenv("MCP_WATCHDOG_SECONDS", "300"))


async def _warn_if_research_is_down() -> None:
    """Shout at boot if tier-3 research can't work.

    The MCP registration lives in another service and can silently go empty (a
    prism restart drops it). Nothing noticed until a user asked a research
    question minutes or days later and got a text-only answer — the failure was
    indistinguishable from "the model chose not to make a widget". Check once at
    boot so the container log names the problem the moment it exists.

    Never raises: a boot check that can take the app down is worse than the bug
    it reports."""
    try:
        status = await _agent_dependency_status()
        if not status.get("ok") and await _try_reconnect_mcp():
            status = await _agent_dependency_status()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[BOOT] agent dependency check failed to run: {e}")
        return
    # MCP being up is not enough: with every search backend unreachable the agent
    # has tools that all return nothing, which is what "research path OK" claimed
    # for an unknown length of time while DuckDuckGo was unreachable.
    try:
        _hits, engines_down = await web_search_ex("test", 3)
    except Exception as e:
        _hits, engines_down = [], True
        logger.warning(f"[BOOT] search probe raised: {e}")
    if engines_down:
        logger.error(
            "[BOOT] WEB SEARCH IS DOWN — every search backend is unreachable. "
            "Research asks will have no data to work from. Check "
            "BRAVE_SEARCH_API_KEY in the vault and outbound access to "
            "api.search.brave.com.")
    if status.get("ok") and not engines_down:
        logger.info(f"[BOOT] research path OK — {status.get('tool_count')} MCP tools "
                    f"via {status.get('prism')}, web search reachable")
        return
    if status.get("ok"):
        return
    logger.error(
        "[BOOT] RESEARCH PATH DOWN: %s. Tier-2 asks (weather/stock/sports/map) "
        "still work; tier-3 research asks will answer in text with no widget. "
        "Detail: %s",
        status.get("error", "unknown"), json.dumps(status)[:400])

# Request / Response Schemas

class MessageRequest(BaseModel):
    session_id: str
    message: str
    target_note_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    current_canvas: Optional[str] = None
    # The widget the question came FROM, when the client knows it (the last one
    # the user interacted with, or an explicit "ask about this"). Everything else
    # in follow-up targeting is inference from the message text; this is a fact,
    # so it outranks the model's guess AND the topical score. Optional — the
    # server still infers when it's absent.
    focus_widget_id: Optional[str] = None
    # Version of the canvas the client's current_canvas snapshot is based on
    # (the last `component` version it painted, or the seed from history load).
    # Lets _run_turn refuse a snapshot taken before another turn's commit.
    canvas_version: Optional[int] = None
    # False (default) = PRISM MODE: the agent runs on prism-service (:7777) with the
    # lazy-tool-service MCP research tools/harnesses, AND research/content asks
    # (products, general answers, images) are routed to that agent instead of being
    # short-circuited by local search-scrape builders. True = legacy: the :5591 fork
    # gateway + local fast-path builders (faster, but no real research).
    use_lazy_agent: bool = False

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
# never what "news" means. Recency words flip the search to order='date' (a
# freshness NUDGE — the blended scorer still runs).
RECENCY_RE = re.compile(r'\b(news|latest|newest|recent|recently|today|tonight|breaking|update|updates|current|new)\b')

# STRICT recency: the user wants THE most recently published video, not the most
# relevant/popular one ("newest Paul Barron Network video", "bitcoin video from an
# hour ago"). This is stronger than RECENCY_RE — it BYPASSES the relevance/views
# scorer and the variety picker entirely and sorts purely by publish time, because
# freshness is only a 0.4-weight, year-scaled axis that can't tell 42 minutes from
# 3 days, so a brand-new upload always loses to an older popular one on the blend.
NEWEST_RE = re.compile(
    r'\b(newest|most[\s-]?recent|latest|just (?:posted|uploaded|dropped|released|out)|'
    r'brand[\s-]?new|freshest|last upload|latest upload)\b'
    r'|\b(?:in|from|over|within) the (?:past|last) (?:hour|few hours|day|24\s?h(?:ours?)?)\b'
    r'|\b\d+\s+(?:minutes?|mins?|hours?|hrs?)\s+ago\b'
    r'|\b(?:an?|one)\s+hour\s+ago\b'
    r'|\bthis (?:morning|afternoon|evening)\b|\bmoments ago\b|\bjust now\b', re.I)

# Words that describe the medium, not the subject. "fifa news video" should search
# YouTube for "fifa news", not for the literal word "video".
VIDEO_FILLER = {
    "video", "videos", "yt", "youtube", "clip", "clips", "watch", "pull", "up", "show",
    "me", "a", "an", "the", "of", "some", "play", "find", "get", "please", "for", "on",
    "give", "want", "see", "put", "add", "open", "stream", "streams", "streaming",
    "live", "livestream", "livestreams",
    # Format words: the FORM axis (parse_video_form) carries them, so they must
    # not leak into the channel-name guess or the topic filter — "newest mkbhd
    # short" was searching channels for "mkbhd short" and filtering that
    # creator's titles for the literal word "short".
    "short", "shorts",
    # Conversational scaffolding. Without these "i want to watch primeagen"
    # produced the channel-name guess "i to primeagen", which bound nothing and
    # dropped the ask to keyword search.
    # "new" is NOT here: it belongs to real channel names (New Rockstars). The
    # leading-"new" peel in _split_video_subject_topic handles "a new X video".
    "i", "you", "we", "to", "can", "could", "would", "let", "us", "my", "from",
    "by", "something", "anything", "there", "is", "are", "any",
}


def clean_video_query(text: str) -> str:
    """Strip medium words so the search hits the subject.
    "pull up a fifa news video" → "fifa news"."""
    cleaned = re.sub(r'[^\w\s]', ' ', (text or '').lower())
    kept = [w for w in cleaned.split() if w not in VIDEO_FILLER]
    return " ".join(kept).strip() or (text or "").strip()


def pick_best_video(hits: list, exclude_ids: set = None):
    """Deterministic pick: the top-ranked hit not yet shown this session.

    Replaces pick_varied_video at selection time (user decision: no
    random.choice — the ranking pipeline already ordered hits best-first, so
    take its word). Variety across repeat asks comes from `exclude_ids` (the
    session's shown-video window): the first unseen hit wins, and when EVERY
    hit was already shown we ignore the exclusion rather than return nothing —
    same exhaustion rule as pick_varied_video. Returns (chosen, other_ids) with
    other_ids in rank order so the embed-error fallback hops to the next-best."""
    if not hits:
        return None, []
    unseen = [h for h in hits if h.get("video_id") not in exclude_ids] if exclude_ids else list(hits)
    chosen = (unseen or hits)[0]
    cid = chosen.get("video_id")
    others = [v["video_id"] for v in unseen if v.get("video_id") and v.get("video_id") != cid]
    others += [v["video_id"] for v in hits
               if v.get("video_id") and v["video_id"] != cid and v["video_id"] not in others]
    return chosen, others


def pick_varied_video(hits: list, k: int = 5, exclude_ids: set = None, wide: bool = False):
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
    fresh_only = [h for h in hits if h.get("video_id") not in exclude_ids] if exclude_ids else list(hits)
    exhausted = not fresh_only  # every top hit already shown this session
    fresh = fresh_only or hits  # all already seen → fall back to the full list
    # Widen the sampling window when the query is broad (`wide`) or the seen-set has
    # exhausted the fresh pool — otherwise a broad/repeated ask recycles the same
    # top-k evergreen hits forever. A wider window rotates genuinely different videos.
    window = max(1, k * 2 if (wide or exhausted) else k)
    pool = fresh[:window]
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

# Freshness parsed from the ORIGINAL user message of the tier-3 agent turn now
# in flight. /internal/execute is a separate HTTP request from lazy-agent-service
# carrying only {tool, args} — no session correlation exists — so this module
# global is how the tool handler recovers a time constraint the agent's query
# rewrite dropped. TTL-bounded and consulted LAST (after the agent's `freshness`
# arg and the query's own words). Caveat: two CONCURRENT agent turns could
# cross-apply a freshness bias; acceptable for this single-user deployment, and
# it is only a bias — the arg/query paths win whenever they carry a signal.
_pending_turn_freshness: Optional[tuple] = None   # (time.monotonic(), Freshness)
_TURN_FRESHNESS_TTL = 180.0


# The format axis rides along in the same stash and for the same reason: the
# agent's query rewrite drops "short" ("newest mkbhd short" → "mkbhd latest
# video") just as readily as it drops "this week".
_pending_turn_form: Optional[tuple] = None       # (time.monotonic(), form)


def _stash_turn_freshness(message: str) -> None:
    global _pending_turn_freshness, _pending_turn_form
    f = parse_freshness(message)
    _pending_turn_freshness = (time.monotonic(), f) if f else None
    form = parse_video_form(message)
    _pending_turn_form = (time.monotonic(), form) if form else None


def _clear_turn_freshness() -> None:
    global _pending_turn_freshness, _pending_turn_form
    _pending_turn_freshness = None
    _pending_turn_form = None


def _stashed_turn_freshness() -> Optional[Freshness]:
    if _pending_turn_freshness:
        ts, f = _pending_turn_freshness
        if time.monotonic() - ts < _TURN_FRESHNESS_TTL:
            return f
    return None


def _stashed_turn_form() -> Optional[str]:
    if _pending_turn_form:
        ts, form = _pending_turn_form
        if time.monotonic() - ts < _TURN_FRESHNESS_TTL:
            return form
    return None

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


# ── Channel- and date-verified recency video selection ──────────────────────
# "fox news video newest about the stock market" once returned a 40-view clip
# from an unrelated channel: RECENCY_RE stripped "news" out of the channel-name
# guess (so FOX News could never resolve to its uploads feed), and the fallback
# date-sorted a polluted query and picked at random — no channel check, no
# relevance floor. Verify BEFORE pulling: bind the named channel and read its
# strictly newest-first uploads feed (channel-correct by construction); only
# fall back to search when no channel binds, and even then hold a strict
# "newest" ask to a title-relevance floor (see _search_youtube_scrape).

_VIDEO_TOPIC_SPLIT_RE = re.compile(
    r'\b(?:about|regarding|covering|on the topic of|talking about)\b', re.I)
# Soft recency words safe to strip from a channel-subject guess. Deliberately
# EXCLUDES "news" and "new" — they are part of real channel names (FOX News,
# Sky News, New Rockstars); stripping them is exactly the bug this fixes.
_SUBJECT_RECENCY_RE = re.compile(
    r'\b(latest|newest|recent|recently|today|tonight|breaking|current|'
    r'update|updates)\b', re.I)


def _split_video_subject_topic(message: str) -> tuple:
    """"fox news video newest about the stock market" → ("fox news",
    "stock market"). The subject is the channel-name guess (recency and medium
    words stripped, channel-name words KEPT); the topic is the about-clause,
    used to filter that channel's uploads. No about-clause → topic ""."""
    text = NEWEST_RE.sub(" ", message or "")
    parts = _VIDEO_TOPIC_SPLIT_RE.split(text, maxsplit=1)
    subject = clean_video_query(_SUBJECT_RECENCY_RE.sub(" ", parts[0]))
    # "a new primeagen video" left the subject as "new primeagen", which searched
    # channels for the literal words and bound nothing. The peeled form is
    # returned as a HINT rather than applied destructively: "new" is also the
    # first word of real channel names (New Rockstars), so the caller tries the
    # full subject first and only falls back to the peeled one. Peeling here
    # unconditionally turned "new rockstars video" into "rockstars".
    subject = subject.strip()
    # clean_video_query falls back to its raw input when every word was filler
    # ("newest video about X" → subject "video"). A channel GUESS made of pure
    # filler must be empty — otherwise we scrape a channel search for "video".
    if all(w in VIDEO_FILLER or _SUBJECT_RECENCY_RE.fullmatch(w)
           for w in re.findall(r"\w+", subject.lower())):
        subject = ""
    topic = clean_video_query(parts[1]) if len(parts) > 1 else ""
    return subject, topic


# Words that make a subject a TOPIC, not a creator. A channel-name guess built
# only from these must never bind a channel: "cookie recipe" resolves to a real
# channel called "1,000 Cookie Recipes", which would then answer every cookie
# question from one creator's feed instead of searching YouTube properly.
# Deliberately generic/instructional vocabulary only — never proper nouns.
_YT_TOPIC_WORDS = {
    "recipe", "recipes", "tutorial", "tutorials", "guide", "guides", "review",
    "reviews", "highlights", "explained", "how", "what", "why", "best", "top",
    "vs", "versus", "comparison", "cooking", "workout", "exercise", "music",
    "song", "songs", "movie", "movies", "trailer", "game", "games", "gameplay",
    "documentary", "lecture", "course", "tricks", "hacks", "diy",
    "unboxing", "reaction", "compilation", "meditation", "interview",
    "market", "stock", "stocks", "crypto", "weather", "score", "scores",
    "price", "prices", "history", "science", "math", "coding", "programming",
    # NOT listed, though they look generic: "tips" (Linus Tech Tips), "news"
    # (Fox News), "podcast", "clips", "highlights" — all appear in real channel
    # names, and listing them made those creators unbindable.
}


def _creator_evidence(subject: str, message: str = "") -> str:
    """How strongly this subject is a CREATOR rather than a topic:
    'explicit' (a possessive/from-phrase names them outright), 'weak' (contains
    generic topic vocabulary, so a bind needs corroboration), or 'plain'.

    A channel bind answers from ONE feed and skips search entirely, so a topic
    ask must not trigger it: 'cookie recipe' resolves to a real channel called
    '1,000 Cookie Recipes', which would answer every cookie question from that
    creator forever. Rather than curate a word list (brittle, and endless), the
    caller pairs 'weak' with a higher match+verified bar — evidence, not
    vocabulary."""
    subj = (subject or "").strip().lower()
    if not subj:
        return "weak"
    msg = (message or "").lower()
    if re.search(r"\b(?:from|by)\s+" + re.escape(subj[:40]), msg) \
       or re.search(re.escape(subj[:40]) + r"\s*'s\b", msg) \
       or re.search(r"\bchannel\b", msg):
        return "explicit"
    words = [w for w in re.findall(r"\w+", subj) if len(w) > 1]
    if words and any(w in _YT_TOPIC_WORDS for w in words):
        return "weak"
    # NOTE: capitalisation was tried as a "names are proper nouns" signal and
    # REJECTED — users type entirely in lowercase, so it marked 'fox news',
    # 'linus tech tips' and 'paul barron' as topics. Do not reintroduce it.
    # The plural tell below is safe because channel names are not pluralised
    # descriptions ('funny cat videos' vs 'New Rockstars').
    if len(words) > 1 and re.search(r"\b(?:videos|clips|compilations?)\b",
                                    (message or "").lower()):
        return "weak"
    return "plain"


def _topic_in_title(topic: str, title: str) -> bool:
    """At least half the topic's content words appear in the title (substring
    containment, so "stock" also matches "stocks"). Empty topic matches all."""
    toks = [w for w in re.findall(r"\w+", (topic or "").lower()) if len(w) > 2]
    if not toks:
        return True
    t = (title or "").lower()
    return sum(1 for w in toks if w in t) >= (len(toks) + 1) // 2


async def _recency_video_pick(message: str, session_id: str,
                              freshness: Optional[Freshness] = None) -> Optional[dict]:
    """Channel-verified pick for a video ask that NAMES a creator, or any ask
    carrying recency intent. Returns a youtube_player config, or None so the
    caller falls back to its generic search path.

    Order of trust:
      1. The named channel binds → its uploads RSS, strictly reverse-
         chronological and channel-verified by construction, topic-filtered
         when the ask has an about-clause. A topic with no recent upload falls
         back to the channel's newest (right channel > exact topic).
      2. No channel binds → date-ordered search when the ask wants recency;
         otherwise None, so a plain topic ask keeps relevance ranking.

    Deliberately NOT gated on a recency word by its callers: "primeagen video"
    means his LATEST, and requiring the user to say "newest" was why keyword
    search kept serving 5-, 11- and 14-day-old clips over a 3-hour-old upload.
    """
    fresh = freshness or parse_freshness(message)
    # "THE newest X" has one right answer (no seen-exclusion rotation); softer
    # recency ("new", "news", "this week") is still newest-first but rotates to
    # the newest UNSEEN hit on a repeat ask.
    want_newest = bool(NEWEST_RE.search((message or "").lower()))
    # Short vs video. The uploads feed is where this bit mattered most: Shorts
    # dominate it by volume, so "newest <creator> video" answered with a Short
    # until the feed learned to tell them apart. None here means long-form.
    form = parse_video_form(message)
    window = fresh.window_days if fresh else None
    subject, topic = _split_video_subject_topic(message)
    search_q = " ".join(x for x in (subject, topic) if x) or clean_video_query(message)

    chans = []
    if subject:
        try:
            # Creators run several channels (ThePrimeagen / The PrimeTime /
            # ...Highlights). Binding only ONE meant "newest primeagen video"
            # answered from a feed whose head was 11 days old while the same
            # creator had posted 3 hours earlier on a sibling channel. Read the
            # top matches and merge, so "newest" means newest across all of them.
            # Try the subject as written FIRST ("new rockstars" is a channel),
            # then a leading-"new" peeled variant ("new primeagen" -> the
            # creator). Order matters: peeling first renamed New Rockstars.
            variants = [subject]
            peeled = re.sub(r'^new\s+', '', subject, flags=re.I).strip()
            if peeled and peeled != subject:
                variants.append(peeled)
            # Score BOTH variants and keep the stronger bind. Stopping at the
            # first that binds anything is not enough: "new primeagen" matches
            # ThePrimeagen weakly (0.65) and misses the sibling channel holding
            # his newest upload, while the peeled "primeagen" matches at 0.90
            # and finds all three.
            best_chans = []
            for cand_subject in variants:
                got = await _resolve_youtube_channels(
                    cand_subject, limit=4,
                    evidence=_creator_evidence(cand_subject, message))
                if got and (not best_chans
                            or got[0]["match"] > best_chans[0]["match"]):
                    best_chans, subject = got, cand_subject
            chans = best_chans
            # Merge only SIBLINGS of the best match — channels within 0.3 of the
            # winner's score. Taking a fixed top-N pulls in whatever search noise
            # fills the slots (a channel coincidentally titled like the query),
            # and a stranger's uploads must never be served as this creator's
            # "newest".
            if chans:
                best = chans[0]["rank_score"]
                chans = [c for c in chans if c["rank_score"] >= best - 0.3][:3]
        except Exception as e:
            logger.warning(f"[YOUTUBE] channel resolve failed for {subject!r}: {e}")
    chan = chans[0] if chans else None
    if chan:
        feeds = await asyncio.gather(
            *[_youtube_channel_uploads(c["channel_id"], limit=12, form=form)
              for c in chans],
            return_exceptions=True)
        merged, seen_ids = [], set()
        for c, f in zip(chans, feeds):
            if isinstance(f, Exception) or not f:
                continue
            for h in f:
                if h.get("video_id") and h["video_id"] not in seen_ids:
                    seen_ids.add(h["video_id"])
                    merged.append({**h, "channel": h.get("channel") or c["title"]})
        merged.sort(key=lambda h: h["age_days"] if h.get("age_days") is not None else 1e9)
        if len(chans) > 1:
            logger.info(f"[YOUTUBE] merged {len(merged)} uploads across "
                        f"{len(chans)} channel(s) for {subject!r}; newest is "
                        f"{(merged[0]['age_days'] if merged else 0):.2f}d "
                        f"from {(merged[0].get('channel') if merged else '?')!r}")
        feed = filter_blocked_videos(merged)
        if window:
            # An explicit window ("this week") bounds even the trusted channel
            # feed; empty-after-filter falls back to the feed head (right
            # channel, newest available) rather than nothing.
            feed = filter_by_age(feed, window) or feed
        pool = [h for h in feed if _topic_in_title(topic, h.get("title"))]
        if topic and feed and not pool:
            # The feed's newest uploads don't cover the topic. Before settling
            # for the channel's newest-overall, date-search the topic and keep
            # ONLY hits whose channel matches the one we verified — this finds
            # an on-topic upload that slipped past the feed window without ever
            # reopening the door to junk channels.
            searched = filter_blocked_videos(await search_youtube_videos(
                search_q, limit=12, order="date", freshness=fresh, form=form))
            verified = [h for h in searched
                        if _yt_channel_name_match(chan["title"], h.get("channel") or "")]
            if verified:
                verified.sort(key=lambda h: h["age_days"]
                              if h.get("age_days") is not None else 1e9)
                pool = verified
                logger.info(f"[YOUTUBE] topic {topic!r} found via channel-"
                            f"verified search ({len(verified)} hits from "
                            f"{chan['title']!r})")
            else:
                logger.info(f"[YOUTUBE] {chan['title']!r} has no recent upload "
                            f"matching {topic!r} — serving its newest instead")
        pool = pool or feed
        if pool:
            if want_newest:
                # "newest" is a factual ask with one right answer — no variety,
                # no seen-exclusion; the feed head IS the latest.
                top = pool[0]
                cands = [v["video_id"] for v in pool[1:] if v.get("video_id")][:5]
            else:
                # Softer recency: newest UNSEEN upload — deterministic, rotates
                # on repeat asks via the session's shown-video window.
                top, cands = pick_best_video(
                    pool, exclude_ids=_shown_video_ids(session_id))
                cands = cands[:5]
            if top:
                age_min = (top.get("age_days") or 0) * 1440
                logger.info(f"[YOUTUBE] recency pick via channel feed: "
                            f"{top['video_id']} from {chan['title']!r} "
                            f"(~{age_min:.0f} min old, topic={topic!r})")
                _remember_current_video(session_id, top, search_q)
                return {"video_id": top["video_id"],
                        "title": top.get("title") or search_q,
                        "query": search_q,
                        "published": top.get("published"),
                        "channel": chan["title"],
                        "candidates": cands}

    # No channel bound. A plain topic ask ("a cookie recipe video") must keep
    # RELEVANCE ranking — hand it back so the caller's normal search path runs.
    # Only an ask that actually wants recency continues into the date search.
    if not fresh:
        return None

    hits = filter_blocked_videos(await search_youtube_videos(
        search_q, limit=10, order="date", strict_recency=want_newest,
        rerank=True, freshness=fresh, form=form))
    if not hits:
        hits = filter_blocked_videos(
            await search_youtube_videos(search_q, limit=10, form=form))
    if not hits:
        return None
    if want_newest:
        hits = sorted(hits, key=lambda h: h["age_days"]
                      if h.get("age_days") is not None else 1e9)
        top = hits[0]
        cands = [v["video_id"] for v in hits[1:] if v.get("video_id")][:5]
    else:
        # Hits arrive newest-first from the strict pipeline; take the newest
        # unseen one deterministically.
        top, cands = pick_best_video(hits, exclude_ids=_shown_video_ids(session_id))
        cands = cands[:5]
    if not top:
        return None
    _remember_current_video(session_id, top, search_q)
    cfg = {"video_id": top["video_id"], "title": top.get("title") or search_q,
           "query": search_q, "candidates": cands}
    if top.get("stale_fallback"):
        cfg["title"] = f'{cfg["title"]} (newest available)'
        cfg["stale_fallback"] = True
    return cfg


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

# ── Crypto fast-lane detection ──────────────────────────────────────────────
# Deliberately CONSERVATIVE: the deterministic fast lane fires only on strong,
# unambiguous crypto signals; everything softer ("price of X") falls through to
# the LLM router, which now knows the crypto/wallet widget types. A cashtag
# ($PEPE) or a 0x contract is unambiguous; bare "coin"/"token" are NOT included
# alone because they'd steal non-crypto asks.
CRYPTO_WORD_RE = re.compile(
    r'\b(crypto\w*|blockchain|on[\s-]?chain|de[\s-]?fi|web3|memecoin|shitcoin|'
    r'altcoin|stablecoin|erc[\s-]?20|spl token|bitcoin|btc|ethereum|'
    r'dogecoin|doge|solana|sol|shiba|shib|pepe|bonk|cardano|ada|ripple|xrp|'
    r'litecoin|polkadot|avalanche|avax|chainlink|link|uniswap|uni|tether|usdt|'
    r'usdc|dai|matic|polygon|tron|trx|monero|xmr|dogwifhat|wif|floki|mog|'
    r'wojak|turbo|brett|degen|toshi)\b', re.I)
# Soft signal: "<name> token/coin/memecoin" is how people name a microcap
# ("jimothy token", "the doge coin"). "coin"/"token" are generic, so this only
# counts on a SHORT query (a lookup, not a sentence like "explain oauth tokens")
# — see the length guard in the fast lane. The builder pre-resolves via
# DexScreener and falls through on a miss, so a stray hit just costs one lookup.
CRYPTO_SOFT_RE = re.compile(r'\b(tokens?|coins?|memecoins?|shitcoins?)\b', re.I)
# A $CASHTAG ($PEPE) or a 0x… contract address — strong crypto/token signal.
CASHTAG_RE = re.compile(r'\$[A-Za-z][A-Za-z0-9]{1,9}\b')
EVM_ADDR_RE_MAIN = re.compile(r'\b0x[a-fA-F0-9]{40}\b')
# Holder-graph / distribution intent — "who holds X", "X whales", "is X a fair
# launch", "pump and dump", "wallet graph". Routes to the holder-network graph.
WALLET_GRAPH_RE = re.compile(
    r'\b(holders?|whales?|distribution|holder graph|wallet graph|connected wallets?|'
    r'pump[\s-]?and[\s-]?dump|pump.{0,4}dump|fair[\s-]?launch|fairly distributed|'
    r'who holds|concentration|top wallets|sybil|rug\w*|insiders?)\b', re.I)
# Inspect ONE address's holdings — needs an address AND holdings framing.
WALLET_INSPECT_RE = re.compile(
    r'\b(hold(?:s|ing|ings)?|balance|portfolio|owns?|inside|contents? of|whats? in)\b',
    re.I)
# A deep-dive / scam-check framing that means "written crypto report".
CRYPTO_REPORT_RE = re.compile(
    r'\b(deep[\s-]?dive|full (?:report|analysis|breakdown)|due diligence|\bdd\b|'
    r'report on|analyz[e]|analysis (?:on|of)|is (?:it|this|\w+) (?:a )?(?:scam|rug|'
    r'legit|safe|good)|should i (?:buy|ape)|tell me everything about|'
    r'research)\b', re.I)
# "market news" without a stock word — still a finance-news ask, routed with them.
MARKET_WORD_RE = re.compile(r'\bmarkets?\b')
# A COMPREHENSIVE stock report ask ("full report on NVDA", "deep dive tesla stock",
# "analyze apple stock", "due diligence on nvidia") — synthesizes every category
# (quotes+news+finnews+transcripts), distinct from the price CARD or news card.
STOCK_REPORT_RE = re.compile(
    r'\b(deep[\s-]?dive|full (?:report|analysis|breakdown|rundown|picture)|'
    r'comprehensive (?:report|analysis|overview)|(?:research|analyst) report|'
    r'due diligence|\bdd\b|report on|deep research|analyz[e]|analysis (?:on|of)|'
    r'breakdown of|tell me everything about)\b', re.I)

# DEEP MARKET RESEARCH — a research-intent word ("deep dive", "research",
# "in-depth", "analyze") that, combined with a MARKET word and NO specific
# company/ticker, means "research the market broadly" rather than the single-
# ticker report (STOCK_REPORT_RE + a resolvable company) or the fast news card.
# Routed to the shared DEEP_RESEARCH agent (multi-source fan-out + synthesis).
MARKET_RESEARCH_RE = re.compile(
    r'\b(deep[\s-]?dive|deep research|in[\s-]?depth|research|comprehensive|'
    r'full (?:report|analysis|breakdown|rundown|picture)|analyz[e]|'
    r'thorough(?:ly)?|dig into|dig in|what\'?s (?:going on|happening|moving))\b',
    re.I)

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
# Calculator / unit / currency conversion. A bare "X to Y" or "N% of M" or a
# plain arithmetic expression opens the converter directly. Kept off the
# stock-compare "vs" phrasing (that's the multi-ticker chart) and off anything
# with a research verb.
CONVERT_INTENT_RE = re.compile(
    r'\b(convert|calculate|calculator)\b'
    r'|\b\d[\d,.]*\s*(?:%|percent)\s+(?:of|off)\b'
    r'|\b\d[\d,.]*\s*(?:[a-z°]{1,5}2?|\$|€|£|¥|₹)\s+(?:to|in|into|=)\s+[a-z°$€£¥₹]{1,5}\b'
    r'|^[\s\d.,()+\-*/^%×÷]+$', re.I)
# An UNAMBIGUOUS request for arithmetic: an explicit verb, a percentage, the
# message IS an expression, or "what is <expression>". These short-circuit the
# question-veto below, so "convert my recipe from cups to grams" stays a
# conversion even though it says "recipe".
#
# The trailing "what is <expr>" arm matters on its own: CONVERT_INTENT_RE's
# arithmetic arm is anchored ^...$, so "what is 15*23" matches NOTHING there and
# a naive `CONVERT_INTENT_RE and not veto` predicate would have pushed a plain
# calculator ask into research.
CALC_IMPERATIVE_RE = re.compile(
    r'\b(convert|calculate|calculator)\b'
    r'|\b\d[\d,.]*\s*(?:%|percent)\s+(?:of|off)\b'
    r'|^[\s\d.,()+\-*/^%×÷]+$'
    r'|^\s*(?:what(?:\'?s| is)|how much is|whats)\s+[\d.,()+\-*/^%×÷\s]+\??$', re.I)
# A QUESTION that merely CONTAINS numbers and units is not a conversion. It
# wants a judgement that needs real-world knowledge — cooking times, doneness,
# food safety, dosages, travel time — and a calculator cannot answer any of
# them. This vetoes CONVERT_INTENT_RE's loose "<n> <unit> to/in <word>" arm,
# which matches on SHAPE alone: "how long should I cook 5 lb in the oven"
# satisfies it ("5" + "lb" + " in " + "the").
NUMERIC_QUESTION_RE = re.compile(
    r'\b(how long|how much longer|how many more|how do i|how does|how can|'
    r'should i|do i need|does it need|will it|would it|is it|is that|are they|'
    r'safe|safely|why is|why does|what temp\w*|what happens|recommend\w*|'
    r'recipe|cook|cooking|bake|baking|roast|grill|smoke|oven|thaw|defrost|'
    r'internal temp\w*|doneness|done yet|drive|driving|commute|flight)\b', re.I)


def is_conversion_ask(text: str) -> bool:
    """True only when the user is ASKING FOR arithmetic — not merely using
    numbers and units inside a question.

    The single predicate behind all three converter entry points (the always-on
    fast path, the tier-2 router builder and the agent's widget injector), so
    they cannot drift apart.

    Live failure 2026-07-31: "145F chicken breast with the carcass how long to
    get to 165 its been cooking for about 25 minutes in the oven at 400F"
    rendered a unit/currency CALCULATOR. Nothing in the stack distinguished
    "contains numbers and units" from "is a question about what those numbers
    mean", and build_converter_config's _UNIT_WORDS maps f/c/cup/tsp straight
    onto tabs, so cooking language reads as units.

    CONVERT_INTENT_RE itself is deliberately NOT tightened: it is load-bearing
    for build_converter_config's tab picker, it is already tuned, and it
    correctly rejected that message. The veto is layered on top instead.
    """
    t = (text or "").strip()
    if not t:
        return False
    # A stock comparison ("NVDA vs SPY") is a chart, never a conversion. Folded
    # in from the fast-path call site so every entry point inherits the guard.
    if re.search(r'\b[A-Za-z]{1,5}\s+vs\.?\s+[A-Za-z]{1,5}\b', t):
        return False
    if CALC_IMPERATIVE_RE.search(t):
        return True
    if not CONVERT_INTENT_RE.search(t):
        return False
    if NUMERIC_QUESTION_RE.search(t):
        return False
    # A real conversion is terse — "20 usd to eur", "5 miles in km". Prose long
    # enough to be a paragraph is a question that happens to contain a unit.
    return len(t.split()) <= 12


# Reminder / alarm at a time. "timer/stopwatch/countdown" stay with the clock
# widget; a reminder carries a THING to be reminded of, or an alarm time.
REMINDER_INTENT_RE = re.compile(
    r'\bremind me\b|\bset (a|an) (reminder|alarm)\b|\balarm (for|at)\b|\breminder to\b', re.I)
# Appearance/theme/settings — a UI-control verb (like CLEAR_ALL), handled by a
# deterministic fast-path so a bare "dark mode" flips instantly instead of
# spinning up the ~30s agent. Deliberately narrow: it must read as a look/skin
# change or an explicit settings request, not merely contain a color word.
THEME_INTENT_RE = re.compile(
    r'\b(theme|themes|appearance|palette|colou?r ?scheme|colou?rway|skin)\b'
    r'|\b(dark|light|day|night) ?mode\b'
    r'|\b(open|show|change|edit)\b[^.]*\bsettings\b'
    r'|\b(make|set|change|switch|turn)\b[^.]{0,30}\b('
    r'dark|light|forest|pastel|egg|eggshell|cream|midnight|ember|sunset|'
    r'grape|purple|synthwave|mono|monochrome|slate|green|orange)\b'
    r'|^\s*settings\s*$', re.I)
# A question/how-to/recipe that wants a SYNTHESISED answer (summary + sources),
# not a widget noun and not a data feed. Routed to build_answer_config so the
# user gets the actual recipe/steps/definition instead of the ~30-60s agent loop.
ANSWER_ASK_RE = re.compile(
    r'\b(recipe|recipes|how to|how do|how does|how can|tutorial|guide|'
    r'what is|what are|whats|what\'s|who is|who are|who was|when is|when was|'
    r'why is|why do|why does|explain|difference between|'
    r'vs\.?|versus|meaning of|definition of|instructions?|steps to)\b')
# A BROAD, rich informational ask that deserves a multi-modal COMPOSITION (an
# explanation + supporting image/video/news), not a single card. High-precision so
# it never steals a narrow single-intent ask (weather, a ticker, a timer, "how to
# boil an egg"): it wants the "give me the whole picture on <subject>" phrasings.
COMPOSE_ASK_RE = re.compile(
    r'\b(tell me (all |everything )?about|everything about|give me (a|the) '
    r'(rundown|overview|breakdown|primer|picture)\b|teach me about|walk me through|'
    r'introduce me to|overview of|(a|an) (overview|introduction|primer) (on|of|to)|'
    r'what should i know about|get me up to speed on|brief me on)\b', re.I)
# Informational framing on a NEWS ask that wants a SYNTHESIZED brief (a written
# answer + sources), not a wall of headline links. "tell me about the stock market
# news", "what's happening in the markets", "summarize today's news", "catch me up".
# Checked BEFORE the news link-list branches so the synthesis wins.
_NEWS_SYNTH_RE = re.compile(
    r'\b(tell me about|what\'?s (happening|going on|new|the latest)|'
    r'whats (happening|going on|new|the latest)|summar(y|ize|ise)|brief me|'
    r'catch me up|get me up to speed|fill me in|whats going on|'
    r'give me the (rundown|scoop|latest|breakdown))\b', re.I)
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




async def _wiki_summary(topic: str) -> dict:
    """Raw Wikipedia REST summary for a named topic, or {} on any failure.
    Shared by the wikipedia card and the profile_card builder — the thumbnail it
    returns is API-sourced, never a model-typed URL."""
    slug = urllib.parse.quote((topic or "").strip().replace(" ", "_"))
    if not slug:
        return {}
    try:
        async with httpx.AsyncClient(
                timeout=8.0, follow_redirects=True,
                headers={"User-Agent": "html-notes/1.0 (canvas widget)"}) as client:
            r = await client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.info(f"[PROFILE] wiki summary miss for {topic!r}: {e}")
        return {}






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
        # Long read budget for the 500-1000 token passes, but a SHORT connect cap:
        # a down/overloaded backend should fail fast (5s) rather than making every
        # card wait the full read ceiling to discover the host is unreachable.
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
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
        # Drop the cached model id: if the backend restarted serving a DIFFERENT
        # model, every call posts a stale `model` and 400s forever until the
        # process restarts. Re-discover it on the next call.
        _fast_model["name"] = None
        # type name matters: httpx timeout exceptions stringify to ""
        logger.warning(f"fast_llm_json failed: {type(e).__name__}: {e}")
        return None


# ── Intent grounding + relevance gate ────────────────────────────────────────
# The pipeline's quality problem was that a terse ask ("sandals") went straight to
# a generic search and whatever came back was rendered with NO check that it
# actually depicted the subject — so "sandals" returned Sandals-Resort promos,
# beach scenery and feet-on-a-dock stock photos. These helpers add the missing
# intent layer (turn the utterance into a disambiguated subject + an expanded
# retrieval query + the list of WRONG-match categories to reject) and a vision
# relevance gate that drops images that don't show the subject. Everything is
# best-effort and FAILS OPEN: any grading outage degrades to the old behaviour,
# never an empty canvas.

_GROUND_CACHE: dict = {}          # message(lower) -> grounded intent (FIFO-capped)
_GROUND_CACHE_MAX = 256


async def ground_query(message: str) -> dict:
    """Turn a raw utterance into a structured retrieval intent. ONE fast LLM pass,
    memoised per-message so the image / products / video builders of a single turn
    share it. Returns
      {subject, intent, retrieval_query, hyde, negatives[], ambiguous, clarify}.
    On any failure returns a permissive fallback (subject=message, no negatives) so
    grounding is a pure quality boost and never blocks a turn.

    `negatives` is the key field: for a word that is also a brand/place/other
    meaning it names the wrong senses ("Sandals Resorts", "beach scenery") so the
    relevance gate can reject them — this is the CLIP-negative-filter idea done with
    a vision LLM."""
    key = (message or "").strip().lower()
    if not key:
        return {"subject": "", "intent": "other", "retrieval_query": "",
                "hyde": "", "negatives": [], "ambiguous": False, "clarify": ""}
    if key in _GROUND_CACHE:
        return _GROUND_CACHE[key]
    data = await fast_llm_json(
        f"Today is {datetime.date.today().isoformat()}.\n"
        "You are the intent-grounding step for a live visual dashboard. Turn the "
        "user's ask into a precise retrieval spec so downstream image / video / web "
        "search fetches the RIGHT thing, not something merely on-theme. Return ONLY "
        "JSON, no prose, no code fence:\n"
        '{"subject": "<the concrete subject in plain words, DISAMBIGUATED — if the '
        'word is also a brand / place / other meaning, state the MOST LIKELY '
        'intended one>", '
        '"intent": "<one of: shopping, informational, media, place, data, other>", '
        '"retrieval_query": "<an expanded, unambiguous web-search query for the '
        'subject>", '
        '"hyde": "<one line describing the IDEAL result; for a product, the ideal '
        'PHOTO, e.g. \\"a product photo of open-toe leather sandals footwear\\">", '
        '"negatives": ["<up to 5 categories a WRONG match would fall in: OTHER '
        'meanings of the word, off-topic themes, ads, generic scenery>"], '
        '"freshness": "<the time constraint COPIED VERBATIM from the ask, e.g. '
        '\\"new\\", \\"this week\\", \\"yesterday\\" — empty string if the ask has '
        'no time constraint>", '
        '"ambiguous": <true only if you genuinely cannot tell what they want>, '
        '"clarify": "<if ambiguous, a one-line disambiguating question, else empty>"}\n\n'
        'Example — ASK "sandals":\n'
        '{"subject":"sandals (footwear to wear)","intent":"shopping",'
        '"retrieval_query":"best sandals to buy footwear reviews","hyde":"a product '
        'photo of sandals footwear on a plain background","negatives":["Sandals '
        'Resorts / Caribbean all-inclusive vacation","beach or ocean scenery","feet '
        'or legs lifestyle photo","advertisement"],"freshness":"",'
        '"ambiguous":false,"clarify":""}\n\n'
        f'USER: "{message}"',
        max_tokens=400,
    )
    if not isinstance(data, dict) or not str(data.get("subject") or "").strip():
        result = {"subject": (message or "").strip(), "intent": "other",
                  "retrieval_query": (message or "").strip(), "hyde": "",
                  "negatives": [], "freshness": "", "ambiguous": False, "clarify": ""}
    else:
        negs = data.get("negatives")
        result = {
            "subject": str(data.get("subject") or message).strip()[:200],
            "intent": str(data.get("intent") or "other").strip().lower(),
            "retrieval_query": str(data.get("retrieval_query") or message).strip()[:200],
            "hyde": str(data.get("hyde") or "").strip()[:300],
            "negatives": ([str(n).strip()[:80] for n in negs if str(n).strip()][:6]
                          if isinstance(negs, list) else []),
            "freshness": str(data.get("freshness") or "").strip()[:60],
            "ambiguous": bool(data.get("ambiguous")),
            "clarify": str(data.get("clarify") or "").strip()[:200],
        }
    _GROUND_CACHE[key] = result
    if len(_GROUND_CACHE) > _GROUND_CACHE_MAX:      # FIFO evict oldest
        _GROUND_CACHE.pop(next(iter(_GROUND_CACHE)), None)
    return result


async def _fast_multimodal_json(content: list, max_tokens: int = 300,
                                temperature: float = 0.1) -> Optional[dict]:
    """fast_llm_json for a multimodal message. `content` is an OpenAI content array
    (text + image_url blocks). Same model discovery, JSON parsing and fail-open
    contract as fast_llm_json — returns None on any error."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            model = _fast_model["name"]
            if not model:
                resp = await client.get(f"{VLLM_URL}/v1/models")
                model = resp.json()["data"][0]["id"]
                _fast_model["name"] = model
            resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            })
            text = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group(0)) if match else None
    except Exception as e:
        _fast_model["name"] = None
        logger.warning(f"_fast_multimodal_json failed: {type(e).__name__}: {e}")
        return None


async def _fetch_image_data_url(client: httpx.AsyncClient, url: str,
                                max_bytes: int = 1_800_000) -> Optional[str]:
    """GET an image and return it as a base64 data: URL, or None. We pass images to
    the vision model as data URLs rather than remote URLs because the model fetches
    remote URLs server-side and many og:image CDNs block hotlinking (403/400) — a
    data URL is judged reliably. Skips non-images and anything over `max_bytes`."""
    try:
        resp = await client.get(url, headers={"User-Agent": _BROWSER_UA})
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            return None
        blob = resp.content
        if not blob or len(blob) > max_bytes:
            return None
        return f"data:{ctype};base64," + base64.b64encode(blob).decode("ascii")
    except Exception:
        return None


async def filter_images_by_relevance(subject: str, negatives: list, items: list,
                                     keep: int = 4, hyde: str = "",
                                     min_keep: int = 0) -> list:
    """Vision relevance gate. `items` are dicts carrying an 'image' (URL) and a
    'caption'/'title'. Fetches each image server-side, base64-encodes it, and asks
    the vision model in ONE batched call which ones genuinely depict `subject` (and
    match none of `negatives`). Returns the surviving items in ORIGINAL order.

    FAILS OPEN per-item: an image we can't fetch or the model can't judge is KEPT
    (the old behaviour) — the gate only ever DROPS an image it actively judged
    off-subject, so a grading/network outage never empties the grid. If the gate
    would leave fewer than `min_keep`, the original list is returned instead."""
    cands = [it for it in items if it.get("image")]
    if not cands:
        return items
    judged = cands[:8]                       # bound the vision call
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0),
                                 follow_redirects=True) as client:
        data_urls = await asyncio.gather(
            *[_fetch_image_data_url(client, it.get("image", "")) for it in judged])
    # Only images we successfully fetched can be judged; the rest fail open.
    fetched = [(i, du) for i, du in enumerate(data_urls) if du]
    if not fetched:
        return items                         # couldn't fetch any → keep all (fail open)
    neg_txt = ("; ".join(negatives)) if negatives else ""
    content = [{"type": "text", "text": (
        "You are a STRICT relevance filter for image search results. The user wants: "
        f"\"{subject}\". " + (f"An ideal result looks like: {hyde}. " if hyde else "") +
        (f"REJECT any image that instead shows: {neg_txt}. " if neg_txt else "") +
        "You are shown numbered images. Return ONLY JSON: "
        '{"keep": [<indices of images that CLEARLY and PRIMARILY depict what the '
        "user wants>]}. Reject off-topic images: a different meaning of the word, "
        "generic scenery, logos/brand art, ads, memes, or unrelated stock photos. "
        "Keep a plausible match; drop the clearly-wrong.")}]
    for i, du in fetched:
        cap = (judged[i].get("caption") or judged[i].get("title") or "")[:90]
        content.append({"type": "text", "text": f"Image [{i}] (caption: {cap}):"})
        content.append({"type": "image_url", "image_url": {"url": du}})
    data = await _fast_multimodal_json(content, max_tokens=120)
    keep_set = None
    if isinstance(data, dict) and isinstance(data.get("keep"), list):
        keep_set = {i for i in data["keep"] if isinstance(i, int)}
    if keep_set is None:
        return items                         # model failed → keep all (fail open)
    judged_idx = {i for i, _ in fetched}
    # Drop ONLY judged images the model rejected; unfetched/unjudged pass through.
    survivors = [it for j, it in enumerate(items)
                 if not (j < len(judged) and j in judged_idx and j not in keep_set)]
    if len(survivors) < max(min_keep, 1) and len(survivors) < len(items):
        logger.info(f"[IMG GATE] all-but-{len(survivors)} rejected for {subject!r}; "
                    f"keeping unfiltered to avoid an empty grid")
        return items
    if len(survivors) < len(items):
        logger.info(f"[IMG GATE] {subject!r}: kept {len(survivors)}/{len(items)} "
                    f"(dropped {len(items) - len(survivors)} off-subject)")
    return survivors[:keep] if keep else survivors


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


# Currency codes the converter recognizes when classifying a seed.
_CURRENCY_CODES = {
    "usd", "eur", "gbp", "jpy", "cad", "aud", "chf", "cny", "inr", "mxn", "brl",
    "nzd", "sek", "nok", "dkk", "sgd", "hkd", "krw", "zar", "rub", "try", "pln",
}
_UNIT_WORDS = {
    "mm", "cm", "m", "km", "in", "inch", "inches", "ft", "foot", "feet", "yd",
    "yard", "yards", "mi", "mile", "miles", "nmi", "mg", "g", "gram", "grams",
    "kg", "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds", "st", "ml",
    "l", "liter", "litre", "liters", "litres", "tsp", "tbsp", "cup", "cups",
    "pt", "qt", "gal", "gallon", "gallons", "floz", "mph", "kph", "knot",
    "celsius", "fahrenheit", "kelvin", "c", "f", "k", "mb", "gb", "tb", "kb",
}




_TIME_AT_RE = re.compile(
    r'\b(?:at|for)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b|\b(?:at|for)\s+(noon|midnight)\b', re.I)




async def fetch_fx_rates(base: str) -> dict:
    """Latest FX rates for `base`, keyless via open.er-api.com, cached via the
    shared tool cache (~5m — rates barely move intraday).
    Returns {base, rates:{CODE:rate}, updated} or {} on failure."""
    base = (base or "USD").upper()
    if not re.fullmatch(r"[A-Z]{3}", base):
        return {}
    cached = get_cached_tool_result(f"fx:{base}")
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(f"https://open.er-api.com/v6/latest/{base}")
            data = r.json()
        if data.get("result") != "success" or not isinstance(data.get("rates"), dict):
            return {}
        out = {"base": base, "rates": data["rates"],
               "updated": (data.get("time_last_update_utc") or "")[:16]}
        cache_tool_result(f"fx:{base}", out)
        return out
    except Exception as e:
        logger.warning(f"fetch_fx_rates({base}) failed: {e}")
        return {}




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






async def _stock_video_commentary(symbol: str, company: str = "", limit: int = 2) -> list:
    """Analyst/commentary TRANSCRIPTS for a ticker via scraper-service's youtube
    collector (yt-dlp + youtube-transcript-api). This is the one path that gives
    us what a human analyst actually SAID, not just a headline — real material for
    the sentiment section of a report. Slow (yt-dlp, ~10-25s) and best-effort;
    returns [{title, channel, transcript}] or [] on failure/timeout."""
    q = f"{company or symbol} stock analysis"
    try:
        # 25s cap: transcripts are the slowest source and gate the whole report
        # (everything is gathered concurrently). If yt-dlp is slow/rate-limited the
        # report ships without the Sentiment section rather than hanging.
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post(f"{SCRAPER_SERVICE_URL}/collect", json={
                "source": "youtube", "query": q, "require_transcript": True,
                "limit": limit, "days_back": 30, "sort": "date"})
            data = r.json()
    except Exception as e:
        logger.warning(f"stock video commentary failed for {symbol!r}: {e}")
        return []
    out = []
    for v in (data.get("items") or [])[:limit]:
        tr = v.get("transcript") or ""
        if isinstance(tr, str) and len(tr) > 400:
            out.append({"title": v.get("title", ""), "channel": v.get("channel", ""),
                        "transcript": tr})
    return out


def _fundamentals_lines(snap: dict) -> str:
    """Compact, LLM-friendly rendering of the snapshot's numbers so the report
    model gets clean facts instead of a raw dict."""
    f = snap.get("fundamentals") or {}
    t = snap.get("technicals") or {}
    rows = [
        ("Price", snap.get("price")), ("Change over range %", snap.get("change_pct")),
        ("Sector", f.get("sector")), ("Industry", f.get("industry")),
        ("Market cap", f.get("market_cap")), ("P/E (trailing)", f.get("pe_ratio")),
        ("Forward P/E", f.get("forward_pe")), ("EPS", f.get("eps")),
        ("Beta", f.get("beta")), ("Dividend yield", f.get("dividend_yield")),
        ("Profit margin", f.get("profit_margin")), ("Revenue", f.get("revenue")),
        ("Revenue growth", f.get("revenue_growth")),
        ("Analyst target", f.get("analyst_target")),
        ("Recommendation", f.get("recommendation")),
        ("RSI(14)", t.get("rsi_14")), ("SMA50", t.get("sma_50")),
        ("SMA200", t.get("sma_200")), ("Trend", t.get("trend")),
        ("Price vs SMA50 %", t.get("vs_sma_50")),
        ("52wk high", t.get("week52_high")), ("52wk low", t.get("week52_low")),
        ("52wk position %", t.get("week52_position")),
        ("Annualized volatility %", t.get("volatility")),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in rows if v is not None and v != "")








# ══════════════════════════════════════════════════════════════════════════════
# CRYPTO / ON-CHAIN BUILDERS
# The stock builders above are the template: resolve an identity, pull live data
# from keyless sources, degrade honestly. Four widgets:
#   crypto_card   — a token's price + chart + key stats + contract addresses
#   crypto_report — a written brief synthesizing price, distribution and news
#   wallet_graph  — THE feature: top-holder connection graph + concentration read
#   wallet_card   — inspect a single address's native + token holdings
# ══════════════════════════════════════════════════════════════════════════════

# CoinGecko range tab -> its `days` param. Mirrors the stock card's range tabs.
_CRYPTO_RANGES = {"1d": "1", "7d": "7", "30d": "30", "90d": "90",
                  "1y": "365", "max": "max"}


async def _noop_dict() -> dict:
    """An awaitable {} — lets asyncio.gather keep a fixed shape when one branch
    (CoinGecko description) is skipped for a DexScreener-sourced token."""
    return {}


def _fmt_usd(v) -> str:
    """Compact USD for stat chips: $1.2B, $3.4M, $0.0000029."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n == 0:
        return "$0"
    a = abs(n)
    if a >= 1e12:
        return f"${n/1e12:.2f}T"
    if a >= 1e9:
        return f"${n/1e9:.2f}B"
    if a >= 1e6:
        return f"${n/1e6:.2f}M"
    if a >= 1e3:
        return f"${n/1e3:.1f}K"
    if a >= 1:
        return f"${n:,.2f}"
    # Sub-dollar memecoin prices: keep the first significant digits.
    return f"${n:.8f}".rstrip("0").rstrip(".")


async def _dexs_snapshot(ref: str, range_: str = "30d") -> dict:
    """DexScreener-sourced token (a "dexs:<chainId>:<pool>" ref) -> the same
    crypto_card payload, using DexScreener for live price/liquidity/mcap and
    GeckoTerminal for the OHLCV chart. This is the keyless path that makes ANY
    token with a live pool chartable — microcap memecoins CoinGecko never listed."""
    try:
        _, chain_id, pool = ref.split(":", 2)
    except ValueError:
        return {"is_error": True}
    gt_net = cryptolib.DEXS_CHAIN.get(chain_id, (chain_id, chain_id))[1]
    slug = cryptolib.DEXS_CHAIN.get(chain_id, (chain_id, chain_id))[0]
    pair, (labels, values) = await asyncio.gather(
        cryptolib.dexscreener_pair(chain_id, pool),
        cryptolib.gt_ohlcv(gt_net, pool, range_),
    )
    if not pair:
        return {"is_error": True}
    bt = pair.get("baseToken") or {}
    try:
        price = float(pair.get("priceUsd")) if pair.get("priceUsd") else None
    except (TypeError, ValueError):
        price = None
    chg = (pair.get("priceChange") or {}).get("h24")
    return {
        "is_error": False,
        "coin_id": ref,
        "name": bt.get("name", ""),
        "symbol": (bt.get("symbol") or "").upper(),
        "image": (pair.get("info") or {}).get("imageUrl", "") or "",
        "price": price,
        "price_str": _fmt_usd(price),
        "change_pct": round(chg, 2) if isinstance(chg, (int, float)) else None,
        "market_cap": _fmt_usd(pair.get("marketCap") or pair.get("fdv")),
        "market_cap_rank": None,
        "volume": _fmt_usd((pair.get("volume") or {}).get("h24")),
        "liquidity": _fmt_usd((pair.get("liquidity") or {}).get("usd")),
        "high_24h": "—", "low_24h": "—", "ath": "—", "ath_change_pct": None,
        "supply": None,
        "range": range_,
        "labels": labels,
        "values": values,
        "platforms": {slug: bt.get("address", "")} if bt.get("address") else {},
        "dex": pair.get("dexId", ""),
        "source": "dexscreener",
    }


async def _crypto_snapshot(coin_id: str, range_: str = "30d") -> dict:
    """Coin ref -> the crypto_card payload (price header + chart + stats +
    contracts). A "dexs:*" ref goes to DexScreener+GeckoTerminal; anything else is
    a CoinGecko coin id. {is_error:True} when it can't be loaded."""
    if coin_id.startswith("dexs:"):
        return await _dexs_snapshot(coin_id, range_)
    days = _CRYPTO_RANGES.get(range_, "30")
    coin, (labels, values) = await asyncio.gather(
        cryptolib.cg_coin(coin_id),
        cryptolib.cg_market_chart(coin_id, days),
    )
    if not coin or not coin.get("id"):
        return {"is_error": True}
    md = coin.get("market_data") or {}

    def _usd(d):
        return (d or {}).get("usd")

    platforms = {k: v for k, v in (coin.get("platforms") or {}).items() if v}
    chg = md.get("price_change_percentage_24h")
    return {
        "is_error": False,
        "coin_id": coin.get("id"),
        "name": coin.get("name", ""),
        "symbol": (coin.get("symbol") or "").upper(),
        "image": ((coin.get("image") or {}).get("large")
                  or (coin.get("image") or {}).get("small") or ""),
        "price": _usd(md.get("current_price")),
        "price_str": _fmt_usd(_usd(md.get("current_price"))),
        "change_pct": round(chg, 2) if isinstance(chg, (int, float)) else None,
        "market_cap": _fmt_usd(_usd(md.get("market_cap"))),
        "market_cap_rank": coin.get("market_cap_rank"),
        "volume": _fmt_usd(_usd(md.get("total_volume"))),
        "high_24h": _fmt_usd(_usd(md.get("high_24h"))),
        "low_24h": _fmt_usd(_usd(md.get("low_24h"))),
        "ath": _fmt_usd(_usd(md.get("ath"))),
        "ath_change_pct": round(md.get("ath_change_percentage", {}).get("usd"), 1)
            if isinstance(md.get("ath_change_percentage"), dict)
            and isinstance(md.get("ath_change_percentage", {}).get("usd"), (int, float))
            else None,
        "supply": md.get("circulating_supply"),
        "range": range_,
        "labels": labels,
        "values": values,
        "platforms": platforms,   # {chain_slug or cg-platform: address}
    }
    # "Chart no matter what": if CoinGecko's chart came back empty (rate-limited,
    # or a thinly-covered microcap) but the coin has an on-chain contract, pull
    # the price series from GeckoTerminal via its DEX pool instead.
    if not values and platforms:
        gl, gv = await _gt_chart_for_contract(platforms, range_)
        if gv:
            snap["labels"], snap["values"] = gl, gv
    return snap


async def _gt_chart_for_contract(platforms: dict, range_: str) -> tuple:
    """Given a coin's {cg-platform: address} map, find its deepest DEX pool via
    DexScreener and pull GeckoTerminal OHLCV — the keyless chart fallback for any
    token with a live pool. ([], []) if none found."""
    for cg_platform, addr in platforms.items():
        if not addr:
            continue
        try:
            best = cryptolib._best_pair(
                await cryptolib.dexscreener_by_address(addr), None)
            if not best:
                continue
            chain_id = best.get("chainId")
            gt_net = cryptolib.DEXS_CHAIN.get(chain_id, (chain_id, chain_id))[1]
            gl, gv = await cryptolib.gt_ohlcv(gt_net, best.get("pairAddress", ""), range_)
            if gv:
                return gl, gv
        except Exception as e:
            logger.info(f"[CRYPTO] GT chart fallback failed for {addr}: {e}")
    return [], []










# Map the LLM-chosen answer `format` to a header icon. The model picks the format;
# this is just the visual affordance for it. Unknown formats fall back to "article".
_ANSWER_ICONS = {
    "recipe": "restaurant", "howto": "checklist", "how-to": "checklist",
    "steps": "checklist", "definition": "menu_book", "fact": "lightbulb",
    "comparison": "compare_arrows", "list": "format_list_bulleted",
    "article": "article", "explainer": "article", "answer": "lightbulb",
}


async def _image_url_loads(url: str) -> bool:
    """Does this URL actually serve an image?

    Only ever used on a URL the MODEL supplied. The agent has no image-search
    tool, so such a URL is recalled, not looked up, and a plausible-looking one
    is frequently dead (observed: a Wikimedia thumb path for "red panda" that
    404s at the CDN and returns 400). An <img> with a dead src is indis-
    tinguishable from a good one in the DOM, so check the bytes.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=6.0) as client:
            r = await client.head(url)
            # Some CDNs refuse HEAD; fall back to a ranged GET before believing it.
            if r.status_code >= 400:
                r = await client.get(url, headers={"Range": "bytes=0-2047"})
            return (r.status_code < 400
                    and r.headers.get("content-type", "").lower().startswith("image/"))
    except Exception as e:
        logger.info(f"[WIDGET INJECTOR] image URL check failed for {url[:80]!r}: {e}")
        return False


async def _resolve_news_topic_config(config: dict) -> dict:
    """Fill a data_card the model tagged with `news_topic` (or `topic`).

    Normally that means it researched via html_notes_news, and the stories it
    already fetched are cached under "news:<topic>" — the server supplies them
    with their photos so the model never re-types them.

    But the model does not always mean it. Observed live on "best espresso
    machines under $500": it researched with html_notes_web_search and then
    labelled the config `news_topic` anyway. (The routing prompt used to promise
    photos ONLY on the news branch, so a model that wanted a picture-bearing
    card was steered there — that wording is fixed now, but a prompt is a
    suggestion and this needs to hold regardless.) There was no "news:" cache,
    so this silently no-opped and the card arrived sourceless.

    So: believe the tool the model RAN, not the key it typed. Which cache is
    populated is a fact about what actually happened; the key name is only what
    the model meant. When only the web_search cache exists, build the research
    card from it.

    Returns the config to use — unchanged when neither cache is available, so
    the downstream quality floor still gets its turn.
    """
    topic = str(config.get("news_topic", config.get("topic", ""))).strip()
    model_keys = {k: v for k, v in config.items()
                  if v and k not in ("news_topic", "topic")}

    cached = get_cached_tool_result(f"news:{topic}")
    if cached:
        logger.info(f"[WIDGET INJECTOR] Rehydrated news data_card for {topic!r}")
        return {**cached, **model_keys}

    searched = get_cached_tool_result(f"search:{topic}")
    if searched:
        logger.info(f"[WIDGET INJECTOR] news_topic {topic!r} has no news cache but a "
                    f"web_search one — treating as research")
        answer_cfg = await build_answer_config(topic, results=searched)
        return {**answer_cfg, **model_keys}

    # Neither cache hit. This branch used to give up here — making news_topic the
    # only injector key with no builder fallback, while its siblings
    # (stock_news_query -> build_stock_news_config, search_query ->
    # build_answer_config) call their builder unconditionally. A one-character
    # drift between the topic passed to html_notes_news and the topic passed to
    # canvas_add_widget was enough to miss the cache, and the card then fell
    # through to the quality floor, which stapled on generic hits: the user got
    # unread sources with no photos in place of the curated photo stories the
    # prompt promised. Build them properly instead.
    if topic:
        try:
            news_cfg = await asyncio.wait_for(build_news_config(topic), timeout=20.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[WIDGET INJECTOR] news rebuild failed for {topic!r}: {e}")
            return config
        if news_cfg and news_cfg.get("items"):
            logger.info(f"[WIDGET INJECTOR] news cache miss for {topic!r} — rebuilt "
                        f"{len(news_cfg['items'])} stories")
            return {**news_cfg, **model_keys}

    return config




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


# Words that REFER to the user's location instead of naming a place. They must
# never reach a geocoder: every one of them is also a real, geocodable place name
# somewhere in the world, so the lookup "succeeds" and pins the map thousands of
# miles away with total confidence. Measured against the live geocoders:
#   "Current"  -> 25.408, -76.784   (a settlement on Eleuthera, Bahamas)
#   "here"     ->  8.660,  45.854   (Somalia)
#   "my location" -> -1.989, 30.086 (Rwanda)
# This is how "how is the traffic" produced a confident traffic map of the
# Bahamas: the LLM router fills `query` with a placeholder like "Current" when
# the user named no place, and we geocoded it literally.
_DEICTIC_PLACE_RE = re.compile(
    # NOTE: callers usually pass text that _DIR_STRIP_RE has already run over,
    # which removes leading "my"/"in"/"the" — so "traffic in my area" arrives as
    # the bare word "area". Both the full phrases and the bare heads must match.
    r"^\s*(?:the\s+)?(?:my\s+|our\s+)?"
    r"(?:current(?:\s+(?:location|position|area|place|spot|city))?"
    r"|here|nearby|near\s*(?:me|by|here)|around\s*(?:me|here)"
    r"|local(?:ly)?|my\s+(?:area|place|city|town|position|spot)"
    r"|location|position|area|vicinity|surroundings"
    r"|this\s+(?:area|place|city|town)|where\s+i\s+am|home)\s*$",
    re.I,
)


def is_deictic_place(text: str) -> bool:
    """True when `text` points AT the user rather than naming a place."""
    return bool(_DEICTIC_PLACE_RE.match((text or "").strip()))


def _extract_directions_place(message: str) -> str:
    cleaned = re.sub(r'[^\w\s]', ' ', message or '')
    cleaned = _DIR_STRIP_RE.sub(' ', cleaned)
    cleaned = ' '.join(cleaned.split()).strip()
    if is_deictic_place(cleaned):
        # Not a place — fall through to the stored user location, or to the
        # "which city?" prompt. Anything but a geocode of the literal word.
        return ''
    return cleaned[:60] if len(cleaned) >= 2 else ''




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
    "stock_news": ("stock-news", 'stock / market / company NEWS. query = the ticker or company (e.g. "TSLA", "Apple"), or the market itself for a broad market-news ask. ONLY for a genuine finance/markets ask.'),
    "stock":      ("stock",     'a ticker\'s price + chart + technicals. query = company or symbol ("Apple", "TSLA")'),
    "stock_trending": ("stock-trending", 'the top TRENDING / most-active / biggest-gainer or -loser stocks — a DISCOVERY ask naming NO specific company ("top trending stocks this month", "biggest gainers today", "compare the top 5 hot stocks"). Renders one multi-series comparison chart from live market feeds. query = the whole request'),
    "stock_report": ("stock-report", 'a COMPREHENSIVE research report on ONE stock — synthesizes price, fundamentals, technicals, recent news AND analyst commentary into a written brief. Use for "full report on X", "deep dive / due diligence on X", "analyze X stock". query = company or symbol'),
    "crypto":     ("crypto",    'a CRYPTOCURRENCY / token\'s price + chart + stats + contract address. query = the coin name, symbol or contract address ("Bitcoin", "PEPE", "$SOL", "0x6982…"). Use for any "price of X coin/token", "X crypto chart" ask. NOT for stocks.'),
    "crypto_report": ("crypto-report", 'a COMPREHENSIVE written report on ONE crypto token — price, market context, ON-CHAIN holder distribution (whale concentration / rug risk) and news. Use for "full report / deep dive / due diligence / is X a scam / analyze X token". query = coin name, symbol or address'),
    "wallet_graph": ("wallet-graph", 'a HOLDER-NETWORK graph for a token: draws the top wallets holding it as nodes (sized by %) with transfer edges between them, and reports concentration — whether a few whales control supply (pump-and-dump / rug risk) or it is fairly distributed. Use for "who holds X", "X token holders / whales / distribution", "is X a fair launch", "wallet graph for X", "show me the whales in X". query = coin name, symbol or contract address'),
    "wallet":     ("wallet",    'inspect ONE wallet ADDRESS — its holdings, portfolio value and token list. Use when the message contains an on-chain address (0x… or a Solana base58 address) and asks what it holds / its balance. query = the whole message (the address is extracted from it)'),
    "sports":     ("scores",    'scores / fixtures / standings. query = the league or team ("nba", "arsenal")'),
    "map":        ("map",       'where something IS / a map of places. query = the subject ("fires in California")'),
    "traffic":    ("traffic",   'live traffic or directions. query = the place, or "from A to B"'),
    "video":      ("video",     'something to WATCH. query = the subject ("cookie recipe")'),
    "image":      ("image",     'a PICTURE of something. query = the subject ("golden retriever puppy")'),
    "music":      ("music",     'background music / radio. query = the genre OR artist/band name. Add modifiers {"kind": "genre"} for a style/mood ("jungle", "smooth jazz", "study") or {"kind": "artist"} for a named act ("Oasis", "the band Jungle"); omit kind if unsure'),
    "answer":     ("answer",    'a fact / recipe / how-to / definition / comparison / explanation — INCLUDING any question that contains numbers or units but wants a JUDGEMENT ("how long to get a 145F chicken to 165", "is 10 more minutes enough", "what temp is safe"). query = the question'),
    "products":   ("products",  'shopping / product recommendations to BUY or compare ("good outdoor shoes", "best budget laptop", "gift for a hiker"). query = the product ask. Renders a grid of picture cards linking to sources'),
    "trip":       ("trip",      'plan a TRIP / vacation / multi-day itinerary to a place ("plan a trip to Japan", "3 days in Rome"). query = the destination + any duration. Renders an itinerary card + a map of the spots'),
    "wikipedia":  ("wikipedia", 'an explicit Wikipedia-article request. query = the subject'),
    "list":       ("checklist", 'a checklist / to-do / shopping list to CREATE. query = the whole request'),
    "notes":      ("notes",     'a notepad, optionally pre-filled. query = the whole request'),
    "clock":      ("clock",     'a clock / world clock / timer / countdown / stopwatch. query = the whole request'),
    "converter":  ("converter", 'ONLY a bare calculation or unit/currency conversion — the ask IS the arithmetic ("40% of 1250", "what is 15*23", "5 miles in km", "20 usd to eur", "convert 10 kg to lb"). NEVER a question that merely contains numbers, temperatures or units ("how long until my 145F chicken hits 165", "is 10 more minutes enough", "how long to drive to SF") — those are "answer". query = the whole request'),
    "reminder":   ("reminder",  'a reminder / alarm at a time ("remind me in 20 minutes", "remind me at 3pm to call mom"). query = the whole request'),
}

# In PRISM MODE (use_lazy_agent=False), these router widget types are RESEARCH asks
# and get handed to the prism agent (which runs the lazy-tool-service MCP
# web_search/read_page harnesses → a synthesised, sourced answer) instead of a local
# search-scrape builder. Everything else (weather/stock/sports/map/traffic/music/
# clock/list/notes/trip) stays on the fast local path — deterministic, fast, and
# already high quality, so a 30-60s research loop would only make it worse.
# NOTE: "products" is deliberately NOT here. With it in this set, prism mode
# (the default) deferred every shopping ask to the agent, whose prompt renders a
# data_card — so render_products, its reuse plumbing and class map serviced a
# type nothing could emit, and the same ask produced a photo grid in lazy-agent
# mode but a text card in prism mode. The router's local products builder is
# deterministic and already high quality; let it run in both modes.
_AGENT_RESEARCH_TYPES = {"answer", "image", "wikipedia",
                         # News is research, not a lookup: the value is in
                         # corroborating across outlets and saying what they
                         # DISAGREE on, which only a read-then-synthesise pass
                         # can do. The local builder summarised each story in
                         # isolation, so the card was N disconnected blurbs with
                         # no through-line and no cross-checking.
                         "news", "stock_news"}

# The tool a research verdict actually resolves to, and the call that finishes
# it. Every string here is copied from the ROUTING section of SYSTEM_PROMPT, so
# the pre-flight block can never name a tool or a config key the prompt does not
# already teach — and every tool name is pinned by a test against the
# enabled_tools list. A hint naming a tool the agent does not have is worse than
# no hint: it burns iterations failing against a phantom (the same failure the
# enabled_tools comment describes for the tools-api data tools).
_PREFLIGHT_RECIPE = {
    "answer":     ("mcp__lazy-tool-service__html_notes_web_search",
                   "canvas_add_widget(widget_type='data_card', config={'search_query': <query>})"),
    "wikipedia":  ("mcp__lazy-tool-service__html_notes_web_search",
                   "canvas_add_widget(widget_type='data_card', config={'search_query': <query>})"),
    "news":       ("mcp__lazy-tool-service__html_notes_news",
                   "canvas_add_widget(widget_type='data_card', config={'news_topic': <query>})"),
    "stock_news": ("mcp__lazy-tool-service__html_notes_stock_news",
                   "canvas_add_widget(widget_type='data_card', config={'stock_news_query': <query>})"),
    # No tool: the agent has no image tool at all — the server searches and
    # vision-checks from image_query.
    "image":      ("",
                   "canvas_add_widget(widget_type='image', config={'image_query': <query>})"),
}

_PREFLIGHT_WANTS = {"answer", "watch", "listen", "see", "track", "compute",
                    "remember", "ui-control"}


def _clean_preflight_checks(raw) -> dict:
    """Validate the classifier's pre-flight answers. Best-effort by design: a
    missing, malformed or half-filled `checks` yields {} (or drops just the bad
    fields), so a model hiccup degrades to today's behaviour rather than putting
    a garbage assertion in front of the agent."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in ("is_arithmetic", "needs_fresh_data"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    wants = str(raw.get("wants", "") or "").strip().lower()
    if wants in _PREFLIGHT_WANTS:
        out["wants"] = wants
    subject = str(raw.get("subject", "") or "").strip()
    if subject:
        out["subject"] = subject[:160]
    unknowns = raw.get("unknowns")
    if isinstance(unknowns, list):
        clean = [str(u).strip()[:80] for u in unknowns[:3] if str(u).strip()]
        if clean:
            out["unknowns"] = clean
    return out


def _preflight_block(specs: list, checks: dict) -> str:
    """Tier 2's read of the ask, rendered as a prompt block for tier 3.

    The classifier already read the message AND the canvas and produced both a
    widget plan and the basic self-check answers. On the research-deferral path
    all of that was logged and thrown away, so the agent re-derived intent from
    raw text with no hint — the same caller-knows-the-answer-and-discards-it
    seam that made the traffic builder re-grep for a keyword the router had
    already consumed (fixed there with an explicit force_traffic flag).

    Deliberately a PRIOR, not a mandate. The classifier that gets this right
    most of the time is also what read a cooking-time question as a unit
    conversion on 2026-07-31; a mandate would make a wrong classification
    unrecoverable, and the agent still needs freedom to pick tools. So: state
    the checks, name the type, the cleaned query and the documented recipe, then
    explicitly yield to the user's own words and to the FOLLOW-UP directive that
    comes after this block.

    Returns "" for an empty plan so the caller can concatenate unconditionally —
    on defer/None the prompt stays byte-identical to before this existed.
    """
    if not specs:
        return ""
    lines = []
    if checks.get("is_arithmetic") is not None:
        lines.append("- Is this ask itself a calculation a converter can finish? "
                     + ("YES" if checks["is_arithmetic"] else "NO"))
    if checks.get("needs_fresh_data") is not None:
        lines.append("- Does answering need data you must look up? "
                     + ("YES — call a tool before you render" if checks["needs_fresh_data"]
                        else "NO — it is already in this conversation"))
    if checks.get("wants"):
        lines.append(f"- What the user wants out of this: {checks['wants']}")
    if checks.get("subject"):
        lines.append(f"- Subject: \"{checks['subject']}\"")
    if checks.get("unknowns"):
        lines.append("- Still unknown: " + "; ".join(checks["unknowns"]))

    plan = []
    for s in specs[:4]:
        wtype = str(s.get("type", ""))
        query = str(s.get("query") or "").strip()
        tool, render = _PREFLIGHT_RECIPE.get(wtype, ("", ""))
        step = (f"call {tool}, then {render}" if tool
                else render or f"use the {wtype} route in ROUTING above")
        tgt = s.get("target")
        plan.append(f'- {wtype} — query: "{query}" → {step}'
                    + (f", writing into the EXISTING widget #{tgt}" if tgt else ""))

    return (
        "\n\nBEFORE YOU PICK A TOOL\n"
        "A fast classifier read this ask and the canvas above before you did.\n"
        + ("\n".join(lines) + "\n" if lines else "")
        + "It concluded:\n" + "\n".join(plan) + "\n"
        "Start there and reuse that query verbatim — it is the cleaned subject, "
        "already stripped of filler. This is a strong prior, NOT an order: if it "
        "plainly contradicts what the user actually wrote, ignore it and use the "
        "tool that fits. Do not re-plan past it and do not add widgets it did not "
        "name unless the ask clearly needs them. If a directive below names a "
        "widget id, that id wins over anything here.\n"
    )


async def route_with_llm(message: str, context_block: str) -> Optional[dict]:
    """Classify an ask the fast lane missed into one or more server-buildable
    widgets, or a deferral. `context_block` is build_turn_context()'s bundle:
    recent turns (with a content gist) + the widgets on the canvas now — so the
    router can recognise a follow-up and target the widget it refines. Returns:
      {"widgets": [{"type", "query", "modifiers", "target"}], "reason"} to
        build+spawn (target = a canvas widget id to UPDATE in place, or omitted), or
      {"defer": true} to hand off to the full agent (removals, edits, note
      dictation, custom widgets, small talk), or
      None on any model failure — the caller then falls back to the agent, so the
    router is a pure latency/quality optimization and never a hard gate.

    The returned dict also carries `checks` — the PRE-FLIGHT answers (see
    _preflight_block). This pass already has the message AND the canvas context,
    and on the research-deferral path its whole output used to be logged and
    thrown away, so asking it a handful of yes/no questions costs no extra call
    and no extra latency. `checks` is best-effort: a missing or malformed object
    yields {} and every downstream consumer degrades to today's behaviour."""
    catalog = "\n".join(f"- {name}: {spec}" for name, (_p, spec) in ROUTER_WIDGETS.items())
    data = await fast_llm_json(
        "You are the router for a live dashboard. Choose the widget(s) that best "
        "serve the user and the search query for each. Return ONLY a JSON object, "
        "no prose, no code fence:\n"
        '{"widgets": [{"type": "<type>", "query": "<query>", "modifiers": {}, '
        '"target": "<canvas widget id or omit>"}], "reason": "<=8 words", '
        '"checks": {"is_arithmetic": <bool>, "needs_fresh_data": <bool>, '
        '"wants": "<answer|watch|listen|see|track|compute|remember|ui-control>", '
        '"subject": "<the real subject, disambiguated>", '
        '"unknowns": ["<what you would still have to look up>"]}}\n'
        "Rules:\n"
        "- Match the widgets to the ask. A NARROW single-intent ask (a forecast, a "
        "ticker, a timer) is ONE widget. A BROAD or rich ask should COMPOSE the "
        "modalities that together serve it — lead with the explanation/answer, then "
        "add supporting media that genuinely helps (an image of the subject, a video "
        "to watch, recent news if it's current, a map if it's a place). Examples: "
        '"plan my Saturday in Seattle" -> weather + map + things-to-do; "tesla stock '
        'and news" -> stock + stock_news; "tell me about black holes" -> answer + '
        "image + video. Do NOT pad with a modality that doesn't serve the subject. Max 4.\n"
        "- For a traffic ask add modifiers {\"traffic\": true}.\n"
        "- FOLLOW-UPS UPDATE, THEY DON'T STACK. Read RECENT TURNS and the canvas "
        "below. If the ask refines something you already built (\"what about the "
        "away team?\" after a scoreboard, \"tell me more\", \"and taco bell?\" after a "
        "news card, another place on an open map), set \"target\" to that widget's "
        "id so the server rewrites it in place. Only omit target when the ask opens "
        "a genuinely NEW subject. Never open a second map/weather/stock of the same kind.\n"
        "- If the ask is to REMOVE / close / clear an EXISTING widget, to "
        "take dictation into a note, to build a custom/one-off widget, to change "
        "the THEME / appearance / settings, or is small "
        'talk with no widget need, return {"defer": true} instead of widgets.\n'
        "- Never invent a type or a widget id. Use only the types listed and the "
        "ids shown below.\n"
        # PRE-FLIGHT. Kept LAST so the tuned widget rules above are untouched.
        # These are the basic questions the agent should have asked itself before
        # reaching for a tool; answering them here means the agent is handed the
        # answers instead of re-deriving intent from raw text.
        '- Then fill in "checks", about the ask as a whole:\n'
        '  "is_arithmetic": true ONLY if the ask IS a calculation or unit/currency '
        "conversion that a calculator alone can finish. A question that merely "
        "MENTIONS numbers, temperatures, weights, times or money is false — "
        '"how long until my 145F chicken hits 165 in a 400F oven" needs cooking '
        "knowledge, so it is false.\n"
        '  "needs_fresh_data": true if answering requires looking something up '
        "rather than reading it off this conversation.\n"
        '  "wants": what the user actually wants OUT of this — an answer, to watch, '
        "to listen, to see, to track, to compute, to remember, or to control the UI.\n"
        '  "subject": the real subject in plain words, disambiguated against the '
        "conversation above.\n"
        '  "unknowns": up to 3 things you would still have to look up. [] if none.\n\n'
        "WIDGET TYPES:\n" + catalog +
        (f"\n\n{context_block[:1200]}" if context_block else "") +
        f'\n\nUSER: "{message}"',
        # Raised from 400: the response now also carries the `checks` object.
        # Too small a ceiling truncates the JSON mid-object and fast_llm_json's
        # brace match then fails, which would silently turn every route into a
        # None -> agent fallback.
        max_tokens=550,
    )
    if not isinstance(data, dict):
        return None
    checks = _clean_preflight_checks(data.get("checks"))
    if data.get("defer"):
        return {"defer": True, "reason": data.get("reason", ""), "checks": checks}
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
        tgt = w.get("target")
        clean.append({"type": wtype,
                      "query": str(w.get("query", "") or "").strip(),
                      "modifiers": w.get("modifiers") if isinstance(w.get("modifiers"), dict) else {},
                      "target": str(tgt).lstrip("#").strip() if tgt else None})
    if not clean:
        return None
    # The prompt says "never open a second stock of the same kind", but prose
    # can't outvote sampling: "XLP vs SPY" reliably came back as TWO stock
    # specs and rendered two separate cards. A comparison is one question —
    # collapse same-type stock specs into a single spec whose query joins the
    # tickers; the stock builder renders it as one multi-series chart.
    stock_specs = [w for w in clean if w["type"] == "stock"]
    if len(stock_specs) > 1:
        joined = " vs ".join(w["query"] for w in stock_specs if w["query"])
        stock_specs[0]["query"] = joined
        clean = [w for w in clean if w["type"] != "stock" or w is stock_specs[0]]
        logger.info(f"[ROUTER] collapsed {len(stock_specs)} stock specs into "
                    f"one compare: {joined!r}")
    # A converter pick is the one classification with no net underneath it (see
    # build_router_widget), and `checks.is_arithmetic` is the model's own answer
    # to "is this actually a calculation?" — when it says no, believe it.
    if checks.get("is_arithmetic") is False:
        for w in clean:
            if w["type"] == "converter":
                logger.info(f"[ROUTER] demoted converter -> answer "
                            f"(checks.is_arithmetic=false): {message[:70]!r}")
                w["type"] = "answer"
                w["query"] = w["query"] or message
    return {"widgets": clean, "reason": str(data.get("reason", ""))[:80],
            "checks": checks}


# Modalities the composition planner is allowed to combine (a subset of
# ROUTER_WIDGETS — the ones that make sense as parts of one rich answer).
_COMPOSE_MODALITIES = ("answer", "image", "video", "news", "map")










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

















# ── Obsidian vault: notes saved from the canvas become .md files ─────────────
def _note_slug(title: str) -> str:
    """A filesystem-safe slug from a note title. No path separators, no dots at
    the ends — so a title can never escape the vault dir."""
    s = re.sub(r"[^\w\s-]", "", (title or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-.")
    return (s or "note")[:80]


def _note_path(slug: str) -> Optional[pathlib.Path]:
    """Resolve a slug to a .md path INSIDE the vault, or None if it would escape."""
    safe = _note_slug(slug)
    if not safe:
        return None
    vault = pathlib.Path(OBSIDIAN_VAULT_DIR).resolve()
    p = (vault / f"{safe}.md").resolve()
    try:
        p.relative_to(vault)   # must stay within the vault
    except ValueError:
        return None
    return p


def _yaml_frontmatter(meta: dict) -> str:
    """Minimal YAML frontmatter Obsidian reads. Hand-rolled (no pyyaml dep):
    title quoted, tags as an inline array, timestamps as ISO strings."""
    def q(v):
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    tags = meta.get("tags") or []
    tags_line = "[" + ", ".join(_note_slug(t) for t in tags if t) + "]"
    lines = ["---",
             f"title: {q(meta.get('title', 'Untitled'))}",
             f"tags: {tags_line}",
             f"created: {meta.get('created', '')}",
             f"updated: {meta.get('updated', '')}",
             "source: html-notes",
             "---", ""]
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> dict:
    """Pull title/tags/created out of an existing note's frontmatter block, so an
    update preserves `created` and can round-trip the metadata."""
    out = {"title": "", "tags": [], "created": "", "body": text}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return out
    fm, body = m.group(1), m.group(2)
    out["body"] = body
    for line in fm.splitlines():
        km = re.match(r"\s*(\w+)\s*:\s*(.*)$", line)
        if not km:
            continue
        key, val = km.group(1), km.group(2).strip()
        if key == "title":
            if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            out["title"] = val
        elif key == "created":
            out["created"] = val.strip('"')
        elif key == "tags":
            inner = val.strip("[]")
            out["tags"] = [t.strip().strip('"') for t in inner.split(",") if t.strip()]
    return out


class SaveNoteRequest(BaseModel):
    title: str = "Untitled"
    content: str = ""
    tags: List[str] = []
    slug: str = ""      # set when re-saving an existing note (keeps the same file)










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


async def _mcp_scopes_serving_us(client: httpx.AsyncClient) -> list:
    """Scopes where lazy-tool-service IS connected, for diagnosing a mismatch.

    Prism scopes /mcp-servers by the x-project/x-username headers, so there is
    no "list every scope" call — we probe the scopes we know this ecosystem
    registers under. Best-effort and purely diagnostic.
    """
    candidates = [(AGENT_PROJECT, "lazycat"), (AGENT_PROJECT, "admin"),
                  ("coding", "admin"), ("vllm-trading-bot", "lazy-trader")]
    found = []
    for project, username in candidates:
        if (project, username) == (AGENT_PROJECT, AGENT_USERNAME):
            continue
        try:
            rows = (await client.get(
                f"{PRISM_URL}/mcp-servers",
                headers={"x-project": project, "x-username": username})).json()
            rows = rows if isinstance(rows, list) else rows.get("servers", [])
            if any(r.get("name") == MCP_SERVER_NAME and r.get("connected")
                   for r in rows):
                found.append(f"{project}/{username}")
        except Exception:
            continue
    return found


async def _agent_dependency_status() -> dict:
    """Can a research ask actually work right now?

    Every tier-3 ask runs on Prism against the CUSTOM_HTML_NOTES_CANVAS persona,
    using tools served over MCP by lazy-tool-service. None of that lives in this
    repo, and when it breaks this app keeps answering /health/app with "ok"
    while every research ask dies on `Unknown tool error`. So check the thing
    that actually has to be true:

      1. Prism is reachable at all,
      2. the lazy-tool-service MCP server is CONNECTED for our scope and serving
         a non-zero tool count (a connected server with 0 tools is the shape a
         half-dead SSE link takes),
      3. our persona exists — without it the run is unscoped, which is not an
         outage but is a large silent quality regression (~79 tools, the agent
         wanders into execute_python).

    Never raises: a health probe that throws is worse than one that reports.
    """
    detail = {"prism": PRISM_URL, "project": AGENT_PROJECT,
              "agent_id": PRISM_AGENT_ID}
    headers = {"x-project": AGENT_PROJECT, "x-username": AGENT_USERNAME}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            servers = (await client.get(f"{PRISM_URL}/mcp-servers",
                                        headers=headers)).json()
            servers = servers if isinstance(servers, list) else servers.get("servers", [])
            mine = next((s for s in servers if s.get("name") == MCP_SERVER_NAME), None)
            if not mine:
                # Not in OUR scope — but Prism serves MCP tools globally once a
                # server is connected under any scope, so this is a registration
                # mismatch rather than an outage, and research may well still
                # work. Report it as degraded and name the scopes that do have
                # it, because the alternative (calling it down) cries wolf while
                # the real symptom is only that nothing shows under our project.
                elsewhere = await _mcp_scopes_serving_us(client)
                if elsewhere:
                    return {**detail, "ok": True, "degraded": True,
                            "error": f"{MCP_SERVER_NAME} is not registered for "
                                     f"{AGENT_PROJECT}/{AGENT_USERNAME}; serving from "
                                     f"{', '.join(elsewhere)} instead (tools resolve, "
                                     f"but our own scope shows none)"}
                return {**detail, "ok": False,
                        "error": f"{MCP_SERVER_NAME} is not registered for this scope"}
            tools = int(mine.get("toolCount") or 0)
            detail |= {"mcp_connected": bool(mine.get("connected")), "tool_count": tools}
            if not mine.get("connected") or tools <= 0:
                return {**detail, "ok": False,
                        "error": "MCP server registered but not serving tools"}

            agents = (await client.get(f"{PRISM_URL}/custom-agents",
                                       headers=headers)).json()
            agents = agents if isinstance(agents, list) else agents.get("agents", [])
            persona = next((a for a in agents
                            if a.get("agentId") == PRISM_AGENT_ID), None)
            detail["persona_tools"] = len(persona.get("availableTools") or []) if persona else 0
            if not persona:
                # Degraded, not down: research still runs, just unscoped.
                return {**detail, "ok": True, "degraded": True,
                        "error": f"persona {PRISM_AGENT_ID} is missing — runs will be unscoped"}
            return {**detail, "ok": True}
    except Exception as e:
        return {**detail, "ok": False, "error": f"prism unreachable: {e}"}





class InternalToolRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = {}

# The complete dispatch set of internal_tool_execute. Membership is checked
# BEFORE dispatch so a compromised or misconfigured caller can only name these
# tools — anything else is rejected without touching the elif chain.
_INTERNAL_EXECUTE_TOOLS = frozenset({
    "html_notes_create_note", "html_notes_update_note", "html_notes_get_note",
    "html_notes_search_notes", "html_notes_link_notes", "html_notes_modify_dom",
    "render_component", "canvas_read_dom", "canvas_modify_dom",
    "html_notes_add_youtube_widget", "create_widget", "update_widget",
    "html_notes_youtube_search", "html_notes_web_search", "html_notes_read_page",
    "html_notes_news", "html_notes_stock_history", "html_notes_stock_news",
    "html_notes_get_weather", "html_notes_sports_scores", "canvas_add_widget",
})
_internal_execute_auth_warned = False


CANVAS_BLOCK_RE = re.compile(r'<!--CANVAS_HTML_START-->(.*?)<!--CANVAS_HTML_END-->', re.DOTALL)


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




# Transparent 1×1 PNG — served when a traffic tile can't be fetched, so the base
# map shows through cleanly instead of a broken-image frame.
_BLANK_PNG = _base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
# TomTom probe result is logged once per state-change so the reason traffic is/ isn't
# showing is visible in the container logs without spamming every tile.
_tomtom_last_status: Dict[str, int] = {}










# Mount UI static files at root
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_CACHE_BUSTED_ASSETS = ("index.js", "index.css", "js/widgets.js", "hud-theme.css")
_ASSET_QUERY_RE = re.compile(r'(index\.js|index\.css|js/widgets\.js|hud-theme\.css)\?v=[^"\']*')


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

from app.config_builders import *
from app.canvas_manager import *

__all__ = list(globals().keys())

from app.routes.message import router as message_router
app.include_router(message_router)
from app.routes.notes import router as notes_router
app.include_router(notes_router)
from app.routes.health import router as health_router
app.include_router(health_router)
from app.routes.api import router as api_router
app.include_router(api_router)
from app.routes.internal import router as internal_router
app.include_router(internal_router)
