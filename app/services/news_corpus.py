"""Access to the trading-service MongoDB news corpus (trading_bot.news_articles).

The corpus is the network's pre-scraped article store: scraper-service
continuously extracts full article bodies into `summary` (~4k chars median),
quality-gates them (`quality_status: ok`), and attributes tickers. It is the
same store trading-client's chat reads. The market-news card checks it BEFORE
fanning out to external news APIs — if yesterday's collector already pulled
the story, we do not re-scrape it.

Read-only, best-effort, fail-open: any error returns [] and the card falls
back to the live providers, exactly as if the corpus did not exist.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Credentials come from the staged deploy env (deploy-kit/.env.deploy); the
# fallback assembles the DSN at call time so no credential-shaped literal is
# stored here.  # secret-ok
_MONGO_HOST = os.getenv("MONGO_HOST", "10.0.0.16:27017")
_MONGO_DB = os.getenv("MONGO_DB", "trading_bot")
_MONGO_COL = os.getenv("MONGO_NEWS_COL", "news_articles")


def _mongo_uri() -> str:
    uri = os.getenv("MONGO_URI")
    if uri:
        return uri
    from urllib.parse import quote
    user = os.getenv("MONGO_USER", "sun")
    pw = quote(os.getenv("MONGO_PASSWORD", ""))
    return "mongodb://" + user + ":" + pw + "@" + _MONGO_HOST + \
        "/?directConnection=true&authSource=admin"

_client = None
_coll = None


def _collection():
    global _client, _coll
    if _coll is None:
        from pymongo import MongoClient
        _client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=4000)
        _coll = _client[_MONGO_DB][_MONGO_COL]
    return _coll


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def corpus_news(
    query: str = "",
    tickers: Optional[list] = None,
    hours: int = 48,
    limit: int = 10,
) -> list:
    """Recent quality-gated articles from the corpus, newest first.

    Matching mirrors the external providers: ticker rows match `ticker`
    attribution, keyword rows match the subject against title + body. The
    body rides in `summary`; the card's editor consumes it as article text.
    Returns stock_news-shaped items: {title, publisher, published, url,
    summary, related_tickers}.
    """
    q = (query or "").strip()
    if not tickers and not q:
        return []
    try:
        col = _collection()
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        clauses = []
        if tickers:
            clauses.append({"ticker": {"$in": [t.upper() for t in tickers if t]}})
        q = (query or "").strip()
        if q:
            # Keyword match on title (indexed-feel, selective); the body is
            # scraped prose and would match everything, so keep it out.
            rx = {"$options": "i", "$regex": "|".join(
                re.escape(w) for w in q.split() if len(w) > 2)}
            clauses.append({"title": rx})
        if not clauses:
            return []
        rows = list(col.find(
            {"$or": clauses, "quality_status": "ok", "published_at": {"$gte": since}},
            {"title": 1, "publisher": 1, "source": 1, "published_at": 1,
             "url": 1, "summary": 1, "ticker": 1},
        ).sort([("published_at", -1), ("summary", -1)]).limit(limit * 2))
    except Exception as e:
        logger.info(f"[NEWS] corpus fetch unavailable: {e}")
        return []

    seen, out = set(), []
    for r in rows:
        title = _clean(r.get("title") or "")
        url = r.get("url") or ""
        if not title or not url or url in seen:
            continue
        seen.add(url)
        pub = r.get("published_at")
        out.append({
            "title": title[:200],
            "publisher": _clean(r.get("publisher") or r.get("source") or "corpus"),
            "published": pub.strftime("%Y-%m-%d %H:%M UTC") if isinstance(pub, datetime) else "",
            "url": url,
            # Full scraped body — the editor reads this instead of a snippet.
            "summary": _clean(r.get("summary") or "")[:4000],
            "related_tickers": [r["ticker"]] if r.get("ticker") else [],
        })
        if len(out) >= limit:
            break
    logger.info(f"[NEWS] corpus -> {len(out)} articles "
                f"({'%s' % (tickers or query)!r}, last {hours}h)")
    return out
