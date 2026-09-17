"""Peer & Sector Context Worker.

Detects sector ETF and peer moves to separate market-wide beta selloffs
from idiosyncratic single-stock catalysts.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Optional, Dict, Any, List, Tuple

from ..models import EvidenceItem, ResearchTask


# Sector mapping: symbol -> (Sector ETF, [Peer tickers])
SECTOR_MAP: Dict[str, Tuple[str, List[str]]] = {
    "NVDA": ("SMH", ["AMD", "INTC", "TSM", "AVGO"]),
    "AMD": ("SMH", ["NVDA", "INTC", "TSM"]),
    "INTC": ("SMH", ["NVDA", "AMD", "TSM"]),
    "TSLA": ("XLY", ["RIVN", "LCID", "F", "GM"]),
    "AAPL": ("XLK", ["MSFT", "GOOGL", "AMZN"]),
    "MSFT": ("XLK", ["AAPL", "GOOGL", "AMZN"]),
    "GOOGL": ("XLK", ["MSFT", "META", "AMZN"]),
    "GOOG": ("XLK", ["MSFT", "META", "AMZN"]),
    "META": ("XLK", ["GOOGL", "SNAP", "PINS"]),
    "AMZN": ("XLY", ["WMT", "TGT", "BABA"]),
    "SOFI": ("XLF", ["AFRM", "UPST", "PYPL", "JPM"]),
    "PLTR": ("XLK", ["SNOW", "DDOG", "AI"]),
}


async def run_peer_sector_worker(symbol: str, task_id: Optional[str] = None) -> Tuple[Optional[EvidenceItem], Optional[Dict[str, Any]], ResearchTask]:
    """Fetch sector ETF and peer performance to evaluate divergence."""
    import app.main as main
    t_start = time.time()
    task = ResearchTask(
        task_id=task_id or f"task_peer_{hashlib.md5(symbol.encode()).hexdigest()[:8]}",
        task_type="peer_sector",
        worker_id="peer_sector_worker",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        deadline_ms=5000,
    )

    sym = (symbol or "").strip().upper()
    mapping = SECTOR_MAP.get(sym)
    sector_etf, peers = mapping if mapping else ("SPY", ["QQQ", "IWM"])

    tickers_to_check = [sector_etf] + peers[:2]
    results = await asyncio.gather(
        *(main.stock_snapshot(t, range_="1d") for t in tickers_to_check),
        return_exceptions=True
    )

    perf: Dict[str, float] = {}
    for ticker, res in zip(tickers_to_check, results):
        if isinstance(res, dict) and not res.get("is_error"):
            perf[ticker] = float(res.get("change_pct", 0.0))

    if not perf:
        task.state = "failed"
        task.error_class = "no_peer_quotes"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return None, None, task

    sector_change = perf.get(sector_etf, 0.0)
    lines = [f"Sector ETF ({sector_etf}): {sector_change:+.2f}%"]
    for p in peers[:2]:
        if p in perf:
            lines.append(f"Peer {p}: {perf[p]:+.2f}%")

    details = {
        "symbol": sym,
        "sector_etf": sector_etf,
        "sector_change_pct": sector_change,
        "peer_changes": {p: perf.get(p) for p in peers[:2] if p in perf},
    }

    summary = f"Sector & Peer Context for {sym}: " + ", ".join(lines)
    ev_id = f"ev_peer_{sym.lower()}_{int(t_start)}"
    ev = EvidenceItem(
        evidence_id=ev_id,
        url=f"https://finance.yahoo.com/quote/{sector_etf}",
        canonical_url=f"https://finance.yahoo.com/quote/{sector_etf}",
        title=f"Sector ({sector_etf}) & Peer Context for {sym}",
        publisher="Market Sector & Peer Analysis",
        tier="primary",
        published_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        extracted_text=summary,
        quality_score=0.95,
        freshness="fresh",
        source_provider="peer_sector_worker",
        related_tickers=[sym, sector_etf] + peers[:2],
    )

    task.state = "completed"
    task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    task.evidence_ids = [ev_id]
    task.details = details

    return ev, details, task
