import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)
from app.utils import _fetch_secret

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

    This collector hits DDG's `/html/` endpoint with an automated Playwright
    fallback on block/captcha, running independently through scraper-service.
    """
    try:
        client_cls = getattr(getattr(main, "httpx", httpx), "AsyncClient", httpx.AsyncClient)
        async with client_cls(timeout=20.0) as client:
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


async def web_search_ex(query: str, limit: int = 6) -> tuple:
    """Web search returning (results, all_engines_failed).

    The flag is the point. Before this, an engine OUTAGE and a genuinely obscure
    query were indistinguishable — both returned `[]` — so the caller told the
    model to "retry with a shorter, simpler query" while the real problem was that
    every backend was unreachable. The model obliged, 10-18 times per turn. Retry
    advice must never be given for a transport failure."""
    reached_any = False
    for engine, search in getattr(main, "_SEARCH_ENGINES", _SEARCH_ENGINES):
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


_GOOGLE_SECTION = {
    "us": "NATION", "world": "WORLD", "business": "BUSINESS",
    "technology": "TECHNOLOGY", "science": "SCIENCE", "health": "HEALTH",
    "sports": "SPORTS", "entertainment": "ENTERTAINMENT",
}


async def _google_news_rss(topic: str, limit: int, category: str = "",
                           country: str = "") -> list:
    """Fallback headlines from Google News RSS. Real and dated, but the links are
    Google redirects that don't resolve to the article, so there is no article
    photo — the publisher favicon is used as a thumbnail instead."""
    gl = (country or "us").upper()
    loc = f"hl=en-{gl}&gl={gl}&ceid={gl}:en"
    if topic and topic.strip():
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(topic.strip()) + f"&{loc}")
    elif _GOOGLE_SECTION.get(category):
        url = ("https://news.google.com/rss/headlines/section/topic/"
               f"{_GOOGLE_SECTION[category]}?{loc}")
    else:
        url = f"https://news.google.com/rss?{loc}"
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


_GENERIC_NEWS_DESC_SNIPPETS = (
    "comprehensive up-to-date news coverage",
    "aggregated from sources all over the world by google news",
    "google news",
    "visit the post for more",
    "please enable javascript",
    "access to this page has been denied",
    "attention required! | cloudflare",
)


def _is_generic_news_desc(desc: str) -> bool:
    if not desc:
        return True
    low = desc.lower().strip()
    return any(sig in low for sig in _GENERIC_NEWS_DESC_SNIPPETS) or len(low) < 15


async def _enrich_news(items: list, timeout: float = 5.0) -> None:
    """Fill each item's summary (og:description) and, when GDELT had no social
    image, its photo (og:image), by fetching the real article. Concurrent and
    best-effort — a slow or blocking site just leaves that item with its title
    as the summary."""
    stats = {"ok": 0, "fail": 0}

    def _worth_fetching(item: dict) -> bool:
        """Two fetches per card were wasted, and one of them could never work.

        A news.google.com/rss/articles/CBMi... link does NOT resolve
        server-side — it 200s with a JS-driven body — and the og:image it
        serves is Google's own News logo, the same picture for every story.
        And an item that already has a photo and a real summary has nothing to
        gain. This fetch is the dominant latency term on the news path: a
        general ask measured 31.2s, almost all of it here.
        """
        url = item.get("url") or ""
        if item.get("stub") or "news.google.com" in url:
            return False
        has_desc = bool(item.get("snippet")) and not _is_generic_news_desc(item.get("snippet", ""))
        return not (has_desc and item.get("image"))

    async def one(client: httpx.AsyncClient, item: dict) -> None:
        if not _worth_fetching(item):
            return
        try:
            resp = await client.get(item["url"], headers={"User-Agent": _BROWSER_UA})
            html = resp.text
        except Exception:
            stats["fail"] += 1
            return
        if not item.get("snippet") or _is_generic_news_desc(item.get("snippet", "")):
            m = _OG_DESC_RE.search(html)
            if m:
                cand = _html_unescape(m.group(1)).strip()[:400]
                item["snippet"] = "" if _is_generic_news_desc(cand) else cand
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
                item["image"] = "" if _is_generic_news_thumb(resolved) else resolved
        stats["ok"] += 1

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        await asyncio.gather(*(one(client, it) for it in items),
                             return_exceptions=True)
    # Filter any surviving generic descriptions across all items
    for it in items:
        if _is_generic_news_desc(it.get("snippet", "")):
            it["snippet"] = ""

    if items and stats["fail"] > stats["ok"]:
        logger.info(f"[ENRICH] {stats['ok']}/{len(items)} enriched "
                    f"({stats['fail']} fetch failures)")


async def _shared_news_search(topic: str, limit: int, category: str = "",
                              country: str = "") -> list:
    """The shared multi-provider news tool in lazy-tool-service.

    PRIMARY source, ahead of the Google News RSS path below, because it returns
    what that path structurally cannot: the PUBLISHER's own URL and THAT STORY's
    photo.
    """
    # An empty topic is passed THROUGH as empty. It used to be substituted with
    # the literal string "top stories", which the provider then keyword-searched
    # — matching roundup pages that contain that phrase rather than returning the
    # day's headlines. Measured 2026-09-05, "what's going on in the news"
    # returned "Alix Earle sheds light on her feud with Alex Cooper and other TOP
    # STORIES highlighted by Us for September 4": the phrase WAS the match.
    # news_search now treats "" as "use your top-headlines endpoint".
    clean_topic = (topic or "").strip()
    # A SECTION, for the top-headlines path only. Sending it with a keyword
    # search would be meaningless, and an empty string is not the same as an
    # absent key to a provider that might read it as a filter.
    body = {"topic": clean_topic, "limit": limit}
    if not clean_topic:
        if category:
            body["category"] = category
        if country:
            body["country"] = country
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{LAZY_TOOL_SERVICE_URL}/execute/news_search",
                json=body,
            )
        if resp.status_code != 200:
            logger.warning(f"[news] shared news_search HTTP {resp.status_code}")
            return []
        payload = resp.json()
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
            # The gateway's top-headlines rows carry three extra fields: which
            # section the story is in, how many independent newsrooms led with
            # it, and whether the link is a Google redirect stub (unscrapeable,
            # no article photo). All three are load-bearing downstream.
            "category": str(r.get("category", "") or ""),
            "consensus": r.get("consensus"),
            "stub": bool(r.get("stub")),
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


async def _scraper_service_news(topic: str, limit: int = 6) -> list:
    """Collect news stories through scraper-service (DuckDuckGo or RSS)."""
    try:
        client_cls = getattr(getattr(main, "httpx", httpx), "AsyncClient", httpx.AsyncClient)
        async with client_cls(timeout=12.0) as client:
            resp = await client.post(
                f"{SCRAPER_SERVICE_URL}/collect",
                json={"source": "duckduckgo", "query": f"{topic} news", "limit": limit},
            )
            payload = resp.json()
        items = []
        for it in (payload.get("items") or [])[:limit]:
            url = it.get("url") or ""
            title = (it.get("title") or "").strip()
            if title and url.startswith("http"):
                items.append({
                    "title": title,
                    "url": url,
                    "image": "",
                    "meta": _host_of(url),
                    "snippet": (it.get("snippet") or "").strip()[:500],
                    "date": "",
                })
        return items
    except Exception as e:
        logger.warning(f"scraper-service news collect failed for {topic!r}: {e}")
        return []


async def news_search(topic: str, limit: int = 6, category: str = "",
                      country: str = "") -> list:
    """Current headlines with real photos and summaries. Returns
    [{title, url, image, meta, snippet, date, category?, consensus?, stub?}].

    An EMPTY topic means "today's top headlines" and is a valid request, not a
    missing argument. `category` narrows that to a section (world, business,
    technology...) and is meaningless for a keyword search.

    Order for a TOPIC:
    1. The SHARED news_search tool in lazy-tool-service (~1s).
    2. Google News RSS (~0.1s, reliable, keyless).
    3. scraper-service news/DDG collector.
    4. GDELT (keyless).
    5. Generic web search.

    Order for the TOP STORIES: tiers 1 and 2 only. Tiers 3-5 have no concept of
    a headline — they are keyword searches, and the only way to use them for a
    general ask is to invent a query. That is precisely the defect this path
    was fixed for once already: the scraper tier searched the literal words
    "news top stories" and the web tier "top news headlines", both of which
    keyword-match roundup pages CONTAINING the phrase rather than returning the
    news. They were fixed in tier 1 and left in place here, where they fire
    only in the degraded state — the moment quality matters most. An empty
    result is now the honest answer.
    """
    # Passed by KEYWORD, deliberately. These two arrived late in the life of
    # this call and several stubs and callers still take (topic, limit); a
    # positional third argument turns those into a TypeError that surfaces two
    # layers away, inside an `asyncio.gather(..., return_exceptions=True)` that
    # silently converts it into "no news".
    items, source = await _shared_news_search(
        topic, limit, category=category, country=country), "lazy-tool"
    if not items:
        items, source = await _google_news_rss(
            topic, limit, category=category, country=country), "google-news"
    if not items and topic:
        items, source = await _scraper_service_news(topic, limit), "scraper-news"
    if not items and topic:
        items, source = await _gdelt_news(topic, limit), "gdelt"
    if not items and topic:
        raw = await web_search(f"{topic} news", limit)
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
    logger.info(f"[NEWS] {topic!r}{f' [{category}]' if category else ''} → "
                f"{len(items)} items via {source}")
    return items


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


async def _fetch_news_page_text(n):
    url = n.get("url", "")
    if not url:
        return ""
    try:
        page = await read_web_page(url, max_chars=4000)
        return "" if page.get("is_error") else (page.get("content") or "")
    except Exception:
        return ""


@app.get("/search")
async def api_search(q: str):
    return database.search_notes(q)


__all__ = [k for k in globals().keys() if not k.startswith('__')]
