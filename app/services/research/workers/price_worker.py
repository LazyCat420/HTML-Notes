"""Price & Market Data Worker.

Retrieves fast real-time quotes, session state, and intraday series (<1.5s).
Outputs normalized EvidenceItem and provisional widget config for instant rendering.
"""
from __future__ import annotations

import time
import hashlib
from typing import Optional, Dict, Any, Tuple

from ..models import EvidenceItem, ResearchTask


async def run_price_worker(symbol: str, range_: str = "1d",
                           task_id: Optional[str] = None) -> Tuple[Optional[EvidenceItem], Optional[Dict[str, Any]], ResearchTask]:
    """Fetch live price snapshot and build provisional quote card config."""
    import app.main as main
    t_start = time.time()
    task = ResearchTask(
        task_id=task_id or f"task_price_{hashlib.md5(symbol.encode()).hexdigest()[:8]}",
        task_type="price",
        worker_id="price_worker",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        deadline_ms=2500,
    )

    sym = (symbol or "").strip().upper()
    if not sym:
        task.state = "failed"
        task.error_class = "missing_symbol"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return None, None, task

    try:
        snap = await main.stock_snapshot(sym, range_=range_)
    except Exception as e:
        task.state = "failed"
        task.error_class = f"exception:{type(e).__name__}"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return None, None, task

    if not isinstance(snap, dict) or snap.get("is_error") or snap.get("error"):
        task.state = "failed"
        task.error_class = "empty_or_error_snapshot"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return None, None, task

    price = snap.get("price")
    change_pct = snap.get("change_pct", 0.0)
    name = snap.get("name") or sym
    currency = snap.get("currency", "USD")
    as_of = snap.get("as_of") or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    ev_id = f"ev_price_{sym.lower()}_{int(t_start)}"
    evidence = EvidenceItem(
        evidence_id=ev_id,
        url=f"https://finance.yahoo.com/quote/{sym}",
        canonical_url=f"https://finance.yahoo.com/quote/{sym}",
        title=f"{name} ({sym}) Quote: {currency} {price} ({change_pct:+.2f}%)",
        publisher="Market Data (Yahoo)",
        tier="primary",
        published_at=as_of,
        extracted_text=f"{sym} is trading at {price} {currency}, a move of {change_pct:+.2f}% as of {as_of}.",
        quality_score=1.0,
        freshness="fresh",
        source_provider="yahoo_quote",
        related_tickers=[sym],
    )

    # Build provisional widget config
    provisional_config = {
        "title": f"{name} ({sym})",
        "symbol": sym,
        "price": price,
        "change_pct": change_pct,
        "change": snap.get("change", 0.0),
        "history": snap.get("history", []),
        "as_of": as_of,
        "provisional": True,
        "provenance": {"source": "Market Data", "freshness": "fresh", "as_of": as_of},
    }

    task.state = "completed"
    task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    task.evidence_ids = [ev_id]
    task.details = {"symbol": sym, "price": price, "change_pct": change_pct}

    return evidence, provisional_config, task
