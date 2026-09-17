"""News & Event Retrieval Worker.

Performs concurrent retrieval across financial and general news providers,
deduplicates articles, scores source quality, and generates provisional card configs.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Optional, Dict, Any, List, Tuple

from ..models import EvidenceItem, ResearchTask


async def run_news_worker(query: str, tickers: Optional[List[str]] = None,
                          limit: int = 6, task_id: Optional[str] = None) -> Tuple[List[EvidenceItem], Optional[Dict[str, Any]], ResearchTask]:
    """Retrieve breaking & current event news, normalized into evidence items."""
    import app.main as main
    t_start = time.time()
    task = ResearchTask(
        task_id=task_id or f"task_news_{hashlib.md5((query or '').encode()).hexdigest()[:8]}",
        task_type="news",
        worker_id="news_worker",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        deadline_ms=3500,
    )

    syms = [t.upper() for t in (tickers or []) if t]
    evidence_items: List[EvidenceItem] = []
    seen_urls: set[str] = set()

    async def _fetch_finnews() -> List[dict]:
        try:
            return await main._finnews_articles(query=query if not syms else "", tickers=syms, limit=limit * 2)
        except Exception as e:
            main.logger.warning(f"[NEWS WORKER] finnews failed: {e}")
            return []

    async def _fetch_yahoo() -> List[dict]:
        if not syms:
            return []
        try:
            res = await main.stock_news(syms[0], limit=limit * 2)
            return (res or {}).get("news") or []
        except Exception as e:
            main.logger.warning(f"[NEWS WORKER] yahoo news failed: {e}")
            return []

    fin_results, yahoo_results = await asyncio.gather(
        _fetch_finnews(), _fetch_yahoo(), return_exceptions=True
    )
    fin_items = fin_results if isinstance(fin_results, list) else []
    yahoo_items = yahoo_results if isinstance(yahoo_results, list) else []

    all_raw = list(fin_items)
    for y in yahoo_items:
        if isinstance(y, dict) and y.get("title") and y.get("url"):
            all_raw.append({
                "title": y.get("title", ""),
                "publisher": y.get("publisher") or "Yahoo Finance",
                "published": y.get("published") or "",
                "url": y.get("url", ""),
                "og_desc": y.get("summary") or y.get("snippet") or "",
                "related_tickers": [syms[0]] if syms else [],
            })

    card_items = []
    for item in all_raw:
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)

        pub = item.get("publisher") or "News"
        published = item.get("published")
        snippet = item.get("og_desc") or ""

        # Score publisher quality
        pub_low = pub.lower()
        tier = "tier1_news" if any(w in pub_low for w in ["reuters", "bloomberg", "wsj", "cnbc", "financial times", "associated press"]) else "secondary"
        q_score = 1.0 if tier == "tier1_news" else 0.8

        ev_id = f"ev_news_{hashlib.md5(url.encode()).hexdigest()[:10]}"
        ev = EvidenceItem(
            evidence_id=ev_id,
            url=url,
            canonical_url=url,
            title=title,
            publisher=pub,
            tier=tier,
            published_at=published,
            extracted_text=snippet or title,
            quality_score=q_score,
            freshness="fresh",
            source_provider=pub,
            related_tickers=item.get("related_tickers") or syms,
        )
        evidence_items.append(ev)
        card_items.append({
            "title": title,
            "publisher": pub,
            "published": published,
            "url": url,
            "description": snippet,
        })
        if len(evidence_items) >= limit:
            break

    if not evidence_items:
        task.state = "failed"
        task.error_class = "no_articles_found"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return [], None, task

    task.state = "completed"
    task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    task.evidence_ids = [e.evidence_id for e in evidence_items]
    task.details = {"count": len(evidence_items), "tickers": syms}

    title_text = f"News: {', '.join(syms)}" if syms else (query[:40] or "Breaking News")
    provisional_config = {
        "title": title_text,
        "items": card_items,
        "provisional": True,
        "provenance": {
            "source": f"{len(evidence_items)} articles",
            "publishers": list({e.publisher for e in evidence_items}),
            "freshness": "fresh",
        }
    }

    return evidence_items, provisional_config, task
