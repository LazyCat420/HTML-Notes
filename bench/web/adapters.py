"""Search provider adapters for bench/web. One normalized contract each.

Adapters are deliberately standalone (stdlib only): the benchmark must not
share code paths with app/services/search.py, or it stops being an independent
measurement of that code.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .schema import (
    SearchResult, ProviderError, ProviderResponse,
    OUTAGE, RATE_LIMITED, CAPTCHA, MALFORMED, SOFT_EMPTY, OK,
)

UA = "Mozilla/5.0 (X11; Linux x86_64) html-notes-bench/1.0"
DEFAULT_TIMEOUT = 30


def _get(url: str, timeout: int = DEFAULT_TIMEOUT, data: bytes | None = None,
         headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise ProviderError(OUTAGE, str(e)) from e


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonicalize(url: str) -> str:
    """Strip common tracking params + fragment (mirrors news_pipeline rules)."""
    p = urllib.parse.urlparse(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith(("utm_", "ref", "fbclid", "gclid", "mc_"))]
    return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode(q), fragment=""))


# ---------------------------------------------------------------- adapters

def search_searxng(query: str, query_id: str, limit: int = 10,
                   base_url: str = "http://127.0.0.1:8888") -> ProviderResponse:
    """Self-hosted SearXNG JSON API. Set engines via `engines=` (csv) to pin."""
    t0 = time.monotonic()
    status, body = _get(
        f"{base_url}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}")
    ms = (time.monotonic() - t0) * 1000
    if status == 429:
        raise ProviderError(RATE_LIMITED, f"searxng 429 after {ms:.0f}ms")
    if status != 200:
        raise ProviderError(OUTAGE, f"searxng HTTP {status}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise ProviderError(MALFORMED, f"searxng bad JSON: {e}") from e
    results = [
        SearchResult(provider="searxng", provider_rank=i, title=r.get("title", ""),
                     url=canonicalize(r.get("url", "")), snippet=r.get("content", ""),
                     display_domain=_domain(r.get("url", "")),
                     published_at=r.get("publishedDate"), retrieved_at=_now(),
                     engine=r.get("engine", ""), result_type="web",
                     fields_genuine=frozenset(k for k in ("title", "url", "content", "publishedDate") if r.get(k)))
        for i, r in enumerate(payload.get("results", [])[:limit], 1)]
    st = OK if results else SOFT_EMPTY
    return ProviderResponse("searxng", query_id, query, st, results, ms)


def search_ddg_lite(query: str, query_id: str, limit: int = 10) -> ProviderResponse:
    """Direct DuckDuckGo Lite scrape — the app's current primary engine."""
    t0 = time.monotonic()
    status, body = _get("https://lite.duckduckgo.com/lite/", data=urllib.parse.urlencode(
        {"q": query}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    ms = (time.monotonic() - t0) * 1000
    html = body.decode("utf-8", "replace")
    if status in (202, 403) or "anomaly" in html.lower() or "captcha" in html.lower():
        # DDG serves HTTP 202 with an anomaly/challenge page for non-browser traffic.
        raise ProviderError(CAPTCHA, f"ddg-lite challenge (HTTP {status}) after {ms:.0f}ms")
    if status != 200:
        raise ProviderError(OUTAGE, f"ddg-lite HTTP {status}")
    links = re.findall(r'href="[^"]*/l/\?uddg=([^"&]+)"[^>]*>(?:<[^>]+>)*([^<]+)', html)
    results = [
        SearchResult(provider="ddg_lite", provider_rank=i,
                     title=urllib.parse.unquote(t.strip()),
                     url=canonicalize(urllib.parse.unquote(u)),
                     display_domain=_domain(urllib.parse.unquote(u)),
                     retrieved_at=_now(), result_type="web",
                     fields_genuine=frozenset(("title", "url")))
        for i, (u, t) in enumerate(links[:limit], 1)]
    st = OK if results else SOFT_EMPTY
    return ProviderResponse("ddg_lite", query_id, query, st, results, ms)


def search_google_news_rss(query: str, query_id: str, limit: int = 10) -> ProviderResponse:
    t0 = time.monotonic()
    status, body = _get("https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query}))
    ms = (time.monotonic() - t0) * 1000
    if status != 200:
        raise ProviderError(OUTAGE, f"gnr HTTP {status}")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ProviderError(MALFORMED, f"gnr bad XML: {e}") from e
    results = []
    for i, item in enumerate(list(root.iter("item"))[:limit], 1):
        title = (item.findtext("title") or "").strip()
        link = canonicalize(item.findtext("link") or "")
        pub = item.findtext("pubDate") or ""
        date = None
        if pub:
            try:
                date = parsedate_to_datetime(pub).isoformat(timespec="seconds")
            except Exception:
                date = pub
        results.append(SearchResult(
            provider="google_news_rss", provider_rank=i, title=title, url=link,
            snippet=item.findtext("description") or "", display_domain=_domain(link),
            published_at=date, retrieved_at=_now(), result_type="news",
            fields_genuine=frozenset(k for k in ("title", "url", "published_at") if (title or link or date))))
    st = OK if results else SOFT_EMPTY
    return ProviderResponse("google_news_rss", query_id, query, st, results, ms)


def search_gdelt(query: str, query_id: str, limit: int = 10) -> ProviderResponse:
    t0 = time.monotonic()
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {"query": f'"{query}"', "mode": "artlist", "maxrecords": limit, "format": "json"})
    status, body = _get(url, timeout=15)
    ms = (time.monotonic() - t0) * 1000
    text = body.decode("utf-8", "replace")
    if status != 200:
        raise ProviderError(OUTAGE, f"gdelt HTTP {status}")
    if not text.strip().startswith("{"):  # GDELT throttles with a plain-text body
        raise ProviderError(RATE_LIMITED, f"gdelt non-JSON throttle body after {ms:.0f}ms")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProviderError(MALFORMED, f"gdelt bad JSON: {e}") from e
    arts = payload.get("articles", [])
    results = [
        SearchResult(provider="gdelt", provider_rank=i, title=a.get("title", ""),
                     url=canonicalize(a.get("url", "")), snippet="",
                     display_domain=_domain(a.get("url", "")),
                     published_at=a.get("seendate"), retrieved_at=_now(),
                     result_type="news", fields_genuine=frozenset(("title", "url", "seendate")))
        for i, a in enumerate(arts[:limit], 1)]
    st = OK if results else SOFT_EMPTY
    return ProviderResponse("gdelt", query_id, query, st, results, ms)


ADAPTERS = {
    "searxng": search_searxng,
    "ddg_lite": search_ddg_lite,
    "google_news_rss": search_google_news_rss,
    "gdelt": search_gdelt,
}


def safe_search(name: str, query: str, query_id: str, limit: int = 10) -> ProviderResponse:
    """Adapter call that converts ProviderError into a classified failed response."""
    try:
        return ADAPTERS[name](query, query_id, limit=limit)
    except ProviderError as e:
        return ProviderResponse(name, query_id, query, e.status, [], 0.0, error=e.detail)
